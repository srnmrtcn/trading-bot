# Binance Veri Altyapısı Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-healing background service that continuously fetches 1h/1d OHLCV kline data for all active Binance USDT pairs, stores it in PostgreSQL with integrity checks, and survives restarts without data loss or duplication.

**Architecture:** A single long-running Python process (`src/main.py`) wires together six components — Symbol Registry, Binance Client, Integrity Checker, Storage Layer, Backfill Engine, and an in-process APScheduler — around a PostgreSQL database. Each component is a plain module of functions (no framework classes beyond a thin `BinanceClient` wrapper and a `RateLimitBackoff` helper), so it can be unit-tested in isolation with mocks and an in-memory SQLite database.

**Tech Stack:** Python 3.9, SQLAlchemy 2.0 (ORM, dialect-agnostic upsert — no Postgres-only SQL), psycopg2-binary (Postgres driver for production), python-binance (public REST endpoints only, no API key), APScheduler (in-process cron), pytest.

## Global Constraints

- Python 3.9 compatibility required (installed runtime is 3.9.6) — every file using `X | None` / `list[X]` style annotations MUST start with `from __future__ import annotations`.
- No Binance API key/secret anywhere — only public endpoints (`get_exchange_info`, `get_klines`).
- Timeframes: exactly `1h` and `1d`. No other timeframe is in scope.
- Coverage: all Binance symbols with `status == "TRADING"` and `quoteAsset == "USDT"`.
- Initial backfill depth: 730 days (2 years) per symbol/timeframe.
- Storage upsert logic must be dialect-agnostic (query-then-insert-or-update via the ORM), so unit tests run against `sqlite:///:memory:` without requiring a live Postgres instance.
- Delisted symbols are marked `is_active=False`, never deleted. Anomalous klines are marked `flagged=True`, never deleted or skipped.
- Scheduling is in-process (APScheduler `BackgroundScheduler`), not OS cron/launchd.
- A symbol's fetch failure must never abort processing of other symbols (isolation).

---

## File Structure

```
Trading Bot/
├── requirements.txt
├── pytest.ini
├── .env.example
├── README.md
├── src/
│   ├── __init__.py
│   ├── config.py
│   ├── rate_limit.py
│   ├── binance_client.py
│   ├── integrity.py
│   ├── storage.py
│   ├── symbol_registry.py
│   ├── kline_fetcher.py
│   ├── backfill.py
│   ├── fetch_log.py
│   ├── scheduler.py
│   ├── main.py
│   └── db/
│       ├── __init__.py
│       ├── base.py
│       ├── models.py
│       └── session.py
└── tests/
    ├── conftest.py
    ├── test_config.py
    ├── test_models.py
    ├── test_storage.py
    ├── test_rate_limit.py
    ├── test_binance_client.py
    ├── test_symbol_registry.py
    ├── test_integrity.py
    ├── test_kline_fetcher.py
    ├── test_backfill.py
    ├── test_fetch_log.py
    ├── test_scheduler.py
    └── test_main.py
```

---

### Task 1: Project scaffolding, dependencies, and config loader

**Files:**
- Create: `requirements.txt`
- Create: `pytest.ini`
- Create: `.env.example`
- Create: `src/__init__.py`
- Create: `src/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `config.get_database_url() -> str`, `config.ConfigError` (raised when `DATABASE_URL` is unset)

- [ ] **Step 1: Create dependency and pytest config files**

`requirements.txt`:
```
sqlalchemy>=2.0,<2.1
psycopg2-binary>=2.9,<3.0
python-binance>=1.0.19,<2.0
apscheduler>=3.10,<4.0
pytest>=7.4,<8.0
```

`pytest.ini`:
```ini
[pytest]
pythonpath = .
```

`.env.example`:
```
DATABASE_URL=postgresql+psycopg2://localhost/crypto_office
```

- [ ] **Step 2: Write the failing test for config**

`tests/test_config.py`:
```python
import pytest
from src import config


def test_get_database_url_reads_env_var(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://localhost/testdb")
    assert config.get_database_url() == "postgresql+psycopg2://localhost/testdb"


def test_get_database_url_raises_when_unset(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(config.ConfigError):
        config.get_database_url()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pip install -r requirements.txt && touch src/__init__.py && pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.config'` (or collection error).

- [ ] **Step 4: Write minimal implementation**

`src/config.py`:
```python
from __future__ import annotations

import os


class ConfigError(Exception):
    pass


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ConfigError("DATABASE_URL environment variable is not set")
    return url
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add requirements.txt pytest.ini .env.example src/__init__.py src/config.py tests/test_config.py
git commit -m "feat: add project scaffolding and config loader"
```

---

### Task 2: SQLAlchemy models and session factory

**Files:**
- Create: `src/db/__init__.py`
- Create: `src/db/base.py`
- Create: `src/db/models.py`
- Create: `src/db/session.py`
- Create: `tests/conftest.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: `db.base.Base`, `db.models.Symbol(symbol, base_asset, quote_asset, is_active, listed_at, updated_at)`, `db.models.Kline(id, symbol, timeframe, open_time, open, high, low, close, volume, flagged)` with unique constraint on `(symbol, timeframe, open_time)`, `db.models.FetchLog(id, symbol, timeframe, started_at, finished_at, status, error_message)`, `db.session.make_engine(database_url) -> Engine`, `db.session.make_session_factory(engine) -> sessionmaker`, `db.session.create_all_tables(engine) -> None`
- Produces (test fixture): `conftest.db_session` — a SQLAlchemy `Session` bound to a fresh in-memory SQLite database with all tables created, used by every later test that touches the DB.

- [ ] **Step 1: Write the failing tests**

`tests/conftest.py`:
```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.base import Base


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    session = SessionLocal()
    yield session
    session.close()
```

`tests/test_models.py`:
```python
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from src.db.models import Symbol, Kline, FetchLog


def test_insert_symbol(db_session):
    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True))
    db_session.commit()
    result = db_session.get(Symbol, "BTCUSDT")
    assert result.base_asset == "BTC"
    assert result.is_active is True


def test_kline_unique_constraint_rejects_duplicates(db_session):
    open_time = datetime(2026, 1, 1, 0, 0, 0)
    db_session.add(Kline(
        symbol="BTCUSDT", timeframe="1h", open_time=open_time,
        open=Decimal("100"), high=Decimal("110"), low=Decimal("90"),
        close=Decimal("105"), volume=Decimal("1000"), flagged=False,
    ))
    db_session.commit()

    db_session.add(Kline(
        symbol="BTCUSDT", timeframe="1h", open_time=open_time,
        open=Decimal("101"), high=Decimal("111"), low=Decimal("91"),
        close=Decimal("106"), volume=Decimal("1001"), flagged=False,
    ))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_insert_fetch_log(db_session):
    now = datetime(2026, 1, 1, 12, 0, 0)
    db_session.add(FetchLog(
        symbol="BTCUSDT", timeframe="1h", started_at=now, finished_at=now,
        status="success", error_message=None,
    ))
    db_session.commit()
    row = db_session.query(FetchLog).first()
    assert row.status == "success"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_models.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.db'`)

