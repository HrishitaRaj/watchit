"""Live market data via Yahoo Finance (direct v8 API — no yfinance dependency).

Every public function returns a well-formed envelope. Nulls are never surfaced to
callers; unavailable data becomes an explicit `status: "unavailable"` envelope so
the UI can render a meaningful state instead of blank fields.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Iterable

import pandas as pd
import requests

log = logging.getLogger("watchit.market")

QUOTE_TTL = int(os.environ.get("QUOTE_CACHE_TTL_SECONDS", "30"))
_YF_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": _USER_AGENT})

# Vendor exchange suffix map. Only NSE / BSE are exercised today; extend here.
_EXCHANGE_SUFFIX = {"XNSE": ".NS", "XBOM": ".BO", "NSE": ".NS", "BSE": ".BO"}
_UNAVAILABLE_FIELDS = {
    "close": 0.0, "previous_close": 0.0, "change_pct": 0.0,
    "volume": 0, "avg_volume_20d": 0, "volume_ratio": 0.0,
    "high_52w": 0.0, "low_52w": 0.0, "momentum_5d_pct": 0.0,
    "sparkline": [],
}


def to_vendor_symbol(symbol: str, exchange: str = "XNSE") -> str:
    """Normalize a user-entered ticker to the vendor's convention (e.g. RELIANCE -> RELIANCE.NS)."""
    symbol = symbol.upper().strip()
    if symbol.endswith((".NS", ".BO")):
        return symbol
    suffix = _EXCHANGE_SUFFIX.get(exchange.upper().strip(), ".NS")
    return f"{symbol}{suffix}"


def _extract_ticker_frame(df: pd.DataFrame, vendor_symbol: str, single: bool) -> pd.DataFrame:
    if single:
        return df.dropna(how="all")
    if vendor_symbol not in df.columns.get_level_values(0):
        return pd.DataFrame()
    return df[vendor_symbol].dropna(how="all")


def _snapshot_from_frame(frame: pd.DataFrame) -> dict | None:
    if frame.empty or len(frame) < 2:
        return None
    last, prev = frame.iloc[-1], frame.iloc[-2]
    close = float(last["Close"])
    prev_close = float(prev["Close"])
    change_pct = ((close - prev_close) / prev_close * 100) if prev_close else 0.0
    volume = int(last["Volume"]) if not pd.isna(last["Volume"]) else 0
    avg_volume = float(frame["Volume"].tail(20).mean() or 0)
    hi_52w = float(frame["High"].tail(252).max())
    lo_52w = float(frame["Low"].tail(252).min())
    tail = frame["Close"].tail(6)
    momentum_5d = (
        float((tail.iloc[-1] - tail.iloc[0]) / tail.iloc[0] * 100)
        if len(tail) >= 2 and tail.iloc[0] else 0.0
    )
    sparkline = [round(float(c), 2) for c in frame["Close"].tail(20).tolist()]
    return {
        "close": round(close, 2),
        "previous_close": round(prev_close, 2),
        "change_pct": round(change_pct, 2),
        "volume": volume,
        "avg_volume_20d": int(avg_volume) if avg_volume else 0,
        "volume_ratio": round(volume / avg_volume, 2) if avg_volume else 0.0,
        "high_52w": round(hi_52w, 2),
        "low_52w": round(lo_52w, 2),
        "momentum_5d_pct": round(momentum_5d, 2),
        "sparkline": sparkline,
        "as_of": frame.index[-1].isoformat(),
    }


# ----------------------------------------------------------------------
# Direct Yahoo Finance v8 API helpers
# ----------------------------------------------------------------------
def _yf_chart(vendor_symbol: str, period: str = "1y", timeout: int = 15) -> dict | None:
    """Fetch raw chart data from Yahoo Finance v8 API. Returns parsed dict or None."""
    now_ts = int(datetime.now(timezone.utc).timestamp())
    period_seconds = {
        "1d": 86400, "5d": 432000, "1mo": 2592000, "3mo": 7776000,
        "6mo": 15552000, "1y": 31536000, "2y": 63072000,
    }
    p1 = now_ts - period_seconds.get(period, 31536000)
    try:
        resp = _SESSION.get(
            f"{_YF_BASE}/{vendor_symbol}",
            params={"period1": p1, "period2": now_ts, "interval": "1d",
                    "events": "div,split"},
            timeout=timeout,
        )
        if resp.status_code == 429:
            log.warning("yfinance_v8 rate-limited: %s", vendor_symbol)
            return None
        if resp.status_code != 200:
            log.warning("yfinance_v8 HTTP %s for %s", resp.status_code, vendor_symbol)
            return None
        result = resp.json().get("chart", {}).get("result", [])
        return result[0] if result else None
    except Exception as err:
        log.warning("yfinance_v8 request failed for %s: %s", vendor_symbol, err)
        return None


