# DURUM — trading-bot

_2026-08-31 01:08 UTC · `durum_yaz.py` uretti, elle duzenleme_

## Ozet

- testler: **33 failed, 269 passed in 5.31s**
- calisma agaci: 1 dosya degismis
- modul: 27 bitmis, 2 eksik

## Eksik moduller (siradaki isler burada)

| modul | kalan fonksiyon |
|---|---|
| `src/funding_collector.py` | `refresh_funding_rates()` |
| `src/funding_gate.py` | `funding_rejection()` |

## Dusen test dosyalari

- `tests/test_binance_client.py` — 3 test
- `tests/test_funding_collector.py` — 5 test
- `tests/test_funding_gate.py` — 12 test
- `tests/test_scenario_runner.py` — 1 test
- `tests/test_scheduler.py` — 10 test
- `tests/test_symbol_registry.py` — 2 test

## Bitmis moduller

`backfill.py`, `btc_regime.py`, `confidence_calibrator.py`, `config.py`, `dashboard_data.py`, `fetch_log.py`, `indicators.py`, `integrity.py`, `kline_fetcher.py`, `learning_runner.py`, `main.py`, `outcome_evaluator.py`, `paper_equity.py`, `paper_position_closer.py`, `paper_position_opener.py`, `paper_sizer.py`, `paper_trading_runner.py`, `scenario_builder.py`, `scenario_runner.py`, `scenario_signal.py`, `scenario_storage.py`, `scheduler.py`, `storage.py`, `support_resistance.py`, `symbol_registry.py`, `timeutil.py`, `web.py`

## Son isler

| is | sonuc | kapi / not |
|---|---|---|
| trading-bot-funding-1 | 0 gecti, 0 kaldi |  |
| duman-qwen-1 | 1 gecti, 0 kaldi |  |
| bakim-14 | 1 gecti, 0 kaldi |  |
| bakim-13 | 0 gecti, 1 kaldi | `?` — Traceback (most recent call last): File "C:\ajan\onmuhasebe\bakim.py",... |
| onmuhasebe-12 | 4 gecti, 0 kaldi |  |

## Son commit'ler

```
0df6dc2 chore: yerel qwen ajani icin QWEN.md, inceleyici ve .qwen ayarlari
c69cb34 test: funding rate gate icin kirmizi testler ve iki stub modul
dfd0445 feat: report a confidence interval with every out-of-sample result
1d9c832 perf: short-circuit the shipped rule on its own RSI precondition
4e4a6a4 refactor: one scenario walk shared by the backtest and walk-forward runner
b128558 feat: add a train/test walk-forward runner and widen the research universe
b89015c perf: compute RSI once per symbol in the replay, short-circuit the EMA gates
0a8aa29 docs: replace the placeholder CLAUDE.md/AGENTS.md with the real thing
```
