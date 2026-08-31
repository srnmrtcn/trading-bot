from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import PaperPosition, Scenario
from src.paper_equity import current_equity
from src.paper_sizer import size_position
from src.paper_trading_config import (
    MAX_CONCURRENT_POSITIONS,
    RISK_PCT,
    MIN_EXPECTED_R,
    MAX_TOTAL_NOTIONAL_MULTIPLE,
)
from src.strategy_version import STRATEGY_VERSION
from src.timeutil import utc_now
from src.trading_costs import fee_and_slippage_cost_in_r

logger = logging.getLogger("paper_position_opener")


@dataclass
class PositionOpenResult:
    scanned: int
    opened: int
    skipped: int
    failed: int


MAX_SCENARIO_AGE = timedelta(hours=1)


def open_qualifying_positions(session, now: datetime = None) -> PositionOpenResult:
    now = now if now is not None else utc_now()

    positioned_scenario_ids = {row[0] for row in session.query(PaperPosition.scenario_id).all()}
    candidates = (
        session.query(Scenario)
        .filter(
            Scenario.status == "pending",
            Scenario.expires_at > now,
            Scenario.calibrated_confidence.isnot(None),
            Scenario.strategy_version == STRATEGY_VERSION,
            Scenario.created_at > now - MAX_SCENARIO_AGE,
        )
        .order_by(Scenario.created_at.asc())
        .all()
    )
    candidates = [scenario for scenario in candidates if scenario.id not in positioned_scenario_ids]

    equity = current_equity(session)
    if equity <= 0:
        # The portfolio is gone. Reported once, here, rather than as one
        # sizing failure per pending scenario — which would bury the cause
        # under a stack trace for every candidate and inflate `failed`.
        logger.error("Paper portfolio equity is %s — not opening any positions", equity)
        return PositionOpenResult(scanned=0, opened=0, skipped=0, failed=0)

    # Get all open positions with current strategy version
    open_positions = session.query(PaperPosition).filter(
        PaperPosition.status == "open",
        PaperPosition.strategy_version == STRATEGY_VERSION,
    ).all()
    
    open_symbols = {position.symbol for position in open_positions}
    open_count = len(open_positions)

    # Calculate total notional of currently open positions with current strategy version
    total_notional = Decimal("0")
    for position in open_positions:
        total_notional += position.position_size * position.entry_price

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

            # Re-read per candidate: a position opened earlier in this loop
            # does not change equity, but staying with the live value keeps
            # sizing correct if that ever changes.
            risk_amount, position_size = size_position(
                current_equity(session), scenario.entry_price, scenario.stop_price, RISK_PCT,
            )

            # Calculate expected return for this scenario
            risk = abs(scenario.entry_price - scenario.stop_price)
            if risk == Decimal("0"):
                logger.debug("Skipping scenario %s (%s): zero risk", scenario_id, symbol)
                skipped += 1
                continue

            rr = abs(scenario.target_price - scenario.entry_price) / risk
            p = scenario.calibrated_confidence
            fee_cost = fee_and_slippage_cost_in_r(scenario.entry_price, scenario.stop_price)
            expected_r = p * rr - (Decimal("1") - p) - fee_cost

            if expected_r <= MIN_EXPECTED_R:
                logger.debug("Skipping scenario %s (%s): expected return %.4f <= minimum %.4f", 
                           scenario_id, symbol, expected_r, MIN_EXPECTED_R)
                skipped += 1
                continue

            # Check if adding this position would exceed the maximum total notional
            new_notional = position_size * scenario.entry_price
            if total_notional + new_notional > MAX_TOTAL_NOTIONAL_MULTIPLE * equity:
                logger.debug("Skipping scenario %s (%s): total notional would exceed limit", scenario_id, symbol)
                skipped += 1
                continue

            session.add(PaperPosition(
                scenario_id=scenario.id, symbol=symbol, direction=scenario.direction,
                entry_price=scenario.entry_price, stop_price=scenario.stop_price, target_price=scenario.target_price,
                risk_amount=risk_amount, position_size=position_size,
                opened_at=now, status="open",
                strategy_version=STRATEGY_VERSION,
            ))
            session.commit()
            open_symbols.add(symbol)
            open_count += 1
            opened += 1
            
            # Update total notional after successfully opening a position
            total_notional += new_notional
            
        except Exception:
            session.rollback()
            logger.exception("Opening paper position for scenario %s (%s) failed", scenario_id, symbol)
            failed += 1

    return PositionOpenResult(scanned=scanned, opened=opened, skipped=skipped, failed=failed)
