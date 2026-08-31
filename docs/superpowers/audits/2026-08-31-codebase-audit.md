# Kod Tabanı Denetimi — 31 Ağustos 2026

**Yöntem:** 4 paralel inceleme ajanı (A veri, B sinyal+araştırma, C/D öğrenme+paper, altyapı/ops/güvenlik/test), ardından her kritik ve önemli bulgu kaynak kod üzerinde elle teyit edildi. Salt okunur; hiçbir dosya değiştirilmedi. Başlangıç durumu: `main` @ `fd1920d`, **302/302 test yeşil**, 6 commit push bekliyor.

**Özet hüküm:** Veri hattı ve izolasyon politikası sağlam, kapalı-mum disiplini gerçekten uygulanmış, replay production'ın saf fonksiyonlarını çağırıyor. Sorunlar iki kümede toplanıyor: (1) **paper portföy ve kalibrasyon sonuçları şu an güvenilir bir ölçüm değil** — bayat giriş fiyatı, fee-dominant pozisyonlar, sızıntılı kalibrasyon hedefi ve iki farklı büyüklüğe uygulanan tek eşik; (2) **servis üretimde kırılgan** — boot'ta korumasız ping, SIGTERM'in yok sayılması, saati aşan job'ın sessizce atlanması, deploy tanımının repoda olmaması.

---

## KRİTİK

### K1. Paper pozisyon bayat `entry_price` ile açılıyor → hayalet PnL
`src/paper_position_opener.py:27-37`, `src/learning_runner.py` (`since = floor_to_timeframe(scenario.created_at)`)

Aday sorgusunda yaş sınırı yok: `pending`, süresi dolmamış, `calibrated_confidence >= 0.65` ve pozisyonu olmayan her senaryo aday. Aynı sembolde açık pozisyon veya 10 tavanı yüzünden atlanan senaryo, saatler sonra slot boşalınca senaryonun **ilk kapanış fiyatı** ile açılıyor; evaluator ise mumları senaryonun `created_at`'inden okuyor, pozisyonun açıldığı andan değil. Doğrulanmış: 3 mum boyunca 100→109.4 giden fiyat, pozisyon 13:05'te entry=100 ile açılıp 110'a dokununca +99.87 yazıyor; gerçek fill ~109.4 olduğundan gerçek brüt ≈ +6. `has_pending_scenario` kilidi `(symbol, direction)` bazlı olduğundan aynı sembolde long+short pending birlikte var olabilir; ikincisi tam bu yola girer. Test yok — `test_paper_position_opener.py` yalnızca "skipped" aşamasına kadar sınıyor.

### K2. `flagged` (fiyat sıçraması) bayrağı mum kapandığı saatte siliniyor → spike filtresi fiilen ölü
`src/integrity.py:60-73`, `src/storage.py:110`, `src/scheduler.py:50-52`

`flag_anomalies` sıçramayı yalnızca aynı batch'teki önceki muma göre hesaplar (`previous_close = None` ile başlar). Saatlik job `MAX(open_time)`'dan devam ettiği için batch'in ilk satırı "az önce kapanan" mum; öncesi batch'te olmadığından spike kontrolü yapılmaz ve `upsert_klines` mevcut bayrağı koşulsuz ezer (`existing_row.flagged = row.get("flagged", False)`). Reprodüksiyon: 11:00 close=200 (%100 sıçrama) 11:05'te `flagged=True`, 12:05'te `False`. B katmanı 12:05'te 11:00 mumunu temiz sanıp sinyal üretebilir; aynı mekanizma 1d'de BTC rejim filtresini de etkiler. Gap onarımında da `gap.start` mumu hiç spike kontrolünden geçmiyor. Yalnızca sıfır-hacim bayrağı hayatta. Test yok.

### K3. `BinanceClient()` kurucusu boot'ta korumasız `ping` atıyor → crash-loop
`src/main.py:62`, `src/binance_client.py:19-22`

python-binance `Client.__init__` varsayılan `ping=True`. `refresh_symbols` özenle try/except içinde ("transient boot failure must not kill an unattended service") ama bir satır üstündeki `BinanceClient()` dışarıda. Binance bakımı / egress IP ban / DNS hatası → `startup()` patlar → Railway restart döngüsü; DB'deki veriyle dashboard bile açılmaz. `test_main.py` `BinanceClient`'ı mock'ladığı için görünmez. Çözüm tek satır: `Client(..., ping=False)`.

### K4. SIGTERM yakalanmıyor → Railway redeploy job'ı ortasından kesiyor
`src/main.py:103-108`

