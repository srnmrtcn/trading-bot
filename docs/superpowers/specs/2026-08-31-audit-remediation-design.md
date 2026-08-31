# Denetim Sonrası Düzeltme ve Geliştirme — Tasarım

**Tarih:** 31 Ağustos 2026
**Kaynak:** [docs/superpowers/audits/2026-08-31-codebase-audit.md](../audits/2026-08-31-codebase-audit.md)
**Hedef (kullanıcı kararı):** Paper aşamasından sonra Binance USDT-M Futures'ta gerçek parayla çalışan bir trading bot. Bu spec, canlıya çıkmanın ön koşullarını kapsar; canlı execution katmanı **ayrı bir spec** olacak ve ancak Faz 1'in walk-forward kapısı geçilince başlar.

## Omurga

```
Faz 0  acil yamalar          → servis kırılmasın, sızıntı kapansın   (1 gece)
Faz 1  ölçüm + replay        → paper ve replay aynı futures gerçeğini ölçsün;
                               walk-forward out-of-sample beklentiyi göstersin  ← EDGE KAPISI
Faz 2  prod sağlamlık        → insansız aylarca çalışsın, repodan kurulabilsin
Faz 3  canlı katman          → ayrı spec; Faz 1 kapısı geçilmeden başlamaz
```

Kullanıcı kararları: uygulamayı `coder-worker` yapar (`motor: "ollama"`; aşağıda "Çalışma modeli"); RSI Wilder'a geçer; kalibre olmamış kovadan paper pozisyon açılmaz; Faz 1 paper ve replay düzeltmelerini tek fazda birleştirir.

## Çalışma modeli (`coder-worker` / `motor: "ollama"`)

Bu spec kapsamındaki bütün dolgu işleri `coder-worker` ile çalışır ve iş JSON'unda `motor: "ollama"` açıkça yazılır. `coder-ajan`, `motor: "qwen"`, `motor: "uret"` ve `gorev_uret.py` bu audit remediation akışında kullanılmaz. `gorev_uret.py` genel ajan altyapısında deneysel kalabilir; bu spec ona bağımlı değildir.

`coder-worker` tek-atış orchestrator üzerinden çalışır ve `QWEN.md` dosyasını otomatik yüklemez. Bu nedenle bağlayıcı kurallar her görevin `goal`, `files`, `context` ve `verify` alanlarına açıkça yazılır: worker `tests/` yazamaz, git çalıştıramaz, bağımlılık ekleyemez, görevde adı geçmeyen dosyaya dokunamaz ve ~250 satır üstü dosyada tam-dosya yazımı güvenilir değildir. Bu yüzden her görev iki yarımdır:

1. **Hazırlık (yönetici/Codex):** kırmızı testler, gerekiyorsa stub imza + adım adım docstring, görev tanımındaki dosya listesi ve `verify` komutu. Testler hedef kaynak dosya yazılmadan önce kırmızı doğrulanır; referans implementasyonla kapının geçilebilirliği kanıtlanır ve kaynak yeniden stub'a döndürülür.
2. **Dolgu (`coder-worker`):** yalnızca `files` alanında listelenen kaynak dosyaları yazar ve `verify` komutunu geçirir. Ardından yönetici diff'i inceler, tam test paketini çalıştırır ve commit eder.

Üç dosya worker sınırının üstünde ve Faz 0/1'de değişecek; **davranış değiştirmeden** önce bölünür (Claude, mevcut testler dokunulmadan yeşil kalır):

