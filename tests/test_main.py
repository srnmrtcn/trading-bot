from unittest.mock import patch

import src.main as main_module
from src.db.models import Kline
from decimal import Decimal
from datetime import datetime


def test_startup_runs_initial_backfill_when_no_klines_exist(db_session, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")

    backfill_calls = []

    with patch.object(main_module, "make_engine", return_value=db_session.get_bind()), \
         patch.object(main_module, "create_all_tables", return_value=None), \
         patch.object(main_module, "make_session_factory", return_value=lambda: db_session), \
         patch.object(main_module, "BinanceClient", return_value=object()), \
         patch.object(main_module, "refresh_symbols", return_value=None), \
         patch.object(main_module, "run_initial_backfill", side_effect=lambda *a, **k: backfill_calls.append(a)):
        main_module.startup()

    assert len(backfill_calls) == 1


def test_startup_skips_initial_backfill_when_klines_already_exist(db_session, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    db_session.add(Kline(
        symbol="BTCUSDT", timeframe="1h", open_time=datetime(2026, 1, 1),
        open=Decimal("1"), high=Decimal("1"), low=Decimal("1"), close=Decimal("1"),
        volume=Decimal("1"), flagged=False,
    ))
    db_session.commit()

    backfill_calls = []

    with patch.object(main_module, "make_engine", return_value=db_session.get_bind()), \
         patch.object(main_module, "create_all_tables", return_value=None), \
         patch.object(main_module, "make_session_factory", return_value=lambda: db_session), \
         patch.object(main_module, "BinanceClient", return_value=object()), \
         patch.object(main_module, "refresh_symbols", return_value=None), \
         patch.object(main_module, "run_initial_backfill", side_effect=lambda *a, **k: backfill_calls.append(a)):
        main_module.startup()

    assert len(backfill_calls) == 0
