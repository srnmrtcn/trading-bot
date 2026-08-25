from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import Kline, PaperPosition, Scenario
from src.paper_position_closer import close_resolved_positions
from src.paper_trading_config import STARTING_EQUITY


def _scenario(symbol="BTCUSDT", direction="long", status="pending",
              target_price=Decimal("110"), stop_price=Decimal("90"),
              created_at=None, expires_at=None):
    created_at = created_at or datetime(2026, 1, 1)
    expires_at = expires_at or created_at + timedelta(hours=24)
    return Scenario(
        symbol=symbol, direction=direction,
        entry_price=Decimal("100"), target_price=target_price, stop_price=stop_price,
        expected_return_pct=Decimal("0.1"), confidence_score=Decimal("0.7"),
        created_at=created_at, expires_at=expires_at, status=status,
        calibrated_confidence=Decimal("0.7"),
    )


def _open_position(scenario, size=Decimal("10")):
    return PaperPosition(
        scenario_id=scenario.id, symbol=scenario.symbol, direction=scenario.direction,
        entry_price=scenario.entry_price, stop_price=scenario.stop_price, target_price=scenario.target_price,
        risk_amount=Decimal("100"), position_size=size,
        opened_at=scenario.created_at, status="open",
    )


def test_close_resolved_positions_closes_a_hit_target_long(db_session):
    scenario = _scenario(status="hit_target")
    db_session.add(scenario)
    db_session.commit()
    db_session.add(_open_position(scenario))
    db_session.commit()

    result = close_resolved_positions(db_session, now=datetime(2026, 1, 2))

    assert result.scanned == 1
    assert result.closed == 1
    assert result.still_open == 0
    assert result.failed == 0
    reloaded = db_session.query(PaperPosition).first()
    assert reloaded.status == "closed"
    assert reloaded.exit_price == Decimal("110")
    assert reloaded.realized_pnl == Decimal("98.95")  # 10 * (110 - 100), less 1.05 fees
    assert reloaded.equity_before == STARTING_EQUITY
    assert reloaded.equity_after == STARTING_EQUITY + Decimal("98.95")
    assert reloaded.closed_at == datetime(2026, 1, 2)


def test_close_resolved_positions_closes_a_hit_stop_short(db_session):
    scenario = _scenario(direction="short", status="hit_stop", target_price=Decimal("90"), stop_price=Decimal("110"))
    db_session.add(scenario)
    db_session.commit()
    db_session.add(_open_position(scenario))
    db_session.commit()

    result = close_resolved_positions(db_session)

    assert result.closed == 1
    reloaded = db_session.query(PaperPosition).first()
    assert reloaded.exit_price == Decimal("110")
    # Fees widen a loss as surely as they shrink a gain: -100 gross, -101.05 net.
    assert reloaded.realized_pnl == Decimal("-101.05")
    assert reloaded.equity_after == STARTING_EQUITY - Decimal("101.05")


def test_close_resolved_positions_marks_to_market_on_expiry(db_session):
    scenario = _scenario(status="expired", expires_at=datetime(2026, 1, 2))
    db_session.add(scenario)
    db_session.commit()
    db_session.add(_open_position(scenario))
    db_session.add(Kline(
        symbol="BTCUSDT", timeframe="1h", open_time=datetime(2026, 1, 1, 23),
        open=Decimal("100"), high=Decimal("106"), low=Decimal("99"), close=Decimal("105"),
        volume=Decimal("1000"), flagged=False,
    ))
    db_session.commit()

    result = close_resolved_positions(db_session)

    assert result.closed == 1
    reloaded = db_session.query(PaperPosition).first()
    assert reloaded.exit_price == Decimal("105")
    assert reloaded.realized_pnl == Decimal("48.975")  # 10 * (105 - 100), less 1.025 fees


def test_close_resolved_positions_defers_expiry_with_no_kline_data(db_session):
    scenario = _scenario(status="expired", expires_at=datetime(2026, 1, 2))
    db_session.add(scenario)
    db_session.commit()
    db_session.add(_open_position(scenario))
    db_session.commit()

    result = close_resolved_positions(db_session)

    assert result.scanned == 1
    assert result.closed == 0
    assert result.still_open == 1
    reloaded = db_session.query(PaperPosition).first()
    assert reloaded.status == "open"


def test_close_resolved_positions_leaves_pending_scenario_positions_untouched(db_session):
    scenario = _scenario(status="pending")
    db_session.add(scenario)
    db_session.commit()
    db_session.add(_open_position(scenario))
    db_session.commit()

    result = close_resolved_positions(db_session)

    assert result.scanned == 0
    reloaded = db_session.query(PaperPosition).first()
    assert reloaded.status == "open"


