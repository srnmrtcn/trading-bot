# Paper Test Portföyü Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After every hourly scheduler job, run a simulated paper-trading portfolio: open sized positions on high-confidence `pending` scenarios, and close positions whose scenario has resolved, tracking equity through the position history itself.

**Architecture:** A pure sizing function (`src/paper_sizer.py`), a small DB-aware equity lookup (`src/paper_equity.py`), two DB-orchestrating modules (`src/paper_position_closer.py`, `src/paper_position_opener.py`), and a thin orchestrator (`src/paper_trading_runner.py`) — the same layering Subsystems B and C used. Wired into `run_timeframe_job("1h")` in `src/scheduler.py`, right after Subsystem C's learning cycle.

**Tech Stack:** Same as Subsystems A/B/C — Python 3.9, SQLAlchemy 2.0 ORM, `Decimal` for all price/ratio/equity math, pytest with `sqlite:///:memory:`.

**Spec:** `docs/superpowers/specs/2026-08-23-paper-trading-design.md`

## Global Constraints

- Python 3.9 compatibility — every file using `X | None` / `list[X]` style annotations MUST start with `from __future__ import annotations`.
- All price, ratio, and equity arithmetic uses `Decimal`, never `float`.
- A position's opening/closing failure must never abort processing of other positions — mirror the existing per-item `try/except` + `session.rollback()` + `logger.exception` isolation pattern already established in `src/scenario_runner.py` and `src/learning_runner.py`.
- `STARTING_EQUITY = Decimal("10000")` — nominal reference amount; only percentage-based outcomes are meaningful.
- `RISK_PCT = Decimal("0.01")` (1% of current equity risked per trade, fixed-fractional sizing).
- `CONFIDENCE_THRESHOLD = Decimal("0.65")` — a scenario needs `calibrated_confidence >= 0.65` to qualify for a paper position; `calibrated_confidence IS NULL` never qualifies.
- `MAX_CONCURRENT_POSITIONS = 10` — the cap on simultaneously open paper positions.
- At most one open paper position per symbol at a time.
- A `Scenario` gets at most one `PaperPosition` ever (`scenario_id` is unique on `paper_positions`).
- Each 1h cycle closes resolved positions **before** opening new ones, so freed capital is reflected in new positions' sizing.
- Equity is never stored as a separate running total: the most recently **closed** position's `equity_after` (or `STARTING_EQUITY` if none has ever closed) IS the current equity.
- `hit_target` closes at `target_price`; `hit_stop` closes at `stop_price`; `expired` closes at the `close` of the nearest closed 1h candle at or before `expires_at` — if no such candle is stored yet, the position stays open and is retried next run.

---

## File Structure

```
src/
├── paper_trading_config.py   (new)  constants: STARTING_EQUITY, RISK_PCT, CONFIDENCE_THRESHOLD, MAX_CONCURRENT_POSITIONS
├── paper_sizer.py             (new)  pure: fixed-fractional position sizing
├── paper_equity.py            (new)  DB: current portfolio equity
├── paper_position_closer.py   (new)  DB: settle positions whose scenario resolved
├── paper_position_opener.py   (new)  DB: open positions for qualifying pending scenarios
├── paper_trading_runner.py    (new)  DB: orchestrates closer then opener
├── scheduler.py                (modify)  hook the paper trading cycle into the 1h job
└── db/
    └── models.py               (modify)  add PaperPosition table
tests/
├── test_paper_sizer.py             (new)
├── test_paper_equity.py            (new)
├── test_paper_position_closer.py   (new)
├── test_paper_position_opener.py   (new)
├── test_paper_trading_runner.py    (new)
├── test_models.py                   (modify)  PaperPosition defaults + unique constraint
└── test_scheduler.py                 (modify)  paper-trading wiring tests + log-line updates
```

---

### Task 1: `PaperPosition` model

