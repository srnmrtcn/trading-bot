# BTC Rejim Filtresi Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sinyal üretimini (Subsystem B), BTC'nin 1d EMA(9/21) trend yönüyle uyuşmayan senaryoları reddedecek şekilde filtrelemek.

**Architecture:** Yeni bağımsız `src/btc_regime.py` modülü BTCUSDT'nin son kapanmış 1d mumlarından `"up"`/`"down"`/`None` döner. `scenario_runner.py` bunu her saatlik çalıştırmada bir kez çağırıp tüm sembollere aynı değeri geçirir; `process_symbol_scenario` sinyal yönü regime ile uyuşmazsa (veya regime `None` ise) senaryoyu üretmeden `"skipped"` döner.

**Tech Stack:** Python, SQLAlchemy (mevcut `Kline` modeli), pytest + `sqlite:///:memory:` (mevcut test deseni).

**Spec:** [docs/superpowers/specs/2026-08-25-btc-regime-filter-design.md](../specs/2026-08-25-btc-regime-filter-design.md)

## Global Constraints

- Rejim göstergesi: BTCUSDT, 1d zaman dilimi, EMA(9)/EMA(21) — EMA9 > EMA21 ⇒ `"up"`, aksi halde ⇒ `"down"`.
- Filtre etkisi: sert engelleme — ters yönlü sinyaller reddedilir, confidence skoru değişmez.
- BTC istisnası yok — BTCUSDT'nin kendi sinyalleri de aynı filtreye tabi.
- Rejim `None` (belirsiz) ise **hiçbir** sembol için senaryo üretilmez, o çalıştırmada.
- `scheduler.py`'de değişiklik gerekmez — entegrasyon `run_scenario_generation`'ın içine gizli.

---

### Task 1: `compute_btc_regime` fonksiyonu

**Files:**
- Create: `src/btc_regime.py`
- Test: `tests/test_btc_regime.py`

**Interfaces:**
- Produces: `compute_btc_regime(session, now: datetime = None) -> str | None` — `"up"`, `"down"`, veya `None`. `BTC_SYMBOL = "BTCUSDT"`, `REGIME_TIMEFRAME = "1d"`, `REGIME_LOOKBACK = 22` (Task 3'ün test fixture'ı bu sabiti import edecek).

- [ ] **Step 1: Write the failing tests**

`tests/test_btc_regime.py`:

```python
from datetime import datetime, timedelta
from decimal import Decimal

from src.btc_regime import REGIME_LOOKBACK, compute_btc_regime
from src.db.models import Kline

# floor_to_timeframe("1d") zeroes out time-of-day, so NOW's boundary is
# midnight of the same calendar day.
NOW = datetime(2026, 8, 21, 5, 0)
DAY_BOUNDARY = datetime(2026, 8, 21)


def _daily_kline(open_time, close, flagged=False):
    return Kline(
        symbol="BTCUSDT", timeframe="1d", open_time=open_time,
        open=close, high=close, low=close, close=close, volume=Decimal("1000"),
        flagged=flagged,
    )


def _seed_daily_closes(db_session, closes, end_boundary=DAY_BOUNDARY, flag_index=None, drop_index=None):
    """`len(closes)` contiguous daily candles, the last one closing right
    before `end_boundary`."""
    rows = []
    for i, close in enumerate(closes):
        open_time = end_boundary - timedelta(days=len(closes) - i)
        rows.append(_daily_kline(open_time, close))
    if flag_index is not None:
        rows[flag_index].flagged = True
    if drop_index is not None:
        del rows[drop_index]
    for row in rows:
        db_session.add(row)
    db_session.commit()


def test_compute_btc_regime_returns_up_for_a_rising_series(db_session):
    closes = [Decimal(100 + i) for i in range(REGIME_LOOKBACK)]
    _seed_daily_closes(db_session, closes)

    assert compute_btc_regime(db_session, now=NOW) == "up"


def test_compute_btc_regime_returns_down_for_a_falling_series(db_session):
    closes = [Decimal(200 - i) for i in range(REGIME_LOOKBACK)]
    _seed_daily_closes(db_session, closes)

    assert compute_btc_regime(db_session, now=NOW) == "down"


def test_compute_btc_regime_returns_none_with_insufficient_candles(db_session):
    closes = [Decimal(100 + i) for i in range(REGIME_LOOKBACK - 1)]
    _seed_daily_closes(db_session, closes)

    assert compute_btc_regime(db_session, now=NOW) is None


def test_compute_btc_regime_returns_none_for_a_non_contiguous_window(db_session):
    # One extra candle before the drop so REGIME_LOOKBACK rows still remain
    # afterwards -- otherwise this would hit the "insufficient candles"
    # branch instead of the contiguity check it's meant to exercise.
    closes = [Decimal(100 + i) for i in range(REGIME_LOOKBACK + 1)]
    _seed_daily_closes(db_session, closes, drop_index=10)

    assert compute_btc_regime(db_session, now=NOW) is None


def test_compute_btc_regime_returns_none_for_stale_data(db_session):
    closes = [Decimal(100 + i) for i in range(REGIME_LOOKBACK)]
    # The newest candle ends 3 days before the boundary instead of 1.
    _seed_daily_closes(db_session, closes, end_boundary=DAY_BOUNDARY - timedelta(days=2))

    assert compute_btc_regime(db_session, now=NOW) is None


def test_compute_btc_regime_returns_none_for_a_flagged_candle(db_session):
    closes = [Decimal(100 + i) for i in range(REGIME_LOOKBACK)]
    _seed_daily_closes(db_session, closes, flag_index=REGIME_LOOKBACK - 1)

    assert compute_btc_regime(db_session, now=NOW) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_btc_regime.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.btc_regime'`

