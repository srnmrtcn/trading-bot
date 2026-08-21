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
   shell profilinden `export DATABASE_URL=...`).

## Çalıştırma

```bash
python -m src.main
```

İlk çalıştırmada: sembol listesi çekilir, henüz hiç mumu olmayan her
sembol/timeframe çifti için son 2 yıllık geçmiş veri (backfill) indirilir,
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
varsa, süresi dolana veya güncellenene kadar yeni bir tane üretilmez.

## Test

```bash
pytest -v
```

Tüm birim testler `sqlite:///:memory:` üzerinde çalışır — gerçek bir
PostgreSQL bağlantısı veya Binance API erişimi gerektirmez.
