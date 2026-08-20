from datetime import datetime, timezone

from src.timeutil import to_epoch_ms, utc_now


def test_to_epoch_ms_converts_naive_utc_to_correct_epoch_ms():
    # 2026-01-01 00:00:00 UTC = 1767225600000 ms
    assert to_epoch_ms(datetime(2026, 1, 1, 0, 0, 0)) == 1767225600000


def test_to_epoch_ms_is_independent_of_local_timezone():
    aware = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert to_epoch_ms(datetime(2026, 1, 1, 0, 0, 0)) == int(aware.timestamp() * 1000)


def test_utc_now_is_naive_and_close_to_utc():
    now = utc_now()
    assert now.tzinfo is None
    delta = abs((datetime.now(timezone.utc).replace(tzinfo=None) - now).total_seconds())
    assert delta < 5
