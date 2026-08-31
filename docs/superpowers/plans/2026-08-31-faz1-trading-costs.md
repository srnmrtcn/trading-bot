# Faz 1.D Trading Costs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Paper ve replay katmanlarının ortak kullanacağı Decimal tabanlı fee, slippage ve funding maliyeti çekirdeğini oluşturmak.

**Architecture:** Yeni `src/trading_costs.py` modülü DB ve diğer `src` modüllerinden bağımsız üç saf fonksiyon sunar. Yönetici testleri ve kesin imzalı stub'ları hazırlar, referans implementasyonla kapının geçilebilirliğini kanıtlar, kaynağı yeniden stub'a döndürür; yalnızca fonksiyon gövdeleri `coder-worker` (`motor: "ollama"`) tarafından doldurulur.

**Tech Stack:** Python 3.14, `Decimal`, pytest, yerel `coder-worker`/Ollama.

**Spec:** `docs/superpowers/specs/2026-08-31-audit-remediation-design.md` — Faz 1.D.

## Global Constraints

- Bütün parasal hesaplar `Decimal`; `float` yasak.
- `coder-worker` yalnızca `src/trading_costs.py` dosyasını yazabilir; `tests/`, git ve bağımlılık dosyaları yasak.
- İş JSON'unda `motor: "ollama"` açıkça yazılır; `qwen`, `uret`, `coder-ajan` kullanılmaz.
- `TAKER_FEE_RATE = Decimal("0.0005")` ve `SLIPPAGE_BPS = Decimal("5")` modülün tek kaynak sabitleridir.
- Test kapısı `python -m pytest tests/test_trading_costs.py -q` komutudur.

---

### Task 1: Contract tests and worker stubs

**Files:**
- Create: `tests/test_trading_costs.py`
- Create: `src/trading_costs.py`

**Interfaces:**
- Produces: `round_trip_cost(position_size: Decimal, entry_price: Decimal, exit_price: Decimal) -> Decimal`
- Produces: `fee_and_slippage_cost_in_r(entry_price: Decimal, stop_price: Decimal) -> Decimal`
- Produces: `funding_cost(direction: str, position_size: Decimal, events: list[tuple[datetime, Decimal, Decimal]]) -> Decimal`

- [ ] **Step 1: Write the failing contract tests**

Create tests covering these exact assertions:

```python
from datetime import datetime
from decimal import Decimal

import pytest

from src.trading_costs import (
    SLIPPAGE_BPS,
    TAKER_FEE_RATE,
    fee_and_slippage_cost_in_r,
    funding_cost,
    round_trip_cost,
)


def test_constants_define_the_single_cost_model():
    assert TAKER_FEE_RATE == Decimal("0.0005")
    assert SLIPPAGE_BPS == Decimal("5")


def test_round_trip_cost_uses_each_legs_own_notional():
    assert round_trip_cost(Decimal("10"), Decimal("100"), Decimal("110")) == Decimal("2.100")


def test_round_trip_cost_is_zero_for_zero_size():
    result = round_trip_cost(Decimal("0"), Decimal("100"), Decimal("110"))
    assert result == Decimal("0")
    assert isinstance(result, Decimal)


@pytest.mark.parametrize("stop", [Decimal("99"), Decimal("101")])
def test_cost_in_r_is_direction_agnostic(stop):
    assert fee_and_slippage_cost_in_r(Decimal("100"), stop) == Decimal("0.2")


def test_cost_in_r_exposes_fee_dominant_tight_stops():
    assert fee_and_slippage_cost_in_r(Decimal("100"), Decimal("99.9")) == Decimal("2.0")


def test_cost_in_r_rejects_zero_risk():
    with pytest.raises(ValueError, match="stop equals entry"):
        fee_and_slippage_cost_in_r(Decimal("100"), Decimal("100"))


def test_funding_cost_long_pays_positive_and_receives_negative_rates():
    events = [
        (datetime(2026, 8, 31, 8), Decimal("0.0001"), Decimal("100")),
        (datetime(2026, 8, 31, 16), Decimal("-0.0002"), Decimal("100")),
    ]
    assert funding_cost("long", Decimal("10"), events) == Decimal("-0.1")


def test_funding_cost_short_has_the_opposite_sign():
    event = [(datetime(2026, 8, 31, 8), Decimal("0.0001"), Decimal("100"))]
    assert funding_cost("short", Decimal("10"), event) == Decimal("-0.1")


def test_funding_cost_empty_events_is_decimal_zero():
    result = funding_cost("long", Decimal("10"), [])
    assert result == Decimal("0")
    assert isinstance(result, Decimal)


def test_funding_cost_validates_direction_even_when_events_are_empty():
    with pytest.raises(ValueError, match="direction"):
        funding_cost("invalid", Decimal("10"), [])
```