| dosya | satır | bölünme |
|---|---|---|
| `src/scheduler.py` | 280 | `src/kline_jobs.py` (`get_resume_point`, `repair_recent_gaps`, `refresh_regime_source`) + `src/scheduler.py` (job'lar, `build_scheduler`) |
| `src/learning_runner.py` | 231 | `src/outcome_resolver.py` (`_load_resolution_window`, `_resolution_window_end`, `_missing_candle_count`, `resolve_pending_scenarios`) + `src/learning_runner.py` (`calibrate_scenarios`, `run_learning_cycle`) |
| `src/research/funnel.py` | 391 | `src/research/funnel.py` (`analyze_symbol`, `FunnelCounts`, kural sinyalleri) + `src/research/replay.py` (`resolve_draft`, `fee_cost_in_r`, `passes_risk_filters`, `is_locked`, `iter_scenarios`, `backtest_symbol`) |

Eski modül adları import uyumluluğu için yeniden dışa aktarılır (`from src.outcome_resolver import resolve_pending_scenarios` gibi tek satır); testler değişmez.

`coder-worker`ın "imza ve sabit değiştirme" kuralı görev bazında açıkça aşılır: görev tanımı hangi sabitin/imzanın değiştiğini yazar, testler yeni değere göre yazılmıştır.

---

## Faz 0 — Acil yamalar

Hepsi bağımsız, hepsi küçük. Sıra önemsiz.

**0.1 Boot ping.** `BinanceClient.__init__` → `Client(..., ping=False)`. Test: `src.binance_client.Client` monkeypatch'lenir, kurucuya `ping=False` geçtiği doğrulanır.

**0.2 SIGTERM.** `main.run_forever` başında `signal.signal(SIGTERM, _raise_system_exit)` kaydeder; handler `SystemExit` fırlatır, mevcut `except (KeyboardInterrupt, SystemExit)` yakalar, `finally: scheduler.shutdown(wait=True)` çalışan job'ı bekler. Railway'in SIGKILL süresi (varsayılan ~10 s, `railway.json` ile Faz 2'de 60 s'ye çıkar) içinde job bitmezse yine kesilir — bu kabul edilen sınır; sembol bazlı commit'ler ve idempotent resume sayesinde veri bozulmaz. Test: handler `SystemExit` fırlatır; sahte `app.run` `SystemExit` fırlatınca `shutdown` `wait=True` ile çağrılır.

**0.3 Tek `now`.** `run_timeframe_job` → `run_scenario_generation(session, symbols, now=end)`. `tests/test_scheduler.py`'deki sahte fonksiyon imzaları `(session, symbols, now=None)` olur ve `now == end` assert edilir. (scheduler.py bölünmesinden sonra.)

**0.4 Kalibrasyon hedefi.** `calibrate_scenarios` hedef sorgusu `calibrated_confidence IS NULL AND status == "pending"`. Test: çözümlenmiş ama kalibre edilmemiş satır dokunulmadan kalır.

**0.5 Retry politikası.** `RateLimitBackoff.call`:
- 429 → mevcut davranış, ama `Retry-After` **`RETRY_AFTER_CAP_SECONDS = 120`** ile sınırlanır.
- 418 → **hiç retry yok**, hemen fırlatır (IP ban'ı sembol bazında denemek anlamsız; job hızlı biter, sonraki saat dener).
- `requests.exceptions.RequestException` (timeout, bağlantı) ve `status_code >= 500` → üstel backoff ile en fazla **`NETWORK_MAX_RETRIES = 2`** ek deneme.
- Diğer her şey → hemen fırlatır.
Testler gerçek `BinanceAPIException` ve `requests.ReadTimeout` nesneleriyle yazılır (sahte exception değil).

---

## Faz 1 — Ölçüm doğruluğu ve replay hizalaması

### 1.A Strateji versiyonu (kirli geçmişi izole et)

`src/strategy_version.py`: `STRATEGY_VERSION = "2026.09.futures-v1"`. `Scenario.strategy_version` ve `PaperPosition.strategy_version` **nullable** `String` kolonları. `insert_scenario` ve opener bunu yazar. Kalibrasyon havuzu, `current_equity`, `get_equity_summary`, `equity_sparkline_points`, dashboard listeleri `strategy_version == STRATEGY_VERSION` ile filtreler; `NULL` (eski) satırlar okunmaz ama silinmez. Yeni versiyon equity'si `STARTING_EQUITY`'den başlar. Kural/parametre değiştiren her gelecek değişiklik sabiti artırır — CLAUDE.md'ye bu kural eklenir.

### 1.B Paper açılış disiplini

- **Tazelik:** opener yalnızca `created_at > now - MAX_SCENARIO_AGE` (`= timedelta(hours=1)`) senaryoyu aday alır. Slot/sembol yüzünden atlanan senaryo bir daha aday olmaz; giriş fiyatı her zaman aynı saatin kapanışıdır.
- **Kalibrasyon şart:** `calibrate_scenarios` kova `MIN_SAMPLES`'a ulaşmamışsa `calibrated_confidence`'ı **NULL bırakır** (ham skoru kopyalamaz); opener `calibrated_confidence IS NOT NULL` ister. Sonuç: ham skorla asla trade yok. `BUCKET_WIDTH = Decimal("0.25")` (4 kova × 2 yön) — kovalar daha hızlı dolar. README/spec'teki "yetersiz veri → ham skor" cümlesi güncellenir.
- **Beklenti kapısı:** eşik isabet oranı değil beklenti. `expected_r = p·rr − (1 − p) − cost_r`; `p = calibrated_confidence`, `rr = |target−entry| / |entry−stop|`, `cost_r = fee_and_slippage_cost_in_r(entry, stop)` (aşağıda). `expected_r > MIN_EXPECTED_R` (`= Decimal("0.1")`) ise açılır. `CONFIDENCE_THRESHOLD` kaldırılır; `paper_trading_config` içinde `MIN_EXPECTED_R` gelir. Red sebebi debug log'a düşer ("expected_r=-0.21 below 0.1").
- **Toplam maruziyet:** açık pozisyonların `Σ position_size × entry_price ≤ MAX_TOTAL_NOTIONAL_MULTIPLE × equity` (`= Decimal("10")`). Aşacaksa atlanır, sebep loglanır.

### 1.C Stop geometrisi ve boyut

- `build_scenario`: `risk / entry_price < MIN_STOP_PCT` (`= Decimal("0.005")`, walk-forward grid'indeki orta değer; kalibre edilecek) ise `None` döner; `process_symbol_scenario` bunu `"stop_too_tight"` sebebiyle loglar (mevcut `risk == 0` yolu ile aynı dal). Replay `passes_risk_filters`'ta aynı sabit kullanılır → iki taraf aynı kuralı ölçer; scriptlerdeki `min_stop_pct=None` seçeneği kalır (kapıyı kaldırıp ölçmek için).
- `size_position`: kaldıraç tavanı kırpınca `risk_amount = position_size × |entry − stop|` döner (gerçek risk). `test_size_position_caps_notional_at_max_leverage` buna göre güncellenir.

### 1.D Maliyet modeli (tek kaynak)

`src/trading_costs.py` (yeni, saf):
- `TAKER_FEE_RATE` (buraya taşınır, `paper_trading_config` yeniden dışa aktarır), `SLIPPAGE_BPS = Decimal("5")`.
- `round_trip_cost(position_size, entry, exit) -> Decimal`: iki bacak taker fee + iki bacak slippage, her biri kendi notional'ı üzerinden.
- `fee_and_slippage_cost_in_r(entry, stop) -> Decimal`: aynı maliyetin R cinsinden yaklaşık değeri (giriş≈çıkış varsayımıyla), opener'ın beklenti kapısı ve replay için.
- `funding_cost(direction, position_size, events: list[(time, rate, mark_price)]) -> Decimal`: long pozitif oranı öder, short alır; her olay `rate × position_size × mark_price`.
Paper closer `_fees` yerine `round_trip_cost` + `funding_cost` kullanır; replay `fee_cost_in_r` bu modülü çağırır. Tek maliyet tanımı, iki tüketici.

### 1.E Funding geçmişi

`FundingRateHistory` tablosu (`symbol`, `funding_time`, `funding_rate`, `mark_price`; `(symbol, funding_time)` unique). Saatlik `refresh_funding_rates` bulk `futures_mark_price` çıktısındaki `nextFundingTime`/`lastFundingRate` ile son olayı yazar (aynı olay ikinci kez yazılmaz). Araştırma tarafı `scripts/fetch_research_data.py` `futures_funding_rate(symbol, startTime, endTime)` ile 90 günü `data/research.db`'ye doldurur. Paper closer pozisyonun açık kaldığı aralıktaki olayları toplar; replay `resolve_draft` aynı hesabı `funding_at(symbol, t)` callback'i ile yapar.

### 1.F Replay evreni ve kapılar

- `fetch_research_data.py`: evren = `futures_exchange_info` PERPETUAL+USDT+TRADING, `quoteVolume`'a göre ilk N, **stablecoin tabanlılar (USDC, FDUSD, TUSD, USDP, DAI, EUR…) ve `UP`/`DOWN` türevleri hariç**; mumlar `futures_klines` (spot değil). `BinanceClient.get_futures_klines` eklenir (spot `get_klines` ile aynı sayfalama).
- `iter_scenarios(..., funding_at=None)`: rejim kapısından sonra `funding_rejection` çağrılır; `regime_blocked` gibi `funding_blocked` olayı sayılır. `funding_at=None` → kapı kapalı (mevcut davranış).
- `resolve_draft`: önce `evaluate_outcome`, yalnızca `expired` dönenlerde veri yetersizliği kontrolü.
- Replay kilidi `resolved_at`'e kadar (production ile aynı), `expires_at`'e değil.

### 1.G Göstergeler

- `compute_rsi` Wilder yumuşatması: ilk `period` için SMA tohumu, sonra `avg = (prev × (period−1) + cur) / period`. Isınma: `MIN_CANDLES = 100` yeterli (Wilder 14'te 100 mumda tohum ağırlığı < %0.2). Testte iki bilinen referans serisi ile sayısal doğrulama. Replay'deki "RSI'ı sembol başına bir kez hesapla" optimizasyonu korunur (fonksiyon zaten tam seri döndürüyor).
- `REGIME_LOOKBACK = 80`. Test: 80 mumla hesaplanan EMA21, 200 mumla hesaplananın %0.5'i içinde.
- Spec dokümanları (`scenario-engine`, `btc-regime-filter`) bu ikisini yansıtacak şekilde güncellenir.

### 1.H Bayrak kalıcılığı

`flag_anomalies(rows, previous_close=None)`; `fetch_and_store` batch'ten önceki son kapanışı DB'den (`storage.get_last_close_before(session, symbol, timeframe, open_time)`) alıp geçer. Böylece batch'in ilk mumu da spike kontrolünden geçer; `upsert_klines` değişmez. Aynı yol gap onarımını da kapsar. Test: iki ardışık çalıştırma boyunca bayrak korunur (denetimdeki reprodüksiyon senaryosu).

### 1.I Takılı senaryo ve pozisyon

- `resolve_pending_scenarios`: `now > expires_at + UNRESOLVABLE_GRACE` (`= timedelta(hours=24)`) ve hâlâ eksik mum varsa `status = "unresolvable"` (yeni değer). Kalibrasyon havuzu bu statüyü **dışlar** (`status NOT IN ("pending", "unresolvable")`).
- Closer: senaryosu `unresolvable` olan ya da senaryosunun `expires_at + UNRESOLVABLE_GRACE`'i geçmiş açık pozisyonu son bilinen kapanışla kapatır, `exit_reason = "forced"`. `PaperPosition.exit_reason` nullable `String` (`target` / `stop` / `expired` / `forced`); dashboard gösterir.

### 1.J İstatistik

- `run_walkforward.score`: train `expires_at <= boundary`; test `now >= boundary`.
- GA: normal yaklaşım yerine **saat-bloklu bootstrap** — işlemler giriş saatine göre gruplanır, gruplar 1000 kez yeniden örneklenir, %2.5/%97.5 dilimleri. `scripts/run_walkforward.py` ve `run_backtest.py` için ilk testler yazılır (`score`, `summarise`, bootstrap).

### 1.K Edge kapısı

Faz 1'in sonunda `fetch_research_data` (futures evreni, funding) → `run_walkforward`. Karar kuralı: en az bir kural/filtre hücresi test döneminde `expected_r` bootstrap GA'sının **alt sınırı > 0** vermeli. Vermezse Faz 3 (canlı) açılmaz; sonraki iş strateji araştırmasıdır, bu spec'in kapsamı dışında.

### Kabul edilen sınır

Paper giriş fiyatı saatin kapanışıdır; senaryo :05-:25 arası yazılır. 1h mumla bu farkı ölçmek mümkün değil; `SLIPPAGE_BPS` bunu kabaca karşılar. Canlı katman gerçek fill fiyatını kaydedecek ve paper ile farkı raporlayacak — Faz 3 spec'inin ilk maddesi.

---

## Faz 2 — Prod sağlamlık

**2.1 Deploy tanımı.** `Procfile` (`web: python -m src.main`), `railway.json` (`healthcheckPath: /health`, `healthcheckTimeout: 300`, restart policy), `runtime.txt` (`python-3.11`), `requirements.txt` tam pin (`==`), `requirements-dev.txt` (`pytest`). Claude yapar (bağımlılık dosyaları worker'a kapalı).

**2.2 `/health`.** Kimliksiz; `get_system_health` son fetch yaşına göre `200 {"status":"ok","last_fetch_age_min":N}` ya da `503`. Boot backfill sırasında `200 {"status":"starting"}` döner ki Railway healthcheck geçsin.

**2.3 Boot sırası.** `main`: Flask önce açılır; `startup()` (sembol yenileme + backfill) bir thread'de koşar; bitince `scheduler.start()`. Son başarılı fetch 1 saatten eskiyse saatlik job `next_run_time=utc_now()` ile hemen tetiklenir (kaçan run'ı telafi).

**2.4 Tek thread executor.** `BackgroundScheduler(executors={"default": ThreadPoolExecutor(1)})`; job'lar üst üste binmez, kuyrukta bekler. `max_instances` uyarısı ve BTCUSDT 1d yarışı ortadan kalkar. Test: executor tipi ve `max_workers == 1`.

**2.5 Gap tavanı.** `repair_recent_gaps` sembol/timeframe başına en yeni `MAX_GAPS_PER_RUN = 5` gap'i dener; kalanı bir sonraki saate bırakır (log'da "N gap ertelendi").

**2.6 `fetch_log`.** Modelde `Index("ix_fetch_log_finished_at", finished_at)`; `db/session.sync_missing_indexes(engine)` model indekslerini `checkfirst=True` ile oluşturur (kolonlarla aynı desen, yalnızca ekler). Günlük job sonunda `FETCH_LOG_RETENTION_DAYS = 30`'dan eski satırlar silinir.

**2.7 Web.** `waitress.serve(app, threads=4)` (yeni bağımlılık, Claude ekler); `_authorized` kullanıcı adını `hmac.compare_digest` ile karşılaştırır ve başarısız denemeyi `logger.warning` (uzak IP) ile yazar. werkzeug istek logları `WARNING`'e çekilir.

**2.8 CI.** `.github/workflows/test.yml`: push/PR'da `python3 -m pytest -q`.

**2.9 Doküman.** `DURUM.md` `.gitignore`'a (üreteç başka projeyi karıştırıyor) ya da yalnızca bu repo için yeniden üretilir; README "2 yıl" → 90 gün, başlık; CLAUDE.md/AGENTS.md test sayısı ve `strategy_version` kuralı; veri-altyapı spec'inde "fetch_log'dan devam" düzeltilir.

---

## Kapsam dışı (bilinçli)

`klines` tekil indeksleri, `Symbol.listed_at`, `funding_collector` N SELECT, engine pool boyutu, `CONFLUENCE_WINDOW`/katı swing karşılaştırması, lint/type araçları. Hiçbiri ölçümü ya da çalışırlığı etkilemiyor; edge kanıtlanınca konuşulur.

## Test stratejisi

Her görev kırmızı testle başlar (worker göremeden Claude yazar). Faz 1 sonunda bütünleşik bir "paper cycle" testi: sahte mumlarla tek sembolde senaryo → kalibrasyon (NULL) → pozisyon açılmaz → 20 çözümlenmiş senaryo eklenir → yeni senaryo kalibre olur → beklenti kapısı → pozisyon açılır → funding olayı → kapanış → `realized_pnl` fee+slippage+funding'i düşmüş. Bu test spec'in kendisidir.
