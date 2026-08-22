# Confidence/Öğrenme Döngüsü Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After every hourly scheduler job, automatically resolve Subsystem B's `pending` scenarios against real price data (`hit_target`/`hit_stop`/`expired`) and calibrate a `calibrated_confidence` field from pattern-level (direction + confidence bucket) historical success rates.

**Architecture:** Two pure, DB-free logic modules (`src/outcome_evaluator.py`, `src/confidence_calibrator.py`) plus one DB-orchestrating module (`src/learning_runner.py`) that loads scenarios/klines, applies the pure logic, and writes results back with per-scenario isolation — the same shape Subsystem B used (pure `scenario_signal.py`/`scenario_builder.py` vs. DB-aware `scenario_runner.py`). Wired into `run_timeframe_job("1h")` in `src/scheduler.py`, right after Subsystem B's scenario generation.

**Tech Stack:** Same as Subsystems A/B — Python 3.9, SQLAlchemy 2.0 ORM, `Decimal` for all price/ratio math, pytest with `sqlite:///:memory:`.

**Spec:** `docs/superpowers/specs/2026-08-22-confidence-learning-loop-design.md`

## Global Constraints

- Python 3.9 compatibility — every file using `X | None` / `list[X]` style annotations MUST start with `from __future__ import annotations`.
- All price, ratio, and score arithmetic uses `Decimal`, never `float`.
- A scenario's resolution/calibration failure must never abort processing of other scenarios — mirror the existing per-item `try/except` + `session.rollback()` + `logger.exception` isolation pattern already established in `src/scenario_runner.py` and `src/scheduler.py`.
- On a candle where both target and stop are touched, **stop wins** (the conservative assumption — intra-candle ordering is unknown).
- `expired` counts as a **failure** for success-rate purposes: `success_rate = hit_target / (hit_target + hit_stop + expired)`.
- A `(direction, confidence bucket)` pattern needs **at least 20** resolved samples before its computed success rate is trusted; below that, `calibrated_confidence` falls back to the scenario's own raw `confidence_score`.
- Confidence buckets are 0.1 wide: `[0.0,0.1), [0.1,0.2), ..., [0.8,0.9), [0.9,1.0]` (10 buckets; a score of exactly `1.0` joins the last bucket, not an eleventh empty one).
- `calibrated_confidence` is set once per scenario and never overwritten after that — `Scenario.confidence_score` (Subsystem B's raw output) is never modified.
- Outcome resolution scans **all** `pending` scenarios regardless of whether their symbol is still active (a delisted symbol's price history is still in `klines`).

---

## File Structure

```
src/
├── outcome_evaluator.py      (new)  pure: has a pending scenario resolved yet?
├── confidence_calibrator.py  (new)  pure: confidence bucketing + pattern success rates
├── learning_runner.py        (new)  DB orchestration: resolve pending scenarios, then calibrate
├── scheduler.py               (modify)  hook the learning cycle into the 1h job
└── db/
    └── models.py              (modify)  add resolved_at + calibrated_confidence to Scenario
tests/
├── test_outcome_evaluator.py      (new)
├── test_confidence_calibrator.py  (new)
├── test_learning_runner.py        (new)
├── test_models.py                 (modify)  Scenario resolved_at/calibrated_confidence tests
└── test_scheduler.py               (modify)  learning-cycle wiring tests + 4 log-line updates
```

---

### Task 1: Extend `Scenario` model

**Files:**
- Modify: `src/db/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: `db.models.Scenario` gains `resolved_at: DateTime, nullable` and `calibrated_confidence: Numeric(5,4), nullable`, both defaulting to `NULL`.

- [ ] **Step 1: Write the failing test**

In `tests/test_models.py`, add this test to the end of the file (the file already imports `Scenario`, `datetime`, `timedelta`, `Decimal` from Task 1 of the scenario-engine plan):

```python
def test_scenario_resolved_at_and_calibrated_confidence_default_to_null(db_session):
    now = datetime(2026, 1, 1, 12, 0, 0)
    db_session.add(Scenario(
        symbol="BTCUSDT", direction="long",
        entry_price=Decimal("50000"), target_price=Decimal("52000"), stop_price=Decimal("49000"),
        expected_return_pct=Decimal("0.04"), confidence_score=Decimal("0.7"),
        created_at=now, expires_at=now + timedelta(hours=24), status="pending",
    ))
    db_session.commit()
    row = db_session.query(Scenario).first()
    assert row.resolved_at is None
    assert row.calibrated_confidence is None

    row.status = "hit_target"
    row.resolved_at = now + timedelta(hours=3)
    row.calibrated_confidence = Decimal("0.65")
    db_session.commit()

    reloaded = db_session.query(Scenario).first()
    assert reloaded.status == "hit_target"
    assert reloaded.resolved_at == now + timedelta(hours=3)
    assert reloaded.calibrated_confidence == Decimal("0.65")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_models.py -v`
Expected: FAIL (`TypeError: 'resolved_at' is an invalid keyword argument` or `AttributeError` on `row.resolved_at`)

- [ ] **Step 3: Write minimal implementation**

In `src/db/models.py`, add two columns to the end of the `Scenario` class (after the existing `status` line):

```python
    resolved_at = Column(DateTime, nullable=True)
    calibrated_confidence = Column(Numeric(5, 4), nullable=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/db/models.py tests/test_models.py
git commit -m "feat: add resolved_at and calibrated_confidence to Scenario"
```

---

### Task 2: Outcome Evaluator (pure)

**Files:**
- Create: `src/outcome_evaluator.py`
- Test: `tests/test_outcome_evaluator.py`

**Interfaces:**
- Produces: `outcome_evaluator.evaluate_outcome(direction: str, target_price: Decimal, stop_price: Decimal, expires_at: datetime, klines: list, now: datetime) -> tuple` (or `None`). Returns `(status, resolved_at)` where `status` is `"hit_target"` | `"hit_stop"` | `"expired"`, or `None` if still pending. `klines` entries are dicts with `open_time`, `high`, `low` keys, ascending by `open_time`, covering the window from just after the scenario's creation up to `now`.

- [ ] **Step 1: Write the failing tests**

`tests/test_outcome_evaluator.py`:
```python
from datetime import datetime, timedelta
from decimal import Decimal

from src.outcome_evaluator import evaluate_outcome


def _kline(hour, high, low):
    return {"open_time": datetime(2026, 1, 1) + timedelta(hours=hour), "high": Decimal(str(high)), "low": Decimal(str(low))}


EXPIRES = datetime(2026, 1, 2)
NOW = datetime(2026, 1, 1, 12)


def test_long_hits_target():
    klines = [_kline(0, 105, 95), _kline(1, 112, 104)]  # candle 1 high >= 110 target
    result = evaluate_outcome("long", Decimal("110"), Decimal("90"), EXPIRES, klines, NOW)
    assert result == ("hit_target", klines[1]["open_time"])


def test_long_hits_stop():
    klines = [_kline(0, 105, 95), _kline(1, 106, 88)]  # candle 1 low <= 90 stop
    result = evaluate_outcome("long", Decimal("110"), Decimal("90"), EXPIRES, klines, NOW)
    assert result == ("hit_stop", klines[1]["open_time"])


def test_long_same_candle_hits_both_stop_wins():
    klines = [_kline(0, 112, 88)]  # high >= target AND low <= stop in one candle
    result = evaluate_outcome("long", Decimal("110"), Decimal("90"), EXPIRES, klines, NOW)
    assert result == ("hit_stop", klines[0]["open_time"])


def test_long_neither_hit_and_not_expired_stays_pending():
    klines = [_kline(0, 105, 95)]
    result = evaluate_outcome("long", Decimal("110"), Decimal("90"), EXPIRES, klines, NOW)
    assert result is None


def test_long_neither_hit_and_expired():
    klines = [_kline(0, 105, 95)]
    result = evaluate_outcome("long", Decimal("110"), Decimal("90"), EXPIRES, klines, now=EXPIRES + timedelta(hours=1))
    assert result == ("expired", EXPIRES)


def test_short_hits_target():
    klines = [_kline(0, 95, 88)]  # low <= 90 target
    result = evaluate_outcome("short", Decimal("90"), Decimal("110"), EXPIRES, klines, NOW)
    assert result == ("hit_target", klines[0]["open_time"])


def test_short_hits_stop():
    klines = [_kline(0, 112, 100)]  # high >= 110 stop
    result = evaluate_outcome("short", Decimal("90"), Decimal("110"), EXPIRES, klines, NOW)
    assert result == ("hit_stop", klines[0]["open_time"])


def test_short_same_candle_hits_both_stop_wins():
    klines = [_kline(0, 112, 88)]  # high >= stop AND low <= target in one candle
    result = evaluate_outcome("short", Decimal("90"), Decimal("110"), EXPIRES, klines, NOW)
    assert result == ("hit_stop", klines[0]["open_time"])


def test_no_klines_and_not_expired_stays_pending():
    result = evaluate_outcome("long", Decimal("110"), Decimal("90"), EXPIRES, [], NOW)
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_outcome_evaluator.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.outcome_evaluator'`)

- [ ] **Step 3: Write minimal implementation**

`src/outcome_evaluator.py`:
```python
from __future__ import annotations

from datetime import datetime
from decimal import Decimal


def evaluate_outcome(
    direction: str,
    target_price: Decimal,
    stop_price: Decimal,
    expires_at: datetime,
    klines: list,
    now: datetime,
):
    """Has this scenario resolved yet?

    `klines` are ascending dicts with `open_time`, `high`, `low`, covering the
    window from just after the scenario's creation up to `now`. Returns
    `(status, resolved_at)` if resolved, else `None` (still pending).

    A candle that touches both target and stop resolves as `hit_stop` — the
    conservative assumption, since intra-candle ordering isn't known.
    """
    for kline in klines:
        if direction == "long":
            stop_hit = kline["low"] <= stop_price
            target_hit = kline["high"] >= target_price
        else:
            stop_hit = kline["high"] >= stop_price
            target_hit = kline["low"] <= target_price

        if stop_hit:
            return ("hit_stop", kline["open_time"])
        if target_hit:
            return ("hit_target", kline["open_time"])

    if now > expires_at:
        return ("expired", expires_at)
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_outcome_evaluator.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/outcome_evaluator.py tests/test_outcome_evaluator.py
git commit -m "feat: add pure scenario outcome evaluation"
```

---

### Task 3: Confidence Calibrator (pure)

**Files:**
- Create: `src/confidence_calibrator.py`
- Test: `tests/test_confidence_calibrator.py`

**Interfaces:**
- Produces:
  - `confidence_calibrator.MIN_SAMPLES = 20`
  - `confidence_calibrator.confidence_bucket(confidence_score: Decimal) -> Decimal` — the lower bound of the 0.1-wide bucket.
  - `confidence_calibrator.compute_success_rates(records: list) -> dict` — `records` is a list of `(direction: str, confidence_score: Decimal, status: str)` tuples for **resolved** (non-pending) scenarios. Returns `{(direction, bucket): (rate_or_None, sample_count)}` — `rate_or_None` is `None` when `sample_count < MIN_SAMPLES`.

- [ ] **Step 1: Write the failing tests**

`tests/test_confidence_calibrator.py`:
```python
from decimal import Decimal

from src.confidence_calibrator import MIN_SAMPLES, compute_success_rates, confidence_bucket


def test_confidence_bucket_lower_bound_values():
    assert confidence_bucket(Decimal("0.0")) == Decimal("0.0")
    assert confidence_bucket(Decimal("0.05")) == Decimal("0.0")
    assert confidence_bucket(Decimal("0.15")) == Decimal("0.1")
    assert confidence_bucket(Decimal("0.65")) == Decimal("0.6")
    assert confidence_bucket(Decimal("0.99")) == Decimal("0.9")


def test_confidence_bucket_exactly_one_joins_last_bucket():
    assert confidence_bucket(Decimal("1.0")) == Decimal("0.9")


def test_compute_success_rates_below_min_samples_returns_none():
    records = [("long", Decimal("0.65"), "hit_target")] * (MIN_SAMPLES - 1)
    rates = compute_success_rates(records)
    rate, count = rates[("long", Decimal("0.6"))]
    assert rate is None
    assert count == MIN_SAMPLES - 1


def test_compute_success_rates_at_min_samples_computes_real_rate():
    hits = [("long", Decimal("0.65"), "hit_target")] * 12
    stops = [("long", Decimal("0.65"), "hit_stop")] * 6
    expired = [("long", Decimal("0.65"), "expired")] * 2
    records = hits + stops + expired  # 20 total, 12 hit_target
    rates = compute_success_rates(records)
    rate, count = rates[("long", Decimal("0.6"))]
    assert count == 20
    assert rate == Decimal("12") / Decimal("20")


def test_compute_success_rates_expired_counts_as_failure():
    records = [("long", Decimal("0.65"), "hit_target")] * 10 + [("long", Decimal("0.65"), "expired")] * 10
    rates = compute_success_rates(records)
    rate, count = rates[("long", Decimal("0.6"))]
    assert count == 20
    assert rate == Decimal("10") / Decimal("20")


def test_compute_success_rates_keeps_direction_and_bucket_separate():
    records = (
        [("long", Decimal("0.65"), "hit_target")] * 20
        + [("short", Decimal("0.65"), "hit_stop")] * 20
        + [("long", Decimal("0.25"), "hit_stop")] * 20
    )
    rates = compute_success_rates(records)
    assert rates[("long", Decimal("0.6"))][0] == Decimal("1")
    assert rates[("short", Decimal("0.6"))][0] == Decimal("0")
    assert rates[("long", Decimal("0.2"))][0] == Decimal("0")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_confidence_calibrator.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.confidence_calibrator'`)

- [ ] **Step 3: Write minimal implementation**

`src/confidence_calibrator.py`:
```python
from __future__ import annotations

from decimal import Decimal

MIN_SAMPLES = 20
BUCKET_WIDTH = Decimal("0.1")


def confidence_bucket(confidence_score: Decimal) -> Decimal:
    """The lower bound of the 0.1-wide bucket this score falls into.

    Buckets are [0.0,0.1), [0.1,0.2), ..., [0.9,1.0]. A score of exactly 1.0
    would compute to bucket index 10 without the cap below; clamping to 9
    joins it to the [0.9,1.0] bucket instead of an eleventh, empty one.
    """
    index = min(int(confidence_score / BUCKET_WIDTH), 9)
    return Decimal(index) * BUCKET_WIDTH


def compute_success_rates(records: list) -> dict:
    """`records`: (direction, confidence_score, status) tuples for resolved
    (non-pending) scenarios. Returns {(direction, bucket): (rate_or_None, sample_count)}.

    A bucket's rate is None until it has at least MIN_SAMPLES resolved
    scenarios — with too few samples, an early lucky or unlucky streak would
    look like a real pattern.
    """
    tally = {}
    for direction, confidence_score, status in records:
        key = (direction, confidence_bucket(confidence_score))
        hits, total = tally.get(key, (0, 0))
        total += 1
        if status == "hit_target":
            hits += 1
        tally[key] = (hits, total)

    return {
        key: (Decimal(hits) / Decimal(total) if total >= MIN_SAMPLES else None, total)
        for key, (hits, total) in tally.items()
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_confidence_calibrator.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/confidence_calibrator.py tests/test_confidence_calibrator.py
git commit -m "feat: add pure confidence bucketing and success-rate calculation"
```

---

### Task 4: Learning Runner — outcome resolution

**Files:**
- Create: `src/learning_runner.py`
- Test: `tests/test_learning_runner.py`

**Interfaces:**
- Consumes: `outcome_evaluator.evaluate_outcome` (Task 2); `db.models.Kline`, `db.models.Scenario` (Task 1, Subsystem B); `integrity.floor_to_timeframe` (Subsystem A); `timeutil.utc_now` (Subsystem A)
- Produces: `learning_runner.OutcomeResolutionResult(scanned: int, resolved: int, still_pending: int, failed: int)`, `learning_runner.resolve_pending_scenarios(session, now: datetime = None) -> OutcomeResolutionResult`

- [ ] **Step 1: Write the failing tests**

`tests/test_learning_runner.py`:
```python
from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import Kline, Scenario
from src.learning_runner import resolve_pending_scenarios


def _pending_scenario(symbol="BTCUSDT", direction="long", created_at=None, expires_at=None):
    created_at = created_at or datetime(2026, 1, 1, 10, 5, 0)
    expires_at = expires_at or created_at + timedelta(hours=24)
    return Scenario(
        symbol=symbol, direction=direction,
        entry_price=Decimal("100"), target_price=Decimal("110"), stop_price=Decimal("90"),
        expected_return_pct=Decimal("0.1"), confidence_score=Decimal("0.6"),
        created_at=created_at, expires_at=expires_at, status="pending",
    )


def _kline(symbol, open_time, high, low):
    return Kline(
        symbol=symbol, timeframe="1h", open_time=open_time,
        open=Decimal("100"), high=Decimal(str(high)), low=Decimal(str(low)), close=Decimal("100"),
        volume=Decimal("1000"), flagged=False,
    )


def test_resolve_pending_scenarios_marks_a_hit_target(db_session):
    scenario = _pending_scenario(created_at=datetime(2026, 1, 1, 10, 5, 0))
    db_session.add(scenario)
    # floor_to_timeframe(10:05, "1h") == 10:00 — the candle forming at creation time.
    db_session.add(_kline("BTCUSDT", datetime(2026, 1, 1, 10, 0, 0), high=112, low=98))
    db_session.commit()

    result = resolve_pending_scenarios(db_session, now=datetime(2026, 1, 1, 11, 5, 0))

    assert result.scanned == 1
    assert result.resolved == 1
    assert result.still_pending == 0
    assert result.failed == 0
    reloaded = db_session.query(Scenario).first()
    assert reloaded.status == "hit_target"
    assert reloaded.resolved_at == datetime(2026, 1, 1, 10, 0, 0)


def test_resolve_pending_scenarios_leaves_unresolved_ones_pending(db_session):
    scenario = _pending_scenario(
        created_at=datetime(2026, 1, 1, 10, 5, 0),
        expires_at=datetime(2026, 1, 2, 10, 5, 0),
    )
    db_session.add(scenario)
    db_session.add(_kline("BTCUSDT", datetime(2026, 1, 1, 10, 0, 0), high=105, low=95))
    db_session.commit()

    result = resolve_pending_scenarios(db_session, now=datetime(2026, 1, 1, 11, 5, 0))

    assert result.resolved == 0
    assert result.still_pending == 1
    reloaded = db_session.query(Scenario).first()
    assert reloaded.status == "pending"
    assert reloaded.resolved_at is None


def test_resolve_pending_scenarios_marks_expired(db_session):
    scenario = _pending_scenario(
        created_at=datetime(2026, 1, 1, 10, 5, 0),
        expires_at=datetime(2026, 1, 2, 10, 5, 0),
    )
    db_session.add(scenario)
    db_session.add(_kline("BTCUSDT", datetime(2026, 1, 1, 10, 0, 0), high=105, low=95))
    db_session.commit()

    result = resolve_pending_scenarios(db_session, now=datetime(2026, 1, 3, 0, 0, 0))

    assert result.resolved == 1
    reloaded = db_session.query(Scenario).first()
    assert reloaded.status == "expired"
    assert reloaded.resolved_at == datetime(2026, 1, 2, 10, 5, 0)


def test_resolve_pending_scenarios_isolates_a_failing_scenario(db_session, monkeypatch):
    good = _pending_scenario(symbol="BTCUSDT", created_at=datetime(2026, 1, 1, 10, 5, 0))
    bad = _pending_scenario(symbol="ETHUSDT", created_at=datetime(2026, 1, 1, 10, 5, 0))
    db_session.add_all([good, bad])
    db_session.add(_kline("BTCUSDT", datetime(2026, 1, 1, 10, 0, 0), high=112, low=98))
    db_session.commit()

    import src.learning_runner as learning_runner_module

    real_evaluate_outcome = learning_runner_module.evaluate_outcome

    def flaky_evaluate_outcome(direction, target_price, stop_price, expires_at, klines, now):
        if direction == "long" and target_price == Decimal("110") and not klines:
            raise RuntimeError("boom")
        return real_evaluate_outcome(direction, target_price, stop_price, expires_at, klines, now)

    monkeypatch.setattr(learning_runner_module, "evaluate_outcome", flaky_evaluate_outcome)

    result = resolve_pending_scenarios(db_session, now=datetime(2026, 1, 1, 11, 5, 0))

    assert result.scanned == 2
    assert result.resolved == 1  # BTCUSDT, has klines
    assert result.failed == 1  # ETHUSDT, no klines -> triggers the flaky raise
    reloaded_good = db_session.query(Scenario).filter(Scenario.symbol == "BTCUSDT").first()
    assert reloaded_good.status == "hit_target"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_learning_runner.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.learning_runner'`)

- [ ] **Step 3: Write minimal implementation**

`src/learning_runner.py`:
```python
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from src.db.models import Kline, Scenario
from src.integrity import floor_to_timeframe
from src.outcome_evaluator import evaluate_outcome
from src.timeutil import utc_now

logger = logging.getLogger("learning_runner")


@dataclass
class OutcomeResolutionResult:
    scanned: int
    resolved: int
    still_pending: int
    failed: int


def _load_klines_since(session, symbol: str, timeframe: str, since: datetime) -> list:
    rows = (
        session.query(Kline)
        .filter(Kline.symbol == symbol, Kline.timeframe == timeframe, Kline.open_time >= since)
        .order_by(Kline.open_time.asc())
        .all()
    )
    return [{"open_time": row.open_time, "high": row.high, "low": row.low} for row in rows]


def resolve_pending_scenarios(session, now: datetime = None) -> OutcomeResolutionResult:
    now = now if now is not None else utc_now()
    pending = session.query(Scenario).filter(Scenario.status == "pending").all()

    scanned = 0
    resolved = 0
    still_pending = 0
    failed = 0
    for scenario in pending:
        scanned += 1
        try:
            since = floor_to_timeframe(scenario.created_at, "1h")
            klines = _load_klines_since(session, scenario.symbol, "1h", since)
            outcome = evaluate_outcome(
                scenario.direction, scenario.target_price, scenario.stop_price,
                scenario.expires_at, klines, now,
            )
            if outcome is not None:
                status, resolved_at = outcome
                scenario.status = status
                scenario.resolved_at = resolved_at
                session.commit()
                resolved += 1
            else:
                still_pending += 1
        except Exception:
            session.rollback()
            logger.exception("Outcome resolution failed for scenario %s (%s)", scenario.id, scenario.symbol)
            failed += 1

    return OutcomeResolutionResult(scanned=scanned, resolved=resolved, still_pending=still_pending, failed=failed)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_learning_runner.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/learning_runner.py tests/test_learning_runner.py
git commit -m "feat: add pending-scenario outcome resolution"
```

---

### Task 5: Learning Runner — confidence calibration

**Files:**
- Modify: `src/learning_runner.py`
- Test: `tests/test_learning_runner.py`

**Interfaces:**
- Consumes: `confidence_calibrator.compute_success_rates`, `confidence_bucket` (Task 3); `db.models.Scenario` (Task 1)
- Produces: `learning_runner.CalibrationResult(scenarios_updated: int, patterns_with_data: int)`, `learning_runner.calibrate_scenarios(session) -> CalibrationResult`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_learning_runner.py`. First add this import alongside the existing ones at the top of the file:

```python
from src.confidence_calibrator import MIN_SAMPLES
from src.learning_runner import calibrate_scenarios
```

Then add these tests to the end of the file:

```python
def _resolved_scenario(symbol, direction, confidence_score, status):
    now = datetime(2026, 1, 1)
    return Scenario(
        symbol=symbol, direction=direction,
        entry_price=Decimal("100"), target_price=Decimal("110"), stop_price=Decimal("90"),
        expected_return_pct=Decimal("0.1"), confidence_score=confidence_score,
        created_at=now, expires_at=now + timedelta(hours=24),
        status=status, resolved_at=now + timedelta(hours=3),
        calibrated_confidence=confidence_score,  # already calibrated when it was created
    )


def test_calibrate_scenarios_falls_back_to_raw_score_below_min_samples(db_session):
    pending = _pending_scenario(symbol="BTCUSDT", direction="long")
    pending.confidence_score = Decimal("0.65")
    db_session.add(pending)
    db_session.commit()

    result = calibrate_scenarios(db_session)

    assert result.scenarios_updated == 1
    assert result.patterns_with_data == 0
    reloaded = db_session.query(Scenario).first()
    assert reloaded.calibrated_confidence == Decimal("0.65")


def test_calibrate_scenarios_uses_computed_rate_at_min_samples(db_session):
    for _ in range(15):
        db_session.add(_resolved_scenario("ETHUSDT", "long", Decimal("0.65"), "hit_target"))
    for _ in range(5):
        db_session.add(_resolved_scenario("ETHUSDT", "long", Decimal("0.65"), "hit_stop"))
    pending = _pending_scenario(symbol="BTCUSDT", direction="long")
    pending.confidence_score = Decimal("0.65")
    db_session.add(pending)
    db_session.commit()

    result = calibrate_scenarios(db_session)

    assert result.patterns_with_data == 1
    reloaded = db_session.query(Scenario).filter(Scenario.symbol == "BTCUSDT").first()
    assert reloaded.calibrated_confidence == Decimal("15") / Decimal("20")


def test_calibrate_scenarios_does_not_touch_already_calibrated_rows(db_session):
    already = _resolved_scenario("ETHUSDT", "long", Decimal("0.65"), "hit_target")
    already.calibrated_confidence = Decimal("0.42")
    db_session.add(already)
    db_session.commit()

    result = calibrate_scenarios(db_session)

    assert result.scenarios_updated == 0
    reloaded = db_session.query(Scenario).first()
    assert reloaded.calibrated_confidence == Decimal("0.42")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_learning_runner.py -v -k calibrate`
Expected: FAIL (`ImportError: cannot import name 'calibrate_scenarios'`)

- [ ] **Step 3: Write minimal implementation**

In `src/learning_runner.py`, add this import to the top import block:

```python
from src.confidence_calibrator import compute_success_rates, confidence_bucket
```

Then add this to the end of the file:

```python
@dataclass
class CalibrationResult:
    scenarios_updated: int
    patterns_with_data: int


def calibrate_scenarios(session) -> CalibrationResult:
    resolved_records = [
        (row.direction, row.confidence_score, row.status)
        for row in session.query(Scenario).filter(Scenario.status != "pending").all()
    ]
    rates = compute_success_rates(resolved_records)
    patterns_with_data = sum(1 for rate, _count in rates.values() if rate is not None)

    targets = session.query(Scenario).filter(Scenario.calibrated_confidence.is_(None)).all()
    scenarios_updated = 0
    for scenario in targets:
        key = (scenario.direction, confidence_bucket(scenario.confidence_score))
        rate, _count = rates.get(key, (None, 0))
        scenario.calibrated_confidence = rate if rate is not None else scenario.confidence_score
        scenarios_updated += 1
    session.commit()

    return CalibrationResult(scenarios_updated=scenarios_updated, patterns_with_data=patterns_with_data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_learning_runner.py -v`
Expected: PASS (7 tests total in the file)

- [ ] **Step 5: Commit**

```bash
git add src/learning_runner.py tests/test_learning_runner.py
git commit -m "feat: add pattern-based confidence calibration"
```

---

### Task 6: Learning Runner — orchestrator

**Files:**
- Modify: `src/learning_runner.py`
- Test: `tests/test_learning_runner.py`

**Interfaces:**
- Consumes: `resolve_pending_scenarios` (Task 4), `calibrate_scenarios` (Task 5)
- Produces: `learning_runner.LearningRunResult(scanned: int, resolved: int, still_pending: int, failed: int, scenarios_calibrated: int)`, `learning_runner.run_learning_cycle(session, now: datetime = None) -> LearningRunResult`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_learning_runner.py`:

```python
def test_run_learning_cycle_resolves_and_calibrates_end_to_end(db_session):
    """Real klines, real scenario, all the way to a persisted, calibrated row —
    a stub or monkeypatched version of this test would not catch a broken wire
    between resolution and calibration."""
    from src.learning_runner import run_learning_cycle

    for _ in range(15):
        db_session.add(_resolved_scenario("ETHUSDT", "long", Decimal("0.65"), "hit_target"))
    for _ in range(5):
        db_session.add(_resolved_scenario("ETHUSDT", "long", Decimal("0.65"), "hit_stop"))

    scenario = _pending_scenario(symbol="BTCUSDT", direction="long", created_at=datetime(2026, 1, 1, 10, 5, 0))
    scenario.confidence_score = Decimal("0.65")
    db_session.add(scenario)
    db_session.add(_kline("BTCUSDT", datetime(2026, 1, 1, 10, 0, 0), high=112, low=98))
    db_session.commit()

    result = run_learning_cycle(db_session, now=datetime(2026, 1, 1, 11, 5, 0))

    assert result.scanned == 1
    assert result.resolved == 1
    assert result.scenarios_calibrated == 1

    reloaded = db_session.query(Scenario).filter(Scenario.symbol == "BTCUSDT").first()
    assert reloaded.status == "hit_target"
    # Outcome resolution commits BEFORE calibration reads the resolved-scenario
    # pool, so the just-resolved BTCUSDT row is itself part of what it's
    # calibrated against: 15 existing hits + this new hit = 16 of 21, not 15 of 20.
    assert reloaded.calibrated_confidence == Decimal("16") / Decimal("21")


def test_run_learning_cycle_survives_a_calibration_failure(db_session, monkeypatch):
    scenario = _pending_scenario(symbol="BTCUSDT", direction="long", created_at=datetime(2026, 1, 1, 10, 5, 0))
    db_session.add(scenario)
    db_session.add(_kline("BTCUSDT", datetime(2026, 1, 1, 10, 0, 0), high=112, low=98))
    db_session.commit()

    import src.learning_runner as learning_runner_module

    def boom(session):
        raise RuntimeError("calibration exploded")

    monkeypatch.setattr(learning_runner_module, "calibrate_scenarios", boom)

    from src.learning_runner import run_learning_cycle
    result = run_learning_cycle(db_session, now=datetime(2026, 1, 1, 11, 5, 0))

    # Outcome resolution still completed even though calibration blew up.
    assert result.resolved == 1
    assert result.scenarios_calibrated == 0
    reloaded = db_session.query(Scenario).first()
    assert reloaded.status == "hit_target"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_learning_runner.py -v -k run_learning_cycle`
Expected: FAIL (`ImportError: cannot import name 'run_learning_cycle'`)

- [ ] **Step 3: Write minimal implementation**

Add this to the end of `src/learning_runner.py`:

```python
@dataclass
class LearningRunResult:
    scanned: int
    resolved: int
    still_pending: int
    failed: int
    scenarios_calibrated: int


def run_learning_cycle(session, now: datetime = None) -> LearningRunResult:
    now = now if now is not None else utc_now()
    outcome_result = resolve_pending_scenarios(session, now)

    scenarios_calibrated = 0
    try:
        calibration_result = calibrate_scenarios(session)
        scenarios_calibrated = calibration_result.scenarios_updated
    except Exception:
        session.rollback()
        logger.exception("Confidence calibration failed")

    logger.info(
        "Learning cycle finished: %d scanned, %d resolved, %d still pending, %d failed, %d scenarios calibrated",
        outcome_result.scanned, outcome_result.resolved, outcome_result.still_pending,
        outcome_result.failed, scenarios_calibrated,
    )
    return LearningRunResult(
        scanned=outcome_result.scanned, resolved=outcome_result.resolved,
        still_pending=outcome_result.still_pending, failed=outcome_result.failed,
        scenarios_calibrated=scenarios_calibrated,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_learning_runner.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add src/learning_runner.py tests/test_learning_runner.py
git commit -m "feat: add learning cycle orchestrator"
```

---

### Task 7: Wire the learning cycle into the hourly scheduler job

**Files:**
- Modify: `src/scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `learning_runner.run_learning_cycle`, `LearningRunResult` (Task 6)
- Produces: no new public interface — `run_timeframe_job` gains a second side effect (the learning cycle, for `timeframe == "1h"`) and its final log line gains `resolved`/`calibrated` counts when both scenario generation and the learning cycle succeed in the same run.

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
from src.scenario_runner import run_scenario_generation
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

        scenario_result = None
        if timeframe == "1h":
            try:
                scenario_result = run_scenario_generation(session, symbols)
            except Exception:
                logger.exception("Scenario generation failed for the %s job", timeframe)

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
    finally:
        session.close()


def run_symbol_refresh_job(session_factory, binance_client) -> None:
    ...  # unchanged


def build_scheduler(session_factory, binance_client) -> BackgroundScheduler:
    ...  # unchanged
```

(`get_resume_point`, `repair_recent_gaps`, `run_symbol_refresh_job`, `build_scheduler` bodies are unchanged by this task — omitted above for brevity, but do not touch them.)

- [ ] **Step 1: Write the failing tests**

Add these three tests to `tests/test_scheduler.py` (the file already has `import src.scheduler as scheduler_module`, `_FakeBinanceClient`, and `Symbol`/`datetime`/`timedelta`/`logging` imported at the top):

```python
def test_run_timeframe_job_runs_learning_cycle_after_1h_fetch(db_session, monkeypatch):
    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True))
    db_session.commit()

    calls = []

    def fake_run_learning_cycle(session, now=None):
        from src.learning_runner import LearningRunResult
        calls.append(now)
        return LearningRunResult(scanned=0, resolved=0, still_pending=0, failed=0, scenarios_calibrated=0)

    monkeypatch.setattr(scheduler_module, "run_learning_cycle", fake_run_learning_cycle)

    end = datetime(2026, 1, 1, 5, 0, 0)
    run_timeframe_job(session_factory=lambda: db_session, binance_client=_FakeBinanceClient(), timeframe="1h", now=end)

    assert calls == [end]


def test_run_timeframe_job_does_not_run_learning_cycle_for_1d(db_session, monkeypatch):
    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True))
    db_session.commit()

    calls = []
    monkeypatch.setattr(
        scheduler_module, "run_learning_cycle",
        lambda session, now=None: calls.append(now),
    )

    run_timeframe_job(session_factory=lambda: db_session, binance_client=_FakeBinanceClient(), timeframe="1d")

    assert calls == []


def test_run_timeframe_job_survives_a_learning_cycle_failure(db_session, caplog, monkeypatch):
    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True))
    db_session.commit()

    def boom(session, now=None):
        raise RuntimeError("learning cycle exploded")

    monkeypatch.setattr(scheduler_module, "run_learning_cycle", boom)

    with caplog.at_level(logging.INFO, logger="scheduler"):
        run_timeframe_job(session_factory=lambda: db_session, binance_client=_FakeBinanceClient(), timeframe="1h")

    assert any(record.exc_info for record in caplog.records if record.levelno >= logging.ERROR)
    # Real scenario generation ran against a symbol with no klines -> skipped, generated=0.
    # Learning cycle blew up, so the summary falls back to the scenario-only format.
    assert _summary_lines(caplog) == ["1h job finished: 1 symbols succeeded, 0 failed, 0 gaps filled, 0 scenarios generated"]
```

Then update these **4 existing tests** (their scenario generation already ran for real in these tests — a real, empty `scenarios` table means the learning cycle will now also run for real and succeed trivially, extending the summary line):

In `test_gap_repair_failure_is_logged_and_not_counted_as_filled`, change:
```python
    assert _summary_lines(caplog) == ["1h job finished: 1 symbols succeeded, 0 failed, 0 gaps filled, 0 scenarios generated"]
```
to:
```python
    assert _summary_lines(caplog) == [
        "1h job finished: 1 symbols succeeded, 0 failed, 0 gaps filled, 0 scenarios generated, 0 resolved, 0 calibrated"
    ]
```

In `test_gaps_filled_counts_stored_rows_not_fetch_attempts`, change:
```python
    assert _summary_lines(caplog) == ["1h job finished: 1 symbols succeeded, 0 failed, 0 gaps filled, 0 scenarios generated"] * 3
```
to:
```python
    assert _summary_lines(caplog) == [
        "1h job finished: 1 symbols succeeded, 0 failed, 0 gaps filled, 0 scenarios generated, 0 resolved, 0 calibrated"
    ] * 3
```

In `test_gaps_filled_counts_a_real_repair`, change:
```python
    assert _summary_lines(caplog) == ["1h job finished: 1 symbols succeeded, 0 failed, 1 gaps filled, 0 scenarios generated"]
```
to:
```python
    assert _summary_lines(caplog) == [
        "1h job finished: 1 symbols succeeded, 0 failed, 1 gaps filled, 0 scenarios generated, 0 resolved, 0 calibrated"
    ]
```

In `test_run_summary_counts_each_symbol_exactly_once`, change:
```python
    assert _summary_lines(caplog) == ["1h job finished: 0 symbols succeeded, 1 failed, 0 gaps filled, 0 scenarios generated"]
```
to:
```python
    assert _summary_lines(caplog) == [
        "1h job finished: 0 symbols succeeded, 1 failed, 0 gaps filled, 0 scenarios generated, 0 resolved, 0 calibrated"
    ]
```

Do **not** change `test_run_timeframe_job_survives_a_scenario_generation_failure` — in that test `scenario_result` stays `None` (generation itself raises), so the summary still falls through to the base 3-field format regardless of the learning cycle's outcome; its existing assertion `["1h job finished: 1 symbols succeeded, 0 failed, 0 gaps filled"]` is unaffected by this task.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_scheduler.py -v -k "learning or gap_repair_failure or gaps_filled or run_summary"`
Expected: FAIL — the 3 new tests fail with `AttributeError` (no `run_learning_cycle` attribute on the module to monkeypatch), and the 4 updated tests fail with an assertion mismatch against the old (not-yet-changed) production code.

- [ ] **Step 3: Write minimal implementation**

In `src/scheduler.py`:

1. Add this import alongside the existing `from src.scenario_runner import run_scenario_generation` line:

```python
from src.learning_runner import run_learning_cycle
```

2. Replace the block from `scenario_result = None` through the end of the `if scenario_result is not None: ... else: ...` logging block with:

```python
        scenario_result = None
        learning_result = None
        if timeframe == "1h":
            try:
                scenario_result = run_scenario_generation(session, symbols)
            except Exception:
                # Scenario generation isolates its own per-symbol failures, but
                # a raise from the call itself (or from its rollback) would
                # escape and swallow the run summary below. Nothing is rolled
                # back here on purpose: session.close() in the finally block
                # already discards the transaction, and a second rollback could
                # raise for the same reason the first one did.
                logger.exception("Scenario generation failed for the %s job", timeframe)

            try:
                learning_result = run_learning_cycle(session, now=end)
            except Exception:
                # Same reasoning as scenario generation above: isolate the
                # summary log from a failure in the learning cycle itself.
                logger.exception("Learning cycle failed for the %s job", timeframe)

        if scenario_result is not None and learning_result is not None:
            logger.info(
                "%s job finished: %d symbols succeeded, %d failed, %d gaps filled, "
                "%d scenarios generated, %d resolved, %d calibrated",
                timeframe, succeeded, failed, gaps_filled, scenario_result.generated,
                learning_result.resolved, learning_result.scenarios_calibrated,
            )
        elif scenario_result is not None:
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

Everything else in `src/scheduler.py` stays exactly as it is.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_scheduler.py -v`
Expected: PASS (all scheduler tests, including the 3 new ones and the 4 updated ones)

- [ ] **Step 5: Run the full test suite**

Run: `python3 -m pytest -v`
Expected: PASS (all tests across Subsystems A, B, and this plan)

- [ ] **Step 6: Commit**

```bash
git add src/scheduler.py tests/test_scheduler.py
git commit -m "feat: wire the learning cycle into the hourly scheduler job"
```

---

### Task 8: README update

**Files:**
- Modify: `README.md`

**Interfaces:**
- None (documentation only)

- [ ] **Step 1: Update the README**

Read the current `README.md` first, then add a new section after the existing "Senaryo Üretimi" section (before "Test"), in the same terse Turkish style as the rest of the file:

```markdown
## Öğrenme Döngüsü

Her saatlik işin sonunda, senaryo üretiminin hemen ardından, tüm `pending` senaryolar gerçek
mum verisiyle değerlendirilir: hedefe ulaştıysa `hit_target`, stop'a vurduysa `hit_stop`
(aynı mumda ikisi de gerçekleşmişse stop öncelikli sayılır), süresi dolmuşsa `expired`
olarak işaretlenir. Ardından yön + confidence aralığı desenine göre geçmiş başarı oranı
hesaplanır (`hit_target / (hit_target + hit_stop + expired)`); bir desen için en az 20
çözümlenmiş örnek varsa bu oran, henüz kalibre edilmemiş senaryolara `calibrated_confidence`
olarak yazılır — yetersiz veri varsa ham `confidence_score` kullanılır. `calibrated_confidence`
bir kez atanır ve tekrar üzerine yazılmaz.
```

Then update the existing "Test" section's line about what the tests exercise, if it names specific behavior that's now incomplete — read the current wording first and adjust minimally rather than rewriting the section.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document the confidence/learning loop"
```

---

## Post-Plan Notes

- This plan covers Subsystem C only (confidence/learning loop), per `docs/superpowers/specs/2026-08-22-confidence-learning-loop-design.md`. Subsystem D (paper testing, which will read `Scenario.calibrated_confidence` and `Scenario.status`) is a separate spec/plan, to be brainstormed after this one is implemented and verified.
- Manual verification after Task 8 (not automated, requires a live Postgres + internet, and enough accumulated scenario history): run `python -m src.main`, wait for pending scenarios to resolve across a few hourly runs, and confirm `scenarios.status`/`resolved_at`/`calibrated_confidence` populate correctly. Meaningful calibration (a bucket crossing the 20-sample threshold) will take longer to observe — that's expected, not a bug; log each run's `scanned`/`resolved`/`scenarios_calibrated` counts (already added in Task 6) so an operator can distinguish "not enough data yet" from "the pipeline is stuck."
