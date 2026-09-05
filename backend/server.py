"""WATCHIT! API — thin FastAPI application.

Domain logic lives in `market.py` (data), `attention.py` (scoring) and
`snapshot.py` (memory). This module wires them into HTTP routes.
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
import requests
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pathlib import Path
from pydantic import BaseModel, EmailStr, Field
from pymongo import ReturnDocument

load_dotenv(Path(__file__).parent / ".env")

import alerts as alerts_module  # noqa: E402
import attention  # noqa: E402
import digest as digest_module  # noqa: E402
import market  # noqa: E402
import snapshot  # noqa: E402
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # noqa: E402
from apscheduler.triggers.cron import CronTrigger  # noqa: E402

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("watchit.api")

client = AsyncIOMotorClient(os.environ["MONGO_URL"])
db = client[os.environ["DB_NAME"]]
app = FastAPI(title="WATCHIT! API")
api = APIRouter(prefix="/api")

JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET must be configured")

# ---- static symbol universe (name + sector metadata) --------------------------
SUPPORTED = {
    "RELIANCE": {"name": "Reliance Industries", "sector": "Energy"},
    "HAL":      {"name": "Hindustan Aeronautics", "sector": "Defence"},
    "BEL":      {"name": "Bharat Electronics", "sector": "Defence"},
    "TCS":      {"name": "Tata Consultancy Services", "sector": "Technology"},
    "INFY":     {"name": "Infosys", "sector": "Technology"},
    "HDFCBANK": {"name": "HDFC Bank", "sector": "Financials"},
    "ICICIBANK":{"name": "ICICI Bank", "sector": "Financials"},
    "SBIN":     {"name": "State Bank of India", "sector": "Financials"},
    "TATAMOTORS":{"name": "Tata Motors", "sector": "Automotive"},
    "M&M":      {"name": "Mahindra & Mahindra", "sector": "Automotive"},
    "MARUTI":   {"name": "Maruti Suzuki", "sector": "Automotive"},
    "ITC":      {"name": "ITC", "sector": "FMCG"},
    "HINDUNILVR":{"name": "Hindustan Unilever", "sector": "FMCG"},
    "SUNPHARMA":{"name": "Sun Pharmaceuticals", "sector": "Pharma"},
    "BHARTIARTL":{"name": "Bharti Airtel", "sector": "Telecom"},
    "LT":       {"name": "Larsen & Toubro", "sector": "Infrastructure"},
    "AXISBANK": {"name": "Axis Bank", "sector": "Financials"},
    "KOTAKBANK":{"name": "Kotak Mahindra Bank", "sector": "Financials"},
    "WIPRO":    {"name": "Wipro", "sector": "Technology"},
    "ADANIENT": {"name": "Adani Enterprises", "sector": "Energy"},
}

# ---- models -------------------------------------------------------------------
class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    name: Optional[str] = Field(default="Investor", max_length=80)


class WatchlistInput(BaseModel):
    name: str = Field(min_length=1, max_length=50)


class StockInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=30)
    exchange: str = Field(default="XNSE", min_length=2, max_length=10)


class AlertInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=30)
    threshold: int = Field(ge=0, le=100, default=70)
    email: EmailStr


class AlertPatch(BaseModel):
    threshold: Optional[int] = Field(default=None, ge=0, le=100)
    active: Optional[bool] = None
    email: Optional[EmailStr] = None


# ---- auth helpers -------------------------------------------------------------
def public_user(doc: dict) -> dict:
    return {"user_id": doc["user_id"], "email": doc["email"], "name": doc.get("name", "Investor")}


def issue_token(user_id: str) -> str:
    return jwt.encode(
        {"sub": user_id, "exp": datetime.now(timezone.utc) + timedelta(days=7)},
        JWT_SECRET, algorithm="HS256",
    )


def set_session(response: Response, user_id: str) -> None:
    response.set_cookie("access_token", issue_token(user_id),
                        httponly=True, secure=True, samesite="none",
                        max_age=7 * 24 * 3600, path="/")


async def current_user(request: Request) -> dict:
    raw = request.cookies.get("access_token") \
        or request.headers.get("Authorization", "").replace("Bearer ", "")
    if not raw:
        raise HTTPException(401, "Please sign in to continue")
    try:
        payload = jwt.decode(raw, JWT_SECRET, algorithms=["HS256"])
    except jwt.InvalidTokenError as err:
        raise HTTPException(401, "Your session has expired") from err
    doc = await db.users.find_one({"user_id": payload["sub"]}, {"_id": 0})
    if not doc:
        raise HTTPException(401, "User not found")
    return doc


# ---- domain helpers -----------------------------------------------------------
def _fallback_meta(symbol: str) -> dict:
    return {"name": symbol.upper(), "sector": "General"}


async def load_meta(symbols: list[str]) -> dict[str, dict]:
    """Bulk-load display metadata (name + sector) for a set of symbols.

    Order of resolution per symbol: curated SUPPORTED map → `stocks_meta`
    collection (populated when users add custom NSE tickers) → fallback
    (symbol as name, "General" sector).
    """
    result: dict[str, dict] = {}
    missing: list[str] = []
    for sym in symbols:
        sym = sym.upper()
        if sym in SUPPORTED:
            result[sym] = SUPPORTED[sym]
        else:
            missing.append(sym)
    if missing:
        async for doc in db.stocks_meta.find({"symbol": {"$in": missing}}):
            result[doc["symbol"]] = {"name": doc.get("name", doc["symbol"]),
                                     "sector": doc.get("sector", "General")}
    for sym in symbols:
        result.setdefault(sym.upper(), _fallback_meta(sym))
    return result


async def ensure_default_watchlist(user_id: str) -> dict:
    """Return the user's active watchlist, self-healing legacy documents.

    Preference order: `is_primary=True` → any existing watchlist (self-healing
    watchlist_id if missing) → freshly seeded default list.
    """
    watch = await db.watchlists.find_one(
        {"user_id": user_id, "is_primary": True}, {"_id": 0},
    )
    if not watch:
        watch = await db.watchlists.find_one({"user_id": user_id}, {"_id": 0})
    if watch:
        if not watch.get("watchlist_id"):
            new_id = "wl_" + secrets.token_hex(6)
            await db.watchlists.update_one(
                {"user_id": user_id, "name": watch["name"]},
                {"$set": {"watchlist_id": new_id}},
            )
            watch["watchlist_id"] = new_id
        return watch
    item = {
        "watchlist_id": "wl_" + secrets.token_hex(6),
        "user_id": user_id,
        "name": "My Watchlist",
        "symbols": ["RELIANCE", "HAL", "BEL", "TCS", "INFY", "HDFCBANK"],
        "is_primary": True,
    }
    await db.watchlists.insert_one(dict(item))
    return item


async def resolve_active_watchlist(user_id: str, watchlist_id: str | None) -> dict:
    """Return the requested watchlist (self-healing) or the default."""
    if watchlist_id:
        watch = await db.watchlists.find_one(
            {"user_id": user_id, "watchlist_id": watchlist_id}, {"_id": 0},
        )
        if watch:
            return watch
    return await ensure_default_watchlist(user_id)


def compose_summary(stocks: list[dict], since_changes: list[dict]) -> str:
    """Build a specific one-paragraph review from live data. No investment advice."""
    if not stocks:
        return ("This watchlist is empty. Add a stock and WATCHIT! will start "
                "remembering what changes between your visits.")
    gainers = [s for s in stocks if s["change"] > 0.5]
    losers = [s for s in stocks if s["change"] < -0.5]
    steady = [s for s in stocks if abs(s["change"]) <= 0.5]
    tone = "mostly higher" if len(gainers) > len(losers) else \
           "mostly lower" if len(losers) > len(gainers) else "mixed"
    parts = [f"Your watchlist is trading {tone} today "
             f"({len(gainers)} up · {len(losers)} down · {len(steady)} steady)"]

    if since_changes:
        big = since_changes[0]
        move = "climbed" if big["direction"] == "up" else "slipped"
        parts.append(
            f"since your last visit {big['symbol']} {move} "
            f"{abs(big['price_delta_pct']):.2f}%"
        )
    if gainers:
        top = max(gainers, key=lambda s: s["change"])
        reason = f", {top['reasons'][0].lower()}" if top.get("reasons") else ""
        parts.append(f"{top['symbol']} leads at {top['change']:+.2f}%{reason}")
    if losers:
        bot = min(losers, key=lambda s: s["change"])
        parts.append(f"{bot['symbol']} lags at {bot['change']:+.2f}%")
    vol_spike = max(stocks, key=lambda s: s.get("volume_ratio", 0.0))
    if vol_spike.get("volume_ratio", 0.0) >= 1.8:
        parts.append(
            f"{vol_spike['symbol']} is trading at {vol_spike['volume_ratio']:.1f}× "
            f"its usual volume"
        )
    breakout = next(
        (s for s in stocks if s.get("high_52w") and s["price"] >= s["high_52w"] * 0.98),
        None,
    )
    breakdown_ = next(
        (s for s in stocks if s.get("low_52w") and s["price"] <= s["low_52w"] * 1.02),
        None,
    )
    if breakout:
        parts.append(f"{breakout['symbol']} is knocking on its 52-week high")
    elif breakdown_:
        parts.append(f"{breakdown_['symbol']} is testing its 52-week low")
    return ". ".join(parts) + "."


def _market_mood(indices: list[dict]) -> str:
    live = [i for i in indices if i["status"] == "live"]
    if not live:
        return "Market pulse unavailable right now."
    ups = sum(1 for i in live if i["change_pct"] > 0)
    if ups == len(live):
        return "Broad-based buying across benchmark indices."
    if ups == 0:
        return "Broad-based pressure across benchmark indices."
    return "Benchmarks are moving unevenly — leadership is narrow today."


def humanize_last_visit(iso: str | None) -> str:
    if not iso:
        return "Welcome — first visit"
    then = datetime.fromisoformat(iso)
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - then
    if delta.total_seconds() < 3600:
        return f"{int(delta.total_seconds() // 60)} min ago"
    if delta.total_seconds() < 86_400:
        return f"{int(delta.total_seconds() // 3600)}h ago"
    return then.strftime("%d %b, %I:%M %p").lstrip("0")


def volume_label(ratio: float) -> str:
    if not ratio:
        return "—"
    return f"{ratio:.1f}x"


def color_for_change(change: float) -> str:
    return "#00D09C" if change >= 0 else "#FF5B5B"


def build_stock_card(symbol: str, meta: dict, quote: dict, attention_result: dict) -> dict:
    return {
        "symbol": symbol,
        "name": meta.get("name", symbol),
        "sector": meta.get("sector", "General"),
        "price": quote.get("close", 0.0),
        "previous_close": quote.get("previous_close", 0.0),
        "change": quote.get("change_pct", 0.0),
        "volume": volume_label(quote.get("volume_ratio", 0.0)),
        "volume_ratio": quote.get("volume_ratio", 0.0),
        "volume_shares": quote.get("volume", 0),
        "avg_volume_20d": quote.get("avg_volume_20d", 0),
        "high_52w": quote.get("high_52w", 0.0),
        "low_52w": quote.get("low_52w", 0.0),
        "momentum_5d_pct": quote.get("momentum_5d_pct", 0.0),
        "sparkline": quote.get("sparkline", []),
        "attention": attention_result["score"],
        "reasons": attention_result["reasons"],
        "color": color_for_change(quote.get("change_pct", 0.0)),
        "status": quote.get("status", "unavailable"),
        "stale": quote.get("status") != "live",
        "quote_source": quote.get("source"),
        "quote_as_of": quote.get("as_of"),
    }


# ---- auth routes --------------------------------------------------------------
@api.post("/auth/register")
async def register(body: Credentials, response: Response):
    email = body.email.lower()
    if await db.users.find_one({"email": email}):
        raise HTTPException(409, "An account with this email already exists")
    doc = {
        "user_id": "user_" + secrets.token_hex(8),
        "email": email,
        "name": body.name or "Investor",
        "password_hash": bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.users.insert_one(doc)
    set_session(response, doc["user_id"])
    return public_user(doc)


@api.post("/auth/login")
async def login(body: Credentials, response: Response):
    doc = await db.users.find_one({"email": body.email.lower()}, {"_id": 0})
    if not doc or not bcrypt.checkpw(body.password.encode(), doc["password_hash"].encode()):
        raise HTTPException(401, "Email or password is incorrect")
    set_session(response, doc["user_id"])
    return public_user(doc)


@api.get("/auth/me")
async def me(user=Depends(current_user)):
    return public_user(user)


@api.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    return {"ok": True}


@api.post("/auth/emergent-session")
async def emergent_session(session_id: str, response: Response):
    try:
        result = requests.get(
            "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data",
            headers={"X-Session-ID": session_id}, timeout=10,
        ).json()
    except Exception as err:  # noqa: BLE001
        raise HTTPException(502, "Google sign-in is temporarily unavailable") from err
    if not result.get("email"):
        raise HTTPException(401, "Google sign-in could not be verified")
    email = result["email"].lower()
    doc = await db.users.find_one({"email": email}, {"_id": 0})
    if not doc:
        doc = {
            "user_id": "user_" + secrets.token_hex(8),
            "email": email,
            "name": result.get("name") or "Investor",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.users.insert_one(dict(doc))
    set_session(response, doc["user_id"])
    return public_user(doc)


# ---- dashboard ----------------------------------------------------------------
@api.get("/dashboard")
async def dashboard(watchlist_id: str | None = None, user=Depends(current_user)):
    watch = await resolve_active_watchlist(user["user_id"], watchlist_id)
    symbols = watch.get("symbols", [])

    quotes, meta, indices = await asyncio.gather(
        market.get_quotes(db, symbols),
        load_meta(symbols),
        market.get_indices(),
    )

    # Sector-level average change for context
    sector_trends: dict[str, list[float]] = {}
    for sym in symbols:
        sector = meta[sym]["sector"]
        sector_trends.setdefault(sector, []).append(quotes[sym].get("change_pct", 0.0))
    sector_avg = {sec: (sum(v) / len(v)) if v else 0.0 for sec, v in sector_trends.items()}

    stocks: list[dict] = []
    for sym in symbols:
        quote = quotes[sym]
        sector = meta[sym]["sector"]
        att = attention.score(quote, sector_avg.get(sector, 0.0))
        stocks.append(build_stock_card(sym, meta[sym], quote, att))

    stocks.sort(key=lambda s: s["attention"], reverse=True)

    snapshot_items = [
        {"symbol": s["symbol"], "close": s["price"], "volume": s["volume_shares"],
         "attention": s["attention"], "change_pct": s["change"]}
        for s in stocks
    ]
    _current_snap, previous_snap = await snapshot.record_visit(
        db, user["user_id"], watch["watchlist_id"], snapshot_items,
    )
    since = snapshot.diff(previous_snap, snapshot_items)

    changes = [
        {
            "symbol": c["symbol"],
            "text": (
                f"{'rose' if c['direction'] == 'up' else 'fell'} "
                f"{c['price_delta_pct']:+.2f}% since your last visit"
            ),
            "direction": c["direction"],
            "delta": c["price_delta_pct"],
        }
        for c in since["changes"]
    ] or [
        {"symbol": s["symbol"],
         "text": f"moved {s['change']:+.2f}% today · attention {s['attention']}",
         "direction": "up" if s["change"] >= 0 else "down",
         "delta": s["change"]}
        for s in stocks[:3]
    ]

    live_count = sum(1 for s in stocks if s["status"] == "live")
    summary_text = compose_summary(stocks, since["changes"])
    market_pulse = {
        "indices": indices,
        "mood": _market_mood(indices),
        "live_count": live_count,
        "tracked": len(stocks),
    }
    # Fire attention emails in the background so page load stays snappy.
    asyncio.create_task(
        alerts_module.evaluate(db, user["user_id"], user.get("name", "Investor"), stocks)
    )
    return {
        "user": public_user(user),
        "last_visit": humanize_last_visit(since["last_visit_at"]),
        "last_visit_at": since["last_visit_at"],
        "active_watchlist": {
            "watchlist_id": watch["watchlist_id"],
            "name": watch["name"],
            "is_primary": bool(watch.get("is_primary")),
        },
        "market_pulse": market_pulse,
        "market": {
            "label": indices[0]["label"] if indices else "NIFTY 50",
            "value": (f"{indices[0]['value']:,.2f}"
                      if indices and indices[0]["status"] == "live" else "—"),
            "change": (f"{indices[0]['change_pct']:+.2f}%"
                       if indices and indices[0]["status"] == "live" else "unavailable"),
        },
        "stocks": stocks,
        "summary": summary_text,
        "stats": {
            "tracked": len(stocks),
            "attention": sum(1 for s in stocks if s["attention"] >= 70),
            "gainers": sum(1 for s in stocks if s["change"] > 0),
            "sectors": len({s["sector"] for s in stocks}),
        },
        "since_last_visit": since,
        "changes": changes,
        "sector_trends": [
            {"name": sec, "change": f"{avg:+.2f}%"} for sec, avg in sorted(sector_avg.items())
        ],
    }


# ---- stock endpoints ----------------------------------------------------------
@api.get("/stocks/preview")
async def stock_preview(symbol: str, exchange: str = "XNSE", user=Depends(current_user)):
    """Autocomplete-friendly preview: verifies the ticker via yfinance and returns
    display metadata + live price. Used by the add-stock drawer before saving."""
    sym = symbol.strip().upper()
    if not sym:
        raise HTTPException(400, "Enter a symbol to preview")
    meta: dict | None = None
    if sym in SUPPORTED:
        meta = SUPPORTED[sym]
    else:
        cached = await db.stocks_meta.find_one({"symbol": sym}, {"_id": 0})
        if cached:
            meta = {"name": cached.get("name", sym), "sector": cached.get("sector", "General")}
        else:
            resolved = await market.resolve_symbol(sym, exchange)
            if not resolved:
                raise HTTPException(404, f"{sym} could not be verified on NSE")
            meta = {"name": resolved["name"], "sector": resolved["sector"]}
            await db.stocks_meta.update_one(
                {"symbol": sym},
                {"$set": {"symbol": sym, "exchange": exchange, **resolved,
                          "added_at": datetime.now(timezone.utc).isoformat()}},
                upsert=True,
            )
    quotes = await market.get_quotes(db, [sym], exchange=exchange)
    quote = quotes[sym]
    return {
        "symbol": sym,
        "name": meta["name"],
        "sector": meta["sector"],
        "supported": sym in SUPPORTED,
        "price": quote.get("close", 0.0),
        "change_pct": quote.get("change_pct", 0.0),
        "status": quote.get("status", "unavailable"),
        "high_52w": quote.get("high_52w", 0.0),
        "low_52w": quote.get("low_52w", 0.0),
    }


@api.get("/stocks/search")
async def search(q: str = "", user=Depends(current_user)):
    q = q.strip().lower()
    return [
        {"symbol": sym, **meta}
        for sym, meta in SUPPORTED.items()
        if not q or q in sym.lower() or q in meta["name"].lower()
    ][:20]


def _humanize_pub(iso: str) -> str:
    if not iso:
        return ""
    try:
        raw = iso.replace("Z", "+00:00")
        then = datetime.fromisoformat(raw)
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - then
        secs = delta.total_seconds()
        if secs < 3600:
            return f"{int(secs // 60)} min ago"
        if secs < 86_400:
            return f"{int(secs // 3600)}h ago"
        if secs < 7 * 86_400:
            return f"{int(secs // 86_400)}d ago"
        return then.strftime("%d %b")
    except Exception:  # noqa: BLE001
        return iso


@api.get("/stocks/{symbol}")
async def stock_detail(symbol: str, user=Depends(current_user)):
    sym = symbol.upper()
    meta_map = await load_meta([sym])
    quotes, history, news, prior_snap = await asyncio.gather(
        market.get_quotes(db, [sym]),
        market.get_history(sym, period="3mo", interval="1d"),
        market.get_news(sym),
        snapshot.last_for_symbol(db, user["user_id"], sym),
    )
    quote = quotes[sym]
    if quote["status"] == "unavailable":
        raise HTTPException(503, f"{sym} could not be resolved on NSE")
    att = attention.score(quote, 0.0)
    card = build_stock_card(sym, meta_map[sym], quote, att)
    news_items = [
        {"title": n["title"], "summary": n["summary"], "source": n["source"],
         "url": n["url"], "time": _humanize_pub(n["published_at"])}
        for n in news
    ] or [
        {"title": f"No fresh headlines for {card['name']} right now",
         "summary": "Yahoo Finance did not return any news for this symbol in the last window.",
         "source": "WATCHIT!", "url": "", "time": "Just now"},
    ]
    snap_comparison = None
    if prior_snap:
        old = prior_snap["item"]
        cur_price = card["price"]
        old_price = old.get("close", 0.0)
        price_delta_pct = ((cur_price - old_price) / old_price * 100) if old_price else 0.0
        snap_comparison = {
            "captured_at": prior_snap["captured_at"],
            "captured_label": humanize_last_visit(prior_snap["captured_at"]),
            "prev_close": old_price,
            "current_close": cur_price,
            "price_delta": round(cur_price - old_price, 2),
            "price_delta_pct": round(price_delta_pct, 2),
            "prev_attention": old.get("attention", 0),
            "current_attention": card["attention"],
            "attention_delta": card["attention"] - old.get("attention", 0),
            "prev_volume": old.get("volume", 0),
            "current_volume": card["volume_shares"],
            "direction": "up" if price_delta_pct >= 0 else "down",
        }
    return {
        **card,
        "chart": [c["c"] for c in history["candles"][-30:]] or [card["price"]],
        "candles": history["candles"],
        "history_status": history["status"],
        "news": news_items,
        "snapshot": snap_comparison,
    }


@api.get("/stocks/{symbol}/news")
async def stock_news(symbol: str, user=Depends(current_user), limit: int = 10):
    items = await market.get_news(symbol, limit=limit)
    return {
        "symbol": symbol.upper(),
        "count": len(items),
        "news": [
            {**n, "time": _humanize_pub(n["published_at"])}
            for n in items
        ],
    }


@api.get("/sectors/{name}")
async def sector_detail(name: str, user=Depends(current_user)):
    watch = await ensure_default_watchlist(user["user_id"])
    symbols = watch.get("symbols", [])
    meta = await load_meta(symbols)
    matching = [s for s in symbols if meta[s]["sector"].lower() == name.lower()]
    if not matching:
        raise HTTPException(404, f"No stocks in your watchlist for sector '{name}'")
    quotes = await market.get_quotes(db, matching)
    cards = []
    for sym in matching:
        att = attention.score(quotes[sym], 0.0)
        cards.append(build_stock_card(sym, meta[sym], quotes[sym], att))
    cards.sort(key=lambda c: c["change"], reverse=True)
    avg_change = round(sum(c["change"] for c in cards) / len(cards), 2)
    total_volume = sum(c["volume_shares"] for c in cards)
    return {
        "name": matching and meta[matching[0]]["sector"] or name,
        "constituents": cards,
        "leaders": cards[:3],
        "laggards": list(reversed(cards))[:3],
        "avg_change": avg_change,
        "total": len(cards),
        "total_volume": total_volume,
        "top_attention": max((c["attention"] for c in cards), default=0),
    }


@api.get("/quotes/{exchange}/{symbol}")
async def quote(exchange: str, symbol: str, user=Depends(current_user)):
    quotes = await market.get_quotes(db, [symbol], exchange=exchange)
    return quotes[symbol.upper()]


@api.get("/series/{exchange}/{symbol}")
async def series(exchange: str, symbol: str, user=Depends(current_user),
                 period: str = "3mo", interval: str = "1d"):
    return await market.get_history(symbol, exchange=exchange, period=period, interval=interval)


# ---- insights -----------------------------------------------------------------
@api.get("/insights")
async def insights(user=Depends(current_user)):
    watch = await ensure_default_watchlist(user["user_id"])
    symbols = watch.get("symbols", [])
    quotes, meta = await asyncio.gather(
        market.get_quotes(db, symbols),
        load_meta(symbols),
    )

    sector_trends: dict[str, list[float]] = {}
    for sym in symbols:
        sector = meta[sym]["sector"]
        sector_trends.setdefault(sector, []).append(quotes[sym].get("change_pct", 0.0))
    sector_avg = {s: (sum(v) / len(v)) if v else 0.0 for s, v in sector_trends.items()}

    cards = []
    for sym in symbols:
        quote = quotes[sym]
        sector = meta[sym]["sector"]
        att = attention.score(quote, sector_avg.get(sector, 0.0))
        cards.append(build_stock_card(sym, meta[sym], quote, att))

    cards.sort(key=lambda c: c["attention"], reverse=True)
    high_attention = [c for c in cards if c["attention"] >= 70]
    health = round(min(100, 40 + (len(high_attention) / max(1, len(cards))) * 60))
    gainers = sorted([c for c in cards if c["change"] > 0],
                     key=lambda c: c["change"], reverse=True)[:5]
    losers = sorted([c for c in cards if c["change"] < 0],
                    key=lambda c: c["change"])[:5]

    return {
        "important": cards[:5],
        "movers": sorted(cards, key=lambda c: abs(c["change"]), reverse=True)[:5],
        "gainers": gainers,
        "losers": losers,
        "volume": sorted(cards, key=lambda c: c["volume_ratio"], reverse=True)[:5],
        "sectors": [
            {"name": sec, "change": f"{avg:+.2f}%"} for sec, avg in sorted(sector_avg.items())
        ],
        "health": health,
    }


@api.post("/summary")
async def ai_summary(user=Depends(current_user)):
    """Gemini 3 Flash summary; graceful fallback preserves availability."""
    try:
        watch = await ensure_default_watchlist(user["user_id"])
        quotes = await market.get_quotes(db, watch.get("symbols", []))
        parts = [
            f"{sym} {q.get('change_pct', 0):+.2f}%"
            for sym, q in quotes.items() if q.get("status") == "live"
        ]
        prompt = "Summarize today's watchlist in one calm sentence: " + ", ".join(parts)
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        chat = (
            LlmChat(
                api_key=os.environ["EMERGENT_LLM_KEY"],
                session_id="watchit-" + user["user_id"],
                system_message="Summarize market changes in one concise sentence. Never give investment advice.",
            ).with_model("google", "gemini-3-flash")
        )
        text = await chat.send_message(UserMessage(text=prompt))
        return {"summary": text, "generated": True}
    except Exception as err:  # noqa: BLE001
        log.warning("ai_summary_fallback err=%s", err)
        return {
            "summary": "Live movers are being tracked across your watchlist. Attention is clustering around defence and energy names.",
            "generated": False,
        }


# ---- watchlists ---------------------------------------------------------------
@api.get("/watchlists")
async def watchlists(user=Depends(current_user)):
    await ensure_default_watchlist(user["user_id"])
    return await db.watchlists.find({"user_id": user["user_id"]}, {"_id": 0}).to_list(50)


@api.post("/watchlists")
async def create_watchlist(body: WatchlistInput, user=Depends(current_user)):
    name = body.name.strip()
    if await db.watchlists.find_one({"user_id": user["user_id"], "name": name}):
        raise HTTPException(409, "A watchlist with this name already exists")
    item = {
        "watchlist_id": "wl_" + secrets.token_hex(6),
        "user_id": user["user_id"],
        "name": name,
        "symbols": [],
    }
    await db.watchlists.insert_one(dict(item))
    return item


@api.patch("/watchlists/{watchlist_id}")
async def rename_watchlist(watchlist_id: str, body: WatchlistInput, user=Depends(current_user)):
    name = body.name.strip()
    if await db.watchlists.find_one({"user_id": user["user_id"], "name": name,
                                     "watchlist_id": {"$ne": watchlist_id}}):
        raise HTTPException(409, "A watchlist with this name already exists")
    result = await db.watchlists.find_one_and_update(
        {"watchlist_id": watchlist_id, "user_id": user["user_id"]},
        {"$set": {"name": name}},
        return_document=ReturnDocument.AFTER, projection={"_id": 0},
    )
    if not result:
        raise HTTPException(404, "Watchlist not found")
    return result


@api.delete("/watchlists/{watchlist_id}")
async def delete_watchlist(watchlist_id: str, user=Depends(current_user)):
    result = await db.watchlists.delete_one({"watchlist_id": watchlist_id, "user_id": user["user_id"]})
    if not result.deleted_count:
        raise HTTPException(404, "Watchlist not found")
    return {"ok": True}


@api.post("/watchlists/{watchlist_id}/stocks")
async def add_stock(watchlist_id: str, body: StockInput, user=Depends(current_user)):
    """Add a stock to a watchlist. Curated symbols are accepted immediately;
    any other NSE ticker is verified against Yahoo Finance before persisting."""
    symbol = body.symbol.strip().upper()
    if symbol not in SUPPORTED:
        existing = await db.stocks_meta.find_one({"symbol": symbol})
        if not existing:
            resolved = await market.resolve_symbol(symbol, body.exchange)
            if not resolved:
                raise HTTPException(
                    404,
                    f"{symbol} could not be verified on NSE. Check the ticker and try again.",
                )
            await db.stocks_meta.update_one(
                {"symbol": symbol},
                {"$set": {"symbol": symbol, "exchange": body.exchange, **resolved,
                          "added_at": datetime.now(timezone.utc).isoformat()}},
                upsert=True,
            )
    result = await db.watchlists.find_one_and_update(
        {"watchlist_id": watchlist_id, "user_id": user["user_id"]},
        {"$addToSet": {"symbols": symbol}},
        return_document=ReturnDocument.AFTER, projection={"_id": 0},
    )
    if not result:
        raise HTTPException(404, "Watchlist not found")
    return result


@api.delete("/watchlists/{watchlist_id}/stocks/{symbol}")
async def remove_stock(watchlist_id: str, symbol: str, user=Depends(current_user)):
    result = await db.watchlists.find_one_and_update(
        {"watchlist_id": watchlist_id, "user_id": user["user_id"]},
        {"$pull": {"symbols": symbol.upper()}},
        return_document=ReturnDocument.AFTER, projection={"_id": 0},
    )
    if not result:
        raise HTTPException(404, "Watchlist not found")
    return result


# ---- attention alerts ---------------------------------------------------------
@api.get("/alerts")
async def list_alerts(user=Depends(current_user)):
    return await alerts_module.list_for_user(db, user["user_id"])


@api.post("/alerts")
async def create_alert(body: AlertInput, user=Depends(current_user)):
    return await alerts_module.create(db, user["user_id"], body.symbol, body.threshold, body.email)


@api.patch("/alerts/{alert_id}")
async def patch_alert(alert_id: str, body: AlertPatch, user=Depends(current_user)):
    result = await alerts_module.update(
        db, user["user_id"], alert_id,
        {k: v for k, v in body.model_dump(exclude_none=True).items()},
    )
    if not result:
        raise HTTPException(404, "Alert not found")
    return result


@api.delete("/alerts/{alert_id}")
async def delete_alert(alert_id: str, user=Depends(current_user)):
    ok = await alerts_module.delete(db, user["user_id"], alert_id)
    if not ok:
        raise HTTPException(404, "Alert not found")
    return {"ok": True}


# ---- market pulse -------------------------------------------------------------
@api.get("/market/pulse")
async def market_pulse(user=Depends(current_user)):
    indices = await market.get_indices()
    return {"indices": indices, "mood": _market_mood(indices)}


# ---- watchlist compare + primary ---------------------------------------------
def _aggregate_watchlist(cards: list[dict]) -> dict:
    if not cards:
        return {"count": 0, "gainers": 0, "losers": 0, "avg_change": 0.0,
                "avg_attention": 0, "sectors": {}, "leader": None, "laggard": None}
    sectors: dict[str, int] = {}
    for c in cards:
        sectors[c["sector"]] = sectors.get(c["sector"], 0) + 1
    leader = max(cards, key=lambda c: c["change"])
    laggard = min(cards, key=lambda c: c["change"])
    return {
        "count": len(cards),
        "gainers": sum(1 for c in cards if c["change"] > 0),
        "losers": sum(1 for c in cards if c["change"] < 0),
        "avg_change": round(sum(c["change"] for c in cards) / len(cards), 2),
        "avg_attention": round(sum(c["attention"] for c in cards) / len(cards)),
        "sectors": sectors,
        "leader": {"symbol": leader["symbol"], "change": leader["change"]},
        "laggard": {"symbol": laggard["symbol"], "change": laggard["change"]},
    }


async def _build_watchlist_cards(user_id: str, watch: dict) -> list[dict]:
    symbols = watch.get("symbols", [])
    if not symbols:
        return []
    quotes, meta = await asyncio.gather(
        market.get_quotes(db, symbols), load_meta(symbols),
    )
    sector_trends: dict[str, list[float]] = {}
    for sym in symbols:
        sector_trends.setdefault(meta[sym]["sector"], []).append(quotes[sym].get("change_pct", 0.0))
    sector_avg = {s: (sum(v) / len(v)) if v else 0.0 for s, v in sector_trends.items()}
    cards = []
    for sym in symbols:
        att = attention.score(quotes[sym], sector_avg.get(meta[sym]["sector"], 0.0))
        cards.append(build_stock_card(sym, meta[sym], quotes[sym], att))
    return cards


@api.get("/watchlists/compare")
async def compare_watchlists(a: str, b: str, user=Depends(current_user)):
    if a == b:
        raise HTTPException(400, "Pick two different watchlists to compare")
    wl_a = await db.watchlists.find_one({"user_id": user["user_id"], "watchlist_id": a}, {"_id": 0})
    wl_b = await db.watchlists.find_one({"user_id": user["user_id"], "watchlist_id": b}, {"_id": 0})
    if not wl_a or not wl_b:
        raise HTTPException(404, "One of the watchlists could not be found")
    cards_a, cards_b = await asyncio.gather(
        _build_watchlist_cards(user["user_id"], wl_a),
        _build_watchlist_cards(user["user_id"], wl_b),
    )
    overlap = sorted(set(wl_a.get("symbols", [])) & set(wl_b.get("symbols", [])))
    return {
        "a": {"watchlist_id": wl_a["watchlist_id"], "name": wl_a["name"],
              "symbols": wl_a.get("symbols", []),
              "stocks": cards_a,
              "aggregates": _aggregate_watchlist(cards_a)},
        "b": {"watchlist_id": wl_b["watchlist_id"], "name": wl_b["name"],
              "symbols": wl_b.get("symbols", []),
              "stocks": cards_b,
              "aggregates": _aggregate_watchlist(cards_b)},
        "overlap": overlap,
    }


@api.post("/watchlists/{watchlist_id}/primary")
async def set_primary(watchlist_id: str, user=Depends(current_user)):
    target = await db.watchlists.find_one(
        {"user_id": user["user_id"], "watchlist_id": watchlist_id}, {"_id": 0},
    )
    if not target:
        raise HTTPException(404, "Watchlist not found")
    await db.watchlists.update_many(
        {"user_id": user["user_id"]}, {"$set": {"is_primary": False}},
    )
    await db.watchlists.update_one(
        {"user_id": user["user_id"], "watchlist_id": watchlist_id},
        {"$set": {"is_primary": True}},
    )
    target["is_primary"] = True
    return target


# ---- weekly digest -----------------------------------------------------------
@api.post("/digest/preview")
async def digest_preview(user=Depends(current_user)):
    """Manually trigger the weekly digest email for the current user."""
    result = await digest_module.send_for_user(db, user)
    return {"ok": bool(result.get("sent")), **result}


# ---- app wiring ---------------------------------------------------------------
app.include_router(api)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

scheduler = AsyncIOScheduler(timezone=os.environ.get("DIGEST_TIMEZONE", "Asia/Kolkata"))


@app.on_event("startup")
async def startup():
    await db.users.create_index("email", unique=True)
    await db.watchlists.create_index([("user_id", 1), ("name", 1)], unique=True)
    # Heal legacy watchlists that pre-date the current schema.
    async for doc in db.watchlists.find({"watchlist_id": {"$exists": False}}):
        await db.watchlists.update_one(
            {"_id": doc["_id"]},
            {"$set": {"watchlist_id": "wl_" + secrets.token_hex(6)}},
        )
    # Ensure exactly one primary watchlist per user (best-effort promotion).
    async for user_doc in db.users.find({}, {"user_id": 1, "_id": 0}):
        has_primary = await db.watchlists.find_one(
            {"user_id": user_doc["user_id"], "is_primary": True},
        )
        if not has_primary:
            first = await db.watchlists.find_one({"user_id": user_doc["user_id"]})
            if first:
                await db.watchlists.update_one(
                    {"_id": first["_id"]}, {"$set": {"is_primary": True}},
                )
    # Cache schema changed with the Yahoo Finance migration; drop legacy indexes.
    try:
        for name in await db.quote_cache.index_information():
            if name != "_id_":
                await db.quote_cache.drop_index(name)
    except Exception:  # noqa: BLE001
        pass
    await db.quote_cache.create_index([("symbol", 1), ("exchange", 1)], unique=True)
    await db.stocks_meta.create_index("symbol", unique=True)
    await db.alerts.create_index([("user_id", 1), ("symbol", 1)])
    await db.snapshots.create_index([("user_id", 1), ("watchlist_id", 1), ("captured_at", -1)])

    async def _weekly_job():
        try:
            await digest_module.run_all(db)
        except Exception as err:  # noqa: BLE001
            log.warning("weekly_digest_failed err=%s", err)

    scheduler.add_job(
        _weekly_job,
        CronTrigger(day_of_week="mon", hour=int(os.environ.get("DIGEST_HOUR_LOCAL", "8")), minute=0),
        id="weekly-digest", replace_existing=True,
    )
    scheduler.start()


@app.on_event("shutdown")
async def shutdown():
    try:
        scheduler.shutdown(wait=False)
    except Exception:  # noqa: BLE001
        pass
    client.close()
