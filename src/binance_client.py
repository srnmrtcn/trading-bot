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
        # ping=False: the library pings the API inside __init__ by default,
        # and that call runs before any of main.startup's error handling —
        # a Binance outage at boot would otherwise crash-loop the service.
        self._client = client or Client(
            api_key="", api_secret="",
            requests_params={"timeout": REQUEST_TIMEOUT_SECONDS},
            ping=False,
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

    def get_funding_events(self) -> dict:
        """Her futures sembolu icin funding event bilgisi, TEK istekte.

        ADIMLAR:
          1. self._backoff.call(self._client.futures_mark_price) cagir -
             PARAMETRESIZ. Parametresiz cagrilinca tum tahtayi (~875 kayit)
             tek listede donduruyor, sembol basina ayri istek YOK.
          2. Bos bir sozluk ac. Donen her kayit icin:
             - entry.get("symbol") oku; alan yoksa ya da bos string ise o kaydi ATLA
             - entry.get("nextFundingTime") oku; alan yoksa ya da bos string ise o kaydi ATLA
             - entry.get("lastFundingRate") oku; alan yoksa ya da bos string ise o kaydi ATLA
             - entry.get("markPrice") oku; alan yoksa ya da bos string ise o kaydi ATLA
             - nextFundingTime_ms = int(entry["nextFundingTime"]) donusumunden sonra
               datetime.utcfromtimestamp(nextFundingTime_ms/1000) ile naive utc datetime olustur
             - Decimal(str(lastFundingRate)) ve Decimal(str(markPrice)) donusumu yap
             - results[entry["symbol"]] = (naive_utc_datetime, Decimal(lastFundingRate), Decimal(markPrice))
          3. Sozlugu don.
        """
        mark_price = self._backoff.call(self._client.futures_mark_price)
        results = {}
        for entry in mark_price:
            symbol = entry.get("symbol")
            if not symbol:
                continue
            next_funding_time = entry.get("nextFundingTime")
            if not next_funding_time:
                continue
            last_funding_rate = entry.get("lastFundingRate")
            if not last_funding_rate:
                continue
            mark_price_value = entry.get("markPrice")
            if not mark_price_value:
                continue
            
            next_funding_time_ms = int(next_funding_time)
            funding_time = datetime.utcfromtimestamp(next_funding_time_ms/1000).replace(tzinfo=None)
            
            results[symbol] = (
                funding_time,
                Decimal(str(last_funding_rate)),
                Decimal(str(mark_price_value))
            )
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

    def get_futures_klines(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
        all_rows = []
        seen_open_times = set()
        cursor = start_ms
        while cursor < end_ms:
            raw = self._backoff.call(
                self._client.futures_klines,
                symbol=symbol,
                interval=interval,
                startTime=cursor,
                endTime=end_ms,
                limit=1000,
            )
            if not raw:
                break
            for entry in raw:
                open_time = entry[0]
                if open_time in seen_open_times:
                    continue
                seen_open_times.add(open_time)
                open_time_dt = datetime.fromtimestamp(open_time / 1000, tz=timezone.utc).replace(tzinfo=None)
                all_rows.append({
                    "open_time": open_time_dt,
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
        # Sort by open_time to ensure correct order
        all_rows.sort(key=lambda x: x["open_time"])
        return all_rows