- [ ] **Step 2: Run the test before creating the module**

Run: `python -m pytest tests/test_trading_costs.py -q`

Expected: collection error `ModuleNotFoundError: No module named 'src.trading_costs'`.

- [ ] **Step 3: Create the exact worker stub**

```python
"""Shared trading-cost calculations for paper trading and replay."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal


TAKER_FEE_RATE = Decimal("0.0005")
SLIPPAGE_BPS = Decimal("5")


def round_trip_cost(
    position_size: Decimal,
    entry_price: Decimal,
    exit_price: Decimal,
) -> Decimal:
    """Return entry and exit taker fees plus assumed slippage."""
    raise NotImplementedError


def fee_and_slippage_cost_in_r(
    entry_price: Decimal,
    stop_price: Decimal,
) -> Decimal:
    """Return approximate round-trip fee and slippage cost in risk units."""
    raise NotImplementedError


def funding_cost(
    direction: str,
    position_size: Decimal,
    events: list[tuple[datetime, Decimal, Decimal]],
) -> Decimal:
    """Return signed funding cost; positive means paid and negative received."""
    raise NotImplementedError
```

- [ ] **Step 4: Verify the stub is red for implementation reasons**

Run: `python -m pytest tests/test_trading_costs.py -q`

Expected: tests collect; constants pass; function tests fail with `NotImplementedError`.

- [ ] **Step 5: Temporarily implement the reference formulas**

Reference behavior:

```python
cost_rate = TAKER_FEE_RATE + SLIPPAGE_BPS / Decimal("10000")
round_trip = position_size * entry_price * cost_rate + position_size * exit_price * cost_rate
cost_in_r = Decimal("2") * entry_price * cost_rate / abs(entry_price - stop_price)
event_payment = funding_rate * position_size * mark_price
signed_payment = event_payment if direction == "long" else -event_payment
```

Validate `direction` before the empty-event fast path and raise `ValueError("invalid direction")`. Raise `ValueError("stop equals entry")` when risk is zero.

- [ ] **Step 6: Prove the contract is passable**

Run: `python -m pytest tests/test_trading_costs.py -q`

Expected: all tests pass.

- [ ] **Step 7: Restore only the three function bodies to `raise NotImplementedError`**

Run: `python -m pytest tests/test_trading_costs.py -q`

Expected: constants pass; implementation tests fail with `NotImplementedError`.

- [ ] **Step 8: Commit the red contract and stub**

```bash
git add tests/test_trading_costs.py src/trading_costs.py
git commit -m "test: define shared trading cost contract"
```

---

### Task 2: Fill the stubs with `coder-worker`

**Files:**
- Modify: `src/trading_costs.py`
- Test: `tests/test_trading_costs.py`

**Interfaces:**
- Consumes and preserves all three signatures from Task 1.
- Produces Decimal-only cost values used by later paper/replay tasks.

- [ ] **Step 1: Create a watcher job with the exact motor**

The job must contain:

```json
{
  "id": "tb-1d-trading-costs-worker",
  "proje": "C:/Users/srnmr/trading-bot",
  "motor": "ollama",
  "tasks": [
    {
      "id": "trading-costs",
      "files": ["src/trading_costs.py"],
      "context": ["tests/test_trading_costs.py"],
      "verify": "python -m pytest tests/test_trading_costs.py -q",
      "max_attempts": 4
    }
  ]
}
```

The `goal` must repeat every formula from Task 1 and explicitly prohibit changing constants, signatures, imports, tests, or adding files.

- [ ] **Step 2: Place the job in `C:\ajan\_ajan\inbox` and wait for the watcher result**

Expected result: `motor == "ollama"`, task `trading-costs` passes, and only `src/trading_costs.py` changes.

- [ ] **Step 3: Independently verify worker output**

Run: `python -m pytest tests/test_trading_costs.py -q`

Run: `python -m pytest -q`

Expected: targeted contract and full suite pass.

- [ ] **Step 4: Review the diff**

Reject if the worker changes a signature, duplicates constants inside functions, uses `float`, accepts an invalid direction, or imports DB/application modules.

- [ ] **Step 5: Commit the implementation**

```bash
git add src/trading_costs.py
git commit -m "feat: add shared trading cost calculations"
```
