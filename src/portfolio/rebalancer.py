from __future__ import annotations

import logging

from datetime import datetime, timedelta
from dataclasses import dataclass
from decimal import Decimal

from src.db.models import FuturesDailyKline, PortfolioSnapshot
from src.integrity import floor_to_timeframe
from src.portfolio.config import REBALANCE_DAYS, STRATEGY_VERSION
from src.funding_collector import funding_events_between
from src.portfolio.accounting import position_sizes
from src.portfolio.book import (
    close_position,
    marked_equity,
    open_positions,
    portfolio_equity,
    record_open,
)
from src.portfolio.config import (
    LEG_EXPOSURE,
    LIQUIDITY_WINDOW_DAYS,
    LOOKBACK_DAYS,
    MIN_DOLLAR_VOLUME,
    MIN_UNIVERSE,
    SIGNAL_SKIP_DAYS,
    TOP_FRACTION,
)
from src.portfolio.selection import book_for, closes_by_day
from src.timeutil import utc_now

logger = logging.getLogger("portfolio")

# A single-day move this large is either a real crypto move or a bad print,
# and from close prices alone the two are indistinguishable. Measured over
# four years, screening such names OUT of the universe costs return at every
# threshold tried -- the extreme movers carry real momentum -- so nothing is
# filtered. This only reports, because the ani-olum test found the one way
# this book's edge dies: a name collapsing while held in the LONG leg. A
# fake +300% print manufactures exactly that, and until now it would have
# happened silently. Four years of data hold 7 candidates, so this should
# almost never fire.
EXTREME_DAILY_MOVE = Decimal("3")

# A position that moved this far against us over the week it was held.
#
# The survivorship stress test found this is the ONE way the book's edge
# dies: names collapsing while held in the long leg. Injected at 1% of the
# universe per rebalance, four-year weekly net goes +0.67% -> -0.94%;
# break-even sits between 0.5% and 1%. The threshold is measured, and until
# now nothing counted how close the live book runs to it.
#
# 50% rather than the 60-95% the test injected: this is meant to catch the
# onset, not only the finished collapse.
COLLAPSE_MOVE = Decimal("0.5")


def last_snapshot(session):
    """The most recent snapshot of THIS strategy version, or None."""
    return (
        session.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.strategy_version == STRATEGY_VERSION)
        .order_by(PortfolioSnapshot.as_of.desc(), PortfolioSnapshot.id.desc())
        .first()
    )


def next_due_after(as_of: datetime) -> datetime:
    """The first moment a rebalance may run again, given the last one's as_of."""
    return floor_to_timeframe(as_of, "1d") + timedelta(days=REBALANCE_DAYS)


def is_rebalance_due(session, now: datetime) -> bool:
    """Is a new rebalance due? Measured in DAYS, not in seconds.

    `as_of` records the instant the job actually ran, and that instant moves
    every week: the job is scheduled for 00:50 but queues behind the 00:40 bar
    sweep on a single worker, so it starts whenever that finishes -- 00:52:46
    one week, 00:52:20 the next.

    Comparing those instants with `now - as_of >= 7 days` turns that jitter
    into a threshold. A week that happens to start seconds EARLIER than the
    previous one falls short of seven days and the book stands still for
    another full week -- silently, because "not due" is the quiet answer six
    days out of seven. Roughly a coin flip, every week.

    Flooring both sides to the day removes the jitter entirely: the question
    becomes "have seven calendar days passed", which no amount of queueing
    delay can change. The same day twice still answers no.
    """
    last = last_snapshot(session)
    if last is None:
        return True
    return floor_to_timeframe(now, "1d") - floor_to_timeframe(last.as_of, "1d") >= timedelta(days=REBALANCE_DAYS)


def load_daily_bars(session, now: datetime, days: int) -> dict:
    """
    Loads daily bars for symbols within the specified date range.
    """
    boundary = floor_to_timeframe(now, "1d")
    rows = (
        session.query(FuturesDailyKline)
        .filter(
            FuturesDailyKline.open_time < boundary,
            FuturesDailyKline.open_time >= boundary - timedelta(days=days),
        )
        .order_by(FuturesDailyKline.symbol.asc(), FuturesDailyKline.open_time.asc())
        .all()
    )
    bars = {}
    for row in rows:
        bars.setdefault(row.symbol, []).append(
            {"open_time": row.open_time, "close": row.close, "volume": row.volume}
        )
    return bars