**Files:**
- Modify: `src/db/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: `db.models.PaperPosition` — see column list below. `scenario_id` is unique (a scenario gets at most one paper position ever).

- [ ] **Step 1: Write the failing test**

Add to the end of `tests/test_models.py` (the file already imports `pytest`, `IntegrityError`, `Scenario`, `datetime`, `timedelta`, `Decimal`):

```python
def test_paper_position_defaults_and_unique_scenario_constraint(db_session):
    from src.db.models import PaperPosition

    now = datetime(2026, 1, 1, 12, 0, 0)
    scenario = Scenario(
        symbol="BTCUSDT", direction="long",
        entry_price=Decimal("50000"), target_price=Decimal("52000"), stop_price=Decimal("49000"),
        expected_return_pct=Decimal("0.04"), confidence_score=Decimal("0.7"),
        created_at=now, expires_at=now + timedelta(hours=24), status="pending",
    )
    db_session.add(scenario)
    db_session.commit()

    db_session.add(PaperPosition(
        scenario_id=scenario.id, symbol="BTCUSDT", direction="long",
        entry_price=Decimal("50000"), stop_price=Decimal("49000"), target_price=Decimal("52000"),
        risk_amount=Decimal("100"), position_size=Decimal("0.1"),
        opened_at=now, status="open",
    ))
    db_session.commit()

    reloaded = db_session.query(PaperPosition).first()
    assert reloaded.status == "open"
    assert reloaded.closed_at is None
    assert reloaded.exit_price is None
    assert reloaded.realized_pnl is None
    assert reloaded.equity_before is None
    assert reloaded.equity_after is None

    db_session.add(PaperPosition(
        scenario_id=scenario.id, symbol="BTCUSDT", direction="long",
        entry_price=Decimal("50000"), stop_price=Decimal("49000"), target_price=Decimal("52000"),
        risk_amount=Decimal("100"), position_size=Decimal("0.1"),
        opened_at=now, status="open",
    ))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_models.py -v -k paper_position`
Expected: FAIL (`ImportError: cannot import name 'PaperPosition'`)

- [ ] **Step 3: Write minimal implementation**

In `src/db/models.py`, change the import line at the top from:

```python
from sqlalchemy import Boolean, Column, DateTime, Integer, Numeric, String, Text, UniqueConstraint
```

to:

```python
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
```

Then add this class to the end of the file:

```python
class PaperPosition(Base):
    __tablename__ = "paper_positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    scenario_id = Column(Integer, ForeignKey("scenarios.id"), nullable=False, unique=True)
    symbol = Column(String, nullable=False, index=True)
    direction = Column(String, nullable=False)
    entry_price = Column(Numeric(20, 8), nullable=False)
    stop_price = Column(Numeric(20, 8), nullable=False)
    target_price = Column(Numeric(20, 8), nullable=False)
    risk_amount = Column(Numeric(20, 8), nullable=False)
    position_size = Column(Numeric(20, 8), nullable=False)
    opened_at = Column(DateTime, nullable=False)
    status = Column(String, nullable=False, default="open")
    closed_at = Column(DateTime, nullable=True)
    exit_price = Column(Numeric(20, 8), nullable=True)
    realized_pnl = Column(Numeric(20, 8), nullable=True)
    equity_before = Column(Numeric(20, 8), nullable=True)
    equity_after = Column(Numeric(20, 8), nullable=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/db/models.py tests/test_models.py
git commit -m "feat: add PaperPosition model"
```

---

### Task 2: Config constants + Position Sizer (pure)

**Files:**
- Create: `src/paper_trading_config.py`
- Create: `src/paper_sizer.py`
- Test: `tests/test_paper_sizer.py`

**Interfaces:**
- Produces:
  - `paper_trading_config.STARTING_EQUITY = Decimal("10000")`
  - `paper_trading_config.RISK_PCT = Decimal("0.01")`
  - `paper_trading_config.CONFIDENCE_THRESHOLD = Decimal("0.65")`
  - `paper_trading_config.MAX_CONCURRENT_POSITIONS = 10`
  - `paper_sizer.size_position(equity: Decimal, entry_price: Decimal, stop_price: Decimal, risk_pct: Decimal) -> tuple[Decimal, Decimal]` — returns `(risk_amount, position_size)`. Raises `ValueError` if `entry_price == stop_price`.

- [ ] **Step 1: Write the failing tests**

`tests/test_paper_sizer.py`:
```python
from decimal import Decimal

import pytest

from src.paper_sizer import size_position


def test_size_position_long():
    risk_amount, position_size = size_position(
        equity=Decimal("10000"), entry_price=Decimal("100"), stop_price=Decimal("90"), risk_pct=Decimal("0.01"),
    )
    assert risk_amount == Decimal("100")
    assert position_size == Decimal("10")  # 100 / |100 - 90|


def test_size_position_short():
    risk_amount, position_size = size_position(
        equity=Decimal("10000"), entry_price=Decimal("100"), stop_price=Decimal("110"), risk_pct=Decimal("0.01"),
    )
    assert risk_amount == Decimal("100")
    assert position_size == Decimal("10")  # 100 / |100 - 110|


def test_size_position_scales_with_equity():
    risk_amount, position_size = size_position(
        equity=Decimal("20000"), entry_price=Decimal("100"), stop_price=Decimal("90"), risk_pct=Decimal("0.01"),
    )
    assert risk_amount == Decimal("200")
    assert position_size == Decimal("20")


def test_size_position_raises_when_entry_equals_stop():
    with pytest.raises(ValueError):
        size_position(equity=Decimal("10000"), entry_price=Decimal("100"), stop_price=Decimal("100"), risk_pct=Decimal("0.01"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_paper_sizer.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.paper_sizer'`)

- [ ] **Step 3: Write minimal implementation**

`src/paper_trading_config.py`:
```python
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
```

`src/paper_sizer.py`:
```python
from __future__ import annotations

from decimal import Decimal


def size_position(equity: Decimal, entry_price: Decimal, stop_price: Decimal, risk_pct: Decimal) -> tuple:
    """Fixed-fractional position sizing.

    Sizes the position so that if price reaches `stop_price`, the loss equals
    exactly `equity * risk_pct`. Returns `(risk_amount, position_size)`.
    """
    if entry_price == stop_price:
        raise ValueError("entry_price and stop_price must differ")
    risk_amount = equity * risk_pct
    position_size = risk_amount / abs(entry_price - stop_price)
    return risk_amount, position_size
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_paper_sizer.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/paper_trading_config.py src/paper_sizer.py tests/test_paper_sizer.py
git commit -m "feat: add paper trading config and fixed-fractional position sizer"
```

---

### Task 3: Equity helper

**Files:**
- Create: `src/paper_equity.py`
- Test: `tests/test_paper_equity.py`

**Interfaces:**
- Consumes: `db.models.PaperPosition` (Task 1), `paper_trading_config.STARTING_EQUITY` (Task 2)
- Produces: `paper_equity.current_equity(session) -> Decimal`

- [ ] **Step 1: Write the failing tests**

`tests/test_paper_equity.py`:
```python
from datetime import datetime
from decimal import Decimal

from src.db.models import PaperPosition, Scenario
from src.paper_equity import current_equity
from src.paper_trading_config import STARTING_EQUITY


def _scenario(symbol):
    now = datetime(2026, 1, 1)
    return Scenario(
        symbol=symbol, direction="long",
        entry_price=Decimal("100"), target_price=Decimal("110"), stop_price=Decimal("90"),
        expected_return_pct=Decimal("0.1"), confidence_score=Decimal("0.7"),
        created_at=now, expires_at=now, status="pending",
    )


def _closed_position(scenario, equity_after, closed_at):
    return PaperPosition(
        scenario_id=scenario.id, symbol=scenario.symbol, direction="long",
        entry_price=Decimal("100"), stop_price=Decimal("90"), target_price=Decimal("110"),
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=datetime(2026, 1, 1), status="closed",
        closed_at=closed_at, exit_price=Decimal("110"), realized_pnl=equity_after - STARTING_EQUITY,
        equity_before=STARTING_EQUITY, equity_after=equity_after,
    )


def _open_position(scenario):
    return PaperPosition(
        scenario_id=scenario.id, symbol=scenario.symbol, direction="long",
        entry_price=Decimal("100"), stop_price=Decimal("90"), target_price=Decimal("110"),
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=datetime(2026, 1, 1), status="open",
    )


def test_current_equity_returns_starting_equity_when_nothing_closed(db_session):
    assert current_equity(db_session) == STARTING_EQUITY


def test_current_equity_returns_the_most_recently_closed_positions_equity_after(db_session):
    s1 = _scenario("BTCUSDT")
    s2 = _scenario("ETHUSDT")
    db_session.add_all([s1, s2])
    db_session.commit()

    db_session.add(_closed_position(s1, Decimal("10100"), closed_at=datetime(2026, 1, 1, 10)))
    db_session.add(_closed_position(s2, Decimal("10250"), closed_at=datetime(2026, 1, 1, 11)))
    db_session.commit()

    assert current_equity(db_session) == Decimal("10250")


def test_current_equity_ignores_open_positions(db_session):
    s1 = _scenario("BTCUSDT")
    db_session.add(s1)
    db_session.commit()
    db_session.add(_open_position(s1))
    db_session.commit()

    assert current_equity(db_session) == STARTING_EQUITY
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_paper_equity.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.paper_equity'`)

- [ ] **Step 3: Write minimal implementation**

`src/paper_equity.py`:
```python
from __future__ import annotations

from decimal import Decimal

from src.db.models import PaperPosition
from src.paper_trading_config import STARTING_EQUITY


def current_equity(session) -> Decimal:
    """The simulated portfolio's current equity.

    There is no separate running total to keep in sync: each closed
    PaperPosition row already records the equity it produced (`equity_after`),
    so the most recently closed position's `equity_after` IS the current
    equity. Before any position has ever closed, equity is the starting
    constant.
    """
    last_closed = (
        session.query(PaperPosition)
        .filter(PaperPosition.status == "closed")
        .order_by(PaperPosition.closed_at.desc(), PaperPosition.id.desc())
        .first()
    )
    return last_closed.equity_after if last_closed is not None else STARTING_EQUITY
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_paper_equity.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/paper_equity.py tests/test_paper_equity.py
git commit -m "feat: add current-equity lookup derived from closed positions"
```

---

### Task 4: Position Closer

**Files:**
- Create: `src/paper_position_closer.py`
- Test: `tests/test_paper_position_closer.py`

**Interfaces:**
- Consumes: `db.models.Kline, PaperPosition, Scenario` (Subsystem A, Task 1); `paper_equity.current_equity` (Task 3); `timeutil.utc_now` (Subsystem A)
- Produces: `paper_position_closer.PositionCloseResult(scanned: int, closed: int, still_open: int, failed: int)`, `paper_position_closer.close_resolved_positions(session, now: datetime = None) -> PositionCloseResult`. Internal helpers `_exit_price_for_expired` and `_realized_pnl` are module-level (later tasks and tests reference them by name for monkeypatching).

- [ ] **Step 1: Write the failing tests**

`tests/test_paper_position_closer.py`:
```python
from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import Kline, PaperPosition, Scenario
from src.paper_position_closer import close_resolved_positions
from src.paper_trading_config import STARTING_EQUITY


def _scenario(symbol="BTCUSDT", direction="long", status="pending",
              target_price=Decimal("110"), stop_price=Decimal("90"),
              created_at=None, expires_at=None):
    created_at = created_at or datetime(2026, 1, 1)
    expires_at = expires_at or created_at + timedelta(hours=24)
    return Scenario(
        symbol=symbol, direction=direction,
        entry_price=Decimal("100"), target_price=target_price, stop_price=stop_price,
        expected_return_pct=Decimal("0.1"), confidence_score=Decimal("0.7"),
        created_at=created_at, expires_at=expires_at, status=status,
        calibrated_confidence=Decimal("0.7"),
    )


def _open_position(scenario, size=Decimal("10")):
    return PaperPosition(
        scenario_id=scenario.id, symbol=scenario.symbol, direction=scenario.direction,
        entry_price=scenario.entry_price, stop_price=scenario.stop_price, target_price=scenario.target_price,
        risk_amount=Decimal("100"), position_size=size,
        opened_at=scenario.created_at, status="open",
    )


def test_close_resolved_positions_closes_a_hit_target_long(db_session):
    scenario = _scenario(status="hit_target")
    db_session.add(scenario)
    db_session.commit()
    db_session.add(_open_position(scenario))
    db_session.commit()

    result = close_resolved_positions(db_session, now=datetime(2026, 1, 2))

    assert result.scanned == 1
    assert result.closed == 1
    assert result.still_open == 0
    assert result.failed == 0
    reloaded = db_session.query(PaperPosition).first()
    assert reloaded.status == "closed"
    assert reloaded.exit_price == Decimal("110")
    assert reloaded.realized_pnl == Decimal("100")  # 10 * (110 - 100)
    assert reloaded.equity_before == STARTING_EQUITY
    assert reloaded.equity_after == STARTING_EQUITY + Decimal("100")
    assert reloaded.closed_at == datetime(2026, 1, 2)


def test_close_resolved_positions_closes_a_hit_stop_short(db_session):
    scenario = _scenario(direction="short", status="hit_stop", target_price=Decimal("90"), stop_price=Decimal("110"))
    db_session.add(scenario)
    db_session.commit()
    db_session.add(_open_position(scenario))
    db_session.commit()

    result = close_resolved_positions(db_session)

    assert result.closed == 1
    reloaded = db_session.query(PaperPosition).first()
    assert reloaded.exit_price == Decimal("110")
    assert reloaded.realized_pnl == Decimal("-100")  # 10 * (100 - 110)
    assert reloaded.equity_after == STARTING_EQUITY - Decimal("100")


def test_close_resolved_positions_marks_to_market_on_expiry(db_session):
    scenario = _scenario(status="expired", expires_at=datetime(2026, 1, 2))
    db_session.add(scenario)
    db_session.commit()
    db_session.add(_open_position(scenario))
    db_session.add(Kline(
        symbol="BTCUSDT", timeframe="1h", open_time=datetime(2026, 1, 1, 23),
        open=Decimal("100"), high=Decimal("106"), low=Decimal("99"), close=Decimal("105"),
        volume=Decimal("1000"), flagged=False,
    ))
    db_session.commit()

    result = close_resolved_positions(db_session)

    assert result.closed == 1
    reloaded = db_session.query(PaperPosition).first()
    assert reloaded.exit_price == Decimal("105")
    assert reloaded.realized_pnl == Decimal("50")  # 10 * (105 - 100)


def test_close_resolved_positions_defers_expiry_with_no_kline_data(db_session):
    scenario = _scenario(status="expired", expires_at=datetime(2026, 1, 2))
    db_session.add(scenario)
    db_session.commit()
    db_session.add(_open_position(scenario))
    db_session.commit()

    result = close_resolved_positions(db_session)

    assert result.scanned == 1
    assert result.closed == 0
    assert result.still_open == 1
    reloaded = db_session.query(PaperPosition).first()
    assert reloaded.status == "open"


def test_close_resolved_positions_leaves_pending_scenario_positions_untouched(db_session):
    scenario = _scenario(status="pending")
    db_session.add(scenario)
    db_session.commit()
    db_session.add(_open_position(scenario))
    db_session.commit()

    result = close_resolved_positions(db_session)

    assert result.scanned == 0
    reloaded = db_session.query(PaperPosition).first()
    assert reloaded.status == "open"


def test_close_resolved_positions_chains_equity_across_two_closes(db_session):
    s1 = _scenario(symbol="BTCUSDT", status="hit_target", created_at=datetime(2026, 1, 1, 0))
    s2 = _scenario(symbol="ETHUSDT", status="hit_target", created_at=datetime(2026, 1, 1, 1))
    db_session.add_all([s1, s2])
    db_session.commit()
    db_session.add(_open_position(s1))
    db_session.add(_open_position(s2))
    db_session.commit()

    result = close_resolved_positions(db_session)

    assert result.closed == 2
    first = db_session.query(PaperPosition).filter(PaperPosition.symbol == "BTCUSDT").first()
    second = db_session.query(PaperPosition).filter(PaperPosition.symbol == "ETHUSDT").first()
    assert first.equity_before == STARTING_EQUITY
    assert second.equity_before == first.equity_after
    assert second.equity_after == first.equity_after + Decimal("100")


def test_close_resolved_positions_isolates_a_failing_position(db_session, monkeypatch):
    good = _scenario(symbol="BTCUSDT", status="hit_target", created_at=datetime(2026, 1, 1, 0))
    bad = _scenario(
        symbol="ETHUSDT", status="hit_target", target_price=Decimal("999"),
        created_at=datetime(2026, 1, 1, 1),
    )
    db_session.add_all([good, bad])
    db_session.commit()
    db_session.add(_open_position(good))
    db_session.add(_open_position(bad))
    db_session.commit()

    import src.paper_position_closer as closer_module
    real_realized_pnl = closer_module._realized_pnl

    def flaky_realized_pnl(direction, position_size, entry_price, exit_price):
        if exit_price == Decimal("999"):
            raise RuntimeError("boom")
        return real_realized_pnl(direction, position_size, entry_price, exit_price)

    monkeypatch.setattr(closer_module, "_realized_pnl", flaky_realized_pnl)

    result = close_resolved_positions(db_session)

    assert result.scanned == 2
    assert result.closed == 1
    assert result.failed == 1
    good_reloaded = db_session.query(PaperPosition).filter(PaperPosition.symbol == "BTCUSDT").first()
    bad_reloaded = db_session.query(PaperPosition).filter(PaperPosition.symbol == "ETHUSDT").first()
    assert good_reloaded.status == "closed"
    assert bad_reloaded.status == "open"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_paper_position_closer.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.paper_position_closer'`)

- [ ] **Step 3: Write minimal implementation**

`src/paper_position_closer.py`:
```python
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from src.db.models import Kline, PaperPosition, Scenario
from src.paper_equity import current_equity
from src.timeutil import utc_now

logger = logging.getLogger("paper_position_closer")

RESOLUTION_TIMEFRAME = "1h"


@dataclass
class PositionCloseResult:
    scanned: int
    closed: int
    still_open: int
    failed: int


def _exit_price_for_expired(session, symbol: str, expires_at: datetime):
    row = (
        session.query(Kline)
        .filter(
            Kline.symbol == symbol,
            Kline.timeframe == RESOLUTION_TIMEFRAME,
            Kline.open_time <= expires_at,
        )
        .order_by(Kline.open_time.desc())
        .first()
    )
    return row.close if row is not None else None


def _realized_pnl(direction: str, position_size: Decimal, entry_price: Decimal, exit_price: Decimal) -> Decimal:
    if direction == "long":
        return position_size * (exit_price - entry_price)
    return position_size * (entry_price - exit_price)


def close_resolved_positions(session, now: datetime = None) -> PositionCloseResult:
    now = now if now is not None else utc_now()
    open_positions = (
        session.query(PaperPosition)
        .join(Scenario, PaperPosition.scenario_id == Scenario.id)
        .filter(PaperPosition.status == "open", Scenario.status != "pending")
        .order_by(Scenario.created_at.asc(), PaperPosition.id.asc())
        .all()
    )

    scanned = 0
    closed = 0
    still_open = 0
    failed = 0
    for position in open_positions:
        scanned += 1
        # Captured before the try block: after session.rollback() these
        # attributes are expired, and reading them in the except handler
        # would issue a refresh SELECT that raises again on a dead connection.
        position_id = position.id
        symbol = position.symbol
        try:
            scenario = session.get(Scenario, position.scenario_id)
            if scenario.status == "hit_target":
                exit_price = scenario.target_price
            elif scenario.status == "hit_stop":
                exit_price = scenario.stop_price
            else:  # "expired"
                exit_price = _exit_price_for_expired(session, symbol, scenario.expires_at)
                if exit_price is None:
                    logger.info(
                        "Deferring paper position %s (%s): no closed candle at/before expiry yet",
                        position_id, symbol,
                    )
                    still_open += 1
                    continue

            equity_before = current_equity(session)
            realized_pnl = _realized_pnl(position.direction, position.position_size, position.entry_price, exit_price)
            position.exit_price = exit_price
            position.realized_pnl = realized_pnl
            position.equity_before = equity_before
            position.equity_after = equity_before + realized_pnl
            position.closed_at = now
            position.status = "closed"
            session.commit()
            closed += 1
        except Exception:
            session.rollback()
            logger.exception("Closing paper position %s (%s) failed", position_id, symbol)
            failed += 1

    return PositionCloseResult(scanned=scanned, closed=closed, still_open=still_open, failed=failed)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_paper_position_closer.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/paper_position_closer.py tests/test_paper_position_closer.py
git commit -m "feat: add paper position closer with equity chaining"
```

---

### Task 5: Position Opener

**Files:**
- Create: `src/paper_position_opener.py`
- Test: `tests/test_paper_position_opener.py`

**Interfaces:**
- Consumes: `db.models.PaperPosition, Scenario` (Task 1); `paper_equity.current_equity` (Task 3); `paper_sizer.size_position` (Task 2); `paper_trading_config.CONFIDENCE_THRESHOLD, MAX_CONCURRENT_POSITIONS, RISK_PCT` (Task 2)
- Produces: `paper_position_opener.PositionOpenResult(scanned: int, opened: int, skipped: int, failed: int)`, `paper_position_opener.open_qualifying_positions(session, now: datetime = None) -> PositionOpenResult`

- [ ] **Step 1: Write the failing tests**

`tests/test_paper_position_opener.py`:
```python
from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import PaperPosition, Scenario
from src.paper_position_opener import open_qualifying_positions
from src.paper_trading_config import CONFIDENCE_THRESHOLD, RISK_PCT, STARTING_EQUITY


def _pending_scenario(symbol="BTCUSDT", direction="long", calibrated_confidence=Decimal("0.7"),
                       entry_price=Decimal("100"), stop_price=Decimal("90"), target_price=Decimal("110"),
                       created_at=None):
    created_at = created_at or datetime(2026, 1, 1)
    return Scenario(
        symbol=symbol, direction=direction,
        entry_price=entry_price, target_price=target_price, stop_price=stop_price,
        expected_return_pct=Decimal("0.1"), confidence_score=calibrated_confidence,
        created_at=created_at, expires_at=created_at + timedelta(hours=24), status="pending",
        calibrated_confidence=calibrated_confidence,
    )


def test_opens_a_position_for_a_qualifying_scenario(db_session):
    scenario = _pending_scenario()
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 5))

    assert result.scanned == 1
    assert result.opened == 1
    assert result.skipped == 0
    position = db_session.query(PaperPosition).first()
    assert position.scenario_id == scenario.id
    assert position.status == "open"
    assert position.opened_at == datetime(2026, 1, 1, 5)
    expected_risk = STARTING_EQUITY * RISK_PCT
    assert position.risk_amount == expected_risk
    assert position.position_size == expected_risk / Decimal("10")  # |100 - 90|


def test_skips_a_scenario_below_the_confidence_threshold(db_session):
    scenario = _pending_scenario(calibrated_confidence=CONFIDENCE_THRESHOLD - Decimal("0.01"))
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session)

    assert result.scanned == 0
    assert db_session.query(PaperPosition).count() == 0


def test_skips_a_scenario_with_no_calibrated_confidence_yet(db_session):
    scenario = _pending_scenario()
    scenario.calibrated_confidence = None
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session)

    assert result.scanned == 0
    assert db_session.query(PaperPosition).count() == 0


def test_skips_a_scenario_that_already_has_a_position(db_session):
    scenario = _pending_scenario()
    db_session.add(scenario)
    db_session.commit()
    db_session.add(PaperPosition(
        scenario_id=scenario.id, symbol=scenario.symbol, direction=scenario.direction,
        entry_price=scenario.entry_price, stop_price=scenario.stop_price, target_price=scenario.target_price,
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=datetime(2026, 1, 1), status="closed",
    ))
    db_session.commit()

    result = open_qualifying_positions(db_session)

    assert result.scanned == 0
    assert db_session.query(PaperPosition).count() == 1


def test_skips_a_second_qualifying_scenario_on_the_same_symbol_while_one_is_open(db_session):
    first = _pending_scenario(symbol="BTCUSDT", created_at=datetime(2026, 1, 1, 0))
    second = _pending_scenario(symbol="BTCUSDT", direction="short", created_at=datetime(2026, 1, 1, 1))
    db_session.add_all([first, second])
    db_session.commit()

    result = open_qualifying_positions(db_session)

    assert result.scanned == 2
    assert result.opened == 1
    assert result.skipped == 1
    open_positions = db_session.query(PaperPosition).filter(PaperPosition.status == "open").all()
    assert len(open_positions) == 1
    assert open_positions[0].scenario_id == first.id


def test_stops_opening_once_max_concurrent_positions_is_reached(db_session, monkeypatch):
    monkeypatch.setattr("src.paper_position_opener.MAX_CONCURRENT_POSITIONS", 1)
    first = _pending_scenario(symbol="BTCUSDT", created_at=datetime(2026, 1, 1, 0))
    second = _pending_scenario(symbol="ETHUSDT", created_at=datetime(2026, 1, 1, 1))
    db_session.add_all([first, second])
    db_session.commit()

    result = open_qualifying_positions(db_session)

    assert result.opened == 1
    assert result.skipped == 1
    assert db_session.query(PaperPosition).filter(PaperPosition.status == "open").count() == 1


def test_isolates_a_failing_position_open(db_session, monkeypatch):
    good = _pending_scenario(symbol="BTCUSDT", created_at=datetime(2026, 1, 1, 0))
    bad = _pending_scenario(symbol="ETHUSDT", entry_price=Decimal("999"), created_at=datetime(2026, 1, 1, 1))
    db_session.add_all([good, bad])
    db_session.commit()

    import src.paper_position_opener as opener_module
    real_size_position = opener_module.size_position

    def flaky_size_position(equity, entry_price, stop_price, risk_pct):
        if entry_price == Decimal("999"):
            raise RuntimeError("boom")
        return real_size_position(equity, entry_price, stop_price, risk_pct)

    monkeypatch.setattr(opener_module, "size_position", flaky_size_position)

    result = open_qualifying_positions(db_session)

    assert result.scanned == 2
    assert result.opened == 1
    assert result.failed == 1
    assert db_session.query(PaperPosition).filter(PaperPosition.status == "open").count() == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_paper_position_opener.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.paper_position_opener'`)