- [ ] **Step 3: Implement `src/btc_regime.py`**

```python
from __future__ import annotations

from datetime import datetime

from src.db.models import Kline
from src.indicators import compute_ema
from src.integrity import TIMEFRAME_DELTAS, floor_to_timeframe
from src.timeutil import utc_now

BTC_SYMBOL = "BTCUSDT"
REGIME_TIMEFRAME = "1d"
EMA_FAST_PERIOD = 9
EMA_SLOW_PERIOD = 21
# One more than EMA_SLOW_PERIOD so compute_ema produces a real EMA21 step
# (not just the seed SMA) at the last index.
REGIME_LOOKBACK = EMA_SLOW_PERIOD + 1


def _load_closed_btc_klines(session, before: datetime) -> list:
    rows = (
        session.query(Kline)
        .filter(
            Kline.symbol == BTC_SYMBOL,
            Kline.timeframe == REGIME_TIMEFRAME,
            Kline.open_time < before,
        )
        .order_by(Kline.open_time.desc())
        .limit(REGIME_LOOKBACK)
        .all()
    )
    rows.reverse()
    return rows


def compute_btc_regime(session, now: datetime = None):
    """BTC's 1d trend direction: "up" (EMA9 > EMA21), "down", or None if it
    can't be determined (insufficient, non-contiguous, stale, or
    anomaly-flagged data). None must block every symbol's scenario
    generation that run, not just BTC's — see the design spec.
    """
    now = now if now is not None else utc_now()
    current_boundary = floor_to_timeframe(now, REGIME_TIMEFRAME)
    klines = _load_closed_btc_klines(session, current_boundary)

    if len(klines) < REGIME_LOOKBACK:
        return None

    step = TIMEFRAME_DELTAS[REGIME_TIMEFRAME]
    newest = klines[-1].open_time
    if newest < current_boundary - step:
        return None
    if newest - klines[0].open_time != (len(klines) - 1) * step:
        return None
    if any(row.flagged for row in klines):
        return None

    closes = [row.close for row in klines]
    ema_fast = compute_ema(closes, EMA_FAST_PERIOD)
    ema_slow = compute_ema(closes, EMA_SLOW_PERIOD)
    if ema_fast[-1] is None or ema_slow[-1] is None:
        return None

    return "up" if ema_fast[-1] > ema_slow[-1] else "down"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_btc_regime.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/btc_regime.py tests/test_btc_regime.py
git commit -m "feat: add BTC 1d EMA(9/21) regime detection"
```

---

### Task 2: Gate `process_symbol_scenario` on regime

**Files:**
- Modify: `src/scenario_runner.py:96-118`
- Modify: `tests/test_scenario_runner.py` (update existing calls, add gating tests)

**Interfaces:**
- Consumes: `compute_btc_regime` is NOT called here — the caller (Task 3) resolves `regime` and passes it in.
- Produces: `process_symbol_scenario(session, symbol: str, regime: str, timeframe: str = "1h", now: datetime = None) -> str` — signature changes, `regime` becomes a required positional argument inserted after `symbol`.

