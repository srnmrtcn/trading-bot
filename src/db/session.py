from __future__ import annotations

import logging

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from src.db.base import Base

logger = logging.getLogger("db.session")


def make_engine(database_url: str) -> Engine:
    return create_engine(database_url, future=True)


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


def create_all_tables(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    # Runs on every startup: a no-op on a fresh database (create_all already
    # made every column) and a self-heal on one that predates a new column.
    sync_missing_columns(engine)