- [ ] **Step 3: Write minimal implementation**

`src/db/__init__.py`: (empty file)

`src/db/base.py`:
```python
from sqlalchemy.orm import declarative_base

Base = declarative_base()
```

`src/db/models.py`:
```python
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, Numeric, String, Text, UniqueConstraint

from src.db.base import Base


class Symbol(Base):
    __tablename__ = "symbols"

    symbol = Column(String, primary_key=True)
    base_asset = Column(String, nullable=False)
    quote_asset = Column(String, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    listed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class Kline(Base):
    __tablename__ = "klines"
    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "open_time", name="uq_kline_symbol_timeframe_open_time"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False, index=True)
    timeframe = Column(String, nullable=False, index=True)
    open_time = Column(DateTime, nullable=False)
    open = Column(Numeric(20, 8), nullable=False)
    high = Column(Numeric(20, 8), nullable=False)
    low = Column(Numeric(20, 8), nullable=False)
    close = Column(Numeric(20, 8), nullable=False)
    volume = Column(Numeric(30, 8), nullable=False)
    flagged = Column(Boolean, nullable=False, default=False)


class FetchLog(Base):
    __tablename__ = "fetch_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False, index=True)
    timeframe = Column(String, nullable=False, index=True)
    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String, nullable=False)
    error_message = Column(Text, nullable=True)
```

`src/db/session.py`:
```python
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from src.db.base import Base


def make_engine(database_url: str) -> Engine:
    return create_engine(database_url, future=True)


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, future=True)


def create_all_tables(engine: Engine) -> None:
    Base.metadata.create_all(engine)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_models.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/db tests/conftest.py tests/test_models.py
git commit -m "feat: add SQLAlchemy models and session factory"
```

---

### Task 3: Storage layer (upsert logic)

**Files:**
- Create: `src/storage.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: `db.models.Symbol`, `db.models.Kline` (Task 2); `conftest.db_session` fixture
- Produces: `storage.KlineUpsertResult(inserted: int, updated: int)`, `storage.upsert_symbols(session, symbols: list[dict]) -> None` (each dict has keys `symbol`, `base_asset`, `quote_asset`, optional `listed_at`), `storage.mark_symbols_inactive(session, active_symbols: set[str]) -> None`, `storage.upsert_klines(session, symbol: str, timeframe: str, rows: list[dict]) -> KlineUpsertResult` (each row dict has keys `open_time`, `open`, `high`, `low`, `close`, `volume`, optional `flagged`)

- [ ] **Step 1: Write the failing tests**

`tests/test_storage.py`:
```python
from datetime import datetime
from decimal import Decimal

from src.db.models import Symbol
from src.storage import upsert_symbols, mark_symbols_inactive, upsert_klines


def test_upsert_symbols_inserts_new(db_session):
    upsert_symbols(db_session, [
        {"symbol": "BTCUSDT", "base_asset": "BTC", "quote_asset": "USDT", "listed_at": None},
    ])
    result = db_session.get(Symbol, "BTCUSDT")
    assert result is not None
    assert result.is_active is True


def test_upsert_symbols_reactivates_existing(db_session):
    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=False))
    db_session.commit()
    upsert_symbols(db_session, [
        {"symbol": "BTCUSDT", "base_asset": "BTC", "quote_asset": "USDT", "listed_at": None},
    ])
    assert db_session.get(Symbol, "BTCUSDT").is_active is True


def test_mark_symbols_inactive_deactivates_missing(db_session):
    db_session.add_all([
        Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True),
        Symbol(symbol="ETHUSDT", base_asset="ETH", quote_asset="USDT", is_active=True),
    ])
    db_session.commit()
    mark_symbols_inactive(db_session, active_symbols={"BTCUSDT"})
    assert db_session.get(Symbol, "BTCUSDT").is_active is True
    assert db_session.get(Symbol, "ETHUSDT").is_active is False


def _row(open_time, close):
    return {
        "open_time": open_time, "open": Decimal("100"), "high": Decimal("110"),
        "low": Decimal("90"), "close": close, "volume": Decimal("1000"), "flagged": False,
    }


def test_upsert_klines_inserts_new_rows(db_session):
    rows = [_row(datetime(2026, 1, 1, 0), Decimal("105")), _row(datetime(2026, 1, 1, 1), Decimal("106"))]
    result = upsert_klines(db_session, "BTCUSDT", "1h", rows)
    assert result.inserted == 2
    assert result.updated == 0


def test_upsert_klines_updates_existing_rows_without_duplicating(db_session):
    open_time = datetime(2026, 1, 1, 0)
    upsert_klines(db_session, "BTCUSDT", "1h", [_row(open_time, Decimal("105"))])
    result = upsert_klines(db_session, "BTCUSDT", "1h", [_row(open_time, Decimal("999"))])
    assert result.inserted == 0
    assert result.updated == 1

    from src.db.models import Kline
    rows_in_db = db_session.query(Kline).filter(Kline.symbol == "BTCUSDT", Kline.timeframe == "1h").all()
    assert len(rows_in_db) == 1
    assert rows_in_db[0].close == Decimal("999")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_storage.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.storage'`)

- [ ] **Step 3: Write minimal implementation**

