from __future__ import annotations

import logging
from datetime import datetime, timedelta

from src.db.models import FuturesDailyKline, Symbol
from src.integrity import floor_to_timeframe
from src.portfolio.config import DAILY_HISTORY_DAYS
from src.timeutil import to_epoch_ms, utc_now

logger = logging.getLogger("portfolio_bars")


def upsert_futures_daily(session, symbol: str, rows: list) -> int:
    """
    Bir sembolun gunluk satirlarini yazar ya da gunceller, YAZILAN SATIR SAYISINI dondurur.
    """
    if not rows:
        return 0
    open_times = [row["open_time"] for row in rows]
    existing = (
        session.query(FuturesDailyKline)
        .filter(
            FuturesDailyKline.symbol == symbol,
            FuturesDailyKline.open_time.in_(open_times),
        )
        .all()
    )
    by_time = {row.open_time: row for row in existing}
    written = 0
    for row in rows:
        current = by_time.get(row["open_time"])
        if current is None:
            session.add(FuturesDailyKline(
                symbol=symbol, open_time=row["open_time"],
                close=row["close"], volume=row["volume"],
            ))
        else:
            current.close = row["close"]
            current.volume = row["volume"]
        written += 1
    session.commit()
    return written


def perpetual_symbols(session) -> list:
    """
    Symbol tablosunda is_active == True VE has_futures_contract == True olan sembol adlari, ARTAN sirali liste.
    """
    return [
        row.symbol for row in session.query(Symbol)
        .filter(Symbol.is_active == True, Symbol.has_futures_contract == True)  # noqa: E712
        .order_by(Symbol.symbol.asc())
        .all()
    ]


def refresh_futures_daily(session, binance_client, now: datetime = None, days: int = DAILY_HISTORY_DAYS) -> int:
    """
    Butun perpetual sembollerin son `days` gunluk mumlarini tazeler, TOPLAM yazilan satir sayisini dondurur.
    """
    now = now if now is not None else utc_now()
    start = floor_to_timeframe(now, "1d") - timedelta(days=days)
    written = 0
    for symbol in perpetual_symbols(session):
        try:
            rows = binance_client.get_futures_klines(
                symbol, "1d", to_epoch_ms(start), to_epoch_ms(now))
        except Exception:
            session.rollback()
            logger.exception("Futures daily refresh failed for %s", symbol)
            continue
        written += upsert_futures_daily(session, symbol, rows)
    return written
