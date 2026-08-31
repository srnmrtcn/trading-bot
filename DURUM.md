# DURUM — trading-bot

_2026-08-31 02:43 UTC · `durum_yaz.py` uretti, elle duzenleme_

## Ozet

- testler: **313 passed in 3.58s**
- calisma agaci: 1 dosya degismis
- modul: 29 bitmis, 0 eksik

## Bitmis moduller

`backfill.py`, `btc_regime.py`, `confidence_calibrator.py`, `config.py`, `dashboard_data.py`, `fetch_log.py`, `funding_collector.py`, `funding_gate.py`, `indicators.py`, `integrity.py`, `kline_fetcher.py`, `learning_runner.py`, `main.py`, `outcome_evaluator.py`, `paper_equity.py`, `paper_position_closer.py`, `paper_position_opener.py`, `paper_sizer.py`, `paper_trading_runner.py`, `scenario_builder.py`, `scenario_runner.py`, `scenario_signal.py`, `scenario_storage.py`, `scheduler.py`, `storage.py`, `support_resistance.py`, `symbol_registry.py`, `timeutil.py`, `web.py`

## Son commit'ler

```
5409277 chore: declare requests explicitly - rate_limit now imports it at module level on the boot path
dee4e64 fix: pass the job's end time to scenario generation so all four hourly steps share one now
81bb39c fix: calibrate only pending scenarios so a resolved row is never scored by its own outcome
f7faf39 fix: handle SIGTERM so a redeploy waits for the in-flight job instead of killing it mid-commit
746cfda fix: cap Retry-After, fail fast on 418, retry transient network errors a bounded number of times
d20f61b fix: do not ping Binance inside BinanceClient() so a boot-time outage cannot crash-loop the service
be737cc test: Faz 0 kirmizi testleri - ping=False, retry politikasi, SIGTERM, kalibrasyon hedefi, tek now
5ddc47f docs: kod tabani denetimi, iyilestirme tasarimi ve Faz 0 uygulama plani
```
