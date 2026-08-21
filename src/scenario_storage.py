from __future__ import annotations

from datetime import datetime

from src.db.models import Scenario


def has_pending_scenario(session, symbol: str, direction: str, now: datetime) -> bool:
    """Is a *live* pending scenario already holding this (symbol, direction)?

    `expires_at` is half of the lock: the dedup hold lasts until the scenario
    expires or Subsystem C updates its status. Without the expiry condition a
    single pending row would block that pair forever, since nothing in this
    subsystem ever mutates `status`.

    `now` is a parameter rather than a `utc_now()` call so the check stays pure
    and testable.
    """
    existing = (
        session.query(Scenario)
        .filter(
            Scenario.symbol == symbol,
            Scenario.direction == direction,
            Scenario.status == "pending",
            Scenario.expires_at > now,
        )
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
