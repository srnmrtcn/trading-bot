# Senaryo Üretim Motoru — Tasarım

**Tarih:** 2026-08-21
**Durum:** Onaylandı, uygulama planı bekleniyor

## Bağlam ve Kapsam

Bu, "Kripto Analiz Ofisi" projesinin ikinci alt sistemidir (Subsystem B). Genel proje dört alt sistemden oluşuyor: A (veri altyapısı — tamamlandı), B (senaryo üretim motoru — bu spec), C (confidence/öğrenme döngüsü), D (paper test çerçevesi). Bu spec yalnızca B'yi kapsar.

Subsystem A, tüm aktif Binance USDT paritelerinin 1h ve 1d mum verisini sürekli çekip PostgreSQL'de saklıyor (`symbols`, `klines`, `fetch_log` tabloları). Subsystem B, bu veriyi okuyarak her aktif sembol için potansiyel kâr senaryoları üretir ve yeni bir `scenarios` tablosuna yazar. Subsystem B kendisi hiçbir veri çekmez, hiçbir emir vermez — yalnızca Subsystem A'nın verisi üzerinde teknik analiz çalıştırıp yapılandırılmış senaryo kayıtları üretir.

## Hedef

Her aktif USDT paritesi için, Subsystem A'nın 1h mum verisini kullanarak RSI + hareketli ortalama kesişimi + hacim spike'ı sinyallerinin aynı yönde hizalandığı anlarda; giriş fiyatı, destek/direnç bazlı hedef ve stop seviyeleri, beklenen getiri yüzdesi, kural bazlı bir güven skoru ve değişken bir geçerlilik süresi (`expires_at`) içeren bir "long" veya "short" senaryo üretip `scenarios` tablosuna kaydeden bir motor.

