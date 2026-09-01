import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from src.portfolio.book import close_position, open_positions, portfolio_equity, record_open
from src.db.models import PortfolioPosition, PortfolioSnapshot
from src.portfolio.config import STARTING_EQUITY, STRATEGY_VERSION

NOW = datetime(2026, 3, 1, 12)
OLAY = [(NOW, Decimal('0.001'), Decimal(100))]

def test_portfolio_equity_no_snapshot(db_session):
    assert portfolio_equity(db_session) == STARTING_EQUITY

def test_portfolio_equity_gets_latest(db_session):
    snapshot1 = PortfolioSnapshot(strategy_version=STRATEGY_VERSION, as_of=NOW, equity=Decimal(10500))
    snapshot2 = PortfolioSnapshot(strategy_version=STRATEGY_VERSION, as_of=NOW + timedelta(days=7), equity=Decimal(11000))
    db_session.add(snapshot1)
    db_session.add(snapshot2)
    db_session.commit()
    assert portfolio_equity(db_session) == Decimal(11000)

def test_portfolio_equity_ignores_other_strategy(db_session):
    snapshot = PortfolioSnapshot(strategy_version='baska-surum', as_of=datetime(2030, 1, 1), equity=Decimal(1))
    db_session.add(snapshot)
    db_session.commit()
    assert portfolio_equity(db_session) == STARTING_EQUITY

def test_open_positions_returns_sorted_open_positions(db_session):
    record_open(db_session, 'ZZZUSDT', 'long', Decimal(100), Decimal(2), NOW)
    record_open(db_session, 'AAAUSDT', 'long', Decimal(100), Decimal(2), NOW)
    positions = open_positions(db_session)
    assert [p.symbol for p in positions] == ['AAAUSDT', 'ZZZUSDT']

def test_open_positions_does_not_return_closed_positions(db_session):
    position = record_open(db_session, 'AAAUSDT', 'long', Decimal(100), Decimal(2), NOW)
    close_position(db_session, position, Decimal(110), OLAY, NOW)
    assert open_positions(db_session) == []

def test_record_open_sets_fields_correctly(db_session):
    p = record_open(db_session, 'AAAUSDT', 'long', Decimal(100), Decimal(2), NOW)
    assert p.status == 'open'
    assert p.strategy_version == STRATEGY_VERSION
    assert p.symbol == 'AAAUSDT'
    assert p.direction == 'long'
    assert p.entry_price == Decimal(100)
    assert p.position_size == Decimal(2)
    assert p.opened_at == NOW
    assert p.realized_pnl is None
    assert p.closed_at is None

def test_close_position_long_profit(db_session):
    p = record_open(db_session, 'AAAUSDT', 'long', Decimal(100), Decimal(2), NOW)
    result = close_position(db_session, p, Decimal(110), OLAY, NOW)
    assert p.gross_pnl == Decimal(20)
    assert p.fee_cost == Decimal('0.42')
    assert p.funding_cost == Decimal('0.2')
    assert p.realized_pnl == Decimal('19.38')
    assert result == Decimal('19.38')
    assert p.status == 'closed'
    assert p.exit_price == Decimal(110)
    assert p.closed_at == NOW

def test_close_position_short_profit_and_funding_collected(db_session):
    p = record_open(db_session, 'BBBUSDT', 'short', Decimal(100), Decimal(2), NOW)
    result = close_position(db_session, p, Decimal(90), OLAY, NOW)
    assert p.gross_pnl == Decimal(20)
    assert p.fee_cost == Decimal('0.38')
    assert p.funding_cost == Decimal('-0.2')
    assert p.realized_pnl == Decimal('19.82')

def test_close_position_short_net_profit_greater_than_gross(db_session):
    p = record_open(db_session, 'BBBUSDT', 'short', Decimal(100), Decimal(2), NOW)
    close_position(db_session, p, Decimal(90), OLAY, NOW)
    assert p.realized_pnl > (p.gross_pnl - p.fee_cost)

def test_close_position_no_funding_event(db_session):
    p = record_open(db_session, 'AAAUSDT', 'long', Decimal(100), Decimal(2), NOW)
    result = close_position(db_session, p, Decimal(110), [], NOW)
    assert p.funding_cost == Decimal(0)
    assert p.realized_pnl == Decimal('19.58')

def test_close_position_loss(db_session):
    p = record_open(db_session, 'AAAUSDT', 'long', Decimal(100), Decimal(2), NOW)
    result = close_position(db_session, p, Decimal(90), [], NOW)
    assert p.gross_pnl == Decimal(-20)
    assert result < Decimal(-20)
