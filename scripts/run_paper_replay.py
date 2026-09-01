"""Run the whole paper-trading plant against history, one hour at a time.

The research funnel measures a *rule*. This measures the *system*: the same
scenario generator, the same BTC regime filter, the same funding gate, the
same calibration, the same sizer, opener and closer that run on Railway --
driven over recorded candles with `now` moved forward an hour per step. What
comes out is a paper equity curve produced by production code, not by a
research reimplementation of it.

Two things it is good for. It shakes the plant down end to end before a real
signal ever reaches it, so shipping an edge is an afternoon rather than a
week. And it is the only place a bug that lives *between* the modules -- a
scenario that calibrates but never sizes, a position that opens but never
closes -- can show up at all; every such bug so far has been invisible to unit
tests because each module was right on its own.

    PYTHONPATH=. python scripts/run_paper_replay.py --days 90 --symbols 40

WHAT IS FAITHFUL AND WHAT IS NOT
  * Faithful: candle windows (`open_time < now`, closed candles only), the BTC
    daily regime, funding-gate blocking, expiry, calibration's MIN_SAMPLES
    cold start, fixed-fractional sizing off live equity, the notional caps.
  * Not faithful: fills are the scenario's own entry/exit prices, so there is
    no queue, no partial fill and no exchange outage. Slippage enters only
    where production already models it, in `trading_costs`.
  * Not fixable here: the universe is the symbols that are liquid TODAY, so
    coins that died inside the window are missing. Read every number as an
    optimistic bound.
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from src.db.models import FundingRate, PaperPosition, Scenario, Symbol
from src.db.session import create_all_tables, make_engine, make_session_factory
from src.learning_runner import run_learning_cycle
from src.paper_trading_config import STARTING_EQUITY
from src.paper_trading_runner import run_paper_trading_cycle
from src.scenario_runner import run_scenario_generation
from src.strategy_version import STRATEGY_VERSION

REGIME_SYMBOL = "BTCUSDT"


PROGRESS_TABLE = "replay_progress"


def read_progress(db: Path):
    """Where the last run stopped, or None for a fresh database.

    A replay of a year is longer than any single watcher verify window, so it
    has to survive being cut off. The cursor lives in the replay database
    itself rather than a side file, because the two must never disagree about
    how far the simulation got.
    """
    conn = sqlite3.connect(db)
    try:
        row = conn.execute("SELECT next_start, end_at, symbols FROM %s WHERE id=1"
                           % PROGRESS_TABLE).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    if not row:
        return None
    return datetime.fromisoformat(row[0]), datetime.fromisoformat(row[1]), int(row[2])


def write_progress(db: Path, next_start: datetime, end_at: datetime, symbols: int) -> None:
    """The symbol count is part of the cursor: the universe is the top N by
    row count, so resuming with a different N would silently replay a
    different market than the half already simulated."""
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE IF NOT EXISTS %s (id INTEGER PRIMARY KEY, "
                 "next_start TEXT, end_at TEXT, symbols INTEGER)" % PROGRESS_TABLE)
    conn.execute("INSERT OR REPLACE INTO %s (id, next_start, end_at, symbols) "
                 "VALUES (1,?,?,?)" % PROGRESS_TABLE,
                 (next_start.isoformat(), end_at.isoformat(), symbols))
    conn.commit()
    conn.close()


def prepare(source: Path, target: Path) -> None:
    """A working copy, because the replay writes scenarios and positions.

    The research database is expensive to rebuild and is the input to every
    other measurement; nothing here may touch it.
    """
    if target.exists():
        target.unlink()
    shutil.copy2(source, target)
    conn = sqlite3.connect(target)
    for table in ("scenarios", "paper_positions", "funding_rates"):
        try:
            conn.execute("DELETE FROM %s" % table)
        except sqlite3.OperationalError:
            pass          # table not in the source yet; create_all makes it
    conn.commit()
    conn.close()


def universe(db: Path, limit: int) -> list:
    """Symbols with hourly candles, most rows first, BTC always included."""
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT symbol, COUNT(*) c FROM klines WHERE timeframe='1h' "
        "GROUP BY symbol ORDER BY c DESC, symbol").fetchall()
    conn.close()
    symbols = [s for s, _ in rows][:limit]
    if REGIME_SYMBOL not in symbols and any(s == REGIME_SYMBOL for s, _ in rows):
        symbols.append(REGIME_SYMBOL)
    return symbols


def seed_symbols(session, symbols: list) -> None:
    """The funding gate is a no-op unless `symbols` says the pair has futures.

    Every pair in the bench is a USDT perpetual -- that is what
    fetch_research_data.py pulls -- so has_futures_contract is True for all of
    them, and the gate does in the replay exactly what it does live.
    """
    for symbol in symbols:
        if session.get(Symbol, symbol) is None:
            session.add(Symbol(
                symbol=symbol, base_asset=symbol[:-4], quote_asset=symbol[-4:],
                is_active=True, has_futures_contract=True,
            ))
    session.commit()


def funding_schedule(db: Path, symbols: list) -> list:
    """(settlement_time, symbol, rate) for the replayed pairs, oldest first."""
    conn = sqlite3.connect(db)
    marks = ",".join("?" * len(symbols))
    rows = conn.execute(
        "SELECT funding_time, symbol, funding_rate FROM funding_rate_history "
        "WHERE symbol IN (%s) ORDER BY funding_time" % marks, symbols).fetchall()
    conn.close()
    out = []
    for t, symbol, rate in rows:
        stamp = datetime.fromisoformat(t).replace(minute=0, second=0, microsecond=0)
        out.append((stamp, symbol, Decimal(str(rate))))
    return out


def apply_funding(session, schedule: list, cursor: int, now: datetime) -> int:
    """Bring `funding_rates` up to what was known at `now`.

    Only the prints that have happened since the last step are written, so the
    cost is one upsert per settlement rather than one per symbol per hour.
    """
    while cursor < len(schedule) and schedule[cursor][0] <= now:
        _, symbol, rate = schedule[cursor]
        row = session.get(FundingRate, symbol)
        if row is None:
            session.add(FundingRate(symbol=symbol, funding_rate=rate, fetched_at=now))
        else:
            row.funding_rate = rate
            row.fetched_at = now
        cursor += 1
    # `fetched_at` also has to keep moving for symbols whose funding has not
    # printed this hour, or FUNDING_DATA_MAX_AGE would block the whole
    # universe two hours in. Live, the hourly refresh does exactly this.
    session.query(FundingRate).update({FundingRate.fetched_at: now})
    session.commit()
    return cursor


def report(session) -> None:
    positions = (
        session.query(PaperPosition)
        .filter(PaperPosition.strategy_version == STRATEGY_VERSION)
        .order_by(PaperPosition.opened_at).all()
    )
    scenarios = session.query(Scenario).filter(
        Scenario.strategy_version == STRATEGY_VERSION).all()
    by_status = {}
    for s in scenarios:
        by_status[s.status] = by_status.get(s.status, 0) + 1
    calibrated = sum(1 for s in scenarios if s.calibrated_confidence is not None)

    print("\n" + "=" * 66)
    print("senaryo: %d  (%s)" % (len(scenarios), by_status or "yok"))
    print("kalibre edilmis senaryo: %d" % calibrated)
    print("pozisyon: %d acilmis, %d kapanmis"
          % (len(positions), sum(1 for p in positions if p.status == "closed")))
    if not positions:
        print("\nHIC POZISYON ACILMADI. Zincirin nerede durdugu yukaridaki")
        print("sayilardan okunur: senaryo yoksa sinyal/rejim/funding kapisi,")
        print("senaryo var ama kalibre yoksa MIN_SAMPLES soguk baslangici,")
        print("kalibre var ama pozisyon yoksa MIN_EXPECTED_R esigi.")
        return
    closed = [p for p in positions if p.status == "closed" and p.equity_after is not None]
    if not closed:
        print("kapanan pozisyon yok - esitlik egrisi yok")
        return
    wins = sum(1 for p in closed if p.realized_pnl and p.realized_pnl > 0)
    equity = [STARTING_EQUITY] + [p.equity_after for p in closed]
    peak, drawdown = equity[0], Decimal("0")
    for e in equity:
        peak = max(peak, e)
        drawdown = max(drawdown, (peak - e) / peak)
    final = equity[-1]
    print("kapanan: %d  kazanan: %d (%.1f%%)"
          % (len(closed), wins, 100.0 * wins / len(closed)))
    print("baslangic %.2f -> son %.2f  (%+.2f%%)  max drawdown %.1f%%"
          % (STARTING_EQUITY, final,
             100.0 * float(final / STARTING_EQUITY - 1), 100.0 * float(drawdown)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="data/research.db")
    ap.add_argument("--db", default="data/replay.db")
    ap.add_argument("--symbols", type=int, default=40)
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--end", default=None, help="ISO tarih; varsayilan verinin sonu")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--keep", action="store_true",
                    help="mevcut replay veritabanindan devam et")
    ap.add_argument("--budget", type=int, default=0,
                    help="saniye; dolunca temiz durur, --keep ile devam edilir")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s %(message)s")

    source, target = Path(args.source), Path(args.db)
    resume = read_progress(target) if (args.keep and target.exists()) else None
    if resume is None:
        if not source.exists():
            print("kaynak veritabani yok: %s" % source, file=sys.stderr)
            return 2
        prepare(source, target)

    engine = make_engine("sqlite:///%s" % target.as_posix())
    create_all_tables(engine)
    session = make_session_factory(engine)()

    requested = resume[2] if resume else args.symbols
    symbols = universe(target, requested)
    seed_symbols(session, symbols)

    if resume is not None:
        start, end, _ = resume
        print("devam ediliyor: %s tarihinden" % start)
    else:
        conn = sqlite3.connect(target)
        last = conn.execute(
            "SELECT MAX(open_time) FROM klines WHERE timeframe='1h'").fetchone()[0]
        conn.close()
        end = datetime.fromisoformat(args.end) if args.end else datetime.fromisoformat(last)
        end = end.replace(minute=0, second=0, microsecond=0)
        start = end - timedelta(days=args.days)

    schedule = funding_schedule(target, symbols)
    # On a resume the cursor is rebuilt from the clock, not carried: every
    # settlement at or before the restart point has already been applied.
    cursor = sum(1 for stamp, _, _ in schedule if stamp < start)
    hours = max(int((end - start).total_seconds() // 3600) + 1, 0)
    print("replay %s -> %s  (%d saat, %d sembol, surum %s)"
          % (start, end, hours, len(symbols), STRATEGY_VERSION))

    now = start
    step = 0
    t0 = time.time()
    generated = opened = 0
    deadline = t0 + args.budget if args.budget else float("inf")
    while now <= end:
        if time.time() >= deadline:
            write_progress(target, now, end, requested)
            print("\nsure doldu, %s tarihinde durdu - --keep ile devam" % now)
            report(session)
            return 0
        cursor = apply_funding(session, schedule, cursor, now)
        scen = run_scenario_generation(session, symbols, now=now)
        run_learning_cycle(session, now=now)
        paper = run_paper_trading_cycle(session, now=now)
        generated += scen.generated
        opened += paper.opened
        step += 1
        if step % 24 == 0:
            rate = step / max(time.time() - t0, 1e-9)
            left = ((end - now).total_seconds() / 3600) / max(rate, 1e-9)
            print("  %s  senaryo=%d pozisyon=%d  (%.1f saat/sn, ~%.0f sn kaldi)"
                  % (now.date(), generated, opened, rate, left), flush=True)
        now += timedelta(hours=1)

    write_progress(target, end + timedelta(hours=1), end, requested)
    print("\nbitti: %d adim, %.0f sn" % (step, time.time() - t0))
    report(session)
    return 0


if __name__ == "__main__":
    sys.exit(main())