- [ ] **Step 3: Write minimal implementation**

`src/paper_position_opener.py`:
```python
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from src.db.models import PaperPosition, Scenario
from src.paper_equity import current_equity
from src.paper_sizer import size_position
from src.paper_trading_config import CONFIDENCE_THRESHOLD, MAX_CONCURRENT_POSITIONS, RISK_PCT
from src.timeutil import utc_now

logger = logging.getLogger("paper_position_opener")


@dataclass
class PositionOpenResult:
    scanned: int
    opened: int
    skipped: int
    failed: int


def open_qualifying_positions(session, now: datetime = None) -> PositionOpenResult:
    now = now if now is not None else utc_now()

    positioned_scenario_ids = {row[0] for row in session.query(PaperPosition.scenario_id).all()}
    candidates = (
        session.query(Scenario)
        .filter(
            Scenario.status == "pending",
            Scenario.calibrated_confidence.isnot(None),
            Scenario.calibrated_confidence >= CONFIDENCE_THRESHOLD,
        )
        .order_by(Scenario.created_at.asc())
        .all()
    )
    candidates = [scenario for scenario in candidates if scenario.id not in positioned_scenario_ids]

    open_symbols = {
        row[0] for row in session.query(PaperPosition.symbol).filter(PaperPosition.status == "open").all()
    }
    open_count = session.query(PaperPosition).filter(PaperPosition.status == "open").count()

    scanned = 0
    opened = 0
    skipped = 0
    failed = 0
    for scenario in candidates:
        scanned += 1
        scenario_id = scenario.id
        symbol = scenario.symbol
        try:
            if symbol in open_symbols:
                logger.debug("Skipping scenario %s: %s already has an open paper position", scenario_id, symbol)
                skipped += 1
                continue
            if open_count >= MAX_CONCURRENT_POSITIONS:
                logger.debug("Skipping scenario %s (%s): max concurrent positions reached", scenario_id, symbol)
                skipped += 1
                continue

            equity = current_equity(session)
            risk_amount, position_size = size_position(equity, scenario.entry_price, scenario.stop_price, RISK_PCT)

            session.add(PaperPosition(
                scenario_id=scenario.id, symbol=symbol, direction=scenario.direction,
                entry_price=scenario.entry_price, stop_price=scenario.stop_price, target_price=scenario.target_price,
                risk_amount=risk_amount, position_size=position_size,
                opened_at=now, status="open",
            ))
            session.commit()
            open_symbols.add(symbol)
            open_count += 1
            opened += 1
        except Exception:
            session.rollback()
            logger.exception("Opening paper position for scenario %s (%s) failed", scenario_id, symbol)
            failed += 1

    return PositionOpenResult(scanned=scanned, opened=opened, skipped=skipped, failed=failed)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_paper_position_opener.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/paper_position_opener.py tests/test_paper_position_opener.py
git commit -m "feat: add paper position opener with confidence/symbol/cap filtering"
```

