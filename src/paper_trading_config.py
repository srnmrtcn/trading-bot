from __future__ import annotations

from decimal import Decimal

from src.trading_costs import TAKER_FEE_RATE

# Nominal reference amount — only percentage-based outcomes (win rate, return,
# drawdown) are meaningful; the absolute starting number is an arbitrary anchor.
STARTING_EQUITY = Decimal("10000")

# Fixed-fractional risk: each trade risks this fraction of current equity.
RISK_PCT = Decimal("0.01")

# A scenario needs calibrated_confidence >= this to qualify for a paper position.
MIN_EXPECTED_R = Decimal("0.1")

# Cap on simultaneously open paper positions.
MAX_CONCURRENT_POSITIONS = 10

# Hard ceiling on a position's notional, as a multiple of current equity.
#
# Not a strategy parameter — a guardrail. Fixed-fractional sizing divides by
# the stop distance, and `build_scenario` puts no floor under that distance,
# so a stop that lands a rounding error away from entry asks for arbitrarily
# large notional. Measured on replayed history, the tightest stops came in at
# 0.003% of entry: 300x notional, where fees alone dwarf the intended risk.
MAX_LEVERAGE = Decimal("3")

# Maximum multiple of current equity that a position's notional may reach.
MAX_TOTAL_NOTIONAL_MULTIPLE = Decimal("10")