from decimal import Decimal, ROUND_HALF_UP

from src.portfolio.momentum import average_rank, percentile_ranks, select_legs


def test_percentile_ranks_simple():
    input_data = {'A': Decimal('0.1'), 'B': Decimal('0.2'), 'C': Decimal('0.3')}
    result = percentile_ranks(input_data)
    expected_a = Decimal(1) / Decimal(3)
    expected_b = Decimal(2) / Decimal(3)
    expected_c = Decimal(1)
    assert result['A'] == expected_a
    assert result['B'] == expected_b
    assert result['C'] == expected_c


def test_percentile_ranks_tie():
    input_data = {'A': Decimal('0.1'), 'B': Decimal('0.1'), 'C': Decimal('0.3')}
    result = percentile_ranks(input_data)
    expected_ab = Decimal('0.5')
    expected_c = Decimal(1)
    assert result['A'] == expected_ab
    assert result['B'] == expected_ab
    assert result['C'] == expected_c


def test_percentile_ranks_empty():
    input_data = {}
    result = percentile_ranks(input_data)
    assert result == {}


def test_percentile_ranks_single():
    input_data = {'A': Decimal('5')}
    result = percentile_ranks(input_data)
    assert result['A'] == Decimal(1)


def test_average_rank_missing_symbol_excluded():
    input_data = {
        7: {'A': Decimal('0.5'), 'B': Decimal('0.1'), 'C': Decimal('-0.2')},
        14: {'A': Decimal('-0.3'), 'B': Decimal('0.4'), 'C': Decimal('0.9'), 'D': Decimal('1')}
    }
    result = average_rank(input_data)
    assert 'D' not in result
    assert len(result) == 3
    assert result['A'] > result['B']
    assert result['B'] > result['C']


def test_average_rank_value():
    input_data = {
        7: {'A': Decimal('0.5'), 'B': Decimal('0.1'), 'C': Decimal('-0.2')},
        14: {'A': Decimal('-0.3'), 'B': Decimal('0.4'), 'C': Decimal('0.9'), 'D': Decimal('1')}
    }
    result = average_rank(input_data)
    assert result['A'] == Decimal('0.625')


def test_average_rank_empty():
    input_data = {}
    result = average_rank(input_data)
    assert result == {}


def test_select_legs_top_and_bottom_selected():
    scores = {'A': Decimal('0.9'), 'B': Decimal('0.8'), 'C': Decimal('0.5'), 'D': Decimal('0.2'), 'E': Decimal('0.1')}
    result = select_legs(scores, Decimal('0.4'))
    assert result == (['A', 'B'], ['E', 'D'])


def test_select_legs_legs_do_not_overlap():
    scores = {'A': Decimal('0.9'), 'B': Decimal('0.8'), 'C': Decimal('0.5'), 'D': Decimal('0.2'), 'E': Decimal('0.1')}
    result = select_legs(scores, Decimal('0.9'))
    longs, shorts = result
    assert len(longs) == 2
    assert len(shorts) == 2
    assert set(longs) & set(shorts) == set()


def test_select_legs_small_universe():
    scores = {'A': Decimal('1')}
    result = select_legs(scores, Decimal('0.4'))
    assert result == ([], [])
    
    result = select_legs({}, Decimal('0.4'))
    assert result == ([], [])


def test_select_legs_at_least_one_name():
    scores = {'A': Decimal('0.9'), 'B': Decimal('0.8'), 'C': Decimal('0.5'), 'D': Decimal('0.2'), 'E': Decimal('0.1')}
    result = select_legs(scores, Decimal('0.01'))
    assert result == (['A'], ['E'])
