from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import Kline, Scenario
from src.scenario_runner import run_scenario_generation, process_symbol_scenario
from src.scenario_signal import MIN_CANDLES

# A fixed "current candle boundary" so nothing in these tests depends on the
# wall clock: NOW sits 30 minutes into the candle that opened at BOUNDARY,
# exactly like the hourly job running at minute 5.
BOUNDARY = datetime(2026, 8, 21, 12, 0)
NOW = BOUNDARY + timedelta(minutes=30)

# The long setup below: a swing high at 1100 (target), a swing low at 900
# (stop), and a reversal candle closing at 1020 (entry).
SWING_HIGH = Decimal("1100")
SWING_LOW = Decimal("900")
ENTRY = Decimal("1020")
# What the still-forming candle closes at mid-formation. If it ever leaks into
# the evaluation, this is the price that would be stamped on the scenario.
IN_PROGRESS_CLOSE = Decimal("1025")


def _kline(symbol, open_time, open_, high, low, close, volume, flagged=False):
    return Kline(
        symbol=symbol, timeframe="1h", open_time=open_time,
        open=open_, high=high, low=low, close=close, volume=volume, flagged=flagged,
    )


def _flat(symbol, open_time, price=Decimal("100")):
    return _kline(symbol, open_time, price, price, price, price, Decimal("1000"))


def _insert_flat_klines(db_session, symbol, count, boundary=BOUNDARY, price=Decimal("100")):
    """`count` contiguous flat candles ending on the last closed candle."""
    for i in range(count):
        db_session.add(_flat(symbol, boundary - timedelta(hours=count - i), price))
    db_session.commit()


def _signal_klines(symbol, boundary):
    """101 closed candles that genuinely fire a long signal on the last one,
    plus the still-forming candle at `boundary`.

    Shape (Binance-shaped, all prices ~1000 so no move is anomaly-sized):
      * 40 flat candles at 1000, with a lone spike high (1100) and a lone dip
        low (900) that become the swing points build_scenario needs,
      * a 60-candle downtrend of -1/candle (drives RSI to 0, EMA9 under EMA21),
      * a reversal candle closing +80 on 5x volume (RSI back above 30, EMA9
        crosses up, volume spike),
      * the in-progress candle: same elevated trade rate, but only ~5 minutes
        of accumulated volume — the stub `get_resume_point` re-fetches hourly.
    """
    rows = []
    flat = Decimal("1000")
    for i in range(40):
        open_time = boundary - timedelta(hours=101 - i)
        high = SWING_HIGH if i == 20 else flat
        low = SWING_LOW if i == 30 else flat
        rows.append(_kline(symbol, open_time, flat, high, low, flat, Decimal("1000")))

    price = flat
    for i in range(40, 100):
        price -= Decimal("1")
        rows.append(_kline(
            symbol, boundary - timedelta(hours=101 - i),
            price, price, price, price, Decimal("1000"),
        ))

    rows.append(_kline(
        symbol, boundary - timedelta(hours=1),
        open_=price, high=ENTRY, low=price, close=ENTRY, volume=Decimal("5000"),
    ))
    in_progress = _kline(
        symbol, boundary,
        open_=ENTRY, high=Decimal("1030"), low=Decimal("1018"),
        close=IN_PROGRESS_CLOSE, volume=Decimal("200"),
    )
    return rows, in_progress


def _seed_signal_klines(
    db_session, symbol="BTCUSDT", boundary=BOUNDARY,
    drop_index=None, flag_index=None, include_in_progress=True,
):
    closed, in_progress = _signal_klines(symbol, boundary)
    if flag_index is not None:
        closed[flag_index].flagged = True
    if drop_index is not None:
        del closed[drop_index]
    for row in closed:
        db_session.add(row)
    if include_in_progress:
        db_session.add(in_progress)
    db_session.commit()


def test_process_symbol_scenario_skips_with_insufficient_data(db_session):
    _insert_flat_klines(db_session, "BTCUSDT", MIN_CANDLES - 1)
    outcome = process_symbol_scenario(db_session, "BTCUSDT", now=NOW)
    assert outcome == "skipped"


def test_process_symbol_scenario_skips_when_no_signal(db_session):
    _insert_flat_klines(db_session, "BTCUSDT", MIN_CANDLES)
    outcome = process_symbol_scenario(db_session, "BTCUSDT", now=NOW)
    assert outcome == "skipped"
    assert db_session.query(Scenario).count() == 0


def test_process_symbol_scenario_generates_a_scenario_from_a_real_signal(db_session):
    """The whole chain, for real: evaluate_signal -> build_scenario ->
    has_pending_scenario -> insert_scenario -> a persisted row."""
    _seed_signal_klines(db_session)

    outcome = process_symbol_scenario(db_session, "BTCUSDT", now=NOW)

    assert outcome == "generated"
    row = db_session.query(Scenario).one()
    assert row.symbol == "BTCUSDT"
    assert row.direction == "long"
    assert Decimal(str(row.entry_price)) == ENTRY
    assert Decimal(str(row.target_price)) == SWING_HIGH
    assert Decimal(str(row.stop_price)) == SWING_LOW
    assert row.status == "pending"
    assert row.created_at == NOW
    assert NOW < row.expires_at <= NOW + timedelta(hours=168)


def test_process_symbol_scenario_ignores_the_in_progress_candle(db_session):
    """Regression: the newest stored row is always the ~5-minute-old stub the
    hourly job re-fetches. Reading it made the volume-spike gate compare a
    partial candle's volume against a 20-hour average — unreachable — and put
    an unclosed candle's price in `entry_price`."""
    _seed_signal_klines(db_session)

    assert process_symbol_scenario(db_session, "BTCUSDT", now=NOW) == "generated"

    row = db_session.query(Scenario).one()
    # The last CLOSED candle's close, not the still-forming candle's.
    assert Decimal(str(row.entry_price)) == ENTRY
    assert Decimal(str(row.entry_price)) != IN_PROGRESS_CLOSE


