from __future__ import annotations

from datetime import datetime, timezone

# How far back history is fetched for a symbol/timeframe that has no rows yet.
# Shared so the startup backfill and the scheduled job's "no data yet" fallback
# can never drift apart and give the same symbol two different windows.
# 90 days is far more than MIN_CANDLES (scenario_signal.py) needs on the 1h
# timeframe — kept short so the initial, synchronous startup backfill across
# every active symbol finishes quickly instead of stalling the boot sequence.
DEFAULT_BACKFILL_DAYS = 90


def to_epoch_ms(naive_utc_dt: datetime) -> int:
    """Convert a naive datetime that represents UTC into epoch milliseconds."""
    return int(naive_utc_dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def utc_now() -> datetime:
    """Current UTC time as a naive datetime (the form stored in the database)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
