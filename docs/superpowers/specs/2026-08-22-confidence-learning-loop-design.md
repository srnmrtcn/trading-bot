# Confidence/Öğrenme Döngüsü — Tasarım

**Tarih:** 2026-08-22
**Durum:** Onaylandı, uygulama planı bekleniyor

## Bağlam ve Kapsam

Bu, "Kripto Analiz Ofisi" projesinin üçüncü alt sistemidir (Subsystem C). Genel proje dört alt sistemden oluşuyor: A (veri altyapısı — tamamlandı), B (senaryo üretim motoru — tamamlandı), C (confidence/öğrenme döngüsü — bu spec), D (paper test çerçevesi). Bu spec yalnızca C'yi kapsar.

Subsystem B, her aktif sembol için `scenarios` tablosuna `status="pending"` ile long/short senaryolar yazıyor (entry/target/stop/expected_return_pct/confidence_score/created_at/expires_at). Subsystem C, bu pending senaryoları Subsystem A'nın gerçek mum verisiyle otomatik olarak sonuçlandırır ve geçmiş sonuçlardan öğrenerek yeni senaryolara kalibre edilmiş bir güven skoru atar. Subsystem C, Subsystem B'den bağımsızdır — B, C'nin varlığından habersiz çalışmaya devam eder; C sadece `scenarios` tablosunu okur ve günceller.

## Hedef

Her 1h scheduler işinin sonunda: (1) tüm `pending` senaryoları gerçek fiyat hareketiyle karşılaştırıp `hit_target`, `hit_stop` veya `expired` olarak sonuçlandırmak, (2) yön + confidence aralığı desenine göre geçmiş başarı oranını hesaplayıp bunu henüz kalibre edilmemiş senaryolara `calibrated_confidence` olarak yazmak.

