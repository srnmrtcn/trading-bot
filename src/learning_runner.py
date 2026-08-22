from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from src.confidence_calibrator import compute_success_rates, confidence_bucket
from src.db.models import Kline, Scenario
from src.integrity import floor_to_timeframe
from src.outcome_evaluator import evaluate_outcome
from src.timeutil import utc_now

logger = logging.getLogger("learning_runner")


@dataclass
class OutcomeResolutionResult:
    scanned: int
    resolved: int
    still_pending: int
    failed: int


def _load_klines_since(session, symbol: str, timeframe: str, since: datetime) -> list:
    rows = (
        session.query(Kline)
        .filter(Kline.symbol == symbol, Kline.timeframe == timeframe, Kline.open_time >= since)
        .order_by(Kline.open_time.asc())
        .all()
    )
    return [{"open_time": row.open_time, "high": row.high, "low": row.low} for row in rows]


def resolve_pending_scenarios(session, now: datetime = None) -> OutcomeResolutionResult:
    now = now if now is not None else utc_now()
    pending = session.query(Scenario).filter(Scenario.status == "pending").all()

    scanned = 0
    resolved = 0
    still_pending = 0
    failed = 0
    for scenario in pending:
        scanned += 1
        try:
            since = floor_to_timeframe(scenario.created_at, "1h")
            klines = _load_klines_since(session, scenario.symbol, "1h", since)
            outcome = evaluate_outcome(
                scenario.direction, scenario.target_price, scenario.stop_price,
                scenario.expires_at, klines, now,
            )
            if outcome is not None:
                status, resolved_at = outcome
                scenario.status = status
                scenario.resolved_at = resolved_at
                session.commit()
                resolved += 1
            else:
                still_pending += 1
        except Exception:
            session.rollback()
            logger.exception("Outcome resolution failed for scenario %s (%s)", scenario.id, scenario.symbol)
            failed += 1

    return OutcomeResolutionResult(scanned=scanned, resolved=resolved, still_pending=still_pending, failed=failed)


@dataclass
class CalibrationResult:
    scenarios_updated: int
    patterns_with_data: int


def calibrate_scenarios(session) -> CalibrationResult:
    resolved_records = [
        (row.direction, row.confidence_score, row.status)
        for row in session.query(Scenario).filter(Scenario.status != "pending").all()
    ]
    rates = compute_success_rates(resolved_records)
    patterns_with_data = sum(1 for rate, _count in rates.values() if rate is not None)

    targets = session.query(Scenario).filter(Scenario.calibrated_confidence.is_(None)).all()
    scenarios_updated = 0
    for scenario in targets:
        key = (scenario.direction, confidence_bucket(scenario.confidence_score))
        rate, _count = rates.get(key, (None, 0))
        scenario.calibrated_confidence = rate if rate is not None else scenario.confidence_score
        scenarios_updated += 1
    session.commit()

    return CalibrationResult(scenarios_updated=scenarios_updated, patterns_with_data=patterns_with_data)
