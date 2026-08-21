# Senaryo Üretim Motoru Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After every hourly kline update, scan all active symbols and generate long/short profit scenarios (entry/target/stop/expected-return/confidence/expiry) from RSI + EMA-crossover + volume-spike signals combined with support/resistance levels, storing them in a new `scenarios` table.

**Architecture:** Six new small modules (`src/indicators.py`, `src/support_resistance.py`, `src/scenario_signal.py`, `src/scenario_builder.py`, `src/scenario_storage.py`, `src/scenario_runner.py`) plus one new `Scenario` model, wired into the existing `run_timeframe_job("1h")` in `src/scheduler.py` as a single pass over all active symbols after their kline fetch/gap-repair completes. Every function is pure and reads only from data already in memory or already fetched from `klines` — no new Binance calls.

**Tech Stack:** Same as Subsystem A — Python 3.9, SQLAlchemy 2.0 ORM, `Decimal` for all price/ratio math, pytest with `sqlite:///:memory:`.

## Global Constraints

- Python 3.9 compatibility — every file using `X | None` / `list[X]` style annotations MUST start with `from __future__ import annotations`.
- All price, ratio, and score arithmetic uses `Decimal`, never `float` (except the single `timedelta(hours=float(...))` conversion in the expiry calculation, which is a duration, not a financial value).
- A symbol's scenario-generation failure must never abort processing of other symbols — mirror the existing `try/except` + `session.rollback()` + `logger.exception` isolation pattern already established in `src/scheduler.py`'s `run_timeframe_job`.
- Long signal: previous RSI < 30 and current RSI ≥ 30 (crossed up through oversold), **and** EMA(9) crossed above EMA(21) on the latest candle, **and** a volume spike (current volume > 2× the average of the prior 20 candles) — all three must hold together.
- Short signal: previous RSI > 70 and current RSI ≤ 70 (crossed down through overbought), **and** EMA(9) crossed below EMA(21), **and** a volume spike — all three must hold together.
- Long target/stop: nearest swing-high resistance above entry = target, nearest swing-low support below entry = stop. Short is the mirror image. If either level is missing, no scenario is produced for that symbol this run.
- A symbol needs at least 100 stored 1h candles before any signal is evaluated; fewer than that is a silent skip, not an error.
- No duplicate `pending` scenario for the same `(symbol, direction)` — skip generation if one already exists.
- `expires_at` is clamped to between 6 and 168 hours from `created_at`.

---

## File Structure

```
src/
├── indicators.py            (new)  RSI, EMA, EMA crossover, volume spike, ATR
├── support_resistance.py    (new)  swing high/low detection, nearest support/resistance
├── scenario_signal.py       (new)  long/short signal evaluation
├── scenario_builder.py      (new)  entry/target/stop/expected-return/confidence/expiry
├── scenario_storage.py      (new)  pending-scenario dedup check + insert
├── scenario_runner.py       (new)  per-symbol orchestration + isolation
├── scheduler.py             (modify)  hook scenario generation into the 1h job
└── db/
    └── models.py            (modify)  add the Scenario model
tests/
├── test_indicators.py           (new)
├── test_support_resistance.py   (new)
├── test_scenario_signal.py      (new)
├── test_scenario_builder.py     (new)
├── test_scenario_storage.py     (new)
├── test_scenario_runner.py      (new)
├── test_models.py               (modify)  Scenario model tests
└── test_scheduler.py            (modify)  scenario-generation wiring tests
```

---

### Task 1: `Scenario` model

**Files:**
- Modify: `src/db/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: `src.db.base.Base`, `src.timeutil.utc_now` (both already used by `Symbol`/`Kline`/`FetchLog` in this file)
- Produces: `db.models.Scenario(id, symbol, direction, entry_price, target_price, stop_price, expected_return_pct, confidence_score, created_at, expires_at, status)`

- [ ] **Step 1: Write the failing test**

In `tests/test_models.py`, change the top two import lines from:

```python
from datetime import datetime
from decimal import Decimal
```

to:

```python
from datetime import datetime, timedelta
from decimal import Decimal
```

and change the models import line from:

```python
from src.db.models import Symbol, Kline, FetchLog
```

to:

```python
from src.db.models import Symbol, Kline, FetchLog, Scenario
```

Then add these two tests to the end of the file:

```python
def test_insert_scenario(db_session):
    now = datetime(2026, 1, 1, 12, 0, 0)
    db_session.add(Scenario(
        symbol="BTCUSDT", direction="long",
        entry_price=Decimal("50000"), target_price=Decimal("52000"), stop_price=Decimal("49000"),
        expected_return_pct=Decimal("0.04"), confidence_score=Decimal("0.7"),
        created_at=now, expires_at=now + timedelta(hours=24), status="pending",
    ))
    db_session.commit()
    row = db_session.query(Scenario).first()
    assert row.symbol == "BTCUSDT"
    assert row.direction == "long"
    assert row.status == "pending"


def test_scenario_status_defaults_to_pending(db_session):
    now = datetime(2026, 1, 1, 12, 0, 0)
    db_session.add(Scenario(
        symbol="ETHUSDT", direction="short",
        entry_price=Decimal("3000"), target_price=Decimal("2900"), stop_price=Decimal("3050"),
        expected_return_pct=Decimal("0.033"), confidence_score=Decimal("0.5"),
        created_at=now, expires_at=now + timedelta(hours=24),
    ))
    db_session.commit()
    row = db_session.query(Scenario).filter(Scenario.symbol == "ETHUSDT").first()
    assert row.status == "pending"
```

Make sure the top of `tests/test_models.py` imports `timedelta` alongside `datetime` if it doesn't already.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_models.py -v`
Expected: FAIL (`ImportError: cannot import name 'Scenario'` or `AttributeError`)

- [ ] **Step 3: Write minimal implementation**

In `src/db/models.py`, add after the `FetchLog` class:

```python
class Scenario(Base):
    __tablename__ = "scenarios"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False, index=True)
    direction = Column(String, nullable=False)
    entry_price = Column(Numeric(20, 8), nullable=False)
    target_price = Column(Numeric(20, 8), nullable=False)
    stop_price = Column(Numeric(20, 8), nullable=False)
    expected_return_pct = Column(Numeric(10, 6), nullable=False)
    confidence_score = Column(Numeric(5, 4), nullable=False)
    created_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    status = Column(String, nullable=False, default="pending")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/db/models.py tests/test_models.py
git commit -m "feat: add Scenario model"
```

