from decimal import Decimal, ROUND_HALF_UP


def percentile_ranks(values: dict) -> dict:
    """
    {sembol: Decimal} -> {sembol: Decimal yuzdelik sira}.
    pandas'in rank(pct=True) davranisiyla BIREBIR ayni olmali.
    """
    raise NotImplementedError


def average_rank(returns_by_lookback: dict) -> dict:
    """
    {ufuk: {sembol: Decimal getiri}} -> {sembol: Decimal puan}.
    Her ufuk icin AYRI AYRI percentile_ranks hesaplanir, sonra her sembolun siralari ortalanir.
    """
    raise NotImplementedError


def select_legs(scores: dict, top_fraction) -> tuple:
    """
    {sembol: Decimal puan} ve bir oran -> (long_listesi, short_listesi).
    """
    raise NotImplementedError
