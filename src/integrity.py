from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

TIMEFRAME_DELTAS = {
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
}


_EPOCH = datetime(1970, 1, 1)


@dataclass
class Gap:
    start: datetime
    end: datetime


def floor_to_timeframe(dt: datetime, timeframe: str) -> datetime:
    """Round a naive UTC datetime down to the timeframe's candle boundary.

    Gap detection walks a grid starting at ``range_start``; if that bound is not
    aligned to real candle open times, every stored candle looks missing.
    """
    step = TIMEFRAME_DELTAS[timeframe]
    steps = (dt - _EPOCH) // step
    return _EPOCH + steps * step


def detect_gaps(existing_open_times: list, timeframe: str, range_start: datetime, range_end: datetime) -> list:
    step = TIMEFRAME_DELTAS[timeframe]
    expected = []
    cursor = range_start
    while cursor <= range_end:
        expected.append(cursor)
        cursor += step

    existing_set = set(existing_open_times)
    missing = [t for t in expected if t not in existing_set]
    if not missing:
        return []

    gaps = []
    gap_start = missing[0]
    prev = missing[0]
    for t in missing[1:]:
        if t - prev == step:
            prev = t
            continue
        gaps.append(Gap(start=gap_start, end=prev))
        gap_start = t
        prev = t
    gaps.append(Gap(start=gap_start, end=prev))
    return gaps


def flag_anomalies(rows: list, spike_threshold: Decimal = Decimal("0.5")) -> list:
    flagged_rows = []
    previous_close = None
    for row in rows:
        is_flagged = False
        if row["volume"] == 0:
            is_flagged = True
        if previous_close is not None and previous_close != 0:
            change = abs(row["close"] - previous_close) / previous_close
            if change > spike_threshold:
                is_flagged = True
        flagged_rows.append({**row, "flagged": is_flagged})
        previous_close = row["close"]
    return flagged_rows
