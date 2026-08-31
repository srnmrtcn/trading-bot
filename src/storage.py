from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.db.models import Kline, Symbol


@dataclass
class KlineUpsertResult:
    inserted: int
    updated: int


def upsert_symbols(session: Session, symbols: list[dict]) -> None:
    for data in symbols:
        existing = session.get(Symbol, data["symbol"])
        if existing is None:
            session.add(Symbol(
                symbol=data["symbol"],
                base_asset=data["base_asset"],
                quote_asset=data["quote_asset"],
                is_active=True,
                listed_at=data.get("listed_at"),
            ))
        else:
            existing.is_active = True
            existing.base_asset = data["base_asset"]
            existing.quote_asset = data["quote_asset"]
    session.commit()


def mark_symbols_inactive(session: Session, active_symbols: set) -> None:
    currently_active = session.query(Symbol).filter(Symbol.is_active == True).all()  # noqa: E712
    for sym in currently_active:
        if sym.symbol not in active_symbols:
            sym.is_active = False
    session.commit()


def set_futures_contract_flags(session: Session, futures_symbols: set) -> None:
    """Bilinen her sembol icin perpetual futures kontrati var mi bilgisini yazar.

    ADIMLAR:
      1. session.query(Symbol).all() ile TUM sembolleri gez - sadece
         settekileri degil, hepsini.
      2. Her biri icin sym.has_futures_contract = sym.symbol in futures_symbols.
      3. session.commit().

    Neden hepsi: kontrati kaybolan bir sembolun bayragi boylece True kalmaz,
    False'a doner.
    """
    for sym in session.query(Symbol).all():
        sym.has_futures_contract = sym.symbol in futures_symbols
    session.commit()


def get_kline_time_bounds(session: Session, symbol: str, timeframe: str) -> tuple:
    """Return ``(earliest, latest)`` stored ``open_time`` for a symbol/timeframe.

    Returns ``(None, None)`` when nothing is stored yet. This is the single
    source of truth for "what data does this symbol/timeframe already have",
    used both for the resume watermark and for the initial-backfill decision.
    """
    earliest, latest = (
        session.query(func.min(Kline.open_time), func.max(Kline.open_time))
        .filter(Kline.symbol == symbol, Kline.timeframe == timeframe)
        .one()
    )
    return earliest, latest


def upsert_klines(session: Session, symbol: str, timeframe: str, rows: list[dict]) -> KlineUpsertResult:
    if not rows:
        return KlineUpsertResult(inserted=0, updated=0)

    open_times = [row["open_time"] for row in rows]
    existing = (
        session.query(Kline)
        .filter(Kline.symbol == symbol, Kline.timeframe == timeframe, Kline.open_time.in_(open_times))
        .all()
    )
    existing_by_time = {row.open_time: row for row in existing}

    inserted = 0
    updated = 0
    for row in rows:
        existing_row = existing_by_time.get(row["open_time"])
        if existing_row is None:
            session.add(Kline(
                symbol=symbol,
                timeframe=timeframe,
                open_time=row["open_time"],
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
                flagged=row.get("flagged", False),
            ))
            inserted += 1
        else:
            existing_row.open = row["open"]
            existing_row.high = row["high"]
            existing_row.low = row["low"]
            existing_row.close = row["close"]
            existing_row.volume = row["volume"]
            existing_row.flagged = row.get("flagged", False)
            updated += 1

    session.commit()
    return KlineUpsertResult(inserted=inserted, updated=updated)