`except (KeyboardInterrupt, SystemExit)` yalnızca Ctrl+C. SIGTERM'in Python varsayılanı anında sonlandırma; `finally: scheduler.shutdown()` hiç çalışmıyor (doğrulandı: exit=143, finally basılmadı). `git push` :05-:25 arası 20 dakikalık job'a denk gelirse: kline tarafı sembol bazlı commit'lendiği için güvenli; ama `learning_runner` (senaryo `resolved`) ile `paper_position_closer` ayrı commit'ler → senaryo çözülmüş, pozisyon açık kalabilir; o saatin senaryo üretimi kaybolur.

### K5. Deploy tanımı repoda yok
Repo kökünde `Procfile` / `railway.json` / `Dockerfile` / `runtime.txt` / lockfile yok; `requirements.txt` yalnızca aralık pinliyor ve `pytest` prod bağımlılıklarında. Start komutu ve Python sürümü Railway UI'da elle girilmiş olmalı — yeni ortam/servis repodan kurulamaz, bir transitif bağımlılığın minor'ı gece rebuild'de davranışı değiştirebilir.

---

## ÖNEMLİ — ölçüm güvenilirliği (paper + kalibrasyon + araştırma)

### O1. `run_scenario_generation` job'ın `end` anını almıyor
`src/scheduler.py:195` vs `:206, :213`. C ve D `now=end` alırken B `utc_now()` kullanıyor. Fetch (484 sembol, ~16 dk; 429 backoff ile daha uzun) saat sınırını aşarsa `floor(now)` ileri kayar: 5 dakikalık stub "kapanmış mum" olarak okunur ya da tüm semboller "stale" diye sessizce atlanır; `created_at` learning'in `now`'ından ~20 dk sonrada kalır, `opened_at` senaryodan önce görünür. Dört ajan da bağımsız buldu. Dikkat: `test_scheduler.py`'deki sahte fonksiyonlar `(session, symbols)` imzasında — `now=end` geçilince güncellenmeli.

### O2. Kaldıraç tavanı fee-dominant pozisyonu reddetmek yerine açıyor; `risk_amount` gerçek riski yansıtmıyor; production'da `min_stop_pct` yok
`src/paper_sizer.py:23-27`, `src/paper_trading_config.py:58`, `src/scenario_builder.py` (stop tampon yok), `src/research/funnel.py:253-262`

entry=100, stop=99.99, equity=10000 → notional 30 000, stop'ta gerçek kayıp **3**, gidiş-dönüş fee **30**, kayıtlı `risk_amount` **100** (33× yanlış). `test_size_position_caps_notional_at_max_leverage` bu yanlış kaydı doğru davranış olarak kilitliyor. Replay'de `min_stop_pct` filtresi var, production `build_scenario`'da yok — CLAUDE.md'nin "iki taraf farklı stratejiyi ölçer" uyarısı tam burada. Ek olarak `confidence_score = 0.5·rr_score + 0.5·strength` formülü dejenere stop'ları (risk→0 ⇒ rr_score=1) en üst kovaya koyuyor.

### O3. Kalibrasyon hedef sorgusu çözümlenmiş satırları da kapsıyor → sızıntı yolu
`src/learning_runner.py:173`: `targets = ... filter(Scenario.calibrated_confidence.is_(None))` — `status == "pending"` şartı yok. Kalibrasyon bir run'da patlar, senaryo aynı run'da çözümlenirse, sonraki run'da kendi sonucu havuzdayken puanlanır. Kolonun ilk eklendiği deploy'daki tüm tarihsel satırlar için de geçerli. Tek satırlık filtre.

### O4. `CONFIDENCE_THRESHOLD = 0.65` iki farklı büyüklüğe uygulanıyor; RR yok sayılıyor; kovalar dolmayabilir
`src/confidence_calibrator.py`, `src/paper_trading_config.py:13`

20 örnekten önce `calibrated_confidence = confidence_score` (RR+RSI derinliğinden türeyen sıralama skoru), 20'den sonra `hit_target / total` (expired kayıp sayılır). RR≈3 hedeflerle %65 isabet pratikte ulaşılmaz → kova 20'ye ulaştığı an o kovada trade kesilir, diğerleri ham skorla devam eder; equity eğrisi iki rejimin karışımı olur. RR=3 / %40 isabetli (kârlı) kova asla geçemez, RR=0.3 / %70 isabetli (zararlı) kova geçer. PLAUSIBLE: ~77 senaryo/90 gün, 2 yön × 10 kova → en dolu kova aylarca 20'ye ulaşmaz. Havuz tüm zamanları kapsıyor (kural değiştiğinde eski kuralın sonuçları yeni kuralı kalibre eder); spec "yeni sonuçlananlara da uygulanır" derken README "bir kez atanır" diyor.

