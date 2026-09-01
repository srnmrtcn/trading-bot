"""Replay the momentum book over history, through the production code.

The funnel measured the idea; this measures the machine. run_rebalance,
book_for, the liquidity floor, the sizer, the funding accounting and the
equity snapshot are the same objects the scheduler will call -- only `now` is
moved a week at a time over recorded candles.

The rule this exists to enforce: the funding trade paid +1.2 R a week in a
table and nothing at all once it was traded bar by bar, because a table cannot
show you overlapping positions or the cost of getting in and out. No idea
reaches the live path without passing through here first.

    PYTHONPATH=. python scripts/run_portfolio_replay.py --weeks 90

DAILY BARS ARE DERIVED, NOT FETCHED. The research database holds hourly
futures candles; production will store daily ones. Each replay day is folded
the way Binance folds it -- close is the last hourly close, volume is the sum
of the hourly volumes -- so the liquidity floor sees close x volume, the same
approximation the live path will see, rather than the exact hourly dollar sum
the research scripts used. If the floor is sensitive to that difference, this
is where it shows.

SURVIVORSHIP: the universe is the pairs liquid enough to be listed today.
Every number here is an optimistic bound.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from src.db.models import FundingRateHistory, FuturesDailyKline, PortfolioPosition, Symbol
from src.db.session import create_all_tables, make_engine, make_session_factory
from src.portfolio.book import portfolio_equity
from src.portfolio.config import (
    LIQUIDITY_WINDOW_DAYS,
    LOOKBACK_DAYS,
    MIN_DOLLAR_VOLUME,
    REBALANCE_DAYS,
    STARTING_EQUITY,
    STRATEGY_VERSION,
)
from src.portfolio.rebalancer import run_rebalance


def prepare(source: Path, target: Path) -> None:
    if target.exists():
        target.unlink()
    shutil.copy2(source, target)


def fold_daily(target: Path) -> int:
    """Hourly candles into daily ones, the way an exchange would fold them."""
    conn = sqlite3.connect(target)
    conn.execute("DROP TABLE IF EXISTS futures_daily_klines")
    conn.execute("""
        CREATE TABLE futures_daily_klines (
            id INTEGER NOT NULL PRIMARY KEY,
            symbol VARCHAR NOT NULL,
            open_time DATETIME NOT NULL,
            close NUMERIC(20, 8) NOT NULL,
            volume NUMERIC(30, 8) NOT NULL,
            CONSTRAINT uq_futures_daily_symbol_open_time UNIQUE (symbol, open_time)
        )""")
    # One pass, no correlated subquery. SQLite resolves a bare column in a
    # group that contains exactly one MAX() from the row holding that maximum,
    # so `close` here is the last hourly close of the day while `volume` sums
    # the whole day. Written as a subquery-per-row instead, this took long
    # enough over the mounted filesystem to fail with an I/O error.
    conn.execute("""
        INSERT INTO futures_daily_klines (symbol, open_time, close, volume)
        SELECT symbol, day, close, total FROM (
            SELECT symbol,
                   date(open_time) || ' 00:00:00.000000' AS day,
                   close,
                   SUM(volume) AS total,
                   MAX(open_time) AS last_hour
              FROM klines
             WHERE timeframe = '1h'
             GROUP BY symbol, date(open_time)
        )""")
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM futures_daily_klines").fetchone()[0]
    conn.close()
    return count


def seed_symbols(session, target: Path) -> int:
    """Every pair in the bench is a USDT perpetual; that is what was fetched."""
    conn = sqlite3.connect(target)
    names = [row[0] for row in conn.execute(
        "SELECT DISTINCT symbol FROM futures_daily_klines ORDER BY symbol")]
    conn.close()
    for name in names:
        if session.get(Symbol, name) is None:
            session.add(Symbol(symbol=name, base_asset=name[:-4], quote_asset=name[-4:],
                               is_active=True, has_futures_contract=True))
    session.commit()
    return len(names)


def report(session, snapshots: list) -> None:
    positions = session.query(PortfolioPosition).filter(
        PortfolioPosition.strategy_version == STRATEGY_VERSION).all()
    closed = [p for p in positions if p.status == "closed" and p.realized_pnl is not None]
    print("\n" + "=" * 70)
    if not snapshots:
        print("hic denge yapilmadi")
        return
    equities = [STARTING_EQUITY] + [e for _, e in snapshots]
    peak, drawdown = equities[0], Decimal("0")
    for value in equities:
        peak = max(peak, value)
        drawdown = max(drawdown, (peak - value) / peak)
    final = equities[-1]
    weeks = len(snapshots)
    print("denge sayisi: %d   acilan pozisyon: %d   kapanan: %d"
          % (weeks, len(positions), len(closed)))
    if closed:
        wins = sum(1 for p in closed if p.realized_pnl > 0)
        gross = sum(p.gross_pnl for p in closed)
        fees = sum(p.fee_cost for p in closed)
        funding = sum(p.funding_cost for p in closed)
        print("kazanan pozisyon: %d/%d (%.1f%%)" % (wins, len(closed), 100.0 * wins / len(closed)))
        print("brut %.2f  komisyon %.2f  funding %.2f  net %.2f"
              % (gross, fees, funding, gross - fees - funding))
        longs = [p for p in closed if p.direction == "long"]
        shorts = [p for p in closed if p.direction == "short"]
        for name, leg in (("long bacagi", longs), ("short bacagi", shorts)):
            if leg:
                print("  %-12s n=%4d  net %10.2f  funding %8.2f"
                      % (name, len(leg), sum(p.realized_pnl for p in leg),
                         sum(p.funding_cost for p in leg)))
    print("esitlik %.2f -> %.2f  (%+.1f%%)  max drawdown %.1f%%"
          % (STARTING_EQUITY, final, 100.0 * float(final / STARTING_EQUITY - 1),
             100.0 * float(drawdown)))
    if weeks > 1:
        years = weeks * REBALANCE_DAYS / 365.0
        print("bilesik yillik: %+.1f%%  (%d hafta)"
              % (100.0 * (float(final / STARTING_EQUITY) ** (1 / years) - 1), weeks))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="data/research.db")
    # Not under data/: that folder lives in the user's own tree, the copy is
    # 400MB of scratch, and this session cannot delete files there.
    ap.add_argument("--db", default="/tmp/portfolio_replay.db")
    ap.add_argument("--weeks", type=int, default=0, help="0 = verinin izin verdigi kadar")
    ap.add_argument("--floor", type=float, default=None, help="hacim tabanini gecici olarak degistir")
    args = ap.parse_args()

    source, target = Path(args.source), Path(args.db)
    if not source.exists():
        print("kaynak veritabani yok: %s" % source, file=sys.stderr)
        return 2
    prepare(source, target)
    days = fold_daily(target)

    import src.portfolio.rebalancer as rebalancer
    if args.floor is not None:
        # The floor lives in the rebalancer's namespace once imported, so the
        # override has to land there, not only on the config module.
        rebalancer.MIN_DOLLAR_VOLUME = Decimal(str(args.floor))

    engine = make_engine("sqlite:///%s" % target.as_posix())
    create_all_tables(engine)
    session = make_session_factory(engine)()
    symbols = seed_symbols(session, target)

    bounds = session.query(FuturesDailyKline).order_by(FuturesDailyKline.open_time.asc()).first()
    newest = session.query(FuturesDailyKline).order_by(FuturesDailyKline.open_time.desc()).first()
    funding_rows = session.query(FundingRateHistory).count()
    warmup = LIQUIDITY_WINDOW_DAYS + max(LOOKBACK_DAYS) + 2
    start = bounds.open_time + timedelta(days=warmup)
    end = newest.open_time

    print("gunluk mum: %d satir, %d sembol   %s -> %s"
          % (days, symbols, bounds.open_time.date(), newest.open_time.date()))
    print("funding gecmisi: %d satir" % funding_rows)
    print("taban: %s $   ilk denge: %s\n" % (rebalancer.MIN_DOLLAR_VOLUME, start.date()))

    snapshots = []
    now = start
    while now <= end:
        if args.weeks and len(snapshots) >= args.weeks:
            break
        result = run_rebalance(session, now)
        if result.acted:
            snapshots.append((now, result.equity))
            if len(snapshots) % 10 == 0:
                print("  %s  denge %2d  evren %2d  acilan %2d  esitlik %9.2f"
                      % (now.date(), len(snapshots), result.universe,
                         result.opened, result.equity))
        now += timedelta(days=REBALANCE_DAYS)

    # one last close so the final book is marked to market rather than left open
    run_rebalance(session, now)
    snapshots.append((now, portfolio_equity(session)))
    report(session, snapshots)
    return 0


if __name__ == "__main__":
    sys.exit(main())