def record_snapshot(session, as_of: datetime, equity, closed: int, opened: int):
    """
    Records a portfolio snapshot to the database.
    """
    snapshot = PortfolioSnapshot(
        strategy_version=STRATEGY_VERSION, as_of=as_of, equity=equity,
        positions_closed=closed, positions_opened=opened,
    )
    session.add(snapshot)
    session.commit()
    return snapshot


@dataclass
class RebalanceResult:
    acted: bool
    closed: int
    opened: int
    equity: Decimal
    universe: int
    # Why the run ended the way it did. acted=False has two causes that look
    # identical from outside and could not be more different: "not_due" is the
    # normal answer on six days out of seven, while "no_book" means the run WAS
    # due and could not act. The second is a silent outage -- the book simply
    # never opens -- so the caller has to be able to tell them apart in order
    # to keep quiet about one and complain about the other.
    reason: str = "ok"
    # How many symbols had daily bars at all. Separates "the bars never
    # arrived" (0) from "the bars are here but too few names clear the
    # liquidity floor" (many, with universe 0).
    symbols: int = 0
    # Positions that moved COLLAPSE_MOVE or more against us over the week.
    # Compared against the universe size, this is the rate the ani-olum test
    # says the edge cannot survive above.
    collapses: int = 0
    # Names that were already held and stay held. They pay no fee this week,
    # which is the whole point: measured on the replayed history, 49.4% of
    # names survive from one book to the next and closing them cost 48.9% of
    # all fees paid.
    carried: int = 0
    # NOTE: everything below is keyword-only in practice. This dataclass is
    # built POSITIONALLY above, so a new field inserted anywhere but the end
    # silently shifts the ones after it -- carried landed in a new slot and
    # two tests caught it.
    #
    # How many positions the book is holding. On a "not_due" run this is the
    # only proof the book is alive: six days out of seven nothing else happens.
    held: int = 0
    # When the next rebalance may run. Reported on "not_due" so a silent week
    # and a dead job stop looking identical from the log.
    next_due: datetime | None = None


def run_rebalance(session, now: datetime = None) -> RebalanceResult:
    """
    Executes a rebalance operation, closing existing positions and opening new ones.
    """
    now = now if now is not None else utc_now()
    equity = portfolio_equity(session)
    if not is_rebalance_due(session, now):
        last = last_snapshot(session)
        return RebalanceResult(False, 0, 0, equity, 0, "not_due",
                               held=len(open_positions(session)),
                               next_due=next_due_after(last.as_of) if last else None)

    history = LIQUIDITY_WINDOW_DAYS + max(LOOKBACK_DAYS) + SIGNAL_SKIP_DAYS + 2
    bars = load_daily_bars(session, now, history)
    closes = {symbol: closes_by_day(rows) for symbol, rows in bars.items()}
    latest = {symbol: day_closes[max(day_closes)]
              for symbol, day_closes in closes.items() if day_closes}

    # The new book is decided BEFORE anything is closed, because what to close
    # depends on it: a name the new book still wants is carried rather than
    # sold and bought back. Sized on marked equity, since carried positions
    # hold unrealised P&L that banked equity does not know about.
    as_of = (floor_to_timeframe(now, "1d") - timedelta(days=1)).date()
    universe = 0
    target = {}
    marked = marked_equity(session, latest)
    if marked > 0:
        longs, shorts, prices = book_for(
            bars, as_of, LOOKBACK_DAYS, SIGNAL_SKIP_DAYS, TOP_FRACTION,
            LIQUIDITY_WINDOW_DAYS, MIN_DOLLAR_VOLUME, MIN_UNIVERSE,
        )
        universe = len(longs) + len(shorts)
        for direction, names in (("long", longs), ("short", shorts)):
            for symbol, size in position_sizes(marked, names, prices, LEG_EXPOSURE).items():
                target[(symbol, direction)] = (size, prices[symbol])

    # Buradan snapshot'a kadar olan her sey TEK transaction. close_position ve
    # record_open artik commit etmiyor, yalnizca flush ediyor; araya giren bir
    # hata her seyi geri alir ve defter bir onceki haftanin halinde kalir.
    try:
        return _defteri_kur(session, now, equity, target, latest, closes, universe, bars)
    except Exception:
        session.rollback()
        raise


