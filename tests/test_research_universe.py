from scripts.fetch_research_data import STABLECOIN_BASES, most_liquid_futures_symbols

def test_most_liquid_futures_symbols_sorting():
    exchange_info = {
        "symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING", "quoteAsset": "USDT", "contractType": "PERPETUAL", "baseAsset": "BTC"},
            {"symbol": "ETHUSDT", "status": "TRADING", "quoteAsset": "USDT", "contractType": "PERPETUAL", "baseAsset": "ETH"},
            {"symbol": "BNBUSDT", "status": "TRADING", "quoteAsset": "USDT", "contractType": "PERPETUAL", "baseAsset": "BNB"},
        ]
    }
    tickers = [
        {"symbol": "BTCUSDT", "quoteVolume": "1000000"},
        {"symbol": "ETHUSDT", "quoteVolume": "2000000"},
        {"symbol": "BNBUSDT", "quoteVolume": "3000000"},
    ]
    result = most_liquid_futures_symbols(exchange_info, tickers, 3)
    assert result == ["BNBUSDT", "ETHUSDT", "BTCUSDT"]

def test_most_liquid_futures_symbols_only_perpetual():
    exchange_info = {
        "symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING", "quoteAsset": "USDT", "contractType": "PERPETUAL", "baseAsset": "BTC"},
            {"symbol": "ETHUSDT", "status": "TRADING", "quoteAsset": "USDT", "contractType": "CURRENT_QUARTER", "baseAsset": "ETH"},
        ]
    }
    tickers = [
        {"symbol": "BTCUSDT", "quoteVolume": "1000000"},
        {"symbol": "ETHUSDT", "quoteVolume": "2000000"},
    ]
    result = most_liquid_futures_symbols(exchange_info, tickers, 3)
    assert result == ["BTCUSDT"]

def test_most_liquid_futures_symbols_only_trading():
    exchange_info = {
        "symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING", "quoteAsset": "USDT", "contractType": "PERPETUAL", "baseAsset": "BTC"},
            {"symbol": "ETHUSDT", "status": "BREAK", "quoteAsset": "USDT", "contractType": "PERPETUAL", "baseAsset": "ETH"},
        ]
    }
    tickers = [
        {"symbol": "BTCUSDT", "quoteVolume": "1000000"},
        {"symbol": "ETHUSDT", "quoteVolume": "2000000"},
    ]
    result = most_liquid_futures_symbols(exchange_info, tickers, 3)
    assert result == ["BTCUSDT"]

def test_most_liquid_futures_symbols_only_usdt():
    exchange_info = {
        "symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING", "quoteAsset": "USDT", "contractType": "PERPETUAL", "baseAsset": "BTC"},
            {"symbol": "ETHUSDC", "status": "TRADING", "quoteAsset": "USDC", "contractType": "PERPETUAL", "baseAsset": "ETH"},
        ]
    }
    tickers = [
        {"symbol": "BTCUSDT", "quoteVolume": "1000000"},
        {"symbol": "ETHUSDC", "quoteVolume": "2000000"},
    ]
    result = most_liquid_futures_symbols(exchange_info, tickers, 3)
    assert result == ["BTCUSDT"]

def test_most_liquid_futures_symbols_stablecoin_filtered():
    exchange_info = {
        "symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING", "quoteAsset": "USDT", "contractType": "PERPETUAL", "baseAsset": "BTC"},
            {"symbol": "FDUSDUSDT", "status": "TRADING", "quoteAsset": "USDT", "contractType": "PERPETUAL", "baseAsset": "FDUSD"},
        ]
    }
    tickers = [
        {"symbol": "BTCUSDT", "quoteVolume": "1000000"},
        {"symbol": "FDUSDUSDT", "quoteVolume": "2000000"},
    ]
    result = most_liquid_futures_symbols(exchange_info, tickers, 3)
    assert result == ["BTCUSDT"]

def test_most_liquid_futures_symbols_volume_missing():
    exchange_info = {
        "symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING", "quoteAsset": "USDT", "contractType": "PERPETUAL", "baseAsset": "BTC"},
            {"symbol": "ETHUSDT", "status": "TRADING", "quoteAsset": "USDT", "contractType": "PERPETUAL", "baseAsset": "ETH"},
        ]
    }
    tickers = [
        {"symbol": "BTCUSDT", "quoteVolume": "1000000"},
    ]
    result = most_liquid_futures_symbols(exchange_info, tickers, 3)
    assert result == ["BTCUSDT"]

def test_most_liquid_futures_symbols_jup_preserved():
    exchange_info = {
        "symbols": [
            {"symbol": "JUPUSDT", "status": "TRADING", "quoteAsset": "USDT", "contractType": "PERPETUAL", "baseAsset": "JUP"},
            {"symbol": "BTCUSDT", "status": "TRADING", "quoteAsset": "USDT", "contractType": "PERPETUAL", "baseAsset": "BTC"},
        ]
    }
    tickers = [
        {"symbol": "JUPUSDT", "quoteVolume": "1000000"},
        {"symbol": "BTCUSDT", "quoteVolume": "2000000"},
    ]
    result = most_liquid_futures_symbols(exchange_info, tickers, 3)
    assert result == ["BTCUSDT", "JUPUSDT"]

def test_most_liquid_futures_symbols_limit_override():
    exchange_info = {
        "symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING", "quoteAsset": "USDT", "contractType": "PERPETUAL", "baseAsset": "BTC"},
            {"symbol": "ETHUSDT", "status": "TRADING", "quoteAsset": "USDT", "contractType": "PERPETUAL", "baseAsset": "ETH"},
        ]
    }
    tickers = [
        {"symbol": "BTCUSDT", "quoteVolume": "1000000"},
        {"symbol": "ETHUSDT", "quoteVolume": "2000000"},
    ]
    result = most_liquid_futures_symbols(exchange_info, tickers, 1)
    assert result == ["ETHUSDT"]