def _snapshot_from_chart(chart: dict) -> dict | None:
    """Build a quote snapshot dict from Yahoo Finance v8 chart data.

    Handles the case where the last trading day's close is NaN (market still open
    or the data hasn't been finalized) by falling back to regularMarketPrice from
    the meta block.
    """
    try:
        timestamps = chart.get("timestamp", []) or []
        quote = ((chart.get("indicators") or {}).get("quote") or [{}])[0] or {}
        meta = chart.get("meta") or {}
        if not timestamps or len(timestamps) < 2:
            return None
        n = len(timestamps)
        closes = (quote.get("close") or [])[:n]
        highs = (quote.get("high") or [])[:n]
        lows = (quote.get("low") or [])[:n]
        volumes = (quote.get("volume") or [])[:n]

        df = pd.DataFrame(
            {"Close": closes, "High": highs, "Low": lows, "Volume": volumes},
            index=pd.to_datetime(timestamps, unit="s", utc=True),
        )

        # If last close is NaN (market still open), fall back to regularMarketPrice
        last_close_raw = df["Close"].iloc[-1] if len(df) > 0 else None
        if pd.isna(last_close_raw):
            mkt_price = meta.get("regularMarketPrice")
            mkt_prev = meta.get("chartPreviousClose")
            if mkt_price is not None and mkt_prev is not None:
                close = float(mkt_price)
                prev_close = float(mkt_prev)
            else:
                return None
        else:
            close = float(last_close_raw)
            prev_close = float(df["Close"].iloc[-2])

        change_pct = ((close - prev_close) / prev_close * 100) if prev_close else 0.0

        last_vol_raw = df["Volume"].iloc[-1] if len(df) > 0 else None
        volume = int(last_vol_raw) if not pd.isna(last_vol_raw) else 0
        avg_vol = float(df["Volume"].tail(20).mean() or 0)
        hi_52w = float(meta.get("fiftyTwoWeekHigh") or df["High"].tail(252).max())
        lo_52w = float(meta.get("fiftyTwoWeekLow") or df["Low"].tail(252).min())

        # Momentum: drop NaN closes from the tail
        valid_tail = df["Close"].dropna().tail(6)
        momentum_5d = 0.0
        if len(valid_tail) >= 2 and valid_tail.iloc[0]:
            momentum_5d = float(
                (valid_tail.iloc[-1] - valid_tail.iloc[0]) / valid_tail.iloc[0] * 100
            )

        sparkline = [round(float(c), 2) for c in df["Close"].dropna().tail(20).tolist()]

        mkt_time = meta.get("regularMarketTime")
        as_of = (
            pd.to_datetime(mkt_time, unit="s", utc=True).isoformat()
            if mkt_time else (df.index[-1].isoformat() if len(df) else None)
        )
        return {
            "close": round(close, 2), "previous_close": round(prev_close, 2),
            "change_pct": round(change_pct, 2), "volume": volume,
            "avg_volume_20d": int(avg_vol) if avg_vol else 0,
            "volume_ratio": round(volume / avg_vol, 2) if avg_vol else 0.0,
            "high_52w": round(hi_52w, 2), "low_52w": round(lo_52w, 2),
            "momentum_5d_pct": round(momentum_5d, 2),
            "sparkline": sparkline, "as_of": as_of,
        }
    except Exception as err:
        log.warning("snapshot_from_chart failed: %s", err)
        return None


def _fetch_batch_sync(vendor_symbols: list[str]) -> dict[str, dict]:
    """Direct Yahoo Finance v8 API — one HTTP request per symbol, sequentially.

    The v8 endpoint is more reliable than yfinance's bulk download (which is
    rate-limited and frequently returns NaN). Sequential requests with a small
    delay keep us under the rate limit while still completing a 6-symbol
    dashboard fetch in ~2 seconds.
    """
    out: dict[str, dict] = {}
    for vendor in vendor_symbols:
        try:
            chart = _yf_chart(vendor, period="1y")
            if chart:
                snap = _snapshot_from_chart(chart)
                if snap:
                    out[vendor] = snap
        except Exception as err:  # noqa: BLE001
            log.warning("yfinance_v8_symbol_failed vendor=%s err=%s", vendor, err)
        time.sleep(0.25)  # polite delay between symbols
    return out


async def _fetch_batch(vendor_symbols: list[str]) -> dict[str, dict]:
    if not vendor_symbols:
        return {}
    for attempt in range(3):
        try:
            return await asyncio.to_thread(_fetch_batch_sync, vendor_symbols)
        except Exception as err:  # noqa: BLE001
            log.warning("yfinance_v8_batch_failed attempt=%s err=%s", attempt, err)
            if attempt == 2:
                return {}
            await asyncio.sleep(0.6 * (2 ** attempt))
    return {}