def test_close_resolved_positions_chains_equity_across_two_closes(db_session):
    s1 = _scenario(symbol="BTCUSDT", status="hit_target", created_at=datetime(2026, 1, 1, 0))
    s2 = _scenario(symbol="ETHUSDT", status="hit_target", created_at=datetime(2026, 1, 1, 1))
    db_session.add_all([s1, s2])
    db_session.commit()
    db_session.add(_open_position(s1))
    db_session.add(_open_position(s2))
    db_session.commit()

    result = close_resolved_positions(db_session)

    assert result.closed == 2
    first = db_session.query(PaperPosition).filter(PaperPosition.symbol == "BTCUSDT").first()
    second = db_session.query(PaperPosition).filter(PaperPosition.symbol == "ETHUSDT").first()
    assert first.equity_before == STARTING_EQUITY
    assert second.equity_before == first.equity_after
    assert second.equity_after == first.equity_after + Decimal("98.95")


def test_close_resolved_positions_equity_chain_survives_id_order_diverging_from_created_at(db_session):
    from src.paper_equity import current_equity

    # This scenario was CREATED later but its POSITION was OPENED first (lower
    # id) — an ordinary case: its symbol had an earlier open position that
    # blocked it, which has since closed and freed the symbol. If the closer's
    # close order ever drifts from id order again, this must fail.
    later_created = _scenario(symbol="ETHUSDT", status="hit_target", created_at=datetime(2026, 1, 1, 1))
    earlier_created = _scenario(symbol="BTCUSDT", status="hit_target", created_at=datetime(2026, 1, 1, 0))
    db_session.add_all([later_created, earlier_created])
    db_session.commit()
    db_session.add(_open_position(later_created))    # gets the lower id
    db_session.add(_open_position(earlier_created))  # gets the higher id
    db_session.commit()

    result = close_resolved_positions(db_session)

    assert result.closed == 2
    assert current_equity(db_session) == STARTING_EQUITY + Decimal("197.90")


def test_close_resolved_positions_warns_about_stuck_positions(db_session, caplog):
    import logging
    stuck_scenario = _scenario(status="pending", created_at=datetime(2026, 1, 1), expires_at=datetime(2026, 1, 2))
    db_session.add(stuck_scenario)
    db_session.commit()
    db_session.add(_open_position(stuck_scenario))
    db_session.commit()

    with caplog.at_level(logging.WARNING, logger="paper_position_closer"):
        result = close_resolved_positions(db_session, now=datetime(2026, 1, 5))

    assert result.scanned == 0  # pending scenario, never entered the main loop
    assert any("stuck" in record.getMessage() for record in caplog.records)


def test_close_resolved_positions_isolates_a_failing_position(db_session, monkeypatch):
    good = _scenario(symbol="BTCUSDT", status="hit_target", created_at=datetime(2026, 1, 1, 0))
    bad = _scenario(
        symbol="ETHUSDT", status="hit_target", target_price=Decimal("999"),
        created_at=datetime(2026, 1, 1, 1),
    )
    db_session.add_all([good, bad])
    db_session.commit()
    db_session.add(_open_position(good))
    db_session.add(_open_position(bad))
    db_session.commit()

    import src.paper_position_closer as closer_module
    real_realized_pnl = closer_module._realized_pnl

    def flaky_realized_pnl(direction, position_size, entry_price, exit_price):
        if exit_price == Decimal("999"):
            raise RuntimeError("boom")
        return real_realized_pnl(direction, position_size, entry_price, exit_price)

    monkeypatch.setattr(closer_module, "_realized_pnl", flaky_realized_pnl)

    result = close_resolved_positions(db_session)

    assert result.scanned == 2
    assert result.closed == 1
    assert result.failed == 1
    good_reloaded = db_session.query(PaperPosition).filter(PaperPosition.symbol == "BTCUSDT").first()
    bad_reloaded = db_session.query(PaperPosition).filter(PaperPosition.symbol == "ETHUSDT").first()
    assert good_reloaded.status == "closed"
    assert bad_reloaded.status == "open"


def test_realized_pnl_is_booked_net_of_both_legs_of_taker_fees(db_session):
    """A paper portfolio that books no costs measures a strategy nobody can
    trade. Entering and exiting are two taker fills, each charged on that
    leg's own notional: 10 units in at 100 and out at 110 is 0.05% of 1000
    plus 0.05% of 1100 — 1.05 off a gross 100.
    """
    scenario = _scenario(status="hit_target")
    db_session.add(scenario)
    db_session.commit()
    db_session.add(_open_position(scenario))
    db_session.commit()

    close_resolved_positions(db_session, now=datetime(2026, 1, 2))

    reloaded = db_session.query(PaperPosition).first()
    assert reloaded.realized_pnl == Decimal("98.95")
    assert reloaded.equity_after == STARTING_EQUITY + Decimal("98.95")
