from decimal import Decimal

def position_sizes(equity, symbols: list, prices: dict, leg_exposure) -> dict:
    """
    Bir bacaktaki her sembol icin kac BIRIM alinacagini/satilacagini dondurur.
    """
    if not symbols or equity <= 0:
        return {}
    per_name = Decimal(equity) * Decimal(leg_exposure) / Decimal(len(symbols))
    sizes = {}
    for symbol in symbols:
        price = prices.get(symbol)
        if price is None or price <= 0:
            continue
        sizes[symbol] = per_name / price
    return sizes

def position_pnl(direction: str, entry_price, exit_price, size) -> Decimal:
    """
    Komisyon ve funding HARIC ham kar/zarar.
    """
    if direction == "long":
        return (exit_price - entry_price) * size
    if direction == "short":
        return (entry_price - exit_price) * size
    raise ValueError("unknown direction: %r" % direction)

def round_trip_cost(entry_price, exit_price, size, fee_rate) -> Decimal:
    """
    Iki dolusun toplam maliyeti: (entry_price + exit_price) * size * fee_rate.
    """
    return (entry_price + exit_price) * size * fee_rate

def funding_cost(direction: str, funding_sum, entry_price, size) -> Decimal:
    """
    Pozisyon acikken tahakkuk eden funding'in NAKIT maliyeti.
    """
    if direction == "long":
        sign = Decimal(1)
    elif direction == "short":
        sign = Decimal(-1)
    else:
        raise ValueError("unknown direction: %r" % direction)
    return sign * funding_sum * entry_price * size
