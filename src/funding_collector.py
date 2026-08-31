from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from src.db.models import FundingRate, Symbol, FundingRateHistory
from src.timeutil import utc_now

logger = logging.getLogger("funding_collector")


@dataclass
class FundingRefreshResult:
    updated: int
    missing: int


def refresh_funding_rates(session, binance_client, now: datetime = None) -> FundingRefreshResult:
    """Store the latest funding rate for every symbol that has a futures contract.

    ADIMLAR:
      1. now verilmemisse utc_now() kullan.
      2. binance_client.get_funding_rates() -> {symbol: Decimal}. TEK cagri;
         bulk endpoint tum tahtayi donduruyor, sembol basina cagri YAPILMAZ.
      3. Symbol tablosundan has_futures_contract == True olan sembolleri oku.
      4. Her biri icin: feed'de yoksa missing += 1 ve mevcut satira DOKUNMA
         (kontrat delist olmus olabilir; bayatlik kontrolu gate'in isi).
         Varsa FundingRate satirini upsert et (yeni satir ekle ya da
         funding_rate + fetched_at guncelle), updated += 1.
      5. session.commit().
      6. rates tamamen bossa logger.warning ile "no funding rates" gecen bir
         uyari yaz; her calistirmada logger.info ile updated/missing ozeti yaz.
      7. FundingRefreshResult dondur.

    Hatalar YUKARI YAYILIR: cagiran (saatlik job) bu adimi kendi try/except'i
    ile izole ediyor. Burada yutmak, bir feed kesintisini "her sey yolunda"
    ozetinin arkasina saklardi - BTC rejim filtresinin incelemesinde cikan
    korlugun ta kendisi.
    """
    if now is None:
        now = utc_now()

    rates = binance_client.get_funding_rates()

    symbols = session.query(Symbol).filter(Symbol.has_futures_contract == True).all()
    updated = 0
    missing = 0

    for symbol in symbols:
        rate = rates.get(symbol.symbol)
        funding_rate_row = session.get(FundingRate, symbol.symbol)
        if rate is None:
            missing += 1
        else:
            if funding_rate_row is None:
                session.add(FundingRate(
                    symbol=symbol.symbol,
                    funding_rate=rate,
                    fetched_at=now,
                ))
            else:
                funding_rate_row.funding_rate = rate
                funding_rate_row.fetched_at = now
            updated += 1

    session.commit()

    if not rates:
        logger.warning("Binance returned no funding rates at all")

    logger.info("Funding rates refreshed: %d updated, %d missing from the feed", updated, missing)
    return FundingRefreshResult(updated=updated, missing=missing)


def record_funding_event(session, symbol: str, funding_time: datetime, funding_rate: Decimal, mark_price: Decimal) -> bool:
    """
    Ayni (symbol, funding_time) icin FundingRateHistory satiri zaten varsa hicbir sey yapmaz ve False doner. Yoksa satiri session'a EKLER ve True doner. COMMIT ETMEZ - cagiran topluca commit eder.
    """
    raise NotImplementedError


def funding_events_between(session, symbol: str, start: datetime, end: datetime) -> list:
    """
    Verilen sembol icin start < funding_time <= end araligindaki FundingRateHistory satirlarini funding_time'a gore ARTAN sirada dondurur; her eleman (funding_time, funding_rate, mark_price) uclusu. Sinirlar bilincli asimetrik: acilis anindaki olay o pozisyonun degil, kapanis anindaki olay onundur. Kayit yoksa bos liste. Baska sembolun satirlari sayilmaz. Yalnizca okur.
    """
    raise NotImplementedError


def refresh_funding_history(session, binance_client, now: datetime = None) -> int:
    """
    now verilmezse utc_now(). binance_client.get_funding_events() cagrilir; donen sozluk {symbol: (funding_time, funding_rate, mark_price)}. Symbol tablosunda has_futures_contract == True olan her sembol icin feed'deki kayit record_funding_event ile yazilir. Feed'de olmayan sembol atlanir. funding_time ya da mark_price None olan kayit atlanir. Sonda session.commit(); YENI yazilan satir sayisi doner. Hatalar YUKARI YAYILIR - cagiran saatlik job kendi try/except'i ile izole eder.
    """
    raise NotImplementedError
