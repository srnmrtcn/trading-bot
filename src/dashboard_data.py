from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import FetchLog, PaperPosition, Scenario
from src.paper_equity import current_equity
from src.timeutil import utc_now

HEALTHY_THRESHOLD = timedelta(minutes=90)
DELAYED_THRESHOLD = timedelta(hours=4)

EQUITY_HISTORY_LIMIT = 50
RECENT_SCENARIOS_LIMIT = 10


@dataclass
class SystemHealth:
    last_activity: datetime | None
    status: str


@dataclass
class EquitySummary:
    current: Decimal
    history: list[tuple[datetime, Decimal]]


def get_system_health(session, now: datetime = None) -> SystemHealth:
    now = now if now is not None else utc_now()
    last_activity = (
        session.query(FetchLog.finished_at)
        .order_by(FetchLog.finished_at.desc())
        .limit(1)
        .scalar()
    )
    if last_activity is None:
        return SystemHealth(last_activity=None, status="stopped")

    age = now - last_activity
    if age < HEALTHY_THRESHOLD:
        status = "healthy"
    elif age < DELAYED_THRESHOLD:
        status = "delayed"
    else:
        status = "stopped"
    return SystemHealth(last_activity=last_activity, status=status)


def get_equity_summary(session) -> EquitySummary:
    rows = (
        session.query(PaperPosition.closed_at, PaperPosition.equity_after)
        .filter(PaperPosition.status == "closed")
        .order_by(PaperPosition.closed_at.asc(), PaperPosition.id.asc())
        .all()
    )
    history = [(closed_at, equity_after) for closed_at, equity_after in rows][-EQUITY_HISTORY_LIMIT:]
    return EquitySummary(current=current_equity(session), history=history)


def equity_sparkline_points(history: list[tuple[datetime, Decimal]], width: int = 200, height: int = 40) -> str | None:
    if len(history) < 2:
        return None

    values = [float(equity) for _, equity in history]
    low, high = min(values), max(values)
    span = high - low or 1.0
    step = width / (len(values) - 1)

    points = []
    for index, value in enumerate(values):
        x = index * step
        y = height - ((value - low) / span) * height
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


def get_open_positions(session) -> list[PaperPosition]:
    return (
        session.query(PaperPosition)
        .filter(PaperPosition.status == "open")
        .order_by(PaperPosition.opened_at.desc())
        .all()
    )


def get_recent_scenarios(session, limit: int = RECENT_SCENARIOS_LIMIT) -> list[Scenario]:
    return (
        session.query(Scenario)
        .order_by(Scenario.created_at.desc())
        .limit(limit)
        .all()
    )
