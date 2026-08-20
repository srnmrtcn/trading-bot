from __future__ import annotations

from datetime import datetime

from src.db.models import FetchLog


def record_run(session, symbol: str, timeframe: str, status: str, started_at: datetime, finished_at: datetime, error_message: str = None) -> None:
    session.add(FetchLog(
        symbol=symbol, timeframe=timeframe, status=status,
        started_at=started_at, finished_at=finished_at, error_message=error_message,
    ))
    session.commit()


def get_last_successful_run(session, symbol: str, timeframe: str):
    row = (
        session.query(FetchLog)
        .filter(FetchLog.symbol == symbol, FetchLog.timeframe == timeframe, FetchLog.status == "success")
        .order_by(FetchLog.finished_at.desc())
        .first()
    )
    return row.finished_at if row else None
