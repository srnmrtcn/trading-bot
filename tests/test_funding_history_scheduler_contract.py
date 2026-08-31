import pytest
from unittest.mock import Mock, patch

from src.scheduler import run_timeframe_job


def test_hourly_job_calls_refresh_funding_history_before_scenario_generation():
    """Test that refresh_funding_history is called once before scenario generation in hourly job."""
    # Setup mocks
    session_factory = Mock()
    binance_client = Mock()
    now = Mock()

    # Mock the session and its methods
    session = Mock()
    session_factory.return_value = session

    # Mock Symbol query to return some symbols
    symbol1 = Mock()
    symbol1.symbol = "BTCUSDT"
    symbol2 = Mock()
    symbol2.symbol = "ETHUSDT"
    session.query().filter().all.return_value = [symbol1, symbol2]

    # Mock get_resume_point to return a fixed datetime
    with patch("src.scheduler.get_resume_point") as mock_get_resume_point:
        mock_get_resume_point.return_value = now

        # Mock other functions that are called during the job
        with patch("src.scheduler.process_symbol_timeframe") as mock_process, \
             patch("src.scheduler.repair_recent_gaps") as mock_repair, \
             patch("src.scheduler.refresh_regime_source") as mock_refresh_regime, \
             patch("src.scheduler.refresh_funding_rates") as mock_refresh_funding_rates, \
             patch("src.scheduler.run_scenario_generation") as mock_run_scenario_generation, \
             patch("src.scheduler.run_learning_cycle") as mock_run_learning_cycle, \
             patch("src.scheduler.run_paper_trading_cycle") as mock_run_paper_trading_cycle, \
             patch("src.scheduler.record_run"):

            # Mock process_symbol_timeframe to return success
            mock_process.return_value = Mock(error=None)

            # Mock repair_recent_gaps to return 0 gaps filled
            mock_repair.return_value = 0

            # Mock refresh_regime_source to succeed
            mock_refresh_regime.return_value = None

            # Mock refresh_funding_rates to succeed
            mock_refresh_funding_rates.return_value = Mock(updated=1)

            # Mock scenario generation to succeed
            mock_run_scenario_generation.return_value = Mock(generated=1)

            # Mock learning and paper trading to succeed
            mock_run_learning_cycle.return_value = Mock(resolved=1, scenarios_calibrated=1)
            mock_run_paper_trading_cycle.return_value = Mock(closed=1, opened=1)

            # Mock refresh_funding_history to be called
            with patch("src.funding_collector.refresh_funding_history") as mock_refresh_funding_history:
                # Run the job
                run_timeframe_job(session_factory, binance_client, "1h", now=now)

                # Verify that refresh_funding_history was called once with correct arguments
                mock_refresh_funding_history.assert_called_once_with(session, binance_client, now=now)


def test_hourly_job_continues_if_refresh_funding_history_fails():
    """Test that scenario generation still runs even if refresh_funding_history fails."""
    # Setup mocks
    session_factory = Mock()
    binance_client = Mock()
    now = Mock()

    # Mock the session and its methods
    session = Mock()
    session_factory.return_value = session

    # Mock Symbol query to return some symbols
    symbol1 = Mock()
    symbol1.symbol = "BTCUSDT"
    symbol2 = Mock()
    symbol2.symbol = "ETHUSDT"
    session.query().filter().all.return_value = [symbol1, symbol2]

    # Mock get_resume_point to return a fixed datetime
    with patch("src.scheduler.get_resume_point") as mock_get_resume_point:
        mock_get_resume_point.return_value = now

        # Mock other functions that are called during the job
        with patch("src.scheduler.process_symbol_timeframe") as mock_process, \
             patch("src.scheduler.repair_recent_gaps") as mock_repair, \
             patch("src.scheduler.refresh_regime_source") as mock_refresh_regime, \
             patch("src.scheduler.refresh_funding_rates") as mock_refresh_funding_rates, \
             patch("src.scheduler.run_scenario_generation") as mock_run_scenario_generation, \
             patch("src.scheduler.run_learning_cycle") as mock_run_learning_cycle, \
             patch("src.scheduler.run_paper_trading_cycle") as mock_run_paper_trading_cycle, \
             patch("src.scheduler.record_run"):

            # Mock process_symbol_timeframe to return success
            mock_process.return_value = Mock(error=None)

            # Mock repair_recent_gaps to return 0 gaps filled
            mock_repair.return_value = 0

            # Mock refresh_regime_source to succeed
            mock_refresh_regime.return_value = None

            # Mock refresh_funding_rates to succeed
            mock_refresh_funding_rates.return_value = Mock(updated=1)

            # Mock scenario generation to succeed
            mock_run_scenario_generation.return_value = Mock(generated=1)

            # Mock learning and paper trading to succeed
            mock_run_learning_cycle.return_value = Mock(resolved=1, scenarios_calibrated=1)
            mock_run_paper_trading_cycle.return_value = Mock(closed=1, opened=1)

            # Mock refresh_funding_history to raise an exception
            with patch("src.funding_collector.refresh_funding_history") as mock_refresh_funding_history:
                mock_refresh_funding_history.side_effect = Exception("Network error")

                # Run the job
                run_timeframe_job(session_factory, binance_client, "1h", now=now)

                # Verify that scenario generation was still called despite funding history failure
                mock_run_scenario_generation.assert_called_once_with(session, [symbol1.symbol, symbol2.symbol], now=now)