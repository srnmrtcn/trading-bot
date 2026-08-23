from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from src.db.models import Kline, PaperPosition, Scenario
from src.paper_equity import current_equity
from src.timeutil import utc_now

logger = logging.getLogger("paper_position_closer")

RESOLUTION_TIMEFRAME = "1h"


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
            Kline.open_time <= expires_at,
        )
        .order_by(Kline.open_time.desc())
        .first()
    )
    return row.close if row is not None else None


def _realized_pnl(direction: str, position_size: Decimal, entry_price: Decimal, exit_price: Decimal) -> Decimal:
    if direction == "long":
        return position_size * (exit_price - entry_price)
    return position_size * (entry_price - exit_price)


def close_resolved_positions(session, now: datetime = None) -> PositionCloseResult:
    now = now if now is not None else utc_now()
    open_positions = (
        session.query(PaperPosition)
        .join(Scenario, PaperPosition.scenario_id == Scenario.id)
        .filter(PaperPosition.status == "open", Scenario.status != "pending")
        .order_by(Scenario.created_at.asc(), PaperPosition.id.asc())
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
            elif scenario.status == "hit_stop":
                exit_price = scenario.stop_price
            else:  # "expired"
                exit_price = _exit_price_for_expired(session, symbol, scenario.expires_at)
                if exit_price is None:
                    logger.info(
                        "Deferring paper position %s (%s): no closed candle at/before expiry yet",
                        position_id, symbol,
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
            session.commit()
            closed += 1
        except Exception:
            session.rollback()
            logger.exception("Closing paper position %s (%s) failed", position_id, symbol)
            failed += 1

    return PositionCloseResult(scanned=scanned, closed=closed, still_open=still_open, failed=failed)
