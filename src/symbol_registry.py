from __future__ import annotations

from dataclasses import dataclass

from src.db.models import Symbol
from src.storage import mark_symbols_inactive, upsert_symbols, set_futures_contract_flags


@dataclass
class SymbolRefreshResult:
    active_count: int
    deactivated_count: int
    futures_count: int


def refresh_symbols(session, binance_client) -> SymbolRefreshResult:
    active_symbols = binance_client.get_active_usdt_symbols()
    active_names = {entry["symbol"] for entry in active_symbols}

    previously_active = {
        row.symbol for row in session.query(Symbol).filter(Symbol.is_active == True).all()  # noqa: E712
    }

    upsert_symbols(session, active_symbols)
    mark_symbols_inactive(session, active_names)

    futures_symbols = binance_client.get_futures_usdt_symbols()
    set_futures_contract_flags(session, futures_symbols)

    deactivated = previously_active - active_names
    return SymbolRefreshResult(
        active_count=len(active_names),
        deactivated_count=len(deactivated),
        futures_count=len(futures_symbols),
    )
