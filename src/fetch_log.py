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
    """Wall-clock ``finished_at`` of the last successful run — for auditing only.

    Do NOT use this as a fetch window's start: it is captured after the fetch
    completes, so it sits later than the data actually stored. The resume
    watermark comes from the stored klines instead — see
    ``scheduler.get_resume_point``.
    """
    row = (
        session.query(FetchLog)
        .filter(FetchLog.symbol == symbol, FetchLog.timeframe == timeframe, FetchLog.status == "success")
        .order_by(FetchLog.finished_at.desc())
        .first()
    )
    return row.finished_at if row else None
