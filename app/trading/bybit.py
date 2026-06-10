import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

BYBIT_INTERVALS = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720",
    "1d": "D", "1w": "W",
}


@dataclass
class TickerInfo:
    symbol: str
    last_price: float
    turnover_24h: float
    volume_24h: float


@dataclass
class Candle:
    start: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: float


class BybitPublicClient:
    """Small public Bybit REST client. No API key required for market data."""

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or getattr(settings, "BYBIT_BASE_URL", "https://api.bybit.com")).rstrip("/")

    def _headers(self) -> Dict[str, str]:
        # Cloudflare/Bybit may return 403 to Python/httpx default user-agent.
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Origin": "https://www.bybit.com",
            "Referer": "https://www.bybit.com/",
        }

    def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        # First try configured proxy/base URL. Then try known public mirrors as fallback.
        bases = [self.base_url]

disable_direct_fallback = bool(
    str(getattr(settings, "BYBIT_DISABLE_DIRECT_FALLBACK", "false")).lower()
    in ("1", "true", "yes")
)

if not disable_direct_fallback:
    for alt in ["https://api.bytick.com", "https://api.bybit.com"]:
        if alt not in bases:
            bases.append(alt)

        last_error = None
        for base in bases:
            url = f"{base}{path}"
            try:
                with httpx.Client(timeout=getattr(settings, "BYBIT_REQUEST_TIMEOUT", 20), headers=self._headers()) as client:
                    resp = client.get(url, params=params)

                if resp.status_code == 403:
                    body = resp.text[:500]
                    logger.warning(f"Bybit 403 via {base}: {body}")
                    last_error = f"403 Forbidden via {base}: {body[:160]}"
                    continue

                resp.raise_for_status()
                data = resp.json()
                if data.get("retCode") != 0:
                    raise RuntimeError(f"Bybit error via {base}: {data.get('retCode')} {data.get('retMsg')}")
                return data.get("result") or {}
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Bybit request failed via {base}: {str(e)[:200]}")
                continue

        raise RuntimeError(f"All Bybit endpoints failed. Last error: {last_error}")

    def get_linear_tickers(self) -> Dict[str, TickerInfo]:
        result = self._get("/v5/market/tickers", {"category": "linear"})
        out: Dict[str, TickerInfo] = {}
        for x in result.get("list", []):
            try:
                symbol = x.get("symbol", "").upper()
                if not symbol.endswith("USDT"):
                    continue
                out[symbol] = TickerInfo(
                    symbol=symbol,
                    last_price=float(x.get("lastPrice") or 0),
                    turnover_24h=float(x.get("turnover24h") or 0),
                    volume_24h=float(x.get("volume24h") or 0),
                )
            except Exception:
                continue
        return out

    def get_ticker(self, symbol: str) -> Optional[TickerInfo]:
        symbol = symbol.upper()
        return self.get_linear_tickers().get(symbol)

    def is_symbol_eligible(self, symbol: str) -> tuple[bool, Optional[TickerInfo], str]:
        symbol = symbol.upper()
        ticker = self.get_ticker(symbol)
        if not ticker:
            return False, None, "symbol not found in Bybit USDT perpetuals"
        min_turnover = float(getattr(settings, "TRADING_MIN_24H_VOLUME", 100000.0))
        if ticker.turnover_24h < min_turnover:
            return False, ticker, f"24h turnover {ticker.turnover_24h:.0f} < {min_turnover:.0f} USDT"
        return True, ticker, "ok"

    def get_klines(self, symbol: str, timeframe: str, limit: int = 120) -> List[Candle]:
        symbol = symbol.upper()
        interval = BYBIT_INTERVALS.get(timeframe.lower())
        if not interval:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        result = self._get("/v5/market/kline", {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "limit": min(max(int(limit), 1), 1000),
        })
        candles: List[Candle] = []
        # Bybit returns newest first; reverse to chronological.
        for row in reversed(result.get("list", [])):
            try:
                candles.append(Candle(
                    start=int(row[0]),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    turnover=float(row[6]),
                ))
            except Exception:
                continue
        return candles


def normalize_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper().replace("/", "").replace("-", "")
    if s and not s.endswith("USDT") and len(s) <= 10:
        s += "USDT"
    return s


def normalize_timeframe(tf: str) -> str:
    tf = (tf or "").strip().lower()
    aliases = {"15": "15m", "60": "1h", "240": "4h", "d": "1d"}
    tf = aliases.get(tf, tf)
    if tf not in BYBIT_INTERVALS:
        raise ValueError(f"Unsupported timeframe: {tf}")
    return tf