---

### Task 6: Paper Trading Runner (orchestrator)

**Files:**
- Create: `src/paper_trading_runner.py`
- Test: `tests/test_paper_trading_runner.py`

**Interfaces:**
- Consumes: `paper_position_closer.close_resolved_positions, PositionCloseResult` (Task 4); `paper_position_opener.open_qualifying_positions, PositionOpenResult` (Task 5)
- Produces: `paper_trading_runner.PaperTradingResult(closed: int, still_open: int, opened: int, skipped: int, failed: int)`, `paper_trading_runner.run_paper_trading_cycle(session, now: datetime = None) -> PaperTradingResult`

- [ ] **Step 1: Write the failing tests**

`tests/test_paper_trading_runner.py`:
```python
from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import PaperPosition, Scenario
from src.paper_trading_config import STARTING_EQUITY
from src.paper_trading_runner import run_paper_trading_cycle


def test_run_paper_trading_cycle_opens_and_later_closes_a_position_end_to_end(db_session):
    """Real scenario, real DB round-trip through both halves — a stub or
    monkeypatched version of this test would not catch a broken wire between
    the opener and the closer."""
    created_at = datetime(2026, 1, 1, 10, 5, 0)
    scenario = Scenario(
        symbol="BTCUSDT", direction="long",
        entry_price=Decimal("100"), target_price=Decimal("110"), stop_price=Decimal("90"),
        expected_return_pct=Decimal("0.1"), confidence_score=Decimal("0.7"),
        created_at=created_at, expires_at=created_at + timedelta(hours=24), status="pending",
        calibrated_confidence=Decimal("0.7"),
    )
    db_session.add(scenario)
    db_session.commit()

    result_1 = run_paper_trading_cycle(db_session, now=created_at)
    assert result_1.opened == 1
    assert result_1.closed == 0
    position = db_session.query(PaperPosition).first()
    assert position.status == "open"

    # Subsystem C's learning cycle would have set this by the time this
    # subsystem's next cycle runs.
    scenario.status = "hit_target"
    db_session.commit()

    result_2 = run_paper_trading_cycle(db_session, now=created_at + timedelta(hours=1))

    assert result_2.opened == 0
    assert result_2.closed == 1
    reloaded = db_session.query(PaperPosition).first()
    assert reloaded.status == "closed"
    assert reloaded.exit_price == Decimal("110")
    assert reloaded.equity_after == STARTING_EQUITY + reloaded.realized_pnl


def test_run_paper_trading_cycle_survives_a_closer_failure_and_still_opens(db_session, monkeypatch):
    scenario = Scenario(
        symbol="BTCUSDT", direction="long",
        entry_price=Decimal("100"), target_price=Decimal("110"), stop_price=Decimal("90"),
        expected_return_pct=Decimal("0.1"), confidence_score=Decimal("0.7"),
        created_at=datetime(2026, 1, 1), expires_at=datetime(2026, 1, 2), status="pending",
        calibrated_confidence=Decimal("0.7"),
    )
    db_session.add(scenario)
    db_session.commit()

    import src.paper_trading_runner as runner_module

    def boom(session, now=None):
        raise RuntimeError("closer exploded")

    monkeypatch.setattr(runner_module, "close_resolved_positions", boom)

    result = run_paper_trading_cycle(db_session, now=datetime(2026, 1, 1))

    assert result.closed == 0
    assert result.opened == 1
    assert db_session.query(PaperPosition).count() == 1


def test_run_paper_trading_cycle_survives_an_opener_failure_and_still_closes(db_session, monkeypatch):
    created_at = datetime(2026, 1, 1)
    scenario = Scenario(
        symbol="BTCUSDT", direction="long",
        entry_price=Decimal("100"), target_price=Decimal("110"), stop_price=Decimal("90"),
        expected_return_pct=Decimal("0.1"), confidence_score=Decimal("0.7"),
        created_at=created_at, expires_at=created_at + timedelta(hours=24), status="hit_target",
        calibrated_confidence=Decimal("0.7"),
    )
    db_session.add(scenario)
    db_session.commit()
    db_session.add(PaperPosition(
        scenario_id=scenario.id, symbol="BTCUSDT", direction="long",
        entry_price=Decimal("100"), stop_price=Decimal("90"), target_price=Decimal("110"),
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=created_at, status="open",
    ))
    db_session.commit()

    import src.paper_trading_runner as runner_module

    def boom(session, now=None):
        raise RuntimeError("opener exploded")

    monkeypatch.setattr(runner_module, "open_qualifying_positions", boom)

    result = run_paper_trading_cycle(db_session, now=created_at + timedelta(hours=1))

    assert result.closed == 1
    assert result.opened == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_paper_trading_runner.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.paper_trading_runner'`)

