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


def flag_anomalies(rows: list, spike_threshold: Decimal = Decimal("0.5"), previous_close=None) -> list:
    flagged_rows = []
    for i, row in enumerate(rows):
        is_flagged = False
        if row["volume"] == 0:
            is_flagged = True
        # Check for spike only if not the first row or if previous_close is provided and non-zero
        if i > 0 or (previous_close is not None and previous_close != 0):
            if i == 0 and previous_close is not None:
                # Use previous_close for the first row check
                current_close = row["close"]
                change = abs(current_close - previous_close) / previous_close
                if change > spike_threshold:
                    is_flagged = True
            elif i > 0:
                # Normal consecutive comparison
                current_close = row["close"]
                prev_close = rows[i-1]["close"]
                change = abs(current_close - prev_close) / prev_close
                if change > spike_threshold:
                    is_flagged = True
        flagged_rows.append({**row, "flagged": is_flagged})
    return flagged_rows
