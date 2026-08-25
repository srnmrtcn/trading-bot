"""Score each candidate signal rule over the local research data.

    PYTHONPATH=. python3 scripts/run_backtest.py
    PYTHONPATH=. python3 scripts/run_backtest.py --min-stop-pct 0.005 --min-rr 1.5
    PYTHONPATH=. python3 scripts/run_backtest.py --no-regime

By default the replay applies the BTC regime gate exactly as the hourly job
does. The risk filters are off by default, which means the headline numbers
include scenarios whose stop sits a rounding error from entry — trades whose R
is arithmetically meaningless in both directions. Pass --min-stop-pct to
exclude them.
"""
from __future__ import annotations

import argparse
import sys
from decimal import Decimal

from src.btc_regime import compute_btc_regime
from src.db.models import Kline
from src.db.session import make_engine, make_session_factory
from src.research.funnel import RuleResult, backtest_symbol
from scripts.run_funnel import RESEARCH_DB_URL, load_klines

RULES = [("shipped", "A: mevcut"), ("trend", "B: trend hizali"), ("delayed", "C: gecikmeli")]


def merge(into: RuleResult, other: RuleResult) -> None:
    for field in vars(other):
        setattr(into, field, getattr(into, field) + getattr(other, field))


def make_regime_lookup(session):
    """BTC's daily regime, computed once per day and reused.

    `run_scenario_generation` computes it once per hourly run; caching by date
    matches that without a query per signal.
    """
    cache = {}

    def regime_at(now):
        key = now.date()
        if key not in cache:
            cache[key] = compute_btc_regime(session, now)
        return cache[key]

    return regime_at


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-stop-pct", type=Decimal, default=None,
                        help="reject drafts whose stop is closer than this fraction of entry (e.g. 0.005)")
    parser.add_argument("--min-rr", type=Decimal, default=None,
                        help="reject drafts below this reward-to-risk (e.g. 1.5)")
    parser.add_argument("--no-regime", action="store_true",
                        help="lift the BTC regime gate the hourly job applies")
    args = parser.parse_args(argv)

    session = make_session_factory(make_engine(RESEARCH_DB_URL))()
    try:
        symbols = [
            s for (s,) in session.query(Kline.symbol)
            .filter(Kline.timeframe == "1h").distinct().order_by(Kline.symbol).all()
        ]
        series = {symbol: load_klines(session, symbol) for symbol in symbols}
        regime_at = None if args.no_regime else make_regime_lookup(session)

        print(f"filtreler: min_stop_pct={args.min_stop_pct} min_rr={args.min_rr} "
              f"rejim_kapisi={'kapali' if args.no_regime else 'acik'}\n")
        header = (f"{'KURAL':<16} {'SINYAL':>7} {'REJIM':>6} {'SR YOK':>7} {'FILTRE':>7} {'ISLEM':>6} "
                  f"{'HEDEF':>6} {'STOP':>5} {'SURE':>5} {'WIN%':>6} {'BRUT R':>8} {'KOMISY':>7} {'NET R':>8} {'BEKL':>7}")
        print(header)
        print("-" * len(header))
        for rule, label in RULES:
            total = RuleResult()
            for symbol in symbols:
                merge(total, backtest_symbol(
                    symbol, series[symbol], rule,
                    min_stop_pct=args.min_stop_pct, min_rr=args.min_rr, regime_at=regime_at,
                ))
            scored = total.hit_target + total.hit_stop + total.expired
            if scored == 0:
                print(f"{label:<16} {total.signals:>7} {total.regime_blocked:>6} {total.no_levels:>7} "
                      f"{total.filtered:>7} {scored:>6}   — skorlanabilir islem yok")
                continue
            win = Decimal(total.hit_target) / Decimal(scored) * 100
            net = total.total_r - total.total_fee_r
            print(f"{label:<16} {total.signals:>7} {total.regime_blocked:>6} {total.no_levels:>7} "
                  f"{total.filtered:>7} {scored:>6} {total.hit_target:>6} {total.hit_stop:>5} "
                  f"{total.expired:>5} {win:>5.1f}% {total.total_r:>8.1f} {total.total_fee_r:>7.1f} "
                  f"{net:>8.1f} {net / scored:>7.3f}")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
