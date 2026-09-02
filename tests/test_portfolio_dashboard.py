from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import PortfolioPosition, PortfolioSnapshot
from src.portfolio.config import STARTING_EQUITY, STRATEGY_VERSION
from src.portfolio.dashboard import book_performance, open_book, portfolio_equity_history

NOW = datetime(2026, 3, 1, 12)


def _snapshot(session, gun, esitlik, surum=STRATEGY_VERSION):
    session.add(PortfolioSnapshot(strategy_version=surum,
                                  as_of=NOW + timedelta(days=gun),
                                  equity=Decimal(esitlik)))


def _pozisyon(session, sembol, yon, durum='open', net=None, surum=STRATEGY_VERSION):
    session.add(PortfolioPosition(
        strategy_version=surum, symbol=sembol, direction=yon,
        entry_price=Decimal(100), position_size=Decimal(2),
        opened_at=NOW, status=durum,
        gross_pnl=None if net is None else Decimal(10),
        fee_cost=None if net is None else Decimal(1),
        funding_cost=None if net is None else Decimal(2),
        realized_pnl=None if net is None else Decimal(net)))


def test_book_performance_bos_veritabani(db_session):
    p = book_performance(db_session)
    assert p.equity == STARTING_EQUITY
    assert p.rebalances == 0
    assert p.last_rebalance is None
    assert p.closed == 0
    assert p.wins == 0
    assert p.gross_pnl == Decimal(0)
    assert p.win_rate == Decimal(0)


def test_book_performance_kapananlari_sayar_ve_maliyetleri_ayri_toplar(db_session):
    _pozisyon(db_session, 'AAAUSDT', 'long', 'closed', net=7)
    _pozisyon(db_session, 'BBBUSDT', 'short', 'closed', net=-3)
    db_session.commit()
    p = book_performance(db_session)
    assert p.closed == 2
    assert p.wins == 1
    assert p.gross_pnl == Decimal(20)
    assert p.fee_cost == Decimal(2)
    assert p.funding_cost == Decimal(4)


def test_book_performance_acik_pozisyonu_sayma(db_session):
    _pozisyon(db_session, 'AAAUSDT', 'long', 'closed', net=7)
    _pozisyon(db_session, 'BBBUSDT', 'long')
    db_session.commit()
    assert book_performance(db_session).closed == 1


def test_book_performance_kazanma_orani(db_session):
    _pozisyon(db_session, 'AAAUSDT', 'long', 'closed', net=5)
    _pozisyon(db_session, 'BBBUSDT', 'long', 'closed', net=5)
    _pozisyon(db_session, 'CCCUSDT', 'long', 'closed', net=-5)
    db_session.commit()
    p = book_performance(db_session)
    assert p.win_rate == Decimal(2) * 100 / Decimal(3)


def test_book_performance_getiri_yuzdesi(db_session):
    _snapshot(db_session, 0, 12000)
    db_session.commit()
    p = book_performance(db_session)
    assert p.equity == Decimal(12000)
    assert p.return_pct == Decimal(20)


def test_book_performance_son_denge_ve_sayi(db_session):
    _snapshot(db_session, 0, 11000)
    _snapshot(db_session, 7, 12000)
    db_session.commit()
    p = book_performance(db_session)
    assert p.rebalances == 2
    assert p.last_rebalance == NOW + timedelta(days=7)


def test_book_performance_baska_surumu_yok_sayar(db_session):
    _snapshot(db_session, 0, 11000, surum='baska')
    _pozisyon(db_session, 'AAAUSDT', 'long', 'closed', net=7, surum='baska')
    db_session.commit()
    p = book_performance(db_session)
    assert p.rebalances == 0
    assert p.closed == 0
    assert p.equity == STARTING_EQUITY


def test_portfolio_equity_history_eskiden_yeniye(db_session):
    _snapshot(db_session, 7, 12000)
    _snapshot(db_session, 0, 11000)
    db_session.commit()
    gecmis = portfolio_equity_history(db_session)
    assert [e for _, e in gecmis] == [Decimal(11000), Decimal(12000)]


def test_portfolio_equity_history_limit_sondan_alir(db_session):
    _snapshot(db_session, 0, 10)
    _snapshot(db_session, 1, 20)
    _snapshot(db_session, 2, 30)
    db_session.commit()
    result = portfolio_equity_history(db_session, limit=2)
    assert [e for _, e in result] == [Decimal(20), Decimal(30)]


def test_open_book_yalnizca_aciklari_yone_ve_sembole_gore_siralı_doner(db_session):
    _pozisyon(db_session, 'ZZZUSDT', 'short')
    _pozisyon(db_session, 'AAAUSDT', 'short')
    _pozisyon(db_session, 'MMMUSDT', 'long')
    _pozisyon(db_session, 'KKKUSDT', 'long', 'closed', net=1)
    db_session.commit()
    result = [(p.direction, p.symbol) for p in open_book(db_session)]
    assert result == [('long', 'MMMUSDT'), ('short', 'AAAUSDT'), ('short', 'ZZZUSDT')]


def test_open_book_baska_surumu_alma(db_session):
    _pozisyon(db_session, 'AAAUSDT', 'long')
    _pozisyon(db_session, 'BBBUSDT', 'long', surum='baska')
    db_session.commit()
    result = [p.symbol for p in open_book(db_session)]
    assert result == ['AAAUSDT']


