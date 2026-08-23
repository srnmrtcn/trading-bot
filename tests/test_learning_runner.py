from datetime import datetime, timedelta
from decimal import Decimal

from src.confidence_calibrator import MIN_SAMPLES
from src.db.models import Kline, Scenario
from src.learning_runner import calibrate_scenarios, resolve_pending_scenarios


def _pending_scenario(symbol="BTCUSDT", direction="long", created_at=None, expires_at=None):
    created_at = created_at or datetime(2026, 1, 1, 10, 5, 0)
    expires_at = expires_at or created_at + timedelta(hours=24)
    return Scenario(
        symbol=symbol, direction=direction,
        entry_price=Decimal("100"), target_price=Decimal("110"), stop_price=Decimal("90"),
        expected_return_pct=Decimal("0.1"), confidence_score=Decimal("0.6"),
        created_at=created_at, expires_at=expires_at, status="pending",
    )


def _kline(symbol, open_time, high, low):
    return Kline(
        symbol=symbol, timeframe="1h", open_time=open_time,
        open=Decimal("100"), high=Decimal(str(high)), low=Decimal(str(low)), close=Decimal("100"),
        volume=Decimal("1000"), flagged=False,
    )


def test_resolve_pending_scenarios_marks_a_hit_target(db_session):
    scenario = _pending_scenario(created_at=datetime(2026, 1, 1, 10, 5, 0))
    db_session.add(scenario)
    # floor_to_timeframe(10:05, "1h") == 10:00 — the candle forming at creation time.
    db_session.add(_kline("BTCUSDT", datetime(2026, 1, 1, 10, 0, 0), high=112, low=98))
    db_session.commit()

    result = resolve_pending_scenarios(db_session, now=datetime(2026, 1, 1, 11, 5, 0))

    assert result.scanned == 1
    assert result.resolved == 1
    assert result.still_pending == 0
    assert result.failed == 0
    reloaded = db_session.query(Scenario).first()
    assert reloaded.status == "hit_target"
    assert reloaded.resolved_at == datetime(2026, 1, 1, 10, 0, 0)


def test_resolve_pending_scenarios_leaves_unresolved_ones_pending(db_session):
    scenario = _pending_scenario(
        created_at=datetime(2026, 1, 1, 10, 5, 0),
        expires_at=datetime(2026, 1, 2, 10, 5, 0),
    )
    db_session.add(scenario)
    db_session.add(_kline("BTCUSDT", datetime(2026, 1, 1, 10, 0, 0), high=105, low=95))
    db_session.commit()

    result = resolve_pending_scenarios(db_session, now=datetime(2026, 1, 1, 11, 5, 0))

    assert result.resolved == 0
    assert result.still_pending == 1
    reloaded = db_session.query(Scenario).first()
    assert reloaded.status == "pending"
    assert reloaded.resolved_at is None


def test_resolve_pending_scenarios_marks_expired(db_session):
    scenario = _pending_scenario(
        created_at=datetime(2026, 1, 1, 10, 5, 0),
        expires_at=datetime(2026, 1, 2, 10, 5, 0),
    )
    db_session.add(scenario)
    db_session.add(_kline("BTCUSDT", datetime(2026, 1, 1, 10, 0, 0), high=105, low=95))
    db_session.commit()

    result = resolve_pending_scenarios(db_session, now=datetime(2026, 1, 3, 0, 0, 0))

    assert result.resolved == 1
    reloaded = db_session.query(Scenario).first()
    assert reloaded.status == "expired"
    assert reloaded.resolved_at == datetime(2026, 1, 2, 10, 5, 0)


