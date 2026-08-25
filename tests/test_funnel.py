from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from src.research.funnel import (
    analyze_symbol,
    backtest_symbol,
    fee_cost_in_r,
    is_locked,
    passes_risk_filters,
    resolve_draft,
)
from src.scenario_builder import ScenarioDraft
from src.scenario_signal import MIN_CANDLES


def _series(closes: list, volumes: list = None, start: datetime = None) -> list:
    """Klines from a close series, one hour apart, oldest first.

    Highs/lows hug the close so swing detection stays driven by the closes.
    """
    start = start if start is not None else datetime(2026, 1, 1)
    volumes = volumes if volumes is not None else [Decimal("100")] * len(closes)
    rows = []
    for index, close in enumerate(closes):
        price = Decimal(str(close))
        rows.append({
            "open_time": start + timedelta(hours=index),
            "open": price,
            "high": price * Decimal("1.001"),
            "low": price * Decimal("0.999"),
            "close": price,
            "volume": Decimal(str(volumes[index])),
            "flagged": False,
        })
    return rows


def test_series_shorter_than_the_window_yields_no_evaluations():
    klines = _series([100] * (MIN_CANDLES - 1))
    assert analyze_symbol(klines).evaluated == 0


def test_counts_an_rsi_cross_up_through_the_oversold_threshold():
    """80 flat candles, 20 steady -1 drops (RSI floors at 0), then a +6 jump
    that lifts RSI to ~31.6 — exactly one crossing of the 30 line."""
    closes = [100] * 80 + list(range(99, 79, -1)) + [86]
    counts = analyze_symbol(_series(closes))
    assert counts.rsi_cross_up == 1


def test_counts_a_bullish_confluence_when_ema_crosses_up_on_a_volume_spike():
    """Flat at 100 (EMA9 == EMA21), then one up candle on 3x volume: EMA9
    lifts above EMA21 and the volume gate opens on the same candle."""
    closes = [100] * 100 + [101]
    volumes = [100] * 100 + [300]
    counts = analyze_symbol(_series(closes, volumes))
    assert counts.confluence_bullish == 1


def test_counts_a_long_signal_when_both_gates_fire_on_the_same_candle():
    """The only shape that satisfies both gates at once: a decline shallow
    enough that EMA9 sits barely under EMA21, but unbroken enough that RSI
    floors at 0 — then one violent reversal candle on spiking volume."""
    closes = [100] * 80 + [round(100 - 0.01 * i, 2) for i in range(1, 21)] + [101]
    volumes = [100] * 100 + [300]
    counts = analyze_symbol(_series(closes, volumes))
    assert counts.signal_long == 1


def test_a_realistic_selloff_and_bounce_crosses_rsi_but_produces_no_signal():
    """Characterisation of the defect this tool was built to measure.

    A 20% selloff then a sharp bounce on 3x volume — the textbook setup the
    strategy is meant to catch. RSI crosses 30 upward, but EMA9 is still far
    under EMA21 after a decline that steep, so the confluence gate stays shut
    and no signal is produced. The two gates are near mutually exclusive.

    When the signal logic is fixed, this test SHOULD start failing.
    """
    closes = [100] * 80 + list(range(99, 79, -1)) + [86]
    volumes = [100] * 100 + [300]
    counts = analyze_symbol(_series(closes, volumes))

    assert counts.rsi_cross_up == 1
    assert counts.confluence_bullish == 0
    assert counts.signal_long == 0


def test_counts_a_trend_aligned_long_when_rsi_crosses_up_inside_an_uptrend():
    """Candidate rule B: RSI cross-up while EMA9 is already above EMA21 —
    'buy the dip in an uptrend' rather than 'catch the reversal instant'.

    A steep 80-candle rally opens a wide EMA9/EMA21 gap, then 20 micro-drops
    floor RSI at 0 without closing that gap, then a bounce candle.
    """
    closes = [round(100 + i * 0.5, 2) for i in range(80)]
    closes += [round(closes[-1] - 0.01 * i, 2) for i in range(1, 21)]
    closes += [round(closes[-1] + 1, 2)]
    counts = analyze_symbol(_series(closes))

    assert counts.rsi_cross_up == 1
    assert counts.signal_long == 0, "the shipped rule still misses it"
    assert counts.signal_long_trend_aligned == 1


