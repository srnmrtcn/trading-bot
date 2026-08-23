from __future__ import annotations

from decimal import Decimal

# Nominal reference amount — only percentage-based outcomes (win rate, return,
# drawdown) are meaningful; the absolute starting number is an arbitrary anchor.
STARTING_EQUITY = Decimal("10000")

# Fixed-fractional risk: each trade risks this fraction of current equity.
RISK_PCT = Decimal("0.01")

# A scenario needs calibrated_confidence >= this to qualify for a paper position.
CONFIDENCE_THRESHOLD = Decimal("0.65")

# Cap on simultaneously open paper positions.
MAX_CONCURRENT_POSITIONS = 10
