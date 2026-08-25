# Funding Rate Risk Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Perpetual futures funding rate'i aşırı olduğunda, sinyalin kendi yönü zaten kalabalık demektir — o senaryoyu üretmeden engellemek.

**Architecture:** Günlük sembol yenileme job'ı her sembolün USDT-M perpetual futures kontratı olup olmadığını `Symbol.has_futures_contract`'a yazar. Saatlik job, senaryo üretiminden önce tek bir bulk Binance isteğiyle tüm funding rate'leri `funding_rates` tablosuna tazeler. `process_symbol_scenario`, BTC rejim gate'inden hemen sonra bu tabloyu okuyup sinyali engelleyebilir.

**Tech Stack:** Python, SQLAlchemy (mevcut `Base` modelleri), python-binance public futures endpoint'leri (API key gerekmiyor), pytest + `sqlite:///:memory:` (mevcut `db_session` fixture'ı).

**Spec:** [docs/superpowers/specs/2026-08-25-funding-rate-gate-design.md](../specs/2026-08-25-funding-rate-gate-design.md)

## Global Constraints

- **Eşik:** `FUNDING_RATE_THRESHOLD = Decimal("0.0005")` (±%0.05). Karşılaştırmalar **kesin** (`>` / `<`) — tam eşik değerinde engellenmez.
- **Bayatlık toleransı:** `FUNDING_DATA_MAX_AGE = timedelta(hours=2)`.
- **`Symbol.has_futures_contract` `nullable=True` OLMAK ZORUNDA.** `src/db/session.py`'deki `sync_missing_columns` canlı veritabanına yalnızca nullable kolon ekler; `nullable=False` olursa kolonu atlar ve `symbols` tablosuna yapılan **her** sorgu patlar (Railway'deki servis komple düşer). Mevcut satırlar `NULL` başlar; kod `NULL`'ı "futures kontratı yok" olarak okur.
- **Futures filtresi:** `status == "TRADING" and quoteAsset == "USDT" and contractType == "PERPETUAL"`.
- **Sert engelleme:** Gate yalnızca `"skipped"` döndürür; `confidence_score` hesabına hiç dokunulmaz.
- **Futures kontratı olmayan sembol hiçbir koşulda bu gate yüzünden engellenmez** — o semboller için davranış bit düzeyinde değişmez.
- **Sessiz engelleme yasak:** her toplama çalıştırması `logger.info` ile özetlenir, boş feed `logger.warning`, gate'in her engellemesi sembol bazlı `logger.debug`.
- **İzolasyon:** funding toplama hatası saatlik job'ın diğer adımlarını (kline fetch, senaryo, öğrenme, paper trading) durdurmaz.

---

### Task 1: Şema — `FundingRate` tablosu ve `Symbol.has_futures_contract`

**Files:**
- Modify: `src/db/models.py:9-20` (Symbol), sonuna yeni model
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: `Symbol.has_futures_contract` (Boolean, nullable) ve `FundingRate` modeli (`symbol` PK, `funding_rate`, `fetched_at`) — Task 3, 4, 5 bunları kullanır.

- [ ] **Step 1: Write the failing tests**

`tests/test_models.py` satır 7'deki import'u genişlet (`datetime`, `Decimal` ve `Symbol` zaten import edilmiş durumda, yalnızca `FundingRate` eksik):

```python
from src.db.models import Symbol, Kline, FetchLog, Scenario, FundingRate
```

Sonra dosyanın sonuna testleri ekle:

```python
def test_symbol_has_futures_contract_defaults_to_false_and_allows_null(db_session):
    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True))
    db_session.commit()
    assert db_session.get(Symbol, "BTCUSDT").has_futures_contract is False

    # Nullable is a hard requirement: sync_missing_columns only ever adds
    # nullable columns to a live database, so a NOT NULL column here would
    # break every query against `symbols` on the deployed service.
    db_session.add(Symbol(
        symbol="NULLUSDT", base_asset="NULL", quote_asset="USDT",
        is_active=True, has_futures_contract=None,
    ))
    db_session.commit()
    assert db_session.get(Symbol, "NULLUSDT").has_futures_contract is None


def test_funding_rate_row_stores_one_row_per_symbol(db_session):
    now = datetime(2026, 8, 25, 12, 0)
    db_session.add(FundingRate(symbol="BTCUSDT", funding_rate=Decimal("0.00012345"), fetched_at=now))
    db_session.commit()

    row = db_session.get(FundingRate, "BTCUSDT")
    assert row.funding_rate == Decimal("0.00012345")
    assert row.fetched_at == now
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_models.py -v -k "futures_contract or funding_rate_row"`
Expected: FAIL — `ImportError: cannot import name 'FundingRate'` ve/veya `TypeError: 'has_futures_contract' is an invalid keyword argument for Symbol`

- [ ] **Step 3: Add the column and the model**

`src/db/models.py` içinde `Symbol` sınıfına, `is_active` satırının hemen altına:

```python
    # Nullable is mandatory, not a style choice: sync_missing_columns (see
    # src/db/session.py) only ever ADDs nullable columns to a live database.
    # A NOT NULL column here would be skipped there, and every ORM query
    # against `symbols` would then fail on the deployed service.
    has_futures_contract = Column(Boolean, nullable=True, default=False)
```