def test_counts_a_delayed_confirmation_long_when_the_ema_cross_lags_the_bounce():
    """Candidate rule C: enter on the EMA/volume confirmation, provided RSI
    crossed up out of oversold within the preceding window.

    The same 20% selloff and bounce as the characterisation test, now followed
    by a sustained rally on heavy volume. The EMA cross lands ~10 candles after
    the RSI cross — invisible to the shipped 3-candle backward window.
    """
    closes = [100] * 80 + list(range(99, 79, -1))
    closes += [86 + 2 * i for i in range(15)]
    # The spike must sit on the cross candle alone: a rally that is heavy
    # throughout lifts the 20-candle average with it and closes the gate.
    volumes = [100] * 100 + [120] * 5 + [400] + [120] * 9
    counts = analyze_symbol(_series(closes, volumes))

    assert counts.rsi_cross_up >= 1
    assert counts.signal_long == 0, "the shipped rule still misses it"
    assert counts.signal_long_delayed_confirm >= 1


def _draft(direction: str, entry, target, stop, hours: int = 24) -> ScenarioDraft:
    created = datetime(2026, 1, 1)
    return ScenarioDraft(
        symbol="TESTUSDT", direction=direction,
        entry_price=Decimal(str(entry)), target_price=Decimal(str(target)),
        stop_price=Decimal(str(stop)), expected_return_pct=Decimal("0"),
        confidence_score=Decimal("0.5"), created_at=created,
        expires_at=created + timedelta(hours=hours),
    )


def test_a_long_draft_that_reaches_its_target_scores_its_reward_to_risk_in_r():
    """Entry 100, target 110, stop 95 -> risk 5, reward 10, so +2R."""
    draft = _draft("long", 100, 110, 95)
    future = _series([102, 111] + [111] * 22, start=datetime(2026, 1, 1, 1))

    outcome = resolve_draft(draft, future)
    assert (outcome.status, outcome.r_multiple) == ("hit_target", Decimal("2"))
    assert outcome.exit_price == Decimal("110")


def test_a_long_draft_that_reaches_its_stop_scores_exactly_minus_one_r():
    """A stop-out always costs one unit of risk, whatever the stop distance —
    that is what makes R comparable across scenarios."""
    draft = _draft("long", 100, 110, 95)
    future = _series([98, 94] + [94] * 22, start=datetime(2026, 1, 1, 1))

    outcome = resolve_draft(draft, future)
    assert (outcome.status, outcome.r_multiple) == ("hit_stop", Decimal("-1"))
    assert outcome.exit_price == Decimal("95")


def test_an_expired_draft_scores_the_unrealised_move_at_the_last_candle():
    """Price drifts to 103 and stalls: neither 110 nor 95 is touched, so the
    trade is worth its open profit at expiry — 3 of a 5-wide risk, +0.6R."""
    draft = _draft("long", 100, 110, 95)
    future = _series([103] * 24, start=datetime(2026, 1, 1, 1))

    outcome = resolve_draft(draft, future)
    assert (outcome.status, outcome.r_multiple) == ("expired", Decimal("0.6"))
    assert outcome.exit_price == Decimal("103")


def test_a_draft_whose_future_data_stops_before_expiry_is_left_unscored():
    """The anti-bias guard. Five hours of a 24-hour scenario is not enough to
    know how it ended, but `evaluate_outcome` would happily call it `expired`
    because the clock is past `expires_at`. Scoring it would let every scenario
    near the end of the dataset land in the loss column and quietly drag the
    measured expectancy down.
    """
    draft = _draft("long", 100, 110, 95)
    truncated = _series([101] * 5, start=datetime(2026, 1, 1, 1))

    assert resolve_draft(draft, truncated) is None


def test_backtest_selects_signals_according_to_the_named_rule():
    """The same candles produce a signal under rule B and none under the
    shipped rule — the driver must dispatch on the rule, not on one fixed
    definition of 'signal'."""
    closes = [round(100 + i * 0.5, 2) for i in range(80)]
    closes += [round(closes[-1] - 0.01 * i, 2) for i in range(1, 21)]
    closes += [round(closes[-1] + 1, 2)]
    klines = _series(closes)

    assert backtest_symbol("TESTUSDT", klines, "trend").signals == 1
    assert backtest_symbol("TESTUSDT", klines, "shipped").signals == 0


def test_fee_cost_in_r_charges_each_leg_on_its_own_notional():
    """Mirrors `paper_position_closer._fees`: each leg is charged on the
    notional actually transacted, not both on the entry price. Entry 100, exit
    110, 0.05% a side: 0.105 against a 5-wide risk is 0.021R.

    The tighter the stop the more of the trade's own risk unit the fees eat —
    shrink the risk to 0.5 and the identical fee becomes 0.21R.
    """
    entry, exit_price = Decimal("100"), Decimal("110")
    rate = Decimal("0.0005")

    assert fee_cost_in_r(entry, exit_price, Decimal("5"), rate) == Decimal("0.021")
    assert fee_cost_in_r(entry, exit_price, Decimal("0.5"), rate) == Decimal("0.21")