Kapsam dışı (bu spec için): gerçek/paper emir verme, senaryo sonuçlarının takibi/değerlendirilmesi (bu Subsystem C'nin işi), confidence skorunun zamanla kalibre edilmesi (Subsystem C), 1h dışındaki zaman dilimleri, futures/margin işlemleri.

## Mimari

Subsystem A'nın scheduler'ındaki 1h işi (`run_timeframe_job(timeframe="1h")`) tamamlandıktan hemen sonra tetiklenen bir Scenario Runner. Aynı Python paketi (`src/`) içinde, aynı PostgreSQL veritabanına yazan yeni modüller. Ayrı bir servis/process değil — mevcut APScheduler döngüsüne eklenen bir adım.

```
run_timeframe_job("1h") biter
        │
        ▼
Scenario Runner ──sembol listesi──> Indicator Calculator
        │                                  │
        │                                  ▼
        │                         Signal Evaluator
        │                                  │
        │                    sinyal tetiklendi mi?
        │                                  │ evet
        │                                  ▼
        │                     Support/Resistance Detector
        │                                  │
        │                                  ▼
        │                          Scenario Builder
        │                                  │
        ▼                                  ▼
   (izolasyon + log)              Storage (scenarios tablosu)
```

## Bileşenler

### Indicator Calculator
Bir sembolün son ~100 saatlik mumundan üç gösterge hesaplar:
- **RSI(14)** — standart 14 periyotluk Relative Strength Index.
- **EMA kesişimi (9/21)** — 9 periyotluk ve 21 periyotluk üstel hareketli ortalamalar arasındaki kesişim durumu (hızlı MA yavaş MA'yı yukarı/aşağı kesti mi, en son kapanan mumda).
- **Hacim spike'ı** — güncel mumun hacmi, son 20 periyodun ortalama hacminin 2 katından fazla mı.

Girdi olarak yalnızca `klines` tablosundan okunan OHLCV verisini kullanır; başka bir veri kaynağına ihtiyaç duymaz.

### Support/Resistance Detector
Son 90 mumluk pencerede yerel tepe/dip (swing high/low) noktalarını tespit eder: bir mumun `high` değeri kendisinden önceki ve sonraki 3'er mumdan (K=3) yüksekse swing high, `low` değeri benzer şekilde düşükse swing low sayılır. Güncel fiyatın üzerindeki en yakın swing high = direnç, altındaki en yakın swing low = destek olarak döner.

### Signal Evaluator
Her aktif sembol için Indicator Calculator'ın çıktısını değerlendirir:
- **Long sinyali:** RSI son mumda 30'un altından yukarı geçmiş (aşırı satımdan dönüş) **ve** EMA(9) EMA(21)'i yukarı kesmiş **ve** hacim spike'ı var.
- **Short sinyali:** RSI son mumda 70'in üzerinden aşağı geçmiş (aşırı alımdan dönüş) **ve** EMA(9) EMA(21)'i aşağı kesmiş **ve** hacim spike'ı var.
- Üç koşuldan biri bile sağlanmıyorsa o sembol için senaryo üretilmez.

### Scenario Builder
Sinyal tetiklendiğinde:
- **entry_price** = son kapanış fiyatı.
- **Long için:** target_price = üstteki en yakın direnç, stop_price = alttaki en yakın destek. **Short için:** tersi.
- Support/Resistance Detector uygun bir seviye bulamazsa (ör. fiyat pencerenin en tepesinde/dibinde) o sembol için senaryo üretilmez — sonraki periyotta tekrar denenir.
- **expected_return_pct** = `(target_price - entry_price) / entry_price` (long) veya tersi (short).
- **confidence_score** (0-1 arası) = risk/ödül oranı (hedef mesafesi / stop mesafesi, 1.0'da tavan) ile sinyal gücünün (RSI'ın eşik değerden ne kadar uzakta olduğu) ağırlıklı ortalaması. Bu, Subsystem C'nin zamanla kalibre edeceği bir başlangıç skorudur — kesin ağırlıklar uygulama planında netleştirilecek.
- **expires_at** = `created_at + (hedef mesafesi / son 20 periyodun ortalama true range'i) saat` — yani fiyatın son volatiliteye göre hedefe ulaşması "normalde" ne kadar sürerse o kadar süre tanınır. Minimum 6 saat, maksimum 7 gün ile sınırlanır (aşırı kısa/uzun pencereleri önlemek için).
- **status** = `"pending"`.

### Scenario Runner
Subsystem A'nın scheduler'ındaki 1h işine (`run_timeframe_job`) entegre edilir: her sembolün kline fetch + gap repair adımları tamamlandıktan sonra, aynı per-symbol döngü içinde (veya işin sonunda ayrı bir per-symbol geçişte) Scenario Runner çalışır. Subsystem A'da kurulan izolasyon politikası burada da uygulanır: bir sembolün gösterge hesaplama veya senaryo yazma hatası diğer sembollerin işlenmesini durdurmaz, hata loglanır.

### Storage
Yeni `Scenario` tablosu (PostgreSQL, SQLAlchemy):
`id, symbol, direction (long/short), entry_price, target_price, stop_price, expected_return_pct, confidence_score, created_at, expires_at, status (pending, varsayılan)`

Aynı sembol için aynı yönde zaten `status="pending"` bir senaryo varsa yeni bir tane oluşturulmaz (tekrar sinyal üretimini önlemek için) — mevcut pending senaryo süresi dolana veya Subsystem C tarafından güncellenene kadar korunur.

## Hata Yönetimi ve Dayanıklılık

- Bir sembol için gösterge hesaplama, support/resistance tespiti veya DB yazma hatası, diğer sembollerin işlenmesini durdurmaz (Subsystem A'daki `process_symbol_timeframe` izolasyon deseninin aynısı).
- Yetersiz mum verisi olan bir sembol (ör. yeni listelenmiş, 100 mumdan az geçmişi olan) sessizce atlanır, hata sayılmaz.
- Loglama: her çalıştırmanın özet istatistiği (kaç sembol tarandı, kaç senaryo üretildi, kaç sembol hata verdi) — Subsystem A'nın log formatına tutarlı.

## Test Yaklaşımı

- **Indicator Calculator**: RSI/EMA/hacim spike hesaplamaları, elle hesaplanmış küçük sentetik veri setleriyle doğrulanır (bilinen girdi → bilinen çıktı).
- **Support/Resistance Detector**: sentetik fiyat serileriyle (bilinen swing high/low noktaları içeren) test edilir; pencerede uygun seviye bulunamama durumu da test edilir.
- **Signal Evaluator**: long/short/hiçbiri tetiklenme senaryoları, mock gösterge girdileriyle test edilir.
- **Scenario Builder**: giriş/hedef/stop/expected_return/confidence/expires_at hesaplamaları, mock gösterge + support/resistance girdileriyle test edilir.
- **Scenario Runner**: Subsystem A'nın scheduler test deseniyle uyumlu, gerçek in-memory SQLite DB üzerinde entegrasyon testleri (gerçek Binance client'a gerek yok, veri zaten `klines` tablosundan okunuyor); izolasyon davranışı (bir sembol hata verirse diğerlerinin işlenmeye devam etmesi) doğrudan test edilir.