---

### Task 2: Indicators (RSI, EMA, crossover, volume spike, ATR)

**Files:**
- Create: `src/indicators.py`
- Test: `tests/test_indicators.py`

**Interfaces:**
- Produces:
  - `indicators.compute_rsi(closes: list, period: int = 14) -> list` — same length as `closes`, `None` for indices with insufficient history, else a `Decimal` 0–100.
  - `indicators.compute_ema(values: list, period: int) -> list` — same length as `values`, `None` before the first full window, else a `Decimal`.
  - `indicators.detect_ema_crossover(fast: list, slow: list) -> str` — `"bullish"`, `"bearish"`, or `"none"`, based only on the last two entries of each series.
  - `indicators.detect_volume_spike(volumes: list, lookback: int = 20, multiplier: Decimal = Decimal("2")) -> bool`
  - `indicators.average_true_range(klines: list, period: int = 20) -> Decimal` — `klines` entries are dicts with `open_time`, `open`, `high`, `low`, `close`, `volume` keys (the shape every later task in this plan uses); returns `Decimal("0")` if there isn't enough history.

- [ ] **Step 1: Write the failing tests**

`tests/test_indicators.py`:
```python
from decimal import Decimal

from src.indicators import (
    compute_rsi, compute_ema, detect_ema_crossover, detect_volume_spike, average_true_range,
)


def _decimals(values):
    return [Decimal(str(v)) for v in values]


def test_compute_rsi_matches_hand_calculation():
    # 15 closes: 14 up-moves of +1 each starting at 100 -> RSI should be 100
    # (all gains, zero losses).
    closes = _decimals([100 + i for i in range(15)])
    rsi = compute_rsi(closes, period=14)
    assert rsi[:14] == [None] * 14
    assert rsi[14] == Decimal("100")


def test_compute_rsi_is_50_for_equal_gains_and_losses():
    # Alternating +1/-1 moves of equal size over the period -> avg gain == avg loss -> RSI 50.
    closes = _decimals([100, 101, 100, 101, 100, 101, 100, 101, 100, 101, 100, 101, 100, 101, 100])
    rsi = compute_rsi(closes, period=14)
    assert rsi[14] == Decimal("50")


def test_compute_ema_seeds_with_sma_then_smooths():
    values = _decimals([1, 2, 3, 4, 5])
    ema = compute_ema(values, period=3)
    assert ema[0] is None
    assert ema[1] is None
    assert ema[2] == Decimal("2")  # SMA of [1,2,3]
    k = Decimal("2") / Decimal("4")  # 2/(period+1)
    expected_3 = values[3] * k + ema[2] * (Decimal("1") - k)
    assert ema[3] == expected_3


def test_detect_ema_crossover_bullish():
    fast = [Decimal("9"), Decimal("11")]
    slow = [Decimal("10"), Decimal("10")]
    assert detect_ema_crossover(fast, slow) == "bullish"


def test_detect_ema_crossover_bearish():
    fast = [Decimal("11"), Decimal("9")]
    slow = [Decimal("10"), Decimal("10")]
    assert detect_ema_crossover(fast, slow) == "bearish"


def test_detect_ema_crossover_none_when_no_cross():
    fast = [Decimal("12"), Decimal("13")]
    slow = [Decimal("10"), Decimal("10")]
    assert detect_ema_crossover(fast, slow) == "none"


def test_detect_volume_spike_true_when_current_exceeds_multiplier():
    volumes = _decimals([100] * 20 + [250])  # avg of first 20 = 100, current 250 > 2x
    assert detect_volume_spike(volumes, lookback=20, multiplier=Decimal("2")) is True


def test_detect_volume_spike_false_when_below_multiplier():
    volumes = _decimals([100] * 20 + [150])
    assert detect_volume_spike(volumes, lookback=20, multiplier=Decimal("2")) is False


def test_average_true_range_computes_expected_value():
    klines = [
        {"high": Decimal("110"), "low": Decimal("90"), "close": Decimal("100")},
        {"high": Decimal("115"), "low": Decimal("95"), "close": Decimal("105")},
    ]
    # true range for the 2nd candle: max(115-95, |115-100|, |95-100|) = max(20, 15, 5) = 20
    atr = average_true_range(klines, period=1)
    assert atr == Decimal("20")


def test_average_true_range_returns_zero_with_insufficient_data():
    klines = [{"high": Decimal("110"), "low": Decimal("90"), "close": Decimal("100")}]
    assert average_true_range(klines, period=20) == Decimal("0")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_indicators.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.indicators'`)

- [ ] **Step 3: Write minimal implementation**

`src/indicators.py`:
```python
from __future__ import annotations

from decimal import Decimal


def compute_rsi(closes: list, period: int = 14) -> list:
    rsi_values = [None] * len(closes)
    if len(closes) <= period:
        return rsi_values

    gains = []
    losses = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, Decimal("0")))
        losses.append(max(-change, Decimal("0")))

    for i in range(period, len(closes)):
        window_gains = gains[i - period:i]
        window_losses = losses[i - period:i]
        avg_gain = sum(window_gains) / period
        avg_loss = sum(window_losses) / period
        if avg_loss == 0:
            rsi_values[i] = Decimal("100")
        else:
            rs = avg_gain / avg_loss
            rsi_values[i] = Decimal("100") - (Decimal("100") / (Decimal("1") + rs))
    return rsi_values


def compute_ema(values: list, period: int) -> list:
    ema_values = [None] * len(values)
    if len(values) < period:
        return ema_values

    k = Decimal("2") / (Decimal(period) + Decimal("1"))
    sma = sum(values[:period]) / period
    ema_values[period - 1] = sma
    prev = sma
    for i in range(period, len(values)):
        current = values[i] * k + prev * (Decimal("1") - k)
        ema_values[i] = current
        prev = current
    return ema_values


def detect_ema_crossover(fast: list, slow: list) -> str:
    if len(fast) < 2 or len(slow) < 2:
        return "none"
    f_prev, f_curr = fast[-2], fast[-1]
    s_prev, s_curr = slow[-2], slow[-1]
    if f_prev is None or f_curr is None or s_prev is None or s_curr is None:
        return "none"
    if f_prev <= s_prev and f_curr > s_curr:
        return "bullish"
    if f_prev >= s_prev and f_curr < s_curr:
        return "bearish"
    return "none"


def detect_volume_spike(volumes: list, lookback: int = 20, multiplier: Decimal = Decimal("2")) -> bool:
    if len(volumes) < lookback + 1:
        return False
    current = volumes[-1]
    window = volumes[-lookback - 1:-1]
    avg = sum(window) / lookback
    if avg == 0:
        return False
    return current > avg * multiplier


def average_true_range(klines: list, period: int = 20) -> Decimal:
    if len(klines) < period + 1:
        return Decimal("0")
    true_ranges = []
    for i in range(1, len(klines)):
        high = klines[i]["high"]
        low = klines[i]["low"]
        prev_close = klines[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)
    recent = true_ranges[-period:]
    return sum(recent) / period
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_indicators.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add src/indicators.py tests/test_indicators.py
git commit -m "feat: add RSI, EMA, volume-spike, and ATR indicator functions"
```

