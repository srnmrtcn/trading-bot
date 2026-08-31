from datetime import datetime
from decimal import Decimal

from src.db.models import Scenario
from src.learning_runner import calibrate_scenarios
from src.strategy_version import STRATEGY_VERSION


def _scenario(
    symbol,
    direction="long",
    confidence_score=Decimal("0.6"),
    status="pending",
    calibrated_confidence=None,
    strategy_version=STRATEGY_VERSION,
):
    return Scenario(
        symbol=symbol,
        direction=direction,
        entry_price=Decimal("100"),
        target_price=Decimal("110"),
        stop_price=Decimal("90"),
        expected_return_pct=Decimal("0.1"),
        confidence_score=confidence_score,
        created_at=datetime(2026, 1, 1, 10),
        expires_at=datetime(2026, 1, 2, 10),
        status=status,
        resolved_at=datetime(2026, 1, 1, 13) if status != "pending" else None,
        calibrated_confidence=calibrated_confidence,
        strategy_version=strategy_version,
    )


def test_1_yetersiz_ornek(db_session):
    # 19 guncel 'hit_target' + 1 guncel pending
    for i in range(19):
        db_session.add(_scenario(f"RES{i}USDT", status="hit_target"))
    db_session.add(_scenario("RES19USDT", status="pending"))

    result = calibrate_scenarios(db_session)
    assert result.scenarios_updated == 0
    assert result.patterns_with_data == 0

    pending = db_session.query(Scenario).filter_by(symbol="RES19USDT").first()
    assert pending.calibrated_confidence is None


def test_2_yeterli_ornek(db_session):
    # 8 'hit_target' + 12 'hit_stop' (20 guncel) + 1 guncel pending
    for i in range(8):
        db_session.add(_scenario(f"RES{i}USDT", status="hit_target"))
    for i in range(8, 20):
        db_session.add(_scenario(f"RES{i}USDT", status="hit_stop"))
    db_session.add(_scenario("RES20USDT", status="pending"))

    result = calibrate_scenarios(db_session)
    assert result.scenarios_updated == 1
    assert result.patterns_with_data == 1

    pending = db_session.query(Scenario).filter_by(symbol="RES20USDT").first()
    assert pending.calibrated_confidence == Decimal("8") / Decimal("20")


def test_3_eski_surum_havuza_girmez(db_session):
    # 20 satirin hepsi strategy_version='2026.01.legacy-v0' 'hit_target', + 1 guncel pending
    for i in range(20):
        db_session.add(_scenario(f"RES{i}USDT", status="hit_target", strategy_version="2026.01.legacy-v0"))
    db_session.add(_scenario("RES20USDT", status="pending"))

    result = calibrate_scenarios(db_session)
    assert result.scenarios_updated == 0
    assert result.patterns_with_data == 0

    pending = db_session.query(Scenario).filter_by(symbol="RES20USDT").first()
    assert pending.calibrated_confidence is None


def test_4_eski_surum_pending_hedef_degildir(db_session):
    # 20 guncel 'hit_target' + 1 eski surumlu pending
    for i in range(20):
        db_session.add(_scenario(f"RES{i}USDT", status="hit_target"))
    db_session.add(_scenario("RES20USDT", status="pending", strategy_version="2026.01.legacy-v0"))

    result = calibrate_scenarios(db_session)
    assert result.scenarios_updated == 0
    assert result.patterns_with_data == 1

    pending = db_session.query(Scenario).filter_by(symbol="RES20USDT").first()
    assert pending.calibrated_confidence is None


def test_5_unresolvable_havuza_girmez(db_session):
    # 19 guncel 'hit_target' + 1 guncel 'unresolvable' + 1 guncel pending
    for i in range(19):
        db_session.add(_scenario(f"RES{i}USDT", status="hit_target"))
    db_session.add(_scenario("RES19USDT", status="unresolvable"))
    db_session.add(_scenario("RES20USDT", status="pending"))

    result = calibrate_scenarios(db_session)
    assert result.scenarios_updated == 0
    assert result.patterns_with_data == 0

    pending = db_session.query(Scenario).filter_by(symbol="RES20USDT").first()
    assert pending.calibrated_confidence is None


def test_6_expired_havuzda_kalir(db_session):
    # 15 guncel 'hit_target' + 5 guncel 'expired' + 1 guncel pending
    for i in range(15):
        db_session.add(_scenario(f"RES{i}USDT", status="hit_target"))
    for i in range(15, 20):
        db_session.add(_scenario(f"RES{i}USDT", status="expired"))
    db_session.add(_scenario("RES20USDT", status="pending"))

    result = calibrate_scenarios(db_session)
    assert result.scenarios_updated == 1
    assert result.patterns_with_data == 1

    pending = db_session.query(Scenario).filter_by(symbol="RES20USDT").first()
    assert pending.calibrated_confidence == Decimal("15") / Decimal("20")


def test_7_zaten_kalibre_edilmis_pending_satir_hedef_degildir(db_session):
    # 20 guncel 'hit_target' + calibrated_confidence'i Decimal('0.42') olan guncel bir pending
    for i in range(20):
        db_session.add(_scenario(f"RES{i}USDT", status="hit_target"))
    db_session.add(_scenario("RES20USDT", status="pending", calibrated_confidence=Decimal("0.42")))

    result = calibrate_scenarios(db_session)
    assert result.scenarios_updated == 0
    assert result.patterns_with_data == 1

    pending = db_session.query(Scenario).filter_by(symbol="RES20USDT").first()
    assert pending.calibrated_confidence == Decimal("0.42")
