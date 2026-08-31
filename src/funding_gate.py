from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import FundingRate, Symbol

# Binance funding normally sits within +/-0.01%-0.05% per 8-hour interval.
# Beyond this, the crowded side is paying to stay in - the squeeze setup this
# gate exists to stay out of.
FUNDING_RATE_THRESHOLD = Decimal("0.0005")

# One failed hourly refresh is tolerable; a persistently stale rate is not, so
# past this age the gate blocks rather than trade on a number of unknown age.
FUNDING_DATA_MAX_AGE = timedelta(hours=2)


def funding_rejection(session, symbol: str, direction: str, now: datetime) -> str | None:
    """Why this signal must not become a scenario, or None if it may.

    `scenario_runner._window_rejection` ile AYNI sozlesme: engelleme sebebini
    aciklayan bir string, ya da engellenmiyorsa None. Bool yerine sebep
    dondurmek bilincli - sessiz engelleme loglarda "sinyal yok"tan ayirt
    edilemiyor (BTC rejim filtresinin incelemesinde cikan bulgu).

    SIRAYLA:
      1. Symbol satiri yoksa ya da has_futures_contract True DEGILSE
         (False veya NULL) -> None. Futures piyasasi yoksa okunacak funding
         de yoktur; o semboller icin davranis bit duzeyinde degismez.
      2. FundingRate satiri yoksa -> engelle (sebep string'i dondur).
      3. now - fetched_at > FUNDING_DATA_MAX_AGE ise -> engelle.
      4. direction == "long" ve rate > FUNDING_RATE_THRESHOLD -> engelle.
      5. direction == "short" ve rate < -FUNDING_RATE_THRESHOLD -> engelle.
      6. Aksi halde None.

    Karsilastirmalar KESIN (> / <): tam esik degerinde engellenmez.
    """
    symbol_row = session.get(Symbol, symbol)
    if not symbol_row or not symbol_row.has_futures_contract:
        return None

    funding_row = session.get(FundingRate, symbol)
    if not funding_row:
        return "no funding data yet for a symbol that has a futures contract"

    if now - funding_row.fetched_at > FUNDING_DATA_MAX_AGE:
        return "stale funding data: fetched at %s (%s old)" % (
            funding_row.fetched_at, now - funding_row.fetched_at)

    rate = funding_row.funding_rate
    if direction == "long" and rate > FUNDING_RATE_THRESHOLD:
        return "funding rate %s is above +%s - longs already crowded" % (
            rate, FUNDING_RATE_THRESHOLD)
    elif direction == "short" and rate < -FUNDING_RATE_THRESHOLD:
        return "funding rate %s is below -%s - shorts already crowded" % (
            rate, FUNDING_RATE_THRESHOLD)

    return None