---

### Task 3: Support/Resistance detector

**Files:**
- Create: `src/support_resistance.py`
- Test: `tests/test_support_resistance.py`

**Interfaces:**
- Produces:
  - `support_resistance.SwingPoint(open_time: datetime, price: Decimal)` (dataclass)
  - `support_resistance.find_swing_points(klines: list, k: int = 3) -> tuple` — `(swing_highs: list[SwingPoint], swing_lows: list[SwingPoint])`. `klines` entries are dicts with `open_time`, `high`, `low` keys (the same shape `indicators.py` uses).
  - `support_resistance.nearest_resistance(swing_highs: list, current_price: Decimal)` — lowest swing-high price strictly above `current_price`, or `None`.
  - `support_resistance.nearest_support(swing_lows: list, current_price: Decimal)` — highest swing-low price strictly below `current_price`, or `None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_support_resistance.py`:
```python
from datetime import datetime, timedelta
from decimal import Decimal

from src.support_resistance import find_swing_points, nearest_resistance, nearest_support


def _kline(hour, high, low):
    return {"open_time": datetime(2026, 1, 1) + timedelta(hours=hour), "high": Decimal(str(high)), "low": Decimal(str(low))}


def test_find_swing_points_detects_a_clear_peak_and_trough():
    # Index 5 is a clear local high (100), index 10 a clear local low (80).
    highs = [90, 91, 92, 93, 94, 100, 94, 93, 92, 91, 80, 91, 92, 93, 94]
    lows = [h - 10 for h in highs]
    lows[10] = 70  # deepen the trough at index 10 so it's a clear local low
    klines = [_kline(i, highs[i], lows[i]) for i in range(len(highs))]

    swing_highs, swing_lows = find_swing_points(klines, k=3)

    assert any(p.price == Decimal("100") for p in swing_highs)
    assert any(p.price == Decimal("70") for p in swing_lows)


def test_find_swing_points_empty_for_monotonic_series():
    klines = [_kline(i, 100 + i, 90 + i) for i in range(10)]
    swing_highs, swing_lows = find_swing_points(klines, k=3)
    assert swing_highs == []
    assert swing_lows == []


def test_nearest_resistance_returns_lowest_price_above_current():
    from src.support_resistance import SwingPoint
    points = [
        SwingPoint(open_time=datetime(2026, 1, 1), price=Decimal("120")),
        SwingPoint(open_time=datetime(2026, 1, 2), price=Decimal("110")),
        SwingPoint(open_time=datetime(2026, 1, 3), price=Decimal("95")),  # below current, excluded
    ]
    assert nearest_resistance(points, current_price=Decimal("100")) == Decimal("110")


def test_nearest_resistance_none_when_nothing_above():
    from src.support_resistance import SwingPoint
    points = [SwingPoint(open_time=datetime(2026, 1, 1), price=Decimal("90"))]
    assert nearest_resistance(points, current_price=Decimal("100")) is None


def test_nearest_support_returns_highest_price_below_current():
    from src.support_resistance import SwingPoint
    points = [
        SwingPoint(open_time=datetime(2026, 1, 1), price=Decimal("80")),
        SwingPoint(open_time=datetime(2026, 1, 2), price=Decimal("90")),
        SwingPoint(open_time=datetime(2026, 1, 3), price=Decimal("105")),  # above current, excluded
    ]
    assert nearest_support(points, current_price=Decimal("100")) == Decimal("90")


def test_nearest_support_none_when_nothing_below():
    from src.support_resistance import SwingPoint
    points = [SwingPoint(open_time=datetime(2026, 1, 1), price=Decimal("110"))]
    assert nearest_support(points, current_price=Decimal("100")) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_support_resistance.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.support_resistance'`)

- [ ] **Step 3: Write minimal implementation**

`src/support_resistance.py`:
```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass
class SwingPoint:
    open_time: datetime
    price: Decimal


def find_swing_points(klines: list, k: int = 3) -> tuple:
    swing_highs = []
    swing_lows = []
    n = len(klines)
    for i in range(k, n - k):
        neighborhood = klines[i - k:i] + klines[i + 1:i + k + 1]
        candle = klines[i]
        if all(candle["high"] > c["high"] for c in neighborhood):
            swing_highs.append(SwingPoint(open_time=candle["open_time"], price=candle["high"]))
        if all(candle["low"] < c["low"] for c in neighborhood):
            swing_lows.append(SwingPoint(open_time=candle["open_time"], price=candle["low"]))
    return swing_highs, swing_lows


def nearest_resistance(swing_highs: list, current_price: Decimal):
    candidates = [p.price for p in swing_highs if p.price > current_price]
    return min(candidates) if candidates else None


def nearest_support(swing_lows: list, current_price: Decimal):
    candidates = [p.price for p in swing_lows if p.price < current_price]
    return max(candidates) if candidates else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_support_resistance.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/support_resistance.py tests/test_support_resistance.py
git commit -m "feat: add swing high/low support-resistance detection"
```

---

### Task 4: Signal Evaluator

**Files:**
- Create: `src/scenario_signal.py`
- Test: `tests/test_scenario_signal.py`

