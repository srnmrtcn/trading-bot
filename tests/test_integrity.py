from datetime import datetime, timedelta
from decimal import Decimal

from src.integrity import detect_gaps, flag_anomalies, floor_to_timeframe


def test_detect_gaps_finds_no_gap_when_complete():
    start = datetime(2026, 1, 1, 0)
    end = datetime(2026, 1, 1, 3)
    existing = [start, start + timedelta(hours=1), start + timedelta(hours=2), end]
    assert detect_gaps(existing, "1h", start, end) == []


def test_detect_gaps_finds_single_missing_candle():
    start = datetime(2026, 1, 1, 0)
    end = datetime(2026, 1, 1, 3)
    existing = [start, start + timedelta(hours=2), end]  # missing hour 1
    gaps = detect_gaps(existing, "1h", start, end)
    assert len(gaps) == 1
    assert gaps[0].start == start + timedelta(hours=1)
    assert gaps[0].end == start + timedelta(hours=1)


def test_detect_gaps_groups_consecutive_missing_candles():
    start = datetime(2026, 1, 1, 0)
    end = datetime(2026, 1, 1, 4)
    existing = [start, end]  # hours 1, 2, 3 all missing
    gaps = detect_gaps(existing, "1h", start, end)
    assert len(gaps) == 1
    assert gaps[0].start == start + timedelta(hours=1)
    assert gaps[0].end == start + timedelta(hours=3)


def _row(close, volume="1000"):
    return {"open_time": datetime(2026, 1, 1), "open": Decimal("100"), "high": Decimal("100"),
            "low": Decimal("100"), "close": Decimal(close), "volume": Decimal(volume)}


def test_flag_anomalies_flags_zero_volume():
    rows = [_row("100", volume="0")]
    result = flag_anomalies(rows)
    assert result[0]["flagged"] is True


def test_flag_anomalies_flags_large_price_spike():
    rows = [_row("100"), _row("160")]  # 60% jump
    result = flag_anomalies(rows)
    assert result[0]["flagged"] is False
    assert result[1]["flagged"] is True


def test_flag_anomalies_does_not_flag_normal_data():
    rows = [_row("100"), _row("102"), _row("99")]
    result = flag_anomalies(rows)
    assert all(row["flagged"] is False for row in result)


def test_floor_to_timeframe_aligns_to_candle_boundary():
    assert floor_to_timeframe(datetime(2026, 1, 1, 13, 47, 12), "1h") == datetime(2026, 1, 1, 13)
    assert floor_to_timeframe(datetime(2026, 1, 1, 13, 47, 12), "1d") == datetime(2026, 1, 1, 0)


def test_floor_to_timeframe_is_idempotent_on_aligned_input():
    aligned = datetime(2026, 1, 1, 13)
    assert floor_to_timeframe(aligned, "1h") == aligned


def test_flag_anomalies_with_previous_close_none():
    rows = [_row("100"), _row("160")]  # 60% jump
    result = flag_anomalies(rows, previous_close=None)
    assert result[0]["flagged"] is False  # No spike check for first row when previous_close is None
    assert result[1]["flagged"] is True


def test_flag_anomalies_with_previous_close_zero():
    rows = [_row("100"), _row("160")]  # 60% jump
    result = flag_anomalies(rows, previous_close=0)
    assert result[0]["flagged"] is False  # No spike check for first row when previous_close is 0
    assert result[1]["flagged"] is True


def test_flag_anomalies_with_previous_close_nonzero():
    rows = [_row("100"), _row("160")]  # 60% jump
    result = flag_anomalies(rows, previous_close=Decimal("100"))
    assert result[0]["flagged"] is False  # First row compared to previous_close (no spike)
    assert result[1]["flagged"] is True  # Second row compared to first row's close


def test_flag_anomalies_with_zero_volume_and_previous_close_nonzero():
    rows = [_row("100", volume="0"), _row("160")]
    result = flag_anomalies(rows, previous_close=Decimal("100"))
    assert result[0]["flagged"] is True  # Zero volume flagged
    assert result[1]["flagged"] is True  # Spike flagged


def test_flag_anomalies_batch_consecutive_check():
    rows = [_row("100"), _row("160"), _row("100")]  # 60% jump, then back to 100
    result = flag_anomalies(rows, previous_close=Decimal("100"))
    assert result[0]["flagged"] is False  # First row compared to previous_close (no spike)
    assert result[1]["flagged"] is True  # Second row compared to first row's close (60% spike)
    assert result[2]["flagged"] is False  # Third row compared to second row's close (no spike)