from decimal import Decimal

def position_sizes(equity, symbols, prices, leg_exposure):
    """
    Bir bacaktaki her sembol icin kac BIRIM alinacagini/satilacagini dondurur.
    """
    raise NotImplementedError

def position_pnl(direction, entry_price, exit_price, size):
    """
    Komisyon ve funding HARIC ham kar/zarar.
    """
    raise NotImplementedError

def round_trip_cost(entry_price, exit_price, size, fee_rate):
    """
    Iki dolusun toplam maliyeti: (entry_price + exit_price) * size * fee_rate.
    """
    raise NotImplementedError

def funding_cost(direction, funding_sum, entry_price, size):
    """
    Pozisyon acikken tahakkuk eden funding'in NAKIT maliyeti.
    """
    raise NotImplementedError
