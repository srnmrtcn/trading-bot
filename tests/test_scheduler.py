from datetime import datetime

from src.db.models import Symbol
from src.scheduler import build_scheduler, run_timeframe_job, run_symbol_refresh_job
from src.db.models import FetchLog


class _FakeBinanceClient:
    def __init__(self):
        self.symbols_requested = []

    def get_active_usdt_symbols(self):
        return [{"symbol": "BTCUSDT", "base_asset": "BTC", "quote_asset": "USDT"}]

    def get_klines(self, symbol, timeframe, start_ms, end_ms):
        self.symbols_requested.append(symbol)
        return []


def test_build_scheduler_registers_expected_jobs():
    scheduler = build_scheduler(session_factory=lambda: None, binance_client=None)
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert job_ids == {"hourly_klines", "daily_klines", "symbol_refresh"}


def test_run_timeframe_job_fetches_active_symbols_and_records_log(db_session):
    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True))
    db_session.commit()
    fake_client = _FakeBinanceClient()

    run_timeframe_job(session_factory=lambda: db_session, binance_client=fake_client, timeframe="1h")

    assert fake_client.symbols_requested == ["BTCUSDT"]
    log_row = db_session.query(FetchLog).first()
    assert log_row.symbol == "BTCUSDT"
    assert log_row.status == "success"


def test_run_symbol_refresh_job_upserts_symbols(db_session):
    fake_client = _FakeBinanceClient()
    run_symbol_refresh_job(session_factory=lambda: db_session, binance_client=fake_client)
    assert db_session.get(Symbol, "BTCUSDT") is not None
