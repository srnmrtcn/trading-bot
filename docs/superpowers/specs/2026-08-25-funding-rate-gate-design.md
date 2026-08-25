# Funding Rate Risk Gate — Tasarım

**Tarih:** 2026-08-25
**Durum:** Onaylandı, uygulama planı bekleniyor

## Bağlam ve Kapsam

"Kripto Analiz Ofisi" projesinin orijinal dört alt sistemi (A: veri altyapısı, B: senaryo motoru, C: öğrenme döngüsü, D: paper test portföyü) ve ilk uzantısı (BTC rejim filtresi, `2026-08-25-btc-regime-filter-design.md`) tamamlandı. Bu spec, uzman kripto analisti değerlendirmesinde çıkan 5 maddelik listenin **2. maddesini** kapsıyor: türev piyasası verisi.

Motivasyon: sistem şu ana kadar yalnızca **spot OHLCV + teknik analiz** kullanıyor. Kripto piyasasında fiyat hareketini süren asıl dinamiklerden biri vadeli piyasadaki pozisyonlanma. Funding rate, perpetual futures'ta long ve short tarafın birbirine ödediği periyodik ücret: aşırı pozitif funding "long'lar aşırı kalabalık ve kaldıraçlı" (long squeeze riski), aşırı negatif funding ise bunun tersi anlamına gelir. Kalabalık tarafa katılmak, teknik sinyal ne kadar güzel görünürse görünsün, sistematik olarak kötü bir risk/ödül dengesi taşır.

Kapsam dışı (bu tur için): **Open Interest** — Binance'in OI geçmiş verisi API'de yalnızca son 30 günle sınırlı (funding rate'in tam geçmişi var, OI'nin yok), dolayısıyla anlamlı bir eşik/kalibrasyon kurmak şimdilik mümkün değil; canlı toplamayla yeterli veri biriktikten sonra ayrı bir alt sistem olarak eklenebilir. Ayrıca kapsam dışı: likidasyon kümeleri, funding'in confidence skoruna girdi olması, funding'in bağımsız bir kontrarian senaryo tetikleyicisi olması, coin-margined (COIN-M) kontratlar.

## Kararlar (brainstorming'de netleşen)

- **Sembol evreni:** Mevcut spot USDT evrenindeki, eşleşen bir USDT-M **PERPETUAL** futures kontratı olan semboller. Futures kontratı olmayan semboller bu gate'ten hiç etkilenmez.
- **Kapsam:** Yalnızca funding rate (OI yok — yukarıdaki gerekçe).
- **Kullanım şekli:** Risk gate'i — **sert engelleme**, BTC rejim filtresiyle aynı desen. Confidence skoru hiç değişmez.
- **Eşik:** ±%0.05 (`Decimal("0.0005")`). Binance'te normal funding genelde ±%0.01-%0.05 aralığında seyreder; bu eşik "normalin üstü" demektir.
- **Okuma yöntemi:** En son funding print'i (ortalama/pencere yok) — mevcut sinyal mantığının "en son kapanan mum" felsefesiyle tutarlı.
- **Backfill:** Yok, yalnızca ileriye dönük toplama. Eşik sabit ve geçmiş veriye dayanan bir kalibrasyon yapılmadığı için geçmiş funding verisinin bu turda bir kullanımı yok.
- **Futures eşleşmesi:** Günlük sembol yenileme job'ına entegre — `Symbol` tablosuna yeni bir `has_futures_contract` kolonu.

## Doğrulanmış API Gerçekleri

Tasarım sırasında canlı olarak doğrulandı (python-binance, public endpoint, API key gerekmiyor):

- `futures_exchange_info()` → her kontrat için `symbol`, `status`, `quoteAsset`, `contractType`. `contractType` değerleri: `PERPETUAL`, `CURRENT_QUARTER`, `NEXT_QUARTER`, `TRADIFI_PERPETUAL`. Bizim istediğimiz: `status == "TRADING" and quoteAsset == "USDT" and contractType == "PERPETUAL"`.
- `futures_mark_price()` **parametresiz** çağrıldığında **tüm** sembollerin güncel değerlerini **tek bir istekte** liste olarak döndürüyor (~875 kayıt). Her kayıtta `symbol`, `lastFundingRate`, `nextFundingTime`, `time` var. Sembol başına ayrı çağrı **gerekmiyor** — bu, saatlik toplama adımının maliyetini tek bir HTTP isteğine indiriyor.