def test_resolve_pending_scenarios_ignores_candles_after_expiry(db_session):
    """A candle from after expires_at must never resolve a scenario as a win.

    Without an upper bound on the resolution window, a single pass over a
    backfilled outage gap would report this as hit_target a week late.
    """
    scenario = _pending_scenario(
        created_at=datetime(2026, 1, 1, 10, 5, 0),
        expires_at=datetime(2026, 1, 1, 16, 5, 0),
    )
    db_session.add(scenario)
    # Flat candles from creation through past expiry: neither target nor stop.
    for hour in range(10, 18):
        db_session.add(_kline("BTCUSDT", datetime(2026, 1, 1, hour, 0, 0), high=105, low=95))
    # A week later, price finally clears the 110 target — long after expiry.
    db_session.add(_kline("BTCUSDT", datetime(2026, 1, 8, 10, 0, 0), high=130, low=95))
    db_session.commit()

    result = resolve_pending_scenarios(db_session, now=datetime(2026, 1, 8, 12, 0, 0))

    assert result.resolved == 1
    assert result.failed == 0
    reloaded = db_session.query(Scenario).first()
    assert reloaded.status == "expired"
    assert reloaded.resolved_at == datetime(2026, 1, 1, 16, 5, 0)


def test_resolve_pending_scenarios_defers_a_gapped_window(db_session):
    """A gap in the window makes the true outcome unknowable, so don't guess.

    Subsystem A tolerates permanently unfillable gaps; the missing candle could
    have hit the stop before the visible candle hit the target.
    """
    scenario = _pending_scenario(
        created_at=datetime(2026, 1, 1, 10, 5, 0),
        expires_at=datetime(2026, 1, 2, 10, 5, 0),
    )
    db_session.add(scenario)
    db_session.add(_kline("BTCUSDT", datetime(2026, 1, 1, 10, 0, 0), high=105, low=95))
    # 11:00 is missing.
    db_session.add(_kline("BTCUSDT", datetime(2026, 1, 1, 12, 0, 0), high=112, low=98))
    db_session.commit()

    result = resolve_pending_scenarios(db_session, now=datetime(2026, 1, 1, 13, 5, 0))

    # A deferral, not an error: still_pending, never failed.
    assert result.scanned == 1
    assert result.resolved == 0
    assert result.still_pending == 1
    assert result.failed == 0
    reloaded = db_session.query(Scenario).first()
    assert reloaded.status == "pending"
    assert reloaded.resolved_at is None


def test_resolve_pending_scenarios_resolves_a_contiguous_window(db_session):
    """The gap check must not reject a window that merely spans several candles."""
    scenario = _pending_scenario(
        created_at=datetime(2026, 1, 1, 10, 5, 0),
        expires_at=datetime(2026, 1, 2, 10, 5, 0),
    )
    db_session.add(scenario)
    for hour, high in ((10, 105), (11, 106), (12, 112)):
        db_session.add(_kline("BTCUSDT", datetime(2026, 1, 1, hour, 0, 0), high=high, low=95))
    db_session.commit()

    result = resolve_pending_scenarios(db_session, now=datetime(2026, 1, 1, 13, 5, 0))

    assert result.resolved == 1
    reloaded = db_session.query(Scenario).first()
    assert reloaded.status == "hit_target"
    assert reloaded.resolved_at == datetime(2026, 1, 1, 12, 0, 0)


def test_resolve_pending_scenarios_isolates_a_failing_scenario(db_session, monkeypatch):
    good = _pending_scenario(symbol="BTCUSDT", created_at=datetime(2026, 1, 1, 10, 5, 0))
    bad = _pending_scenario(symbol="ETHUSDT", created_at=datetime(2026, 1, 1, 10, 5, 0))
    db_session.add_all([good, bad])
    db_session.add(_kline("BTCUSDT", datetime(2026, 1, 1, 10, 0, 0), high=112, low=98))
    db_session.commit()

    import src.learning_runner as learning_runner_module

    real_evaluate_outcome = learning_runner_module.evaluate_outcome

    def flaky_evaluate_outcome(direction, target_price, stop_price, expires_at, klines, now):
        if direction == "long" and target_price == Decimal("110") and not klines:
            raise RuntimeError("boom")
        return real_evaluate_outcome(direction, target_price, stop_price, expires_at, klines, now)

    monkeypatch.setattr(learning_runner_module, "evaluate_outcome", flaky_evaluate_outcome)

    result = resolve_pending_scenarios(db_session, now=datetime(2026, 1, 1, 11, 5, 0))

    assert result.scanned == 2
    assert result.resolved == 1  # BTCUSDT, has klines
    assert result.failed == 1  # ETHUSDT, no klines -> triggers the flaky raise
    reloaded_good = db_session.query(Scenario).filter(Scenario.symbol == "BTCUSDT").first()
    assert reloaded_good.status == "hit_target"


