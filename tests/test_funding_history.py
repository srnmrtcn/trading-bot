import pytest
from decimal import Decimal
from datetime import datetime

from src.db.models import FundingRateHistory, Symbol


def test_record_funding_event_new_entry(db_session):
    """record_funding_event yeni satir ekler ve True doner (commit sonrasi tabloda 1 satir)"""
    raise NotImplementedError


def test_record_funding_event_duplicate_entry(db_session):
    """Ayni (symbol, funding_time) ikinci kez cagrilinca False doner ve satir sayisi artmaz"""
    raise NotImplementedError


def test_record_funding_event_no_commit(db_session):
    """record_funding_event commit etmez: cagri sonrasi session.rollback() ile satir kaybolur"""
    raise NotImplementedError


def test_funding_events_between_boundaries(db_session):
    """funding_events_between sinirlari: funding_time == start olan olay DISARIDA, funding_time == end olan olay ICERIDE"""
    raise NotImplementedError


def test_funding_events_between_ordering(db_session):
    """funding_events_between funding_time'a gore ARTAN sirali doner"""
    raise NotImplementedError


def test_funding_events_between_isolation(db_session):
    """funding_events_between baska sembolun satirlarini vermez; kayit yoksa bos liste"""
    raise NotImplementedError


def test_refresh_funding_history_new_entries(db_session):
    """refresh_funding_history: has_futures_contract True iki sembol, feed'de ikisi de var -> 2 doner ve tabloda iki satir olur"""
    raise NotImplementedError


def test_refresh_funding_history_duplicate_handling(db_session):
    """refresh_funding_history ayni olayi ikinci kez isleyince 0 doner ve satir sayisi artmaz"""
    raise NotImplementedError


def test_refresh_funding_history_filtering(db_session):
    """refresh_funding_history feed'de olmayan sembolu atlar; has_futures_contract False/None sembol icin satir yazmaz"""
    raise NotImplementedError


def test_refresh_funding_history_skip_nulls(db_session):
    """refresh_funding_history funding_time ya da mark_price None olan kaydi atlar"""
    raise NotImplementedError
