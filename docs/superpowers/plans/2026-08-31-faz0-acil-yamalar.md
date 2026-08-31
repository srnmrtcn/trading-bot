# Faz 0 — Acil Yamalar Uygulama Planı

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Servisi crash-loop ve yarım kesilmiş job'dan korumak, kalibrasyon sızıntısını kapatmak ve saatlik zincire tek bir `now` vermek — beş bağımsız, küçük yama.

**Architecture:** Hiçbir yeni modül yok. Beş mevcut dosyada davranış düzeltmesi: `binance_client` (ping), `rate_limit` (retry politikası), `main` (SIGTERM), `learning_runner` (kalibrasyon hedefi), `scheduler` (`now=end`). Her görev kendi kırmızı testiyle başlar; testler `sqlite:///:memory:` üzerinde, ağ yok.

**Tech Stack:** Python 3.11, pytest, python-binance 1.0.37 (`BinanceAPIException`), `requests` istisnaları, APScheduler, `signal` modülü.

**Spec:** [docs/superpowers/specs/2026-08-31-audit-remediation-design.md](../specs/2026-08-31-audit-remediation-design.md) — bölüm "Faz 0".

## Global Constraints

- **Uygulayıcı etiketi:** her görev `Uygulayıcı: worker` ya da `Uygulayıcı: Claude` taşır. Worker görevlerinde hazırlık (test + docstring) Claude'undur; worker yalnızca "Files → Modify" altındaki dosyayı yazar. `QWEN.md` kuralı gereği worker `tests/` yazamaz, `git` çalıştıramaz, bağımlılık ekleyemez. 200 satır üstü dosyalar (`learning_runner.py` 231, `scheduler.py` 280) Claude'a atanmıştır — tek-atış tam-dosya yazımı bu boyutta güvenilir değil.
- **`float` yok:** fiyat/oran her yerde `Decimal`. (Bu fazda para hesabı yok; `Retry-After` saniye değeri `float` kalabilir — zaman, para değil.)
- **Naive UTC:** yeni zaman değeri yalnızca `src.timeutil.utc_now()`.
- **İzolasyon politikası:** var olan hiçbir `try/except` kaldırılmaz, daraltılmaz.
- **Sessiz engelleme yasak:** yeni retry/vazgeçme kararları `logger` ile yazılır.
- **Sabitler (spec'ten birebir):** `RETRY_AFTER_CAP_SECONDS = 120`, `NETWORK_MAX_RETRIES = 2`.
- **Commit'ler Claude tarafından atılır** (worker git çalıştıramaz); her görev sonunda tek commit.
- **Verify:** her görevin `verify` komutu tam dosya adıyla çalışır, `-k` filtresi yok.

---

### Task 1: Boot'ta Binance ping'i kapat

**Uygulayıcı:** worker
**Files:**
- Modify: `src/binance_client.py:18-23`
- Test: `tests/test_binance_client.py`

**Interfaces:**
- Consumes: `binance.client.Client(api_key, api_secret, requests_params=..., ping=...)`
- Produces: `BinanceClient()` kurucusu ağ çağrısı yapmaz. Başka görev buna bağlı değil.

- [ ] **Step 1: Write the failing test** (Claude)

`tests/test_binance_client.py` içinde `test_init_configures_a_request_timeout_so_the_client_never_hangs_forever` fonksiyonunun hemen altına:

```python
def test_init_never_pings_binance_so_an_outage_cannot_crash_the_boot():
    # python-binance's Client pings the API inside __init__ by default. That
    # call sits outside every try/except in main.startup, so a DNS failure or
    # a temporary IP ban at boot would take the whole service down with it.
    with patch("src.binance_client.Client") as mock_client_cls:
        BinanceClient()
        _, kwargs = mock_client_cls.call_args
        assert kwargs.get("ping") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_binance_client.py -v`
Expected: `test_init_never_pings_binance_so_an_outage_cannot_crash_the_boot` FAIL — `assert None is False`

- [ ] **Step 3: Implement** (worker)

`src/binance_client.py` içinde `BinanceClient.__init__`'i şu hale getir (yalnızca `Client(...)` çağrısı değişiyor):

```python
class BinanceClient:
    def __init__(self, client=None, backoff: RateLimitBackoff = None):
        # ping=False: the library pings the API inside __init__ by default,
        # and that call runs before any of main.startup's error handling —
        # a Binance outage at boot would otherwise crash-loop the service.
        self._client = client or Client(
            api_key="", api_secret="",
            requests_params={"timeout": REQUEST_TIMEOUT_SECONDS},
            ping=False,
        )
        self._backoff = backoff or RateLimitBackoff()
```

- [ ] **Step 4: Verify**

Run: `python3 -m pytest tests/test_binance_client.py -v`
Expected: hepsi PASS

- [ ] **Step 5: Commit** (Claude)

```bash
git add src/binance_client.py tests/test_binance_client.py
git commit -m "fix: do not ping Binance inside BinanceClient() so a boot-time outage cannot crash-loop the service"
```

---

### Task 2: Retry politikası — Retry-After tavanı, 418'de vazgeç, ağ hatasında sınırlı retry

**Uygulayıcı:** worker
**Files:**
- Modify: `src/rate_limit.py` (tam dosya, 37 satır)
- Test: `tests/test_rate_limit.py`

**Interfaces:**
- Consumes: `binance.exceptions.BinanceAPIException` (`.status_code`, `.response.headers`), `requests.exceptions.RequestException`
- Produces: `RateLimitBackoff.call(func, *args, **kwargs)` imzası değişmez; `RETRY_AFTER_CAP_SECONDS`, `NETWORK_MAX_RETRIES` modül sabitleri.

- [ ] **Step 1: Write the failing tests** (Claude)

`tests/test_rate_limit.py` dosyasının başındaki import bloğunu şöyle genişlet:

```python
import logging

import pytest
import requests
from binance.exceptions import BinanceAPIException

from src.rate_limit import NETWORK_MAX_RETRIES, RETRY_AFTER_CAP_SECONDS, RateLimitBackoff
```

Mevcut `_RateLimitError` ve `_FakeResponse` sınıfları ve dört mevcut test **aynen kalır**. Dosyanın sonuna:

```python
def _api_error(status_code, headers=None):
    # A real BinanceAPIException, not a stand-in: the backoff reads
    # `.status_code` and `.response.headers`, and a library upgrade that
    # renames either must fail here rather than in production.
    return BinanceAPIException(_FakeResponse(headers or {}), status_code, '{"code":-1003,"msg":"limited"}')


def test_retry_after_is_capped_so_a_long_header_cannot_freeze_the_hourly_job():
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] == 1:
            raise _api_error(429, {"Retry-After": "7200"})
        return "ok"

    sleeps = []
    backoff = RateLimitBackoff(sleep_fn=sleeps.append)
    assert backoff.call(flaky) == "ok"
    assert sleeps == [float(RETRY_AFTER_CAP_SECONDS)]


def test_418_ip_ban_is_raised_immediately_without_sleeping():
    # A 418 bans the IP, not the symbol; retrying per symbol only stacks
    # sleeps while the hourly job starves. Fail fast, let the next hour try.
    calls = {"count": 0}

    def banned():
        calls["count"] += 1
        raise _api_error(418, {"Retry-After": "60"})

    sleeps = []
    backoff = RateLimitBackoff(sleep_fn=sleeps.append)
    with pytest.raises(BinanceAPIException):
        backoff.call(banned)
    assert calls["count"] == 1
    assert sleeps == []


def test_network_errors_are_retried_a_bounded_number_of_times():
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] <= NETWORK_MAX_RETRIES:
            raise requests.exceptions.ReadTimeout("read timed out")
        return "ok"

    sleeps = []
    backoff = RateLimitBackoff(base_delay=1.0, sleep_fn=sleeps.append)
    assert backoff.call(flaky) == "ok"
    assert calls["count"] == NETWORK_MAX_RETRIES + 1
    assert sleeps == [1.0, 2.0]


def test_network_errors_give_up_after_the_bound():
    def always_times_out():
        raise requests.exceptions.ConnectionError("unreachable")

    sleeps = []
    backoff = RateLimitBackoff(sleep_fn=sleeps.append)
    with pytest.raises(requests.exceptions.ConnectionError):
        backoff.call(always_times_out)
    assert len(sleeps) == NETWORK_MAX_RETRIES


def test_5xx_is_treated_like_a_network_error():
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] == 1:
            raise _api_error(503)
        return "ok"

    sleeps = []
    backoff = RateLimitBackoff(base_delay=1.0, sleep_fn=sleeps.append)
    assert backoff.call(flaky) == "ok"
    assert sleeps == [1.0]


def test_rate_limit_retries_are_still_bounded_by_max_retries_with_real_exception():
    def always_rate_limited():
        raise _api_error(429)

    backoff = RateLimitBackoff(max_retries=2, sleep_fn=lambda seconds: None)
    with pytest.raises(BinanceAPIException):
        backoff.call(always_rate_limited)


def test_every_retry_is_logged_so_a_slow_hour_is_explainable(caplog):
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] == 1:
            raise _api_error(429, {"Retry-After": "3"})
        return "ok"

    backoff = RateLimitBackoff(sleep_fn=lambda seconds: None)
    with caplog.at_level(logging.WARNING, logger="rate_limit"):
        backoff.call(flaky)
    assert any("429" in record.getMessage() for record in caplog.records)
```

Not: mevcut `test_backoff_raises_immediately_for_non_rate_limit_errors` sahte `_RateLimitError(status_code=500)` kullanıyor. Yeni politikada 5xx retry'lanır, ama o sahte sınıf `requests.RequestException` **değil** ve `BinanceAPIException` **değil**; yalnızca `status_code` taşıyor. Politika "5xx" kararını **yalnızca `BinanceAPIException`** için verir; tanınmayan istisna tipi hemen fırlatılır. Bu yüzden mevcut test değişmeden geçmeye devam eder.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_rate_limit.py -v`
Expected: `ImportError: cannot import name 'NETWORK_MAX_RETRIES'` (dosya toplu düşer)

- [ ] **Step 3: Implement** (worker) — `src/rate_limit.py` tam dosya:

```python
from __future__ import annotations

import logging
import time

import requests
from binance.exceptions import BinanceAPIException

logger = logging.getLogger("rate_limit")

# Binance's Retry-After climbs from seconds to days for repeat offenders.
# Sleeping the full value inside the hourly job would starve every later
# step (scenarios, learning, paper); cap it and let the next hour retry.
RETRY_AFTER_CAP_SECONDS = 120

# Transient network faults (timeouts, resets, 5xx) get a short exponential
# retry; anything still failing after this is the next hour's problem.
NETWORK_MAX_RETRIES = 2


def _is_network_error(exc) -> bool:
    if isinstance(exc, requests.exceptions.RequestException):
        return True
    return isinstance(exc, BinanceAPIException) and exc.status_code >= 500


class RateLimitBackoff:
    def __init__(self, max_retries: int = 5, base_delay: float = 1.0, sleep_fn=time.sleep):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.sleep_fn = sleep_fn

    def call(self, func, *args, **kwargs):
        """Retry policy, in order of precedence:

        1. 418 (IP ban): raise at once — the ban is per IP, retrying per
           symbol only stacks sleeps.
        2. 429 (rate limit): honor Retry-After capped at
           RETRY_AFTER_CAP_SECONDS, else exponential backoff; at most
           `max_retries` retries.
        3. Network faults (requests exceptions, HTTP 5xx from Binance):
           exponential backoff, at most NETWORK_MAX_RETRIES retries.
        4. Anything else: raise at once.
        Every sleep is logged at WARNING with the reason.
        """
        rate_limit_attempt = 0
        network_attempt = 0
        while True:
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                status_code = getattr(exc, "status_code", None)
                if status_code == 418:
                    logger.warning("Binance returned 418 (IP ban); not retrying")
                    raise
                if status_code == 429:
                    if rate_limit_attempt >= self.max_retries:
                        raise
                    delay = self._retry_after_seconds(exc)
                    if delay is None:
                        delay = self.base_delay * (2 ** rate_limit_attempt)
                    delay = min(delay, float(RETRY_AFTER_CAP_SECONDS))
                    logger.warning("Binance returned 429; sleeping %.0fs before retry %d", delay, rate_limit_attempt + 1)
                    self.sleep_fn(delay)
                    rate_limit_attempt += 1
                    continue
                if _is_network_error(exc):
                    if network_attempt >= NETWORK_MAX_RETRIES:
                        raise
                    delay = self.base_delay * (2 ** network_attempt)
                    logger.warning("Network error (%s); sleeping %.0fs before retry %d", exc, delay, network_attempt + 1)
                    self.sleep_fn(delay)
                    network_attempt += 1
                    continue
                raise

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

- [ ] **Step 4: Verify**

Run: `python3 -m pytest tests/test_rate_limit.py tests/test_binance_client.py -v`
Expected: hepsi PASS (mevcut dört test dahil)

- [ ] **Step 5: Commit** (Claude)

```bash
git add src/rate_limit.py tests/test_rate_limit.py
git commit -m "fix: cap Retry-After, fail fast on 418, retry transient network errors a bounded number of times"
```

---

### Task 3: SIGTERM'i yakala, çalışan job'ı bekleyerek kapan

**Uygulayıcı:** worker
**Files:**
- Modify: `src/main.py:96-108` (`run_forever`) ve import bloğu
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `signal.signal`, `signal.SIGTERM`; `BackgroundScheduler.shutdown(wait=True)`
- Produces: `main._raise_system_exit(signum, frame)` modül seviyesi fonksiyon (test doğrudan çağırır).

- [ ] **Step 1: Write the failing tests** (Claude)

`tests/test_main.py` içinde, `test_run_forever_starts_scheduler_serves_dashboard_and_shuts_down_on_exit` testinin `_FakeScheduler.shutdown` metodunu şöyle değiştir (imza `wait` alır, çağrı listesine değeri yazar) ve beklenen listeyi güncelle:

```python
    class _FakeScheduler:
        def start(self):
            calls.append("scheduler.start")

        def shutdown(self, wait=True):
            calls.append(("scheduler.shutdown", wait))
```

ve aynı testin sonundaki beklenti:

```python
    assert calls == [
        "get_basic_auth_credentials",
        "create_app",
        "build_scheduler",
        "scheduler.start",
        ("app.run", "0.0.0.0", 9000, False, False),
        # wait=True: a redeploy's SIGTERM must let the in-flight hourly job
        # finish its commit instead of dropping it mid-step.
        ("scheduler.shutdown", True),
    ]
```

`test_run_forever_defaults_to_port_8000_when_unset` içindeki `_FakeScheduler.shutdown(self)` imzasını da `shutdown(self, wait=True)` yap (gövde `pass` kalır).

Dosyanın sonuna iki yeni test:

```python
def test_sigterm_handler_raises_system_exit_so_the_finally_block_runs():
    # Python's default SIGTERM action ends the process without unwinding —
    # no `finally`, no scheduler.shutdown. Turning it into SystemExit reuses
    # the existing except/finally path that Ctrl+C already takes.
    with pytest.raises(SystemExit):
        main_module._raise_system_exit(signal.SIGTERM, None)


def test_run_forever_installs_the_sigterm_handler_before_serving(monkeypatch):
    installed = {}

    def _fake_signal(signum, handler):
        installed[signum] = handler

    class _FakeScheduler:
        def start(self):
            pass

        def shutdown(self, wait=True):
            pass

    class _FakeApp:
        def run(self, host, port, debug=None, use_reloader=None):
            raise SystemExit()

    monkeypatch.setattr(main_module.signal, "signal", _fake_signal)
    monkeypatch.setattr(main_module, "build_scheduler", lambda session_factory, binance_client: _FakeScheduler())
    monkeypatch.setattr(main_module, "get_basic_auth_credentials", lambda: ("admin", "hash"))
    monkeypatch.setattr(
        main_module, "create_app",
        lambda session_factory, auth_user, auth_pass_hash: _FakeApp(),
    )

    main_module.run_forever(session_factory=lambda: None, binance_client=None)

    assert installed[signal.SIGTERM] is main_module._raise_system_exit
```

Dosyanın başına `import signal` ve (yoksa) `import pytest` ekle.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_main.py -v`
Expected: `AttributeError: module 'src.main' has no attribute 'signal'` / `_raise_system_exit`; ilk test listede `"scheduler.shutdown"` vs `("scheduler.shutdown", True)` uyuşmazlığıyla düşer.

- [ ] **Step 3: Implement** (worker)

`src/main.py` import bloğuna (alfabetik sırada, `os`'tan sonra):

```python
import signal
```

Modül seviyesine, `TIMEFRAMES` tanımından sonra:

```python
def _raise_system_exit(signum, frame):
    # Railway sends SIGTERM on every redeploy. Python's default action ends
    # the process without unwinding, skipping scheduler.shutdown — so an
    # hourly job dies between two commits. SystemExit rides the same
    # except/finally path Ctrl+C already uses.
    raise SystemExit(0)
```

`run_forever`'ı şu hale getir:

```python
def run_forever(session_factory, binance_client) -> None:
    auth_user, auth_pass_hash = get_basic_auth_credentials()
    port = int(os.environ.get("PORT", 8000))
    app = create_app(session_factory, auth_user, auth_pass_hash)
    scheduler = build_scheduler(session_factory, binance_client)
    signal.signal(signal.SIGTERM, _raise_system_exit)
    scheduler.start()
    logger.info("Scheduler started, service running")
    try:
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        # wait=True blocks until the running job returns; Railway's kill
        # grace (raised in railway.json, Faz 2) is the outer bound.
        scheduler.shutdown(wait=True)
```

- [ ] **Step 4: Verify**

Run: `python3 -m pytest tests/test_main.py -v`
Expected: hepsi PASS

- [ ] **Step 5: Commit** (Claude)

```bash
git add src/main.py tests/test_main.py
git commit -m "fix: handle SIGTERM so a redeploy waits for the in-flight job instead of killing it mid-commit"
```

---

### Task 4: Kalibrasyon hedefi yalnızca `pending` senaryolar

**Uygulayıcı:** Claude (dosya 231 satır)
**Files:**
- Modify: `src/learning_runner.py:173`
- Test: `tests/test_learning_runner.py`

**Interfaces:**
- Consumes: `calibrate_scenarios(session) -> CalibrationResult` (değişmez)
- Produces: aynı fonksiyon; çözümlenmiş satırların `calibrated_confidence`'ı `NULL` kalır.

- [ ] **Step 1: Write the failing test**

`tests/test_learning_runner.py` içinde `test_calibrate_scenarios_does_not_touch_already_calibrated_rows` testinin hemen altına:

```python
def test_calibrate_scenarios_never_scores_a_resolved_row_by_its_own_outcome(db_session):
    # A resolved scenario that somehow reached this point uncalibrated (a
    # calibration failure in the run it resolved, or a row that predates the
    # column) is already in the pool its rate is computed from. Scoring it
    # now would be post-hoc; leave it NULL so nothing downstream mistakes it
    # for a prediction.
    for _ in range(20):
        db_session.add(_resolved_scenario("ETHUSDT", "long", Decimal("0.65"), "hit_target"))
    orphan = _resolved_scenario("BTCUSDT", "long", Decimal("0.65"), "hit_target")
    orphan.calibrated_confidence = None
    db_session.add(orphan)
    pending = _pending_scenario(symbol="SOLUSDT", direction="long")
    pending.confidence_score = Decimal("0.65")
    db_session.add(pending)
    db_session.commit()

    result = calibrate_scenarios(db_session)

    assert result.scenarios_updated == 1
    reloaded_orphan = db_session.query(Scenario).filter(Scenario.symbol == "BTCUSDT").first()
    assert reloaded_orphan.calibrated_confidence is None
    reloaded_pending = db_session.query(Scenario).filter(Scenario.symbol == "SOLUSDT").first()
    assert reloaded_pending.calibrated_confidence == Decimal("1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_learning_runner.py -v`
Expected: yeni test FAIL — `assert Decimal('1.0000') is None` (orphan puanlanmış)

- [ ] **Step 3: Implement**

`src/learning_runner.py:173` satırını:

```python
    targets = session.query(Scenario).filter(Scenario.calibrated_confidence.is_(None)).all()
```

şu hale getir:

```python
    # Only pending rows are predictions. A resolved row is already in the
    # pool above; scoring it would be post-hoc.
    targets = (
        session.query(Scenario)
        .filter(Scenario.calibrated_confidence.is_(None), Scenario.status == "pending")
        .all()
    )
```

- [ ] **Step 4: Verify**

Run: `python3 -m pytest tests/test_learning_runner.py -v`
Expected: hepsi PASS (`test_run_learning_cycle_survives_a_calibration_failure` dahil — o test çözümlenmiş satırın sonraki run'da puanlanmasını assert etmiyor; ediyorsa spec'e göre beklenti `None` olarak güncellenir)

- [ ] **Step 5: Commit**

```bash
git add src/learning_runner.py tests/test_learning_runner.py
git commit -m "fix: calibrate only pending scenarios so a resolved row is never scored by its own outcome"
```

---

### Task 5: Saatlik zincire tek `now` — `run_scenario_generation(..., now=end)`

**Uygulayıcı:** Claude (dosya 280 satır; ayrıca `tests/test_scheduler.py`'de 4 sahte imza değişiyor)
**Files:**
- Modify: `src/scheduler.py:195`
- Test: `tests/test_scheduler.py:389, 407, 427, 563, 611`

**Interfaces:**
- Consumes: `run_scenario_generation(session, symbols: list, now: datetime = None)` (zaten bu imzada)
- Produces: `run_timeframe_job` içindeki dört adım (`refresh_funding_rates`, `run_scenario_generation`, `run_learning_cycle`, `run_paper_trading_cycle`) aynı `end` değerini alır.

- [ ] **Step 1: Write the failing test**

`tests/test_scheduler.py` içindeki `test_run_timeframe_job_generates_scenarios_after_1h_fetch` testini şöyle güncelle:

```python
def test_run_timeframe_job_generates_scenarios_after_1h_fetch(db_session, monkeypatch):
    db_session.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", is_active=True))
    for i in range(100):
        db_session.add(_kline("BTCUSDT", "1h", datetime(2026, 1, 1) + timedelta(hours=i)))
    db_session.commit()

    calls = []

    def fake_run_scenario_generation(session, symbols, now=None):
        from src.scenario_runner import ScenarioRunResult
        calls.append((list(symbols), now))
        return ScenarioRunResult(scanned=len(symbols), generated=0, skipped=len(symbols), failed=0)

    monkeypatch.setattr(scheduler_module, "run_scenario_generation", fake_run_scenario_generation)

    job_now = datetime(2026, 1, 5, 4, 5, 0)
    run_timeframe_job(
        session_factory=lambda: db_session, binance_client=_FakeBinanceClient(), timeframe="1h", now=job_now,
    )

    # The job's own `end` is threaded through, the same instant learning and
    # paper trading get. Otherwise a fetch that crosses the hour boundary
    # makes scenario generation read a 5-minute stub as a closed candle.
    assert calls == [(["BTCUSDT"], job_now)]
```

Satır 407 (`def boom(session, symbols):`), 427 (lambda), 563 ve 611'deki sahte `run_scenario_generation` fonksiyonlarının imzasına `now=None` ekle; gövdeleri değişmez.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_scheduler.py -v`
Expected: `test_run_timeframe_job_generates_scenarios_after_1h_fetch` FAIL — `calls == [(['BTCUSDT'], None)]`

- [ ] **Step 3: Implement**

`src/scheduler.py:195`:

```python
                scenario_result = run_scenario_generation(session, symbols, now=end)
```

- [ ] **Step 4: Verify**

Run: `python3 -m pytest tests/test_scheduler.py -v && python3 -m pytest -q`
Expected: hepsi PASS, tam paket yeşil (Faz 0 sonunda 302 + 11 yeni = 313 test — plan container'da kuru koşuyla doğrulandı: 313 passed)

- [ ] **Step 5: Commit**

```bash
git add src/scheduler.py tests/test_scheduler.py
git commit -m "fix: pass the job's end time to scenario generation so all four hourly steps share one now"
```

---

### Faz 0 kapanışı (Claude)

- [ ] `python3 -m pytest -q` → tümü yeşil
- [ ] `gozden-gecirici` ile Task 1-3 diff'leri incelenir (worker işleri)
- [ ] `DURUM.md` yeniden üretilir ya da bu commit'te güncellenmez (Faz 2.9'da ele alınır)
- [ ] Kullanıcı onayıyla `git push origin main` (Railway deploy) — SIGTERM düzeltmesi bu deploy'da **henüz** aktif değil (eski süreç eski kodla kapanır), bir sonraki deploy'dan itibaren etkili.