Kapsam dışı (bu spec için): gerçek/paper emir verme (Subsystem D'nin işi), Subsystem B'nin ham `confidence_score` hesaplama mantığının değiştirilmesi, sembol veya coin bazlı ayrı öğrenme (sadece yön+confidence deseni), Bayesian/ağırlıklı yumuşatma (basit eşik-altı/eşik-üstü mantığı yeterli).

## Mimari

Subsystem A'nın scheduler'ındaki 1h işine (`run_timeframe_job("1h")`), Subsystem B'nin senaryo üretiminden hemen sonra eklenen bir Learning Runner adımı. Ayrı bir servis değil, aynı Python paketi içinde aynı veritabanına yazan yeni modüller.

```
run_timeframe_job("1h") — kline fetch/gap repair biter
        │
        ▼
run_scenario_generation (Subsystem B) — yeni pending senaryolar
        │
        ▼
Learning Runner (Subsystem C)
        │
        ├──> Outcome Evaluator ──> tüm pending senaryoları sonuçlandır
        │
        └──> Confidence Calibrator ──> kalibre edilmemiş senaryolara calibrated_confidence yaz
```

## Bileşenler

### Outcome Evaluator
Tüm `status="pending"` senaryoları sorgular — aktif sembol listesine bağlı kalmadan (delisted coinlerin geçmiş fiyat verisi `klines` tablosunda hâlâ mevcut, sonuçları yine değerlendirilebilir). Her senaryo için:
- Symbol'ün `created_at`'ten şimdiye kadarki 1h mumlarını kronolojik sırayla (`open_time` artan) tarar.
- Her mumda: **long** için `high >= target_price` VE `low <= stop_price` aynı anda gerçekleşmişse **stop öncelikli** sayılır (muhafazakâr varsayım — sistem kendi başarısını abartmaz). Sadece `high >= target_price` ise `hit_target`; sadece `low <= stop_price` ise `hit_stop`. **short** için yönler ters çevrilir (`low <= target_price` / `high >= stop_price`).
- Hiçbir mumda ne hedef ne stop tetiklenmemişse ve `expires_at` geçmişse → `expired`.
- Sonuçlanan senaryolarda `status` güncellenir, `resolved_at` = sonuçlandığı mumun `open_time`'ı (veya `expired` durumunda `expires_at`) olarak yazılır.
- Henüz ne sonuçlanmış ne süresi dolmuşsa dokunulmaz, `pending` kalır.

### Confidence Calibrator
`confidence_score`'u 0.1 genişliğinde 10 kovaya ayırır (`[0.0,0.1)`, ..., `[0.9,1.0]`). Her `(direction, kova)` deseni için, çözümlenmiş (pending olmayan) tüm geçmiş senaryolardan başarı oranını hesaplar:

```
başarı_oranı = hit_target_sayısı / (hit_target_sayısı + hit_stop_sayısı + expired_sayısı)
```

Bir desen için toplam örnek sayısı **20 veya üzeri** ise bu oran, o desendeki senaryolara `calibrated_confidence` olarak yazılır. Örnek sayısı 20'nin altındaysa `calibrated_confidence` = ham `confidence_score` (kalibrasyon henüz güvenilir değil, veri biriktikçe devreye girer). Bu, `calibrated_confidence` alanı henüz `NULL` olan (yeni üretilmiş) veya bir önceki çalıştırmadan bu yana sonucu değişen (Outcome Evaluator tarafından yeni sonuçlanan) senaryolara uygulanır.

### Learning Runner
Her 1h işinin sonunda, `run_scenario_generation`'dan hemen sonra çalışır: önce Outcome Evaluator tüm pending senaryoları tarar, sonra Confidence Calibrator kalibrasyon gerektiren senaryoları günceller. Diğer alt sistemlerle tutarlı per-senaryo izolasyon uygulanır: bir senaryonun değerlendirilmesi sırasında hata olursa (ör. o sembolün mum verisi eksikse) diğer senaryoların işlenmesi durmaz, hata loglanır.

### Storage
`scenarios` tablosuna iki yeni alan:
- `resolved_at` (DateTime, nullable) — senaryonun sonuçlandığı an.
- `calibrated_confidence` (Numeric(5,4), nullable) — kalibre edilmiş güven skoru.

Mevcut `status` alanının kabul ettiği değerler genişletilir: `pending` (varsayılan) | `hit_target` | `hit_stop` | `expired`. Mevcut `confidence_score` alanına dokunulmaz — Subsystem B'nin ham çıktısı olarak, denetlenebilirlik için korunur.

## Hata Yönetimi ve Dayanıklılık

- Bir senaryonun değerlendirilmesi veya kalibrasyonu sırasında hata, diğer senaryoların işlenmesini durdurmaz (Subsystem A/B'deki izolasyon deseninin aynısı: try/except + rollback + log + devam).
- Loglama: her çalıştırmanın özet istatistiği (kaç senaryo değerlendirildi, kaç tanesi sonuçlandı — hangi durumda, kaç desen için kalibrasyon güncellendi).

## Test Yaklaşımı

- **Outcome Evaluator**: hit_target, hit_stop, expired, aynı mumda çakışma (stop öncelikli), henüz sonuçlanmamış (pending kalması) durumları sentetik mum verisiyle test edilir; hem long hem short yön için.
- **Confidence Calibrator**: kova hesaplama doğruluğu, 20 örnek eşiğinin altında ham skora düşme, eşik üstünde gerçek başarı oranını doğru hesaplama, farklı (yön, kova) desenlerinin birbirini etkilememesi test edilir.
- **Learning Runner**: gerçek in-memory SQLite DB üzerinde uçtan uca entegrasyon testi — gerçek bir pending senaryo + gerçek mum verisiyle sonuçlanıp `calibrated_confidence` alanının güncellendiği, izole edilmiş bir hatanın diğer senaryoları etkilemediği doğrudan test edilir (Subsystem B'nin final review'ında öğrenilen ders: en az bir testin, stub'larla değil, gerçek bir kayda kadar tüm zinciri çalıştırması şart).
