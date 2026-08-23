# Paper Test Portföyü — Tasarım

**Tarih:** 2026-08-23
**Durum:** Onaylandı, uygulama planı bekleniyor

## Bağlam ve Kapsam

Bu, "Kripto Analiz Ofisi" projesinin dördüncü ve son alt sistemidir (Subsystem D). Genel proje dört alt sistemden oluşuyor: A (veri altyapısı — tamamlandı), B (senaryo üretim motoru — tamamlandı), C (confidence/öğrenme döngüsü — tamamlandı), D (paper test portföyü — bu spec).

Subsystem C, her `pending` senaryoyu gerçek fiyat verisiyle `hit_target`/`hit_stop`/`expired` olarak sonuçlandırıyor ve yön+confidence-aralığı desenine göre `calibrated_confidence` atıyor — bu, tek tek senaryo fikirlerinin doğru çıkıp çıkmadığını gösterir. Subsystem D bunun üstüne, **kalibre edilmiş yüksek güvenli senaryoları gerçekten "işleseydik" ne olurdu** sorusunu cevaplayan simüle bir portföy ekler: sabit sermaye ile başlayan, sabit-oransal-risk ile pozisyon büyüklüğü belirleyen, eşzamanlı pozisyon sayısını sınırlayan bir kağıt üzerinde (paper) hesap.

Kapsam dışı: gerçek/otomatik emir verme (asla yapılmayacak — projenin tavanı budur), Subsystem B'nin sinyal mantığının veya Subsystem C'nin kalibrasyon mantığının değiştirilmesi, birden fazla eşzamanlı "strateji" veya risk profili simülasyonu (tek bir sabit parametre seti yeterli), gerçek zamanlı/canlı bir arayüz (raporlama şimdilik veritabanı sorgusu + log seviyesinde).

## Hedef

Her 1h scheduler işinin sonunda, öğrenme döngüsünden hemen sonra: (1) sonuçlanmış senaryolara bağlı açık paper pozisyonlarını kapatıp gerçekleşen kâr/zararı simüle edilen equity'ye işlemek, (2) kalibre edilmiş güveni eşik üzerinde olan ve henüz pozisyonu olmayan pending senaryolar için, güncel equity'ye göre boyutlandırılmış yeni paper pozisyonları açmak.

## Mimari

Subsystem A'nın scheduler'ındaki 1h işine, Subsystem C'nin öğrenme döngüsünden hemen sonra eklenen bir Paper Trading Runner adımı. Ayrı bir servis değil, aynı Python paketi içinde aynı veritabanına yazan yeni modüller.

```
run_timeframe_job("1h") — kline fetch/gap repair biter
        │
        ▼
run_scenario_generation (Subsystem B) — yeni pending senaryolar
        │
        ▼
run_learning_cycle (Subsystem C) — kalibrasyon (önce) + sonuçlandırma (sonra)
        │
        ▼
Paper Trading Runner (Subsystem D)
        │
        ├──> Position Closer ──> sonuçlanmış senaryoların açık pozisyonlarını kapat, equity güncelle
        │
        └──> Position Opener ──> eşik üstü, pozisyonsuz pending senaryolar için yeni pozisyon aç
```

**Equity takibi:** Ayrı bir "hesap" veya "equity eğrisi" tablosu yok. `PaperPosition` tablosundaki her satır kapanınca `equity_before`/`equity_after` alanlarını doldurur — pozisyon geçmişinin kendisi equity eğrisidir. Güncel equity, en son kapanan pozisyonun `equity_after`'ı (hiç pozisyon kapanmadıysa `STARTING_EQUITY` sabiti). Bu, ayrı bir running-total'ın satır geçmişinden sapabileceği bir hata sınıfını tamamen ortadan kaldırır.

**Sıralama:** Her çalıştırmada önce Closer, sonra Opener çalışır — böylece bu run'da serbest kalan sermaye, yeni pozisyonların boyutlandırılmasına yansır.

## Bileşenler

### Storage — `PaperPosition` tablosu (yeni)

- `id` (Integer, PK, autoincrement)
- `scenario_id` (Integer, FK → `scenarios.id`, **unique** — bir senaryo en fazla bir paper pozisyon açar)
- `symbol` (String), `direction` (String)
- `entry_price`, `stop_price`, `target_price` (Numeric(20,8) — senaryodan kopyalanır, pozisyon açıldığı andaki değerlerin denetlenebilirliği için)
- `risk_amount` (Numeric(20,8) — bu pozisyonda risk edilen tutar, equity'nin `RISK_PCT`'i)
- `position_size` (Numeric(20,8) — `risk_amount / |entry_price - stop_price|`)
- `opened_at` (DateTime, not null)
- `status` (String, not null, default `"open"`) — `"open"` | `"closed"`
- `closed_at` (DateTime, nullable)
- `exit_price` (Numeric(20,8), nullable)
- `realized_pnl` (Numeric(20,8), nullable — pozitif/negatif, `position_size × (exit_price − entry_price)` yön işaretiyle)
- `equity_before`, `equity_after` (Numeric(20,8), nullable — sadece kapanınca dolar)

### Sabitler (`src/paper_trading_config.py`)

- `STARTING_EQUITY = Decimal("10000")` — nominal referans miktar (birim yok, sadece yüzdesel getiri ve oranlar anlamlıdır; mutlak rakamın kendisi rastgele bir başlangıç noktasıdır).
- `RISK_PCT = Decimal("0.01")` (%1)
- `CONFIDENCE_THRESHOLD = Decimal("0.65")`
- `MAX_CONCURRENT_POSITIONS = 10`

### Position Sizer (`src/paper_sizer.py`, saf fonksiyon)

