import logging
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


def test_run_futures_daily_job_yazar_mum(db_session):
    _sembol(db_session, 'AAAUSDT')
    db_session.commit()
    istemci = _SahteIstemci()
    run_futures_daily_job(lambda: db_session, istemci, NOW)
    assert db_session.query(FuturesDailyKline).count() == 1
    assert istemci.cagrilar == ['AAAUSDT']


def test_run_futures_daily_job_patlarsa_yutar(db_session):
    _sembol(db_session, 'AAAUSDT')
    db_session.commit()
    istemci = _SahteIstemci(patlar=True)
    run_futures_daily_job(lambda: db_session, istemci, NOW)
    assert db_session.query(FuturesDailyKline).count() == 0


def test_run_portfolio_rebalance_job_defter_acar(db_session):
    _evren(db_session)
    run_portfolio_rebalance_job(lambda: db_session, NOW)
    assert len(open_positions(db_session)) == 8


def test_run_portfolio_rebalance_job_sirasi_gelmediyse_dokunmaz(db_session):
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
    assert "minute='40'" in mumlar
    assert "minute='50'" in denge


# --- acted=False'in iki yuzu -------------------------------------------
# Ilk canli gecede is 00:50'de kostu, 1.15 saniyede bitti, sifir dondurdu ve
# defteri hic acmadi: 00:44'teki deploy yuzunden 00:40'taki bar isi hic
# calismamisti. Loglarda debug'in ustunde tek iz yoktu. "Sirasi degildi" ile
# "sirasiydi ama acamadim" ayni sessiz dala dusuyordu; ikincisi her gun
# tekrarlanabilir ve haftalarca fark edilmez.

def test_run_rebalance_bar_yoksa_no_book_der(db_session):
    sonuc = run_rebalance(db_session, NOW)
    assert sonuc.acted is False
    assert sonuc.reason == 'no_book'
    assert sonuc.symbols == 0          # barlar hic gelmedi


def test_run_rebalance_barlar_var_ama_likit_degilse_no_book_der(db_session):
    for i in range(20):
        ad = 'S%02d' % i
        _sembol(db_session, ad)
        _mumlar(db_session, ad, 0.010 - i * 0.0008, dolar=Decimal(1000000))
    db_session.commit()
    sonuc = run_rebalance(db_session, NOW)
    assert sonuc.acted is False
    assert sonuc.reason == 'no_book'
    assert sonuc.symbols == 20         # barlar burada, gecen isim yok
    assert sonuc.universe == 0


def test_run_rebalance_sirasi_gelmediyse_not_due_der(db_session):
    _evren(db_session)
    assert run_rebalance(db_session, NOW).acted is True
    sonuc = run_rebalance(db_session, NOW + timedelta(days=1))
    assert sonuc.acted is False
    assert sonuc.reason == 'not_due'


def test_rebalance_isi_acamadiysa_uyarir(db_session, caplog):
    with caplog.at_level(logging.WARNING, logger='src.scheduler'):
        run_portfolio_rebalance_job(lambda: db_session, NOW)
    uyarilar = [k for k in caplog.records if k.levelno == logging.WARNING]
    assert len(uyarilar) == 1
    assert 'could not act' in uyarilar[0].getMessage()


def test_rebalance_isi_sirasi_gelmediyse_uyarmaz(db_session, caplog):
    _evren(db_session)
    run_portfolio_rebalance_job(lambda: db_session, NOW)
    with caplog.at_level(logging.WARNING, logger='src.scheduler'):
        run_portfolio_rebalance_job(lambda: db_session, NOW + timedelta(days=1))
    assert [k for k in caplog.records if k.levelno == logging.WARNING] == []


# --- isler sirayla kosmali, ayni anda degil ---------------------------------
# Saatlik is 00:05'te basliyor ve yaklasik 24 dakika suruyor; gunluk mum
# suprgesi 00:10'da basliyor ve 19 dakika suruyor. Yani HER GUN 19 dakika
# boyunca iki sembol supurgesi ayni rate-limit'li Binance istemcisini
# paylasiyor - 418'in gelis sekli tam olarak bu. Isleri saatte bir oteleyerek
# cozmek tahmin: her isin suresi zamanla degisir ve bir gun yine cakisirlar.
# Yapisal cozum tek isci: isler kuyruga girer, tetiklenme sirasina gore
# kosar, ve bar isinin dengelemeden once bitmesi artik zamanlama sansina
# degil kuyruk sirasina bagli olur.

def test_scheduler_isleri_ayni_anda_kosturmaz():
    import threading
    import time
    from datetime import datetime, timezone as _tz
    from apscheduler.triggers.date import DateTrigger

    scheduler = build_scheduler(session_factory=lambda: None, binance_client=None)
    scheduler.remove_all_jobs()          # gercek isler bu testte kosmasin

    kilit = threading.Lock()
    araliklar = []

    def _mesgul(ad):
        basla = time.monotonic()
        time.sleep(0.3)
        with kilit:
            araliklar.append((ad, basla, time.monotonic()))

    calis = datetime.now(_tz.utc)
    for ad in ("a", "b"):
        scheduler.add_job(_mesgul, DateTrigger(run_date=calis), args=[ad],
                          id=ad, misfire_grace_time=60)
    scheduler.start()
    try:
        for _ in range(100):
            if len(araliklar) == 2:
                break
            time.sleep(0.05)
    finally:
        scheduler.shutdown(wait=True)

    assert len(araliklar) == 2, 'iki is de kosmadi'
    araliklar.sort(key=lambda k: k[1])
    (_, _, ilk_bitis), (_, ikinci_basla, _) = araliklar
    assert ikinci_basla >= ilk_bitis, 'isler ust uste kostu'

