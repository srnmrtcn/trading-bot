# Kripto Analiz Ofisi — Binance Veri Altyapısı

Binance'teki tüm aktif USDT paritelerinin 1h/1d mum verisini sürekli çeken,
PostgreSQL'de saklayan ve kendi bütünlüğünü doğrulayan arka plan servisi.

## Kurulum

1. PostgreSQL'i başlat (Homebrew ile kuruluysa):
   ```bash
   brew services start postgresql@16
   ```
2. Veritabanını oluştur:
   ```bash
   createdb crypto_office
   ```
3. Bağımlılıkları kur:
   ```bash
   pip install -r requirements.txt
   ```
4. `.env.example` dosyasını `.env` olarak kopyala ve `DATABASE_URL`'i düzenle,
   sonra ortam değişkenini yükle (örn. `export $(cat .env | xargs)` veya
   shell profilinden `export DATABASE_URL=...`) — servis `BASIC_AUTH_USER` ve
   `BASIC_AUTH_PASS_HASH` olmadan da başlamaz.

## Çalıştırma

```bash
python -m src.main
```

İlk çalıştırmada: sembol listesi çekilir, henüz hiç mumu olmayan her
sembol/timeframe çifti için son 90 günlük geçmiş veri (backfill) indirilir
(`DEFAULT_BACKFILL_DAYS`),
ardından zamanlayıcı devreye girer (1h mumlar için saatlik, 1d mumlar için
günlük, sembol listesi için günlük). Her saatlik/günlük çalıştırma, normal
çekimden sonra son 30 günde eksik kalan mumları da otomatik olarak tamamlar.

Servis `Ctrl+C` ile durdurulabilir; yeniden başlatıldığında her sembol için
saklanan son mumdan (`MAX(klines.open_time)`) devam eder — `fetch_log` yalnızca
izlenebilirlik/denetim içindir, çekim penceresini belirlemez.

Loglar hem konsola hem de `logs/app.log` dosyasına (döngüsel, 5 MB × 5)
yazılır; her çalıştırmanın sonunda kaç sembolün başarılı/başarısız olduğu ve
kaç gap doldurulduğu özetlenir.

## Senaryo Üretimi

Her saatlik (1h) mum güncellemesi tamamlandıktan hemen sonra, tüm aktif semboller için
RSI + EMA(9/21) kesişimi + hacim spike'ı sinyalleri kontrol edilir. Üçü birden aynı yönde
tetiklenirse, en yakın destek/direnç seviyelerinden giriş/hedef/stop hesaplanıp `scenarios`
tablosuna `status="pending"` olarak yazılır. Bir sembol için zaten `pending` bir senaryo
varsa, süresi dolana (`expires_at`) veya güncellenene kadar yeni bir tane üretilmez.

Sinyaller yalnızca **kapanmış** mumlar üzerinden değerlendirilir; o an oluşmakta olan
(yarım) mum hesaba katılmaz. Verisi bayat (bu saat çekilememiş), aralıklı (recent
pencerede eksik mum) veya anomali işaretli (`flagged`) olan semboller o çalıştırmada
sessizce atlanır — hata sayılmaz, sonraki saatte tekrar denenir.

## Funding Rate Risk Filtresi

Senaryo üretiminden hemen önce, her saatlik çalıştırmada tüm perpetual futures kontratlarının
güncel funding rate'i tek bir Binance isteğiyle çekilip `funding_rates` tablosuna yazılır
(sembol başına tek satır — yalnızca en son değer tutulur, geçmiş saklanmaz).

Bir sinyal üretildiğinde, sembolün USDT-M perpetual futures kontratı varsa funding rate
kontrol edilir: funding **+%0.05'in üzerindeyse** long sinyalleri, **-%0.05'in altındaysa**
short sinyalleri reddedilir — bu, sinyalin gitmek istediği yönün zaten aşırı kalabalık ve
kaldıraçlı olduğu, yani squeeze riskinin yüksek olduğu anlamına gelir. Futures kontratı
olmayan semboller bu filtreden hiç etkilenmez.

Funding verisi hiç yoksa veya 2 saatten eskiyse, futures kontratı olan semboller için sinyal
üretilmez ("veri güvenilir değilse işlem yapma"). Hangi sembolde hangi kontratın olduğu,
günlük sembol yenileme job'ı tarafından `symbols.has_futures_contract` kolonuna yazılır.

## Öğrenme Döngüsü

Her saatlik işin sonunda, senaryo üretiminin hemen ardından, tüm `pending` senaryolar gerçek
mum verisiyle değerlendirilir: hedefe ulaştıysa `hit_target`, stop'a vurduysa `hit_stop`
(aynı mumda ikisi de gerçekleşmişse stop öncelikli sayılır), süresi dolmuşsa `expired`
olarak işaretlenir. Ardından yön + confidence aralığı desenine göre geçmiş başarı oranı
hesaplanır (`hit_target / (hit_target + hit_stop + expired)`); bir desen için en az 20
çözümlenmiş örnek varsa bu oran, henüz kalibre edilmemiş senaryolara `calibrated_confidence`
olarak yazılır. Yetersiz veri varsa alan **NULL bırakılır** — ham `confidence_score`
kopyalanmaz, çünkü paper açılışı `calibrated_confidence IS NOT NULL` şartı arıyor: kalibre
edilmemiş bir tahminle pozisyon açılmaz. Havuza yalnızca güncel `STRATEGY_VERSION`'ın
`hit_target` / `hit_stop` / `expired` satırları girer; `unresolvable` olanlar bir sonucu
temsil etmediği için dışarıda kalır. `calibrated_confidence` bir kez atanır ve tekrar
üzerine yazılmaz.

