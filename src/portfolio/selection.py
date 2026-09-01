from datetime import date, datetime, timedelta
from decimal import Decimal

from src.portfolio.momentum import average_rank, select_legs
from src.portfolio.universe import daily_dollar_volume, lookback_return, median_value


def closes_by_day(bars: list) -> dict:
    """
    Gunluk mum listesinden {datetime.date: Decimal kapanis} sozlugu.
    Anahtar bar['open_time'].date(), deger bar['close'].
    Bos liste -> bos sozluk.
    """
    raise NotImplementedError


def is_liquid(bars: list, as_of, window_days: int, floor) -> bool:
    """
    Sembol as_of gununde yeterince likit mi?
    SIRAYLA:
        1. volumes = daily_dollar_volume(bars)
        2. first = as_of - timedelta(days=window_days - 1)
        3. window = first <= gun <= as_of araligina DUSEN degerler (liste)
        4. len(window) * 2 < window_days ise False dondur - penceresinin yarisindan azi elinde olan sembol olculmez
        5. median = median_value(window); median None ise False
        6. median >= floor dondur
    """
    raise NotImplementedError


def build_scores(bars_by_symbol: dict, as_of, lookbacks, skip: int) -> dict:
    """
    {sembol: mum listesi} -> {sembol: Decimal puan}.
    SIRAYLA:
        1. signal_day = as_of - timedelta(days=skip)
        2. by_lookback = her ufuk icin bos sozluk
        3. Her sembol icin: closes = closes_by_day(bars); her ufuk icin lookback_return(closes, signal_day, ufuk) hesapla.
           HERHANGI BIRI None ise O SEMBOL TAMAMEN ATLANIR (hicbir ufka yazilmaz).
           Hepsi doluysa her ufkun sozlugune yazilir.
        4. average_rank(by_lookback) dondur.
    """
    raise NotImplementedError


def book_for(bars_by_symbol: dict, as_of, lookbacks, skip: int, top_fraction, window_days: int, floor, min_universe: int) -> tuple:
    """
    Bir denge gununde acilacak defteri kurar. (long_listesi, short_listesi, fiyat_sozlugu) dondurur.
    SIRAYLA:
        1. eligible = bars_by_symbol icinden SU IKI sarti saglayanlar: is_liquid(bars, as_of, window_days, floor) True VE as_of gunu closes_by_day(bars) icinde VAR.
        2. scores = build_scores(eligible, as_of, lookbacks, skip)
        3. len(scores) < min_universe ise ([], [], {}) dondur - esit agirlik ancak yeterli isim varsa anlamli.
        4. longs, shorts = select_legs(scores, top_fraction)
        5. prices = longs + shorts icindeki her sembol icin closes_by_day(eligible[sembol])[as_of]
        6. (longs, shorts, prices) dondur.
    """
    raise NotImplementedError