def _resolved_scenario(symbol, direction, confidence_score, status):
    now = datetime(2026, 1, 1)
    return Scenario(
        symbol=symbol, direction=direction,
        entry_price=Decimal("100"), target_price=Decimal("110"), stop_price=Decimal("90"),
        expected_return_pct=Decimal("0.1"), confidence_score=confidence_score,
        created_at=now, expires_at=now + timedelta(hours=24),
        status=status, resolved_at=now + timedelta(hours=3),
        calibrated_confidence=confidence_score,  # already calibrated when it was created
    )


def test_calibrate_scenarios_falls_back_to_raw_score_below_min_samples(db_session):
    pending = _pending_scenario(symbol="BTCUSDT", direction="long")
    pending.confidence_score = Decimal("0.65")
    db_session.add(pending)
    db_session.commit()

    result = calibrate_scenarios(db_session)

    assert result.scenarios_updated == 1
    assert result.patterns_with_data == 0
    reloaded = db_session.query(Scenario).first()
    assert reloaded.calibrated_confidence == Decimal("0.65")


def test_calibrate_scenarios_uses_computed_rate_at_min_samples(db_session):
    for _ in range(15):
        db_session.add(_resolved_scenario("ETHUSDT", "long", Decimal("0.65"), "hit_target"))
    for _ in range(5):
        db_session.add(_resolved_scenario("ETHUSDT", "long", Decimal("0.65"), "hit_stop"))
    pending = _pending_scenario(symbol="BTCUSDT", direction="long")
    pending.confidence_score = Decimal("0.65")
    db_session.add(pending)
    db_session.commit()

    result = calibrate_scenarios(db_session)

    assert result.patterns_with_data == 1
    reloaded = db_session.query(Scenario).filter(Scenario.symbol == "BTCUSDT").first()
    assert reloaded.calibrated_confidence == Decimal("15") / Decimal("20")


def test_calibrate_scenarios_does_not_touch_already_calibrated_rows(db_session):
    already = _resolved_scenario("ETHUSDT", "long", Decimal("0.65"), "hit_target")
    already.calibrated_confidence = Decimal("0.42")
    db_session.add(already)
    db_session.commit()

    result = calibrate_scenarios(db_session)

    assert result.scenarios_updated == 0
    reloaded = db_session.query(Scenario).first()
    assert reloaded.calibrated_confidence == Decimal("0.42")


def test_run_learning_cycle_resolves_and_calibrates_end_to_end(db_session):
    """Real klines, real scenario, all the way to a persisted, calibrated row —
    a stub or monkeypatched version of this test would not catch a broken wire
    between resolution and calibration."""
    from src.learning_runner import run_learning_cycle

    for _ in range(15):
        db_session.add(_resolved_scenario("ETHUSDT", "long", Decimal("0.65"), "hit_target"))
    for _ in range(5):
        db_session.add(_resolved_scenario("ETHUSDT", "long", Decimal("0.65"), "hit_stop"))

    scenario = _pending_scenario(symbol="BTCUSDT", direction="long", created_at=datetime(2026, 1, 1, 10, 5, 0))
    scenario.confidence_score = Decimal("0.65")
    db_session.add(scenario)
    db_session.add(_kline("BTCUSDT", datetime(2026, 1, 1, 10, 0, 0), high=112, low=98))
    db_session.commit()

    result = run_learning_cycle(db_session, now=datetime(2026, 1, 1, 11, 5, 0))

    assert result.scanned == 1
    assert result.resolved == 1
    assert result.scenarios_calibrated == 1

    reloaded = db_session.query(Scenario).filter(Scenario.symbol == "BTCUSDT").first()
    assert reloaded.status == "hit_target"
    # Calibration runs BEFORE resolution, so the BTCUSDT scenario is scored
    # against the 20 outcomes that were already known — 15 of 20 — and not
    # against its own, which this same run is about to determine. A score that
    # knows how the scenario turned out is not a prediction.
    assert reloaded.calibrated_confidence == Decimal("15") / Decimal("20")