## Paper Test Portföyü

Öğrenme döngüsünün hemen ardından, **beklenti kapısını** geçen `pending` senaryolar için simüle
bir paper pozisyon açılır. Eşik isabet oranı değil beklentidir:
`expected_r = p·rr − (1 − p) − maliyet_r` (`p` = `calibrated_confidence`,
`rr` = `|hedef−giriş| / |giriş−stop|`, maliyet iki bacağın komisyon + slippage'ı);
`expected_r > MIN_EXPECTED_R` ise açılır. Senaryonun ayrıca taze olması gerekir —
`created_at` son bir saat içinde olmalı — sabit sermayeli (10000, nominal
bir referans; yalnızca yüzdesel getiri anlamlıdır), sabit-oransal risk (%1) ile boyutlandırılır:
pozisyon büyüklüğü `equity × %1 / |entry - stop|` olarak hesaplanır. Aynı sembolde zaten açık bir
pozisyon varsa, eşzamanlı açık pozisyon sayısı 10'a ulaştıysa ya da açık pozisyonların toplam
notional'ı equity'nin 10 katını aşacaksa yeni pozisyon açılmaz. Bu üç sayım da yalnızca güncel
`STRATEGY_VERSION`'lı açık pozisyonları kapsar. Bir senaryo en fazla bir kez paper pozisyona
dönüşür.

Bir senaryo sonuçlandığında (Öğrenme Döngüsü tarafından), ilişkili paper pozisyon aynı çalıştırmada
kapatılır: `hit_target` → hedef fiyattan, `hit_stop` → stop fiyatından, `expired` → süre dolduğunda
en yakın kapanmış mumun kapanış fiyatından (henüz o mum yoksa pozisyon açık kalır, sonraki
çalıştırmada tekrar denenir). Gerçekleşen kâr/zarar equity'ye eklenir — ayrı bir "hesap" tablosu
yok, her kapanan pozisyon satırı kendi `equity_before`/`equity_after` değerlerini taşır; pozisyon
geçmişinin kendisi equity eğrisidir.

Her kapanışta iki bacağın taker komisyonu (%0.05, `TAKER_FEE_RATE`) kendi işlem
hacmi üzerinden düşülür — kazancı küçültür, zararı büyütür. Maliyetsiz bir paper
portföy kimsenin işleyemeyeceği bir edge raporlar: 90 günlük replay'de komisyon
tek başına +17.7R brüt sonucu -14.5R nete çevirdi.

## Dashboard

Servis çalışırken `http://localhost:8000` (Railway'de kendi public domain'inde, `PORT` ortam
değişkeni Railway tarafından otomatik enjekte edilir) üzerinden tek sayfalık bir dashboard
sunulur: son aktivite zamanı/sağlık durumu, paper portföyün güncel equity'si ve kısa geçmişi,
açık pozisyonlar ve en son üretilen senaryolar. `BASIC_AUTH_USER` / `BASIC_AUTH_PASS_HASH`
ortam değişkenleriyle korunur — parola hash'i `python3 -c "from werkzeug.security import
generate_password_hash; print(generate_password_hash('...', method='pbkdf2:sha256'))"` ile
üretilir, düz metin parola hiçbir yerde saklanmaz. Dashboard verisi her istekte veritabanından
canlı okunur; scheduler'dan bağımsız ayrı bir DB oturumu kullanır, birbirlerini bloklamazlar.

## Test

```bash
pytest -v
```

Tüm birim testler `sqlite:///:memory:` üzerinde çalışır — gerçek bir
PostgreSQL bağlantısı veya Binance API erişimi gerektirmez.

## Strateji Araştırma Tezgahı

Sinyal kurallarını canlıda saat saat beklemek yerine saklanan geçmiş üzerinde
çevrimdışı ölçmek için:

```bash
PYTHONPATH=. python3 scripts/fetch_research_data.py   # 25 likit parite x 90 gun -> data/research.db
PYTHONPATH=. python3 scripts/run_funnel.py            # her gate kac mumu eliyor
PYTHONPATH=. python3 scripts/run_backtest.py          # kural basina win% / R / komisyon / net
```

Replay, production'ın kendi saf fonksiyonlarını (`evaluate_signal`,
`build_scenario`, `evaluate_outcome`, `_window_rejection`) çağırır — yeniden
yazılmış bir kopyayı değil — ve `has_pending_scenario` ile aynı "yön başına tek
canlı senaryo" kilidini uygular. Süresi verinin bittiği yere taşan senaryolar
zarar olarak yazılmaz, skorsuz bırakılır.

Sonuçlar R cinsinden, yani işlemin kendi riskine bölünmüş olarak raporlanır.
Dikkat: stop mesafesi sıfıra yaklaşınca hem R hem komisyon-R patlar, o
işlemlerin ölçümü anlamsızdır — `min_stop_pct` filtresi bunun içindir.