def test_a_draft_with_no_candle_before_expiry_is_left_unscored():
    """A data gap can leave a scenario with future candles that all open after
    it expired: the coverage guard passes, `evaluate_outcome` returns
    `expired`, and there is no candle to read an exit price from."""
    draft = _draft("long", 100, 110, 95)
    after_expiry_only = _series([101] * 5, start=datetime(2026, 1, 2, 5))

    assert resolve_draft(draft, after_expiry_only) is None


def test_a_direction_is_locked_while_its_scenario_is_still_live():
    """Mirrors `has_pending_scenario`. Without this lock a single EMA cross is
    counted three times over, because `detect_confluence_in_window` reports it
    for CONFLUENCE_WINDOW consecutive candles — which inflates every rule that
    keys off a crossover.
    """
    expiry = datetime(2026, 1, 2)
    live = {"long": expiry}

    assert is_locked(live, "long", datetime(2026, 1, 1, 12)) is True
    assert is_locked(live, "long", expiry) is False, "the lock ends at expiry"
    assert is_locked(live, "short", datetime(2026, 1, 1, 12)) is False
    assert is_locked({}, "long", datetime(2026, 1, 1, 12)) is False


def test_a_flagged_candle_in_the_window_suppresses_the_signal():
    """Production refuses to read indicators off an anomaly-flagged window
    (`scenario_runner._window_rejection`). A replay that ignores that measures
    a strategy nobody is running — and flagged candles are exactly the shape
    that manufactures a spurious cross."""
    closes = [round(100 + i * 0.5, 2) for i in range(80)]
    closes += [round(closes[-1] - 0.01 * i, 2) for i in range(1, 21)]
    closes += [round(closes[-1] + 1, 2)]
    klines = _series(closes)
    assert backtest_symbol("TESTUSDT", klines, "trend").signals == 1

    klines[-5]["flagged"] = True
    assert backtest_symbol("TESTUSDT", klines, "trend").signals == 0


def test_risk_filters_reject_degenerate_stops_and_upside_down_reward():
    """The two guards the shipped scenario builder lacks.

    `nearest_support` can sit a rounding error below entry, which is what
    produces 196:1 reward-to-risk, 10x notional and a fee bill measured in
    whole R. And taking the nearest swing in each direction gives no control
    over reward vs risk at all — the measured median R:R is below 1.
    """
    healthy = _draft("long", 100, 103, 98)          # stop 2% away, R:R 1.5
    degenerate = _draft("long", 100, 103, 99.99)    # stop 0.01% away
    upside_down = _draft("long", 100, 100.5, 98)    # risk 2, reward 0.5

    assert passes_risk_filters(healthy, Decimal("0.005"), Decimal("1.5")) is True
    assert passes_risk_filters(degenerate, Decimal("0.005"), Decimal("1.5")) is False
    assert passes_risk_filters(upside_down, Decimal("0.005"), Decimal("1.5")) is False
    assert passes_risk_filters(degenerate, None, None) is True, "filters are opt-in"


def test_the_funnel_table_applies_the_same_window_gate_as_production():
    """`analyze_symbol` feeds the funnel report, which is read side by side
    with the backtest. If one honours `_window_rejection` and the other does
    not, the two tables describe different strategies and the comparison
    between them is meaningless."""
    closes = [100] * 80 + list(range(99, 79, -1)) + [86]
    klines = _series(closes)
    assert analyze_symbol(klines).rsi_cross_up == 1

    klines[-5]["flagged"] = True
    assert analyze_symbol(klines).rsi_cross_up == 0


def test_the_backtest_honours_the_btc_regime_gate():
    """`process_symbol_scenario` drops a long unless BTC's daily regime is
    "up", and drops everything when the regime cannot be determined. A replay
    without that gate measures a rule the service never runs — roughly twice
    the trades, drawn from exactly the conditions the gate exists to avoid.
    """
    closes = [round(100 + i * 0.5, 2) for i in range(80)]
    closes += [round(closes[-1] - 0.01 * i, 2) for i in range(1, 21)]
    closes += [round(closes[-1] + 1, 2)]
    klines = _series(closes)

    assert backtest_symbol("TESTUSDT", klines, "trend", regime_at=lambda now: "up").signals == 1
    assert backtest_symbol("TESTUSDT", klines, "trend", regime_at=lambda now: "down").signals == 0
    assert backtest_symbol("TESTUSDT", klines, "trend", regime_at=lambda now: None).signals == 0