`src/storage.py`:
```python
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from src.db.models import Kline, Symbol


@dataclass
class KlineUpsertResult:
    inserted: int
    updated: int


def upsert_symbols(session: Session, symbols: list[dict]) -> None:
    for data in symbols:
        existing = session.get(Symbol, data["symbol"])
        if existing is None:
            session.add(Symbol(
                symbol=data["symbol"],
                base_asset=data["base_asset"],
                quote_asset=data["quote_asset"],
                is_active=True,
                listed_at=data.get("listed_at"),
            ))
        else:
            existing.is_active = True
            existing.base_asset = data["base_asset"]
            existing.quote_asset = data["quote_asset"]
    session.commit()


def mark_symbols_inactive(session: Session, active_symbols: set) -> None:
    currently_active = session.query(Symbol).filter(Symbol.is_active == True).all()  # noqa: E712
    for sym in currently_active:
        if sym.symbol not in active_symbols:
            sym.is_active = False
    session.commit()


def upsert_klines(session: Session, symbol: str, timeframe: str, rows: list[dict]) -> KlineUpsertResult:
    if not rows:
        return KlineUpsertResult(inserted=0, updated=0)

    open_times = [row["open_time"] for row in rows]
    existing = (
        session.query(Kline)
        .filter(Kline.symbol == symbol, Kline.timeframe == timeframe, Kline.open_time.in_(open_times))
        .all()
    )
    existing_by_time = {row.open_time: row for row in existing}

    inserted = 0
    updated = 0
    for row in rows:
        existing_row = existing_by_time.get(row["open_time"])
        if existing_row is None:
            session.add(Kline(
                symbol=symbol,
                timeframe=timeframe,
                open_time=row["open_time"],
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
                flagged=row.get("flagged", False),
            ))
            inserted += 1
        else:
            existing_row.open = row["open"]
            existing_row.high = row["high"]
            existing_row.low = row["low"]
            existing_row.close = row["close"]
            existing_row.volume = row["volume"]
            existing_row.flagged = row.get("flagged", False)
            updated += 1

    session.commit()
    return KlineUpsertResult(inserted=inserted, updated=updated)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_storage.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/storage.py tests/test_storage.py
git commit -m "feat: add dialect-agnostic upsert storage layer"
```

---

### Task 4: Rate-limit backoff helper

**Files:**
- Create: `src/rate_limit.py`
- Test: `tests/test_rate_limit.py`

**Interfaces:**
- Produces: `rate_limit.RateLimitBackoff(max_retries=5, base_delay=1.0, sleep_fn=time.sleep)` with method `.call(func, *args, **kwargs)` — retries on exceptions whose `.status_code` is `429` or `418`, honoring a `Retry-After` header on `exc.response.headers` when present, else exponential backoff (`base_delay * 2**attempt`); re-raises immediately for any other exception or once `max_retries` is exceeded.

- [ ] **Step 1: Write the failing tests**

`tests/test_rate_limit.py`:
```python
import pytest

from src.rate_limit import RateLimitBackoff


class _RateLimitError(Exception):
    def __init__(self, status_code, response=None):
        super().__init__("rate limited")
        self.status_code = status_code
        self.response = response


class _FakeResponse:
    def __init__(self, headers):
        self.headers = headers


def test_backoff_retries_then_succeeds_with_exponential_delay():
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] == 1:
            raise _RateLimitError(status_code=429)
        return "ok"

    sleeps = []
    backoff = RateLimitBackoff(max_retries=3, base_delay=1.0, sleep_fn=sleeps.append)
    assert backoff.call(flaky) == "ok"
    assert calls["count"] == 2
    assert sleeps == [1.0]


def test_backoff_honors_retry_after_header():
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] == 1:
            raise _RateLimitError(status_code=429, response=_FakeResponse({"Retry-After": "2"}))
        return "ok"

    sleeps = []
    backoff = RateLimitBackoff(sleep_fn=sleeps.append)
    assert backoff.call(flaky) == "ok"
    assert sleeps == [2.0]


def test_backoff_raises_immediately_for_non_rate_limit_errors():
    def always_fails():
        raise _RateLimitError(status_code=500)

    backoff = RateLimitBackoff(sleep_fn=lambda seconds: None)
    with pytest.raises(_RateLimitError):
        backoff.call(always_fails)


def test_backoff_gives_up_after_max_retries():
    def always_rate_limited():
        raise _RateLimitError(status_code=429)

    backoff = RateLimitBackoff(max_retries=2, sleep_fn=lambda seconds: None)
    with pytest.raises(_RateLimitError):
        backoff.call(always_rate_limited)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_rate_limit.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.rate_limit'`)

- [ ] **Step 3: Write minimal implementation**

`src/rate_limit.py`:
```python
from __future__ import annotations

import time


class RateLimitBackoff:
    def __init__(self, max_retries: int = 5, base_delay: float = 1.0, sleep_fn=time.sleep):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.sleep_fn = sleep_fn

    def call(self, func, *args, **kwargs):
        attempt = 0
        while True:
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                status_code = getattr(exc, "status_code", None)
                if status_code not in (429, 418) or attempt >= self.max_retries:
                    raise
                delay = self._retry_after_seconds(exc)
                if delay is None:
                    delay = self.base_delay * (2 ** attempt)
                self.sleep_fn(delay)
                attempt += 1

    @staticmethod
    def _retry_after_seconds(exc):
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None) if response is not None else None
        if not headers:
            return None
        value = headers.get("Retry-After")
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_rate_limit.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/rate_limit.py tests/test_rate_limit.py
git commit -m "feat: add rate-limit backoff helper"
```

---

### Task 5: Binance client wrapper

**Files:**
- Create: `src/binance_client.py`
- Test: `tests/test_binance_client.py`

**Interfaces:**
- Consumes: `rate_limit.RateLimitBackoff` (Task 4)
- Produces: `binance_client.BinanceClient(client=None, backoff=None)` with methods `.get_active_usdt_symbols() -> list[dict]` (each dict: `symbol`, `base_asset`, `quote_asset`) and `.get_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[dict]` (each dict: `open_time: datetime` naive UTC, `open/high/low/close/volume: Decimal`), paginating internally past Binance's 1000-row-per-call limit.

- [ ] **Step 1: Write the failing tests**

`tests/test_binance_client.py`:
```python
from datetime import datetime

from src.binance_client import BinanceClient


class _FakeClient:
    def __init__(self, exchange_info=None, kline_pages=None):
        self._exchange_info = exchange_info or {"symbols": []}
        self._kline_pages = kline_pages or []
        self._page_index = 0
        self.get_klines_calls = []

    def get_exchange_info(self):
        return self._exchange_info

    def get_klines(self, symbol, interval, startTime, endTime, limit):
        self.get_klines_calls.append({"startTime": startTime, "endTime": endTime})
        if self._page_index >= len(self._kline_pages):
            return []
        page = self._kline_pages[self._page_index]
        self._page_index += 1
        return page


def test_get_active_usdt_symbols_filters_trading_and_usdt():
    fake = _FakeClient(exchange_info={"symbols": [
        {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT", "status": "TRADING"},
        {"symbol": "ETHBTC", "baseAsset": "ETH", "quoteAsset": "BTC", "status": "TRADING"},
        {"symbol": "XRPUSDT", "baseAsset": "XRP", "quoteAsset": "USDT", "status": "BREAK"},
    ]})
    client = BinanceClient(client=fake)
    result = client.get_active_usdt_symbols()
    assert result == [{"symbol": "BTCUSDT", "base_asset": "BTC", "quote_asset": "USDT"}]


def test_get_klines_parses_rows_into_decimal_dicts():
    fake = _FakeClient(kline_pages=[
        [[1735689600000, "100.5", "110.2", "90.1", "105.3", "1000.0"]],
    ])
    client = BinanceClient(client=fake)
    rows = client.get_klines("BTCUSDT", "1h", start_ms=1735689600000, end_ms=1735693200000)
    assert len(rows) == 1
    assert rows[0]["open_time"] == datetime(2025, 1, 1, 0, 0, 0)
    assert str(rows[0]["close"]) == "105.3"


def test_get_klines_paginates_past_1000_row_limit():
    page1 = [[1735689600000 + i * 3600000, "1", "1", "1", "1", "1"] for i in range(1000)]
    page2 = [[page1[-1][0] + 3600000, "2", "2", "2", "2", "2"]]
    fake = _FakeClient(kline_pages=[page1, page2])
    client = BinanceClient(client=fake)
    rows = client.get_klines("BTCUSDT", "1h", start_ms=1735689600000, end_ms=1735689600000 + 2000 * 3600000)
    assert len(rows) == 1001
    assert len(fake.get_klines_calls) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_binance_client.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.binance_client'`)

