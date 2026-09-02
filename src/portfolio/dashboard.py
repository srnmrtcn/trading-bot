from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from src.db.models import PortfolioPosition, PortfolioSnapshot
from src.portfolio.book import portfolio_equity
from src.portfolio.config import STARTING_EQUITY, STRATEGY_VERSION

EQUITY_HISTORY_LIMIT = 50


@dataclass
class BookPerformance:
    equity: Decimal
    rebalances: int
    last_rebalance: datetime | None
    closed: int
    wins: int
    gross_pnl: Decimal
    fee_cost: Decimal
    funding_cost: Decimal

    @property
    def return_pct(self) -> Decimal:
        return (self.equity / STARTING_EQUITY - 1) * 100

    @property
    def win_rate(self) -> Decimal:
        if not self.closed:
            return Decimal(0)
        return Decimal(self.wins) * 100 / Decimal(self.closed)


def portfolio_equity_history(session, limit: int = EQUITY_HISTORY_LIMIT) -> list:
    """
    PortfolioSnapshot satirlarindan (as_of, equity) ikilileri, ESKIDEN YENIYE sirali, son `limit` tanesi.
    Sorgu: session.query(PortfolioSnapshot.as_of, PortfolioSnapshot.equity).filter(PortfolioSnapshot.strategy_version == STRATEGY_VERSION).order_by(PortfolioSnapshot.as_of.asc(), PortfolioSnapshot.id.asc()).all()
    Sonra listeye cevirip [-limit:] dilimi dondurulur.
    """
    rows = (
        session.query(PortfolioSnapshot.as_of, PortfolioSnapshot.equity)
        .filter(PortfolioSnapshot.strategy_version == STRATEGY_VERSION)
        .order_by(PortfolioSnapshot.as_of.asc(), PortfolioSnapshot.id.asc())
        .all()
    )
    return [(as_of, equity) for as_of, equity in rows][-limit:]


def open_book(session) -> list:
    """
    status == 'open' VE strategy_version == STRATEGY_VERSION olan PortfolioPosition satirlari.
    Siralama: direction ARTAN, sonra symbol ARTAN. Boylece long bacagi ve short bacagi tabloda bitisik durur ve defterin iki yakasi bir bakista gorunur.
    """
    return (
        session.query(PortfolioPosition)
        .filter(
            PortfolioPosition.status == "open",
            PortfolioPosition.strategy_version == STRATEGY_VERSION,
        )
        .order_by(PortfolioPosition.direction.asc(), PortfolioPosition.symbol.asc())
        .all()
    )


def book_performance(session) -> BookPerformance:
    """
    Once dosyaya su dataclass eklenir:
    @dataclass
    class BookPerformance:
        equity: Decimal
        rebalances: int
        last_rebalance: datetime | None
        closed: int
        wins: int
        gross_pnl: Decimal
        fee_cost: Decimal
        funding_cost: Decimal

        @property
        def return_pct(self) -> Decimal:
            return (self.equity / STARTING_EQUITY - 1) * 100

        @property
        def win_rate(self) -> Decimal:
            if not self.closed:
                return Decimal(0)
            return Decimal(self.wins) * 100 / Decimal(self.closed)

    Fonksiyon SIRAYLA:
      1. snapshots = PortfolioSnapshot, strategy_version suzulur, order_by(as_of.desc(), id.desc()), .all()
      2. closed = PortfolioPosition, status == 'closed' VE strategy_version suzulur VE realized_pnl.isnot(None), .all()
      3. zero = Decimal(0)
      4. BookPerformance dondurulur:
           equity=portfolio_equity(session)
           rebalances=len(snapshots)
           last_rebalance=snapshots[0].as_of if snapshots else None
           closed=len(closed)
           wins=realized_pnl > 0 olanlarin sayisi
           gross_pnl=sum((row.gross_pnl or zero for row in closed), zero)
           fee_cost=sum((row.fee_cost or zero for row in closed), zero)
           funding_cost=sum((row.funding_cost or zero for row in closed), zero)
    """
    snapshots = (
        session.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.strategy_version == STRATEGY_VERSION)
        .order_by(PortfolioSnapshot.as_of.desc(), PortfolioSnapshot.id.desc())
        .all()
    )
    closed = (
        session.query(PortfolioPosition)
        .filter(
            PortfolioPosition.status == "closed",
            PortfolioPosition.strategy_version == STRATEGY_VERSION,
            PortfolioPosition.realized_pnl.isnot(None),
        )
        .all()
    )
    zero = Decimal(0)
    return BookPerformance(
        equity=portfolio_equity(session),
        rebalances=len(snapshots),
        last_rebalance=snapshots[0].as_of if snapshots else None,
        closed=len(closed),
        wins=sum(1 for row in closed if row.realized_pnl > 0),
        gross_pnl=sum((row.gross_pnl or zero for row in closed), zero),
        fee_cost=sum((row.fee_cost or zero for row in closed), zero),
        funding_cost=sum((row.funding_cost or zero for row in closed), zero),
    )