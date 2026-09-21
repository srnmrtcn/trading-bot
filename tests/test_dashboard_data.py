from datetime import datetime, timedelta
from decimal import Decimal

from src.dashboard_data import (
    equity_sparkline_points,
    get_equity_summary,
    get_open_positions,
    get_recent_scenarios,
    get_system_health,
)
from src.db.models import FetchLog, PaperPosition, Scenario
from src.paper_trading_config import STARTING_EQUITY
from src.strategy_version import STRATEGY_VERSION


def _log(finished_at):
    return FetchLog(
        symbol="BTCUSDT", timeframe="1h", status="success",
        started_at=finished_at, finished_at=finished_at,
    )


def _scenario(id_, symbol="BTCUSDT", created_at=None, status="pending",
              calibrated_confidence=None, confidence_score=Decimal("0.7")):
    created_at = created_at or datetime(2026, 1, 1)
    return Scenario(
        id=id_, symbol=symbol, direction="long",
        entry_price=Decimal("100"), target_price=Decimal("110"), stop_price=Decimal("90"),
        expected_return_pct=Decimal("0.1"), confidence_score=confidence_score,
        created_at=created_at, expires_at=created_at + timedelta(hours=24),
        status=status, calibrated_confidence=calibrated_confidence,
        strategy_version=STRATEGY_VERSION,
    )


def _closed_position(scenario_id, symbol, closed_at, equity_after):
    return PaperPosition(
        scenario_id=scenario_id, symbol=symbol, direction="long",
        entry_price=Decimal("100"), stop_price=Decimal("90"), target_price=Decimal("110"),
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=closed_at - timedelta(hours=1), status="closed",
        closed_at=closed_at, exit_price=Decimal("110"), realized_pnl=equity_after - STARTING_EQUITY,
        equity_before=STARTING_EQUITY, equity_after=equity_after,
        strategy_version=STRATEGY_VERSION,
    )


def _open_position(scenario_id, symbol, opened_at):
    return PaperPosition(
        scenario_id=scenario_id, symbol=symbol, direction="long",
        entry_price=Decimal("100"), stop_price=Decimal("90"), target_price=Decimal("110"),
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=opened_at, status="open",
        strategy_version=STRATEGY_VERSION,
    )


# --- get_system_health ---

def test_get_system_health_reports_stopped_when_no_runs_exist(db_session):
    health = get_system_health(db_session)
    assert health.last_activity is None
    assert health.status == "stopped"


def test_get_system_health_reports_healthy_for_recent_activity(db_session):
    now = datetime(2026, 1, 1, 12, 0)
    db_session.add(_log(now - timedelta(minutes=10)))
    db_session.commit()
    assert get_system_health(db_session, now=now).status == "healthy"


def test_get_system_health_reports_delayed_between_thresholds(db_session):
    now = datetime(2026, 1, 1, 12, 0)
    db_session.add(_log(now - timedelta(minutes=91)))
    db_session.commit()
    assert get_system_health(db_session, now=now).status == "delayed"


def test_get_system_health_reports_stopped_past_four_hours(db_session):
    now = datetime(2026, 1, 1, 12, 0)
    db_session.add(_log(now - timedelta(hours=4, minutes=1)))
    db_session.commit()
    assert get_system_health(db_session, now=now).status == "stopped"


def test_get_system_health_uses_the_most_recent_run(db_session):
    now = datetime(2026, 1, 1, 12, 0)
    db_session.add(_log(now - timedelta(hours=5)))
    db_session.add(_log(now - timedelta(minutes=5)))
    db_session.commit()
    assert get_system_health(db_session, now=now).status == "healthy"


# --- get_equity_summary ---

def test_get_equity_summary_returns_starting_equity_with_no_history(db_session):
    summary = get_equity_summary(db_session)
    assert summary.current == STARTING_EQUITY
    assert summary.history == []


def test_get_equity_summary_returns_current_equity_and_ordered_history(db_session):
    t0 = datetime(2026, 1, 1)
    db_session.add(_scenario(1, created_at=t0))
    db_session.add(_scenario(2, created_at=t0 + timedelta(hours=1)))
    db_session.commit()
    db_session.add(_closed_position(2, "BTCUSDT", t0 + timedelta(hours=2), Decimal("10200")))
    db_session.add(_closed_position(1, "BTCUSDT", t0 + timedelta(hours=1), Decimal("10100")))
    db_session.commit()

    summary = get_equity_summary(db_session)

    assert summary.current == Decimal("10200")
    assert [equity for _, equity in summary.history] == [Decimal("10100"), Decimal("10200")]


def test_get_equity_summary_caps_history_at_50_points(db_session):
    t0 = datetime(2026, 1, 1)
    for i in range(60):
        scenario_id = i + 1
        db_session.add(_scenario(scenario_id, created_at=t0 + timedelta(hours=i)))
    db_session.commit()
    for i in range(60):
        db_session.add(_closed_position(i + 1, "BTCUSDT", t0 + timedelta(hours=i), STARTING_EQUITY + i))
    db_session.commit()

    summary = get_equity_summary(db_session)

    assert len(summary.history) == 50
    assert summary.history[-1][1] == STARTING_EQUITY + 59


# --- equity_sparkline_points ---

def test_equity_sparkline_points_returns_none_for_fewer_than_two_points():
    assert equity_sparkline_points([]) is None
    assert equity_sparkline_points([(datetime(2026, 1, 1), Decimal("10000"))]) is None