**Interfaces:**
- Consumes: `indicators.compute_rsi`, `indicators.compute_ema`, `indicators.detect_ema_crossover`, `indicators.detect_volume_spike` (Task 2)
- Produces:
  - `scenario_signal.RSI_PERIOD = 14`, `EMA_FAST_PERIOD = 9`, `EMA_SLOW_PERIOD = 21`, `VOLUME_LOOKBACK = 20`, `VOLUME_MULTIPLIER = Decimal("2")`, `RSI_OVERSOLD = Decimal("30")`, `RSI_OVERBOUGHT = Decimal("70")`, `MIN_CANDLES = 100` — module-level constants later tasks import.
  - `scenario_signal.SignalResult(direction: str, entry_price: Decimal, rsi: Decimal, previous_rsi: Decimal)` (dataclass)
  - `scenario_signal.evaluate_signal(klines: list) -> SignalResult` (or `None`). `klines` entries are dicts with `open_time`, `open`, `high`, `low`, `close`, `volume` keys, sorted ascending by `open_time`.

- [ ] **Step 1: Write the failing tests**

`tests/test_scenario_signal.py`:
```python
from datetime import datetime, timedelta
from decimal import Decimal

from src.scenario_signal import evaluate_signal, MIN_CANDLES


def _flat_klines(count, price=Decimal("100"), volume=Decimal("1000")):
    return [
        {
            "open_time": datetime(2026, 1, 1) + timedelta(hours=i),
            "open": price, "high": price, "low": price, "close": price, "volume": volume,
        }
        for i in range(count)
    ]


def test_evaluate_signal_returns_none_with_insufficient_candles():
    klines = _flat_klines(MIN_CANDLES - 1)
    assert evaluate_signal(klines) is None


def test_evaluate_signal_returns_none_for_flat_uneventful_data():
    klines = _flat_klines(MIN_CANDLES)
    assert evaluate_signal(klines) is None


def test_evaluate_signal_detects_long_setup():
    # Build a long, gentle downtrend (pushes RSI well below 30, EMA9 below EMA21),
    # then a sharp reversal candle with a volume spike that pushes RSI back
    # above 30 and crosses EMA9 above EMA21.
    klines = _flat_klines(60, price=Decimal("100"))
    price = Decimal("100")
    downtrend = []
    for i in range(60):
        price -= Decimal("1")
        downtrend.append({
            "open_time": datetime(2026, 1, 1) + timedelta(hours=60 + i),
            "open": price, "high": price, "low": price, "close": price, "volume": Decimal("1000"),
        })
    klines = klines + downtrend
    last_price = downtrend[-1]["close"]
    reversal_price = last_price + Decimal("30")
    reversal = {
        "open_time": datetime(2026, 1, 1) + timedelta(hours=120),
        "open": last_price, "high": reversal_price, "low": last_price,
        "close": reversal_price, "volume": Decimal("5000"),
    }
    klines = klines + [reversal]

    signal = evaluate_signal(klines)

    assert signal is not None
    assert signal.direction == "long"
    assert signal.entry_price == reversal_price


def test_evaluate_signal_detects_short_setup():
    klines = _flat_klines(60, price=Decimal("100"))
    price = Decimal("100")
    uptrend = []
    for i in range(60):
        price += Decimal("1")
        uptrend.append({
            "open_time": datetime(2026, 1, 1) + timedelta(hours=60 + i),
            "open": price, "high": price, "low": price, "close": price, "volume": Decimal("1000"),
        })
    klines = klines + uptrend
    last_price = uptrend[-1]["close"]
    reversal_price = last_price - Decimal("30")
    reversal = {
        "open_time": datetime(2026, 1, 1) + timedelta(hours=120),
        "open": last_price, "high": last_price, "low": reversal_price,
        "close": reversal_price, "volume": Decimal("5000"),
    }
    klines = klines + [reversal]

    signal = evaluate_signal(klines)

    assert signal is not None
    assert signal.direction == "short"
    assert signal.entry_price == reversal_price
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_scenario_signal.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.scenario_signal'`)

- [ ] **Step 3: Write minimal implementation**

`src/scenario_signal.py`:
```python
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.indicators import compute_ema, compute_rsi, detect_ema_crossover, detect_volume_spike

RSI_PERIOD = 14
EMA_FAST_PERIOD = 9
EMA_SLOW_PERIOD = 21
VOLUME_LOOKBACK = 20
VOLUME_MULTIPLIER = Decimal("2")
RSI_OVERSOLD = Decimal("30")
RSI_OVERBOUGHT = Decimal("70")
MIN_CANDLES = 100


@dataclass
class SignalResult:
    direction: str
    entry_price: Decimal
    rsi: Decimal
    previous_rsi: Decimal


def evaluate_signal(klines: list):
    if len(klines) < MIN_CANDLES:
        return None

    closes = [c["close"] for c in klines]
    volumes = [c["volume"] for c in klines]

    rsi_series = compute_rsi(closes, RSI_PERIOD)
    ema_fast = compute_ema(closes, EMA_FAST_PERIOD)
    ema_slow = compute_ema(closes, EMA_SLOW_PERIOD)
    crossover = detect_ema_crossover(ema_fast, ema_slow)
    volume_spike = detect_volume_spike(volumes, VOLUME_LOOKBACK, VOLUME_MULTIPLIER)

    current_rsi = rsi_series[-1]
    previous_rsi = rsi_series[-2]
    if current_rsi is None or previous_rsi is None:
        return None

    entry_price = closes[-1]

    if previous_rsi < RSI_OVERSOLD <= current_rsi and crossover == "bullish" and volume_spike:
        return SignalResult(direction="long", entry_price=entry_price, rsi=current_rsi, previous_rsi=previous_rsi)
    if previous_rsi > RSI_OVERBOUGHT >= current_rsi and crossover == "bearish" and volume_spike:
        return SignalResult(direction="short", entry_price=entry_price, rsi=current_rsi, previous_rsi=previous_rsi)
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_scenario_signal.py -v`
Expected: PASS (4 tests)

If the long/short synthetic-data tests don't trigger on the first attempt (getting the exact combination of RSI crossing 30/70 AND an EMA crossover AND a volume spike to align from hand-built data can take some tuning), adjust the synthetic trend length, the reversal candle's price jump, or its volume — the assertions and the constants in `scenario_signal.py` must not change to make a stubborn test pass; only the test's synthetic input data may be adjusted.

- [ ] **Step 5: Commit**

```bash
git add src/scenario_signal.py tests/test_scenario_signal.py
git commit -m "feat: add long/short signal evaluation"
```

---

### Task 5: Scenario Builder

