from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from src.paper_position_closer import PositionCloseResult, close_resolved_positions
from src.paper_position_opener import PositionOpenResult, open_qualifying_positions
from src.timeutil import utc_now

logger = logging.getLogger("paper_trading_runner")


@dataclass
class PaperTradingResult:
    closed: int
    still_open: int
    opened: int
    skipped: int
    failed: int


def run_paper_trading_cycle(session, now: datetime = None) -> PaperTradingResult:
    now = now if now is not None else utc_now()

    # Each half is wrapped separately: neither failing may stop the other from
    # running, nor swallow the summary below.
    close_result = PositionCloseResult(scanned=0, closed=0, still_open=0, failed=0)
    try:
        close_result = close_resolved_positions(session, now)
    except Exception:
        session.rollback()
        logger.exception("Closing paper positions failed")

    open_result = PositionOpenResult(scanned=0, opened=0, skipped=0, failed=0)
    try:
        open_result = open_qualifying_positions(session, now)
    except Exception:
        session.rollback()
        logger.exception("Opening paper positions failed")

    logger.info(
        "Paper trading cycle finished: %d closed, %d still open, %d opened, %d skipped, %d failed",
        close_result.closed, close_result.still_open, open_result.opened,
        open_result.skipped, close_result.failed + open_result.failed,
    )
    return PaperTradingResult(
        closed=close_result.closed, still_open=close_result.still_open,
        opened=open_result.opened, skipped=open_result.skipped,
        failed=close_result.failed + open_result.failed,
    )