- [ ] **Step 3: Write minimal implementation**

`src/paper_trading_runner.py`:
```python
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from src.paper_position_closer import PositionCloseResult, close_resolved_positions
from src.paper_position_opener import PositionOpenResult, open_qualifying_positions
from src.timeutil import utc_now

logger = logging.getLogger("paper_trading_runner")


@dataclass
class PaperTradingResult:
    closed: int
    still_open: int
    opened: int
    skipped: int
    failed: int


def run_paper_trading_cycle(session, now: datetime = None) -> PaperTradingResult:
    now = now if now is not None else utc_now()

    # Each half is wrapped separately: neither failing may stop the other from
    # running, nor swallow the summary below.
    close_result = PositionCloseResult(scanned=0, closed=0, still_open=0, failed=0)
    try:
        close_result = close_resolved_positions(session, now)
    except Exception:
        session.rollback()
        logger.exception("Closing paper positions failed")

    open_result = PositionOpenResult(scanned=0, opened=0, skipped=0, failed=0)
    try:
        open_result = open_qualifying_positions(session, now)
    except Exception:
        session.rollback()
        logger.exception("Opening paper positions failed")

    logger.info(
        "Paper trading cycle finished: %d closed, %d still open, %d opened, %d skipped, %d failed",
        close_result.closed, close_result.still_open, open_result.opened,
        open_result.skipped, close_result.failed + open_result.failed,
    )
    return PaperTradingResult(
        closed=close_result.closed, still_open=close_result.still_open,
        opened=open_result.opened, skipped=open_result.skipped,
        failed=close_result.failed + open_result.failed,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_paper_trading_runner.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/paper_trading_runner.py tests/test_paper_trading_runner.py
git commit -m "feat: add paper trading cycle orchestrator"
```

