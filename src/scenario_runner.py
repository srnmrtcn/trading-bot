from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from src.btc_regime import compute_btc_regime
from src.db.models import Kline
from src.funding_gate import funding_rejection
from src.integrity import TIMEFRAME_DELTAS, floor_to_timeframe
from src.scenario_builder import build_scenario
from src.scenario_signal import MIN_CANDLES, evaluate_signal
from src.scenario_storage import has_pending_scenario, insert_scenario
from src.timeutil import utc_now

logger = logging.getLogger("scenario_runner")

# How many candles to *fetch*, as opposed to MIN_CANDLES ("the minimum needed
# to evaluate a signal"). The still-forming candle is excluded by the query's
# `open_time < before` filter before LIMIT is applied, so MIN_CANDLES alone
# already returns MIN_CANDLES closed rows when they exist. The one extra candle
# of headroom here is a small margin for the RSI/EMA warm-up and the contiguity
# check, not something the "skip forever" behavior depends on.
SCENARIO_LOOKBACK = MIN_CANDLES + 1


@dataclass
class ScenarioRunResult:
    scanned: int
    generated: int
    skipped: int
    failed: int


def _load_recent_klines(session, symbol: str, timeframe: str, limit: int, before: datetime) -> list:
    """The most recent `limit` **closed** candles for a symbol, oldest first.

    `before` is the current candle boundary. The row at that open_time is the
    partially-formed candle the hourly job re-fetches and overwrites every run
    (see `scheduler.get_resume_point`); its ~5 minutes of accumulated volume
    compared against a 20-hour average would structurally disable the
    volume-spike gate, and the design spec requires the crossover to be read on
    the most recently *closed* candle.
    """
    rows = (
        session.query(Kline)
        .filter(
            Kline.symbol == symbol,
            Kline.timeframe == timeframe,
            Kline.open_time < before,
        )
        .order_by(Kline.open_time.desc())
        .limit(limit)
        .all()
    )
    rows.reverse()
    return [
        {
            "open_time": row.open_time, "open": row.open, "high": row.high,
            "low": row.low, "close": row.close, "volume": row.volume,
            "flagged": row.flagged,
        }
        for row in rows
    ]


def _window_rejection(klines: list, timeframe: str, current_boundary: datetime):
    """Why this candle window must not be evaluated, or None if it is usable.

    Every one of these is a normal outcome, not an error: the caller turns a
    reason into a `"skipped"` result and tries again next run.
    """
    if len(klines) < MIN_CANDLES:
        return "only %d of %d required candles" % (len(klines), MIN_CANDLES)

    step = TIMEFRAME_DELTAS[timeframe]
    newest = klines[-1]["open_time"]

    # Stale data: this run's fetch failed, or the process was down. The prices
    # would be hours old while created_at/expires_at say "now".
    if newest < current_boundary - step:
        return "stale data: newest closed candle is %s, expected %s" % (newest, current_boundary - step)

    # Subsystem A tolerates permanently unfillable gaps, so "the last N rows"
    # can span far more than N candles. RSI/EMA/ATR and the volume average all
    # assume adjacency, and build_scenario reads ATR as "per hour".
    if newest - klines[0]["open_time"] != (len(klines) - 1) * step:
        return "non-contiguous candle window (gap in recent history)"

    # An anomaly-flagged candle (zero volume, or a >50% close-to-close move) is
    # exactly the shape that manufactures a spurious RSI + EMA cross.
    if any(row["flagged"] for row in klines):
        return "anomaly-flagged candle in the recent window"

    return None


def process_symbol_scenario(session, symbol: str, regime: str | None, timeframe: str = "1h", now: datetime = None) -> str:
    now = now if now is not None else utc_now()
    current_boundary = floor_to_timeframe(now, timeframe)
    klines = _load_recent_klines(session, symbol, timeframe, SCENARIO_LOOKBACK, current_boundary)

    rejection = _window_rejection(klines, timeframe, current_boundary)
    if rejection is not None:
        logger.debug("Skipping %s %s: %s", symbol, timeframe, rejection)
        return "skipped"

    signal = evaluate_signal(klines)
    if signal is None:
        return "skipped"

    if regime is None:
        return "skipped"
    if signal.direction == "long" and regime != "up":
        return "skipped"
    if signal.direction == "short" and regime != "down":
        return "skipped"

    funding_block = funding_rejection(session, symbol, signal.direction, now)
    if funding_block is not None:
        logger.debug("Skipping %s: %s", symbol, funding_block)
        return "skipped"

    if has_pending_scenario(session, symbol, signal.direction, now):
        return "skipped"

    draft = build_scenario(symbol, signal, klines, now)
    if draft is None:
        return "skipped"

    insert_scenario(session, draft)
    return "generated"


def run_scenario_generation(session, symbols: list, now: datetime = None) -> ScenarioRunResult:
    now = now if now is not None else utc_now()
    regime = compute_btc_regime(session, now)
    if regime is None:
        logger.warning("BTC regime undetermined — no scenarios will be generated this run")
    else:
        logger.info("BTC regime: %s", regime)
    scanned = 0
    generated = 0
    skipped = 0
    failed = 0
    for symbol in symbols:
        scanned += 1
        try:
            outcome = process_symbol_scenario(session, symbol, regime, now=now)
        except Exception:
            session.rollback()
            logger.exception("Scenario generation failed for %s", symbol)
            failed += 1
            continue
        if outcome == "generated":
            generated += 1
        else:
            skipped += 1
    logger.info(
        "Scenario generation finished: %d scanned, %d generated, %d skipped, %d failed",
        scanned, generated, skipped, failed,
    )
    return ScenarioRunResult(scanned=scanned, generated=generated, skipped=skipped, failed=failed)
