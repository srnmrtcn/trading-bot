"""Senaryo yolu kapaliyken saatlik is yalnizca defterin ihtiyacini yapar.

29 Eylul 2026: senaryo/paper yolu haftalardir sifir sinyal uretiyor, xsec
kurali on-kayitli testte kaldi. Karar: olu yol kapatilir, veri + defter
altyapisi kalir. Anahtar SCENARIO_PATH_ENABLED; varsayilan ACIK, yani bu
dosyadaki testler disinda hicbir sey davranis degistirmez.
"""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from src.config import scenario_path_enabled
from src.dashboard_data import get_system_health
from src.db.models import FetchLog, FundingRateHistory, Kline, Symbol
from src.scheduler import run_timeframe_job

NOW = datetime(2026, 9, 29, 13, 5)


class _Sayac:
    def __init__(self, fon_patlasin=False):
        self.kline_cagri = 0
        self.fon_patlasin = fon_patlasin

    def get_klines(self, *a, **k):
        self.kline_cagri += 1
        return []

    def get_funding_rates(self):
        return {"BTCUSDT": Decimal("0.0001")}

    def get_funding_history(self):
        if self.fon_patlasin:
            raise RuntimeError("binance cevap vermedi")
        return [("BTCUSDT", datetime(2026, 9, 29, 8), Decimal("0.0001"), Decimal("80000"))]


def _sembol(db):
    db.add(Symbol(symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT",
                  is_active=True, has_futures_contract=True))
    db.commit()


@pytest.mark.parametrize("deger,beklenen", [
    (None, True), ("1", True), ("true", True), ("acik", True),
    ("0", False), ("false", False), ("no", False), ("off", False), ("kapali", False),
])
def test_anahtar_okunur_varsayilan_acik(monkeypatch, deger, beklenen):
    if deger is None:
        monkeypatch.delenv("SCENARIO_PATH_ENABLED", raising=False)
    else:
        monkeypatch.setenv("SCENARIO_PATH_ENABLED", deger)
    assert scenario_path_enabled() is beklenen


def test_kapaliyken_saatlik_is_mum_cekmez_ama_funding_gecmisini_yazar(db_session):
    _sembol(db_session)
    istemci = _Sayac()
    run_timeframe_job(lambda: db_session, istemci, "1h", now=NOW, scenario_path=False)

    assert istemci.kline_cagri == 0, "olu yol icin 1h mum cekilmemeli"
    assert db_session.query(Kline).count() == 0
    assert db_session.query(FundingRateHistory).count() == 1, "defter funding gecmisine muhtac"


def test_kapaliyken_health_canli_kalir(db_session):
    """/health fetch_log'dan besleniyor. Mum dongusu kapaninca satir yazilmazsa
    birkac saat sonra servis 503 verir -- Railway saglik kontrolunu de bozar.
    Defter modunda saatlik funding adimi kendi satirini yazar."""
    _sembol(db_session)
    run_timeframe_job(lambda: db_session, _Sayac(), "1h", now=NOW, scenario_path=False)

    satir = db_session.query(FetchLog).one()
    assert satir.status == "success"
    assert get_system_health(db_session, now=NOW + timedelta(minutes=5)).status == "healthy"


def test_kapaliyken_funding_patlarsa_health_bunu_gizlemez(db_session):
    _sembol(db_session)
    run_timeframe_job(lambda: db_session, _Sayac(fon_patlasin=True), "1h", now=NOW, scenario_path=False)

    assert db_session.query(FetchLog).one().status == "error"
    assert get_system_health(db_session, now=NOW + timedelta(minutes=5)).status != "healthy"


def test_kapaliyken_gunluk_spot_is_hic_calismaz(db_session):
    _sembol(db_session)
    istemci = _Sayac()
    run_timeframe_job(lambda: db_session, istemci, "1d", now=NOW, scenario_path=False)

    assert istemci.kline_cagri == 0
    assert db_session.query(FetchLog).count() == 0