`size_position(equity: Decimal, entry_price: Decimal, stop_price: Decimal, risk_pct: Decimal) -> tuple[Decimal, Decimal]`

`risk_amount = equity * risk_pct`; `position_size = risk_amount / abs(entry_price - stop_price)`. `entry_price == stop_price` durumunda (Subsystem B'nin garantisiyle pratikte imkansız, ama savunma amaçlı) `ValueError` fırlatır — çağıran taraf bunu izolasyon try/except'i içinde yakalar.

### Position Closer (`src/paper_position_closer.py`)

`close_resolved_positions(session, now=None) -> PositionCloseResult(scanned, closed, still_open, failed)`

`status="open"` olan `PaperPosition` satırlarını, ilişkili `Scenario.status != "pending"` olanlar için tarar (`created_at` sırasına göre — equity zincirinin deterministik olması için). Her biri için:

- `hit_target` → `exit_price = target_price`
- `hit_stop` → `exit_price = stop_price`
- `expired` → `expires_at`'ten önceki veya ona en yakın kapanmış 1h mumun `close`'unu `klines` tablosundan sorgular. Böyle bir mum yoksa (ör. delisted sembol, veri boşluğu) bu pozisyon bu run'da atlanır (`still_open` sayılır), bir sonraki run'da tekrar denenir — senaryo zaten `expired` sabit kaldığı için sonsuz deneme güvenlidir.

`realized_pnl` yön işaretine göre hesaplanır: long için `position_size × (exit_price − entry_price)`, short için `position_size × (entry_price − exit_price)`. `equity_before` = güncel equity (en son kapanan pozisyonun `equity_after`'ı veya `STARTING_EQUITY`), `equity_after = equity_before + realized_pnl`.

### Position Opener (`src/paper_position_opener.py`)

`open_qualifying_positions(session, now=None) -> PositionOpenResult(scanned, opened, skipped, failed)`

`status="pending"`, `calibrated_confidence IS NOT NULL AND calibrated_confidence >= CONFIDENCE_THRESHOLD` olan ve `PaperPosition` tablosunda kaydı olmayan senaryoları tarar (`created_at` sırasına göre). Her biri için sırayla kontrol eder:

1. Aynı sembolde zaten `status="open"` bir pozisyon var mı? Varsa atla.
2. Açık pozisyon sayısı `MAX_CONCURRENT_POSITIONS`'a ulaştı mı? Ulaştıysa atla (kalan tüm adaylar da atlanır — cap dolduğunda taramayı erken durdurmak yerine her birini ayrı ayrı loglamak için tek tek "skipped" sayılır).
3. Sizer ile boyut hesapla, `PaperPosition` satırı ekle (`status="open"`, `equity_before`/`equity_after` boş — bunlar kapanışta dolar).

### Paper Trading Runner (`src/paper_trading_runner.py`)

`run_paper_trading_cycle(session, now=None) -> PaperTradingResult(closed, still_open, opened, skipped, failed)`

Önce `close_resolved_positions`, sonra `open_qualifying_positions` çağırır; ikisi de kendi içinde per-item izolasyon uygular (aşağıya bakınız). Sonuçları birleştirip döner.

### Scheduler entegrasyonu

`run_timeframe_job`'daki `1h` bloğunda, `run_learning_cycle`'dan hemen sonra çağrılır — aynı `try/except` + `logger.exception` izolasyon deseniyle (B ve C'nin scheduler'a bağlandığı şekille birebir aynı). Özet log satırı, sonuç mevcutsa `%d pozisyon kapandı, %d açıldı` bilgisiyle genişler.

## Hata Yönetimi ve Dayanıklılık

- Bir pozisyonun kapatılması veya açılması sırasında hata, diğer pozisyonların işlenmesini durdurmaz (Subsystem A/B/C'deki izolasyon deseninin aynısı: try/except + rollback + log + devam).
- `run_paper_trading_cycle`'ın kendisi de scheduler seviyesinde ayrı bir try/except içine alınır — B ve C çağrılarıyla aynı gerekçeyle (bir çağrının kendisinin veya rollback'inin fırlattığı hata, altındaki özet log satırını yutmamalı).
- Loglama: her çalıştırmanın özet istatistiği (kaç pozisyon kapandı/açık kaldı/açıldı/atlandı, kaç tanesi hata verdi).

## Test Yaklaşımı

- **Sizer**: risk/entry/stop kombinasyonlarıyla doğru `position_size` hesaplama; `entry_price == stop_price` için `ValueError`.
- **Position Closer**: hit_target/hit_stop/expired için doğru `exit_price` ve `realized_pnl` (hem long hem short), `equity_before`/`equity_after` zincirinin ardışık kapanışlarda doğru ilerlemesi, expired için kline verisi eksikse `still_open` kalması, izolasyon (bir pozisyonun hatası diğerlerini etkilemez).
- **Position Opener**: eşik altı/eşik üstü filtreleme, `calibrated_confidence IS NULL` olanların atlanması, aynı sembolde açık pozisyon varsa atlama, `MAX_CONCURRENT_POSITIONS` dolunca atlama, izolasyon.
- **Runner**: gerçek in-memory SQLite ile uçtan uca entegrasyon testi — bir senaryo pending→hit_target olup pozisyonun kapandığı, equity'nin güncellendiği ve bir sonraki pozisyonun güncel equity'yi kullandığı zincirin tamamı, stub'larla değil gerçek bir kayıt zinciriyle test edilir.
- **Scheduler**: 1h işine D'nin bağlandığı, 1d'de çalışmadığı, D hata verirse job'ın hayatta kaldığı testler (B/C'de kurulan desenin aynısı) + mevcut exact-log-line testlerinin yeni formata güncellenmesi.
