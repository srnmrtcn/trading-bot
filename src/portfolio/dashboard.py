from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func

from src.db.models import FuturesDailyKline, PortfolioPosition, PortfolioSnapshot
from src.portfolio.book import marked_equity, portfolio_equity
from src.portfolio.config import REBALANCE_DAYS, STARTING_EQUITY, STRATEGY_VERSION
from src.timeutil import utc_now

EQUITY_HISTORY_LIMIT = 50


@dataclass
class LegPerformance:
    """One side of the book, on its own.

    The four-year measurement found the two legs take turns: in 2023-2024 the
    long leg carried the book and the short leg lost money, in 2025-2026 the
    reverse. A single net figure hides that -- one leg earning while the other
    gives back the same amount looks identical to both legs being dead, and
    those two states call for opposite responses. Same reason gross, fees and
    funding are already three separate lines rather than one.
    """
    closed: int
    gross_pnl: Decimal
    fee_cost: Decimal
    funding_cost: Decimal

    @property
    def net_pnl(self) -> Decimal:
        # funding_cost is a COST: negative means the leg collected funding.
        return self.gross_pnl - self.fee_cost - self.funding_cost


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
    long_leg: LegPerformance
    short_leg: LegPerformance
    as_of: datetime
    # Equity including the unrealised P&L of positions still open.
    #
    # It became necessary the moment the book started carrying names. Before
    # that it closed everything every week, so realised equity WAS the whole
    # story and the page told the truth. A carried position's profit now sits
    # unrealised for as long as the book keeps wanting that name, and the
    # realised curve reports it as if it did not exist -- lumpy, always behind,
    # and worst at exactly the moment the book is doing well.
    marked: Decimal = Decimal(0)

    @property
    def marked_return_pct(self) -> Decimal:
        return (self.marked / STARTING_EQUITY - 1) * 100

    @property
    def days_since_rebalance(self) -> int | None:
        if self.last_rebalance is None:
            return None
        return (self.as_of - self.last_rebalance).days

    @property
    def rebalance_overdue(self) -> bool:
        """A rebalance that was due yesterday and still has not happened.

        This is the state the first live night produced and nothing showed: the
        job ran, found no daily bars, correctly refused to spend the week, and
        left an empty book behind. An empty book on the page looks the same
        whether the strategy is between rebalances or has been unable to open
        for a fortnight.

        One day of slack, deliberately. The rebalance runs in the small hours
        and the page is read at any hour, so a book rebalanced exactly seven
        days ago is normal, not late.
        """
        days = self.days_since_rebalance
        return days is not None and days > REBALANCE_DAYS

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


def book_performance(session, now: datetime | None = None) -> BookPerformance:
    """Headline numbers for the book, plus each leg on its own.

    Every query filters on STRATEGY_VERSION. The paper path and the book share
    a database, and a summary that summed them would describe neither.

    The legs are selected by direction rather than derived by subtraction, so
    a row with an unexpected direction shows up as a mismatch between the two
    leg lines and the totals instead of being silently folded into one side.
    """
    now = now if now is not None else utc_now()
    closes = latest_closes(session)
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

    def leg(direction: str) -> LegPerformance:
        rows = [row for row in closed if row.direction == direction]
        return LegPerformance(
            closed=len(rows),
            gross_pnl=sum((row.gross_pnl or zero for row in rows), zero),
            fee_cost=sum((row.fee_cost or zero for row in rows), zero),
            funding_cost=sum((row.funding_cost or zero for row in rows), zero),
        )

    return BookPerformance(
        equity=portfolio_equity(session),
        rebalances=len(snapshots),
        last_rebalance=snapshots[0].as_of if snapshots else None,
        closed=len(closed),
        wins=sum(1 for row in closed if row.realized_pnl > 0),
        gross_pnl=sum((row.gross_pnl or zero for row in closed), zero),
        fee_cost=sum((row.fee_cost or zero for row in closed), zero),
        funding_cost=sum((row.funding_cost or zero for row in closed), zero),
        long_leg=leg("long"),
        short_leg=leg("short"),
        as_of=now,
        marked=marked_equity(session, closes),
    )


def latest_closes(session) -> dict:
    """{symbol: most recent daily close} for every symbol that has bars.

    One grouped query rather than one per position: this runs on every page
    load, and the book holds up to twenty names.
    """
    newest = (
        session.query(
            FuturesDailyKline.symbol.label("symbol"),
            func.max(FuturesDailyKline.open_time).label("open_time"),
        )
        .group_by(FuturesDailyKline.symbol)
        .subquery()
    )
    rows = (
        session.query(FuturesDailyKline.symbol, FuturesDailyKline.close)
        .join(
            newest,
            (FuturesDailyKline.symbol == newest.c.symbol)
            & (FuturesDailyKline.open_time == newest.c.open_time),
        )
        .all()
    )
    return {symbol: close for symbol, close in rows}