def _cache_updated_at(raw) -> datetime:
    if isinstance(raw, str):
        raw = datetime.fromisoformat(raw)
    return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)


async def get_quotes(db, symbols: Iterable[str], exchange: str = "XNSE") -> dict[str, dict]:
    """Fetch quotes for many symbols. Cache-first, batch-vendor-second.

    Returns a mapping from *user-supplied* uppercase symbol to an envelope:
        {status, as_of, source, close, previous_close, change_pct, volume,
         avg_volume_20d, volume_ratio, high_52w, low_52w, momentum_5d_pct}
    Missing symbols get `status="unavailable"` with zeroed numeric fields.
    """
    now = datetime.now(timezone.utc)
    sym_to_vendor = {s.upper().strip(): to_vendor_symbol(s, exchange) for s in symbols}
    if not sym_to_vendor:
        return {}
    vendor_to_sym = {v: s for s, v in sym_to_vendor.items()}
    envelopes: dict[str, dict] = {}

    async for cached in db.quote_cache.find(
        {"symbol": {"$in": list(sym_to_vendor.keys())}, "exchange": exchange}
    ):
        updated = _cache_updated_at(cached["updated_at"])
        if (now - updated).total_seconds() < QUOTE_TTL:
            envelopes[cached["symbol"]] = {
                "status": "live", "as_of": cached["as_of"],
                "source": "Yahoo Finance (cached)", **cached["data"],
            }

    to_fetch = [v for s, v in sym_to_vendor.items() if s not in envelopes]
    fresh = await _fetch_batch(to_fetch)
    for vendor, data in fresh.items():
        sym = vendor_to_sym[vendor]
        envelopes[sym] = {"status": "live", "as_of": data["as_of"],
                          "source": "Yahoo Finance", **data}
        await db.quote_cache.update_one(
            {"symbol": sym, "exchange": exchange},
            {"$set": {"data": data, "updated_at": now.isoformat(), "as_of": data["as_of"]}},
            upsert=True,
        )

    for sym in sym_to_vendor:
        if sym in envelopes:
            continue
        cached = await db.quote_cache.find_one({"symbol": sym, "exchange": exchange}, {"_id": 0})
        if cached:
            envelopes[sym] = {"status": "stale", "as_of": cached["as_of"],
                              "source": "Yahoo Finance (last cached)", **cached["data"]}
        else:
            envelopes[sym] = {"status": "unavailable", "as_of": None,
                              "source": "Yahoo Finance",
                              "message": "Live quote is temporarily unavailable",
                              **_UNAVAILABLE_FIELDS}
    return envelopes


async def get_indices(names: list[str] | None = None) -> list[dict]:
    """Fetch headline Indian index quotes (Nifty 50, Sensex, Bank Nifty by default).

    Returns a list of `{code, label, value, change_pct, status, sparkline}` dicts.
    Always shape-stable; each entry falls back to `status='unavailable'` on failure.
    """
    default = [
        {"code": "^NSEI",    "label": "NIFTY 50"},
        {"code": "^BSESN",   "label": "SENSEX"},
        {"code": "^NSEBANK", "label": "BANK NIFTY"},
    ]
    targets = default if not names else [{"code": n, "label": n} for n in names]

    async def _gather() -> dict:
        out: dict[str, dict] = {}
        for t in targets:
            chart = await asyncio.to_thread(_yf_chart, t["code"], period="3mo")
            if chart:
                snap = _snapshot_from_chart(chart)
                if snap:
                    out[t["code"]] = snap
            await asyncio.sleep(0.3)
        return out

    try:
        raw = await _gather()
    except Exception as err:  # noqa: BLE001
        log.warning("indices_failed err=%s", err)
        raw = {}
    out: list[dict] = []
    for t in targets:
        snap = raw.get(t["code"])
        if snap:
            out.append({
                "code": t["code"], "label": t["label"],
                "value": snap["close"], "change_pct": snap["change_pct"],
                "sparkline": snap["sparkline"], "as_of": snap["as_of"],
                "status": "live",
            })
        else:
            out.append({
                "code": t["code"], "label": t["label"], "value": 0.0,
                "change_pct": 0.0, "sparkline": [], "as_of": None,
                "status": "unavailable",
            })
    return out


