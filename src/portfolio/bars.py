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
    raise NotImplementedError


def perpetual_symbols(session) -> list:
    """
    Symbol tablosunda is_active == True VE has_futures_contract == True olan sembol adlari, ARTAN sirali liste.
    """
    raise NotImplementedError


def refresh_futures_daily(session, binance_client, now: datetime = None, days: int = DAILY_HISTORY_DAYS) -> int:
    """
    Butun perpetual sembollerin son `days` gunluk mumlarini tazeler, TOPLAM yazilan satir sayisini dondurur.
    """
    raise NotImplementedError