Dosyanın sonuna yeni model:

```python
class FundingRate(Base):
    __tablename__ = "funding_rates"

    # One row per symbol: only the latest print matters to the gate, so this
    # is a "last known value" table, not an append-only history.
    symbol = Column(String, primary_key=True)
    funding_rate = Column(Numeric(10, 8), nullable=False)
    fetched_at = Column(DateTime, nullable=False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_models.py -v`
Expected: hepsi PASS

- [ ] **Step 5: Commit**

```bash
git add src/db/models.py tests/test_models.py
git commit -m "feat: add funding_rates table and Symbol.has_futures_contract"
```

---

### Task 2: Binance client — futures sembol listesi ve bulk funding rate

**Files:**
- Modify: `src/binance_client.py` (sınıfa iki yeni metod)
- Test: `tests/test_binance_client.py`

**Interfaces:**
- Produces:
  - `BinanceClient.get_futures_usdt_symbols() -> set` — TRADING + USDT + PERPETUAL kontratların sembol adları.
  - `BinanceClient.get_funding_rates() -> dict` — `{symbol: Decimal}`, tüm semboller için tek istekte.

- [ ] **Step 1: Write the failing tests**

`tests/test_binance_client.py` içindeki `_FakeClient.__init__`'i genişlet ve iki fake metod ekle:

```python
class _FakeClient:
    def __init__(self, exchange_info=None, kline_pages=None, futures_exchange_info=None, mark_price=None):
        self._exchange_info = exchange_info or {"symbols": []}
        self._kline_pages = kline_pages or []
        self._futures_exchange_info = futures_exchange_info or {"symbols": []}
        self._mark_price = mark_price or []
        self._page_index = 0
        self.get_klines_calls = []

    def get_exchange_info(self):
        return self._exchange_info

    def futures_exchange_info(self):
        return self._futures_exchange_info

    def futures_mark_price(self):
        return self._mark_price

    def get_klines(self, symbol, interval, startTime, endTime, limit):
        self.get_klines_calls.append({"startTime": startTime, "endTime": endTime})
        if self._page_index >= len(self._kline_pages):
            return []
        page = self._kline_pages[self._page_index]
        self._page_index += 1
        return page
```

Dosyanın sonuna testleri ekle (`from decimal import Decimal` importunu dosyanın başına eklemeyi unutma):

```python
def test_get_futures_usdt_symbols_keeps_only_trading_usdt_perpetuals():
    fake = _FakeClient(futures_exchange_info={"symbols": [
        {"symbol": "BTCUSDT", "status": "TRADING", "quoteAsset": "USDT", "contractType": "PERPETUAL"},
        {"symbol": "ETHUSDT", "status": "TRADING", "quoteAsset": "USDT", "contractType": "PERPETUAL"},
        {"symbol": "BTCUSDT_260626", "status": "TRADING", "quoteAsset": "USDT", "contractType": "CURRENT_QUARTER"},
        {"symbol": "BTCUSDC", "status": "TRADING", "quoteAsset": "USDC", "contractType": "PERPETUAL"},
        {"symbol": "DEADUSDT", "status": "BREAK", "quoteAsset": "USDT", "contractType": "PERPETUAL"},
    ]})
    client = BinanceClient(client=fake)

    assert client.get_futures_usdt_symbols() == {"BTCUSDT", "ETHUSDT"}


def test_get_funding_rates_returns_decimals_keyed_by_symbol():
    fake = _FakeClient(mark_price=[
        {"symbol": "BTCUSDT", "markPrice": "79636.12", "lastFundingRate": "0.00005955"},
        {"symbol": "ETHUSDT", "markPrice": "3000.00", "lastFundingRate": "-0.00012000"},
    ])
    client = BinanceClient(client=fake)

    rates = client.get_funding_rates()

    assert rates == {"BTCUSDT": Decimal("0.00005955"), "ETHUSDT": Decimal("-0.00012000")}
    # Parsed via str(), never float, so the stored rate is exact.
    assert isinstance(rates["BTCUSDT"], Decimal)


def test_get_funding_rates_skips_entries_without_a_funding_rate():
    # The bulk endpoint covers every contract type; not all of them carry a
    # lastFundingRate field.
    fake = _FakeClient(mark_price=[
        {"symbol": "BTCUSDT", "lastFundingRate": "0.00005955"},
        {"symbol": "WEIRDUSDT", "markPrice": "1.0"},
        {"symbol": "EMPTYUSDT", "lastFundingRate": ""},
    ])
    client = BinanceClient(client=fake)

    assert client.get_funding_rates() == {"BTCUSDT": Decimal("0.00005955")}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_binance_client.py -v`
Expected: 3 yeni test FAIL — `AttributeError: 'BinanceClient' object has no attribute 'get_futures_usdt_symbols'`

- [ ] **Step 3: Add the two methods**

`src/binance_client.py` içinde `get_active_usdt_symbols`'ün hemen altına:

```python
    def get_futures_usdt_symbols(self) -> set:
        """Symbols that have a USDT-margined perpetual futures contract.

        Only PERPETUAL contracts have a funding rate in the sense the gate
        means; the quarterly ones settle instead.
        """
        info = self._backoff.call(self._client.futures_exchange_info)
        return {
            entry["symbol"]
            for entry in info["symbols"]
            if entry["status"] == "TRADING"
            and entry["quoteAsset"] == "USDT"
            and entry["contractType"] == "PERPETUAL"
        }

    def get_funding_rates(self) -> dict:
        """Latest funding rate for every futures symbol, in ONE request.

        `futures_mark_price()` with no symbol returns the whole board (~875
        rows), so tracking more symbols costs no extra API calls.
        """
        rows = self._backoff.call(self._client.futures_mark_price)
        rates = {}
        for entry in rows:
            raw = entry.get("lastFundingRate")
            if not raw:
                continue
            rates[entry["symbol"]] = Decimal(str(raw))
        return rates
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_binance_client.py -v`
Expected: hepsi PASS

- [ ] **Step 5: Commit**

```bash
git add src/binance_client.py tests/test_binance_client.py
git commit -m "feat: add futures perpetual symbol and bulk funding rate fetching"
```

---

### Task 3: Günlük sembol yenilemesi `has_futures_contract`'ı doldursun

**Files:**
- Modify: `src/storage.py` (yeni fonksiyon)
- Modify: `src/symbol_registry.py:9-27`
- Modify: `src/scheduler.py:184-198` (log satırı)
- Test: `tests/test_symbol_registry.py`, `tests/test_scheduler.py` (fake client)

**Interfaces:**
- Consumes: `BinanceClient.get_futures_usdt_symbols() -> set` (Task 2), `Symbol.has_futures_contract` (Task 1).
- Produces: `storage.set_futures_contract_flags(session, futures_symbols: set) -> None`; `SymbolRefreshResult` artık `futures_count` alanını da taşır.

- [ ] **Step 1: Write the failing tests**

`tests/test_symbol_registry.py` içindeki fake client'a yeni metodu ekle ve testleri güncelle/ekle:

```python
class _FakeBinanceClient:
    def __init__(self, symbols, futures_symbols=None):
        self._symbols = symbols
        self._futures_symbols = futures_symbols or set()

    def get_active_usdt_symbols(self):
        return self._symbols

    def get_futures_usdt_symbols(self):
        return self._futures_symbols
```

Dosyanın sonuna:

```python
def test_refresh_symbols_flags_symbols_that_have_a_futures_contract(db_session):
    fake = _FakeBinanceClient(
        [
            {"symbol": "BTCUSDT", "base_asset": "BTC", "quote_asset": "USDT"},
            {"symbol": "TINYUSDT", "base_asset": "TINY", "quote_asset": "USDT"},
        ],
        futures_symbols={"BTCUSDT"},
    )

    result = refresh_symbols(db_session, fake)

    assert result.futures_count == 1
    assert db_session.get(Symbol, "BTCUSDT").has_futures_contract is True
    assert db_session.get(Symbol, "TINYUSDT").has_futures_contract is False


def test_refresh_symbols_clears_the_flag_when_a_futures_contract_disappears(db_session):
    db_session.add(Symbol(
        symbol="GONEUSDT", base_asset="GONE", quote_asset="USDT",
        is_active=True, has_futures_contract=True,
    ))
    db_session.commit()
    fake = _FakeBinanceClient(
        [{"symbol": "GONEUSDT", "base_asset": "GONE", "quote_asset": "USDT"}],
        futures_symbols=set(),
    )

    refresh_symbols(db_session, fake)

    assert db_session.get(Symbol, "GONEUSDT").has_futures_contract is False
```

`tests/test_scheduler.py` içindeki `_FakeBinanceClient`'a (satır 17) da metodu ekle — bu sınıf `run_symbol_refresh_job` ile de kullanılıyor:

```python
    def get_futures_usdt_symbols(self):
        return {"BTCUSDT"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_symbol_registry.py -v`
Expected: 2 yeni test FAIL — `AttributeError: 'SymbolRefreshResult' object has no attribute 'futures_count'`

- [ ] **Step 3: Implement the flag update**

`src/storage.py` içinde `mark_symbols_inactive`'in hemen altına:

```python
def set_futures_contract_flags(session: Session, futures_symbols: set) -> None:
    """Record, for every known symbol, whether it has a perpetual futures contract.

    Every row is rewritten rather than only the ones in the set, so a contract
    that disappears clears the flag instead of leaving a stale True behind.
    """
    for sym in session.query(Symbol).all():
        sym.has_futures_contract = sym.symbol in futures_symbols
    session.commit()
```

`src/symbol_registry.py`'yi tamamen şu hale getir:

```python
from __future__ import annotations

from dataclasses import dataclass

from src.db.models import Symbol
from src.storage import mark_symbols_inactive, set_futures_contract_flags, upsert_symbols


@dataclass
class SymbolRefreshResult:
    active_count: int
    deactivated_count: int
    futures_count: int


def refresh_symbols(session, binance_client) -> SymbolRefreshResult:
    # Spot first: a client that fails here (the daily job tolerates that) must
    # fail before any futures call, exactly as it did before this gate existed.
    active_symbols = binance_client.get_active_usdt_symbols()
    active_names = {entry["symbol"] for entry in active_symbols}
    futures_symbols = binance_client.get_futures_usdt_symbols()

    previously_active = {
        row.symbol for row in session.query(Symbol).filter(Symbol.is_active == True).all()  # noqa: E712
    }

    upsert_symbols(session, active_symbols)
    mark_symbols_inactive(session, active_names)
    set_futures_contract_flags(session, futures_symbols)

    deactivated = previously_active - active_names
    return SymbolRefreshResult(
        active_count=len(active_names),
        deactivated_count=len(deactivated),
        futures_count=len(futures_symbols),
    )
```

`src/scheduler.py` satır 188-191'deki log çağrısını genişlet:

```python
        logger.info(
            "Symbol refresh finished: %d active, %d deactivated, %d with futures contracts",
            result.active_count, result.deactivated_count, result.futures_count,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_symbol_registry.py tests/test_scheduler.py -v`
Expected: hepsi PASS

- [ ] **Step 5: Commit**

```bash
git add src/storage.py src/symbol_registry.py src/scheduler.py tests/test_symbol_registry.py tests/test_scheduler.py
git commit -m "feat: record which symbols have a perpetual futures contract"
```

---

### Task 4: Funding rate toplayıcı

**Files:**
- Create: `src/funding_collector.py`
- Test: `tests/test_funding_collector.py`

**Interfaces:**
- Consumes: `BinanceClient.get_funding_rates() -> dict` (Task 2), `Symbol.has_futures_contract` + `FundingRate` (Task 1).
- Produces: `refresh_funding_rates(session, binance_client, now: datetime = None) -> FundingRefreshResult`, alanları `updated` ve `missing`.

- [ ] **Step 1: Write the failing tests**

`tests/test_funding_collector.py`:

```python
from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import FundingRate, Symbol
from src.funding_collector import refresh_funding_rates

NOW = datetime(2026, 8, 25, 12, 5)


class _FakeBinanceClient:
    def __init__(self, rates=None, boom=False):
        self._rates = rates or {}
        self._boom = boom
        self.calls = 0

    def get_funding_rates(self):
        self.calls += 1
        if self._boom:
            raise RuntimeError("binance down")
        return self._rates


def _symbol(db_session, name, has_futures):
    db_session.add(Symbol(
        symbol=name, base_asset=name[:-4], quote_asset="USDT",
        is_active=True, has_futures_contract=has_futures,
    ))
    db_session.commit()


def test_refresh_funding_rates_stores_rates_only_for_futures_symbols(db_session):
    _symbol(db_session, "BTCUSDT", True)
    _symbol(db_session, "TINYUSDT", False)
    fake = _FakeBinanceClient({"BTCUSDT": Decimal("0.0001"), "TINYUSDT": Decimal("0.0002")})

    result = refresh_funding_rates(db_session, fake, now=NOW)

    assert result.updated == 1
    assert result.missing == 0
    assert db_session.get(FundingRate, "BTCUSDT").funding_rate == Decimal("0.0001")
    assert db_session.get(FundingRate, "TINYUSDT") is None


def test_refresh_funding_rates_costs_exactly_one_api_call(db_session):
    _symbol(db_session, "BTCUSDT", True)
    _symbol(db_session, "ETHUSDT", True)
    fake = _FakeBinanceClient({"BTCUSDT": Decimal("0.0001"), "ETHUSDT": Decimal("0.0002")})

    refresh_funding_rates(db_session, fake, now=NOW)

    assert fake.calls == 1


def test_refresh_funding_rates_updates_the_existing_row_in_place(db_session):
    _symbol(db_session, "BTCUSDT", True)
    refresh_funding_rates(db_session, _FakeBinanceClient({"BTCUSDT": Decimal("0.0001")}), now=NOW)

    later = NOW + timedelta(hours=1)
    refresh_funding_rates(db_session, _FakeBinanceClient({"BTCUSDT": Decimal("0.0009")}), now=later)

    assert db_session.query(FundingRate).count() == 1
    row = db_session.get(FundingRate, "BTCUSDT")
    assert row.funding_rate == Decimal("0.0009")
    assert row.fetched_at == later


def test_refresh_funding_rates_leaves_a_stored_row_alone_when_the_feed_omits_it(db_session):
    _symbol(db_session, "BTCUSDT", True)
    refresh_funding_rates(db_session, _FakeBinanceClient({"BTCUSDT": Decimal("0.0001")}), now=NOW)

    later = NOW + timedelta(hours=1)
    result = refresh_funding_rates(db_session, _FakeBinanceClient({}), now=later)

    assert result.updated == 0
    assert result.missing == 1
    row = db_session.get(FundingRate, "BTCUSDT")
    # Untouched, so the gate's own staleness check is what retires it.
    assert row.funding_rate == Decimal("0.0001")
    assert row.fetched_at == NOW


def test_refresh_funding_rates_propagates_a_binance_failure(db_session):
    _symbol(db_session, "BTCUSDT", True)
    fake = _FakeBinanceClient(boom=True)

    try:
        refresh_funding_rates(db_session, fake, now=NOW)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the Binance failure to propagate to the scheduler")

    assert db_session.query(FundingRate).count() == 0


def test_refresh_funding_rates_warns_when_the_feed_is_completely_empty(db_session, caplog):
    import logging

    _symbol(db_session, "BTCUSDT", True)

    with caplog.at_level(logging.WARNING, logger="funding_collector"):
        refresh_funding_rates(db_session, _FakeBinanceClient({}), now=NOW)

    assert any("no funding rates" in record.getMessage() for record in caplog.records)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_funding_collector.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.funding_collector'`