## Şema

### `Symbol` tablosuna yeni kolon

```
has_futures_contract = Column(Boolean, nullable=True, default=False)
```

**`nullable=True` zorunludur ve bu bir tercih değil, kısıttır.** [`src/db/session.py`](../../src/db/session.py)'deki `sync_missing_columns`, canlı veritabanına yalnızca **nullable** kolonları otomatik ekliyor; `nullable=False` bir kolonu "gerçek bir migration gerekir" diyerek loglayıp atlıyor. Atlanırsa, ORM'in SELECT'i her mapped kolonu saydığı için `symbols` tablosuna yapılan **her** sorgu patlar — yani Railway'de çalışan servis komple düşer. Nullable olduğu için mevcut satırlar `NULL` ile başlar; kod `NULL`'ı "futures kontratı yok/bilinmiyor" olarak okur (aşağıda), ve ilk `refresh_symbols` çağrısı (servis açılışında ve günlük job'da) gerçek değeri yazar.

### Yeni tablo: `funding_rates`

Sembol başına **tek satır** — geçmiş tutulmuyor, yalnızca "en son bilinen değer" (backfill kararının doğal sonucu):

```
symbol        = Column(String, primary_key=True)
funding_rate  = Column(Numeric(10, 8), nullable=False)
fetched_at    = Column(DateTime, nullable=False)
```

Yeni bir tablo olduğu için `Base.metadata.create_all` tarafından eksiksiz oluşturulur; migration sorunu yok.

## Mimari / Veri Akışı

```
Günlük sembol yenileme job'ı (mevcut: run_symbol_refresh_job → refresh_symbols)
        │
        ├── get_active_usdt_symbols()        (mevcut, spot)
        └── get_futures_usdt_symbols()       (YENİ, perpetual futures seti)
        │
        ▼
   Symbol.has_futures_contract güncellenir


Saatlik "1h" job (mevcut: run_timeframe_job)
        │
   kline fetch + gap repair            (mevcut, değişmiyor)
        │
        ▼
   refresh_funding_rates()             (YENİ — tek API isteği, funding_rates upsert)
        │
        ▼
   run_scenario_generation()           (mevcut)
        │
        └── process_symbol_scenario()
                 │
            sinyal var mı?  →  BTC rejim gate'i  →  YENİ: funding gate'i  →  senaryo
        │
        ▼
   run_learning_cycle() → run_paper_trading_cycle()   (mevcut, değişmiyor)
```

BTC rejim filtresinden farkı: rejim zaten var olan `klines` verisini okuduğu için `scheduler.py`'ye hiç dokunulmamıştı. Funding rate **yeni bir veri toplama adımı** gerektiriyor, dolayısıyla bu kez `scheduler.py` de değişiyor.

**Gate'in yeri neden önemli:** funding kontrolü, `evaluate_signal` bir sinyal döndürdükten *sonra* çalışıyor. Sinyal üretimi nadir olduğu için, yüzlerce sembollü bir taramada funding sorgusu yalnızca birkaç sembol için yapılır — tarama başına ek DB maliyeti pratikte sıfır.

## Bileşenler

### `src/binance_client.py` (mevcut dosya, iki yeni metod)

- `get_futures_usdt_symbols() -> set` — `futures_exchange_info()` çağırır, `status == "TRADING" and quoteAsset == "USDT" and contractType == "PERPETUAL"` filtresini uygular, sembol adlarından bir `set` döndürür. Mevcut `_backoff.call` deseniyle sarılır.
- `get_funding_rates() -> dict` — `futures_mark_price()` (parametresiz) çağırır, `{symbol: Decimal(lastFundingRate)}` sözlüğü döndürür. Mevcut `get_klines`'taki gibi `Decimal(str(...))` ile dönüştürülür (float hassasiyet kaybı olmaması için).

### `src/symbol_registry.py` (mevcut dosya, değişiklik)

