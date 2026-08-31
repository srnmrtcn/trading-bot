# Kripto Analiz Ofisi — çalışma kuralları

Binance verisi çeken, teknik sinyalden senaryo üreten, sonuçları öğrenen ve
kâğıt üstünde portföy taşıyan bir arka plan servisi. **Testler
şartnamedir:** `tests/` altındaki her dosya, kodun ne yapması gerektiğinin
tek doğru tanımıdır.

## Mutlak kurallar

1. **`tests/`, `conftest.py`, `pytest.ini`, `QWEN.md`, `.qwen/` — asla
   yazma.** Testi geçmek yerine testi değiştirmek başarısızlıktır. Dışarıda
   bir `git diff` kapısı bunu her seferinde yakalar ve yaptığın tüm işi
   siler.
2. **Görevde sana açıkça listelenmemiş hiçbir dosyayı değiştirme veya
   oluşturma.** Yeni yardımcı modül gerektiğini düşünüyorsan yapma; görev
   yanlış tasarlanmış demektir, sebebini yaz ve dur.
3. **`git` komutu çalıştırma.** Ne commit, ne checkout, ne stash.
4. **Mevcut imzaları, dataclass alanlarını ve modül seviyesi sabitleri
   değiştirme.** Testler onlara göre yazıldı.
5. **Yeni bağımlılık ekleme.** `requirements.txt` sabittir.

## Para ve sayı

**`float` kullanılmaz.** Fiyat, hacim, funding oranı, PnL — hepsi
`Decimal`. Dışarıdan gelen ham değer her zaman `Decimal(str(deger))` ile
çevrilir; `Decimal(float)` hassasiyeti bozar ve fark birikir.

## Zaman

Her yerde **naive UTC** (`src/timeutil.py` → `utc_now()`). DB kolonları da
naive; tz-aware bir değer PostgreSQL'de kayar. Yeni bir zaman değeri
üretiyorsan `utc_now()` kullan, `datetime.now()` değil.

## Şema

Alembic **yok**. `src/db/session.py` içindeki `sync_missing_columns` her
açılışta modeldeki eksik kolonları canlı veritabanına ekler — ama
**yalnızca `nullable=True` olanları**. `nullable=False` bir kolon eklersen
sessizce atlanır ve o tabloya yapılan **her** sorgu prod'da patlar. Yeni
kolon = `nullable=True`, istisnasız.

## İzolasyon politikası — zayıflatma

Hiçbir sembolün, hiçbir aşamanın hatası diğerlerini durdurmaz. Saatlik
job'daki her adım kendi `try/except`'ini taşır, hatayı
`logger.exception` ile yazar ve gerekiyorsa `session.rollback()` yapar.
Var olan bir `try/except` bloğunu kaldırma, daraltma veya birleştirme.

## Sessiz engelleme yasak

Bir sinyal/senaryo engellendiğinde sebebi loglanır. Engelleme fonksiyonları
`bool` değil **sebep string'i ya da `None`** döndürür (`_window_rejection`
deseni). Loglanmayan bir engelleme, loglarda "sinyal yok"tan ayırt
edilemez ve sistemi kör eder.

## Kod stili

- Kod, ad ve yorumlar **İngilizce** — mevcut dosyalardaki dili örnek al.
- `from __future__ import annotations` en üstte, tip ipuçları kullanılıyor.
- Girinti 4 boşluk, satır ~100 karakter.
- Yorum, "ne" değil "neden" anlatır ve sadece şaşırtıcı olan yere konur.
- Gereksiz soyutlama ekleme; mevcut desenin dışına çıkma.

## Import düzeni

`pytest.ini` içinde `pythonpath = .` var. Modüller **`from src.x import y`**
şeklinde import edilir (`from x import y` değil). Import bloğu alfabetiktir.

## Düzenleme yöntemi

Dosya ~250 satırdan kısaysa: `read_file` ile **tamamını oku** → değişikliği
kafanda yap → `write_file` ile **tamamını yaz**.

Sebep: `replace` aracı `old_string`'in birebir eşleşmesini ister — boşluk,
girinti, satır sonu dahil. Eşleşmeyince model aynı çağrıyı biraz
değiştirip tekrar dener ve döngüye girer. Bir koşu tam olarak böyle
düştü: 587 saniye, 17 tur, 421 bin token, sıfır satır yazıldı.

Aynı dosyaya üst üste `replace` denemesi yapma; ikinci deneme başarısızsa
tam dosya yaz. Bu depodaki dosyalar bu sınırın altında.

## Çalışma şekli

1. Önce görevde adı geçen **test dosyasını oku**. Şartname odur.
2. Değiştireceğin kaynak dosyanın tamamını oku; docstring'de adım adım
   tarif varsa harfiyen uygula.
3. Kodu yaz.
4. Görevdeki `verify` komutunu **kendin çalıştır**, geçene kadar düzelt.
5. Geçtiğinde **dur**. Ekstra iyileştirme, yeniden düzenleme, yorum
   temizliği yapma — kapsam dışı her değişiklik reddedilme sebebidir.

`-k` filtresi kullanma: hiçbir teste uymayan bir filtre pytest'e sıfır
döndürtür ve hiçbir şey yapmadan "geçti" görünürsün.
