"""Score each candidate signal rule over the local research data.

    PYTHONPATH=. python3 scripts/run_backtest.py
"""
from __future__ import annotations

import sys
from decimal import Decimal

from src.db.models import Kline
from src.db.session import make_engine, make_session_factory
from src.research.funnel import RuleResult, backtest_symbol
from scripts.run_funnel import RESEARCH_DB_URL, load_klines

RULES = [("shipped", "A: mevcut"), ("trend", "B: trend hizali"), ("delayed", "C: gecikmeli")]


def merge(into: RuleResult, other: RuleResult) -> None:
    for field in vars(other):
        setattr(into, field, getattr(into, field) + getattr(other, field))


def main() -> int:
    session = make_session_factory(make_engine(RESEARCH_DB_URL))()
    try:
        symbols = [s for (s,) in session.query(Kline.symbol).distinct().order_by(Kline.symbol).all()]
        series = {symbol: load_klines(session, symbol) for symbol in symbols}
    finally:
        session.close()

    print(f"{'KURAL':<16} {'SINYAL':>7} {'SR YOK':>7} {'SKORSUZ':>8} {'ISLEM':>6} "
          f"{'HEDEF':>6} {'STOP':>5} {'SURE':>5} {'WIN%':>6} {'BRUT R':>8} {'NET R':>8} {'BEKL/ISL':>9}")
    print("-" * 104)
    for rule, label in RULES:
        total = RuleResult()
        for symbol in symbols:
            merge(total, backtest_symbol(symbol, series[symbol], rule))
        scored = total.hit_target + total.hit_stop + total.expired
        if scored == 0:
            print(f"{label:<16} {total.signals:>7} {total.no_levels:>7} {total.unscored:>8} {scored:>6}"
                  "        — skorlanabilir islem yok")
            continue
        win = Decimal(total.hit_target) / Decimal(scored) * 100
        net = total.total_r - total.total_fee_r
        print(f"{label:<16} {total.signals:>7} {total.no_levels:>7} {total.unscored:>8} {scored:>6} "
              f"{total.hit_target:>6} {total.hit_stop:>5} {total.expired:>5} {win:>5.1f}% "
              f"{total.total_r:>8.1f} {net:>8.1f} {net / scored:>9.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