# --- bacak bazinda ayrim -----------------------------------------------
# Dort yillik olcum defterin iki bacaginin DONUSUMLU calistigini gosterdi:
# 2023-2024'te uzun bacak tasidi ve kisa bacak kaybetti, 2025-2026'da tam
# tersi. Tek bir net rakam bunu gizler - bir bacak kazanip digeri ayni kadar
# kaybederken defter "duz" gorunur ve bu, iki bacagin da olu oldugu durumdan
# ayirt edilemez. Ayni sebeple brut/ucret/funding zaten ayri duruyor.

def _bacak_pozisyonu(session, sembol, yon, brut, ucret, funding):
    session.add(PortfolioPosition(
        strategy_version=STRATEGY_VERSION, symbol=sembol, direction=yon,
        entry_price=Decimal(100), position_size=Decimal(2),
        opened_at=NOW, status='closed',
        gross_pnl=Decimal(brut), fee_cost=Decimal(ucret),
        funding_cost=Decimal(funding),
        realized_pnl=Decimal(brut) - Decimal(ucret) - Decimal(funding)))


def test_book_performance_bacaklari_ayri_toplar(db_session):
    _bacak_pozisyonu(db_session, 'AAAUSDT', 'long', 30, 2, -5)   # funding TAHSIL
    _bacak_pozisyonu(db_session, 'BBBUSDT', 'long', 10, 1, -3)
    _bacak_pozisyonu(db_session, 'CCCUSDT', 'short', -10, 3, 4)  # funding ODENDI
    db_session.commit()
    p = book_performance(db_session)

    assert p.long_leg.closed == 2
    assert p.long_leg.gross_pnl == Decimal(40)
    assert p.long_leg.fee_cost == Decimal(3)
    assert p.long_leg.funding_cost == Decimal(-8)
    assert p.long_leg.net_pnl == Decimal(45)

    assert p.short_leg.closed == 1
    assert p.short_leg.gross_pnl == Decimal(-10)
    assert p.short_leg.net_pnl == Decimal(-17)


def test_book_performance_bacak_toplamlari_genel_toplamla_uyusur(db_session):
    _bacak_pozisyonu(db_session, 'AAAUSDT', 'long', 30, 2, -5)
    _bacak_pozisyonu(db_session, 'CCCUSDT', 'short', -10, 3, 4)
    db_session.commit()
    p = book_performance(db_session)
    # Bir bacak sessizce dusseydi (ornegin beklenmeyen bir direction degeri)
    # panodaki iki satir toplami karta yazilan brut rakamla tutmazdi.
    assert p.long_leg.gross_pnl + p.short_leg.gross_pnl == p.gross_pnl
    assert p.long_leg.fee_cost + p.short_leg.fee_cost == p.fee_cost
    assert p.long_leg.funding_cost + p.short_leg.funding_cost == p.funding_cost
    assert p.long_leg.closed + p.short_leg.closed == p.closed


def test_book_performance_bos_defterde_bacaklar_sifir(db_session):
    p = book_performance(db_session)
    for bacak in (p.long_leg, p.short_leg):
        assert bacak.closed == 0
        assert bacak.gross_pnl == Decimal(0)
        assert bacak.net_pnl == Decimal(0)


def test_book_performance_bacaklar_baska_surumu_almaz(db_session):
    session = db_session
    session.add(PortfolioPosition(
        strategy_version='baska-surum', symbol='ZZZUSDT', direction='long',
        entry_price=Decimal(100), position_size=Decimal(2), opened_at=NOW,
        status='closed', gross_pnl=Decimal(999), fee_cost=Decimal(0),
        funding_cost=Decimal(0), realized_pnl=Decimal(999)))
    session.commit()
    assert book_performance(session).long_leg.gross_pnl == Decimal(0)


# --- gecikmis denge gorunur olsun -------------------------------------------
# Ilk canli gecede is kostu, defteri acamadi, dogru sekilde haftayi harcamadi
# ve geriye bos bir defter birakti. Sayfada bu, "dengeler arasindayiz"den
# ayirt edilemiyordu.

def test_book_performance_son_dengeden_bu_yana_gecen_gun(db_session):
    _snapshot(db_session, 0, 10500)
    db_session.commit()
    p = book_performance(db_session, now=NOW + timedelta(days=3))
    assert p.days_since_rebalance == 3
    assert p.rebalance_overdue is False


def test_book_performance_hic_denge_yoksa_gun_yok(db_session):
    p = book_performance(db_session, now=NOW)
    assert p.days_since_rebalance is None
    assert p.rebalance_overdue is False


def test_book_performance_tam_yedi_gun_gecikmis_sayilmaz(db_session):
    # Denge gecenin bir yarisi kosuyor, sayfa her saat okunabiliyor: tam yedi
    # gun once dengelenmis bir defter gec degil, zamaninda.
    _snapshot(db_session, 0, 10500)
    db_session.commit()
    assert book_performance(db_session, now=NOW + timedelta(days=7)).rebalance_overdue is False


def test_book_performance_sekiz_gun_gecikmis_sayilir(db_session):
    _snapshot(db_session, 0, 10500)
    db_session.commit()
    p = book_performance(db_session, now=NOW + timedelta(days=8))
    assert p.days_since_rebalance == 8
    assert p.rebalance_overdue is True
