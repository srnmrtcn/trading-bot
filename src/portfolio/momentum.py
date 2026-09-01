from decimal import Decimal, ROUND_HALF_UP


def percentile_ranks(values: dict) -> dict:
    """
    {sembol: Decimal} -> {sembol: Decimal yuzdelik sira}.
    pandas'in rank(pct=True) davranisiyla BIREBIR ayni olmali.
    """
    if not values:
        return {}
    ordered = sorted(values.items(), key=lambda kv: (kv[1], kv[0]))
    n = len(ordered)
    ranks, i = {}, 0
    while i < n:
        j = i
        while j + 1 < n and ordered[j + 1][1] == ordered[i][1]:
            j += 1
        average = Decimal(sum(range(i + 1, j + 2))) / Decimal(j - i + 1)
        for k in range(i, j + 1):
            ranks[ordered[k][0]] = average / Decimal(n)
        i = j + 1
    return ranks


def average_rank(returns_by_lookback: dict) -> dict:
    """
    {ufuk: {sembol: Decimal getiri}} -> {sembol: Decimal puan}.
    Her ufuk icin AYRI AYRI percentile_ranks hesaplanir, sonra her sembolun siralari ortalanir.
    """
    if not returns_by_lookback:
        return {}
    per = [percentile_ranks(v) for v in returns_by_lookback.values()]
    common = set(per[0])
    for p in per[1:]:
        common &= set(p)
    return {s: sum(p[s] for p in per) / Decimal(len(per)) for s in sorted(common)}


def select_legs(scores: dict, top_fraction) -> tuple:
    """
    {sembol: Decimal puan} ve bir oran -> (long_listesi, short_listesi).
    """
    n = len(scores)
    if n < 2:
        return [], []
    k = int((Decimal(n) * Decimal(str(top_fraction))).to_integral_value(rounding=ROUND_HALF_UP))
    k = max(1, min(k, n // 2))
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [s for s, _ in ordered[:k]], [s for s, _ in ordered[n - k:]][::-1]