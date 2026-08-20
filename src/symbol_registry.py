from __future__ import annotations

from dataclasses import dataclass

from src.db.models import Symbol
from src.storage import mark_symbols_inactive, upsert_symbols


@dataclass
class SymbolRefreshResult:
    active_count: int
    deactivated_count: int


def refresh_symbols(session, binance_client) -> SymbolRefreshResult:
    active_symbols = binance_client.get_active_usdt_symbols()
    active_names = {entry["symbol"] for entry in active_symbols}

    previously_active = {
        row.symbol for row in session.query(Symbol).filter(Symbol.is_active == True).all()  # noqa: E712
    }

    upsert_symbols(session, active_symbols)
    mark_symbols_inactive(session, active_names)

    deactivated = previously_active - active_names
    return SymbolRefreshResult(active_count=len(active_names), deactivated_count=len(deactivated))
