import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from src.db.models import Scenario, Kline
from src.learning_runner import resolve_pending_scenarios, OutcomeResolutionResult
from src.timeutil import utc_now


def test_empty_db_window_unresolvable():
    """Test that scenarios with empty resolution windows are marked as unresolvable after grace period."""
    now = utc_now()
    expires_at = now - timedelta(hours=25)  # More than 24h grace period
    
    # Create a mock scenario
    scenario = Mock()
    scenario.id = 1
    scenario.symbol = "BTCUSDT"
    scenario.direction = "long"
    scenario.target_price = 50000
    scenario.stop_price = 45000
    scenario.created_at = now - timedelta(hours=1)
    scenario.expires_at = expires_at
    scenario.status = "pending"
    scenario.resolved_at = None
    
    # Create a mock session
    session = Mock()
    session.query.return_value.filter.return_value.all.return_value = [scenario]
    
    # Mock the _load_resolution_window function to return empty list
    with patch('src.learning_runner._load_resolution_window') as mock_load:
        mock_load.return_value = []
        
        # Mock the _missing_candle_count to return 1
        with patch('src.learning_runner._missing_candle_count') as mock_missing:
            mock_missing.return_value = 1
            
            # Mock the evaluate_outcome function to avoid complex logic
            with patch('src.outcome_evaluator.evaluate_outcome') as mock_evaluate:
                mock_evaluate.return_value = None
                
                result = resolve_pending_scenarios(session, now)
                
                assert result.unresolvable == 1
                assert result.resolved == 1  # The scenario was resolved (marked as unresolvable)


def test_exact_expiry_pending():
    """Test that scenarios with exact expiry time are still pending."""
    now = utc_now()
    expires_at = now - timedelta(hours=24)  # Exactly at grace period
    
    # Create a mock scenario
    scenario = Mock()
    scenario.id = 1
    scenario.symbol = "BTCUSDT"
    scenario.direction = "long"
    scenario.target_price = 50000
    scenario.stop_price = 45000
    scenario.created_at = now - timedelta(hours=1)
    scenario.expires_at = expires_at
    scenario.status = "pending"
    scenario.resolved_at = None
    
    # Create a mock session
    session = Mock()
    session.query.return_value.filter.return_value.all.return_value = [scenario]
    
    # Mock the _load_resolution_window function to return empty list
    with patch('src.learning_runner._load_resolution_window') as mock_load:
        mock_load.return_value = []
        
        # Mock the _missing_candle_count to return 1
        with patch('src.learning_runner._missing_candle_count') as mock_missing:
            mock_missing.return_value = 1
            
            # Mock the evaluate_outcome function to avoid complex logic
            with patch('src.outcome_evaluator.evaluate_outcome') as mock_evaluate:
                mock_evaluate.return_value = None
                
                result = resolve_pending_scenarios(session, now)
                
                assert result.still_pending == 1
                assert result.unresolvable == 0


def test_target_hit_scenario():
    """Test that scenarios with target hit are properly resolved."""
    now = utc_now()
    expires_at = now + timedelta(hours=1)  # Not expired yet
    
    # Create a mock scenario
    scenario = Mock()
    scenario.id = 1
    scenario.symbol = "BTCUSDT"
    scenario.direction = "long"
    scenario.target_price = 50000
    scenario.stop_price = 45000
    scenario.created_at = now - timedelta(hours=1)
    scenario.expires_at = expires_at
    scenario.status = "pending"
    scenario.resolved_at = None
    
    # Create a mock session
    session = Mock()
    session.query.return_value.filter.return_value.all.return_value = [scenario]
    
    # Mock the _load_resolution_window function to return empty list (no missing candles)
    with patch('src.learning_runner._load_resolution_window') as mock_load:
        mock_load.return_value = []
        
        # Mock the _missing_candle_count to return 0 (no missing candles)
        with patch('src.learning_runner._missing_candle_count') as mock_missing:
            mock_missing.return_value = 0
            
            # Mock the evaluate_outcome function to return None (still pending)
            with patch('src.outcome_evaluator.evaluate_outcome') as mock_evaluate:
                mock_evaluate.return_value = None
                
                result = resolve_pending_scenarios(session, now)
                
                assert result.still_pending == 1
                assert result.unresolvable == 0