from __future__ import annotations

from src.db.models import Scenario


def has_pending_scenario(session, symbol: str, direction: str) -> bool:
    existing = (
        session.query(Scenario)
        .filter(Scenario.symbol == symbol, Scenario.direction == direction, Scenario.status == "pending")
        .first()
    )
    return existing is not None


def insert_scenario(session, draft) -> None:
    session.add(Scenario(
        symbol=draft.symbol,
        direction=draft.direction,
        entry_price=draft.entry_price,
        target_price=draft.target_price,
        stop_price=draft.stop_price,
        expected_return_pct=draft.expected_return_pct,
        confidence_score=draft.confidence_score,
        created_at=draft.created_at,
        expires_at=draft.expires_at,
        status="pending",
    ))
    session.commit()
