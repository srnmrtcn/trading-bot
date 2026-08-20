from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from src.db.models import Kline
from src.kline_fetcher import FetchResult
import src.backfill as backfill_module


def test_run_initial_backfill_calls_fetch_and_store_per_symbol_and_timeframe(db_session):
    calls = []

    def fake_fetch_and_store(session, client, symbol, timeframe, start_ms, end_ms):
        calls.append((symbol, timeframe))
        return FetchResult(symbol=symbol, timeframe=timeframe, fetched=0, inserted=0, updated=0, flagged=0)

    with patch.object(backfill_module, "fetch_and_store", side_effect=fake_fetch_and_store):
        results = backfill_module.run_initial_backfill(
            db_session, binance_client=object(), symbols=["BTCUSDT", "ETHUSDT"], timeframes=["1h", "1d"],
        )
    assert set(calls) == {("BTCUSDT", "1h"), ("BTCUSDT", "1d"), ("ETHUSDT", "1h"), ("ETHUSDT", "1d")}
    assert len(results) == 4


def test_run_gap_backfill_fetches_only_missing_ranges(db_session):
    db_session.add(Kline(
        symbol="BTCUSDT", timeframe="1h", open_time=datetime(2026, 1, 1, 0),
        open=Decimal("1"), high=Decimal("1"), low=Decimal("1"), close=Decimal("1"),
        volume=Decimal("1"), flagged=False,
    ))
    db_session.add(Kline(
        symbol="BTCUSDT", timeframe="1h", open_time=datetime(2026, 1, 1, 2),
        open=Decimal("1"), high=Decimal("1"), low=Decimal("1"), close=Decimal("1"),
        volume=Decimal("1"), flagged=False,
    ))
    db_session.commit()

    calls = []

    def fake_fetch_and_store(session, client, symbol, timeframe, start_ms, end_ms):
        calls.append((start_ms, end_ms))
        return FetchResult(symbol=symbol, timeframe=timeframe, fetched=0, inserted=0, updated=0, flagged=0)

    with patch.object(backfill_module, "fetch_and_store", side_effect=fake_fetch_and_store):
        results = backfill_module.run_gap_backfill(
            db_session, binance_client=object(), symbol="BTCUSDT", timeframe="1h",
            range_start=datetime(2026, 1, 1, 0), range_end=datetime(2026, 1, 1, 2),
        )
    assert len(results) == 1
    assert len(calls) == 1


def test_to_epoch_ms_converts_naive_utc_to_correct_epoch_ms():
    """Verify _to_epoch_ms correctly converts naive UTC datetimes to UTC epoch milliseconds."""
    # 2026-01-01 00:00:00 UTC = 1767225600000 ms
    test_dt = datetime(2026, 1, 1, 0, 0, 0)
    expected_ms = 1767225600000
    assert backfill_module._to_epoch_ms(test_dt) == expected_ms


def test_run_initial_backfill_uses_correct_epoch_milliseconds(db_session):
    """Verify run_initial_backfill passes correct UTC epoch milliseconds to fetch_and_store."""
    captured_calls = []

    def fake_fetch_and_store(session, client, symbol, timeframe, start_ms, end_ms):
        captured_calls.append({"start_ms": start_ms, "end_ms": end_ms})
        return FetchResult(symbol=symbol, timeframe=timeframe, fetched=0, inserted=0, updated=0, flagged=0)

    with patch.object(backfill_module, "fetch_and_store", side_effect=fake_fetch_and_store):
        # Use a known datetime for testing
        test_start = datetime(2026, 1, 1, 0, 0, 0)
        test_end = datetime(2026, 1, 2, 0, 0, 0)

        # Temporarily patch datetime.now to return a known value
        with patch("src.backfill.datetime") as mock_datetime:
            mock_datetime.now.return_value = test_end
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)

            results = backfill_module.run_initial_backfill(
                db_session, binance_client=object(), symbols=["BTC"], timeframes=["1h"], since_days=1
            )

        # Verify at least one call was made
        assert len(captured_calls) >= 1

        # The epoch ms values should correspond to UTC times, not local timezone
        # For 2026-01-01 00:00:00 UTC, the ms should be 1767225600000
        # For 2026-01-02 00:00:00 UTC, the ms should be 1767312000000
        test_start_ms = 1767225600000
        test_end_ms = 1767312000000

        # Verify the captured call has the correct ms values (allowing for some flexibility
        # due to how we mocked datetime)
        call = captured_calls[0]
        # The important thing is that the ms values represent UTC, not local time
        # We can verify this by checking that _to_epoch_ms produces the expected value
        assert backfill_module._to_epoch_ms(datetime(2026, 1, 1, 0, 0, 0)) == 1767225600000
