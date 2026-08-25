# BTC Rejim Filtresi — Tasarım

**Tarih:** 2026-08-25
**Durum:** Onaylandı, uygulama planı bekleniyor

## Bağlam ve Kapsam

"Kripto Analiz Ofisi" projesinin orijinal dört alt sistemi (A: veri altyapısı, B: senaryo motoru, C: öğrenme döngüsü, D: paper test portföyü) tamamlandı. Bu spec, o roadmap'in üzerine eklenen yeni bir alt sistemi kapsıyor: sinyal üretimine bir **BTC trend rejimi filtresi** eklemek.

Motivasyon: mevcut sinyal mantığı (RSI + EMA(9/21) kesişimi + hacim spike'ı) her sembolü piyasa rejiminden bağımsız, izole şekilde değerlendiriyor. Altcoin'lerin büyük çoğunluğu BTC'ye yüksek beta ile hareket ettiği için, BTC güçlü bir düşüş trendindeyken üretilen "long" sinyalleri sistematik olarak "falling knife" yakalama riski taşıyor (ve tersi, güçlü yükseliş trendinde "short" sinyalleri). Bu filtre, sinyalin yönü BTC'nin mevcut trendiyle uyuşmadığında senaryo üretimini engelleyerek bu riski azaltmayı hedefliyor.

Kapsam dışı: confidence skoruna rejim bazlı bir ceza/ağırlık eklemek (sert engelleme seçildi, bkz. Kararlar), BTC dışında bir rejim referansı (ör. toplam piyasa değeri) kullanmak, mevcut dört alt sistemin herhangi birinin davranışını değiştirmek.

## Kararlar (brainstorming'de netleşen)

- **Rejim göstergesi:** BTCUSDT'nin 1d zaman diliminde EMA(9) / EMA(21) ilişkisi. EMA9 > EMA21 ⇒ `"up"`, aksi halde ⇒ `"down"`.
- **Filtre etkisi:** Sert engelleme — rejimle ters yönlü sinyaller senaryo aşamasında tamamen reddedilir, confidence skoru etkilenmez.
- **BTC istisnası:** Yok. BTCUSDT'nin kendi sinyalleri de aynı filtreye tabidir (özel durum kodu yok).
- **Rejim belirsizliği:** BTC'nin 1d verisi yetersiz/süreksiz/bayatsa rejim `None` sayılır ve o çalıştırmada **hiçbir** sembol için senaryo üretilmez (yalnızca BTC değil — yön bilinmeden hiçbir sinyal işlenmez).

## Mimari / Veri Akışı

Yeni bağımsız modül: `src/btc_regime.py`. Mevcut `run_scenario_generation` (Subsystem B, `scenario_runner.py`) tarafından çağrılır; scheduler'a (`scheduler.py`) doğrudan bir değişiklik gerekmez çünkü entegrasyon `run_scenario_generation`'ın içine gizlenir.

```
run_scenario_generation(session, symbols)
        │
        ▼
compute_btc_regime(session, now)  ──BTCUSDT 1d klines──> "up" | "down" | None
        │
        ▼
her sembol için process_symbol_scenario(session, symbol, regime, ...)
        │
   sinyal üretildi mi (mevcut mantık)?
        │ evet
        ▼
   sinyal yönü regime ile uyuşuyor mu?
        │ evet                    │ hayır / regime None
        ▼                         ▼
   senaryo üretilir           "skipped"
```

BTC'nin 1d verisi günde bir değiştiği için regime, saatlik job'ın her çalıştırmasında yeniden hesaplanır ama pratikte gün içinde aynı sonucu verir — ayrı bir cache mekanizması gerekmez, hesaplama ucuzdur (tek sembol, tek sorgu).

## Bileşenler

### `compute_btc_regime(session, now=None) -> "up" | "down" | None`

`src/btc_regime.py` içinde, `scenario_runner.py`'deki `_load_recent_klines` / `_window_rejection` desenini BTCUSDT + 1d için tekrar kullanır (aynı sorgu şekli, aynı red gerekçeleri — mevcut kod bu haliyle sembole/timeframe'e parametrik değil, bu yeni kullanım için genelleştirilip paylaşılan bir yardımcıya taşınabilir; kesin refactor kararı uygulama planında verilecek).