def _defteri_kur(session, now, equity, target, latest, closes, universe, bars):
    closed = 0
    carried = 0
    collapses = 0
    for position in open_positions(session):
        key = (position.symbol, position.direction)
        if key in target:
            # Same name, same side: keep it. Its size is left alone rather than
            # trimmed to this week's target. Equity moves a per cent or so a
            # week, so the drift is small, and correcting it would mean a
            # partial close -- paying part of the fee this change exists to
            # avoid, and blending the entry basis that the P&L is measured
            # against. The replay is what decides whether that trade is worth
            # making; it is not worth guessing at.
            carried += 1
            del target[key]
            continue
        price = latest.get(position.symbol)
        if price is None:
            continue
        events = funding_events_between(session, position.symbol, position.opened_at, now)
        adverse = adverse_move(position.direction, position.entry_price, price)
        if adverse >= COLLAPSE_MOVE:
            collapses += 1
            logger.warning(
                "%s %s collapsed %.0f%% against the book while held "
                "(entry %s, exit %s)",
                position.symbol, position.direction, adverse * 100,
                position.entry_price, price,
            )
        equity += close_position(session, position, price, events, now)
        closed += 1

    opened = 0
    for (symbol, direction), (size, price) in target.items():
        record_open(session, symbol, direction, price, size, now)
        opened += 1
        move = biggest_daily_move(closes.get(symbol) or {})
        if move is not None and move > EXTREME_DAILY_MOVE:
            logger.warning(
                "%s entered the book %s after a %.0f%% single-day move -- "
                "real move or bad print, worth a look",
                symbol, direction, move * 100,
            )

    if closed == 0 and opened == 0 and carried == 0:
        # Nothing happened, so the week is NOT spent. Recording a snapshot here
        # would set the clock and block the next attempt for a full
        # REBALANCE_DAYS -- which is exactly the wrong response to the reason
        # this branch is usually reached: the daily bars did not arrive, so
        # there was no universe to rank. That is a data outage lasting minutes,
        # and it would have cost a week of trading. A book that legitimately
        # has too few eligible names simply retries tomorrow, which is cheap.
        return RebalanceResult(False, 0, 0, equity, universe, "no_book",
                              len(bars))

    # The snapshot still records REALISED equity, so the stored history and
    # the dashboard keep the meaning they have always had. Only the sizing
    # above uses the marked figure.
    record_snapshot(session, now, equity, closed, opened)
    return RebalanceResult(True, closed, opened, equity, universe, "ok",
                           len(bars), collapses, carried)


def biggest_daily_move(day_closes: dict):
    """Largest absolute close-to-close move in a symbol's loaded window.

    Returns None when there is nothing to compare. Only closes are stored for
    daily futures bars -- no high or low -- so this is the only integrity
    signal available; an intra-bar consistency check is not possible.
    """
    if len(day_closes) < 2:
        return None
    ordered = [day_closes[day] for day in sorted(day_closes)]
    biggest = None
    for previous, current in zip(ordered, ordered[1:]):
        if previous is None or current is None or previous <= 0:
            continue
        move = abs(current / previous - 1)
        if biggest is None or move > biggest:
            biggest = move
    return biggest


def adverse_move(direction: str, entry, exit_price):
    """How far the price went AGAINST a position, as a positive fraction.

    A separate function because the sign cannot be tested through
    run_rebalance. Everything that reaches the closing branch has usually moved
    against the book already -- a name that rose stays in the long leg and is
    carried, a name that fell stays in the short leg and is carried -- so
    signed and absolute agree on nearly every position that is actually closed.
    A test driving the whole rebalance therefore passes against a version that
    ignores direction entirely, which is the version that would report the
    book's best week as an alarm.
    """
    move = (exit_price - entry) / entry
    return -move if direction == "long" else move
