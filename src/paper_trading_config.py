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

# Binance USDT-M perpetual taker fee, charged on each leg's own notional.
# Booked on every close: a paper portfolio that ignores costs reports an edge
# that does not survive contact with the exchange — measured on 90 days of
# replayed history, fees alone turned a +17.7R gross result into -14.5R net.
TAKER_FEE_RATE = Decimal("0.0005")
