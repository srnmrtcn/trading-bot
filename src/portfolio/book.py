from __future__ import annotations

from decimal import Decimal
from datetime import datetime
from src.db.models import PortfolioPosition, PortfolioSnapshot
from src.portfolio.config import STARTING_EQUITY, STRATEGY_VERSION
from src.portfolio.accounting import position_pnl
from src import trading_costs

def portfolio_equity(session) -> Decimal:
    """
    PortfolioSnapshot tablosundan, strategy_version == STRATEGY_VERSION olan EN YENI satirin equity degeri.
    Siralama: as_of AZALAN, esitlik bozulursa id AZALAN.
    Hic satir yoksa STARTING_EQUITY dondur.
    BASKA strateji surumune ait satirlar HIC dikkate alinmaz - daha yeni tarihli olsalar bile.
    """
    snapshot = (
        session.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.strategy_version == STRATEGY_VERSION)
        .order_by(PortfolioSnapshot.as_of.desc(), PortfolioSnapshot.id.desc())
        .first()
    )
    return snapshot.equity if snapshot is not None else STARTING_EQUITY

def marked_equity(session, closes: dict) -> Decimal:
    """Realised equity plus the unrealised P&L of everything still open.

    portfolio_equity answers "what has this book actually banked". That is the
    right number while the book closes every name every week, because at the
    moment it sizes a new book it holds nothing. Once unchanged names are
    carried instead, it stops being right: a carried position's profit sits
    unrealised, and sizing next week's book on the banked figure ignores it.
    The compounding would then be quietly wrong while every individual
    position still looked correct -- the failure mode no single test catches.

    Sizing uses this; the snapshot keeps recording realised equity, so the
    stored history and the dashboard keep their existing meaning.

    A position whose symbol has no price in `closes` is carried at cost. Zero
    P&L is an assumption too, but it is a smaller lie than a mark invented
    from nothing -- and it matches close_position, which leaves a position
    open rather than closing it at a fictional price.
    """
    equity = portfolio_equity(session)
    for position in open_positions(session):
        price = closes.get(position.symbol)
        if price is None:
            continue
        equity += position_pnl(
            position.direction, position.entry_price, price, position.position_size)
    return equity


def open_positions(session) -> list:
    """
    PortfolioPosition tablosunda status == 'open' VE strategy_version == STRATEGY_VERSION olan satirlar, symbol'e gore ARTAN sirali liste.
    Baska surumun ya da kapanmis pozisyonlarin satirlari donmez.
    """
    return (
        session.query(PortfolioPosition)
        .filter(
            PortfolioPosition.status == "open",
            PortfolioPosition.strategy_version == STRATEGY_VERSION,
        )
        .order_by(PortfolioPosition.symbol.asc())
        .all()
    )

def record_open(session, symbol: str, direction: str, entry_price, size, now: datetime):
    """
    Yeni bir PortfolioPosition satiri ekler ve DONDURUR.
    Alanlar: strategy_version=STRATEGY_VERSION, symbol, direction, entry_price, position_size=size, opened_at=now, status='open'. Diger alanlar (exit_price, closed_at, gross_pnl, fee_cost, funding_cost, realized_pnl) DOKUNULMAZ, NULL kalir.
    session.add + session.commit yapilir.
    """
    position = PortfolioPosition(
        strategy_version=STRATEGY_VERSION,
        symbol=symbol,
        direction=direction,
        entry_price=entry_price,
        position_size=size,
        opened_at=now,
        status="open",
    )
    session.add(position)
    session.commit()
    return position

def close_position(session, position, exit_price, funding_events: list, now: datetime) -> Decimal:
    """
    Bir pozisyonu kapatir, uc maliyet bileşenini AYRI AYRI yazar ve net kar/zarari dondurur.
    SIRAYLA:
      gross = position_pnl(position.direction, position.entry_price, exit_price, position.position_size)
      fee = trading_costs.round_trip_cost(position.position_size, position.entry_price, exit_price)
      funding = trading_costs.funding_cost(position.direction, position.position_size, funding_events)
      position.exit_price = exit_price
      position.closed_at = now
      position.status = 'closed'
      position.gross_pnl = gross
      position.fee_cost = fee
      position.funding_cost = funding
      position.realized_pnl = gross - fee - funding
      session.commit()
      return position.realized_pnl
    """
    gross = position_pnl(position.direction, position.entry_price, exit_price, position.position_size)
    fee = trading_costs.round_trip_cost(position.position_size, position.entry_price, exit_price)
    funding = trading_costs.funding_cost(position.direction, position.position_size, funding_events)
    position.exit_price = exit_price
    position.closed_at = now
    position.status = "closed"
    position.gross_pnl = gross
    position.fee_cost = fee
    position.funding_cost = funding
    position.realized_pnl = gross - fee - funding
    session.commit()
    return position.realized_pnl
