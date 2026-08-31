from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from binance.client import Client

from src.rate_limit import RateLimitBackoff

# requests has no default timeout, so a Binance call with no response (a
# blocked/throttled outbound IP, a dead TCP connection) would otherwise hang
# forever — silently stalling the whole boot sequence before it ever logs
# anything or opens the HTTP port.
REQUEST_TIMEOUT_SECONDS = 10


class BinanceClient:
    def __init__(self, client=None, backoff: RateLimitBackoff = None):
        self._client = client or Client(
            api_key="", api_secret="",
            requests_params={"timeout": REQUEST_TIMEOUT_SECONDS},
        )
        self._backoff = backoff or RateLimitBackoff()

    def get_active_usdt_symbols(self) -> list:
        info = self._backoff.call(self._client.get_exchange_info)
        results = []
        for entry in info["symbols"]:
            if entry["status"] == "TRADING" and entry["quoteAsset"] == "USDT":
                results.append({
                    "symbol": entry["symbol"],
                    "base_asset": entry["baseAsset"],
                    "quote_asset": entry["quoteAsset"],
                })
        return results

    def get_futures_usdt_symbols(self) -> set:
        """USDT-M perpetual futures kontrati olan sembollerin adlari.

        ADIMLAR:
          1. self._backoff.call(self._client.futures_exchange_info) cagir.
          2. Donen sozlukteki info["symbols"] listesini gez.
          3. status == "TRADING" ve quoteAsset == "USDT" ve
             contractType == "PERPETUAL" olan kayitlarin ["symbol"]
             degerlerinden bir SET olustur ve don.

        Sadece PERPETUAL kontratlarin gate'in kastettigi anlamda funding
        rate'i vardir; ceyreklik olanlar (CURRENT_QUARTER, NEXT_QUARTER)
        vadesinde kapanir.
        """
        info = self._backoff.call(self._client.futures_exchange_info)
        results = set()
        for entry in info["symbols"]:
            if (entry["status"] == "TRADING" and
                    entry["quoteAsset"] == "USDT" and
                    entry["contractType"] == "PERPETUAL"):
                results.add(entry["symbol"])
        return results

    def get_funding_rates(self) -> dict:
        """Her futures sembolu icin en son funding rate, TEK istekte.

        ADIMLAR:
          1. self._backoff.call(self._client.futures_mark_price) cagir -
             PARAMETRESIZ. Parametresiz cagrilinca tum tahtayi (~875 kayit)
             tek listede donduruyor, sembol basina ayri istek YOK.
          2. Bos bir sozluk ac. Donen her kayit icin entry.get("lastFundingRate")
             oku; alan yoksa ya da bos string ise o kaydi ATLA.
          3. Aksi halde sozluge entry["symbol"] -> Decimal(str(ham)) yaz.
             Decimal(float) DEGIL - str uzerinden cevir, yoksa hassasiyet kaybolur.
          4. Sozlugu don.
        """
        mark_price = self._backoff.call(self._client.futures_mark_price)
        results = {}
        for entry in mark_price:
            rate = entry.get("lastFundingRate")
            if not rate:
                continue
            results[entry["symbol"]] = Decimal(str(rate))
        return results

    def get_klines(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
        all_rows = []
        cursor = start_ms
        while cursor < end_ms:
            raw = self._backoff.call(
                self._client.get_klines,
                symbol=symbol,
                interval=interval,
                startTime=cursor,
                endTime=end_ms,
                limit=1000,
            )
            if not raw:
                break
            for entry in raw:
                open_time = datetime.fromtimestamp(entry[0] / 1000, tz=timezone.utc).replace(tzinfo=None)
                all_rows.append({
                    "open_time": open_time,
                    "open": Decimal(str(entry[1])),
                    "high": Decimal(str(entry[2])),
                    "low": Decimal(str(entry[3])),
                    "close": Decimal(str(entry[4])),
                    "volume": Decimal(str(entry[5])),
                })
            next_cursor = raw[-1][0] + 1
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            if len(raw) < 1000:
                break
        return all_rows