- [ ] **Step 3: Write minimal implementation**

`src/binance_client.py`:
```python
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from binance.client import Client

from src.rate_limit import RateLimitBackoff


class BinanceClient:
    def __init__(self, client=None, backoff: RateLimitBackoff = None):
        self._client = client or Client(api_key="", api_secret="")
        self._backoff = backoff or RateLimitBackoff()

    def get_active_usdt_symbols(self) -> list:
        info = self._backoff.call(self._client.get_exchange_info)
        results = []
        for entry in info["symbols"]:
            if entry["status"] == "TRADING" and entry["quoteAsset"] == "USDT":
                results.append({
                    "symbol": entry["symbol"],
                    "base_asset": entry["baseAsset"],
                    "quote_asset": entry["quoteAsset"],
                })
        return results

    def get_klines(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
        all_rows = []
        cursor = start_ms
        while cursor < end_ms:
            raw = self._backoff.call(
                self._client.get_klines,
                symbol=symbol,
                interval=interval,
                startTime=cursor,
                endTime=end_ms,
                limit=1000,
            )
            if not raw:
                break
            for entry in raw:
                open_time = datetime.fromtimestamp(entry[0] / 1000, tz=timezone.utc).replace(tzinfo=None)
                all_rows.append({
                    "open_time": open_time,
                    "open": Decimal(str(entry[1])),
                    "high": Decimal(str(entry[2])),
                    "low": Decimal(str(entry[3])),
                    "close": Decimal(str(entry[4])),
                    "volume": Decimal(str(entry[5])),
                })
            next_cursor = raw[-1][0] + 1
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            if len(raw) < 1000:
                break
        return all_rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_binance_client.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/binance_client.py tests/test_binance_client.py
git commit -m "feat: add Binance public REST client wrapper with pagination"
```

---

### Task 6: Symbol Registry

**Files:**
- Create: `src/symbol_registry.py`
- Test: `tests/test_symbol_registry.py`

**Interfaces:**
- Consumes: `storage.upsert_symbols`, `storage.mark_symbols_inactive` (Task 3); `db.models.Symbol` (Task 2); a `binance_client` object exposing `.get_active_usdt_symbols() -> list[dict]` (Task 5's shape)
- Produces: `symbol_registry.SymbolRefreshResult(active_count: int, deactivated_count: int)`, `symbol_registry.refresh_symbols(session, binance_client) -> SymbolRefreshResult`

- [ ] **Step 1: Write the failing tests**

`tests/test_symbol_registry.py`:
```python
from src.db.models import Symbol
from src.symbol_registry import refresh_symbols


class _FakeBinanceClient:
    def __init__(self, symbols):
        self._symbols = symbols

    def get_active_usdt_symbols(self):
        return self._symbols


def test_refresh_symbols_inserts_new_active_symbols(db_session):
    fake = _FakeBinanceClient([
        {"symbol": "BTCUSDT", "base_asset": "BTC", "quote_asset": "USDT"},
        {"symbol": "ETHUSDT", "base_asset": "ETH", "quote_asset": "USDT"},
    ])
    result = refresh_symbols(db_session, fake)
    assert result.active_count == 2
    assert result.deactivated_count == 0
    assert db_session.get(Symbol, "BTCUSDT").is_active is True


def test_refresh_symbols_deactivates_delisted_symbol(db_session):
    db_session.add(Symbol(symbol="OLDUSDT", base_asset="OLD", quote_asset="USDT", is_active=True))
    db_session.commit()
    fake = _FakeBinanceClient([{"symbol": "BTCUSDT", "base_asset": "BTC", "quote_asset": "USDT"}])
    result = refresh_symbols(db_session, fake)
    assert result.deactivated_count == 1
    assert db_session.get(Symbol, "OLDUSDT").is_active is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_symbol_registry.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.symbol_registry'`)

- [ ] **Step 3: Write minimal implementation**

`src/symbol_registry.py`:
```python
from __future__ import annotations

from dataclasses import dataclass

from src.db.models import Symbol
from src.storage import mark_symbols_inactive, upsert_symbols


@dataclass
class SymbolRefreshResult:
    active_count: int
    deactivated_count: int


def refresh_symbols(session, binance_client) -> SymbolRefreshResult:
    active_symbols = binance_client.get_active_usdt_symbols()
    active_names = {entry["symbol"] for entry in active_symbols}

    previously_active = {
        row.symbol for row in session.query(Symbol).filter(Symbol.is_active == True).all()  # noqa: E712
    }

    upsert_symbols(session, active_symbols)
    mark_symbols_inactive(session, active_names)

    deactivated = previously_active - active_names
    return SymbolRefreshResult(active_count=len(active_names), deactivated_count=len(deactivated))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_symbol_registry.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/symbol_registry.py tests/test_symbol_registry.py
git commit -m "feat: add symbol registry refresh logic"
```

---

### Task 7: Integrity Checker (gap detection + anomaly flagging)

**Files:**
- Create: `src/integrity.py`
- Test: `tests/test_integrity.py`

