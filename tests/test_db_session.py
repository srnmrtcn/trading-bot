from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import Column, MetaData, Table, create_engine, inspect
from sqlalchemy.orm import sessionmaker

from src.db.models import Scenario
from src.db.session import create_all_tables, sync_missing_columns

# The two columns Subsystem C added to the already-existing `scenarios` table.
SUBSYSTEM_C_COLUMNS = ("resolved_at", "calibrated_confidence")


def _create_pre_subsystem_c_database(engine):
    """A `scenarios` table as it existed before Subsystem C added its columns.

    Built from the live model minus those two columns, rather than a second
    hand-written copy of the schema, so it cannot drift from the real thing.
    """
    old_metadata = MetaData()
    Table(
        "scenarios",
        old_metadata,
        *[
            Column(
                column.name, column.type,
                primary_key=column.primary_key, nullable=column.nullable,
            )
            for column in Scenario.__table__.columns
            if column.name not in SUBSYSTEM_C_COLUMNS
        ],
    )
    old_metadata.create_all(engine)


def _column_names(engine, table_name):
    return {column["name"] for column in inspect(engine).get_columns(table_name)}


def test_sync_missing_columns_adds_columns_a_pre_existing_table_lacks():
    engine = create_engine("sqlite:///:memory:")
    _create_pre_subsystem_c_database(engine)
    assert not _column_names(engine, "scenarios") & set(SUBSYSTEM_C_COLUMNS)

    added = sync_missing_columns(engine)

    assert set(added) == {"scenarios.%s" % name for name in SUBSYSTEM_C_COLUMNS}
    assert set(SUBSYSTEM_C_COLUMNS) <= _column_names(engine, "scenarios")


def test_sync_missing_columns_is_a_no_op_on_a_current_database():
    """Idempotent: it runs on every startup, so the common case must do nothing."""
    engine = create_engine("sqlite:///:memory:")
    create_all_tables(engine)

    assert sync_missing_columns(engine) == []
    assert sync_missing_columns(engine) == []


def test_sync_missing_columns_leaves_absent_tables_to_create_all():
    engine = create_engine("sqlite:///:memory:")
    _create_pre_subsystem_c_database(engine)

    # `klines`/`symbols`/`fetch_log` do not exist at all here; ALTER TABLE on a
    # missing table would raise rather than being create_all's job.
    added = sync_missing_columns(engine)

    assert all(name.startswith("scenarios.") for name in added)


def test_create_all_tables_upgrades_a_database_that_predates_the_new_columns():
    """The whole point: an existing DB stays queryable after a model gains a column.

    `create_all` alone leaves `scenarios` one version behind, and *every* ORM
    query on it then fails — the SELECT enumerates all mapped columns, so even
    Subsystem B's `has_pending_scenario` dedup check breaks, silently, inside
    the scheduler's exception handlers.
    """
    engine = create_engine("sqlite:///:memory:")
    _create_pre_subsystem_c_database(engine)

    create_all_tables(engine)

    now = datetime(2026, 1, 1, 12, 0, 0)
    session = sessionmaker(bind=engine, future=True)()
    try:
        session.add(Scenario(
            symbol="BTCUSDT", direction="long",
            entry_price=Decimal("50000"), target_price=Decimal("52000"), stop_price=Decimal("49000"),
            expected_return_pct=Decimal("0.04"), confidence_score=Decimal("0.7"),
            created_at=now, expires_at=now + timedelta(hours=24), status="pending",
        ))
        session.commit()

        row = session.query(Scenario).first()
        assert row.resolved_at is None
        assert row.calibrated_confidence is None

        row.resolved_at = now + timedelta(hours=3)
        row.calibrated_confidence = Decimal("0.65")
        session.commit()

        reloaded = session.query(Scenario).first()
        assert reloaded.resolved_at == now + timedelta(hours=3)
        assert reloaded.calibrated_confidence == Decimal("0.65")
    finally:
        session.close()


def test_engine_pre_pings_and_recycles_pooled_connections():
    """Railway's Postgres drops idle connections, and the scheduler leaves one
    idle for ~55 minutes between hourly jobs. Without pre-ping the next job
    checks out a dead socket and dies on `server closed the connection
    unexpectedly`; recycling caps how long a connection can go stale.
    """
    from src.db.session import POOL_RECYCLE_SECONDS, make_engine

    engine = make_engine("postgresql+psycopg2://user:pw@example.invalid/db")

    assert engine.pool._pre_ping is True
    assert engine.pool._recycle == POOL_RECYCLE_SECONDS