def test_process_symbol_scenario_skips_when_a_live_pending_scenario_exists(db_session):
    """The dedup path must fire against a real persisted row, not a stub."""
    _seed_signal_klines(db_session)

    assert process_symbol_scenario(db_session, "BTCUSDT", now=NOW) == "generated"
    assert process_symbol_scenario(db_session, "BTCUSDT", now=NOW) == "skipped"
    assert db_session.query(Scenario).count() == 1


def test_process_symbol_scenario_regenerates_after_the_pending_scenario_expired(db_session):
    """An expired pending scenario must release the (symbol, direction) lock."""
    _seed_signal_klines(db_session)
    db_session.add(Scenario(
        symbol="BTCUSDT", direction="long",
        entry_price=Decimal("990"), target_price=Decimal("1100"), stop_price=Decimal("900"),
        expected_return_pct=Decimal("0.11"), confidence_score=Decimal("0.5"),
        created_at=NOW - timedelta(hours=12), expires_at=NOW - timedelta(hours=1),
        status="pending",
    ))
    db_session.commit()

    assert process_symbol_scenario(db_session, "BTCUSDT", now=NOW) == "generated"
    assert db_session.query(Scenario).count() == 2


def test_process_symbol_scenario_skips_stale_data(db_session):
    """A symbol whose fetch failed this run (or a restart after downtime) has
    stored nothing new, so its newest candle is hours old. The series below
    would otherwise fire a signal — stamping those stale prices with a fresh
    created_at/expires_at would be a lie."""
    # Fetch failed, so not even the in-progress stub was stored: the newest row
    # is the closed candle from four hours ago.
    _seed_signal_klines(
        db_session, boundary=BOUNDARY - timedelta(hours=3), include_in_progress=False,
    )

    assert process_symbol_scenario(db_session, "BTCUSDT", now=NOW) == "skipped"
    assert db_session.query(Scenario).count() == 0


def test_process_symbol_scenario_accepts_a_symbol_with_no_in_progress_candle_yet(db_session):
    """The freshness gate must not require the stub to exist — a fetch that has
    not yet stored the current candle is still perfectly fresh."""
    _seed_signal_klines(db_session, include_in_progress=False)

    assert process_symbol_scenario(db_session, "BTCUSDT", now=NOW) == "generated"


def test_process_symbol_scenario_skips_when_recent_window_has_a_gap(db_session):
    """Subsystem A tolerates permanently unfillable gaps, so non-adjacent
    candles must not be fed to RSI/EMA/ATR as if they were consecutive."""
    _seed_signal_klines(db_session, drop_index=10)

    assert process_symbol_scenario(db_session, "BTCUSDT", now=NOW) == "skipped"
    assert db_session.query(Scenario).count() == 0


def test_process_symbol_scenario_skips_when_a_recent_candle_is_flagged(db_session):
    """A flagged candle (zero volume, or a >50% close-to-close move) is exactly
    the shape that manufactures a spurious RSI + EMA cross."""
    _seed_signal_klines(db_session, flag_index=50)

    assert process_symbol_scenario(db_session, "BTCUSDT", now=NOW) == "skipped"
    assert db_session.query(Scenario).count() == 0


def test_run_scenario_generation_persists_scenarios_end_to_end(db_session, monkeypatch):
    import src.scenario_runner as scenario_runner_module

    _seed_signal_klines(db_session, symbol="BTCUSDT")
    _insert_flat_klines(db_session, "ETHUSDT", MIN_CANDLES)
    monkeypatch.setattr(scenario_runner_module, "utc_now", lambda: NOW)

    result = run_scenario_generation(db_session, ["BTCUSDT", "ETHUSDT"])

    assert result.scanned == 2
    assert result.generated == 1
    assert result.skipped == 1
    assert result.failed == 0
    assert [row.symbol for row in db_session.query(Scenario).all()] == ["BTCUSDT"]


def test_run_scenario_generation_isolates_symbol_failures(db_session, monkeypatch):
    _insert_flat_klines(db_session, "BTCUSDT", MIN_CANDLES)
    _insert_flat_klines(db_session, "ETHUSDT", MIN_CANDLES)

    import src.scenario_runner as scenario_runner_module

    def fake_process(session, symbol, timeframe="1h"):
        if symbol == "BTCUSDT":
            raise RuntimeError("boom")
        return "skipped"

    monkeypatch.setattr(scenario_runner_module, "process_symbol_scenario", fake_process)

    result = run_scenario_generation(db_session, ["BTCUSDT", "ETHUSDT"])

    assert result.scanned == 2
    assert result.failed == 1
    assert result.skipped == 1
    assert result.generated == 0
    # The session must still be usable after the failure (rolled back, not poisoned).
    assert db_session.query(Kline).filter(Kline.symbol == "ETHUSDT").count() == MIN_CANDLES


def test_run_scenario_generation_counts_generated(db_session, monkeypatch):
    _insert_flat_klines(db_session, "BTCUSDT", MIN_CANDLES)

    import src.scenario_runner as scenario_runner_module

    monkeypatch.setattr(scenario_runner_module, "process_symbol_scenario", lambda session, symbol, timeframe="1h": "generated")

    result = run_scenario_generation(db_session, ["BTCUSDT"])

    assert result.scanned == 1
    assert result.generated == 1
    assert result.skipped == 0
    assert result.failed == 0
