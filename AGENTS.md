# AGENTS.md

This file provides guidance to coding agents working in this repository.

## Çalışma kuralları

- **Her zaman Türkçe konuş.**
- Mümkün olan her anda ilgili skill'leri kullan (brainstorming, systematic-debugging, test-driven-development, code-review vb.) — elle iş yapmadan önce uygun bir skill olup olmadığını kontrol et.
- Kod satırlarında sade davran: gereksiz soyutlama, fazladan yorum veya ihtiyaç olmayan karmaşıklık ekleme.
- Her büyük task'ten sonra code review yap (code-review skill'i ile).
- Kullanıcı onaylayıp test edilmiş her değişiklikten sonra main branch'e GitHub'a push et — Railway otomatik rebuild/deploy ediyor.

## Komutlar

```bash
python3 -m pytest -q                  # tüm testler (~270, sqlite:///:memory:, ağ gerektirmez)
python3 -m pytest tests/test_x.py -q -k isim_parcasi   # tek test
PYTHONPATH=. python3 -m src.main      # servisi çalıştır (DATABASE_URL + BASIC_AUTH_* gerekir)
```

Scriptler `PYTHONPATH=.` ister; testler `pytest.ini`'deki `pythonpath = .` sayesinde istemez.

## Mimari

Saatlik bir APScheduler job'ı tek bir süreçte her şeyi sırayla çalıştırır; Flask dashboard aynı süreçte kendi DB oturumuyla servis edilir.

```
kline_fetcher → scenario_runner → learning_runner → paper_trading_runner
  (A: veri)      (B: sinyal)       (C: öğrenme)      (D: paper portföy)
```

- **A — Veri** (`binance_client`, `kline_fetcher`, `storage`, `backfill`, `integrity`): 484 aktif USDT paritesi için 1h/1d mum. Devam noktası `MAX(klines.open_time)`'dan gelir, `fetch_log`'dan **değil** (o sadece denetim içindir). Doldurulamayan gap'ler kalıcıdır ve tolere edilir.
- **B — Sinyal** (`scenario_signal`, `scenario_builder`, `btc_regime`, `support_resistance`): RSI + EMA(9/21) + hacim confluence, BTC günlük rejim kapısıyla. Yalnızca **kapanmış** mumlar okunur.
- **C — Öğrenme** (`outcome_evaluator`, `confidence_calibrator`): senaryoları gerçek mumlarla çözer, yön+confidence kovası başına başarı oranı hesaplar. Kalibrasyon **çözümlemeden önce** çalışır ki bir senaryo kendi sonucuyla puanlanmasın.
- **D — Paper** (`paper_sizer`, `paper_position_opener/closer`, `paper_equity`): sabit-oransal risk. Ayrı hesap tablosu yok — her kapanan pozisyon kendi `equity_before/after`'ını taşır, pozisyon geçmişi equity eğrisidir.

**İzolasyon politikası:** hiçbir sembolün, hiçbir aşamanın hatası diğerlerini durdurmaz. Her aşama kendi try/except'ini taşır ve `session.rollback()` yapar. Bunu zayıflatma.

**Zaman:** her yerde naive UTC (`timeutil.utc_now`). DB kolonları da naive; tz-aware bir değer PostgreSQL'de kayar.

## Şema değişiklikleri

Alembic **yok**. `db/session.sync_missing_columns` her açılışta modeldeki eksik kolonları canlı DB'ye ekler — ama **yalnızca nullable** olanları. `nullable=False` bir kolon eklersen atlanır ve o tabloya yapılan **her** sorgu prod'da patlar. Yeni kolon = `nullable=True`, istisnasız.

## Strateji araştırma tezgahı

`src/research/funnel.py` + `scripts/` — sinyal kurallarını saklanan geçmiş üzerinde çevrimdışı ölçer (README'de kullanım). Üretimin kendi saf fonksiyonlarını çağırır, kopyalarını değil; yeni bir kapı/filtre eklersen replay'e de eklemen gerekir yoksa iki taraf farklı stratejiyi ölçer.

**Ölçülmüş durum (2026-08-26):** mevcut sinyal kuralı 90 günde 25 sembolde ~4 sinyal üretiyor ve hiçbir varyantın komisyon sonrası pozitif beklentisi yok. Parametre seçerken tek bir taramadan en iyi hücreyi almak veriye uydurmaktır — train/test ayrımı olmadan sonuçlara güvenme.

## Yapılandırma

`DATABASE_URL`, `BASIC_AUTH_USER`, `BASIC_AUTH_PASS_HASH` (pbkdf2), `PORT` (Railway enjekte eder). Sırları koda yazma, commit'leme.
