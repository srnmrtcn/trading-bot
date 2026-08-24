# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

This repository is currently empty — no code has been added yet. This file is a placeholder scaffold to be filled in once the project takes shape.

## To fill in once code exists

- **Commands**: build, lint, test (including how to run a single test), and run/dev commands.
- **Architecture**: language/framework choice, how the trading bot is structured (e.g. data ingestion, strategy/signal logic, order execution, exchange API integration, backtesting vs. live trading), and how these pieces fit together.
- **Configuration**: where API keys/secrets are expected to be configured (e.g. env vars, config files) — do not hardcode them in code or commit them.
- Sadece Türkçe cevap ver

## Çalışma kuralları

- Mümkün olan her anda ilgili skill'leri kullan (örn. brainstorming, systematic-debugging, code-review vb.) — elle iş yapmadan önce uygun bir skill olup olmadığını kontrol et.
- Her zaman Türkçe konuş.
- Kod satırlarında sade davran: gereksiz soyutlama, fazladan yorum veya ihtiyaç olmayan karmaşıklık ekleme.
- Her büyük task'ten sonra sıklıkla code review yap (code-review skill'i ile).
- Kullanıcı onaylayıp test edilmiş her değişiklikten sonra main branch'e GitHub'a push et — Railway otomatik rebuild/deploy ediyor.
