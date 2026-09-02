from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import FuturesDailyKline, Symbol
from src.portfolio.book import open_positions
from src.scheduler import (
    build_scheduler,
    run_futures_daily_job,
    run_portfolio_rebalance_job,
)
from src.portfolio.bars import refresh_futures_daily
from src.portfolio.rebalancer import run_rebalance


BASLANGIC = datetime(2026, 1, 1)
NOW = BASLANGIC + timedelta(days=70)


def _sembol(session, ad):
    session.add(Symbol(symbol=ad, base_asset=ad[:3], quote_asset='USDT',
                       is_active=True, has_futures_contract=True))


def _mumlar(session, sembol, drift, dolar=Decimal(60000000)):
    price = Decimal(100)
    for i in range(70):
        session.add(FuturesDailyKline(symbol=sembol,
                                      open_time=BASLANGIC + timedelta(days=i),
                                      close=price, volume=dolar / price))
        price = price * (Decimal(1) + Decimal(str(drift)))


def _evren(session):
    for i in range(20):
        ad = 'S%02d' % i
        _sembol(session, ad)
        _mumlar(session, ad, 0.010 - i * 0.0008)
    session.commit()


class _SahteIstemci:
    def __init__(self, patlar=False):
        self.cagrilar = []
        self.patlar = patlar

    def get_futures_klines(self, symbol, interval, start_ms, end_ms):
        self.cagrilar.append(symbol)
        if self.patlar:
            raise RuntimeError('binance patladi')
        return [{'open_time': datetime(2026, 3, 1), 'open': Decimal(1),
                 'high': Decimal(1), 'low': Decimal(1), 'close': Decimal(5),
                 'volume': Decimal(7)}]


def test_run_futures_daily_job_yazar_mum():
    from src.db.session import get_session
    db_session = get_session()
    _sembol(db_session, 'AAAUSDT')
    db_session.commit()
    istemci = _SahteIstemci()
    run_futures_daily_job(lambda: db_session, istemci, NOW)
    assert db_session.query(FuturesDailyKline).count() == 1
    assert istemci.cagrilar == ['AAAUSDT']


def test_run_futures_daily_job_patlarsa_yutar():
    from src.db.session import get_session
    db_session = get_session()
    _sembol(db_session, 'AAAUSDT')
    db_session.commit()
    istemci = _SahteIstemci(patlar=True)
    run_futures_daily_job(lambda: db_session, istemci, NOW)
    assert db_session.query(FuturesDailyKline).count() == 0


def test_run_portfolio_rebalance_job_defter_acar():
    from src.db.session import get_session
    db_session = get_session()
    _evren(db_session)
    run_portfolio_rebalance_job(lambda: db_session, NOW)
    assert len(open_positions(db_session)) == 8


def test_run_portfolio_rebalance_job_sirasi_gelmediyse_dokunmaz():
    from src.db.session import get_session
    db_session = get_session()
    _evren(db_session)
    run_portfolio_rebalance_job(lambda: db_session, NOW)
    run_portfolio_rebalance_job(lambda: db_session, NOW + timedelta(days=1))
    assert len(open_positions(db_session)) == 8


def test_run_portfolio_rebalance_job_patlarsa_yutar():
    class _BozukOturum:
        def __init__(self):
            self.geri_alindi = False
            self.kapandi = False
        def query(self, *args, **kwargs):
            raise RuntimeError('veritabani gitti')
        def rollback(self):
            self.geri_alindi = True
        def close(self):
            self.kapandi = True

    bozuk = _BozukOturum()
    run_portfolio_rebalance_job(lambda: bozuk, NOW)
    assert bozuk.geri_alindi is True
    assert bozuk.kapandi is True


def test_build_scheduler_iki_yeni_is_kaydeder():
    scheduler = build_scheduler(session_factory=lambda: None, binance_client=None)
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert 'futures_daily_bars' in job_ids
    assert 'portfolio_rebalance' in job_ids
    assert len(job_ids) == 5


def test_build_scheduler_mumlar_dengeden_once():
    scheduler = build_scheduler(session_factory=lambda: None, binance_client=None)
    jobs = {job.id: job for job in scheduler.get_jobs()}
    mumlar = str(jobs['futures_daily_bars'].trigger)
    denge = str(jobs['portfolio_rebalance'].trigger)
    assert "minute='20'" in mumlar
    assert "minute='30'" in denge