---

### Task 7: Wire the paper trading cycle into the hourly scheduler job

**Files:**
- Modify: `src/scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `paper_trading_runner.run_paper_trading_cycle, PaperTradingResult` (Task 6)
- Produces: no new public interface — `run_timeframe_job` gains a third side effect (the paper trading cycle, for `timeframe == "1h"`, after the learning cycle) and its final log line gains `positions closed`/`opened` counts when scenario generation, the learning cycle, AND the paper trading cycle all succeed in the same run.

The current `src/scheduler.py` (only the changed function shown; everything else — `get_resume_point`, `repair_recent_gaps`, `run_symbol_refresh_job`, `build_scheduler` — is untouched by this task) is:

```python
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
        learning_result = None
        if timeframe == "1h":
            try:
                scenario_result = run_scenario_generation(session, symbols)
            except Exception:
                logger.exception("Scenario generation failed for the %s job", timeframe)

            try:
                learning_result = run_learning_cycle(session, now=end)
            except Exception:
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
    finally:
        session.close()
```

- [ ] **Step 1: Write the failing tests**

Add these three tests to `tests/test_scheduler.py` (the file already has `import src.scheduler as scheduler_module`, `_FakeBinanceClient`, and `Symbol`/`datetime`/`timedelta`/`logging` imported at the top):

```python
def test_run_timeframe_job_runs_paper_trading_cycle_after_1h_learning_cycle(db_session, monkeypatch):
    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True))
    db_session.commit()

    calls = []

    def fake_run_paper_trading_cycle(session, now=None):
        from src.paper_trading_runner import PaperTradingResult
        calls.append(now)
        return PaperTradingResult(closed=0, still_open=0, opened=0, skipped=0, failed=0)

    monkeypatch.setattr(scheduler_module, "run_paper_trading_cycle", fake_run_paper_trading_cycle)

    end = datetime(2026, 1, 1, 5, 0, 0)
    run_timeframe_job(session_factory=lambda: db_session, binance_client=_FakeBinanceClient(), timeframe="1h", now=end)

    assert calls == [end]