- [ ] **Step 3: Implement `src/funding_collector.py`**

```python
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from src.db.models import FundingRate, Symbol
from src.timeutil import utc_now

logger = logging.getLogger("funding_collector")


@dataclass
class FundingRefreshResult:
    updated: int
    missing: int


def refresh_funding_rates(session, binance_client, now: datetime = None) -> FundingRefreshResult:
    """Store the latest funding rate for every symbol that has a futures contract.

    One bulk request covers the whole board, so this costs a single API call
    per run no matter how many symbols are tracked.

    Failures propagate: the caller (the hourly job) isolates this step, and
    swallowing the error here would hide a feed outage behind an "all fine"
    summary — the exact blindness the BTC regime filter's review flagged.
    """
    now = now if now is not None else utc_now()
    rates = binance_client.get_funding_rates()

    futures_symbols = [
        row.symbol
        for row in session.query(Symbol).filter(Symbol.has_futures_contract == True).all()  # noqa: E712
    ]

    updated = 0
    missing = 0
    for symbol in futures_symbols:
        rate = rates.get(symbol)
        if rate is None:
            # Contract delisted since the last daily symbol refresh. Leave any
            # stored row alone: the gate's staleness check retires it on its own.
            missing += 1
            continue
        row = session.get(FundingRate, symbol)
        if row is None:
            session.add(FundingRate(symbol=symbol, funding_rate=rate, fetched_at=now))
        else:
            row.funding_rate = rate
            row.fetched_at = now
        updated += 1
    session.commit()

    if not rates:
        logger.warning("Binance returned no funding rates at all")
    logger.info("Funding rates refreshed: %d updated, %d missing from the feed", updated, missing)
    return FundingRefreshResult(updated=updated, missing=missing)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_funding_collector.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/funding_collector.py tests/test_funding_collector.py
git commit -m "feat: collect latest perpetual funding rates in one bulk call"
```

---

### Task 5: Funding gate kararı

**Files:**
- Create: `src/funding_gate.py`
- Test: `tests/test_funding_gate.py`

