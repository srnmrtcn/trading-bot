from decimal import Decimal
from datetime import datetime, timedelta
from src.research.funnel import DraftOutcome, resolve_draft, is_locked
from scripts.run_walkforward import score

# Yardimci fonksiyonlar
def _draft(symbol, created_at, expires_at, entry=Decimal('100'), stop=Decimal('90'), target=Decimal('110')):
    from src.scenario_builder import ScenarioDraft
    return ScenarioDraft(
        symbol=symbol,
        direction="long",
        entry_price=entry,
        target_price=target,
        stop_price=stop,
        expected_return_pct=Decimal('0.1'),
        confidence_score=Decimal('0.8'),
        created_at=created_at,
        expires_at=expires_at
    )

def _mumlar(baslangic, adet, high, low):
    step = timedelta(hours=1)
    return [
        {
            'open_time': baslangic + i * step,
            'open': Decimal(str(low + (high - low) * i / (adet - 1))),
            'high': Decimal(str(high)),
            'low': Decimal(str(low)),
            'close': Decimal(str(low + (high - low) * i / (adet - 1))),
            'volume': Decimal('1000')
        }
        for i in range(adet)
    ]

def _kayit(d, high, low):
    mumlar = _mumlar(d.created_at, int((d.expires_at - d.created_at).total_seconds() / 3600) + 3, high, low)
    return (d.symbol, d.created_at, d, mumlar)

def test_resolve_draft_hit_target():
    now = datetime(2023, 1, 1, 12, 0, 0)
    expires_at = now + timedelta(hours=24)
    d = _draft("BTCUSDT", now, expires_at)
    
    # Hedefe ikinci mumda ulasilsin
    future_klines = _mumlar(now, 3, 115, 95)
    future_klines[1]['high'] = Decimal('112')  # Hedefe ulasildi
    future_klines[1]['close'] = Decimal('112')
    
    outcome = resolve_draft(d, future_klines)
    
    assert outcome is not None
    assert outcome.status == "hit_target"
    assert outcome.r_multiple == Decimal('1.0')  # (112-100)/(100-90) = 12/10 = 1.2
    assert outcome.exit_price == Decimal('112')
    assert outcome.resolved_at is not None

def test_resolve_draft_hit_stop():
    now = datetime(2023, 1, 1, 12, 0, 0)
    expires_at = now + timedelta(hours=24)
    d = _draft("BTCUSDT", now, expires_at)
    
    # Stopa ilk mumda vurulsun
    future_klines = _mumlar(now, 3, 115, 95)
    future_klines[0]['low'] = Decimal('88')  # Stopa vuruldu
    
    outcome = resolve_draft(d, future_klines)
    
    assert outcome is not None
    assert outcome.status == "hit_stop"
    assert outcome.r_multiple == Decimal('-1')
    assert outcome.exit_price == Decimal('88')
    assert outcome.resolved_at is not None

def test_resolve_draft_expired():
    now = datetime(2023, 1, 1, 12, 0, 0)
    expires_at = now + timedelta(hours=24)
    d = _draft("BTCUSDT", now, expires_at)
    
    # Suresi dolmus ama hicbir seviyeye degmemis
    future_klines = _mumlar(now, 3, 105, 95)
    
    outcome = resolve_draft(d, future_klines)
    
    assert outcome is not None
    assert outcome.status == "expired"
    assert outcome.r_multiple == Decimal('0.5')  # (102-100)/(100-90) = 2/10 = 0.2
    assert outcome.exit_price == Decimal('102')
    assert outcome.resolved_at is not None

def test_score_unlocks_early():
    now = datetime(2023, 1, 1, 12, 0, 0)
    expires_at = now + timedelta(seconds=24)
    
    # Birinci senaryo
    d1 = _draft("BTCUSDT", now, expires_at)
    future_klines_1 = _mumlar(now, 3, 115, 95)
    future_klines_1[1]['high'] = Decimal('112')
    future_klines_1[1]['close'] = Decimal('112')
    
    # Ikinci senaryo
    d2 = _draft("BTCUSDT", now + timedelta(seconds=5), expires_at)
    future_klines_2 = _mumlar(now, 3, 115, 95)
    future_klines_2[1]['high'] = Decimal('112')
    future_klines_2[1]['close'] = Decimal('112')
    
    drafts = [
        ("BTCUSDT", now, d1, future_klines_1),
        ("BTCUSDT", now + timedelta(seconds=5), d2, future_klines_2)
    ]
    
    results = score(drafts, None, None)
    
    # Her iki senaryo da puanlanmali
    assert len(results) == 2

def test_score_locks_before_resolution():
    now = datetime(2023, 1, 1, 12, 0, 0)
    expires_at = now + timedelta(hours=24)
    
    # Birinci senaryo hedefe onuncu mumda ulasir
    d1 = _draft("BTCUSDT", now, expires_at)
    future_klines_1 = _mumlar(now, 11, 115, 95)
    future_klines_1[10]['high'] = Decimal('112')
    future_klines_1[10]['close'] = Decimal('112')
    
    # Ikinci senaryo birinci senaryonun cozulmesinden once acilir
    d2 = _draft("BTCUSDT", now + timedelta(seconds=2), expires_at)
    future_klines_2 = _mumlar(now, 3, 115, 95)
    
    drafts = [
        ("BTCUSDT", now, d1, future_klines_1),
        ("BTCUSDT", now + timedelta(seconds=2), d2, future_klines_2)
    ]
    
    results = score(drafts, None, None)
    
    # Sadece birinci senaryo puanlanmali
    assert len(results) == 1

def test_score_different_directions():
    now = datetime(2023, 1, 1, 12, 0, 0)
    expires_at = now + timedelta(hours=24)
    
    # Long senaryo
    d1 = _draft("BTCUSDT", now, expires_at, entry=Decimal('100'), stop=Decimal('90'), target=Decimal('110'))
    future_klines_1 = _mumlar(now, 3, 115, 95)
    future_klines_1[1]['high'] = Decimal('112')
    future_klines_1[1]['close'] = Decimal('112')
    
    # Short senaryo
    d2 = _draft("BTCUSDT", now, expires_at, entry=Decimal('100'), stop=Decimal('110'), target=Decimal('90'))
    future_klines_2 = _mumlar(now, 3, 115, 95)
    future_klines_2[1]['low'] = Decimal('98')
    future_klines_2[1]['close'] = Decimal('98')
    
    drafts = [
        ("BTCUSDT", now, d1, future_klines_1),
        ("BTCUSDT", now, d2, future_klines_2)
    ]
    
    results = score(drafts, None, None)
    
    # Her iki senaryo da puanlanmali
    assert len(results) == 2

def test_score_unresolved_locks_until_expiry():
    now = datetime(2023, 1, 1, 12, 0, 0)
    expires_at = now + timedelta(hours=24)
    
    # Birinci senaryo mumlari expires_at'i kapsamaz
    d1 = _draft("BTCUSDT", now, expires_at)
    future_klines_1 = _mumlar(now, 2, 105, 95)  # Yeterli mum yok
    
    # Ikinci senaryo ayni sembol ve yonde suresi dolmadan acilir
    d2 = _draft("BTCUSDT", now + timedelta(seconds=5), expires_at)
    future_klines_2 = _mumlar(now, 3, 115, 95)
    
    drafts = [
        ("BTCUSDT", now, d1, future_klines_1),
        ("BTCUSDT", now + timedelta(seconds=5), d2, future_klines_2)
    ]
    
    results = score(drafts, None, None)
    
    # Ikinci senaryo kilit yuzunden atlanmali
    assert len(results) == 0
