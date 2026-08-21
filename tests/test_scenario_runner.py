from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import Kline, Scenario
from src.scenario_runner import run_scenario_generation, process_symbol_scenario
from src.scenario_signal import MIN_CANDLES


def _insert_flat_klines(db_session, symbol, count, price=Decimal("100")):
    for i in range(count):
        db_session.add(Kline(
            symbol=symbol, timeframe="1h",
            open_time=datetime(2026, 1, 1) + timedelta(hours=i),
            open=price, high=price, low=price, close=price, volume=Decimal("1000"), flagged=False,
        ))
    db_session.commit()


def test_process_symbol_scenario_skips_with_insufficient_data(db_session):
    _insert_flat_klines(db_session, "BTCUSDT", MIN_CANDLES - 1)
    outcome = process_symbol_scenario(db_session, "BTCUSDT")
    assert outcome == "skipped"


def test_process_symbol_scenario_skips_when_no_signal(db_session):
    _insert_flat_klines(db_session, "BTCUSDT", MIN_CANDLES)
    outcome = process_symbol_scenario(db_session, "BTCUSDT")
    assert outcome == "skipped"
    assert db_session.query(Scenario).count() == 0


def test_run_scenario_generation_isolates_symbol_failures(db_session, monkeypatch):
    _insert_flat_klines(db_session, "BTCUSDT", MIN_CANDLES)
    _insert_flat_klines(db_session, "ETHUSDT", MIN_CANDLES)

    import src.scenario_runner as scenario_runner_module

    def fake_process(session, symbol, timeframe="1h"):
        if symbol == "BTCUSDT":
            raise RuntimeError("boom")
        return "skipped"

    monkeypatch.setattr(scenario_runner_module, "process_symbol_scenario", fake_process)

    result = run_scenario_generation(db_session, ["BTCUSDT", "ETHUSDT"])

    assert result.scanned == 2
    assert result.failed == 1
    assert result.skipped == 1
    assert result.generated == 0
    # The session must still be usable after the failure (rolled back, not poisoned).
    assert db_session.query(Kline).filter(Kline.symbol == "ETHUSDT").count() == MIN_CANDLES


def test_run_scenario_generation_counts_generated(db_session, monkeypatch):
    _insert_flat_klines(db_session, "BTCUSDT", MIN_CANDLES)

    import src.scenario_runner as scenario_runner_module

    monkeypatch.setattr(scenario_runner_module, "process_symbol_scenario", lambda session, symbol, timeframe="1h": "generated")

    result = run_scenario_generation(db_session, ["BTCUSDT"])

    assert result.scanned == 1
    assert result.generated == 1
    assert result.skipped == 0
    assert result.failed == 0