**Files:**
- Create: `src/scenario_builder.py`
- Test: `tests/test_scenario_builder.py`

**Interfaces:**
- Consumes: `support_resistance.find_swing_points`, `nearest_resistance`, `nearest_support` (Task 3); `indicators.average_true_range` (Task 2); `scenario_signal.SignalResult`, `RSI_OVERSOLD`, `RSI_OVERBOUGHT` (Task 4)
- Produces:
  - `scenario_builder.ScenarioDraft(symbol, direction, entry_price, target_price, stop_price, expected_return_pct, confidence_score, created_at, expires_at)` (dataclass)
  - `scenario_builder.build_scenario(symbol: str, signal: SignalResult, klines: list, now: datetime) -> ScenarioDraft` (or `None`)

- [ ] **Step 1: Write the failing tests**

`tests/test_scenario_builder.py`:
```python
from datetime import datetime, timedelta
from decimal import Decimal

from src.scenario_builder import build_scenario, MIN_EXPIRY_HOURS, MAX_EXPIRY_HOURS
from src.scenario_signal import SignalResult


def _kline(hour, high, low, close=None):
    close = close if close is not None else (high + low) / 2
    return {
        "open_time": datetime(2026, 1, 1) + timedelta(hours=hour),
        "open": close, "high": Decimal(str(high)), "low": Decimal(str(low)), "close": close,
        "volume": Decimal("1000"),
    }


def _klines_with_swings():
    # A clear swing low at index 5 (90) below, a clear swing high at index 15
    # (130) above, entry sits between them.
    klines = [_kline(i, 105, 95) for i in range(30)]
    klines[5] = _kline(5, 91, 90)
    klines[15] = _kline(15, 130, 129)
    return klines


def test_build_scenario_long_uses_resistance_as_target_and_support_as_stop():
    klines = _klines_with_swings()
    signal = SignalResult(direction="long", entry_price=Decimal("100"), rsi=Decimal("35"), previous_rsi=Decimal("25"))
    now = datetime(2026, 1, 2)

    draft = build_scenario("BTCUSDT", signal, klines, now)

    assert draft is not None
    assert draft.symbol == "BTCUSDT"
    assert draft.direction == "long"
    assert draft.entry_price == Decimal("100")
    assert draft.target_price == Decimal("130")
    assert draft.stop_price == Decimal("90")
    assert draft.expected_return_pct == (Decimal("130") - Decimal("100")) / Decimal("100")
    assert Decimal("0") <= draft.confidence_score <= Decimal("1")
    assert draft.created_at == now
    assert MIN_EXPIRY_HOURS <= (draft.expires_at - now).total_seconds() / 3600 <= MAX_EXPIRY_HOURS


def test_build_scenario_short_uses_support_as_target_and_resistance_as_stop():
    klines = _klines_with_swings()
    signal = SignalResult(direction="short", entry_price=Decimal("100"), rsi=Decimal("65"), previous_rsi=Decimal("75"))
    now = datetime(2026, 1, 2)

    draft = build_scenario("BTCUSDT", signal, klines, now)

    assert draft is not None
    assert draft.target_price == Decimal("90")
    assert draft.stop_price == Decimal("130")
    assert draft.expected_return_pct == (Decimal("100") - Decimal("90")) / Decimal("100")


def test_build_scenario_returns_none_when_no_resistance_above_entry():
    # Entry above every swing high in the data -> no resistance found.
    klines = [_kline(i, 105, 95) for i in range(30)]
    klines[5] = _kline(5, 91, 90)
    signal = SignalResult(direction="long", entry_price=Decimal("200"), rsi=Decimal("35"), previous_rsi=Decimal("25"))
    now = datetime(2026, 1, 2)

    assert build_scenario("BTCUSDT", signal, klines, now) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_scenario_builder.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.scenario_builder'`)

- [ ] **Step 3: Write minimal implementation**

`src/scenario_builder.py`:
```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from src.indicators import average_true_range
from src.scenario_signal import RSI_OVERBOUGHT, RSI_OVERSOLD, SignalResult
from src.support_resistance import find_swing_points, nearest_resistance, nearest_support

MIN_EXPIRY_HOURS = 6
MAX_EXPIRY_HOURS = 168
ATR_PERIOD = 20
SWING_LOOKBACK_K = 3
RISK_REWARD_CAP = Decimal("3")


@dataclass
class ScenarioDraft:
    symbol: str
    direction: str
    entry_price: Decimal
    target_price: Decimal
    stop_price: Decimal
    expected_return_pct: Decimal
    confidence_score: Decimal
    created_at: datetime
    expires_at: datetime


def build_scenario(symbol: str, signal: SignalResult, klines: list, now: datetime):
    swing_highs, swing_lows = find_swing_points(klines, k=SWING_LOOKBACK_K)
    entry = signal.entry_price

    if signal.direction == "long":
        target = nearest_resistance(swing_highs, entry)
        stop = nearest_support(swing_lows, entry)
    else:
        target = nearest_support(swing_lows, entry)
        stop = nearest_resistance(swing_highs, entry)

    if target is None or stop is None:
        return None

    reward = abs(target - entry)
    risk = abs(entry - stop)
    if risk == 0:
        return None

    if signal.direction == "long":
        expected_return_pct = (target - entry) / entry
        strength = min(max((RSI_OVERSOLD - signal.previous_rsi) / RSI_OVERSOLD, Decimal("0")), Decimal("1"))
    else:
        expected_return_pct = (entry - target) / entry
        strength = min(max((signal.previous_rsi - RSI_OVERBOUGHT) / (Decimal("100") - RSI_OVERBOUGHT), Decimal("0")), Decimal("1"))

    risk_reward_score = min(reward / risk / RISK_REWARD_CAP, Decimal("1"))
    confidence_score = (Decimal("0.5") * risk_reward_score) + (Decimal("0.5") * strength)

    atr = average_true_range(klines, ATR_PERIOD)
    if atr > 0:
        hours = reward / atr
    else:
        hours = Decimal(MAX_EXPIRY_HOURS)
    hours = min(max(hours, Decimal(MIN_EXPIRY_HOURS)), Decimal(MAX_EXPIRY_HOURS))
    expires_at = now + timedelta(hours=float(hours))

    return ScenarioDraft(
        symbol=symbol,
        direction=signal.direction,
        entry_price=entry,
        target_price=target,
        stop_price=stop,
        expected_return_pct=expected_return_pct,
        confidence_score=confidence_score,
        created_at=now,
        expires_at=expires_at,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_scenario_builder.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/scenario_builder.py tests/test_scenario_builder.py
git commit -m "feat: add scenario builder (entry/target/stop/confidence/expiry)"
```