`refresh_symbols`, spot sembolleri güncellerken `binance_client.get_futures_usdt_symbols()` sonucunu da alır ve her `Symbol` satırının `has_futures_contract` alanını buna göre yazar (sembol futures setinde varsa `True`, yoksa `False`).

### `src/funding_collector.py` (YENİ)

`refresh_funding_rates(session, binance_client, now=None) -> FundingRefreshResult`

Tek bir `get_funding_rates()` çağrısı yapar; `has_futures_contract == True` olan semboller için `funding_rates` tablosuna upsert eder (`fetched_at = now`). Binance'in döndürmediği bir sembol varsa o sembolün mevcut satırına dokunmaz (bir sonraki saatte tazelenir; bayatlık kontrolü zaten devrede). `logger.info` ile sonucu özetler ve şunu döndürür:

- `FundingRefreshResult.updated` — yazılan/güncellenen satır sayısı.
- `FundingRefreshResult.missing` — `has_futures_contract == True` olduğu halde Binance yanıtında bulunmayan sembol sayısı (kontrat yeni delist edilmiş olabilir; `has_futures_contract` bir sonraki günlük sembol yenilemesinde düzelir).

### `src/funding_gate.py` (YENİ)

```
FUNDING_RATE_THRESHOLD = Decimal("0.0005")   # ±%0.05
FUNDING_DATA_MAX_AGE   = timedelta(hours=2)
```

`funding_rejection(session, symbol, direction, now) -> str | None`

Mevcut `scenario_runner._window_rejection` ile **aynı sözleşme**: engelleme sebebini açıklayan bir string, ya da engellenmiyorsa `None`. (Brainstorming'de `-> bool` olarak konuşulmuştu; bu, BTC rejim filtresinin final review'ünde çıkan "sessiz engelleme gözlemlenemiyor" bulgusunun aynısını baştan önlemek için bilinçli bir iyileştirme ve kod tabanının mevcut desenine birebir uyuyor.)

Sırayla:
1. Sembolün `has_futures_contract` değeri `True` değilse (`False` **veya** `NULL`) → `None` (gate uygulanmaz; futures piyasası yoksa kontrol edilecek funding de yoktur, mevcut davranış korunur).
2. `funding_rates` satırı yoksa → engelle: "futures kontratı olan sembol için funding verisi yok".
3. `now - fetched_at > FUNDING_DATA_MAX_AGE` ise → engelle: "bayat funding verisi".
4. `direction == "long"` ve `funding_rate > FUNDING_RATE_THRESHOLD` → engelle: "long'lar kalabalık".
5. `direction == "short"` ve `funding_rate < -FUNDING_RATE_THRESHOLD` → engelle: "short'lar kalabalık".
6. Aksi halde → `None`.

Karşılaştırmalar **kesin** (`>` / `<`): tam eşik değerinde engellenmez.

### `src/scenario_runner.py` (mevcut dosya, değişiklik)

`process_symbol_scenario` içinde, BTC rejim gate'inden hemen sonra ve `has_pending_scenario`'dan önce:

```
rejection = funding_rejection(session, symbol, signal.direction, now)
if rejection is not None:
    logger.debug("Skipping %s: %s", symbol, rejection)
    return "skipped"
```

### `src/scheduler.py` (mevcut dosya, değişiklik)

`run_timeframe_job`'ın `timeframe == "1h"` bloğunda, `run_scenario_generation`'dan **önce**, mevcut adımlarla aynı `try/except` izolasyon deseninde `refresh_funding_rates(session, binance_client, now=end)` çağrısı eklenir. İş sonundaki özet log satırına funding sonucu da eklenir.

## Hata Yönetimi

Proje genelindeki "hiçbir alt adımın hatası diğerlerini durdurmaz" ilkesi (bkz. `feedback-plan-mandated-isolation-policy`) aynen uygulanır:

- **`refresh_funding_rates` patlarsa** (Binance API hatası, rate limit, timeout): `scheduler.py`'deki kendi `try/except`'i yakalar ve `logger.exception` ile loglar. Mevcut `funding_rates` satırları dokunulmadan kalır, bir sonraki saatte tekrar denenir. Kline fetch, senaryo üretimi, öğrenme döngüsü ve paper trading etkilenmez.
- **Toplama sürekli başarısızsa:** satırlar bayatlar ve `FUNDING_DATA_MAX_AGE` (2 saat) dolduğunda gate, futures kontratı olan semboller için sinyalleri **engellemeye** başlar — "veri güvenilir değilse işlem yapma" ilkesi (BTC rejim filtresiyle aynı yön). 2 saatlik tolerans, tek bir başarısız saatlik çalıştırmayı sorunsuz atlatmayı sağlar.
- **Gözlemlenebilirlik (BTC rejim filtresinin final review'ünden öğrenilen ders):** sessiz engelleme yasak. `refresh_funding_rates` her çalıştırmada kaç sembolün güncellendiğini `logger.info` ile yazar; bulk çağrı hiç veri döndürmezse `logger.warning`. Gate'in her engellemesi sembol bazlı `logger.debug` (mevcut `_window_rejection` ile aynı seviye — sistem geneli bir arıza değil, sembole özel normal bir sonuç).
- **Futures kontratı olmayan semboller** hiçbir koşulda bu gate yüzünden engellenmez; onlar için sistemin davranışı bit düzeyinde değişmez.

## Bilinen Sınırlama

Binance'te funding aralığı sembol başına değişebiliyor (çoğunlukla 8 saat, bazı sembollerde 4 veya 1 saat). Sabit ±%0.05 eşiği 8 saatlik aralığa göre anlamlıdır; daha kısa aralıklı bir sembolde aynı oran daha yüksek yıllıklandırılmış funding demektir, yani gate o sembollerde olması gerekenden biraz geç devreye girer. Sembol başına `fundingIntervalHours` okuyup eşiği normalize etmek mümkün (`futures_v1_get_funding_info`) ama bu tur için kapsam dışı — sabit eşik kullanıcı kararıdır ve etkilenen sembol sayısı azdır.

## Test Yaklaşımı

Tüm testler mevcut desene uyar: `sqlite:///:memory:` üzerinde `db_session` fixture'ı, gerçek Binance erişimi yok (client mock'lanır).

- **`binance_client.py`**: `get_futures_usdt_symbols()` yalnızca TRADING + USDT + PERPETUAL kontratları döndürüyor (CURRENT_QUARTER/TRADIFI_PERPETUAL/delisted olanları eliyor); `get_funding_rates()` bulk yanıtı `{symbol: Decimal}`'a doğru çeviriyor ve float'a düşmüyor. Mevcut `get_active_usdt_symbols` testlerinin mock deseniyle aynı.
- **`symbol_registry.py`**: `has_futures_contract`, futures setindeki sembollerde `True`, olmayanlarda `False` oluyor; mevcut spot aktif/pasif mantığı bozulmuyor (regresyon).
- **`funding_collector.py`**: yalnızca `has_futures_contract=True` semboller için satır yazıyor; ikinci çağrıda upsert ediyor (yeni satır eklemiyor, `fetched_at` güncelleniyor); Binance'in döndürmediği sembolün mevcut satırına dokunmuyor; sonuç sayıları doğru.
- **`funding_gate.py`**: kontrat yok (`False` **ve** `NULL` ayrı ayrı) → `None`; satır yok → engelliyor; bayat veri → engelliyor; tam eşik değeri → engellemiyor; eşiğin üstü/altı doğru yönde engelliyor (long ve short ayrı ayrı); ters yön engellenmiyor (pozitif aşırı funding short'u engellemez).
- **`scenario_runner.py`**: funding gate'i sinyal üretimini engelliyor; BTC rejim gate'i ile birlikte doğru çalışıyor (ikisi bağımsız); futures kontratı olmayan sembolde senaryo üretimi eskisi gibi çalışıyor (regresyon).
- **`scheduler.py`**: `refresh_funding_rates` saatlik job'da `run_scenario_generation`'dan önce çağrılıyor; kendi hatası job'ın geri kalanını (senaryo/öğrenme/paper adımları ve özet log) durdurmuyor; günlük "1d" job'ında çağrılmıyor.
