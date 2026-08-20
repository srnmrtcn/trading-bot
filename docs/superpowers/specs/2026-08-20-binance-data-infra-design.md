# Binance Veri Altyapısı — Tasarım

**Tarih:** 2026-08-20
**Durum:** Onaylandı, uygulama planı bekleniyor

## Bağlam ve Kapsam

Bu, daha büyük "Kripto Analiz Ofisi" projesinin ilk alt sistemidir (Subsystem A). Genel hedef; Binance'ten sürekli veri çeken, potansiyel coinler üzerinde kâr senaryoları üreten ve bu senaryoların sonuçlarından öğrenerek confidence skorunu artıran bir sistem kurmaktır. Bu kapsam dört bağımsız alt sisteme ayrıldı:

- **A — Veri altyapısı** (bu spec): Binance'ten güvenilir, bütünlüğü doğrulanmış piyasa verisi toplama ve saklama.
- **B — Senaryo üretim motoru**: Potansiyel coinler için kâr senaryoları üretir. (sonraki spec)
- **C — Confidence/öğrenme döngüsü**: Üretilen senaryoların sonuçlarını takip edip güven skorunu günceller. (sonraki spec)
- **D — Paper test çerçevesi**: Senaryoları gerçek para riske atmadan simüle eder. (sonraki spec)

B, C ve D; A'nın sağladığı güvenilir veri katmanı üzerine kurulacak. Bu spec yalnızca A'yı kapsar.

## Hedef

Binance'teki tüm USDT paritelerinin 1h ve 1d mum (kline) verilerini sürekli çeken, PostgreSQL'de saklayan ve kendi bütünlüğünü (eksik veri, kopya kayıt, anormal değer) sürekli kontrol eden, kesintiden kendini toparlayan bir arka plan servisi.

Kapsam dışı (bu spec için): sipariş defteri/trade akışı verisi, çoklu borsa karşılaştırması, senaryo üretimi, gerçek/paper emir verme.

## Mimari

Tek bir sürekli çalışan Python servisi + PostgreSQL veritabanı. Servis, uygulama içi bir zamanlayıcı (APScheduler) ile kendi döngüsünü yönetir — harici cron'a bağımlı değildir. Binance'in yalnızca herkese açık (public) kline endpoint'i kullanılır; API key/secret gerekmez, bu da ilk fazı güvenlik açısından basit tutar (okuma dışında hiçbir yetki yok).

```
Scheduler ──tetikler──> Symbol Registry ──sembol listesi──> Kline Fetcher
                                                                  │
                                                                  ▼
                                                         Integrity Checker
                                                                  │
                                                                  ▼
                                                            Storage Layer (PostgreSQL)
                                                                  │
                                                                  ▼
                                                              fetch_log
```

## Bileşenler

### Symbol Registry
Binance `exchangeInfo` endpoint'inden aktif tüm USDT paritelerini çeker. Günde bir kez yenilenir; yeni listelenen veya delist edilen coinleri yakalar. `symbols` tablosunda `is_active` alanıyla takip edilir — delist edilen semboller silinmez, pasif olarak işaretlenir (geçmiş veri korunur).

### Kline Fetcher
`python-binance` kütüphanesi ile 1h ve 1d mum verisi çeker. Binance'in ağırlık bazlı (weight-based) rate limitine uygun şekilde istekleri gruplar/aralıklandırır; 429/418 yanıtlarında exponential backoff + Binance'in `Retry-After` başlığına uyar.

### Backfill Engine
İlk kurulumda her sembol için son 2 yıllık geçmiş veriyi çeker. Sonrasında Integrity Checker bir gap tespit ettiğinde, yalnızca eksik aralığı otomatik olarak doldurur. Yeni listelenen bir coin eklendiğinde de otomatik olarak tetiklenir.

### Integrity Checker
Her yazım öncesi/sonrası şu kontrolleri yapar:
- **Gap tespiti**: beklenen mum aralığında (örn. ardışık saatlerde) eksik `open_time` var mı.
- **Kopya kayıt önleme**: `(symbol, timeframe, open_time)` üzerinde unique constraint + upsert.
- **Anormal değer işaretleme**: sıfır hacim, aşırı fiyat sapması (örn. önceki muma göre %50+ sıçrama) gibi şüpheli kayıtlar silinmez, `flagged=true` ile işaretlenir — ileride analiz katmanının bilinçli karar vermesi için saklanır.

### Scheduler
Uygulama içi APScheduler döngüsü: 1h mumlar için saatlik, 1d mumlar için günlük tetikleme; sembol listesi günde bir kez yenilenir. Servis yeniden başladığında `fetch_log`'daki son başarılı çalışmaya bakarak kaldığı yerden devam eder.

### Storage Layer
PostgreSQL + SQLAlchemy. Tablolar:
- `symbols` (symbol, base_asset, quote_asset, is_active, listed_at, updated_at)
- `klines` (symbol, timeframe, open_time, open, high, low, close, volume, flagged, unique(symbol, timeframe, open_time))
- `fetch_log` (run_id, started_at, finished_at, symbol, timeframe, status, error_message) — izlenebilirlik ve crash-recovery için.

## Hata Yönetimi ve Dayanıklılık

- Bir sembolün çekilmesi başarısız olursa diğer sembollerin işlenmesini durdurmaz (sembol bazlı izolasyon).
- Ağ hatalarında sembol bazında sınırlı sayıda retry.
- Rate limit aşımında (429/418) exponential backoff.
- Servis çökerse/yeniden başlatılırsa `fetch_log` üzerinden kaldığı yerden devam eder — veri kaybı veya tekrar olmadan.
- Loglama: konsol + dosya, her çalıştırmanın özet istatistiği (kaç sembol başarılı/başarısız, kaç gap dolduruldu).

## Test Yaklaşımı

- **Birim testler**: Integrity Checker (gap tespiti, duplicate önleme mantığı), Storage Layer (upsert davranışı), Kline Fetcher (rate-limit backoff mantığı) — gerçek Binance API'sine gitmeden, mock response'larla.
- **Entegrasyon testi**: Gerçek Binance API'sine karşı küçük ölçekli, opsiyonel bir test (tek sembol, kısa zaman aralığı) — CI'da varsayılan olarak çalışmaz, manuel tetiklenir.

## Kurulum Notları (uygulama planında ele alınacak)

- Ortamda PostgreSQL 16 (Homebrew ile kurulu, `psql` mevcut) var ama servis olarak çalışmıyor — ilk kurulumda başlatılması gerekecek.
- Docker mevcut değil — Postgres doğrudan Homebrew servisi olarak veya yerel process olarak çalıştırılacak.