def test_equity_sparkline_points_scales_into_the_given_box():
    history = [
        (datetime(2026, 1, 1), Decimal("100")),
        (datetime(2026, 1, 2), Decimal("200")),
    ]
    assert equity_sparkline_points(history, width=100, height=50) == "0.0,50.0 100.0,0.0"


def test_equity_sparkline_points_handles_flat_history():
    history = [
        (datetime(2026, 1, 1), Decimal("100")),
        (datetime(2026, 1, 2), Decimal("100")),
    ]
    # No spread between low and high: both points sit on the bottom edge.
    assert equity_sparkline_points(history, width=100, height=50) == "0.0,50.0 100.0,50.0"


# --- get_open_positions ---

def test_get_open_positions_returns_only_open_ones_newest_first(db_session):
    db_session.add(_scenario(1, symbol="BTCUSDT"))
    db_session.add(_scenario(2, symbol="ETHUSDT", created_at=datetime(2026, 1, 1, 1)))
    db_session.add(_scenario(3, symbol="BNBUSDT", created_at=datetime(2026, 1, 1, 2)))
    db_session.commit()
    db_session.add(_open_position(1, "BTCUSDT", datetime(2026, 1, 1, 0)))
    db_session.add(_open_position(2, "ETHUSDT", datetime(2026, 1, 1, 2)))
    db_session.add(_closed_position(3, "BNBUSDT", datetime(2026, 1, 1, 3), Decimal("10100")))
    db_session.commit()

    positions = get_open_positions(db_session)

    assert [position.symbol for position in positions] == ["ETHUSDT", "BTCUSDT"]


# --- get_recent_scenarios ---

def test_get_recent_scenarios_returns_newest_first_and_respects_limit(db_session):
    for i in range(3):
        db_session.add(_scenario(i + 1, symbol=f"SYM{i}", created_at=datetime(2026, 1, 1, i)))
    db_session.commit()

    scenarios = get_recent_scenarios(db_session, limit=2)

    assert [scenario.symbol for scenario in scenarios] == ["SYM2", "SYM1"]

def _hatali_log(finished_at):
    return FetchLog(
        symbol="BTCUSDT", timeframe="1h", status="error",
        started_at=finished_at, finished_at=finished_at,
        error_message="APIError: -1003",
    )


def test_health_yalnizca_hata_satiri_varken_saglikli_demez(db_session):
    """Canlilik ile veri sagligi ayri sorular.

    Eski sorgu en yeni `finished_at` satirini aliyor, `status`a bakmiyordu.
    Binance her istegi reddetse bile dongu donmeye ve her sembol icin satir
    yazmaya devam eder -- hepsi hata. Servis "healthy" gorunur, veri akmaz.
    Bu test o suzgeci civiliyor: kaldirilirsa burada duser.
    """
    simdi = datetime(2026, 1, 1, 12, 0)
    db_session.add(_log(simdi - timedelta(hours=9)))     # eski ama basarili
    db_session.add(_hatali_log(simdi - timedelta(minutes=2)))   # yeni ama hata
    db_session.commit()

    saglik = get_system_health(db_session, now=simdi)

    assert saglik.status == "stopped"
    assert saglik.last_activity == simdi - timedelta(hours=9)


def test_health_hicbir_basarili_satir_yokken_durdu_der(db_session):
    simdi = datetime(2026, 1, 1, 12, 0)
    db_session.add(_hatali_log(simdi - timedelta(minutes=1)))
    db_session.commit()

    saglik = get_system_health(db_session, now=simdi)

    assert saglik.status == "stopped"
    assert saglik.last_activity is None


def test_equity_grafigi_eski_surumun_pozisyonlarini_saymaz(db_session):
    """Guncel equity `paper_equity` uzerinden zaten surum suzuyordu; grafik
    suzmuyordu. Ikisi farkli populasyondan gelince rakam ile egri birbirini
    tutmuyordu."""
    db_session.add(_scenario(1))
    db_session.add(_scenario(2, symbol="ESKIUSDT"))
    db_session.commit()
    yeni = _closed_position(1, "BTCUSDT", datetime(2026, 1, 2), Decimal("10500"))
    eski = _closed_position(2, "ESKIUSDT", datetime(2026, 1, 3), Decimal("99999"))
    eski.strategy_version = "2020.01.cok-eski"
    db_session.add(yeni)
    db_session.add(eski)
    db_session.commit()

    ozet = get_equity_summary(db_session)

    assert [equity for _, equity in ozet.history] == [Decimal("10500")]


def test_equity_grafigi_equity_after_bos_satiri_atlar(db_session):
    """equity_after NULL olan kapanmis tek bir satir sparkline'da float(None)
    ile patliyor ve tum dashboard'i hata sayfasina dusuruyordu."""
    db_session.add(_scenario(1))
    db_session.add(_scenario(2, symbol="ETHUSDT"))
    db_session.commit()
    saglam = _closed_position(1, "BTCUSDT", datetime(2026, 1, 2), Decimal("10500"))
    bozuk = _closed_position(2, "ETHUSDT", datetime(2026, 1, 3), Decimal("10600"))
    bozuk.equity_after = None
    db_session.add(saglam)
    db_session.add(bozuk)
    db_session.commit()

    ozet = get_equity_summary(db_session)

    assert [equity for _, equity in ozet.history] == [Decimal("10500")]
    assert equity_sparkline_points(ozet.history) is None  # tek nokta, cizgi yok
