# Faz 1.C Risk Geometry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Kaldıraç tavanında kaydedilen riski gerçek stop kaybına eşitlemek ve production ile replay'e aynı minimum stop mesafesi kapısını uygulamak.

**Architecture:** `paper_sizer` tavan sonrası gerçek riski döndürür. `scenario_builder` ortak `MIN_STOP_PCT` sabitinin sahibi olur; production builder dar stop'u reddeder, runner sebebi görünür biçimde loglar ve replay aynı sabiti yeniden dışa aktarır. Her kaynak dosya ayrı `coder-worker` görevidir.

**Tech Stack:** Python, Decimal, pytest, coder-worker (`motor: "ollama"`).

**Spec:** `docs/superpowers/specs/2026-08-31-audit-remediation-design.md` — Faz 1.C.

## Global Constraints

- `float` yasak; oran ve parasal değerler `Decimal`.
- İşler yalnızca `motor: "ollama"` kullanır.
- Worker test, git, bağımlılık veya görev dışı dosya değiştiremez.
- Mevcut imzalar korunur; yalnızca açıkça belirtilen `MIN_STOP_PCT` sabiti eklenir.

---

### Task 1: Record actual risk after leverage clamping

**Files:**
- Modify: `tests/test_paper_sizer.py`
- Modify: `src/paper_sizer.py`

**Interfaces:**
- Preserves: `size_position(equity, entry_price, stop_price, risk_pct) -> (risk_amount, position_size)`

- [ ] **Step 1: Change the capped-position expectation**

For equity 10000, entry 100, stop 99.99 and risk 1%, assert:

```python
assert position_size == Decimal("300")
assert risk_amount == Decimal("3.00")
assert risk_amount == position_size * abs(Decimal("100") - Decimal("99.99"))
```

Add a boundary test where the requested size equals the cap and confirm the returned risk remains `equity * risk_pct`.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_paper_sizer.py -q`

Expected: capped-position test gets `100` instead of `3.00`.

- [ ] **Step 3: Prove the reference behavior and restore the old body**

Reference minimal implementation:

```python
target_risk = equity * risk_pct
requested_size = target_risk / abs(entry_price - stop_price)
max_size = equity * MAX_LEVERAGE / entry_price
position_size = min(requested_size, max_size)
risk_amount = position_size * abs(entry_price - stop_price)
return risk_amount, position_size
```

Run targeted tests green, then restore the old source so the worker receives a red contract.

- [ ] **Step 4: Commit red tests**

Commit only `tests/test_paper_sizer.py`.

- [ ] **Step 5: Run a one-file coder-worker job**

Job requirements: `motor: "ollama"`, `files: ["src/paper_sizer.py"]`, context target test, and exact formula above. Worker must preserve validation and signature.

- [ ] **Step 6: Independently verify and commit**

Run targeted and full pytest, review diff, commit `src/paper_sizer.py`.

---

### Task 2: Reject fee-dominant stops in production

**Files:**
- Modify: `tests/test_scenario_builder.py`
- Modify: `src/scenario_builder.py`

**Interfaces:**
- Produces: `MIN_STOP_PCT = Decimal("0.005")`
- Preserves: `build_scenario(symbol, signal, klines, now)`

- [ ] **Step 1: Add boundary tests**

Patch `find_swing_points` to return explicit `SwingPoint` values so the test exercises real `build_scenario` geometry with entry 100:

```python
assert build_with_stop(Decimal("99.6")) is None       # 0.004
assert build_with_stop(Decimal("99.5")) is not None   # exact 0.005
assert build_with_stop(Decimal("99")) is not None     # 0.01
```

Also assert `MIN_STOP_PCT == Decimal("0.005")` because downstream replay imports this public strategy parameter.

- [ ] **Step 2: Verify RED**

Expected: import of `MIN_STOP_PCT` fails or the 0.004 draft is returned.

- [ ] **Step 3: Prove and restore reference behavior**

Add the constant and replace `if risk == 0` with:

```python
if risk == 0 or risk / entry < MIN_STOP_PCT:
    return None
```

Run targeted tests green, then restore production body/constant to the pre-worker red state while keeping tests.

- [ ] **Step 4: Commit tests and run coder-worker**

Worker job: one source file, exact constant and boundary semantics, `motor: "ollama"`.

- [ ] **Step 5: Verify and commit**

Run target and full suite; reject float, changed signature, or unrelated edits.

---

### Task 3: Make the production rejection observable

**Files:**
- Modify: `tests/test_scenario_runner.py`
- Modify: `src/scenario_runner.py`

**Interfaces:**
- Consumes: `build_scenario(...) -> None` for invalid geometry.
- Preserves: return value `"skipped"`.

- [ ] **Step 1: Add a caplog regression test**

Use the existing real signal fixture and patch only `build_scenario` to return `None`. Assert outcome is `"skipped"` and a DEBUG record contains both the symbol and `stop_too_tight`.

- [ ] **Step 2: Verify RED**

Expected: outcome is skipped but no diagnostic record exists.

- [ ] **Step 3: Implement via one-file coder-worker task**

In the existing `draft is None` branch add:

```python
logger.debug("Skipping %s: stop_too_tight", symbol)
```

No try/except or other branch may change.

- [ ] **Step 4: Verify targeted/full tests and commit**

---

### Task 4: Re-export the production stop threshold to replay

**Files:**
- Modify: `tests/test_funnel.py`
- Modify: `src/research/funnel.py`

**Interfaces:**
- Consumes: `src.scenario_builder.MIN_STOP_PCT`
- Produces: `src.research.funnel.MIN_STOP_PCT` as the same Decimal object.

- [ ] **Step 1: Add a failing public-interface test**

```python
from src.scenario_builder import MIN_STOP_PCT as PRODUCTION_MIN_STOP_PCT
from src.research.funnel import MIN_STOP_PCT as REPLAY_MIN_STOP_PCT

assert REPLAY_MIN_STOP_PCT is PRODUCTION_MIN_STOP_PCT
```

- [ ] **Step 2: Verify RED**

Expected: import fails before the funnel re-export exists.

- [ ] **Step 3: Add only the import with coder-worker**

Add `from src.scenario_builder import MIN_STOP_PCT` without changing `passes_risk_filters` or other replay behavior. The 391-line file gets no test file in worker context; the goal names the exact import and location.

- [ ] **Step 4: Verify target/full tests and commit**