### O5. Funding gate production'da var, replay'de yok
`src/scenario_runner.py:119-122` vs `src/research/funnel.py` (`funding` geçmiyor). Replay squeeze dönemlerinde production'ın reddettiği işlemleri skorluyor. Binance `futures_funding_rate` endpoint'i tam geçmiş veriyor — düzeltilebilir.

### O6. Göstergeler spec'teki gibi değil
- `src/indicators.py:18-27`: RSI Cutler (SMA), spec/README "standart RSI" (Wilder). Sentetik seride 30-kesme sayısı 2.6× fazla, olayların %75-90'ı farklı. Bilinçli (replay hızı için, testte itiraf ediliyor) ama spec güncellenmemiş; "~4 sinyal/90 gün" bulgusu Wilder'a ait değil.
- `src/btc_regime.py:16`: `REGIME_LOOKBACK = 22` → EMA21 tek adım atıyor (0.09·close + 0.91·SMA21). Doğrulandı: 22 mumda 111, 80 mumda 169. Rejim dönüşleri geç görülüyor; %9.8 günde işaret farklı. Düzeltme: lookback ~60-80. Test yok.

### O7. Dolum/maliyet modeli iyimser ve tutarsız
- `entry_price = closes[-1]` ama senaryo ~:21'de yazılıyor; 0-21 dk'lık hareket "pozisyondaymışız gibi" sayılıyor. Giriş hiç "gerçekleşti mi" kontrol edilmiyor; `high >= target` = dolum. Slippage yok; gap-through-stop'ta çıkış tam stop'tan.
- Araştırma evreni **spot** çiftler (short yok), komisyon **futures** taker (%0.05); futures kabul edilirse 7 güne kadar tutulan pozisyonun 8 saatlik funding ödemeleri (~komisyonla aynı mertebe) modellenmiyor — funding gate var, funding maliyeti yok.
- Evrende stablecoin/kaldıraçlı token olabilir (`quoteVolume` top-N) — PLAUSIBLE, `data/research.db` ile teyit gerekir.

### O8. Risk sınırları eksik; takılı pozisyon kalıcı deadlock
`src/paper_position_opener.py:63-70`, `src/paper_position_closer.py` (`_count_stuck_positions` yalnızca warning)

Tek sınır pozisyon başına 3× notional × 10 = 30× equity; toplam maruziyet/marjin tavanı, günlük zarar kesicisi, maksimum tutma süresi yok. Kalıcı gap'te senaryo sonsuza dek `pending` kalıyor, bağlı pozisyon açık kalıp sembolü ve slotu kalıcı kilitliyor (delist'te deadlock).

### O9. İstatistik: GA bağımsızlık varsayıyor, walk-forward sınırından sızıntı
`scripts/run_walkforward.py:182-191, 152-179`. Aynı saatte 25-150 altcoin'de açılan işlemler bağımsız değil (BTC beta) → etkin n küçük, GA dar. Train seçimi `now < boundary` ama sonuçlar boundary sonrası mumlarla çözülüyor; `expires_at <= boundary` şartı yeterli. `resolve_draft` bilinen stop sonuçlarını kuyrukta atıyor (`funnel.py:117`). Replay kilidi expiry'ye kadar, production kilidi çözümlemeye kadar.

---

## ÖNEMLİ — operasyon

### O10. Saati aşan job sessizce atlanıyor; restart sonrası kaçan run oynatılmıyor; gap'ler sonsuz yeniden isteniyor
`src/scheduler.py:27-34, 259-279`, `src/backfill.py:40-50`. `max_instances=1` + memory jobstore: overrun'da APScheduler "skipped: maximum instances" basar, `misfire_grace_time` kurtarmaz; restart'ta `next_run_time` bir sonraki :05. Redeploy 12:04'te → ilk fetch 13:05, dashboard 90 dk eşiğiyle bunu göstermez. Doldurulamayan gap'ler her saat ayrı HTTP isteğiyle yeniden isteniyor, "bu gap dolmuyor" hafızası yok → job süresi sınırsız büyüyebilir (PLAUSIBLE, prod log teyit eder).

### O11. Ağ hatalarında retry yok; 418'de `Retry-After` sınırsız uyunuyor
`src/rate_limit.py`. `ReadTimeout`/`ConnectionError`/5xx → ilk denemede fırlatılır (spec sınırlı retry diyor). 418 IP ban'ı sembol bazında 5 kez denenir ve `Retry-After` (saatler) cap'siz `sleep`'e verilir → pipeline saatlerce donar, sonraki job'lar atlanır.

