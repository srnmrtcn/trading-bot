"""Replay the production signal rule over the local research data.

    PYTHONPATH=. python3 scripts/run_funnel.py
"""
from __future__ import annotations

import sys

from src.db.models import Kline
from src.db.session import make_engine, make_session_factory
from src.research.funnel import FunnelCounts, analyze_symbol

RESEARCH_DB_URL = "sqlite:///data/research.db"
TIMEFRAME = "1h"


def load_klines(session, symbol: str) -> list:
    rows = (
        session.query(Kline)
        .filter(Kline.symbol == symbol, Kline.timeframe == TIMEFRAME)
        .order_by(Kline.open_time.asc())
        .all()
    )
    return [
        {"open_time": r.open_time, "open": r.open, "high": r.high,
         "low": r.low, "close": r.close, "volume": r.volume, "flagged": r.flagged}
        for r in rows
    ]


def main() -> int:
    session = make_session_factory(make_engine(RESEARCH_DB_URL))()
    try:
        symbols = [s for (s,) in session.query(Kline.symbol).distinct().order_by(Kline.symbol).all()]
        total = FunnelCounts()
        header = f"{'SYMBOL':<10} {'EVAL':>6} {'RSI↑':>6} {'CONFL↑':>7} | {'A:shipped':>10} {'B:trend':>8} {'C:delayed':>10}"
        print(header)
        print("-" * len(header))
        for symbol in symbols:
            c = analyze_symbol(load_klines(session, symbol))
            print(f"{symbol:<10} {c.evaluated:>6} {c.rsi_cross_up:>6} {c.confluence_bullish:>7} | "
                  f"{c.signal_long:>10} {c.signal_long_trend_aligned:>8} {c.signal_long_delayed_confirm:>10}")
            total.evaluated += c.evaluated
            total.rsi_cross_up += c.rsi_cross_up
            total.confluence_bullish += c.confluence_bullish
            total.signal_long += c.signal_long
            total.signal_long_trend_aligned += c.signal_long_trend_aligned
            total.signal_long_delayed_confirm += c.signal_long_delayed_confirm
        print("-" * len(header))
        print(f"{'TOPLAM':<10} {total.evaluated:>6} {total.rsi_cross_up:>6} {total.confluence_bullish:>7} | "
              f"{total.signal_long:>10} {total.signal_long_trend_aligned:>8} {total.signal_long_delayed_confirm:>10}")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
