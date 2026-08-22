from __future__ import annotations

from datetime import datetime
from decimal import Decimal


def evaluate_outcome(
    direction: str,
    target_price: Decimal,
    stop_price: Decimal,
    expires_at: datetime,
    klines: list,
    now: datetime,
):
    """Has this scenario resolved yet?

    `klines` are ascending dicts with `open_time`, `high`, `low`, covering the
    window from just after the scenario's creation up to `now`. Returns
    `(status, resolved_at)` if resolved, else `None` (still pending).

    A candle that touches both target and stop resolves as `hit_stop` — the
    conservative assumption, since intra-candle ordering isn't known.
    """
    for kline in klines:
        if direction == "long":
            stop_hit = kline["low"] <= stop_price
            target_hit = kline["high"] >= target_price
        else:
            stop_hit = kline["high"] >= stop_price
            target_hit = kline["low"] <= target_price

        if stop_hit:
            return ("hit_stop", kline["open_time"])
        if target_hit:
            return ("hit_target", kline["open_time"])

    if now > expires_at:
        return ("expired", expires_at)
    return None
