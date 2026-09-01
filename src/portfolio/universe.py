from datetime import date, datetime, timedelta
from decimal import Decimal

def daily_dollar_volume(klines: list) -> dict:
    """
    Saatlik mum listesini gunluk dolar hacmine toplar.
    Girdi: [{'open_time': datetime, 'close': Decimal, 'volume': Decimal, ...}] - baska anahtarlar olabilir, yok sayilir.
    Cikti: {datetime.date: Decimal} - o gunun butun mumlarinda close * volume toplami.
    Gun anahtari mumun open_time.date() degeridir.
    Bos liste -> bos sozluk.
    Aritmetik tamamen Decimal; float'a DONULMEZ.
    """
    raise NotImplementedError

def median_value(values: list) -> Decimal:
    """
    Decimal listesinin medyani.
        Bos liste -> None.
        Tek sayida eleman -> ortadaki deger.
        Cift sayida eleman -> ortadaki IKI degerin ortalamasi, Decimal bolme ile.
      Girdi listesi DEGISTIRILMEZ (kendi kopyani sirala).
      Ornek: [Decimal(1), Decimal(3), Decimal(2)] -> Decimal(2); [Decimal(1), Decimal(2), Decimal(3), Decimal(10)] -> Decimal('2.5')
    """
    raise NotImplementedError

def lookback_return(closes: dict, end_day, lookback_days: int):
    """
    Bir sembolun `lookback_days` gunluk getirisi.
    Girdi: closes {datetime.date: Decimal kapanis}, end_day bir datetime.date, lookback_days pozitif tam sayi.
    SIRAYLA:
        1. base_day = end_day - timedelta(days=lookback_days)
        2. end_day closes'ta YOKSA -> None
        3. base_day closes'ta YOKSA -> None
        4. closes[base_day] <= 0 ise -> None
        5. Aksi halde closes[end_day] / closes[base_day] - 1 dondur (Decimal)
    Eksik gun icin None dondurmek bilincli: takvim gunu bazinda calisiyoruz ve veri bosluğu olan sembol o hafta siralamaya HIC girmemeli, elindeki kismi pencereyle siralanmamali.
    """
    raise NotImplementedError
