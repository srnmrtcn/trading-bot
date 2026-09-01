from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import FuturesDailyKline
from src.portfolio.book import portfolio_equity
from src.portfolio.config import REBALANCE_DAYS, STRATEGY_VERSION
from src.portfolio.rebalancer import is_rebalance_due, load_daily_bars, record_snapshot

BASLANGIC = datetime(2026, 1, 1)
NOW = BASLANGIC + timedelta(days=70)


def _ekle(session, sembol, gun_sayisi, dolar=Decimal(60000000)):
    price = Decimal(100)
    for i in range(gun_sayisi):
        session.add(FuturesDailyKline(symbol=sembol,
                                      open_time=BASLANGIC + timedelta(days=i),
                                      close=price, volume=dolar / price))


def test_is_rebalance_due_no_snapshot(db_session):
    assert is_rebalance_due(db_session, NOW) is True


def test_is_rebalance_due_time_not_expired(db_session):
    record_snapshot(db_session, NOW, Decimal(10000), 0, 0)
    assert is_rebalance_due(db_session, NOW + timedelta(days=REBALANCE_DAYS - 1)) is False


def test_is_rebalance_due_time_expired(db_session):
    record_snapshot(db_session, NOW, Decimal(10000), 0, 0)
    assert is_rebalance_due(db_session, NOW + timedelta(days=REBALANCE_DAYS)) is True


def test_load_daily_bars_group_by_symbol(db_session):
    _ekle(db_session, 'AAAUSDT', 5)
    _ekle(db_session, 'BBBUSDT', 5)
    db_session.commit()
    bars = load_daily_bars(db_session, NOW, 100)
    assert sorted(bars) == ['AAAUSDT', 'BBBUSDT']
    assert len(bars['AAAUSDT']) == 5
    assert bars['AAAUSDT'][0]['open_time'] < bars['AAAUSDT'][-1]['open_time']


def test_load_daily_bars_outside_window(db_session):
    _ekle(db_session, 'AAAUSDT', 5)
    db_session.commit()
    assert load_daily_bars(db_session, NOW, 3) == {}


def test_load_daily_bars_mum_dict_keys(db_session):
    _ekle(db_session, 'AAAUSDT', 5)
    db_session.commit()
    bars = load_daily_bars(db_session, NOW, 100)
    assert sorted(bars['AAAUSDT'][0]) == ['close', 'open_time', 'volume']


def test_record_snapshot_inserts_row(db_session):
    snap = record_snapshot(db_session, NOW, Decimal('12345.67'), 3, 4)
    assert snap.strategy_version == STRATEGY_VERSION
    assert snap.equity == Decimal('12345.67')
    assert snap.positions_closed == 3
    assert snap.positions_opened == 4


def test_record_snapshot_equity_visible(db_session):
    record_snapshot(db_session, NOW, Decimal('12345.67'), 0, 0)
    assert portfolio_equity(db_session) == Decimal('12345.67')
