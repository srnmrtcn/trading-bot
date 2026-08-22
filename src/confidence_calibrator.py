from __future__ import annotations

from decimal import Decimal

MIN_SAMPLES = 20
BUCKET_WIDTH = Decimal("0.1")


def confidence_bucket(confidence_score: Decimal) -> Decimal:
    """The lower bound of the 0.1-wide bucket this score falls into.

    Buckets are [0.0,0.1), [0.1,0.2), ..., [0.9,1.0]. A score of exactly 1.0
    would compute to bucket index 10 without the cap below; clamping to 9
    joins it to the [0.9,1.0] bucket instead of an eleventh, empty one.
    """
    index = min(int(confidence_score / BUCKET_WIDTH), 9)
    return Decimal(index) * BUCKET_WIDTH


def compute_success_rates(records: list) -> dict:
    """`records`: (direction, confidence_score, status) tuples for resolved
    (non-pending) scenarios. Returns {(direction, bucket): (rate_or_None, sample_count)}.

    A bucket's rate is None until it has at least MIN_SAMPLES resolved
    scenarios — with too few samples, an early lucky or unlucky streak would
    look like a real pattern.
    """
    tally = {}
    for direction, confidence_score, status in records:
        key = (direction, confidence_bucket(confidence_score))
        hits, total = tally.get(key, (0, 0))
        total += 1
        if status == "hit_target":
            hits += 1
        tally[key] = (hits, total)

    return {
        key: (Decimal(hits) / Decimal(total) if total >= MIN_SAMPLES else None, total)
        for key, (hits, total) in tally.items()
    }