**Interfaces:**
- Produces: `integrity.TIMEFRAME_DELTAS` (dict mapping `"1h"`/`"1d"` to `timedelta`), `integrity.Gap(start: datetime, end: datetime)`, `integrity.detect_gaps(existing_open_times: list[datetime], timeframe: str, range_start: datetime, range_end: datetime) -> list[Gap]`, `integrity.flag_anomalies(rows: list[dict], spike_threshold: Decimal = Decimal("0.5")) -> list[dict]` (returns new dicts with a `flagged: bool` key added; flags zero volume or a close-price move exceeding `spike_threshold` from the previous row's close within the batch)

- [ ] **Step 1: Write the failing tests**

`tests/test_integrity.py`:
```python
from datetime import datetime, timedelta
from decimal import Decimal

from src.integrity import detect_gaps, flag_anomalies


def test_detect_gaps_finds_no_gap_when_complete():
    start = datetime(2026, 1, 1, 0)
    end = datetime(2026, 1, 1, 3)
    existing = [start, start + timedelta(hours=1), start + timedelta(hours=2), end]
    assert detect_gaps(existing, "1h", start, end) == []


def test_detect_gaps_finds_single_missing_candle():
    start = datetime(2026, 1, 1, 0)
    end = datetime(2026, 1, 1, 3)
    existing = [start, start + timedelta(hours=2), end]  # missing hour 1
    gaps = detect_gaps(existing, "1h", start, end)
    assert len(gaps) == 1
    assert gaps[0].start == start + timedelta(hours=1)
    assert gaps[0].end == start + timedelta(hours=1)


def test_detect_gaps_groups_consecutive_missing_candles():
    start = datetime(2026, 1, 1, 0)
    end = datetime(2026, 1, 1, 4)
    existing = [start, end]  # hours 1, 2, 3 all missing
    gaps = detect_gaps(existing, "1h", start, end)
    assert len(gaps) == 1
    assert gaps[0].start == start + timedelta(hours=1)
    assert gaps[0].end == start + timedelta(hours=3)


def _row(close, volume="1000"):
    return {"open_time": datetime(2026, 1, 1), "open": Decimal("100"), "high": Decimal("100"),
            "low": Decimal("100"), "close": Decimal(close), "volume": Decimal(volume)}


def test_flag_anomalies_flags_zero_volume():
    rows = [_row("100", volume="0")]
    result = flag_anomalies(rows)
    assert result[0]["flagged"] is True


def test_flag_anomalies_flags_large_price_spike():
    rows = [_row("100"), _row("160")]  # 60% jump
    result = flag_anomalies(rows)
    assert result[0]["flagged"] is False
    assert result[1]["flagged"] is True


def test_flag_anomalies_does_not_flag_normal_data():
    rows = [_row("100"), _row("102"), _row("99")]
    result = flag_anomalies(rows)
    assert all(row["flagged"] is False for row in result)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_integrity.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.integrity'`)

- [ ] **Step 3: Write minimal implementation**

`src/integrity.py`:
```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

TIMEFRAME_DELTAS = {
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
}


@dataclass
class Gap:
    start: datetime
    end: datetime


def detect_gaps(existing_open_times: list, timeframe: str, range_start: datetime, range_end: datetime) -> list:
    step = TIMEFRAME_DELTAS[timeframe]
    expected = []
    cursor = range_start
    while cursor <= range_end:
        expected.append(cursor)
        cursor += step

    existing_set = set(existing_open_times)
    missing = [t for t in expected if t not in existing_set]
    if not missing:
        return []

    gaps = []
    gap_start = missing[0]
    prev = missing[0]
    for t in missing[1:]:
        if t - prev == step:
            prev = t
            continue
        gaps.append(Gap(start=gap_start, end=prev))
        gap_start = t
        prev = t
    gaps.append(Gap(start=gap_start, end=prev))
    return gaps


def flag_anomalies(rows: list, spike_threshold: Decimal = Decimal("0.5")) -> list:
    flagged_rows = []
    previous_close = None
    for row in rows:
        is_flagged = False
        if row["volume"] == 0:
            is_flagged = True
        if previous_close is not None and previous_close != 0:
            change = abs(row["close"] - previous_close) / previous_close
            if change > spike_threshold:
                is_flagged = True
        flagged_rows.append({**row, "flagged": is_flagged})
        previous_close = row["close"]
    return flagged_rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_integrity.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/integrity.py tests/test_integrity.py
git commit -m "feat: add gap detection and anomaly flagging"
```

---

### Task 8: Kline Fetcher (per symbol/timeframe orchestration)

**Files:**
- Create: `src/kline_fetcher.py`
- Test: `tests/test_kline_fetcher.py`

**Interfaces:**
- Consumes: `integrity.flag_anomalies` (Task 7), `storage.upsert_klines` (Task 3); a `binance_client` object exposing `.get_klines(symbol, timeframe, start_ms, end_ms) -> list[dict]` (Task 5's shape)
- Produces: `kline_fetcher.FetchResult(symbol: str, timeframe: str, fetched: int, inserted: int, updated: int, flagged: int, error)`, `kline_fetcher.fetch_and_store(session, binance_client, symbol, timeframe, start_ms, end_ms) -> FetchResult` — never raises; a Binance client error is captured into `FetchResult.error`.

- [ ] **Step 1: Write the failing tests**

`tests/test_kline_fetcher.py`:
```python
from datetime import datetime
from decimal import Decimal

from src.kline_fetcher import fetch_and_store


class _FakeBinanceClient:
    def __init__(self, rows=None, error=None):
        self._rows = rows or []
        self._error = error

    def get_klines(self, symbol, timeframe, start_ms, end_ms):
        if self._error:
            raise self._error
        return self._rows


def _row(open_time, close, volume="1000"):
    return {"open_time": open_time, "open": Decimal("100"), "high": Decimal("100"),
            "low": Decimal("100"), "close": Decimal(close), "volume": Decimal(volume)}


def test_fetch_and_store_persists_rows_and_reports_counts(db_session):
    rows = [_row(datetime(2026, 1, 1, 0), "100"), _row(datetime(2026, 1, 1, 1), "160")]
    fake = _FakeBinanceClient(rows=rows)
    result = fetch_and_store(db_session, fake, "BTCUSDT", "1h", start_ms=0, end_ms=1)
    assert result.fetched == 2
    assert result.inserted == 2
    assert result.updated == 0
    assert result.flagged == 1  # the 60% spike
    assert result.error is None


def test_fetch_and_store_captures_error_without_raising(db_session):
    fake = _FakeBinanceClient(error=RuntimeError("network down"))
    result = fetch_and_store(db_session, fake, "BTCUSDT", "1h", start_ms=0, end_ms=1)
    assert result.error == "network down"
    assert result.fetched == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_kline_fetcher.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.kline_fetcher'`)

- [ ] **Step 3: Write minimal implementation**