---

### Task 6: Scenario storage (dedup check + insert)

**Files:**
- Create: `src/scenario_storage.py`
- Test: `tests/test_scenario_storage.py`

**Interfaces:**
- Consumes: `db.models.Scenario` (Task 1); `scenario_builder.ScenarioDraft` (Task 5, structurally — this module only reads its attributes, no import needed since Python is duck-typed here, but for clarity the function signature names the type)
- Produces: `scenario_storage.has_pending_scenario(session, symbol: str, direction: str) -> bool`, `scenario_storage.insert_scenario(session, draft) -> None`

- [ ] **Step 1: Write the failing tests**

`tests/test_scenario_storage.py`:
```python
from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import Scenario
from src.scenario_builder import ScenarioDraft
from src.scenario_storage import has_pending_scenario, insert_scenario


def test_has_pending_scenario_false_when_none_exists(db_session):
    assert has_pending_scenario(db_session, "BTCUSDT", "long") is False


def test_has_pending_scenario_true_when_one_exists(db_session):
    now = datetime(2026, 1, 1)
    db_session.add(Scenario(
        symbol="BTCUSDT", direction="long",
        entry_price=Decimal("100"), target_price=Decimal("110"), stop_price=Decimal("95"),
        expected_return_pct=Decimal("0.1"), confidence_score=Decimal("0.6"),
        created_at=now, expires_at=now + timedelta(hours=24), status="pending",
    ))
    db_session.commit()
    assert has_pending_scenario(db_session, "BTCUSDT", "long") is True
    assert has_pending_scenario(db_session, "BTCUSDT", "short") is False


def test_insert_scenario_persists_a_pending_row(db_session):
    now = datetime(2026, 1, 1)
    draft = ScenarioDraft(
        symbol="ETHUSDT", direction="short",
        entry_price=Decimal("3000"), target_price=Decimal("2900"), stop_price=Decimal("3050"),
        expected_return_pct=Decimal("0.033"), confidence_score=Decimal("0.4"),
        created_at=now, expires_at=now + timedelta(hours=12),
    )
    insert_scenario(db_session, draft)
    row = db_session.query(Scenario).filter(Scenario.symbol == "ETHUSDT").first()
    assert row is not None
    assert row.status == "pending"
    assert row.direction == "short"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_scenario_storage.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.scenario_storage'`)

- [ ] **Step 3: Write minimal implementation**

`src/scenario_storage.py`:
```python
from __future__ import annotations

from src.db.models import Scenario


def has_pending_scenario(session, symbol: str, direction: str) -> bool:
    existing = (
        session.query(Scenario)
        .filter(Scenario.symbol == symbol, Scenario.direction == direction, Scenario.status == "pending")
        .first()
    )
    return existing is not None


def insert_scenario(session, draft) -> None:
    session.add(Scenario(
        symbol=draft.symbol,
        direction=draft.direction,
        entry_price=draft.entry_price,
        target_price=draft.target_price,
        stop_price=draft.stop_price,
        expected_return_pct=draft.expected_return_pct,
        confidence_score=draft.confidence_score,
        created_at=draft.created_at,
        expires_at=draft.expires_at,
        status="pending",
    ))
    session.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_scenario_storage.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/scenario_storage.py tests/test_scenario_storage.py
git commit -m "feat: add scenario dedup check and insert"
```

---

### Task 7: Scenario Runner (per-symbol orchestration)

**Files:**
- Create: `src/scenario_runner.py`
- Test: `tests/test_scenario_runner.py`

**Interfaces:**
- Consumes: `db.models.Kline` (Subsystem A); `scenario_signal.evaluate_signal`, `MIN_CANDLES` (Task 4); `scenario_builder.build_scenario` (Task 5); `scenario_storage.has_pending_scenario`, `insert_scenario` (Task 6); `timeutil.utc_now` (Subsystem A)
- Produces:
  - `scenario_runner.ScenarioRunResult(scanned: int, generated: int, skipped: int, failed: int)` (dataclass)
  - `scenario_runner.process_symbol_scenario(session, symbol: str, timeframe: str = "1h") -> str` — returns `"generated"`, `"skipped"`, or raises (caller handles isolation, matching `kline_fetcher.process_symbol_timeframe`'s pattern of doing the isolation/rollback one level up in the caller — see `run_scenario_generation` below and `scheduler.run_timeframe_job`'s existing per-symbol try/except for the established pattern).
  - `scenario_runner.run_scenario_generation(session, symbols: list) -> ScenarioRunResult`

- [ ] **Step 1: Write the failing tests**

`tests/test_scenario_runner.py`:
```python
from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import Kline, Scenario
from src.scenario_runner import run_scenario_generation, process_symbol_scenario
from src.scenario_signal import MIN_CANDLES


def _insert_flat_klines(db_session, symbol, count, price=Decimal("100")):
    for i in range(count):
        db_session.add(Kline(
            symbol=symbol, timeframe="1h",
            open_time=datetime(2026, 1, 1) + timedelta(hours=i),
            open=price, high=price, low=price, close=price, volume=Decimal("1000"), flagged=False,
        ))
    db_session.commit()


def test_process_symbol_scenario_skips_with_insufficient_data(db_session):
    _insert_flat_klines(db_session, "BTCUSDT", MIN_CANDLES - 1)
    outcome = process_symbol_scenario(db_session, "BTCUSDT")
    assert outcome == "skipped"


def test_process_symbol_scenario_skips_when_no_signal(db_session):
    _insert_flat_klines(db_session, "BTCUSDT", MIN_CANDLES)
    outcome = process_symbol_scenario(db_session, "BTCUSDT")
    assert outcome == "skipped"
    assert db_session.query(Scenario).count() == 0


def test_run_scenario_generation_isolates_symbol_failures(db_session, monkeypatch):
    _insert_flat_klines(db_session, "BTCUSDT", MIN_CANDLES)
    _insert_flat_klines(db_session, "ETHUSDT", MIN_CANDLES)

    import src.scenario_runner as scenario_runner_module

    def fake_process(session, symbol, timeframe="1h"):
        if symbol == "BTCUSDT":
            raise RuntimeError("boom")
        return "skipped"

    monkeypatch.setattr(scenario_runner_module, "process_symbol_scenario", fake_process)

    result = run_scenario_generation(db_session, ["BTCUSDT", "ETHUSDT"])

    assert result.scanned == 2
    assert result.failed == 1
    assert result.skipped == 1
    assert result.generated == 0
    # The session must still be usable after the failure (rolled back, not poisoned).
    assert db_session.query(Kline).filter(Kline.symbol == "ETHUSDT").count() == MIN_CANDLES


def test_run_scenario_generation_counts_generated(db_session, monkeypatch):
    _insert_flat_klines(db_session, "BTCUSDT", MIN_CANDLES)

    import src.scenario_runner as scenario_runner_module

    monkeypatch.setattr(scenario_runner_module, "process_symbol_scenario", lambda session, symbol, timeframe="1h": "generated")

    result = run_scenario_generation(db_session, ["BTCUSDT"])

    assert result.scanned == 1
    assert result.generated == 1
    assert result.skipped == 0
    assert result.failed == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_scenario_runner.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.scenario_runner'`)