- [ ] **Step 1: Update the existing calls to pass `regime` and add the new failing gating tests**

In `tests/test_scenario_runner.py`, every existing `process_symbol_scenario(db_session, "BTCUSDT", now=NOW)` call adds `regime="up"` (the fixtures in this file all produce a `"long"` signal, so `"up"` keeps their current pass/fail outcome unchanged). Apply this to all 10 call sites at lines 101, 107, 117, 138, 150, 151, 167, 182, 191, 199, 208 — e.g.:

```python
def test_process_symbol_scenario_skips_with_insufficient_data(db_session):
    _insert_flat_klines(db_session, "BTCUSDT", MIN_CANDLES - 1)
    outcome = process_symbol_scenario(db_session, "BTCUSDT", regime="up", now=NOW)
    assert outcome == "skipped"
```

(repeat the same `regime="up"` insertion for the other 9 calls, keeping every other line unchanged).

Then append the new gating tests at the end of the file:

```python
def test_process_symbol_scenario_skips_long_signal_when_regime_is_down(db_session):
    # BTCUSDT itself is filtered too -- no exemption for the regime's own symbol.
    _seed_signal_klines(db_session)

    assert process_symbol_scenario(db_session, "BTCUSDT", regime="down", now=NOW) == "skipped"
    assert db_session.query(Scenario).count() == 0


def test_process_symbol_scenario_skips_any_signal_when_regime_is_none(db_session):
    _seed_signal_klines(db_session)

    assert process_symbol_scenario(db_session, "BTCUSDT", regime=None, now=NOW) == "skipped"
    assert db_session.query(Scenario).count() == 0


def test_process_symbol_scenario_skips_short_signal_when_regime_is_up(db_session, monkeypatch):
    import src.scenario_runner as scenario_runner_module
    from src.scenario_signal import SignalResult

    _insert_flat_klines(db_session, "BTCUSDT", MIN_CANDLES)
    monkeypatch.setattr(
        scenario_runner_module, "evaluate_signal",
        lambda klines: SignalResult(
            direction="short", entry_price=Decimal("100"),
            rsi=Decimal("65"), previous_rsi=Decimal("72"),
        ),
    )

    assert process_symbol_scenario(db_session, "BTCUSDT", regime="up", now=NOW) == "skipped"
    assert db_session.query(Scenario).count() == 0


def test_process_symbol_scenario_accepts_short_signal_when_regime_is_down(db_session, monkeypatch):
    import src.scenario_runner as scenario_runner_module
    from src.scenario_builder import ScenarioDraft
    from src.scenario_signal import SignalResult

    _insert_flat_klines(db_session, "BTCUSDT", MIN_CANDLES)
    monkeypatch.setattr(
        scenario_runner_module, "evaluate_signal",
        lambda klines: SignalResult(
            direction="short", entry_price=Decimal("100"),
            rsi=Decimal("65"), previous_rsi=Decimal("72"),
        ),
    )
    draft = ScenarioDraft(
        symbol="BTCUSDT", direction="short",
        entry_price=Decimal("100"), target_price=Decimal("90"), stop_price=Decimal("110"),
        expected_return_pct=Decimal("0.1"), confidence_score=Decimal("0.7"),
        created_at=NOW, expires_at=NOW + timedelta(hours=24),
    )
    monkeypatch.setattr(scenario_runner_module, "build_scenario", lambda symbol, signal, klines, now: draft)

    assert process_symbol_scenario(db_session, "BTCUSDT", regime="down", now=NOW) == "generated"
    assert db_session.query(Scenario).count() == 1
```

- [ ] **Step 2: Run the test file to verify the new/changed tests fail correctly**

Run: `pytest tests/test_scenario_runner.py -v`
Expected: the 10 updated calls fail with `TypeError: process_symbol_scenario() missing 1 required positional argument: 'regime'` (signature not changed yet) and/or the 4 new tests fail the same way.

- [ ] **Step 3: Add the regime gate to `process_symbol_scenario`**

In `src/scenario_runner.py`, change line 96 and insert the gate after line 108:

```python
def process_symbol_scenario(session, symbol: str, regime: str, timeframe: str = "1h", now: datetime = None) -> str:
    now = now if now is not None else utc_now()
    current_boundary = floor_to_timeframe(now, timeframe)
    klines = _load_recent_klines(session, symbol, timeframe, SCENARIO_LOOKBACK, current_boundary)

    rejection = _window_rejection(klines, timeframe, current_boundary)
    if rejection is not None:
        logger.debug("Skipping %s %s: %s", symbol, timeframe, rejection)
        return "skipped"

    signal = evaluate_signal(klines)
    if signal is None:
        return "skipped"

    if regime is None:
        return "skipped"
    if signal.direction == "long" and regime != "up":
        return "skipped"
    if signal.direction == "short" and regime != "down":
        return "skipped"

    if has_pending_scenario(session, symbol, signal.direction, now):
        return "skipped"

    draft = build_scenario(symbol, signal, klines, now)
    if draft is None:
        return "skipped"

    insert_scenario(session, draft)
    return "generated"
```

(Only the `def` line and the new `if regime is None: ...` block are additions; the rest of the function body is unchanged.)

- [ ] **Step 4: Run the test file to verify it passes**

Run: `pytest tests/test_scenario_runner.py -v`
Expected: FAIL only on the `test_run_scenario_generation_*` tests (Task 3 fixes those) — every `test_process_symbol_scenario_*` test passes.

- [ ] **Step 5: Commit**

```bash
git add src/scenario_runner.py tests/test_scenario_runner.py
git commit -m "feat: gate process_symbol_scenario on BTC regime direction"
```

---

### Task 3: Compute regime once per `run_scenario_generation` run

**Files:**
- Modify: `src/scenario_runner.py:121-129`
- Modify: `tests/test_scenario_runner.py` (fix the two mocked `process_symbol_scenario` tests, seed BTC regime data for the end-to-end test, add two new tests)

**Interfaces:**
- Consumes: `compute_btc_regime(session, now: datetime = None) -> str | None` from Task 1; `process_symbol_scenario(session, symbol, regime, timeframe="1h", now=None)` from Task 2.
- Produces: `run_scenario_generation(session, symbols: list, now: datetime = None) -> ScenarioRunResult` — `now` is a new optional parameter.

- [ ] **Step 1: Update the failing/broken tests**

In `tests/test_scenario_runner.py`:

1. Add the import at the top of the file: `from src.btc_regime import REGIME_LOOKBACK`.

2. Add a helper (near the other `_seed_*` helpers) to seed a BTC daily regime:

```python
def _seed_btc_regime_klines(db_session, day_boundary, regime="up"):
    for i in range(REGIME_LOOKBACK):
        open_time = day_boundary - timedelta(days=REGIME_LOOKBACK - i)
        price = Decimal(100 + i) if regime == "up" else Decimal(200 - i)
        db_session.add(Kline(
            symbol="BTCUSDT", timeframe="1d", open_time=open_time,
            open=price, high=price, low=price, close=price, volume=Decimal("1000"),
            flagged=False,
        ))
    db_session.commit()
```

3. Fix `test_run_scenario_generation_persists_scenarios_end_to_end` (currently line 212) by seeding a BTC `"up"` regime before the call — `floor_to_timeframe(NOW, "1d")` is midnight of `NOW`'s calendar day, i.e. `datetime(2026, 8, 21)` since `NOW = BOUNDARY + timedelta(minutes=30)` and `BOUNDARY = datetime(2026, 8, 21, 12, 0)`:

```python
def test_run_scenario_generation_persists_scenarios_end_to_end(db_session, monkeypatch):
    import src.scenario_runner as scenario_runner_module

    _seed_signal_klines(db_session, symbol="BTCUSDT")
    _insert_flat_klines(db_session, "ETHUSDT", MIN_CANDLES)
    _seed_btc_regime_klines(db_session, day_boundary=datetime(2026, 8, 21), regime="up")
    monkeypatch.setattr(scenario_runner_module, "utc_now", lambda: NOW)

    result = run_scenario_generation(db_session, ["BTCUSDT", "ETHUSDT"])

    assert result.scanned == 2
    assert result.generated == 1
    assert result.skipped == 1
    assert result.failed == 0
    assert [row.symbol for row in db_session.query(Scenario).all()] == ["BTCUSDT"]
```

4. Fix the two mocked-`process_symbol_scenario` tests to accept the new `regime` argument (`compute_btc_regime` runs for real in both and returns `None` since no BTC data is seeded — the fakes ignore it, so no BTC seeding is needed here):

