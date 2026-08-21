from __future__ import annotations

import logging
from dataclasses import dataclass

from src.db.models import Kline
from src.scenario_builder import build_scenario
from src.scenario_signal import MIN_CANDLES, evaluate_signal
from src.scenario_storage import has_pending_scenario, insert_scenario
from src.timeutil import utc_now

logger = logging.getLogger("scenario_runner")


@dataclass
class ScenarioRunResult:
    scanned: int
    generated: int
    skipped: int
    failed: int


def _load_recent_klines(session, symbol: str, timeframe: str, limit: int) -> list:
    rows = (
        session.query(Kline)
        .filter(Kline.symbol == symbol, Kline.timeframe == timeframe)
        .order_by(Kline.open_time.desc())
        .limit(limit)
        .all()
    )
    rows.reverse()
    return [
        {
            "open_time": row.open_time, "open": row.open, "high": row.high,
            "low": row.low, "close": row.close, "volume": row.volume,
        }
        for row in rows
    ]


def process_symbol_scenario(session, symbol: str, timeframe: str = "1h") -> str:
    klines = _load_recent_klines(session, symbol, timeframe, MIN_CANDLES)
    if len(klines) < MIN_CANDLES:
        return "skipped"

    signal = evaluate_signal(klines)
    if signal is None:
        return "skipped"

    if has_pending_scenario(session, symbol, signal.direction):
        return "skipped"

    draft = build_scenario(symbol, signal, klines, utc_now())
    if draft is None:
        return "skipped"

    insert_scenario(session, draft)
    return "generated"


def run_scenario_generation(session, symbols: list) -> ScenarioRunResult:
    scanned = 0
    generated = 0
    skipped = 0
    failed = 0
    for symbol in symbols:
        scanned += 1
        try:
            outcome = process_symbol_scenario(session, symbol)
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