- [ ] **Step 3: Write minimal implementation**

`src/scenario_runner.py`:
```python
from __future__ import annotations

import logging
from dataclasses import dataclass

from src.db.models import Kline
from src.scenario_builder import build_scenario
from src.scenario_signal import MIN_CANDLES, evaluate_signal
from src.scenario_storage import has_pending_scenario, insert_scenario
from src.timeutil import utc_now

logger = logging.getLogger("scenario_runner")


@dataclass
class ScenarioRunResult:
    scanned: int
    generated: int
    skipped: int
    failed: int


def _load_recent_klines(session, symbol: str, timeframe: str, limit: int) -> list:
    rows = (
        session.query(Kline)
        .filter(Kline.symbol == symbol, Kline.timeframe == timeframe)
        .order_by(Kline.open_time.desc())
        .limit(limit)
        .all()
    )
    rows.reverse()
    return [
        {
            "open_time": row.open_time, "open": row.open, "high": row.high,
            "low": row.low, "close": row.close, "volume": row.volume,
        }
        for row in rows
    ]


def process_symbol_scenario(session, symbol: str, timeframe: str = "1h") -> str:
    klines = _load_recent_klines(session, symbol, timeframe, MIN_CANDLES)
    if len(klines) < MIN_CANDLES:
        return "skipped"

    signal = evaluate_signal(klines)
    if signal is None:
        return "skipped"

    if has_pending_scenario(session, symbol, signal.direction):
        return "skipped"

    draft = build_scenario(symbol, signal, klines, utc_now())
    if draft is None:
        return "skipped"

    insert_scenario(session, draft)
    return "generated"


def run_scenario_generation(session, symbols: list) -> ScenarioRunResult:
    scanned = 0
    generated = 0
    skipped = 0
    failed = 0
    for symbol in symbols:
        scanned += 1
        try:
            outcome = process_symbol_scenario(session, symbol)
        except Exception:
            session.rollback()
            logger.exception("Scenario generation failed for %s", symbol)
            failed += 1
            continue
        if outcome == "generated":
            generated += 1
        else:
            skipped += 1
    logger.info(
        "Scenario generation finished: %d scanned, %d generated, %d skipped, %d failed",
        scanned, generated, skipped, failed,
    )
    return ScenarioRunResult(scanned=scanned, generated=generated, skipped=skipped, failed=failed)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_scenario_runner.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/scenario_runner.py tests/test_scenario_runner.py
git commit -m "feat: add per-symbol scenario generation orchestration"
```

---

### Task 8: Wire scenario generation into the hourly scheduler job

**Files:**
- Modify: `src/scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `scenario_runner.run_scenario_generation`, `ScenarioRunResult` (Task 7)
- Produces: no new public interface — `run_timeframe_job` gains a side effect (scenario generation for `timeframe == "1h"`) and its final log line gains a `scenarios generated` count on that path.

The current `src/scheduler.py` (reproduced in full so the diff is unambiguous) is:

```python
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from src.backfill import run_gap_backfill
from src.db.models import Symbol
from src.fetch_log import record_run
from src.integrity import floor_to_timeframe
from src.kline_fetcher import process_symbol_timeframe
from src.storage import get_kline_time_bounds
from src.symbol_registry import refresh_symbols
from src.timeutil import DEFAULT_BACKFILL_DAYS, to_epoch_ms, utc_now

logger = logging.getLogger("scheduler")

GAP_LOOKBACK_DAYS = 30


def get_resume_point(session, symbol: str, timeframe: str, now: datetime = None) -> datetime:
    ...  # unchanged


def repair_recent_gaps(session, binance_client, symbol: str, timeframe: str, now: datetime = None) -> int:
    ...  # unchanged


def run_timeframe_job(session_factory, binance_client, timeframe: str, now: datetime = None) -> None:
    session = session_factory()
    try:
        end = now if now is not None else utc_now()
        symbols = [row.symbol for row in session.query(Symbol).filter(Symbol.is_active == True).all()]  # noqa: E712
        succeeded = 0
        failed = 0
        gaps_filled = 0
        for symbol in symbols:
            symbol_ok = False
            try:
                start = get_resume_point(session, symbol, timeframe, now=end)
                started_at = utc_now()
                result = process_symbol_timeframe(
                    session, binance_client, symbol, timeframe,
                    start_ms=to_epoch_ms(start),
                    end_ms=to_epoch_ms(end),
                )
                if result.error:
                    logger.error("Fetch failed for %s %s: %s", symbol, timeframe, result.error)
                record_run(
                    session, symbol, timeframe,
                    status="error" if result.error else "success",
                    started_at=started_at, finished_at=utc_now(),
                    error_message=result.error,
                )
                if not result.error:
                    gaps_filled += repair_recent_gaps(session, binance_client, symbol, timeframe, now=end)
                    symbol_ok = True
            except Exception:
                logger.exception("Unhandled error processing %s %s", symbol, timeframe)
                session.rollback()

            if symbol_ok:
                succeeded += 1
            else:
                failed += 1
        logger.info(
            "%s job finished: %d symbols succeeded, %d failed, %d gaps filled",
            timeframe, succeeded, failed, gaps_filled,
        )
    finally:
        session.close()