def test_run_timeframe_job_does_not_run_paper_trading_cycle_for_1d(db_session, monkeypatch):
    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True))
    db_session.commit()

    calls = []
    monkeypatch.setattr(
        scheduler_module, "run_paper_trading_cycle",
        lambda session, now=None: calls.append(now),
    )

    run_timeframe_job(session_factory=lambda: db_session, binance_client=_FakeBinanceClient(), timeframe="1d")

    assert calls == []


def test_run_timeframe_job_survives_a_paper_trading_cycle_failure(db_session, caplog, monkeypatch):
    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True))
    db_session.commit()

    def boom(session, now=None):
        raise RuntimeError("paper trading cycle exploded")

    monkeypatch.setattr(scheduler_module, "run_paper_trading_cycle", boom)

    with caplog.at_level(logging.INFO, logger="scheduler"):
        run_timeframe_job(session_factory=lambda: db_session, binance_client=_FakeBinanceClient(), timeframe="1h")

    assert any(record.exc_info for record in caplog.records if record.levelno >= logging.ERROR)
    # Real scenario generation and learning cycle ran for real against empty
    # tables and trivially succeeded. Paper trading blew up, so the summary
    # falls back to the scenario+learning (3-field) format, not the full one.
    assert _summary_lines(caplog) == [
        "1h job finished: 1 symbols succeeded, 0 failed, 0 gaps filled, 0 scenarios generated, 0 resolved, 0 calibrated"
    ]
```

Then update these **4 existing tests** (their scenario generation and learning cycle already ran for real in these tests — a real, empty `scenarios`/`paper_positions` table means the paper trading cycle will now also run for real and succeed trivially, extending the summary line):

In `test_gap_repair_failure_is_logged_and_not_counted_as_filled`, change:
```python
    assert _summary_lines(caplog) == [
        "1h job finished: 1 symbols succeeded, 0 failed, 0 gaps filled, 0 scenarios generated, 0 resolved, 0 calibrated"
    ]
```
to:
```python
    assert _summary_lines(caplog) == [
        "1h job finished: 1 symbols succeeded, 0 failed, 0 gaps filled, 0 scenarios generated, "
        "0 resolved, 0 calibrated, 0 positions closed, 0 opened"
    ]
```

In `test_gaps_filled_counts_stored_rows_not_fetch_attempts`, change:
```python
    assert _summary_lines(caplog) == [
        "1h job finished: 1 symbols succeeded, 0 failed, 0 gaps filled, 0 scenarios generated, 0 resolved, 0 calibrated"
    ] * 3
