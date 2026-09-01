from decimal import Decimal
import pytest
from src.portfolio.accounting import funding_cost, position_pnl, position_sizes, round_trip_cost

def test_position_sizes_esit_notional():
    result = position_sizes(Decimal(10000), ['A','B'], {'A': Decimal(100), 'B': Decimal(50)}, Decimal(1))
    expected = {'A': Decimal(50), 'B': Decimal(100)}
    assert result == expected

def test_position_sizes_maruziyet_olceler():
    result = position_sizes(Decimal(10000), ['A','B'], {'A': Decimal(100), 'B': Decimal(50)}, Decimal('0.5'))
    expected = {'A': Decimal(25), 'B': Decimal(50)}
    assert result == expected

def test_position_sizes_fiyati_olmayani_atlar_ama_bacagi_buyutmez():
    result = position_sizes(Decimal(10000), ['A','B'], {'A': Decimal(100)}, Decimal(1))
    expected = {'A': Decimal(50)}
    assert result == expected

def test_position_sizes_fiyat_sifir():
    result = position_sizes(Decimal(10000), ['A','B'], {'A': Decimal(0), 'B': Decimal(50)}, Decimal(1))
    expected = {'B': Decimal(100)}
    assert result == expected

def test_position_sizes_bossembol_listesi():
    result = position_sizes(Decimal(10000), [], {}, Decimal(1))
    expected = {}
    assert result == expected

def test_position_sizes_sermaye_sifir_ya_da_negatif():
    result = position_sizes(Decimal(0), ['A'], {'A': Decimal(100)}, Decimal(1))
    expected = {}
    assert result == expected
    result = position_sizes(Decimal(-5), ['A'], {'A': Decimal(100)}, Decimal(1))
    assert result == expected

def test_position_pnl_long_kazanir():
    result = position_pnl('long', Decimal(100), Decimal(110), Decimal(2))
    expected = Decimal(20)
    assert result == expected

def test_position_pnl_short_kazanir():
    result = position_pnl('short', Decimal(100), Decimal(90), Decimal(2))
    expected = Decimal(20)
    assert result == expected

def test_position_pnl_long_kaybeder():
    result = position_pnl('long', Decimal(100), Decimal(90), Decimal(2))
    expected = Decimal(-20)
    assert result == expected

def test_position_pnl_bilinmeyen_yon():
    with pytest.raises(ValueError):
        position_pnl('flat', Decimal(100), Decimal(110), Decimal(2))

def test_round_trip_cost_iki_dolus():
    result = round_trip_cost(Decimal(100), Decimal(110), Decimal(2), Decimal('0.0006'))
    expected = Decimal('0.2520')
    assert result == expected

def test_funding_cost_long_oder():
    result = funding_cost('long', Decimal('0.001'), Decimal(100), Decimal(2))
    expected = Decimal('0.200')
    assert result == expected

def test_funding_cost_short_tahsil_eder():
    result = funding_cost('short', Decimal('0.001'), Decimal(100), Decimal(2))
    expected = Decimal('-0.200')
    assert result == expected

def test_funding_cost_negatif_funding_tersine_cevirir():
    result = funding_cost('long', Decimal('-0.001'), Decimal(100), Decimal(2))
    expected = Decimal('-0.200')
    assert result == expected

def test_funding_cost_bilinmeyen_yon():
    with pytest.raises(ValueError):
        funding_cost('flat', Decimal('0.001'), Decimal(100), Decimal(2))
