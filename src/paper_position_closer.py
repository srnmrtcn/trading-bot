from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import Kline, PaperPosition, Scenario
from src.paper_equity import current_equity
from src.paper_trading_config import TAKER_FEE_RATE
from src.timeutil import utc_now

logger = logging.getLogger("paper_position_closer")

RESOLUTION_TIMEFRAME = "1h"
UNRESOLVABLE_GRACE = timedelta(hours=1)  # 1 hour


@dataclass
class PositionCloseResult:
    scanned: int
    closed: int
    still_open: int
    failed: int


def _exit_price_for_expired(session, symbol: str, expires_at: datetime):
    row = (
        session.query(Kline)
        .filter(
            Kline.symbol == symbol,
            Kline.timeframe == RESOLUTION_TIMEFRAME,
            Kline.open_time < expires_at,
        )
        .order_by(Kline.open_time.desc())
        .first()
    )
    return row.close if row is not None else None


def _count_stuck_positions(session, now: datetime) -> int:
    """Open positions whose scenario never resolved and is now past expiry.

    These are invisible to the loop above (it only touches positions whose
    scenario is non-pending) and can never close on their own — Subsystem C
    left the scenario pending because its resolution window has a permanently
    unfillable gap. Not acted on here (inventing an exit price for a scenario
    C never resolved is not this closer's call to make) — logged so an
    operator can see a symbol/slot is stuck instead of a quiet portfolio
    deadlock.
    """
    return (
        session.query(PaperPosition)
        .join(Scenario, PaperPosition.scenario_id == Scenario.id)
        .filter(
            PaperPosition.status == "open",
            Scenario.status == "pending",
            Scenario.expires_at <= now,
        )
        .count()
    )


def _fees(position_size: Decimal, entry_price: Decimal, exit_price: Decimal) -> Decimal:
    """Taker fees for both legs, each on the notional actually transacted."""
    return position_size * (entry_price + exit_price) * TAKER_FEE_RATE


def _realized_pnl(direction: str, position_size: Decimal, entry_price: Decimal, exit_price: Decimal) -> Decimal:
    if direction == "long":
        gross = position_size * (exit_price - entry_price)
    else:
        gross = position_size * (entry_price - exit_price)
    return gross - _fees(position_size, entry_price, exit_price)


def close_resolved_positions(session, now: datetime = None) -> PositionCloseResult:
    now = now if now is not None else utc_now()
    open_positions = (
        session.query(PaperPosition)
        .join(Scenario, PaperPosition.scenario_id == Scenario.id)
        .filter(PaperPosition.status == "open", Scenario.status != "pending")
        # Close order must agree with current_equity's id-based tie-break (both
        # share `closed_at` within one cycle), not with scenario creation order.
        .order_by(PaperPosition.id.asc())
        .all()
    )

    scanned = 0
    closed = 0
    still_open = 0
    failed = 0
    for position in open_positions:
        scanned += 1
        # Captured before the try block: after session.rollback() these
        # attributes are expired, and reading them in the except handler
        # would issue a refresh SELECT that raises again on a dead connection.
        position_id = position.id
        symbol = position.symbol
        try:
            scenario = session.get(Scenario, position.scenario_id)
            if scenario.status == "hit_target":
                exit_price = scenario.target_price
                exit_reason = "target"
            elif scenario.status == "hit_stop":
                exit_price = scenario.stop_price
                exit_reason = "stop"
            elif scenario.status == "expired":
                exit_price = _exit_price_for_expired(session, symbol, scenario.expires_at)
                if exit_price is None:
                    logger.info(
                        "Deferring paper position %s (%s): no closed candle at/before expiry yet",
                        position_id, symbol,
                    )
                    still_open += 1
                    continue
                exit_reason = "expired"
            elif scenario.status == "unresolvable":
                # Check if the position is past its grace period for unresolvable scenarios
                if now > scenario.expires_at + UNRESOLVABLE_GRACE:
                    # Try to close with the last known kline price
                    exit_price = _exit_price_for_expired(session, symbol, scenario.expires_at)
                    if exit_price is None:
                        # If no kline available, leave position open
                        logger.info(
                            "Deferring paper position %s (%s): no closed candle at/before expiry yet",
                            position_id, symbol,
                        )
                        still_open += 1
                        continue
                    exit_reason = "forced"
                else:
                    # Still within grace period, leave position open
                    still_open += 1
                    continue
            else:
                logger.error(
                    "Unrecognized scenario status %r for paper position %s (%s) — leaving it open",
                    scenario.status, position_id, symbol,
                )
                still_open += 1
                continue

            equity_before = current_equity(session)
            realized_pnl = _realized_pnl(position.direction, position.position_size, position.entry_price, exit_price)
            position.exit_price = exit_price
            position.realized_pnl = realized_pnl
            position.equity_before = equity_before
            position.equity_after = equity_before + realized_pnl
            position.closed_at = now
            position.status = "closed"
            position.exit_reason = exit_reason
            session.commit()
            closed += 1
        except Exception:
            session.rollback()
            logger.exception("Closing paper position %s (%s) failed", position_id, symbol)
            failed += 1

    stuck = _count_stuck_positions(session, now)
    if stuck:
        logger.warning(
            "%d open paper position(s) stuck: scenario still pending past its expiry",
            stuck,
        )

    return PositionCloseResult(scanned=scanned, closed=closed, still_open=still_open, failed=failed)