async def resolve_symbol(symbol: str, exchange: str = "XNSE") -> dict | None:
    """Verify a ticker exists on Yahoo Finance and pull display metadata.

    Returns {name, sector, industry} on success or None if not found.
    Uses the direct v8 API for the existence check and the quoteSummary
    endpoint for metadata (best-effort).
    """
    vendor = to_vendor_symbol(symbol, exchange)

    def _sync() -> dict | None:
        # Confirm the symbol exists by fetching chart data
        chart = _yf_chart(vendor, period="5d")
        if not chart:
            return None
        name, sector, industry = symbol.upper(), "General", ""
        try:
            info_resp = _SESSION.get(
                f"https://query1.finance.yahoo.com/v7/finance/quoteSummary/{vendor}",
                params={"modules": "summaryProfile,summaryDetail,defaultKeyStatistics"},
                timeout=10,
            )
            if info_resp.status_code == 200:
                data = info_resp.json()
                result = (data.get("quoteSummary", {}) or {}).get("result", [])
                if result:
                    r = result[0]
                    profile = (r.get("summaryProfile") or {})
                    qt = (r.get("quoteType") or {})
                    name = qt.get("longName") or qt.get("shortName") or name
                    sector = profile.get("sector") or sector
                    industry = profile.get("industry") or ""
        except Exception as err:  # noqa: BLE001
            log.info("resolve_info_soft_failed vendor=%s err=%s", vendor, err)
        return {"name": name, "sector": sector, "industry": industry}

    try:
        return await asyncio.to_thread(_sync)
    except Exception as err:  # noqa: BLE001
        log.warning("resolve_failed vendor=%s err=%s", vendor, err)
        return None


async def get_news(symbol: str, exchange: str = "XNSE", limit: int = 6) -> list[dict]:
    """Return recent news headlines for a symbol. Empty list if unavailable."""
    vendor = to_vendor_symbol(symbol, exchange)

    def _sync() -> list[dict]:
        try:
            resp = _SESSION.get(
                f"https://query1.finance.yahoo.com/v1/finance/search",
                params={"q": vendor, "quotesCount": 0, "newsCount": limit},
                timeout=10,
            )
            if resp.status_code != 200:
                return []
            news = (resp.json() or {}).get("news", []) or []
            items: list[dict] = []
            for entry in news[:limit]:
                items.append({
                    "title": entry.get("title") or "Untitled",
                    "summary": entry.get("summary") or entry.get("description") or "",
                    "source": (entry.get("publisher") or "Market Wire"),
                    "url": entry.get("link") or "",
                    "published_at": str(entry.get("providerPublishTime") or ""),
                })
            return items
        except Exception as err:
            log.warning("news_request_failed vendor=%s err=%s", vendor, err)
            return []

    try:
        return await asyncio.to_thread(_sync)
    except Exception as err:  # noqa: BLE001
        log.warning("news_failed vendor=%s err=%s", vendor, err)
        return []


async def get_history(symbol: str, exchange: str = "XNSE",
                      period: str = "1mo", interval: str = "1d") -> dict:
    """Return an OHLCV history envelope. Always shaped, `candles` may be empty."""
    vendor = to_vendor_symbol(symbol, exchange)

    def _sync() -> list[dict]:
        chart = _yf_chart(vendor, period=period)
        if not chart:
            return []
        timestamps = chart.get("timestamp", []) or []
        quote = ((chart.get("indicators") or {}).get("quote") or [{}])[0] or {}
        n = len(timestamps)
        opens = (quote.get("open") or [])[:n]
        highs = (quote.get("high") or [])[:n]
        lows = (quote.get("low") or [])[:n]
        closes = (quote.get("close") or [])[:n]
        volumes = (quote.get("volume") or [])[:n]
        idx = pd.to_datetime(timestamps, unit="s", utc=True)
        rows: list[dict] = []
        for i, ts in enumerate(idx):
            try:
                rows.append({
                    "t": ts.isoformat(),
                    "o": float(opens[i]) if opens[i] is not None and not pd.isna(opens[i]) else 0.0,
                    "h": float(highs[i]) if highs[i] is not None and not pd.isna(highs[i]) else 0.0,
                    "l": float(lows[i]) if lows[i] is not None and not pd.isna(lows[i]) else 0.0,
                    "c": float(closes[i]) if closes[i] is not None and not pd.isna(closes[i]) else 0.0,
                    "v": int(volumes[i]) if volumes[i] is not None and not pd.isna(volumes[i]) else 0,
                })
            except (IndexError, TypeError):
                continue
        return rows

    try:
        candles = await asyncio.to_thread(_sync)
    except Exception as err:  # noqa: BLE001
        log.warning("history_failed vendor=%s err=%s", vendor, err)
        return {"status": "unavailable", "source": "Yahoo Finance",
                "symbol": symbol.upper(), "exchange": exchange,
                "candles": [], "message": "Historical data unavailable"}
    return {
        "status": "live" if candles else "unavailable",
        "source": "Yahoo Finance",
        "symbol": symbol.upper(), "exchange": exchange,
        "candles": candles,
    }
