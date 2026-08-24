# Web Dashboard — Tasarım

**Tarih:** 2026-08-24
**Durum:** Onaylandı, uygulama planı bekleniyor

## Bağlam ve Kapsam

"Kripto Analiz Ofisi" projesinin dört alt sistemi (A: veri altyapısı, B: senaryo üretim motoru, C: confidence/öğrenme döngüsü, D: paper test portföyü) tamamlandı, ancak servisin ilerlemesini görmenin tek yolu loglar (`logs/app.log`) ve doğrudan veritabanı sorgusu. Bu, Subsystem E: servisin çalıştığını ve paper portföyün durumunu bir tarayıcıdan tek bakışta görmeyi sağlayan basit, tek sayfalık, herkese açık ama parola korumalı bir web dashboard'u ekler.

Kapsam dışı: gerçek zamanlı push/websocket güncellemeleri (sayfa yenilemesi yeterli), çoklu sayfa/gezinme, kullanıcı yönetimi (tek sabit kullanıcı/parola çifti yeterli), grafik için harici JS kütüphanesi (inline SVG yeterli), mobil özel tasarım, HTTPS/TLS'in uygulama içinde yönetilmesi (Railway'in public domain'i bunu otomatik sağlıyor).

## Hedef

Bir tarayıcıdan, Basic Auth ile korunan tek bir sayfada: servisin en son ne zaman çalıştığı (sağlık durumu), simüle edilen portföyün güncel equity'si ve kısa geçmişi, açık paper pozisyonlar ve en son üretilen senaryolar görülebilsin.

## Mimari

Ayrı bir servis değil, mevcut sürecin (process) içinde çalışan bir Flask uygulaması. `src/main.py`'deki `run_forever`, `BackgroundScheduler`'ı başlattıktan sonra ana thread'i `time.sleep(60)` ile boşta bekletiyordu (bkz. `main.py:96-104`) — bu döngü, ana thread'i artık web sunucusuna devreden bir çağrıyla değiştirilir. `BackgroundScheduler` değişmeden kendi arka plan thread'inde çalışmaya devam eder; iki katman yalnızca aynı `session_factory`'yi paylaşır, aralarında başka bir bağ yoktur.

```
main() ── startup() ── run_forever()
                              │
                              ├──> scheduler.start()  (arka plan thread — değişmedi)
                              │
                              └──> create_app(session_factory).run(host="0.0.0.0", port=PORT)  (ana thread — yeni)
```

Her HTTP isteği kendi DB oturumunu (`session_factory()`) açıp kapatır — scheduler'ın job'larıyla oturum paylaşımı yoktur, birbirlerini bloklamazlar.

## Bileşenler

### `src/web.py` (yeni)

`create_app(session_factory) -> Flask`: Flask app factory.

- `GET /` — Basic Auth kontrolünden geçen tek route. Auth başarısızsa `401` + `WWW-Authenticate: Basic` header'ı döner (tarayıcı kendi login popup'ını gösterir).
- Auth kontrolü: `BASIC_AUTH_USER` / `BASIC_AUTH_PASS_HASH` ortam değişkenleriyle karşılaştırma (`werkzeug.security.check_password_hash`, `generate_password_hash` ile üretilmiş bir hash `BASIC_AUTH_PASS_HASH`'te tutulur — düz metin parola commit edilmez, env'de de düz metin olarak durmaz).
- Route içinde bir `try/except`: DB sorgusu patlarsa 500 yerine "veri şu an okunamıyor" mesajıyla `dashboard.html` render edilir; hata loglanır. Bot'un kendisi (scheduler thread'i) bundan etkilenmez.

### `src/dashboard_data.py` (yeni, saf sorgu fonksiyonları — test edilebilirlik için `web.py`'den ayrı)