Adımlar:
1. `current_boundary = floor_to_timeframe(now, "1d")`.
2. Son `EMA_SLOW_PERIOD + 1` (=22) kapanmış BTCUSDT 1d mumunu oku (`open_time < current_boundary`, en yeniden eskiye, sonra ters çevir).
3. Aşağıdaki durumlardan biri varsa `None` dön (regime belirsiz):
   - Yetersiz mum sayısı (< 22).
   - Süreksiz pencere (mumlar arasında gap var — `scenario_runner._window_rejection`'daki kontiguity kontrolüyle aynı prensip).
   - Bayat veri (en yeni kapanmış mum, beklenen sınırdan daha eski).
   - Pencerede anomali işaretli (`flagged`) bir mum var.
4. `compute_ema` (mevcut `indicators.py`) ile kapanışlardan EMA9 ve EMA21 serilerini hesapla.
5. Son değerleri karşılaştır: EMA9[-1] > EMA21[-1] ⇒ `"up"`, aksi halde ⇒ `"down"`.

### `scenario_runner.py` değişiklikleri

- `run_scenario_generation(session, symbols, now=None)`: en başta `regime = compute_btc_regime(session, now)` çağrılır (bir kez, döngü dışında). `now` parametresi mevcut imzaya eklenir (şu an yok) — testlerde deterministik regime/sinyal kombinasyonları kurabilmek için.
- `process_symbol_scenario(session, symbol, regime, timeframe="1h", now=None)`: `evaluate_signal` bir sinyal döndürdükten sonra, mevcut `has_pending_scenario` kontrolünden önce şu ek kontrol eklenir:
  ```
  if regime is None:
      return "skipped"
  if signal.direction == "long" and regime != "up":
      return "skipped"
  if signal.direction == "short" and regime != "down":
      return "skipped"
  ```

## Hata Yönetimi

- `compute_btc_regime` içindeki tüm "belirsiz" durumlar exception değil, `None` dönüşü — mevcut "atla, hata sayma" felsefesiyle tutarlı.
- `compute_btc_regime` döngü dışında, per-symbol try/except'ten önce çağrıldığı için beklenmedik bir hatası (ör. DB bağlantı sorunu) `run_scenario_generation`'ın tamamını durdurabilir; bu, `scheduler.py`'nin zaten `run_scenario_generation` çağrısını saran dış try/except'i tarafından yakalanıp loglanır ve öğrenme döngüsü/paper trading cycle etkilenmeden devam eder (mevcut job-seviyesi izolasyon deseni, [[feedback-plan-mandated-isolation-policy]]).

## Test Yaklaşımı

- `compute_btc_regime`: sentetik BTCUSDT 1d klines ile — EMA9>EMA21 → `"up"`, tersi → `"down"`, yetersiz mum → `None`, süreksiz pencere → `None`, bayat veri → `None`, flagged mum → `None`.
- `process_symbol_scenario`: mevcut testlere ek olarak — aşağı rejimde long sinyali reddediliyor, yukarı rejimde short reddediliyor, uyumlu yön geçiyor, `regime=None` iken her iki yön de reddediliyor; BTCUSDT sembolüyle aynı testlerin tekrarı (istisna olmadığını doğrulamak için).
- `run_scenario_generation`: regime'in sembol döngüsünden önce **bir kez** hesaplandığını doğrulayan bir test (mock/patch ile çağrı sayısı sayılır), regime sonucu tüm sembollere tutarlı şekilde uygulandığı entegrasyon testi (in-memory SQLite, mevcut desen).
