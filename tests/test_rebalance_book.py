from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import FuturesDailyKline, PortfolioPosition
from src.portfolio.book import open_positions, portfolio_equity
from src.portfolio.config import LEG_EXPOSURE, REBALANCE_DAYS, STARTING_EQUITY
from src.portfolio.rebalancer import RebalanceResult, run_rebalance


BASLANGIC = datetime(2026, 1, 1)
NOW = BASLANGIC + timedelta(days=70)


def _ekle(session, sembol, gun_sayisi, drift, dolar=Decimal(60000000)):
    price = Decimal(100)
    for i in range(gun_sayisi):
        session.add(FuturesDailyKline(symbol=sembol,
                                      open_time=BASLANGIC + timedelta(days=i),
                                      close=price, volume=dolar / price))
        price = price * (Decimal(1) + Decimal(str(drift)))


def _evren(session):
    for i in range(20):
        _ekle(session, 'S%02d' % i, 70, 0.010 - i * 0.0008)
    session.commit()


def test_run_rebalance_creates_one_position(db_session):
    _evren(db_session)
    sonuc = run_rebalance(db_session, NOW)
    assert sonuc.acted is True
    assert sonuc.closed == 0
    assert sonuc.opened == 8
    assert sonuc.universe == 8


def test_run_rebalance_opens_both_long_and_short_positions(db_session):
    _evren(db_session)
    run_rebalance(db_session, NOW)
    pos = open_positions(db_session)
    assert len(pos) == 8
    long_count = sum(1 for p in pos if p.direction == 'long')
    short_count = sum(1 for p in pos if p.direction == 'short')
    assert long_count == 4
    assert short_count == 4


def test_run_rebalance_is_dollar_neutral(db_session):
    _evren(db_session)
    run_rebalance(db_session, NOW)
    pos = open_positions(db_session)
    long_notional = sum(p.entry_price * p.position_size for p in pos if p.direction == 'long')
    short_notional = sum(p.entry_price * p.position_size for p in pos if p.direction == 'short')
    diff = abs(long_notional - short_notional)
    assert diff < Decimal('0.01')


def test_run_rebalance_has_total_exposure_of_twice_the_equity(db_session):
    _evren(db_session)
    run_rebalance(db_session, NOW)
    pos = open_positions(db_session)
    total_notional = sum(p.entry_price * p.position_size for p in pos)
    diff = abs(total_notional - STARTING_EQUITY * LEG_EXPOSURE * 2)
    assert diff < Decimal('0.01')


def test_run_rebalance_opens_strongest_long_and_weakest_short(db_session):
    _evren(db_session)
    run_rebalance(db_session, NOW)
    pos = open_positions(db_session)
    long_symbols = [p.symbol for p in pos if p.direction == 'long']
    short_symbols = [p.symbol for p in pos if p.direction == 'short']
    assert 'S00' in long_symbols
    assert 'S19' in short_symbols


def test_run_rebalance_does_not_open_illiquid_assets(db_session):
    _evren(db_session)
    _ekle(db_session, 'ILLIQ', 70, 0.05, Decimal(1000))
    db_session.commit()
    run_rebalance(db_session, NOW)
    pos = open_positions(db_session)
    illiq_in_positions = [p for p in pos if p.symbol == 'ILLIQ']
    assert len(illiq_in_positions) == 0


def test_run_rebalance_does_nothing_if_not_time_yet(db_session):
    _evren(db_session)
    run_rebalance(db_session, NOW)
    sonuc2 = run_rebalance(db_session, NOW + timedelta(days=REBALANCE_DAYS - 1))
    assert sonuc2.acted is False
    assert sonuc2.opened == 0
    assert sonuc2.closed == 0
    assert len(open_positions(db_session)) == 8


def test_run_rebalance_closes_first_then_opens_new_positions(db_session):
    _evren(db_session)
    run_rebalance(db_session, NOW)
    sonuc2 = run_rebalance(db_session, NOW + timedelta(days=REBALANCE_DAYS))
    assert sonuc2.acted is True
    assert sonuc2.closed == 8


