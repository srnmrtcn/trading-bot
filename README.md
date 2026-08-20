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

İlk çalıştırmada: sembol listesi çekilir, `klines` tablosu boşsa son 2 yıllık
geçmiş veri (backfill) indirilir, ardından zamanlayıcı devreye girer (1h
mumlar için saatlik, 1d mumlar için günlük, sembol listesi için günlük).
Servis `Ctrl+C` ile durdurulabilir; yeniden başlatıldığında `fetch_log`
tablosuna bakarak kaldığı yerden devam eder.

## Test

```bash
pytest -v
```

Tüm birim testler `sqlite:///:memory:` üzerinde çalışır — gerçek bir
PostgreSQL bağlantısı veya Binance API erişimi gerektirmez.
