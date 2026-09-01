"""Every number the momentum book runs on, and where it came from.

These are not tuned. The measurement that produced them deliberately refused
to choose: picking the best cell of a 48-cell grid on the first year of data
gave an in-sample Sharpe of 3.16 and lost money in the second year, while the
average of all 48 cells earned +1.22% then +1.98% a week. So the lookbacks are
averaged rather than selected, and the remaining settings are the conventional
ones -- weekly rebalance, top and bottom fifth -- not the best ones.
"""
from __future__ import annotations

from decimal import Decimal

STRATEGY_VERSION = "2026.09.xsec-momentum-v1"

# Ranked on each of these horizons; the percentile ranks are averaged. Two
# years is 99 weekly observations, which cannot tell a 14-day lookback from a
# 21-day one, so it does not try.
LOOKBACK_DAYS = (7, 14, 21, 30)

# The signal is read one day before the trade, so a stale price feed produces
# no signal rather than a signal built from the bar being traded.
SIGNAL_SKIP_DAYS = 1

REBALANCE_DAYS = 7

# Top and bottom fifth of the eligible universe.
TOP_FRACTION = Decimal("0.2")

# Median daily dollar volume over the trailing month. This floor is the reason
# the strategy is worth running: the dollar-neutral spread pays +0.77% a week
# with no floor and +1.20% above this one. It gets stronger as liquidity is
# demanded, which is the opposite of the funding trade that died the moment
# size was asked of it.
MIN_DOLLAR_VOLUME = Decimal("50000000")
LIQUIDITY_WINDOW_DAYS = 30

# Notional per leg as a multiple of equity. Two legs, so gross exposure is
# twice this. The measured drawdown of 16.9% is at exactly this exposure.
LEG_EXPOSURE = Decimal("1")

# A leg needs enough names for equal weighting to mean anything.
MIN_UNIVERSE = 15

STARTING_EQUITY = Decimal("10000")
