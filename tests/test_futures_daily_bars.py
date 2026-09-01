from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import FuturesDailyKline, Symbol
from src.integrity import floor_to_timeframe
from src.portfolio.bars import perpetual_symbols, refresh_futures_daily, upsert_futures_daily
from src.timeutil import to_epoch_ms

NOW = datetime(2026, 3, 10, 12)


def _sembol(session, ad, aktif=True, futures=True):
    session.add(Symbol(symbol=ad, base_asset=ad[:3], quote_asset='USDT',
                       is_active=aktif, has_futures_contract=futures))


def _satirlar(adet=3):
    return [{'open_time': datetime(2026, 3, 1) + timedelta(days=i),
             'open': Decimal(1), 'high': Decimal(1), 'low': Decimal(1),
             'close': Decimal(100 + i), 'volume': Decimal(10)} for i in range(adet)]


class _SahteIstemci:
    def __init__(self, patlayan=None):
        self.cagrilar = []
        self.patlayan = patlayan

    def get_futures_klines(self, symbol, interval, start_ms, end_ms):
        self.cagrilar.append((symbol, interval, start_ms, end_ms))
        if symbol == self.patlayan:
            raise RuntimeError('binance patladi')
        return _satirlar()


def test_perpetual_symbols_siralı_doner(db_session):
    _sembol(db_session, 'ZZZUSDT')
    _sembol(db_session, 'AAAUSDT')
    db_session.commit()
    assert perpetual_symbols(db_session) == ['AAAUSDT', 'ZZZUSDT']


def test_perpetual_symbols_pasifi_almaaz(db_session):
    _sembol(db_session, 'AAAUSDT')
    _sembol(db_session, 'BBBUSDT', aktif=False)
    db_session.commit()
    assert perpetual_symbols(db_session) == ['AAAUSDT']


def test_perpetual_symbols_futures_olmayani_almaaz(db_session):
    _sembol(db_session, 'AAAUSDT')
    _sembol(db_session, 'BBBUSDT', futures=False)
    db_session.commit()
    assert perpetual_symbols(db_session) == ['AAAUSDT']


def test_upsert_futures_daily_ekler(db_session):
    written = upsert_futures_daily(db_session, 'AAAUSDT', _satirlar())
    assert written == 3
    assert db_session.query(FuturesDailyKline).count() == 3


def test_upsert_futures_daily_gunceller_cogaltmaz(db_session):
    upsert_futures_daily(db_session, 'AAAUSDT', _satirlar())
    satirlar = _satirlar()
    satirlar[0]['close'] = Decimal(999)
    written = upsert_futures_daily(db_session, 'AAAUSDT', satirlar)
    assert written == 3
    assert db_session.query(FuturesDailyKline).count() == 3
    assert db_session.query(FuturesDailyKline).filter_by(open_time=satirlar[0]['open_time']).first().close == Decimal(999)


def test_upsert_futures_daily_bos_liste(db_session):
    written = upsert_futures_daily(db_session, 'AAAUSDT', [])
    assert written == 0
    assert db_session.query(FuturesDailyKline).count() == 0


def test_upsert_futures_daily_open_high_low_u_yok_sayar(db_session):
    upsert_futures_daily(db_session, 'AAAUSDT', _satirlar())
    ilk_satir = db_session.query(FuturesDailyKline).first()
    assert ilk_satir.close == Decimal(100)
    assert ilk_satir.volume == Decimal(10)


def test_refresh_futures_daily_yalnizca_perp_sembolleri_ceker(db_session):
    _sembol(db_session, 'AAAUSDT')
    _sembol(db_session, 'BBBUSDT', futures=False)
    db_session.commit()
    istemci = _SahteIstemci()
    written = refresh_futures_daily(db_session, istemci, NOW, days=10)
    assert written == 3
    assert [c[0] for c in istemci.cagrilar] == ['AAAUSDT']


def test_refresh_futures_daily_pencereyi_dogru_ister(db_session):
    _sembol(db_session, 'AAAUSDT')
    db_session.commit()
    istemci = _SahteIstemci()
    refresh_futures_daily(db_session, istemci, NOW, days=10)
    assert istemci.cagrilar[0][2] == to_epoch_ms(floor_to_timeframe(NOW, '1d') - timedelta(days=10))
    assert istemci.cagrilar[0][1] == '1d'


def test_refresh_futures_daily_tek_sembolun_hatasi_kosuyu_durdurmez(db_session):
    _sembol(db_session, 'AAAUSDT')
    _sembol(db_session, 'PATLAR')
    _sembol(db_session, 'ZZZUSDT')
    db_session.commit()
    istemci = _SahteIstemci(patlayan='PATLAR')
    written = refresh_futures_daily(db_session, istemci, NOW, days=10)
    assert written == 6
    assert len(istemci.cagrilar) == 3


def test_refresh_futures_daily_tekrar_cogaltmaz(db_session):
    _sembol(db_session, 'AAAUSDT')
    db_session.commit()
    istemci = _SahteIstemci()
    refresh_futures_daily(db_session, istemci, NOW, days=10)
    refresh_futures_daily(db_session, istemci, NOW, days=10)
    assert db_session.query(FuturesDailyKline).count() == 3