`src/kline_fetcher.py`:
```python
from __future__ import annotations

from dataclasses import dataclass

from src.integrity import flag_anomalies
from src.storage import upsert_klines


@dataclass
class FetchResult:
    symbol: str
    timeframe: str
    fetched: int
    inserted: int
    updated: int
    flagged: int
    error: str = None


def fetch_and_store(session, binance_client, symbol: str, timeframe: str, start_ms: int, end_ms: int) -> FetchResult:
    try:
        rows = binance_client.get_klines(symbol, timeframe, start_ms, end_ms)
    except Exception as exc:
        return FetchResult(symbol=symbol, timeframe=timeframe, fetched=0, inserted=0, updated=0, flagged=0, error=str(exc))

    flagged_rows = flag_anomalies(rows)
    upsert_result = upsert_klines(session, symbol, timeframe, flagged_rows)
    flagged_count = sum(1 for row in flagged_rows if row["flagged"])
    return FetchResult(
        symbol=symbol, timeframe=timeframe, fetched=len(rows),
        inserted=upsert_result.inserted, updated=upsert_result.updated,
        flagged=flagged_count, error=None,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_kline_fetcher.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/kline_fetcher.py tests/test_kline_fetcher.py
git commit -m "feat: add per-symbol kline fetch-and-store orchestration"
```

---

### Task 9: Backfill Engine

**Files:**
- Create: `src/backfill.py`
- Test: `tests/test_backfill.py`

**Interfaces:**
- Consumes: `kline_fetcher.fetch_and_store` (Task 8), `integrity.detect_gaps` (Task 7), `db.models.Kline` (Task 2)
- Produces: `backfill.run_initial_backfill(session, binance_client, symbols: list[str], timeframes: list[str], since_days: int = 730) -> list[FetchResult]`, `backfill.run_gap_backfill(session, binance_client, symbol: str, timeframe: str, range_start: datetime, range_end: datetime) -> list[FetchResult]`

- [ ] **Step 1: Write the failing tests**

`tests/test_backfill.py`:
```python
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from src.db.models import Kline
from src.kline_fetcher import FetchResult
import src.backfill as backfill_module


def test_run_initial_backfill_calls_fetch_and_store_per_symbol_and_timeframe(db_session):
    calls = []

    def fake_fetch_and_store(session, client, symbol, timeframe, start_ms, end_ms):
        calls.append((symbol, timeframe))
        return FetchResult(symbol=symbol, timeframe=timeframe, fetched=0, inserted=0, updated=0, flagged=0)

    with patch.object(backfill_module, "fetch_and_store", side_effect=fake_fetch_and_store):
        results = backfill_module.run_initial_backfill(
            db_session, binance_client=object(), symbols=["BTCUSDT", "ETHUSDT"], timeframes=["1h", "1d"],
        )
    assert set(calls) == {("BTCUSDT", "1h"), ("BTCUSDT", "1d"), ("ETHUSDT", "1h"), ("ETHUSDT", "1d")}
    assert len(results) == 4


def test_run_gap_backfill_fetches_only_missing_ranges(db_session):
    db_session.add(Kline(
        symbol="BTCUSDT", timeframe="1h", open_time=datetime(2026, 1, 1, 0),
        open=Decimal("1"), high=Decimal("1"), low=Decimal("1"), close=Decimal("1"),
        volume=Decimal("1"), flagged=False,
    ))
    db_session.add(Kline(
        symbol="BTCUSDT", timeframe="1h", open_time=datetime(2026, 1, 1, 2),
        open=Decimal("1"), high=Decimal("1"), low=Decimal("1"), close=Decimal("1"),
        volume=Decimal("1"), flagged=False,
    ))
    db_session.commit()

    calls = []

    def fake_fetch_and_store(session, client, symbol, timeframe, start_ms, end_ms):
        calls.append((start_ms, end_ms))
        return FetchResult(symbol=symbol, timeframe=timeframe, fetched=0, inserted=0, updated=0, flagged=0)

    with patch.object(backfill_module, "fetch_and_store", side_effect=fake_fetch_and_store):
        results = backfill_module.run_gap_backfill(
            db_session, binance_client=object(), symbol="BTCUSDT", timeframe="1h",
            range_start=datetime(2026, 1, 1, 0), range_end=datetime(2026, 1, 1, 2),
        )
    assert len(results) == 1
    assert len(calls) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_backfill.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.backfill'`)

- [ ] **Step 3: Write minimal implementation**

`src/backfill.py`:
```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.db.models import Kline
from src.integrity import detect_gaps
from src.kline_fetcher import fetch_and_store


def _to_epoch_ms(naive_utc_dt: datetime) -> int:
    return int(naive_utc_dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def run_initial_backfill(session, binance_client, symbols: list, timeframes: list, since_days: int = 730) -> list:
    end = datetime.now(timezone.utc).replace(tzinfo=None)
    start = end - timedelta(days=since_days)
    results = []
    for symbol in symbols:
        for timeframe in timeframes:
            result = fetch_and_store(
                session, binance_client, symbol, timeframe,
                start_ms=_to_epoch_ms(start),
                end_ms=_to_epoch_ms(end),
            )
            results.append(result)
    return results


def run_gap_backfill(session, binance_client, symbol: str, timeframe: str, range_start: datetime, range_end: datetime) -> list:
    existing_times = [
        row.open_time for row in
        session.query(Kline.open_time)
        .filter(Kline.symbol == symbol, Kline.timeframe == timeframe,
                Kline.open_time >= range_start, Kline.open_time <= range_end)
        .all()
    ]
    gaps = detect_gaps(existing_times, timeframe, range_start, range_end)

    results = []
    for gap in gaps:
        result = fetch_and_store(
            session, binance_client, symbol, timeframe,
            start_ms=_to_epoch_ms(gap.start),
            end_ms=_to_epoch_ms(gap.end),
        )
        results.append(result)
    return results
```

**Note (added after Task 9's review):** `naive_utc_dt.timestamp()` alone is wrong here — Python's `datetime.timestamp()` on a naive datetime assumes the *host's local timezone*, not UTC, even though every `open_time`/`start`/`end` value in this codebase is a naive datetime that *represents* UTC (per `binance_client.py`'s `.replace(tzinfo=timezone.utc).replace(tzinfo=None)` convention). `_to_epoch_ms` re-attaches `tzinfo=timezone.utc` before converting, which is the correct inverse of that convention. Task 11's scheduler.py has the same conversion and needs the same helper.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_backfill.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/backfill.py tests/test_backfill.py
git commit -m "feat: add initial and gap backfill orchestration"
```

---

### Task 10: Fetch log (crash-recovery bookkeeping)

**Files:**
- Create: `src/fetch_log.py`
- Test: `tests/test_fetch_log.py`

**Interfaces:**
- Consumes: `db.models.FetchLog` (Task 2)
- Produces: `fetch_log.record_run(session, symbol, timeframe, status, started_at, finished_at, error_message=None) -> None`, `fetch_log.get_last_successful_run(session, symbol, timeframe) -> datetime` (or `None` if no successful run recorded)

- [ ] **Step 1: Write the failing tests**

`tests/test_fetch_log.py`:
```python
from datetime import datetime