- `get_system_health(session, now=None) -> SystemHealth(last_activity, status)`
  `MAX(FetchLog.finished_at)` = `last_activity`. `status`: `now - last_activity` **90 dakikadan azsa** `"healthy"`, **90 dk – 4 saat** arasıysa `"delayed"`, **4 saatten fazlaysa veya hiç kayıt yoksa** `"stopped"`. (Saatlik cron'un normal aralığı 1 saat; 90 dk eşiği tek bir gecikmiş çalıştırmaya tolerans tanır.)
- `get_equity_summary(session) -> EquitySummary(current, history)`
  `current`: mevcut `paper_equity.current_equity(session)`. `history`: son 50 kapanmış `PaperPosition`'ın `(closed_at, equity_after)` çiftleri, `closed_at` ASC (grafik solda eskiden sağda yeniye aksın diye).
- `get_open_positions(session) -> list[PaperPosition]`
  `status == "open"`, `opened_at` DESC.
- `get_recent_scenarios(session, limit=10) -> list[Scenario]`
  `created_at` DESC LIMIT `limit`.

### `src/templates/dashboard.html` (yeni)

Jinja2 template, inline `<style>` (build adımı yok, harici CDN yok). Bölümler:

1. Sağlık rozeti: `status`'a göre renk (`healthy`=yeşil, `delayed`=sarı, `stopped`=kırmızı) + "son aktivite: X dakika önce".
2. Equity: güncel rakam (büyük punto) + `history`'den çizilen basit inline-SVG polyline sparkline (harici JS/kütüphane yok — Jinja içinde noktalar hesaplanıp `<polyline points="...">` olarak basılır). `history` boşsa (hiç pozisyon kapanmadıysa) sadece `STARTING_EQUITY` rakamı gösterilir, grafik atlanır.
3. Açık pozisyonlar tablosu: sembol, yön, giriş, stop, hedef, açılış zamanı. Boşsa "açık pozisyon yok".
4. Son senaryolar tablosu: sembol, yön, confidence, durum, oluşturulma zamanı. Boşsa "henüz senaryo yok".

### `src/main.py` değişikliği

`run_forever`'daki `while True: time.sleep(60)` bloğu kaldırılır, yerine:

```python
from src.web import create_app

app = create_app(session_factory)
app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
```

`KeyboardInterrupt`/`SystemExit` yakalama mantığı korunur (Flask'in `run()`'ı da `Ctrl+C` ile durur; `scheduler.shutdown()` `finally` bloğuna taşınır).

### Konfigürasyon

`.env.example`'a eklenir: `BASIC_AUTH_USER`, `BASIC_AUTH_PASS_HASH`, `PORT` (yerelde varsayılan 8000; Railway kendi `PORT` değerini otomatik enjekte eder). `requirements.txt`'e eklenen: `flask`.

## Hata Yönetimi ve Dayanıklılık

- Dashboard route'u kendi hatasını yutar (yukarıda) — bir DB sorgu hatası ne botu ne de sayfanın erişilebilirliğini bozar.
- Web sunucusunun çökmesi/başlatılamaması (ör. port çakışması) `main()`'i durdurur — bu kabul edilebilir, çünkü mevcut davranışta da ana thread'in kendisi zaten servisin "canlı" göstergesiydi (`time.sleep(60)` döngüsü). Scheduler'ın web sunucusundan bağımsız çalışmaya devam etmesi bu tasarımın kapsamı dışında (ayrı process/thread ayrımı gerektirir, YAGNI).
- Basic Auth başarısız denemeleri sadece `401` döner, ekstra rate-limit/lockout eklenmez (tek kullanıcılı, düşük riskli bir izleme sayfası — kapsam dışı).

## Test Yaklaşımı

- **`dashboard_data`**: her fonksiyon için in-memory SQLite ile — health eşiklerinin (healthy/delayed/stopped) doğru sınırları, equity history'nin boş/dolu durumları, open positions ve recent scenarios sıralaması/limiti.
- **`web`**: Flask test client ile — auth yoksa/yanlışsa `401`, doğru auth ile `200` ve sayfada DB'deki verilerin (equity rakamı, pozisyon sembolleri) göründüğünün doğrulanması; DB sorgusu bir mock ile patlatıldığında sayfanın yine de `200` + hata mesajıyla döndüğü.
- Mevcut `tests/conftest.py`'deki in-memory SQLite fixture pattern'i yeniden kullanılır.
