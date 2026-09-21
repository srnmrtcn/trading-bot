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


def upsert_funding_event(session, symbol: str, funding_time: datetime,
                         funding_rate: Decimal, mark_price: Decimal) -> bool:
    """Bir settlement olayini yaz ya da DUZELT. COMMIT ETMEZ.

    Duzeltebilmesi sart. Onceki surum var olan satiri gorup dokunmadan
    donuyordu; yanlis oranla erkenden yazilmis bir satir bu yuzden kalici
    hale geliyordu. Ayni (symbol, funding_time) icin farkli bir oran gelmesi
    normaldir -- veri kaynagi duzelmis ya da eksik bir kayit geri doldurulmus
    olabilir -- ve son soz her zaman borsanin gerceklesmis kaydinindir.

    Degisiklik yazildiysa True doner (yeni satir ya da duzeltme), satir zaten
    aynıysa False.
    """
    existing = session.query(FundingRateHistory).filter_by(
        symbol=symbol, funding_time=funding_time).first()
    if existing is not None:
        if (existing.funding_rate == funding_rate
                and existing.mark_price == mark_price):
            return False
        existing.funding_rate = funding_rate
        existing.mark_price = mark_price
        return True

    session.add(FundingRateHistory(
        symbol=symbol,
        funding_time=funding_time,
        funding_rate=funding_rate,
        mark_price=mark_price,
    ))
    return True


def funding_events_between(session, symbol: str, start: datetime, end: datetime) -> list:
    """
    Verilen sembol icin start < funding_time <= end araligindaki FundingRateHistory satirlarini funding_time'a gore ARTAN sirada dondurur; her eleman (funding_time, funding_rate, mark_price) uclusu. Sinirlar bilincli asimetrik: acilis anindaki olay o pozisyonun degil, kapanis anindaki olay onundur. Kayit yoksa bos liste. Baska sembolun satirlari sayilmaz. Yalnizca okur.
    """
    events = session.query(FundingRateHistory).filter(
        FundingRateHistory.symbol == symbol,
        FundingRateHistory.funding_time > start,
        FundingRateHistory.funding_time <= end
    ).order_by(FundingRateHistory.funding_time.asc()).all()
    
    return [(event.funding_time, event.funding_rate, event.mark_price) for event in events]


def refresh_funding_history(session, binance_client, now: datetime = None) -> int:
    """Gerceklesmis funding olaylarini tahtadan alip kaydeder.

    binance_client.get_funding_history() TEK istekte butun tahtanin son
    settlement'larini donduruyor. Futures kontrati olan sembollerin kayitlari
    upsert edilir; tahtada olup bizde olmayan sembol atlanir.

    Yazilan/duzeltilen satir sayisi doner. Hatalar YUKARI YAYILIR - cagiran
    saatlik job kendi try/except'i ile izole eder.

    Kacan olay kalici olur: bu cagri yalnizca son birkac saatlik pencereyi
    gorur, saatlik is bir kez atlanirsa o saatin olaylari bir daha gelmez.
    Sembol bazli geri doldurma `scripts/repair_funding_history.py` isidir --
    A altsisteminin doldurulamayan mum bosluklarini tolere etmesiyle ayni
    politika.

    `now` kullanilmiyor: zamani artik borsa soyluyor, biz degil. Imzada
    duruyor cunku saatlik isin butun adimlari ayni imzayi tasiyor ve zamanin
    disaridan verilebilmesi testlerin tek tutamagi.
    """
    events = binance_client.get_funding_history()

    bizim = {
        row.symbol for row in
        session.query(Symbol.symbol).filter(Symbol.has_futures_contract == True).all()
    }

    yazilan = 0
    for symbol, funding_time, funding_rate, mark_price in events:
        if symbol not in bizim:
            continue
        if funding_time is None or funding_rate is None or mark_price is None:
            continue
        if upsert_funding_event(session, symbol, funding_time, funding_rate, mark_price):
            yazilan += 1

    session.commit()
    logger.info("Funding history refreshed: %d events written or corrected "
                "from %d board rows", yazilan, len(events))
    return yazilan