from src.fetch_log import record_run, get_last_successful_run


def test_get_last_successful_run_returns_none_when_no_history(db_session):
    assert get_last_successful_run(db_session, "BTCUSDT", "1h") is None


def test_get_last_successful_run_returns_latest_success_only(db_session):
    record_run(db_session, "BTCUSDT", "1h", status="success",
               started_at=datetime(2026, 1, 1, 0), finished_at=datetime(2026, 1, 1, 0, 5))
    record_run(db_session, "BTCUSDT", "1h", status="error",
               started_at=datetime(2026, 1, 2, 0), finished_at=datetime(2026, 1, 2, 0, 5),
               error_message="boom")
    record_run(db_session, "BTCUSDT", "1h", status="success",
               started_at=datetime(2026, 1, 3, 0), finished_at=datetime(2026, 1, 3, 0, 5))

    result = get_last_successful_run(db_session, "BTCUSDT", "1h")
    assert result == datetime(2026, 1, 3, 0, 5)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fetch_log.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.fetch_log'`)

- [ ] **Step 3: Write minimal implementation**

`src/fetch_log.py`:
```python
from __future__ import annotations

from datetime import datetime

from src.db.models import FetchLog


def record_run(session, symbol: str, timeframe: str, status: str, started_at: datetime, finished_at: datetime, error_message: str = None) -> None:
    session.add(FetchLog(
        symbol=symbol, timeframe=timeframe, status=status,
        started_at=started_at, finished_at=finished_at, error_message=error_message,
    ))
    session.commit()


def get_last_successful_run(session, symbol: str, timeframe: str):
    row = (
        session.query(FetchLog)
        .filter(FetchLog.symbol == symbol, FetchLog.timeframe == timeframe, FetchLog.status == "success")
        .order_by(FetchLog.finished_at.desc())
        .first()
    )
    return row.finished_at if row else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fetch_log.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/fetch_log.py tests/test_fetch_log.py
git commit -m "feat: add fetch log for crash-recovery bookkeeping"
```

---

### Task 11: Scheduler

**Files:**
- Create: `src/scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `kline_fetcher.fetch_and_store` (Task 8), `symbol_registry.refresh_symbols` (Task 6), `fetch_log.record_run` / `get_last_successful_run` (Task 10), `db.models.Symbol` (Task 2)
- Produces: `scheduler.run_timeframe_job(session_factory, binance_client, timeframe: str) -> None`, `scheduler.run_symbol_refresh_job(session_factory, binance_client) -> None`, `scheduler.build_scheduler(session_factory, binance_client) -> BackgroundScheduler` (registers 3 jobs: `id="hourly_klines"` for `1h`, `id="daily_klines"` for `1d`, `id="symbol_refresh"`)

- [ ] **Step 1: Write the failing tests**

`tests/test_scheduler.py`:
```python
from datetime import datetime

from src.db.models import Symbol
from src.scheduler import build_scheduler, run_timeframe_job, run_symbol_refresh_job
from src.db.models import FetchLog


class _FakeBinanceClient:
    def __init__(self):
        self.symbols_requested = []

    def get_active_usdt_symbols(self):
        return [{"symbol": "BTCUSDT", "base_asset": "BTC", "quote_asset": "USDT"}]

    def get_klines(self, symbol, timeframe, start_ms, end_ms):
        self.symbols_requested.append(symbol)
        return []


def test_build_scheduler_registers_expected_jobs():
    scheduler = build_scheduler(session_factory=lambda: None, binance_client=None)
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert job_ids == {"hourly_klines", "daily_klines", "symbol_refresh"}


def test_run_timeframe_job_fetches_active_symbols_and_records_log(db_session):
    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True))
    db_session.commit()
    fake_client = _FakeBinanceClient()

    run_timeframe_job(session_factory=lambda: db_session, binance_client=fake_client, timeframe="1h")

    assert fake_client.symbols_requested == ["BTCUSDT"]
    log_row = db_session.query(FetchLog).first()
    assert log_row.symbol == "BTCUSDT"
    assert log_row.status == "success"


def test_run_symbol_refresh_job_upserts_symbols(db_session):
    fake_client = _FakeBinanceClient()
    run_symbol_refresh_job(session_factory=lambda: db_session, binance_client=fake_client)
    assert db_session.get(Symbol, "BTCUSDT") is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scheduler.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.scheduler'`)

- [ ] **Step 3: Write minimal implementation**

`src/scheduler.py`:
```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from src.db.models import Symbol
from src.fetch_log import get_last_successful_run, record_run
from src.kline_fetcher import fetch_and_store
from src.symbol_registry import refresh_symbols


def _to_epoch_ms(naive_utc_dt: datetime) -> int:
    return int(naive_utc_dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def run_timeframe_job(session_factory, binance_client, timeframe: str) -> None:
    session = session_factory()
    try:
        symbols = [row.symbol for row in session.query(Symbol).filter(Symbol.is_active == True).all()]  # noqa: E712
        end = datetime.now(timezone.utc).replace(tzinfo=None)
        for symbol in symbols:
            last_success = get_last_successful_run(session, symbol, timeframe)
            start = last_success if last_success else end - timedelta(days=730)
            started_at = datetime.now(timezone.utc).replace(tzinfo=None)
            result = fetch_and_store(
                session, binance_client, symbol, timeframe,
                start_ms=_to_epoch_ms(start),
                end_ms=_to_epoch_ms(end),
            )
            finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
            record_run(
                session, symbol, timeframe,
                status="error" if result.error else "success",
                started_at=started_at, finished_at=finished_at,
                error_message=result.error,
            )
    finally:
        session.close()


def run_symbol_refresh_job(session_factory, binance_client) -> None:
    session = session_factory()
    try:
        refresh_symbols(session, binance_client)
    finally:
        session.close()


def build_scheduler(session_factory, binance_client) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        lambda: run_timeframe_job(session_factory, binance_client, "1h"),
        CronTrigger(minute=5),
        id="hourly_klines",
    )
    scheduler.add_job(
        lambda: run_timeframe_job(session_factory, binance_client, "1d"),
        CronTrigger(hour=0, minute=10),
        id="daily_klines",
    )
    scheduler.add_job(
        lambda: run_symbol_refresh_job(session_factory, binance_client),
        CronTrigger(hour=0, minute=0),
        id="symbol_refresh",
    )
    return scheduler
```