def test_run_learning_cycle_calibrates_before_resolving(db_session):
    """Guard the ordering itself: a self-resolving scenario must not be in its own pool.

    16/21 here would mean its own outcome leaked into its calibrated_confidence.
    """
    from src.learning_runner import run_learning_cycle

    for _ in range(15):
        db_session.add(_resolved_scenario("ETHUSDT", "long", Decimal("0.65"), "hit_target"))
    for _ in range(5):
        db_session.add(_resolved_scenario("ETHUSDT", "long", Decimal("0.65"), "hit_stop"))

    scenario = _pending_scenario(symbol="BTCUSDT", direction="long", created_at=datetime(2026, 1, 1, 10, 5, 0))
    scenario.confidence_score = Decimal("0.65")
    db_session.add(scenario)
    db_session.add(_kline("BTCUSDT", datetime(2026, 1, 1, 10, 0, 0), high=112, low=98))
    db_session.commit()

    run_learning_cycle(db_session, now=datetime(2026, 1, 1, 11, 5, 0))

    reloaded = db_session.query(Scenario).filter(Scenario.symbol == "BTCUSDT").first()
    assert reloaded.calibrated_confidence != (Decimal("16") / Decimal("21")).quantize(Decimal("0.0001"))
    assert reloaded.calibrated_confidence == Decimal("0.75")


def test_run_learning_cycle_survives_a_resolution_failure(db_session, monkeypatch):
    """Calibration already ran and is not undone by resolution blowing up."""
    import src.learning_runner as learning_runner_module

    pending = _pending_scenario(symbol="BTCUSDT", direction="long")
    pending.confidence_score = Decimal("0.65")
    db_session.add(pending)
    db_session.commit()

    def boom(session, now=None):
        raise RuntimeError("resolution exploded")

    monkeypatch.setattr(learning_runner_module, "resolve_pending_scenarios", boom)

    from src.learning_runner import run_learning_cycle
    result = run_learning_cycle(db_session, now=datetime(2026, 1, 1, 11, 5, 0))

    assert result.scenarios_calibrated == 1
    assert result.resolved == 0
    assert result.failed == 0
    assert db_session.query(Scenario).first().calibrated_confidence == Decimal("0.65")


def test_run_learning_cycle_survives_a_calibration_failure(db_session, monkeypatch):
    scenario = _pending_scenario(symbol="BTCUSDT", direction="long", created_at=datetime(2026, 1, 1, 10, 5, 0))
    db_session.add(scenario)
    db_session.add(_kline("BTCUSDT", datetime(2026, 1, 1, 10, 0, 0), high=112, low=98))
    db_session.commit()

    import src.learning_runner as learning_runner_module

    def boom(session):
        raise RuntimeError("calibration exploded")

    monkeypatch.setattr(learning_runner_module, "calibrate_scenarios", boom)

    from src.learning_runner import run_learning_cycle
    result = run_learning_cycle(db_session, now=datetime(2026, 1, 1, 11, 5, 0))

    # Outcome resolution still completed even though calibration blew up.
    assert result.resolved == 1
    assert result.scenarios_calibrated == 0
    reloaded = db_session.query(Scenario).first()
    assert reloaded.status == "hit_target"
