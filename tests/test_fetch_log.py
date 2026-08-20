from datetime import datetime

from src.fetch_log import record_run, get_last_successful_run


def test_get_last_successful_run_returns_none_when_no_history(db_session):
    assert get_last_successful_run(db_session, "BTCUSDT", "1h") is None


def test_get_last_successful_run_returns_latest_success_only(db_session):
    record_run(db_session, "BTCUSDT", "1h", status="success",
               started_at=datetime(2026, 1, 1, 0), finished_at=datetime(2026, 1, 1, 0, 5))
    record_run(db_session, "BTCUSDT", "1h", status="error",
               started_at=datetime(2026, 1, 2, 0), finished_at=datetime(2026, 1, 2, 0, 5),
               error_message="boom")
    record_run(db_session, "BTCUSDT", "1h", status="success",
               started_at=datetime(2026, 1, 3, 0), finished_at=datetime(2026, 1, 3, 0, 5))

    result = get_last_successful_run(db_session, "BTCUSDT", "1h")
    assert result == datetime(2026, 1, 3, 0, 5)
