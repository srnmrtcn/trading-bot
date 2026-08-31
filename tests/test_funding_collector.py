from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import FundingRate, Symbol
from src.funding_collector import refresh_funding_rates

NOW = datetime(2026, 8, 25, 12, 5)


class _FakeBinanceClient:
    def __init__(self, rates=None, boom=False):
        self._rates = rates or {}
        self._boom = boom
        self.calls = 0

    def get_funding_rates(self):
        self.calls += 1
        if self._boom:
            raise RuntimeError("binance down")
        return self._rates


def _symbol(db_session, name, has_futures):
    db_session.add(Symbol(
        symbol=name, base_asset=name[:-4], quote_asset="USDT",
        is_active=True, has_futures_contract=has_futures,
    ))
    db_session.commit()


def test_refresh_funding_rates_stores_rates_only_for_futures_symbols(db_session):
    _symbol(db_session, "BTCUSDT", True)
    _symbol(db_session, "TINYUSDT", False)
    fake = _FakeBinanceClient({"BTCUSDT": Decimal("0.0001"), "TINYUSDT": Decimal("0.0002")})

    result = refresh_funding_rates(db_session, fake, now=NOW)

    assert result.updated == 1
    assert result.missing == 0
    assert db_session.get(FundingRate, "BTCUSDT").funding_rate == Decimal("0.0001")
    assert db_session.get(FundingRate, "TINYUSDT") is None


def test_refresh_funding_rates_costs_exactly_one_api_call(db_session):
    _symbol(db_session, "BTCUSDT", True)
    _symbol(db_session, "ETHUSDT", True)
    fake = _FakeBinanceClient({"BTCUSDT": Decimal("0.0001"), "ETHUSDT": Decimal("0.0002")})

    refresh_funding_rates(db_session, fake, now=NOW)

    assert fake.calls == 1


def test_refresh_funding_rates_updates_the_existing_row_in_place(db_session):
    _symbol(db_session, "BTCUSDT", True)
    refresh_funding_rates(db_session, _FakeBinanceClient({"BTCUSDT": Decimal("0.0001")}), now=NOW)

    later = NOW + timedelta(hours=1)
    refresh_funding_rates(db_session, _FakeBinanceClient({"BTCUSDT": Decimal("0.0009")}), now=later)

    assert db_session.query(FundingRate).count() == 1
    row = db_session.get(FundingRate, "BTCUSDT")
    assert row.funding_rate == Decimal("0.0009")
    assert row.fetched_at == later


def test_refresh_funding_rates_leaves_a_stored_row_alone_when_the_feed_omits_it(db_session):
    _symbol(db_session, "BTCUSDT", True)
    refresh_funding_rates(db_session, _FakeBinanceClient({"BTCUSDT": Decimal("0.0001")}), now=NOW)

    later = NOW + timedelta(hours=1)
    result = refresh_funding_rates(db_session, _FakeBinanceClient({}), now=later)

    assert result.updated == 0
    assert result.missing == 1
    row = db_session.get(FundingRate, "BTCUSDT")
    # Untouched, so the gate's own staleness check is what retires it.
    assert row.funding_rate == Decimal("0.0001")
    assert row.fetched_at == NOW


def test_refresh_funding_rates_propagates_a_binance_failure(db_session):
    _symbol(db_session, "BTCUSDT", True)
    fake = _FakeBinanceClient(boom=True)

    try:
        refresh_funding_rates(db_session, fake, now=NOW)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the Binance failure to propagate to the scheduler")

    assert db_session.query(FundingRate).count() == 0


def test_refresh_funding_rates_warns_when_the_feed_is_completely_empty(db_session, caplog):
    import logging

    _symbol(db_session, "BTCUSDT", True)

    with caplog.at_level(logging.WARNING, logger="funding_collector"):
        refresh_funding_rates(db_session, _FakeBinanceClient({}), now=NOW)

    assert any("no funding rates" in record.getMessage() for record in caplog.records)
