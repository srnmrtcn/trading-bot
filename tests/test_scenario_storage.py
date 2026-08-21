from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import Scenario
from src.scenario_builder import ScenarioDraft
from src.scenario_storage import has_pending_scenario, insert_scenario


def test_has_pending_scenario_false_when_none_exists(db_session):
    assert has_pending_scenario(db_session, "BTCUSDT", "long") is False


def test_has_pending_scenario_true_when_one_exists(db_session):
    now = datetime(2026, 1, 1)
    db_session.add(Scenario(
        symbol="BTCUSDT", direction="long",
        entry_price=Decimal("100"), target_price=Decimal("110"), stop_price=Decimal("95"),
        expected_return_pct=Decimal("0.1"), confidence_score=Decimal("0.6"),
        created_at=now, expires_at=now + timedelta(hours=24), status="pending",
    ))
    db_session.commit()
    assert has_pending_scenario(db_session, "BTCUSDT", "long") is True
    assert has_pending_scenario(db_session, "BTCUSDT", "short") is False


def test_insert_scenario_persists_a_pending_row(db_session):
    now = datetime(2026, 1, 1)
    draft = ScenarioDraft(
        symbol="ETHUSDT", direction="short",
        entry_price=Decimal("3000"), target_price=Decimal("2900"), stop_price=Decimal("3050"),
        expected_return_pct=Decimal("0.033"), confidence_score=Decimal("0.4"),
        created_at=now, expires_at=now + timedelta(hours=12),
    )
    insert_scenario(db_session, draft)
    row = db_session.query(Scenario).filter(Scenario.symbol == "ETHUSDT").first()
    assert row is not None
    assert row.status == "pending"
    assert row.direction == "short"