def test_run_rebalance_accounting_for_closed_positions(db_session):
    _evren(db_session)
    run_rebalance(db_session, NOW)
    run_rebalance(db_session, NOW + timedelta(days=REBALANCE_DAYS))
    kapananlar = db_session.query(PortfolioPosition).filter(
        PortfolioPosition.status == 'closed').all()
    assert len(kapananlar) == 8
    for p in kapananlar:
        assert p.realized_pnl is not None
        assert p.gross_pnl is not None


def test_run_rebalance_has_cost_for_closing_and_opening(db_session):
    _evren(db_session)
    run_rebalance(db_session, NOW)
    run_rebalance(db_session, NOW + timedelta(days=REBALANCE_DAYS))
    equity = portfolio_equity(db_session)
    assert equity < STARTING_EQUITY


def test_run_rebalance_does_not_open_positions_if_no_universe(db_session):
    sonuc = run_rebalance(db_session, NOW)
    assert sonuc.acted is False
    assert sonuc.opened == 0
    assert sonuc.universe == 0
    assert len(open_positions(db_session)) == 0


def test_run_rebalance_bos_kosu_haftayi_harcamaz(db_session):
    # Veri gelmediyse denge yapilamaz; o hafta harcanmis SAYILMAZ, yarin
    # tekrar denenir. Aksi halde birkac dakikalik bir veri kesintisi bir
    # haftalik islem kaybina donerdi.
    bos = run_rebalance(db_session, NOW)
    assert bos.acted is False
    _evren(db_session)
    sonuc = run_rebalance(db_session, NOW)
    assert sonuc.acted is True
    assert sonuc.opened == 8


# --- degismeyen isimleri tasima ---------------------------------------------
# Replay'de olculdu: ardisik defterlerde isimlerin %49.4'u ayni bacakta
# kaliyor ve defter hepsini kapatip yeniden aciyordu. Odenen komisyonun
# %48.9'u bu yuzden gereksizdi (~%0.098/hafta, edge'in ~%11'i).


def _gunler_ekle(session, ilk_gun, gun_sayisi):
    """Mevcut 20 sembolun serisini ileri uzatir, egimleri koruyarak."""
    for i in range(20):
        sembol = 'S%02d' % i
        son = (session.query(FuturesDailyKline)
               .filter(FuturesDailyKline.symbol == sembol)
               .order_by(FuturesDailyKline.open_time.desc()).first())
        fiyat, drift = son.close, Decimal(str(0.010 - i * 0.0008))
        for g in range(gun_sayisi):
            fiyat = fiyat * (Decimal(1) + drift)
            session.add(FuturesDailyKline(
                symbol=sembol, open_time=ilk_gun + timedelta(days=g),
                close=fiyat, volume=Decimal(60000000) / fiyat))
    session.commit()


def test_ikinci_dengede_degismeyen_isimler_tasinir(db_session):
    _evren(db_session)
    run_rebalance(db_session, NOW)
    ilk_kimlikler = {p.id for p in open_positions(db_session)}
    assert len(ilk_kimlikler) == 8

    _gunler_ekle(db_session, BASLANGIC + timedelta(days=70), REBALANCE_DAYS + 1)
    sonuc = run_rebalance(db_session, NOW + timedelta(days=REBALANCE_DAYS))

    assert sonuc.acted is True
    assert sonuc.carried == 8
    assert sonuc.closed == 0 and sonuc.opened == 0
    # ayni satirlar, yeniden acilmis kopyalar degil
    assert {p.id for p in open_positions(db_session)} == ilk_kimlikler


def test_tasinan_pozisyon_komisyon_odemez(db_session):
    _evren(db_session)
    run_rebalance(db_session, NOW)
    _gunler_ekle(db_session, BASLANGIC + timedelta(days=70), REBALANCE_DAYS + 1)
    run_rebalance(db_session, NOW + timedelta(days=REBALANCE_DAYS))

    kapanan = (db_session.query(PortfolioPosition)
               .filter(PortfolioPosition.status == 'closed').all())
    assert kapanan == []          # hicbiri kapanmadi -> hicbiri ucret odemedi


