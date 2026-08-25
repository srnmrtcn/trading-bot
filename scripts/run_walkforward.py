"""Choose rule parameters on an in-sample period, then check them once out of sample.

    PYTHONPATH=. python3 scripts/run_walkforward.py

Why this and not `run_backtest.py` with a grid: sweeping N configurations and
reporting the best one measures the luckiest draw, not an edge. On the first
25-symbol sample a 30-cell sweep produced two "profitable" cells at n=12 and
n=21 purely by chance. Here the grid only ever sees the training period, one
configuration is carried forward, and the test period is scored once. The gap
between the two numbers is the finding — a train result that does not survive
the split is noise, however good it looks.
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from decimal import Decimal

from src.btc_regime import compute_btc_regime
from src.db.models import Kline
from src.db.session import make_engine, make_session_factory
from src.paper_trading_config import TAKER_FEE_RATE
from src.research.funnel import (
    FUTURE_HORIZON, MIN_CANDLES, fee_cost_in_r, is_locked, iter_scenarios,
    passes_risk_filters, resolve_draft,
)
from scripts.run_backtest import make_regime_lookup
from scripts.run_funnel import RESEARCH_DB_URL, load_klines

RULES = ["shipped", "trend", "delayed"]
MIN_STOP_PCTS = [None, Decimal("0.003"), Decimal("0.005"), Decimal("0.01")]
MIN_RRS = [None, Decimal("1.0"), Decimal("1.5"), Decimal("2.0")]
# A configuration with fewer resolved trades than this in training is not
# evidence of anything and is never carried to the test period.
MIN_TRAIN_TRADES = 100
def collect_drafts(series, rule, regime_at):
    """Every draft `rule` would produce, with the candles that follow it.

    Built once per rule; the filter grid then replays this list, which is what
    keeps a 48-cell sweep to minutes instead of hours.
    """
    drafts = []
    for symbol, klines in series.items():
        for event in iter_scenarios(symbol, klines, rule, regime_at):
            if event.kind == "draft":
                drafts.append((symbol, event.now, event.draft, event.future))
    return drafts


def score(drafts, min_stop_pct, min_rr, start_after=None, until=None):
    """Replay collected drafts under one filter setting. Returns (n, net_r)."""
    live, trades, net = {}, 0, Decimal("0")
    for symbol, now, draft, future in drafts:
        if start_after is not None and now < start_after:
            continue
        if until is not None and now >= until:
            continue
        key = (symbol, draft.direction)
        if is_locked(live, key, now):
            continue
        if not passes_risk_filters(draft, min_stop_pct, min_rr):
            continue
        live[key] = draft.expires_at
        outcome = resolve_draft(draft, future)
        if outcome is None:
            continue
        risk = abs(draft.entry_price - draft.stop_price)
        trades += 1
        net += outcome.r_multiple - fee_cost_in_r(
            draft.entry_price, outcome.exit_price, risk, TAKER_FEE_RATE,
        )
    return trades, net


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-frac", type=float, default=0.7)
    args = parser.parse_args(argv)

    session = make_session_factory(make_engine(RESEARCH_DB_URL))()
    try:
        symbols = [
            s for (s,) in session.query(Kline.symbol)
            .filter(Kline.timeframe == "1h").distinct().order_by(Kline.symbol).all()
        ]
        series = {s: load_klines(session, s) for s in symbols}
        series = {s: k for s, k in series.items() if len(k) > MIN_CANDLES + FUTURE_HORIZON}
        regime_at = make_regime_lookup(session)

        opens = [k[0]["open_time"] for k in series.values()] + [k[-1]["open_time"] for k in series.values()]
        first, last = min(opens), max(opens)
        boundary = first + (last - first) * args.train_frac
        print(f"{len(series)} sembol | {first:%Y-%m-%d} .. {last:%Y-%m-%d}")
        print(f"train: .. {boundary:%Y-%m-%d}   test: {boundary:%Y-%m-%d} ..")
        print(f"grid: {len(MIN_STOP_PCTS) * len(MIN_RRS)} hucre/kural, "
              f"min {MIN_TRAIN_TRADES} train islemi\n")

        header = (f"{'KURAL':<9} {'SECILEN AYAR':<22} {'TRAIN n':>8} {'TRAIN R/isl':>12} "
                  f"{'TEST n':>7} {'TEST R/isl':>11}")
        print(header)
        print("-" * len(header))

        for rule in RULES:
            drafts = collect_drafts(series, rule, regime_at)
            best, eligible = None, 0
            for stop in MIN_STOP_PCTS:
                for rr in MIN_RRS:
                    n, net = score(drafts, stop, rr, until=boundary)
                    if n < MIN_TRAIN_TRADES:
                        continue
                    eligible += 1
                    expectancy = net / n
                    if best is None or expectancy > best[0]:
                        best = (expectancy, stop, rr, n)
            if best is None:
                print(f"{rule:<9} {'—':<22} {'':>8} {'':>12} "
                      f"{'':>7} {'yetersiz train islemi':>11}")
                continue

            train_exp, stop, rr, train_n = best
            # The only time this configuration meets the test period.
            test_n, test_net = score(drafts, stop, rr, start_after=boundary)
            label = f"stop>={stop} rr>={rr}"
            test_cell = f"{test_net / test_n:>11.3f}" if test_n else f"{'n=0':>11}"
            print(f"{rule:<9} {label:<22} {train_n:>8} {train_exp:>12.3f} {test_n:>7} {test_cell}"
                  f"   ({eligible} uygun hucre)")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