```
to:
```python
    assert _summary_lines(caplog) == [
        "1h job finished: 1 symbols succeeded, 0 failed, 0 gaps filled, 0 scenarios generated, "
        "0 resolved, 0 calibrated, 0 positions closed, 0 opened"
    ] * 3
```

In `test_gaps_filled_counts_a_real_repair`, change:
```python
    assert _summary_lines(caplog) == [
        "1h job finished: 1 symbols succeeded, 0 failed, 1 gaps filled, 0 scenarios generated, 0 resolved, 0 calibrated"
    ]
```
to:
```python
    assert _summary_lines(caplog) == [
        "1h job finished: 1 symbols succeeded, 0 failed, 1 gaps filled, 0 scenarios generated, "
        "0 resolved, 0 calibrated, 0 positions closed, 0 opened"
    ]
```

In `test_run_summary_counts_each_symbol_exactly_once`, change:
```python
    assert _summary_lines(caplog) == [
        "1h job finished: 0 symbols succeeded, 1 failed, 0 gaps filled, 0 scenarios generated, 0 resolved, 0 calibrated"
    ]
```
to:
```python
    assert _summary_lines(caplog) == [
        "1h job finished: 0 symbols succeeded, 1 failed, 0 gaps filled, 0 scenarios generated, "
        "0 resolved, 0 calibrated, 0 positions closed, 0 opened"
    ]
```

Do **not** change `test_run_timeframe_job_survives_a_scenario_generation_failure` or `test_run_timeframe_job_survives_a_learning_cycle_failure` — in both, either `scenario_result` or `learning_result` stays `None`, so the summary already falls through to a shorter format before the paper-trading tier is even considered; their existing assertions are unaffected by this task.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_scheduler.py -v -k "paper_trading or gap_repair_failure or gaps_filled or run_summary"`
Expected: FAIL — the 3 new tests fail with `AttributeError` (no `run_paper_trading_cycle` attribute on the module to monkeypatch), and the 4 updated tests fail with an assertion mismatch against the old (not-yet-changed) production code.

- [ ] **Step 3: Write minimal implementation**

In `src/scheduler.py`:

1. Add this import alongside the existing `from src.learning_runner import run_learning_cycle` line:

```python
from src.paper_trading_runner import run_paper_trading_cycle
```

2. Replace the block from `scenario_result = None` through the end of the summary logging `if`/`elif`/`else` with:

```python
        scenario_result = None
        learning_result = None
        paper_result = None
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

            try:
                paper_result = run_paper_trading_cycle(session, now=end)
            except Exception:
                # Same reasoning again: isolate the summary log from a failure
                # in the paper trading cycle itself.
                logger.exception("Paper trading cycle failed for the %s job", timeframe)

        if scenario_result is not None and learning_result is not None and paper_result is not None:
            logger.info(
                "%s job finished: %d symbols succeeded, %d failed, %d gaps filled, "
                "%d scenarios generated, %d resolved, %d calibrated, %d positions closed, %d opened",
                timeframe, succeeded, failed, gaps_filled, scenario_result.generated,
                learning_result.resolved, learning_result.scenarios_calibrated,
                paper_result.closed, paper_result.opened,
            )
        elif scenario_result is not None and learning_result is not None:
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
Expected: PASS (all tests across Subsystems A, B, C, and this plan)

- [ ] **Step 6: Commit**

```bash
git add src/scheduler.py tests/test_scheduler.py
git commit -m "feat: wire the paper trading cycle into the hourly scheduler job"
```

---

### Task 8: README update

**Files:**
- Modify: `README.md`

**Interfaces:**
- None (documentation only)

- [ ] **Step 1: Update the README**

Read the current `README.md` first, then add a new section after the existing "Öğrenme Döngüsü" section (before "Test"), in the same terse Turkish style as the rest of the file:

```markdown
## Paper Test Portföyü

Öğrenme döngüsünün hemen ardından, kalibre edilmiş güveni (`calibrated_confidence`) 0.65 ve üzeri
olan `pending` senaryolar için simüle bir paper pozisyon açılır — sabit sermayeli (10000, nominal
bir referans; yalnızca yüzdesel getiri anlamlıdır), sabit-oransal risk (%1) ile boyutlandırılır:
pozisyon büyüklüğü `equity × %1 / |entry - stop|` olarak hesaplanır. Aynı sembolde zaten açık bir
pozisyon varsa veya eşzamanlı açık pozisyon sayısı 10'a ulaştıysa yeni pozisyon açılmaz. Bir
senaryo en fazla bir kez paper pozisyona dönüşür.

Bir senaryo sonuçlandığında (Öğrenme Döngüsü tarafından), ilişkili paper pozisyon aynı çalıştırmada
kapatılır: `hit_target` → hedef fiyattan, `hit_stop` → stop fiyatından, `expired` → süre dolduğunda
en yakın kapanmış mumun kapanış fiyatından (henüz o mum yoksa pozisyon açık kalır, sonraki
çalıştırmada tekrar denenir). Gerçekleşen kâr/zarar equity'ye eklenir — ayrı bir "hesap" tablosu
yok, her kapanan pozisyon satırı kendi `equity_before`/`equity_after` değerlerini taşır; pozisyon
geçmişinin kendisi equity eğrisidir.
```

Then update the existing "Test" section's line about what the tests exercise, if it names specific behavior that's now incomplete — read the current wording first and adjust minimally rather than rewriting the section.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document the paper trading portfolio"
```

---

## Post-Plan Notes

- This plan completes Subsystem D (paper trading), the last of the four planned subsystems (A: data infra, B: scenario engine, C: confidence/learning loop, D: this plan), per `docs/superpowers/specs/2026-08-23-paper-trading-design.md`.
- Manual verification after Task 8 (not automated, requires a live Postgres + internet, and enough accumulated scenario/calibration history): run `python -m src.main`, wait for scenarios to reach `calibrated_confidence >= 0.65` and resolve across several hourly runs, and confirm `paper_positions` rows open, later close with a plausible `exit_price`/`realized_pnl`, and that `equity_after` values chain correctly in `id`/`closed_at` order. Meaningful paper trading activity depends on Subsystem C's calibration accumulating enough samples first — that's expected, not a bug; log each run's `closed`/`opened`/`skipped`/`failed` counts (already added in Task 7) so an operator can distinguish "no qualifying scenarios yet" from "the pipeline is stuck."
- Real capital, real order placement, and any form of automatic trading are explicitly out of scope for this project, permanently — this plan's ceiling is a simulated, database-only portfolio.
