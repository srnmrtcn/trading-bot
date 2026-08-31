from __future__ import annotations

from decimal import Decimal

MIN_SAMPLES = 20
BUCKET_WIDTH = Decimal("0.25")


def confidence_bucket(confidence_score: Decimal) -> Decimal:
    """The lower bound of the 0.25-wide bucket this score falls into.

    Buckets are [0.0,0.25), [0.25,0.5), [0.5,0.75), [0.75,1.0].
    A score of exactly 1.0 would compute to bucket index 4 without the cap below;
    clamping to 3 joins it to the [0.75,1.0] bucket instead of a fifth, empty one.
    """
    index = min(int(confidence_score / BUCKET_WIDTH), 3)
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
