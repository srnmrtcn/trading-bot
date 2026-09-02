from __future__ import annotations

import logging

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from src.db.base import Base

logger = logging.getLogger("db.session")

# Recycled well inside the hour between scheduled jobs, so a connection is
# never handed out after sitting idle long enough for the far end to have
# quietly dropped it.
POOL_RECYCLE_SECONDS = 1800


def make_engine(database_url: str) -> Engine:
    # pre_ping verifies a pooled connection before handing it out; Railway's
    # Postgres closes idle connections and the scheduler idles for most of
    # every hour, so without it the first statement of a job fails.
    return create_engine(
        database_url, future=True,
        pool_pre_ping=True, pool_recycle=POOL_RECYCLE_SECONDS,
    )


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, future=True)


def sync_missing_columns(engine: Engine) -> list:
    """Add columns the models declare but the live database does not have yet.

    ``Base.metadata.create_all`` only ever creates *missing tables*; it never
    touches a table that already exists. So the first time a column is added to
    an existing model, every database created before that change stays one
    column behind forever — and because the ORM's SELECT enumerates every
    mapped column, *any* query against that table then fails, not just the code
    that uses the new field.

    This is a deliberate lightweight stopgap, not a migration system. It only
    ever ADDs nullable columns; it never drops, renames or retypes anything, and
    it is idempotent so it is safe to run on every startup. A nullable
    ``ADD COLUMN`` needs no default and no table rewrite, which is what makes it
    safe to do live. Anything else — a NOT NULL column, a type change, a
    backfill — is a real migration: adopt Alembic before schema changes become
    frequent rather than growing this helper.

    Returns the ``"table.column"`` names it added.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    added = []
    statements = []
    for table_name, table in Base.metadata.tables.items():
        if table_name not in existing_tables:
            # create_all just built it, with every column already present.
            continue
        existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
        for column in table.columns:
            if column.name in existing_columns:
                continue
            if not column.nullable:
                # Adding a NOT NULL column to a populated table needs a default
                # and a decision about existing rows — a human's call, not this
                # helper's. Loud, because the app will now fail on that table.
                logger.error(
                    "Column %s.%s is missing from the database and is NOT NULL — "
                    "it needs a real migration and was left alone",
                    table_name, column.name,
                )
                continue
            type_sql = column.type.compile(dialect=engine.dialect)
            statements.append("ALTER TABLE %s ADD COLUMN %s %s" % (table_name, column.name, type_sql))
            added.append("%s.%s" % (table_name, column.name))

    if statements:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
        logger.info("Added missing columns to existing tables: %s", ", ".join(added))
    return added


def sync_missing_indexes(engine: Engine) -> list:
    """Create indexes the models declare but the live database does not have.

    The same blind spot as ``sync_missing_columns``: ``create_all`` builds the
    indexes of tables it creates and never revisits a table that already
    exists. So an index added to a model after deployment exists only on
    databases built after that change -- and unlike a missing column, a missing
    index fails silently. Nothing errors; queries just get slower as the table
    grows, which is the hardest kind of problem to attribute later.

    Additive and idempotent, like its sibling: it only ever CREATEs an index
    the database lacks, and never drops, renames or rebuilds one. Safe to run
    on every startup.

    Two costs worth naming. Creating an index on a large table takes a lock
    that blocks writes for its duration, so this runs at startup before the
    scheduler is started and nothing else is touching the database. And a
    failure to create one index must not stop an unattended service from
    booting -- the service works without it, only slower -- so each is
    attempted on its own and a failure is logged rather than raised.

    Returns the names of the indexes it created.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    added = []
    for table_name, table in Base.metadata.tables.items():
        if table_name not in existing_tables:
            continue
        existing = {index["name"] for index in inspector.get_indexes(table_name)}
        for index in table.indexes:
            if index.name in existing:
                continue
            try:
                index.create(bind=engine)
            except Exception:
                logger.exception("Could not create index %s on %s", index.name, table_name)
                continue
            added.append(index.name)

    if added:
        logger.info("Created missing indexes: %s", ", ".join(sorted(added)))
    return added


def create_all_tables(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    # Both run on every startup: a no-op on a fresh database (create_all
    # already made every column and index) and a self-heal on one that
    # predates either.
    sync_missing_columns(engine)
    sync_missing_indexes(engine)
