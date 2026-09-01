from decimal import Decimal
from datetime import datetime
from src.db.models import PortfolioPosition, PortfolioSnapshot
from src.portfolio.config import STARTING_EQUITY, STRATEGY_VERSION
from src.portfolio.accounting import position_pnl
import src.trading_costs

def portfolio_equity(session):
    """
    PortfolioSnapshot tablosundan, strategy_version == STRATEGY_VERSION olan EN YENI satirin equity degeri.
    Siralama: as_of AZALAN, esitlik bozulursa id AZALAN.
    Hic satir yoksa STARTING_EQUITY dondur.
    BASKA strateji surumune ait satirlar HIC dikkate alinmaz - daha yeni tarihli olsalar bile.
    """
    raise NotImplementedError

def open_positions(session):
    """
    PortfolioPosition tablosunda status == 'open' VE strategy_version == STRATEGY_VERSION olan satirlar, symbol'e gore ARTAN sirali liste.
    Baska surumun ya da kapanmis pozisyonlarin satirlari donmez.
    """
    raise NotImplementedError

def record_open(session, symbol: str, direction: str, entry_price, size, now: datetime):
    """
    Yeni bir PortfolioPosition satiri ekler ve DONDURUR.
    Alanlar: strategy_version=STRATEGY_VERSION, symbol, direction, entry_price, position_size=size, opened_at=now, status='open'. Diger alanlar (exit_price, closed_at, gross_pnl, fee_cost, funding_cost, realized_pnl) DOKUNULMAZ, NULL kalir.
    session.add + session.commit yapilir.
    """
    raise NotImplementedError

def close_position(session, position, exit_price, funding_events: list, now: datetime) -> Decimal:
    """
    Bir pozisyonu kapatir, uc maliyet bileşenini AYRI AYRI yazar ve net kar/zarari dondurur.
    SIRAYLA:
      gross = position_pnl(position.direction, position.entry_price, exit_price, position.position_size)
      fee = trading_costs.round_trip_cost(position.position_size, position.entry_price, exit_price)
      funding = trading_costs.funding_cost(position.direction, position.position_size, funding_events)
      position.exit_price = exit_price
      position.closed_at = now
      position.status = 'closed'
      position.gross_pnl = gross
      position.fee_cost = fee
      position.funding_cost = funding
      position.realized_pnl = gross - fee - funding
      session.commit()
      return position.realized_pnl
    """
    raise NotImplementedError
