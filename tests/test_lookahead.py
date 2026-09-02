"""Gelecek sizmasi testleri.

Bir stratejinin kendini kazaniyor sanmasinin en sessiz yolu, secim aninda
bilemeyecegi bir seyi bilmesidir. Bu dosya o bilginin uretim yoluna girip
girmedigini DAVRANISLA olcuyor: gelecege ait mumlar veriliyor ve defterin
degismedigi dogrulaniyor.

IKI AYRI KAPI VAR ve her biri ayri test edilmeli:

  1. load_daily_bars  -- veritabanindan `open_time < bugun` disini hic almaz.
  2. book_for/is_liquid -- eline gelecege ait mum GECSE BILE onu kullanmaz,
     cunku her sey as_of gunune gore anahtarlanir.

Ilk yazdigim halde ikinci kapinin testleri barlari veritabanina koyuyordu, yani
birinci kapi onlari zaten suzuyordu: is_liquid'in ust sinirini ve giris fiyati
secimini BILEREK bozdum, dordu de yesil kaldi. Test tiyatrosuydu. Simdi ikinci
kapinin testleri barlari dogrudan book_for'a veriyor ve ayni bozmalar kirmizi
oluyor.
"""
from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import FuturesDailyKline
from src.integrity import floor_to_timeframe
from src.portfolio.config import (
    LIQUIDITY_WINDOW_DAYS,
    LOOKBACK_DAYS,
    MIN_DOLLAR_VOLUME,
    MIN_UNIVERSE,
    SIGNAL_SKIP_DAYS,
    TOP_FRACTION,
)
from src.portfolio.rebalancer import load_daily_bars
from src.portfolio.selection import book_for

BASLANGIC = datetime(2026, 1, 1)
GUN_SAYISI = 70
SIMDI = BASLANGIC + timedelta(days=GUN_SAYISI, hours=1)   # gun ici bir an
SINIR = floor_to_timeframe(SIMDI, "1d")
AS_OF = (SINIR - timedelta(days=1)).date()
TARIH = 100
BOL_HACIM = Decimal(60000000)


def _mum(gun, kapanis, dolar=BOL_HACIM):
    kapanis = Decimal(str(kapanis))
    return {"open_time": gun, "close": kapanis, "volume": dolar / kapanis}


def _evren_barlari(egim_baslangic=0.010, egim_adim=0.0008, adet=20):
    """{sembol: mum listesi} - egimler farkli, siralama belirgin."""
    bars = {}
    for i in range(adet):
        fiyat, seri = Decimal(100), []
        for gun in range(GUN_SAYISI):
            seri.append(_mum(BASLANGIC + timedelta(days=gun), fiyat))
            fiyat = fiyat * (Decimal(1) + Decimal(str(egim_baslangic - i * egim_adim)))
        bars["S%02d" % i] = seri
    return bars


def _defter(bars):
    longs, shorts, prices = book_for(
        bars, AS_OF, LOOKBACK_DAYS, SIGNAL_SKIP_DAYS, TOP_FRACTION,
        LIQUIDITY_WINDOW_DAYS, MIN_DOLLAR_VOLUME, MIN_UNIVERSE,
    )
    return sorted(longs), sorted(shorts), dict(prices)


# --- Kapi 1: veritabani sorgusu -------------------------------------------

def test_load_daily_bars_bugunun_ve_sonrasinin_barini_almaz(db_session):
    for gun, kapanis in ((SINIR - timedelta(days=1), 100),
                         (SINIR, 999),                      # bugun, yarim mum
                         (SINIR + timedelta(days=1), 999)):  # yarin
        db_session.add(FuturesDailyKline(
            symbol="AAAUSDT", open_time=gun,
            close=Decimal(kapanis), volume=Decimal(1)))
    db_session.commit()

    bars = load_daily_bars(db_session, SIMDI, TARIH)
    assert [bar["open_time"] for bar in bars["AAAUSDT"]] == [SINIR - timedelta(days=1)]


# --- Kapi 2: barlar ELINE GECSE BILE kullanilmamali ------------------------

def test_book_for_gelecekteki_ralliye_kanmaz():
    bars = _evren_barlari()
    once = _defter(bars)
    assert len(once[0]) >= 2 and len(once[1]) >= 2, "defter kurulamadi, test anlamsiz"

    ucan = once[1][0]                     # short bacagindaki en kotu momentum
    fiyat = bars[ucan][-1]["close"]
    for gun in range(10):                 # yarindan itibaren her gun +%50
        fiyat *= Decimal("1.5")
        bars[ucan].append(_mum(SINIR + timedelta(days=gun), fiyat))

    assert _defter(bars) == once


def test_book_for_giris_fiyatini_as_of_gununden_alir():
    bars = _evren_barlari()
    once = _defter(bars)
    beklenen = {sembol: next(m["close"] for m in bars[sembol]
                             if m["open_time"].date() == AS_OF)
                for sembol in once[0] + once[1]}
    assert once[2] == beklenen

    for sembol in bars:                   # bugunun yarim mumu + yarin
        bars[sembol].append(_mum(SINIR, 7777))
        bars[sembol].append(_mum(SINIR + timedelta(days=1), 8888))

    sonra = _defter(bars)
    assert sonra[2] == beklenen           # fiyatlar hala as_of gununden
    assert sonra[:2] == once[:2]


def test_is_liquid_gelecekteki_hacmi_saymaz():
    bars = _evren_barlari()
    once = _defter(bars)

    # Tabanin cok altinda hacimli ama EN GUCLU momentumlu bir isim; yarin
    # devasa hacim gorecek. Gelecege bakan bir elek onu long bacagina koyardi.
    fiyat, seri = Decimal(100), []
    for gun in range(GUN_SAYISI):
        seri.append(_mum(BASLANGIC + timedelta(days=gun), fiyat, dolar=Decimal(1000000)))
        fiyat *= Decimal("1.02")
    # 45 gun, 30 degil: likidite eleginin olcusu MEDYAN ve medyan birkac
    # aykiri degere aldirmaz. Ilk halinde bu testi 5 gunluk devasa hacimle
    # yazmistim, elegin ust sinirini bilerek kaldirdigimda yesil kaldi -
    # cunku 30 dusuk + 5 yuksek degerin medyani hala dusuk. Gelecege bakan bir
    # elegi yakalayabilmesi icin gelecegin pencereyi DOMINE etmesi lazim.
    for gun in range(45):
        seri.append(_mum(SINIR + timedelta(days=gun), fiyat, dolar=Decimal(900000000)))
    bars["THINUSDT"] = seri

    sonra = _defter(bars)
    assert "THINUSDT" not in sonra[0] and "THINUSDT" not in sonra[1]
    assert sonra == once


# --- Uctan uca: iki kapi birlikte -----------------------------------------

def test_uctan_uca_gelecek_barlar_defteri_degistirmez(db_session):
    bars = _evren_barlari()
    for sembol, seri in bars.items():
        for mum in seri:
            db_session.add(FuturesDailyKline(
                symbol=sembol, open_time=mum["open_time"],
                close=mum["close"], volume=mum["volume"]))
    db_session.commit()
    once = _defter(load_daily_bars(db_session, SIMDI, TARIH))

    for sembol in bars:
        for gun in range(5):
            db_session.add(FuturesDailyKline(
                symbol=sembol, open_time=SINIR + timedelta(days=gun),
                close=Decimal(50000), volume=Decimal(1000)))
    db_session.commit()

    assert _defter(load_daily_bars(db_session, SIMDI, TARIH)) == once
