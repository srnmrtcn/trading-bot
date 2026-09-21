"""Bozuk funding gecmisini borsanin gerceklesmis kayitlariyla onarir.

NEDEN. Toplayicinin onceki surumu premiumIndex'ten `lastFundingRate` okuyup
`nextFundingTime` ile esliyordu. Oran settle olmamis bir tahmindi, zaman ise
GELECEKTEKI bir settlement'in zamani. Kayit (symbol, funding_time) ile
tekillestirildigi ve var olan satira dokunulmadigi icin de hata kaliciydi:
her olay bir onceki donemin oraniyla -- ya da hic gerceklesmemis bir oranla --
saklandi. Toplayici duzeltildi ama diskteki satirlar kendiliginden duzelmiyor;
saatlik toplama yalnizca son birkac saati goruyor.

NE YAPAR. Gecmisi olan her sembol icin `GET /fapi/v1/fundingRate` ile
gerceklesmis kayitlari ceker ve:
  * saklanan satiri gercek oranla DUZELTIR,
  * karsiligi hic olmayan satiri SILER (gelecege yazilmis, yasanmamis
    settlement),
  * bizde olmayan gercek olayi EKLER.

CALISTIRMA. Uretim veritabani yalnizca Railway agi icinden erisilebilir:

    railway ssh -p <proje> -s trading-bot -e production -- \
        python scripts/repair_funding_history.py

Yerelde bir kopya uzerinde denemek icin DATABASE_URL vermek yeterli.
--kuru-calistir hicbir sey yazmaz, ne yapacagini raporlar. Silme ayrica
--sil ister: karsiligi bulunamayan satir, gercekten yasanmamis bir
settlement de olabilir, basarisiz bir sorgunun sonucu da. Once kuru kosu.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from datetime import timezone

from src.binance_client import BinanceClient
from src.db.models import FundingRateHistory
from src.db.session import make_engine, make_session_factory
from src.funding_collector import upsert_funding_event

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("repair_funding")

MS_PER_SECOND = 1000

# Bir sembolun saklanan satirlarinin bu kadarindan fazlasi silinecekse o
# sembol atlanir. Silme kararı "borsada karsiligi yok" sinyaline dayaniyor ve
# o sinyal API tarafindan yanlis verilebilir: bos yanit, kisa sayfa, gecici
# bosluk. Birkac hayalet satir beklenir; yarisi beklenmez. Boyle bir sonuc
# verinin degil sorgunun bozuk oldugunu soyler.
SILME_TAVANI = 0.10


def _to_ms(dt) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * MS_PER_SECOND)


def gercek_olaylar(client, symbol: str, start_ms: int) -> dict:
    """{funding_time: (oran, mark)} -- start_ms'ten itibaren tum gerceklesmisler."""
    bulunan = {}
    imlec = start_ms
    while True:
        batch = client.get_funding_history(symbol=symbol, start_ms=imlec, limit=1000)
        if not batch:
            break
        for _, when, rate, mark in batch:
            bulunan[when] = (rate, mark)
        son = max(when for _, when, _, _ in batch)
        yeni_imlec = _to_ms(son) + 1
        # `len(batch) < 1000` ile "son sayfa" cikarimi yapilmiyor: batch
        # SUZULMUS liste, eksik alanli tek bir satir bile elenirse 999 doner
        # ve sayfalama erken kirilir. Kirilan yerden sonraki gercek olaylar
        # sozluge hic girmez, silme mantigi da onlari "yasanmadi" sayardi.
        # Imlec zamanla ilerliyor; bos yanit ya da ilerlemeyen imlec durdurur.
        if yeni_imlec <= imlec:
            break
        imlec = yeni_imlec
    return bulunan