### O12. Eşzamanlılık yarışları
`upsert_klines` ve `has_pending_scenario→insert_scenario` select-sonra-insert, `ON CONFLICT` yok. 00:05 hourly ve 00:10 daily `BTCUSDT 1d` stub'ını aynı anda upsert edebilir (ayrı thread'ler) → unique ihlali, sahte "failed". Railway rolling deploy'da iki scheduler aynı :05'i çalıştırırsa aynı sembol için iki `pending` senaryo (unique kısıt yok).

### O13. `fetch_log` sınırsız büyüyor, dashboard sorgusu indekssiz
≈12k satır/gün, prune yok; `get_system_health` her istekte `ORDER BY finished_at DESC` tam tarama. `sync_missing_columns` indeks eklemez — elle.

### O14. Flask dev server üretimde; auth rate-limit/log yok; `compare_digest` yok
`src/main.py:104`, `src/web.py:22-30`. pbkdf2 1M iterasyon = 0.29 s/deneme CPU, GIL'den dolayı job'dan çalınır; 50 istek/sn ile saatlik fetch uzar. `username ==` kısa devre → kullanıcı adı zamanlama kanalıyla doğrulanabilir.

### O15. Health endpoint yok, boot backfill portu bloke ediyor, alerting yok
Tek rota `/` auth zorunlu → Railway healthcheck tanımlanamaz. `startup()` senkron backfill (ilk deploy'da ~1900 istek, 15-30 dk) bitmeden port açılmaz. "4 saattir fetch yok" bildirimi hiçbir yerde yok.

### O16. Test altyapısı boşlukları
sqlite `Numeric(5,4)` overflow'u ve FK ihlalini kabul ediyor (PG reddeder) → `confidence_score > 1` yalnızca prod'da patlar. `CronTrigger(minute=5)` hiçbir testte assert edilmiyor. SIGTERM, ping, overrun, `_authorized` zamanlama testsiz. CI/lint/type yok. `sync_missing_columns` NOT NULL dalı testsiz; modelde `nullable=False`+default deseni var, kopyalanırsa prod patlar.

---

## İYİLEŞTİRME

- **Doküman sürüklenmesi:** `DURUM.md` "33 failed" + başka projenin (`onmuhasebe`) durumu; README "2 yıl" vs `DEFAULT_BACKFILL_DAYS=90`; spec "fetch_log'dan devam"; "~270 test"; README başlığı; `QWEN.md` "İngilizce" vs Türkçe ADIMLAR docstring'leri kodda kalmış; 7 planda 261 `[ ]`, 0 `[x]`.
- **Dashboard:** `get_equity_summary` `equity_after IS NULL` satırında `TypeError` → tüm dashboard hata sayfası (`current_equity` bu satırı tolere ediyor, tutarsız).
- **Spec sapmaları:** `CONFLUENCE_WINDOW=3` (spec "son mumda"), swing tespitinde katı `>`/`<` (eşit tepeler swing değil), S/R penceresi 90 vs 101, README sembol-düzeyi vs kod yön-düzeyi kilit.
- **Küçük şeyler:** `klines` üzerinde gereksiz tekil indeksler; `Symbol.listed_at` hiç dolmuyor; `funding_collector` sembol başına SELECT; `logs/app.log` Railway'de ölü; werkzeug istek logları root'a akıyor; `positioned_scenario_ids` tüm tabloyu belleğe çekiyor; engine pool 5+10 vs thread-per-request.
- **Test boşlukları:** K1 yolu, K2 bayrak kalıcılığı, `confidence_score` formülü, `risk==0`, short uçtan uca (mock'suz), `run_walkforward`/`run_backtest` için hiç test yok, closer short+expired+fee.

---

## İyi yapılanlar (korunmalı)

Resume tasarımı (`MAX(open_time)` + son mumu yeniden çekme) idempotent ve kendi kendine iyileşiyor. İzolasyon politikası her katmanda gerçekten uygulanmış ve gerçek NOT NULL ihlaliyle test ediliyor. Zaman disiplini tutarlı (naive UTC, `Decimal`). Kapalı-mum disiplini forming mumu bilinçli sızdıran testlerle korunuyor. Replay production'ın saf fonksiyonlarını çağırıyor ve kısa devre eşdeğerliği mum mum test ediliyor. Kalibrasyon→çözümleme sırası gerekçeli ve testli. Equity zinciri id/closed_at tie-break'iyle sağlam. Güvenlik temelleri (pbkdf2, autoescape, debug=False, sırlar env'den) doğru. Red sebepleri string olarak dönüyor, sessiz engelleme yok.