**Interfaces:**
- Consumes: `Symbol.has_futures_contract` + `FundingRate` (Task 1).
- Produces: `funding_rejection(session, symbol: str, direction: str, now: datetime) -> str | None` — engelleme sebebi ya da `None`. Ayrıca `FUNDING_RATE_THRESHOLD` ve `FUNDING_DATA_MAX_AGE` sabitleri (Task 6'nın testleri bunları import eder).

- [ ] **Step 1: Write the failing tests**

`tests/test_funding_gate.py`:

```python
from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import FundingRate, Symbol
from src.funding_gate import FUNDING_DATA_MAX_AGE, FUNDING_RATE_THRESHOLD, funding_rejection

NOW = datetime(2026, 8, 25, 12, 5)


def _setup(db_session, has_futures=True, rate=None, fetched_at=NOW):
    db_session.add(Symbol(
        symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT",
        is_active=True, has_futures_contract=has_futures,
    ))
    if rate is not None:
        db_session.add(FundingRate(symbol="BTCUSDT", funding_rate=rate, fetched_at=fetched_at))
    db_session.commit()


def test_no_futures_contract_is_never_blocked(db_session):
    _setup(db_session, has_futures=False)
    assert funding_rejection(db_session, "BTCUSDT", "long", NOW) is None


def test_unknown_futures_contract_flag_is_never_blocked(db_session):
    # Rows that predate the column carry NULL until the daily refresh runs.
    _setup(db_session, has_futures=None)
    assert funding_rejection(db_session, "BTCUSDT", "long", NOW) is None


def test_symbol_absent_from_the_symbols_table_is_never_blocked(db_session):
    assert funding_rejection(db_session, "GHOSTUSDT", "long", NOW) is None


def test_missing_funding_data_blocks(db_session):
    _setup(db_session, has_futures=True, rate=None)
    assert funding_rejection(db_session, "BTCUSDT", "long", NOW) is not None


def test_stale_funding_data_blocks(db_session):
    _setup(db_session, rate=Decimal("0"), fetched_at=NOW - FUNDING_DATA_MAX_AGE - timedelta(minutes=1))
    assert funding_rejection(db_session, "BTCUSDT", "long", NOW) is not None


def test_funding_data_inside_the_tolerance_does_not_block(db_session):
    _setup(db_session, rate=Decimal("0"), fetched_at=NOW - FUNDING_DATA_MAX_AGE + timedelta(minutes=1))
    assert funding_rejection(db_session, "BTCUSDT", "long", NOW) is None


def test_crowded_longs_block_a_long_signal(db_session):
    _setup(db_session, rate=FUNDING_RATE_THRESHOLD + Decimal("0.0001"))
    assert funding_rejection(db_session, "BTCUSDT", "long", NOW) is not None


def test_crowded_longs_do_not_block_a_short_signal(db_session):
    _setup(db_session, rate=FUNDING_RATE_THRESHOLD + Decimal("0.0001"))
    assert funding_rejection(db_session, "BTCUSDT", "short", NOW) is None


def test_crowded_shorts_block_a_short_signal(db_session):
    _setup(db_session, rate=-FUNDING_RATE_THRESHOLD - Decimal("0.0001"))
    assert funding_rejection(db_session, "BTCUSDT", "short", NOW) is not None


def test_crowded_shorts_do_not_block_a_long_signal(db_session):
    _setup(db_session, rate=-FUNDING_RATE_THRESHOLD - Decimal("0.0001"))
    assert funding_rejection(db_session, "BTCUSDT", "long", NOW) is None


def test_exactly_at_the_threshold_does_not_block(db_session):
    # The comparison is strict, so the threshold value itself is allowed.
    _setup(db_session, rate=FUNDING_RATE_THRESHOLD)
    assert funding_rejection(db_session, "BTCUSDT", "long", NOW) is None


def test_normal_funding_does_not_block_either_direction(db_session):
    _setup(db_session, rate=Decimal("0.00005955"))
    assert funding_rejection(db_session, "BTCUSDT", "long", NOW) is None
    assert funding_rejection(db_session, "BTCUSDT", "short", NOW) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_funding_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.funding_gate'`

- [ ] **Step 3: Implement `src/funding_gate.py`**

```python
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import FundingRate, Symbol

# Binance funding normally sits within ±0.01%-0.05% per 8-hour interval.
# Beyond this, the crowded side is paying to stay in — the squeeze setup this
# gate exists to stay out of.
FUNDING_RATE_THRESHOLD = Decimal("0.0005")

# One failed hourly refresh is tolerable; a persistently stale rate is not, so
# past this age the gate blocks rather than trade on a number of unknown age.
FUNDING_DATA_MAX_AGE = timedelta(hours=2)


def funding_rejection(session, symbol: str, direction: str, now: datetime) -> str | None:
    """Why this signal must not become a scenario, or None if it may.

    Same contract as `scenario_runner._window_rejection`: a reason string for
    the caller to log, or None. Returning a reason rather than a bare bool is
    deliberate — the BTC regime filter's review found that silent blocking is
    indistinguishable from "no signal" in the logs.

    A symbol with no perpetual futures contract is never blocked: there is no
    funding market to read, so the gate does not apply to it at all.
    """
    symbol_row = session.get(Symbol, symbol)
    if symbol_row is None or not symbol_row.has_futures_contract:
        return None

    row = session.get(FundingRate, symbol)
    if row is None:
        return "no funding data yet for a symbol that has a futures contract"

    age = now - row.fetched_at
    if age > FUNDING_DATA_MAX_AGE:
        return "stale funding data: fetched at %s (%s old)" % (row.fetched_at, age)

    rate = row.funding_rate
    if direction == "long" and rate > FUNDING_RATE_THRESHOLD:
        return "funding rate %s is above +%s — longs already crowded" % (rate, FUNDING_RATE_THRESHOLD)
    if direction == "short" and rate < -FUNDING_RATE_THRESHOLD:
        return "funding rate %s is below -%s — shorts already crowded" % (rate, FUNDING_RATE_THRESHOLD)
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_funding_gate.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add src/funding_gate.py tests/test_funding_gate.py
git commit -m "feat: add funding rate risk gate decision logic"
```

---

### Task 6: Gate'i senaryo üretimine bağla

**Files:**
- Modify: `src/scenario_runner.py:7-13` (import), `src/scenario_runner.py:111-119` (gate)
- Test: `tests/test_scenario_runner.py`

**Interfaces:**
- Consumes: `funding_rejection(session, symbol, direction, now) -> str | None`, `FUNDING_RATE_THRESHOLD` (Task 5); `FundingRate`, `Symbol.has_futures_contract` (Task 1).
- Produces: davranış değişikliği — `process_symbol_scenario` funding gate'i de uygular. İmza değişmez.

- [ ] **Step 1: Write the failing tests**

Önce `tests/test_scenario_runner.py`'nin başındaki iki import satırını şu hale getir (`Decimal`, `NOW` ve `_seed_signal_klines` zaten mevcut):

```python
from src.db.models import FundingRate, Kline, Scenario, Symbol
from src.funding_gate import FUNDING_RATE_THRESHOLD
```

Sonra dosyanın sonuna testleri ekle:

```python
def _seed_futures_symbol(db_session, symbol, rate, fetched_at=NOW):
    db_session.add(Symbol(
        symbol=symbol, base_asset=symbol[:-4], quote_asset="USDT",
        is_active=True, has_futures_contract=True,
    ))
    db_session.add(FundingRate(symbol=symbol, funding_rate=rate, fetched_at=fetched_at))
    db_session.commit()


def test_process_symbol_scenario_skips_a_long_when_longs_are_crowded(db_session):
    _seed_signal_klines(db_session, symbol="BTCUSDT")
    _seed_futures_symbol(db_session, "BTCUSDT", FUNDING_RATE_THRESHOLD + Decimal("0.0001"))

    assert process_symbol_scenario(db_session, "BTCUSDT", regime="up", now=NOW) == "skipped"
    assert db_session.query(Scenario).count() == 0


def test_process_symbol_scenario_generates_when_funding_is_normal(db_session):
    _seed_signal_klines(db_session, symbol="BTCUSDT")
    _seed_futures_symbol(db_session, "BTCUSDT", Decimal("0.00005955"))

    assert process_symbol_scenario(db_session, "BTCUSDT", regime="up", now=NOW) == "generated"
    assert db_session.query(Scenario).count() == 1


def test_process_symbol_scenario_ignores_funding_for_a_symbol_without_futures(db_session):
    """Regression: symbols with no perpetual contract must behave exactly as
    they did before this gate existed."""
    _seed_signal_klines(db_session, symbol="BTCUSDT")
    db_session.add(Symbol(
        symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT",
        is_active=True, has_futures_contract=False,
    ))
    db_session.commit()

    assert process_symbol_scenario(db_session, "BTCUSDT", regime="up", now=NOW) == "generated"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_scenario_runner.py -v -k crowded`
Expected: FAIL — `test_process_symbol_scenario_skips_a_long_when_longs_are_crowded` "generated" döner (gate henüz yok)

- [ ] **Step 3: Add the gate to `process_symbol_scenario`**

`src/scenario_runner.py` importlarına ekle (alfabetik: `src.db.models`'ten sonra, `src.integrity`'den önce):

```python
from src.funding_gate import funding_rejection
```

`process_symbol_scenario` içinde, `if signal.direction == "short" and regime != "down": return "skipped"` satırının hemen ardına, `has_pending_scenario` kontrolünden önce:

```python
    funding_block = funding_rejection(session, symbol, signal.direction, now)
    if funding_block is not None:
        logger.debug("Skipping %s: %s", symbol, funding_block)
        return "skipped"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_scenario_runner.py -v`
Expected: hepsi PASS (mevcut testler `Symbol` satırı hiç eklemediği için gate onlara uygulanmaz — davranışları değişmez)

- [ ] **Step 5: Commit**

```bash
git add src/scenario_runner.py tests/test_scenario_runner.py
git commit -m "feat: block scenarios whose direction is already crowded by funding"
```

---

### Task 7: Saatlik job funding rate'leri tazelesin

**Files:**
- Modify: `src/scheduler.py:9-19` (import), `src/scheduler.py:137-179` (job gövdesi ve özet)
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `refresh_funding_rates(session, binance_client, now=None) -> FundingRefreshResult` (Task 4).
- Produces: saatlik job özet log formatı artık `gaps filled`'dan sonra `, %d funding rates updated` içerir.

- [ ] **Step 1: Update the test doubles and the summary assertions**

`tests/test_scheduler.py` içinde **üç** fake client sınıfına `get_funding_rates` ekle — üçü de `run_timeframe_job(..., timeframe="1h")` ile kullanılıyor, metod olmadan funding adımı sessizce `AttributeError` ile patlar:

`_FakeBinanceClient` (satır ~17), `_PartiallyFailingBinanceClient` (satır ~31) ve `_GapServingClient` (satır ~236) sınıflarının her birine:

```python
    def get_funding_rates(self):
        return {}
```

Sonra, dosyadaki **her** özet-log iddiasını güncelle. Kural mekanik ve tek tip — funding adımı gap onarımından sonra, senaryo üretiminden önce çalıştığı için segment oraya girer:

- `"0 gaps filled, "` → `"0 gaps filled, 0 funding rates updated, "`
- `"1 gaps filled, "` → `"1 gaps filled, 0 funding rates updated, "`

Bu, şu satırlardaki iddiaları etkiler: 292, 311, 329, 349, 389, 459, 514 (toplam 7 iddia).

Ayrıca dosyanın sonuna üç yeni test ekle:

```python
def test_run_timeframe_job_refreshes_funding_before_generating_scenarios(db_session, monkeypatch):
    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True))
    db_session.commit()

    order = []

    def fake_refresh_funding_rates(session, binance_client, now=None):
        from src.funding_collector import FundingRefreshResult
        order.append("funding")
        return FundingRefreshResult(updated=3, missing=0)

    def fake_run_scenario_generation(session, symbols):
        from src.scenario_runner import ScenarioRunResult
        order.append("scenarios")
        return ScenarioRunResult(scanned=0, generated=0, skipped=0, failed=0)

    monkeypatch.setattr(scheduler_module, "refresh_funding_rates", fake_refresh_funding_rates)
    monkeypatch.setattr(scheduler_module, "run_scenario_generation", fake_run_scenario_generation)

    run_timeframe_job(session_factory=lambda: db_session, binance_client=_FakeBinanceClient(), timeframe="1h")

    assert order == ["funding", "scenarios"]


def test_run_timeframe_job_survives_a_funding_refresh_failure(db_session, caplog, monkeypatch):
    """A funding feed outage must not stop the fetch, scenario, learning or
    paper-trading steps — only the gate's own staleness check reacts to it."""
    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True))
    db_session.commit()

    def boom(session, binance_client, now=None):
        raise RuntimeError("funding feed exploded")

    monkeypatch.setattr(scheduler_module, "refresh_funding_rates", boom)

    with caplog.at_level(logging.INFO, logger="scheduler"):
        run_timeframe_job(session_factory=lambda: db_session, binance_client=_FakeBinanceClient(), timeframe="1h")

    # No funding segment, but every downstream step still reported.
    assert _summary_lines(caplog) == [
        "1h job finished: 1 symbols succeeded, 0 failed, 0 gaps filled, 0 scenarios generated, "
        "0 resolved, 0 calibrated, 0 positions closed, 0 opened"
    ]
    assert any(record.exc_info for record in caplog.records if record.levelno >= logging.ERROR)


def test_run_timeframe_job_does_not_refresh_funding_for_1d(db_session, monkeypatch):
    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True))
    db_session.commit()

    calls = []
    monkeypatch.setattr(
        scheduler_module, "refresh_funding_rates",
        lambda session, binance_client, now=None: calls.append(now),
    )

    run_timeframe_job(session_factory=lambda: db_session, binance_client=_FakeBinanceClient(), timeframe="1d")

    assert calls == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_scheduler.py -v`
Expected: FAIL — `AttributeError: module 'src.scheduler' has no attribute 'refresh_funding_rates'` ve güncellenmiş özet iddiaları eşleşmiyor

- [ ] **Step 3: Wire the refresh into the hourly job**

`src/scheduler.py` importlarına ekle (`from src.fetch_log import record_run` satırının hemen ardına, alfabetik sırada):

```python
from src.funding_collector import refresh_funding_rates
```

`run_timeframe_job` içinde, `scenario_result = None` bloğunu şu hale getir:

```python
        funding_result = None
        scenario_result = None
        learning_result = None
        paper_result = None
        if timeframe == "1h":
            try:
                funding_result = refresh_funding_rates(session, binance_client, now=end)
            except Exception:
                # Same isolation as the steps below. A funding feed outage must
                # not stop scenario generation, learning or paper trading — the
                # gate's own staleness check is what reacts to missing data.
                logger.exception("Funding rate refresh failed for the %s job", timeframe)
                session.rollback()

            try:
                scenario_result = run_scenario_generation(session, symbols)
```

(geri kalan `try/except` blokları aynen kalır.)

Özet log bloğunda, `gaps filled` ile `scenarios generated` arasına funding segmentini ekle:

```python
        fmt = "%s job finished: %d symbols succeeded, %d failed, %d gaps filled"
        args = [timeframe, succeeded, failed, gaps_filled]
        if funding_result is not None:
            fmt += ", %d funding rates updated"
            args.append(funding_result.updated)
        if scenario_result is not None:
            fmt += ", %d scenarios generated"
            args.append(scenario_result.generated)
```

(`learning_result` ve `paper_result` blokları aynen kalır.)

- [ ] **Step 4: Run the full suite**

Run: `python3 -m pytest tests/ -v`
Expected: hepsi PASS, çıktı temiz (uyarı/hata yok)

- [ ] **Step 5: Commit**

```bash
git add src/scheduler.py tests/test_scheduler.py
git commit -m "feat: refresh funding rates before each hourly scenario run"
```

---

### Task 8: README'yi güncelle

**Files:**
- Modify: `README.md` (Senaryo Üretimi bölümünden sonra yeni bölüm)

**Interfaces:**
- Consumes: Task 1-7'nin tamamlanmış davranışı.
- Produces: yok (yalnızca dokümantasyon).

- [ ] **Step 1: Add the section**

`README.md` içinde "## Senaryo Üretimi" bölümünün sonuna (yani "## Öğrenme Döngüsü" başlığından hemen önce) şu bölümü ekle:

```markdown
## Funding Rate Risk Filtresi

Senaryo üretiminden hemen önce, her saatlik çalıştırmada tüm perpetual futures kontratlarının
güncel funding rate'i tek bir Binance isteğiyle çekilip `funding_rates` tablosuna yazılır
(sembol başına tek satır — yalnızca en son değer tutulur, geçmiş saklanmaz).

Bir sinyal üretildiğinde, sembolün USDT-M perpetual futures kontratı varsa funding rate
kontrol edilir: funding **+%0.05'in üzerindeyse** long sinyalleri, **-%0.05'in altındaysa**
short sinyalleri reddedilir — bu, sinyalin gitmek istediği yönün zaten aşırı kalabalık ve
kaldıraçlı olduğu, yani squeeze riskinin yüksek olduğu anlamına gelir. Futures kontratı
olmayan semboller bu filtreden hiç etkilenmez.

Funding verisi hiç yoksa veya 2 saatten eskiyse, futures kontratı olan semboller için sinyal
üretilmez ("veri güvenilir değilse işlem yapma"). Hangi sembolde hangi kontratın olduğu,
günlük sembol yenileme job'ı tarafından `symbols.has_futures_contract` kolonuna yazılır.
```

- [ ] **Step 2: Verify the suite is still green**

Run: `python3 -m pytest tests/ -q`
Expected: hepsi PASS (README değişikliği koda dokunmaz; bu adım yalnızca ağacın temiz olduğunu doğrular)

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document the funding rate risk filter"
```