def onar(session, client, kuru: bool = False, sil: bool = False) -> dict:
    semboller = [row[0] for row in session.query(FundingRateHistory.symbol)
                 .distinct().order_by(FundingRateHistory.symbol.asc()).all()]
    sayac = {"sembol": 0, "duzeltilen": 0, "silinen": 0, "hayalet": 0,
             "eklenen": 0, "dokunulmayan": 0, "atlanan": 0}

    for symbol in semboller:
        satirlar = (session.query(FundingRateHistory)
                    .filter(FundingRateHistory.symbol == symbol)
                    .order_by(FundingRateHistory.funding_time.asc()).all())
        if not satirlar:
            continue
        sayac["sembol"] += 1
        try:
            gercek = gercek_olaylar(client, symbol, _to_ms(satirlar[0].funding_time))
        except Exception:
            logger.exception("%s: gecmis cekilemedi, atlaniyor", symbol)
            session.rollback()
            continue

        # Bos yanit exception degildir. Karsilik bulunamamasi burada "bu
        # settlement yasanmadi" demek; hicbiri bulunamadiysa soylenen sey
        # "bu sembolun gecmisi hic yasanmadi" olur ki bu bir veri bulgusu
        # degil, basarisiz bir sorgudur.
        if not gercek:
            logger.warning("%s: borsa hic kayit dondurmedi, atlaniyor "
                           "(%d satir dokunulmadan kaldi)", symbol, len(satirlar))
            sayac["atlanan"] += 1
            session.rollback()
            continue

        hayalet = [satir for satir in satirlar
                   if gercek.get(satir.funding_time) is None]
        if hayalet and len(hayalet) > SILME_TAVANI * len(satirlar):
            logger.warning("%s: %d/%d satirin karsiligi yok -- tavanin (%d%%) "
                           "ustunde, sembol atlaniyor", symbol, len(hayalet),
                           len(satirlar), int(SILME_TAVANI * 100))
            sayac["atlanan"] += 1
            session.rollback()
            continue

        for satir in satirlar:
            esles = gercek.get(satir.funding_time)
            if esles is None:
                # Bu settlement hic yasanmadi: satir gelecege yazilmisti.
                # Silme AYRI bir bayrak ister: geri donusu yok ve
                # `funding_rate_history` tek kaynak. Bayraksiz yalnizca sayilir.
                sayac["silinen" if sil else "hayalet"] += 1
                if sil and not kuru:
                    session.delete(satir)
                continue
            oran, mark = esles
            if satir.funding_rate == oran and satir.mark_price == mark:
                sayac["dokunulmayan"] += 1
                continue
            sayac["duzeltilen"] += 1
            if not kuru:
                satir.funding_rate = oran
                satir.mark_price = mark

        saklanan = {satir.funding_time for satir in satirlar}
        for when, (oran, mark) in gercek.items():
            if when in saklanan:
                continue
            sayac["eklenen"] += 1
            if not kuru:
                upsert_funding_event(session, symbol, when, oran, mark)

        if kuru:
            session.rollback()
        else:
            session.commit()

    return sayac


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kuru-calistir", action="store_true",
                    help="hicbir sey yazma, ne yapacagini raporla")
    ap.add_argument("--sil", action="store_true",
                    help="karsiligi olmayan satirlari SIL (geri donusu yok). "
                         "Bayraksiz yalnizca sayilir -- once --kuru-calistir "
                         "ile sayilara bak.")
    a = ap.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL yok. Uretim veritabanina yalnizca Railway "
                     "agi icinden erisilebilir:\n"
                     "  railway ssh -s trading-bot -e production -- "
                     "python scripts/repair_funding_history.py")
        return 1
    session = make_session_factory(make_engine(database_url))()
    try:
        sayac = onar(session, BinanceClient(), kuru=a.kuru_calistir, sil=a.sil)
    finally:
        session.close()

    logger.info(
        "Onarim %s: %d sembol | %d duzeltildi, %d eklendi, %d zaten dogru, "
        "%d karsiliksiz (%s), %d sembol atlandi",
        "PROVASI" if a.kuru_calistir else "tamam",
        sayac["sembol"], sayac["duzeltilen"], sayac["eklenen"],
        sayac["dokunulmayan"],
        sayac["silinen"] if a.sil else sayac["hayalet"],
        "silindi" if a.sil else "sayildi, --sil verilmedi",
        sayac["atlanan"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
