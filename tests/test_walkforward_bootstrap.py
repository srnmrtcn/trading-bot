from datetime import datetime
from scripts.run_walkforward import bootstrap_interval, score, summarise

def test_degenerate_single_block():
    """1 TEK BLOK DEJENERE OLUR (asil kanit): hepsi ayni saatte (dakikalari farkli olabilir) 20 islem verilir, degerleri farklidir. Butun bloklar tek blok oldugu icin her yeniden orneklemede ayni havuz secilir: low == high ve ikisi de listenin ortalamasina esittir (pytest.approx ile). Bagimsiz sayilsaydi aralik daralir ama dejenere OLMAZDI."""
    samples = [(datetime(2026,1,1,9,0), 1.0), (datetime(2026,1,1,9,30), -0.5), (datetime(2026,1,1,9,45), 0.5)]
    result = bootstrap_interval(samples, iterations=1000, seed=0)
    assert result[0] == result[1]
    # low == high ve ikisi de ortalama
    mean_val = sum(r for _, r in samples) / len(samples)
    assert result[0] == mean_val

def test_two_blocks_interval():
    """2 IKI BLOK ARALIGI ACAR: bir saatte hepsi +1.0 olan 5 islem, baska bir saatte hepsi -1.0 olan 5 islem. low < 0 < high ve low ile high birbirinden farklidir."""
    samples = [
        (datetime(2026,1,1,9,0), 1.0),
        (datetime(2026,1,1,9,30), 1.0),
        (datetime(2026,1,1,9,45), 1.0),
        (datetime(2026,1,1,9,15), 1.0),
        (datetime(2026,1,1,9,50), 1.0),
        (datetime(2026,1,1,10,0), -1.0),
        (datetime(2026,1,1,10,30), -1.0),
        (datetime(2026,1,1,10,45), -1.0),
        (datetime(2026,1,1,10,15), -1.0),
        (datetime(2026,1,1,10,50), -1.0),
    ]
    result = bootstrap_interval(samples, iterations=1000, seed=0)
    assert result[0] < 0 < result[1]
    assert result[0] != result[1]

def test_determinism():
    """3 DETERMINIZM: ayni ornek listesiyle ayni seed ile iki kez cagrilinca birebir ayni (low, high) doner."""
    samples = [
        (datetime(2026,1,1,9,0), 1.0),
        (datetime(2026,1,1,9,30), -0.5),
        (datetime(2026,1,1,10,0), -1.0),
        (datetime(2026,1,1,10,30), 0.5),
    ]
    result1 = bootstrap_interval(samples, iterations=1000, seed=0)
    result2 = bootstrap_interval(samples, iterations=1000, seed=0)
    assert result1 == result2

def test_different_seeds():
    """4 SEED FARKI: farkli iki seed ile (ornegin 0 ve 7) iki blok ornegi uzerinde cagrilinca sonuclar birbirinden farkli olabilir; test yalnizca IKISININ DE low <= high oldugunu dogrular (kirilgan esitlik iddiasi yok)."""
    samples = [
        (datetime(2026,1,1,9,0), 1.0),
        (datetime(2026,1,1,9,30), -0.5),
        (datetime(2026,1,1,10,0), -1.0),
        (datetime(2026,1,1,10,30), 0.5),
    ]
    result1 = bootstrap_interval(samples, iterations=1000, seed=0)
    result2 = bootstrap_interval(samples, iterations=1000, seed=7)
    assert result1[0] <= result1[1]
    assert result2[0] <= result2[1]
    # farkli olabilir ama her ikisi de low <= high
    # (ayni ornek listesiyle farkli seedlerle farkli sonuc olabilir)

def test_fewer_than_two_samples():
    """5 IKIDEN AZ ORNEK: bos liste ve tek elemanli liste icin (None, None) doner."""
    assert bootstrap_interval([], iterations=1000, seed=0) == (None, None)
    assert bootstrap_interval([(datetime(2026,1,1,9,0), 1.0)], iterations=1000, seed=0) == (None, None)

def test_interval_contains_mean():
    """6 ARALIK ORTALAMAYI KAPSAR: iki blokluk ornekte low <= butun net_r'lerin ortalamasi <= high."""
    samples = [
        (datetime(2026,1,1,9,0), 1.0),
        (datetime(2026,1,1,9,30), -0.5),
        (datetime(2026,1,1,10,0), -1.0),
        (datetime(2026,1,1,10,30), 0.5),
    ]
    result = bootstrap_interval(samples, iterations=1000, seed=0)
    mean_val = sum(r for _, r in samples) / len(samples)
    assert result[0] <= mean_val <= result[1]

def test_summarise_empty():
    """7 summarise BOS: summarise([]) -> (0, None, None, None) - dort eleman."""
    assert summarise([]) == (0, None, None, None)

def test_summarise_single():
    """8 summarise TEK: tek ikili ile -> n == 1, mean o degere esit, low ve high None."""
    result = summarise([(datetime(2026,1,1,9,0), 1.0)])
    assert result[0] == 1
    assert result[1] == 1.0
    assert result[2] is None
    assert result[3] is None

def test_summarise_multiple():
    """9 summarise COK: iki bloklu ornekte -> n dogru, mean net_r ortalamasi, low ve high None DEGIL ve low <= mean <= high."""
    samples = [
        (datetime(2026,1,1,9,0), 1.0),
        (datetime(2026,1,1,9,30), -0.5),
        (datetime(2026,1,1,10,0), -1.0),
        (datetime(2026,1,1,10,30), 0.5),
    ]
    result = summarise(samples)
    assert result[0] == 4
    mean_val = sum(r for _, r in samples) / len(samples)
    assert result[1] == mean_val
    assert result[2] is not None
    assert result[3] is not None
    assert result[2] <= mean_val <= result[3]