def test_defter_hic_tasima_yokken_bile_hafta_harcanir(db_session):
    # Tasima devreye girince "hicbir sey olmadi" korumasi yanlis tetiklenebilir:
    # kapanan 0 + acilan 0 ama 8 isim TASINDI ise hafta gercekten harcandi ve
    # snapshot yazilmali. Yazilmazsa defter her gun yeniden dengelemeye kalkar.
    _evren(db_session)
    run_rebalance(db_session, NOW)
    _gunler_ekle(db_session, BASLANGIC + timedelta(days=70), REBALANCE_DAYS + 1)
    ikinci = run_rebalance(db_session, NOW + timedelta(days=REBALANCE_DAYS))
    assert ikinci.acted is True

    # ertesi gun tekrar denenirse sirasi gelmemis olmali
    ucuncu = run_rebalance(db_session, NOW + timedelta(days=REBALANCE_DAYS + 1))
    assert ucuncu.acted is False
    assert ucuncu.reason == 'not_due'


def test_bar_gelmezse_koruma_hala_calisir(db_session):
    # Barlar hic gelmediginde kapanan da acilan da tasinan da 0 olmali ve
    # hafta harcanmamali.
    sonuc = run_rebalance(db_session, NOW)
    assert sonuc.acted is False
    assert sonuc.reason == 'no_book'
    assert sonuc.carried == 0


def test_defterden_cikan_isim_kapanir_yeni_giren_acilir(db_session):
    _evren(db_session)
    run_rebalance(db_session, NOW)
    tutulan = {(p.symbol, p.direction) for p in open_positions(db_session)}

    # En guclu long ismini ceviriyoruz: siralamadan dusmeli, yerine baskasi
    # girmeli.
    _gunler_ekle(db_session, BASLANGIC + timedelta(days=70), REBALANCE_DAYS + 1)
    en_iyi = sorted(s for s, d in tutulan if d == 'long')[0]
    fiyat = (db_session.query(FuturesDailyKline)
             .filter(FuturesDailyKline.symbol == en_iyi)
             .order_by(FuturesDailyKline.open_time.desc()).first().close)
    for g in range(REBALANCE_DAYS + 1):
        satir = (db_session.query(FuturesDailyKline)
                 .filter(FuturesDailyKline.symbol == en_iyi,
                         FuturesDailyKline.open_time
                         == BASLANGIC + timedelta(days=70 + g)).first())
        if satir is not None:
            fiyat = fiyat * Decimal("0.90")
            satir.close = fiyat
            satir.volume = Decimal(60000000) / fiyat
    db_session.commit()

    sonuc = run_rebalance(db_session, NOW + timedelta(days=REBALANCE_DAYS))
    assert sonuc.acted is True
    assert sonuc.closed >= 1, "defterden dusen isim kapanmali"
    assert sonuc.opened >= 1, "yerine yeni isim acilmali"
    assert sonuc.carried >= 1, "geri kalanlar tasinmali"


def test_boyutlandirma_gerceklesmemis_kari_da_sayar(db_session):
    # Tasinan pozisyonun karini yok sayan bir boyutlandirici yeni ismi
    # oldugundan kucuk acar. Bu, bilesiklenmeyi sessizce yanlis yapan hatadir.
    from src.portfolio.book import marked_equity
    _evren(db_session)
    run_rebalance(db_session, NOW)
    _gunler_ekle(db_session, BASLANGIC + timedelta(days=70), REBALANCE_DAYS + 1)

    kapanislar = {}
    for i in range(20):
        sembol = 'S%02d' % i
        son = (db_session.query(FuturesDailyKline)
               .filter(FuturesDailyKline.symbol == sembol)
               .order_by(FuturesDailyKline.open_time.desc()).first())
        kapanislar[sembol] = son.close
    isaretli = marked_equity(db_session, kapanislar)
    gerceklesmis = portfolio_equity(db_session)
    assert isaretli != gerceklesmis, "test anlamsiz: gerceklesmemis k/z sifir"