**Note on `session.close()` in tests:** `Session.close()` releases the connection and expires loaded objects but does not make the `Session` object unusable — a later query on the same `Session` transparently opens a new connection. This is why the tests above can call `db_session.query(...)` for assertions after `run_timeframe_job`/`run_symbol_refresh_job` already closed that same session internally.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scheduler.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/scheduler.py tests/test_scheduler.py
git commit -m "feat: add in-process scheduler for hourly/daily jobs"
```

---

### Task 12: Main entrypoint

**Files:**
- Create: `src/main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `config.get_database_url` (Task 1), `db.session.make_engine/make_session_factory/create_all_tables` (Task 2), `binance_client.BinanceClient` (Task 5), `symbol_registry.refresh_symbols` (Task 6), `backfill.run_initial_backfill` (Task 9), `scheduler.build_scheduler` (Task 11)
- Produces: `main.startup() -> (session_factory, binance_client)` (creates tables, refreshes symbols, runs initial backfill only if the `klines` table is empty), `main.run_forever(session_factory, binance_client) -> None` (starts the scheduler and blocks), `main.main() -> None`

- [ ] **Step 1: Write the failing test**

`tests/test_main.py`:
```python
from unittest.mock import patch

import src.main as main_module
from src.db.models import Kline
from decimal import Decimal
from datetime import datetime


def test_startup_runs_initial_backfill_when_no_klines_exist(db_session, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")

    backfill_calls = []

    with patch.object(main_module, "make_engine", return_value=db_session.get_bind()), \
         patch.object(main_module, "create_all_tables", return_value=None), \
         patch.object(main_module, "make_session_factory", return_value=lambda: db_session), \
         patch.object(main_module, "BinanceClient", return_value=object()), \
         patch.object(main_module, "refresh_symbols", return_value=None), \
         patch.object(main_module, "run_initial_backfill", side_effect=lambda *a, **k: backfill_calls.append(a)):
        main_module.startup()

    assert len(backfill_calls) == 1


def test_startup_skips_initial_backfill_when_klines_already_exist(db_session, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    db_session.add(Kline(
        symbol="BTCUSDT", timeframe="1h", open_time=datetime(2026, 1, 1),
        open=Decimal("1"), high=Decimal("1"), low=Decimal("1"), close=Decimal("1"),
        volume=Decimal("1"), flagged=False,
    ))
    db_session.commit()

    backfill_calls = []

    with patch.object(main_module, "make_engine", return_value=db_session.get_bind()), \
         patch.object(main_module, "create_all_tables", return_value=None), \
         patch.object(main_module, "make_session_factory", return_value=lambda: db_session), \
         patch.object(main_module, "BinanceClient", return_value=object()), \
         patch.object(main_module, "refresh_symbols", return_value=None), \
         patch.object(main_module, "run_initial_backfill", side_effect=lambda *a, **k: backfill_calls.append(a)):
        main_module.startup()

    assert len(backfill_calls) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.main'`)

- [ ] **Step 3: Write minimal implementation**

`src/main.py`:
```python
from __future__ import annotations

import logging
import time

from src.backfill import run_initial_backfill
from src.binance_client import BinanceClient
from src.config import get_database_url
from src.db.models import Kline, Symbol
from src.db.session import create_all_tables, make_engine, make_session_factory
from src.scheduler import build_scheduler
from src.symbol_registry import refresh_symbols

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main")

TIMEFRAMES = ["1h", "1d"]


def startup():
    database_url = get_database_url()
    engine = make_engine(database_url)
    create_all_tables(engine)
    session_factory = make_session_factory(engine)
    binance_client = BinanceClient()

    session = session_factory()
    try:
        refresh_symbols(session, binance_client)
        has_data = session.query(Kline).first() is not None
        if not has_data:
            logger.info("No existing kline data found, running initial backfill")
            symbols = [row.symbol for row in session.query(Symbol).filter(Symbol.is_active == True).all()]  # noqa: E712
            run_initial_backfill(session, binance_client, symbols, TIMEFRAMES)
    finally:
        session.close()

    return session_factory, binance_client


def run_forever(session_factory, binance_client) -> None:
    scheduler = build_scheduler(session_factory, binance_client)
    scheduler.start()
    logger.info("Scheduler started, service running")
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


def main() -> None:
    session_factory, binance_client = startup()
    run_forever(session_factory, binance_client)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_main.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: PASS (all tests across every task)

- [ ] **Step 6: Commit**

```bash
git add src/main.py tests/test_main.py
git commit -m "feat: add main entrypoint wiring startup and scheduler"
```

---

### Task 13: README and local setup instructions

**Files:**
- Create: `README.md`

**Interfaces:**
- None (documentation only)

- [ ] **Step 1: Write the README**

`README.md`:
```markdown
# Kripto Analiz Ofisi — Binance Veri Altyapısı

Binance'teki tüm aktif USDT paritelerinin 1h/1d mum verisini sürekli çeken,
PostgreSQL'de saklayan ve kendi bütünlüğünü doğrulayan arka plan servisi.

## Kurulum

1. PostgreSQL'i başlat (Homebrew ile kuruluysa):
   ```bash
   brew services start postgresql@16
   ```
2. Veritabanını oluştur:
   ```bash
   createdb crypto_office
   ```
3. Bağımlılıkları kur:
   ```bash
   pip install -r requirements.txt
   ```
4. `.env.example` dosyasını `.env` olarak kopyala ve `DATABASE_URL`'i düzenle,
   sonra ortam değişkenini yükle (örn. `export $(cat .env | xargs)` veya
   shell profilinden `export DATABASE_URL=...`).

## Çalıştırma

```bash
python -m src.main
```

İlk çalıştırmada: sembol listesi çekilir, `klines` tablosu boşsa son 2 yıllık
geçmiş veri (backfill) indirilir, ardından zamanlayıcı devreye girer (1h
mumlar için saatlik, 1d mumlar için günlük, sembol listesi için günlük).
Servis `Ctrl+C` ile durdurulabilir; yeniden başlatıldığında `fetch_log`
tablosuna bakarak kaldığı yerden devam eder.

## Test

```bash
pytest -v
```

Tüm birim testler `sqlite:///:memory:` üzerinde çalışır — gerçek bir
PostgreSQL bağlantısı veya Binance API erişimi gerektirmez.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add setup and run instructions"
```

---

## Post-Plan Notes

- This plan covers Subsystem A only (data infrastructure), per `docs/superpowers/specs/2026-08-20-binance-data-infra-design.md`. Subsystems B (scenario engine), C (confidence learning loop), and D (paper testing) are separate specs/plans, to be brainstormed after this one is implemented and verified.
- Manual verification after Task 13 (not automated, requires a live Postgres + internet): run `python -m src.main`, confirm the `symbols` table populates from Binance, and let the initial backfill run for a small time before interrupting it to confirm `fetch_log` enables resume-from-last-success on restart.
