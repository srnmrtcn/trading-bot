from __future__ import annotations

from dataclasses import dataclass

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
