from datetime import datetime, timedelta
from decimal import Decimal
from src.research.funnel import DraftOutcome, is_locked, resolve_draft
from scripts.run_walkforward import score

# Test helper functions
def _draft(now, expires_at):
    from src.scenario_builder import ScenarioDraft
    return ScenarioDraft(
        symbol="BTCUSDT",
        direction="long",
        entry_price=Decimal("100"),
        target_price=Decimal("110"),
        stop_price=Decimal("90"),
        expected_return_pct=Decimal("0.1"),
        confidence_score=Decimal("0.8"),
        created_at=now,
        expires_at=expires_at
    )

def _mumlar(baslangic, adet, high=Decimal('105'), low=Decimal('95')):
    """Mum dizisini olusturur. Her mumun open_time saat basi artar."""
    return [
        {
            'open_time': baslangic + timedelta(hours=i),
            'open': Decimal('100'),
            'high': high,
            'low': low,
            'close': Decimal('100'),
            'volume': Decimal('1000')
        }
        for i in range(adet)
    ]

NOW = datetime(2026, 1, 1, 12)

def test_resolve_draft_hit_target():
    draft = _draft(NOW, NOW + timedelta(hours=5))
    mumlar = _mumlar(NOW, 8)
    mumlar[2]['high'] = Decimal('111')
    outcome = resolve_draft(draft, mumlar)
    assert outcome.status == 'hit_target'
    assert outcome.r_multiple == Decimal('1')
    assert outcome.exit_price == Decimal('110')
    assert outcome.resolved_at == NOW + timedelta(hours=2)

def test_resolve_draft_hit_stop():
    draft = _draft(NOW, NOW + timedelta(hours=5))
    mumlar = _mumlar(NOW, 8)
    mumlar[1]['low'] = Decimal('89')
    outcome = resolve_draft(draft, mumlar)
    assert outcome.status == 'hit_stop'
    assert outcome.r_multiple == Decimal('-1')
    assert outcome.exit_price == Decimal('90')
    assert outcome.resolved_at == NOW + timedelta(hours=1)

def test_resolve_draft_expired():
    draft = _draft(NOW, NOW + timedelta(hours=5))
    mumlar = _mumlar(NOW, 8)
    outcome = resolve_draft(draft, mumlar)
    assert outcome.status == 'expired'
    assert outcome.resolved_at == draft.expires_at

def test_score_unlocks_early():
    d1 = _draft(NOW, NOW + timedelta(hours=5))
    d1_mumlar = _mumlar(NOW, 8)
    d1_mumlar[2]['high'] = Decimal('111')
    
    d2 = _draft(NOW + timedelta(hours=3), NOW + timedelta(hours=8))
    d2_mumlar = _mumlar(NOW + timedelta(hours=3), 8)
    d2_mumlar[2]['high'] = Decimal('111')
    
    results = score([(d1.symbol, NOW, d1, d1_mumlar), (d2.symbol, NOW + timedelta(hours=3), d2, d2_mumlar)], None, None)
    assert len(results) == 2

def test_score_locks_before_resolution():
    d1 = _draft(NOW, NOW + timedelta(hours=5))
    d1_mumlar = _mumlar(NOW, 8)
    
    d2 = _draft(NOW + timedelta(hours=2), NOW + timedelta(hours=7))
    d2_mumlar = _mumlar(NOW + timedelta(hours=2), 8)
    
    results = score([(d1.symbol, NOW, d1, d1_mumlar), (d2.symbol, NOW + timedelta(hours=2), d2, d2_mumlar)], None, None)
    assert len(results) == 1

def test_score_different_directions():
    d1 = _draft(NOW, NOW + timedelta(hours=5))
    d1_mumlar = _mumlar(NOW, 8)
    
    d2 = _draft(NOW, NOW + timedelta(hours=5))
    d2.direction = 'short'
    d2.stop_price = Decimal('110')
    d2.target_price = Decimal('90')
    d2_mumlar = _mumlar(NOW, 8)
    
    results = score([(d1.symbol, NOW, d1, d1_mumlar), (d2.symbol, NOW, d2, d2_mumlar)], None, None)
    assert len(results) == 2

def test_score_unscored_locks_until_expiry():
    d1 = _draft(NOW, NOW + timedelta(hours=24))
    d1_mumlar = _mumlar(NOW, 3)
    
    d2 = _draft(NOW + timedelta(hours=2), NOW + timedelta(hours=7))
    d2_mumlar = _mumlar(NOW + timedelta(hours=2), 8)
    
    results = score([(d1.symbol, NOW, d1, d1_mumlar), (d2.symbol, NOW + timedelta(hours=2), d2, d2_mumlar)], None, None)
    assert len(results) == 0