```python
def test_run_scenario_generation_isolates_symbol_failures(db_session, monkeypatch):
    _insert_flat_klines(db_session, "BTCUSDT", MIN_CANDLES)
    _insert_flat_klines(db_session, "ETHUSDT", MIN_CANDLES)

    import src.scenario_runner as scenario_runner_module

    def fake_process(session, symbol, regime, timeframe="1h", now=None):
        if symbol == "BTCUSDT":
            raise RuntimeError("boom")
        return "skipped"

    monkeypatch.setattr(scenario_runner_module, "process_symbol_scenario", fake_process)

    result = run_scenario_generation(db_session, ["BTCUSDT", "ETHUSDT"])

    assert result.scanned == 2
    assert result.failed == 1
    assert result.skipped == 1
    assert result.generated == 0
    assert db_session.query(Kline).filter(Kline.symbol == "ETHUSDT").count() == MIN_CANDLES


def test_run_scenario_generation_counts_generated(db_session, monkeypatch):
    _insert_flat_klines(db_session, "BTCUSDT", MIN_CANDLES)

    import src.scenario_runner as scenario_runner_module

    monkeypatch.setattr(
        scenario_runner_module, "process_symbol_scenario",
        lambda session, symbol, regime, timeframe="1h", now=None: "generated",
    )

    result = run_scenario_generation(db_session, ["BTCUSDT"])

    assert result.scanned == 1
    assert result.generated == 1
    assert result.skipped == 0
    assert result.failed == 0
```

5. Add two new tests at the end of the file:

```python
def test_run_scenario_generation_computes_regime_once_for_all_symbols(db_session, monkeypatch):
    import src.scenario_runner as scenario_runner_module

    _insert_flat_klines(db_session, "BTCUSDT", MIN_CANDLES)
    _insert_flat_klines(db_session, "ETHUSDT", MIN_CANDLES)

    call_count = {"n": 0}

    def fake_compute_regime(session, now):
        call_count["n"] += 1
        return "up"

    monkeypatch.setattr(scenario_runner_module, "compute_btc_regime", fake_compute_regime)
    monkeypatch.setattr(scenario_runner_module, "utc_now", lambda: NOW)

    run_scenario_generation(db_session, ["BTCUSDT", "ETHUSDT"])

    assert call_count["n"] == 1


def test_run_scenario_generation_applies_regime_to_every_symbol(db_session):
    _seed_signal_klines(db_session, symbol="BTCUSDT")
    _seed_signal_klines(db_session, symbol="ETHUSDT")
    _seed_btc_regime_klines(db_session, day_boundary=datetime(2026, 8, 21), regime="down")

    result = run_scenario_generation(db_session, ["BTCUSDT", "ETHUSDT"], now=NOW)

    assert result.generated == 0
    assert result.skipped == 2
    assert db_session.query(Scenario).count() == 0
```

- [ ] **Step 2: Run the test file to verify the expected failures**

Run: `pytest tests/test_scenario_runner.py -v`
Expected: the `test_run_scenario_generation_*` tests fail (`run_scenario_generation` doesn't accept `now=` yet, doesn't call `compute_btc_regime`, and `process_symbol_scenario` isn't called with a `regime` argument yet).

- [ ] **Step 3: Update `run_scenario_generation`**

In `src/scenario_runner.py`, add the import at the top (near the other `src.` imports, alongside line 9):

```python
from src.btc_regime import compute_btc_regime
```

Replace lines 121-129:

```python
def run_scenario_generation(session, symbols: list, now: datetime = None) -> ScenarioRunResult:
    now = now if now is not None else utc_now()
    regime = compute_btc_regime(session, now)
    scanned = 0
    generated = 0
    skipped = 0
    failed = 0
    for symbol in symbols:
        scanned += 1
        try:
            outcome = process_symbol_scenario(session, symbol, regime, now=now)
        except Exception:
            session.rollback()
            logger.exception("Scenario generation failed for %s", symbol)
            failed += 1
            continue
```

(the rest of the function — the `if outcome == "generated":` block through the final `return` — is unchanged.)

- [ ] **Step 4: Run the full test suite to verify everything passes**

Run: `pytest tests/ -v`
Expected: all tests pass, including `tests/test_scheduler.py` (its `run_scenario_generation` mocks take `(session, symbols)` and scheduler.py never passes `now=`, so they're unaffected by the new optional parameter).

- [ ] **Step 5: Commit**

```bash
git add src/scenario_runner.py tests/test_scenario_runner.py
git commit -m "feat: compute BTC regime once per scenario generation run"
```
