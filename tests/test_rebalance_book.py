from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import Kline, PortfolioPosition
from src.portfolio.book import open_positions, portfolio_equity
from src.portfolio.config import REBALANCE_DAYS, STARTING_EQUITY
from src.portfolio.rebalancer import RebalanceResult, run_rebalance


BASLANGIC = datetime(2026, 1, 1)
NOW = BASLANGIC + timedelta(days=70)


def _ekle(session, sembol, gun_sayisi, drift, dolar=Decimal(60000000)):
    price = Decimal(100)
    for i in range(gun_sayisi):
        session.add(Kline(symbol=sembol, timeframe='1d', open_time=BASLANGIC + timedelta(days=i),
                          open=price, high=price, low=price, close=price,
                          volume=dolar / price, flagged=False))
        price = price * (Decimal(1) + Decimal(str(drift)))


def _evren(session):
    for i in range(20):
        _ekle(session, 'S%02d' % i, 70, 0.010 - i * 0.0008)
    session.commit()


def test_run_rebalance_creates_one_position(db_session):
    _evren(db_session)
    sonuc = run_rebalance(db_session, NOW)
    assert sonuc.acted is True
    assert sonuc.closed == 0
    assert sonuc.opened == 8
    assert sonuc.universe == 8


def test_run_rebalance_opens_both_long_and_short_positions(db_session):
    _evren(db_session)
    run_rebalance(db_session, NOW)
    pos = open_positions(db_session)
    assert len(pos) == 8
    long_count = sum(1 for p in pos if p.direction == 'long')
    short_count = sum(1 for p in pos if p.direction == 'short')
    assert long_count == 4
    assert short_count == 4


def test_run_rebalance_is_dollar_neutral(db_session):
    _evren(db_session)
    run_rebalance(db_session, NOW)
    pos = open_positions(db_session)
    long_notional = sum(p.entry_price * p.position_size for p in pos if p.direction == 'long')
    short_notional = sum(p.entry_price * p.position_size for p in pos if p.direction == 'short')
    diff = abs(long_notional - short_notional)
    assert diff < Decimal('0.01')


def test_run_rebalance_has_total_exposure_of_twice_the_equity(db_session):
    _evren(db_session)
    run_rebalance(db_session, NOW)
    pos = open_positions(db_session)
    total_notional = sum(p.entry_price * p.position_size for p in pos)
    diff = abs(total_notional - STARTING_EQUITY * 2)
    assert diff < Decimal('0.01')


def test_run_rebalance_opens_strongest_long_and_weakest_short(db_session):
    _evren(db_session)
    run_rebalance(db_session, NOW)
    pos = open_positions(db_session)
    long_symbols = [p.symbol for p in pos if p.direction == 'long']
    short_symbols = [p.symbol for p in pos if p.direction == 'short']
    assert 'S00' in long_symbols
    assert 'S19' in short_symbols


def test_run_rebalance_does_not_open_illiquid_assets(db_session):
    _evren(db_session)
    _ekle(db_session, 'ILLIQ', 70, 0.05, Decimal(1000))
    db_session.commit()
    run_rebalance(db_session, NOW)
    pos = open_positions(db_session)
    illiq_in_positions = [p for p in pos if p.symbol == 'ILLIQ']
    assert len(illiq_in_positions) == 0


def test_run_rebalance_does_nothing_if_not_time_yet(db_session):
    _evren(db_session)
    run_rebalance(db_session, NOW)
    sonuc2 = run_rebalance(db_session, NOW + timedelta(days=REBALANCE_DAYS - 1))
    assert sonuc2.acted is False
    assert sonuc2.opened == 0
    assert sonuc2.closed == 0
    assert len(open_positions(db_session)) == 8


def test_run_rebalance_closes_first_then_opens_new_positions(db_session):
    _evren(db_session)
    run_rebalance(db_session, NOW)
    sonuc2 = run_rebalance(db_session, NOW + timedelta(days=REBALANCE_DAYS))
    assert sonuc2.acted is True
    assert sonuc2.closed == 8


def test_run_rebalance_accounting_for_closed_positions(db_session):
    _evren(db_session)
    run_rebalance(db_session, NOW)
    run_rebalance(db_session, NOW + timedelta(days=REBALANCE_DAYS))
    kapananlar = db_session.query(PortfolioPosition).filter(
        PortfolioPosition.status == 'closed').all()
    assert len(kapananlar) == 8
    for p in kapananlar:
        assert p.realized_pnl is not None
        assert p.gross_pnl is not None


def test_run_rebalance_has_cost_for_closing_and_opening(db_session):
    _evren(db_session)
    run_rebalance(db_session, NOW)
    run_rebalance(db_session, NOW + timedelta(days=REBALANCE_DAYS))
    equity = portfolio_equity(db_session)
    assert equity < STARTING_EQUITY


def test_run_rebalance_does_not_open_positions_if_no_universe(db_session):
    sonuc = run_rebalance(db_session, NOW)
    assert sonuc.acted is True
    assert sonuc.opened == 0
    assert sonuc.universe == 0
    assert len(open_positions(db_session)) == 0
