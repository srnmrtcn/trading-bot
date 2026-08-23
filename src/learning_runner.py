from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.confidence_calibrator import compute_success_rates, confidence_bucket
from src.db.models import Kline, Scenario
from src.integrity import TIMEFRAME_DELTAS, floor_to_timeframe
from src.outcome_evaluator import evaluate_outcome
from src.timeutil import utc_now

logger = logging.getLogger("learning_runner")

# Subsystem B only ever writes 1h scenarios, and outcomes are resolved on the
# same candles the signal was read from.
RESOLUTION_TIMEFRAME = "1h"


@dataclass
class OutcomeResolutionResult:
    scanned: int
    resolved: int
    still_pending: int
    failed: int


def _load_resolution_window(session, symbol: str, timeframe: str, since: datetime, until: datetime) -> list:
    """A scenario's candles: `since` (inclusive) through `until` (exclusive).

    `until` is the scenario's expiry, or the boundary just past the still-forming
    candle, whichever comes first (see `_resolution_window_end`). Bounding the
    query at expiry means the caller can never hand `evaluate_outcome` a candle
    from after the scenario expired — after an outage a single pass can
    otherwise span the whole backfilled window — and the same bound scopes the
    completeness check below to the candles the outcome actually depends on.

    The still-forming candle IS included, deliberately: the high/low it has
    printed so far is real, observed price action, and "has price touched target
    or stop yet" is a fair question to ask of it. This is the opposite of
    `scenario_runner._load_recent_klines`, which excludes it because a partial
    candle's volume and close distort *indicator* inputs — a different concern.
    Don't "fix" this into matching Subsystem B.

    Anomaly-flagged candles (zero volume, a >50% move) are included for the same
    reason: flagged means "don't trust this as an indicator input", not "this
    price never happened". A level that a flagged candle reached was reached.
    """
    rows = (
        session.query(Kline)
        .filter(
            Kline.symbol == symbol,
            Kline.timeframe == timeframe,
            Kline.open_time >= since,
            Kline.open_time < until,
        )
        .order_by(Kline.open_time.asc())
        .all()
    )
    return [{"open_time": row.open_time, "high": row.high, "low": row.low} for row in rows]


def _resolution_window_end(expires_at: datetime, timeframe: str, now: datetime) -> datetime:
    """The exclusive end of the window that *can* be observed right now.

    Two bounds, whichever binds first: the scenario's expiry (price action after
    it is not the scenario's to claim) and the boundary just past the candle
    currently forming (nothing later has opened yet). Using this for both the
    query and the completeness check is what stops a scenario that is simply not
    due for its next candle from being mistaken for one with missing data.
    """
    step = TIMEFRAME_DELTAS[timeframe]
    return min(expires_at, floor_to_timeframe(now, timeframe) + step)


def _missing_candle_count(klines: list, timeframe: str, since: datetime, until: datetime) -> int:
    """How many candles this window should hold but does not.

    Subsystem A tolerates permanently unfillable gaps, so a stored window can be
    missing hours. The true high/low during a missing candle is unknown — it
    could have touched the target, the stop, or neither — so any outcome derived
    from an incomplete window is a guess dressed up as a resolution.

    Counted against the expected grid rather than measured as the span between
    the first and last stored candle. A span check only ever sees holes
    *between* stored rows, and silently accepts three windows that are just as
    underivable: one missing its start (the scenario was created as an outage
    began, so an early stop-out is invisible), one missing its end (a tail
    outage, whose unobserved hours would be scored `expired` — a false miss fed
    straight into the calibration pool), and one that is empty altogether. The
    count subsumes the span check: an interior hole is a missing candle too.

    The unique constraint on (symbol, timeframe, open_time) rules out duplicates
    inflating the stored count.
    """
    step = TIMEFRAME_DELTAS[timeframe]
    span = until - since
    if span <= timedelta(0):
        return 0
    expected = span // step
    if expected * step < span:
        expected += 1  # a partial trailing step still means one more candle
    return max(expected - len(klines), 0)


def resolve_pending_scenarios(session, now: datetime = None) -> OutcomeResolutionResult:
    now = now if now is not None else utc_now()
    pending = session.query(Scenario).filter(Scenario.status == "pending").all()

    scanned = 0
    resolved = 0
    still_pending = 0
    failed = 0
    for scenario in pending:
        scanned += 1
        # Captured before the try block, on purpose. After session.rollback()
        # these attributes are expired, so reading them in the except handler
        # would issue a refresh SELECT — which raises again if the failure was a
        # dead connection, taking the log line and every remaining scenario's
        # isolation down with it.
        scenario_id = scenario.id
        symbol = scenario.symbol
        try:
            since = floor_to_timeframe(scenario.created_at, RESOLUTION_TIMEFRAME)
            until = _resolution_window_end(scenario.expires_at, RESOLUTION_TIMEFRAME, now)
            klines = _load_resolution_window(session, symbol, RESOLUTION_TIMEFRAME, since, until)
            missing = _missing_candle_count(klines, RESOLUTION_TIMEFRAME, since, until)
            if missing:
                # A normal deferral, not an error: it stays pending and is
                # retried next run, exactly like the still_pending bucket's
                # other members. Logged at INFO because a window that never
                # fills in means this scenario is silently stuck.
                logger.info(
                    "Deferring scenario %s (%s): %d candle(s) missing from its resolution window",
                    scenario_id, symbol, missing,
                )
                still_pending += 1
                continue
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
            logger.exception("Outcome resolution failed for scenario %s (%s)", scenario_id, symbol)
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


@dataclass
class LearningRunResult:
    scanned: int
    resolved: int
    still_pending: int
    failed: int
    scenarios_calibrated: int


def run_learning_cycle(session, now: datetime = None) -> LearningRunResult:
    now = now if now is not None else utc_now()

    # Calibration runs BEFORE resolution, so a scenario is only ever scored
    # against outcomes that were already known when it was scored. Resolving
    # first lets a scenario that resolves in this very run — reachable, since
    # the still-forming candle is in scope — land in the pool its own
    # calibrated_confidence is computed from, which turns a prediction into a
    # partly post-hoc score. Subsystem D will read this field as a *predictive*
    # signal. Scenarios Subsystem B created minutes ago in the same job are
    # still calibrated in this same run, so nothing is left uncalibrated.
    scenarios_calibrated = 0
    try:
        calibration_result = calibrate_scenarios(session)
        scenarios_calibrated = calibration_result.scenarios_updated
    except Exception:
        session.rollback()
        logger.exception("Confidence calibration failed")

    # Each half is wrapped separately: neither failing may stop the other from
    # running, nor swallow the summary below.
    outcome_result = OutcomeResolutionResult(scanned=0, resolved=0, still_pending=0, failed=0)
    try:
        outcome_result = resolve_pending_scenarios(session, now)
    except Exception:
        session.rollback()
        logger.exception("Outcome resolution failed")

    logger.info(
        "Learning cycle finished: %d scanned, %d resolved, %d still pending, %d failed, %d scenarios calibrated",
        outcome_result.scanned, outcome_result.resolved, outcome_result.still_pending,
        outcome_result.failed, scenarios_calibrated,
    )
    return LearningRunResult(
        scanned=outcome_result.scanned, resolved=outcome_result.resolved,
        still_pending=outcome_result.still_pending, failed=outcome_result.failed,
        scenarios_calibrated=scenarios_calibrated,
    )
