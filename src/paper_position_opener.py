from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from src.db.models import PaperPosition, Scenario
from src.paper_equity import current_equity
from src.paper_sizer import size_position
from src.paper_trading_config import CONFIDENCE_THRESHOLD, MAX_CONCURRENT_POSITIONS, RISK_PCT
from src.timeutil import utc_now

logger = logging.getLogger("paper_position_opener")


@dataclass
class PositionOpenResult:
    scanned: int
    opened: int
    skipped: int
    failed: int


def open_qualifying_positions(session, now: datetime = None) -> PositionOpenResult:
    now = now if now is not None else utc_now()

    positioned_scenario_ids = {row[0] for row in session.query(PaperPosition.scenario_id).all()}
    candidates = (
        session.query(Scenario)
        .filter(
            Scenario.status == "pending",
            Scenario.expires_at > now,
            Scenario.calibrated_confidence.isnot(None),
            Scenario.calibrated_confidence >= CONFIDENCE_THRESHOLD,
        )
        .order_by(Scenario.created_at.asc())
        .all()
    )
    candidates = [scenario for scenario in candidates if scenario.id not in positioned_scenario_ids]

    open_symbols = {
        row[0] for row in session.query(PaperPosition.symbol).filter(PaperPosition.status == "open").all()
    }
    open_count = session.query(PaperPosition).filter(PaperPosition.status == "open").count()

    scanned = 0
    opened = 0
    skipped = 0
    failed = 0
    for scenario in candidates:
        scanned += 1
        scenario_id = scenario.id
        symbol = scenario.symbol
        try:
            if symbol in open_symbols:
                logger.debug("Skipping scenario %s: %s already has an open paper position", scenario_id, symbol)
                skipped += 1
                continue
            if open_count >= MAX_CONCURRENT_POSITIONS:
                logger.debug("Skipping scenario %s (%s): max concurrent positions reached", scenario_id, symbol)
                skipped += 1
                continue

            equity = current_equity(session)
            risk_amount, position_size = size_position(equity, scenario.entry_price, scenario.stop_price, RISK_PCT)

            session.add(PaperPosition(
                scenario_id=scenario.id, symbol=symbol, direction=scenario.direction,
                entry_price=scenario.entry_price, stop_price=scenario.stop_price, target_price=scenario.target_price,
                risk_amount=risk_amount, position_size=position_size,
                opened_at=now, status="open",
            ))
            session.commit()
            open_symbols.add(symbol)
            open_count += 1
            opened += 1
        except Exception:
            session.rollback()
            logger.exception("Opening paper position for scenario %s (%s) failed", scenario_id, symbol)
            failed += 1

    return PositionOpenResult(scanned=scanned, opened=opened, skipped=skipped, failed=failed)