```

(`repair_recent_gaps` and `get_resume_point`'s bodies are unchanged by this task — omitted above for brevity, but do not touch them.)

- [ ] **Step 1: Write the failing test**

At the top of `tests/test_scheduler.py`, add one import line alongside the existing `from src.scheduler import (...)` block — the file already imports `Symbol`, `Kline`, `Decimal`, `datetime`, `timedelta`, and has a reusable `_FakeBinanceClient` (with a `get_klines(self, symbol, timeframe, start_ms, end_ms)` that returns `[]`) and a `_kline(symbol, timeframe, open_time, close="1")` helper — reuse all of these rather than redefining them:

```python
import src.scheduler as scheduler_module
```

Then add these two tests to the end of the file:

```python
def test_run_timeframe_job_generates_scenarios_after_1h_fetch(db_session, monkeypatch):
    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True))
    for i in range(100):
        db_session.add(_kline("BTCUSDT", "1h", datetime(2026, 1, 1) + timedelta(hours=i)))
    db_session.commit()

    calls = []

    def fake_run_scenario_generation(session, symbols):
        from src.scenario_runner import ScenarioRunResult
        calls.append(list(symbols))
        return ScenarioRunResult(scanned=len(symbols), generated=0, skipped=len(symbols), failed=0)

    monkeypatch.setattr(scheduler_module, "run_scenario_generation", fake_run_scenario_generation)

    run_timeframe_job(session_factory=lambda: db_session, binance_client=_FakeBinanceClient(), timeframe="1h")

    assert calls == [["BTCUSDT"]]


def test_run_timeframe_job_does_not_generate_scenarios_for_1d(db_session, monkeypatch):
    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True))
    db_session.commit()

    calls = []
    monkeypatch.setattr(
        scheduler_module, "run_scenario_generation",
        lambda session, symbols: calls.append(list(symbols)),
    )

    run_timeframe_job(session_factory=lambda: db_session, binance_client=_FakeBinanceClient(), timeframe="1d")

    assert calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_scheduler.py -v -k scenario`
Expected: FAIL (`AttributeError: <module 'src.scheduler'> does not have the attribute 'run_scenario_generation'` from `monkeypatch.setattr`)

- [ ] **Step 3: Write minimal implementation**

In `src/scheduler.py`:

1. Add this import alongside the existing `from src.symbol_registry import refresh_symbols` line:

```python
from src.scenario_runner import run_scenario_generation
```

2. Replace the body of `run_timeframe_job` from the `for symbol in symbols:` loop's closing (the line `failed += 1`) through the existing final `logger.info(...)` call with:

```python
            if symbol_ok:
                succeeded += 1
            else:
                failed += 1

        scenario_result = None
        if timeframe == "1h":
            scenario_result = run_scenario_generation(session, symbols)

        if scenario_result is not None:
            logger.info(
                "%s job finished: %d symbols succeeded, %d failed, %d gaps filled, %d scenarios generated",
                timeframe, succeeded, failed, gaps_filled, scenario_result.generated,
            )
        else:
            logger.info(
                "%s job finished: %d symbols succeeded, %d failed, %d gaps filled",
                timeframe, succeeded, failed, gaps_filled,
            )
```

Everything else in `src/scheduler.py` (imports you didn't add, `get_resume_point`, `repair_recent_gaps`, `run_symbol_refresh_job`, `build_scheduler`) stays exactly as it is.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_scheduler.py -v`
Expected: PASS (all scheduler tests, including the 2 new ones)

- [ ] **Step 5: Run the full test suite**

Run: `python3 -m pytest -v`
Expected: PASS (all tests across Subsystem A and this plan)

- [ ] **Step 6: Commit**

```bash
git add src/scheduler.py tests/test_scheduler.py
git commit -m "feat: wire scenario generation into the hourly scheduler job"
```

---

### Task 9: README update

**Files:**
- Modify: `README.md`

**Interfaces:**
- None (documentation only)

- [ ] **Step 1: Update the README**

Read the current `README.md` first, then add a new section after the existing "Çalıştırma" section (before "Test"), describing the scenario engine in the same terse style as the rest of the file:

```markdown
## Senaryo Üretimi

Her saatlik (1h) mum güncellemesi tamamlandıktan hemen sonra, tüm aktif semboller için
RSI + EMA(9/21) kesişimi + hacim spike'ı sinyalleri kontrol edilir. Üçü birden aynı yönde
tetiklenirse, en yakın destek/direnç seviyelerinden giriş/hedef/stop hesaplanıp `scenarios`
tablosuna `status="pending"` olarak yazılır. Bir sembol için zaten `pending` bir senaryo
varsa, süresi dolana veya güncellenene kadar yeni bir tane üretilmez.
```

Then update the existing "Test" section's line about what the tests exercise, if it names specific behavior that's now incomplete (e.g. if it says "1h/1d mum verisi" only, extend it to mention scenario generation) — read the current wording first and adjust minimally rather than rewriting the section.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document scenario generation"
```

---

## Post-Plan Notes

- This plan covers Subsystem B only (scenario generation), per `docs/superpowers/specs/2026-08-21-scenario-engine-design.md`. Subsystem C (confidence/learning loop, which will read and update `scenarios.status`) and Subsystem D (paper testing) are separate specs/plans, to be brainstormed after this one is implemented and verified.
- Manual verification after Task 9 (not automated, requires a live Postgres + internet, and enough real market history accumulated by Subsystem A): run `python -m src.main`, wait for at least one hourly job to complete, and confirm the `scenarios` table populates when a real signal fires.
- An empty `scenarios` table after a run is **not** self-evidently fine. "Signals are rare, so zero is expected" is unfalsifiable and will hide a structurally broken gate indefinitely (it did: the runner read the still-forming candle, making the volume-spike gate mathematically unreachable, and nine per-task reviews plus a green suite reported nothing). Verify it instead: each run should log at INFO how many symbols passed each gate in turn — candle freshness (fresh, contiguous, unflagged window), sufficient history (≥ `MIN_CANDLES` closed candles), and signal conditions (RSI cross + EMA cross + volume spike) — so an operator can tell "no setups this hour" (symbols reach the signal gate, none trigger) from "a gate is broken" (a gate drops every symbol, every hour). A gate that has never once passed a symbol across many runs is a bug until proven otherwise, and the fastest way to prove it is to replay a stored window that *should* trigger through `process_symbol_scenario`.
