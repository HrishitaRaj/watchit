# WATCHIT! — A Real-Time Indian Market Dashboard

> A calm, opinionated dashboard for Indian investors who want to *understand* the market, not just stare at a ticker tape.

WATCHIT! is a full-stack web application that ingests live NSE/BSE quotes from Yahoo Finance, scores every stock in your watchlist with a deterministic **Attention Engine**, and tells you in plain English *what changed since you last looked*. It ships with a **Snapshot Engine** that remembers your last visit, an **Alert** system that emails you only when a stock crosses your threshold, and a **Weekly Digest** every Monday morning.

---

## What it does

| Feature | How |
|---|---|
| Live NSE/BSE quotes | Direct HTTP to Yahoo Finance v8 `chart` endpoint |
| N watchlists per user | MongoDB-backed, indexed on `(user_id, name)` |
| Attention score (0-100) per stock | Weighted sum of price move, volume, 52w break, momentum, sector |
| "Since your last visit" diff | Snapshot Engine writes a debounced record every dashboard load |
| Per-stock email alerts | APScheduler + Resend, throttled by 6h cooldown |
| Weekly Monday digest | APScheduler cron + Resend HTML email |
| News per symbol | Yahoo Finance v1 search endpoint |
| Multi-watchlist comparison | `?a=&b=` query on `/watchlists/compare` |
| Sector heatmap | Average `change_pct` grouped by `sector` |

---

## System architecture

```
+--------------------------------------------------+
|      React 19 + Tailwind UI (CRACO + Radix)     |
+---------------------+----------------------------+
                      | fetch (credentials: include)
                      v
+--------------------------------------------------+
|           FastAPI (uvicorn, port 8000)          |
|  server.py - 14 route groups, CORS, JWT auth   |
|  +--------------------------------------------+ |
|  | market.py | snapshot.py | attention.py     | |
|  | alerts.py | digest.py   | mailer.py       | |
|  +--------------------------------------------+ |
|                      |                         |
|                      v                         |
|              APScheduler cron                    |
|        (weekly digest, alert evaluation)         |
+-----+--------------------------+---------------+
      |                          |
      v                          v
+-----------------+    +-----------------------+
|  Yahoo Finance  |    |  MongoDB Atlas         |
|  v8 chart API  |    |  users, watchlists,    |
|  (no yfinance!) |    |  snapshots, alerts,    |
+-----------------+    |  quote_cache           |
                       +-----------------------+
                                  ^
                                  |
                       +----------+----------+
                       | Resend (email)      |
                       | digest + alerts     |
                       +---------------------+
```

### Tech stack

| Layer | Choice | Why |
|---|---|---|
| API | FastAPI + uvicorn | Async-first, Pydantic validation, OpenAPI free |
| Data | MongoDB (Motor async) | Document model fits watchlists, schema evolves easily |
| Auth | JWT (PyJWT) + bcrypt | Stateless, 7-day expiry, no session store |
| Market data | Direct Yahoo Finance v8 HTTP | Avoids yfinance rate limits and sync blocking |
| Scheduling | APScheduler (AsyncIOScheduler) | In-process, no Redis needed |
| Email | Resend | Simple API, free tier (100/day) |
| Frontend | React 19 + CRACO | Fast dev, no eject needed |
| UI | Tailwind + Radix primitives | Accessible, small bundle, easy theming |
| State | React Context (2 contexts only) | Auth + Watchlist, no Redux overhead |

---

## Data flow

### Dashboard load

```
React mount
  -> GET /api/dashboard
        -> ensure_default_watchlist(user)        # Mongo
        -> market.get_quotes(db, symbols)        # Yahoo + cache
              -> check quote_cache (TTL=30s)
              -> if miss: batch Yahoo v8 -> cache
        -> attention.score(snapshot, sector)     # per stock
        -> snapshot.record_visit()               # debounced save
        -> market.get_indices()                  # NIFTY/SENSEX/BANKNIFTY
        -> fire-and-forget alerts.evaluate()     # background email
  <- JSON: { user, market_pulse, stocks, since_last_visit, ... }
```

### Weekly digest (Monday 8am IST)

```
APScheduler cron (day_of_week=mon, hour=8)
  -> digest.run_all(db)
        -> for each opted-in user:
              -> build_for_user: fresh quotes for all watchlists
              -> attention.score() each card
              -> render HTML (top 3 gainers + 3 laggards per list)
              -> mailer.send() via Resend
```



---

## The four engines

### 1. Market Engine (`backend/market.py`)

The only module that touches the outside world for price data. Every public function returns a well-formed envelope with `status: "live" | "unavailable"` - nulls are never surfaced.

- `to_vendor_symbol()` - `RELIANCE` -> `RELIANCE.NS`
- `_yf_chart()` - one HTTP GET to Yahoo v8 with a shared `requests.Session`
- `_snapshot_from_chart()` - extracts close, previous_close, change_pct, volume, avg_volume_20d, volume_ratio, 52w high/low, 5d momentum, sparkline
- `get_quotes_batch()` - concurrent calls via `asyncio.gather`
- `get_indices()` - NIFTY 50, SENSEX, BANK NIFTY
- `get_history()` - OHLCV candles for charts
- `get_news()` - Yahoo v1 search endpoint

**30-second MongoDB cache** keyed by `(symbol, exchange)` prevents redundant Yahoo calls when multiple users share tickers.

### 2. Attention Engine (`backend/attention.py`)

Pure function: snapshot -> `{score: 0-100, reasons: [str, ...]}`. No ML, no hidden state - the reasons ARE the API.

**Weights (sum to 100):**

| Signal | Weight | Trigger |
|---|---|---|
| Price movement | 30 | linear `abs(change_pct)` over [0.5, 6.0]% |
| Volume spike | 25 | linear `volume_ratio` over [1.2, 2.5]x |
| 52-week breakout | 20 | within 2% of 52w high or low |
| Short-term momentum | 15 | linear `abs(5d momentum)` over [1, 8]% |
| Sector context | 10 | linear `abs(sector_avg)` over [0.5, 3.5]% |

Every score has at least one reason - even a quiet stock gets "Price is relatively unchanged today".

### 3. Snapshot Engine (`backend/snapshot.py`)

Answers "what happened while I was away?"

- `record_visit()` is **debounced** (5 min default) - a page refresh does not overwrite the previous reference point
- `diff()` returns top 8 stocks by absolute movement, only including changes where `abs(price_delta) >= 0.5%` OR `abs(attention_delta) >= 5`
- `last_for_symbol()` finds the last remembered price across all a user's watchlists

### 4. Alert & Digest Engines (`backend/alerts.py`, `backend/digest.py`)

Both are fire-and-forget background tasks with cooldowns.

- **Alerts**: per-(user, symbol, threshold) with 6-hour cooldown. Fires when `attention >= threshold`. Updates `last_triggered_at` so we never spam the same person about the same stock.
- **Digest**: APScheduler cron writes `last_digest_at` to the user doc to prevent duplicate sends within 24h.

---

## Why direct Yahoo Finance v8 (and not `yfinance`)

The original implementation used the `yfinance` Python package. It worked in dev, then:

1. **Rate-limited** - yfinance makes several sub-requests per ticker; Yahoo started returning 429s for clusters of 6+ symbols
2. **Synchronous** - blocks the event loop; every call needed `asyncio.to_thread`
3. **Version-pinned to a moving target** - yfinance 1.7.0 -> 1.8 broke a public method

The direct v8 API:
- One HTTP GET per ticker (`/v8/finance/chart/{symbol}?interval=1d&range=1y`)
- Browser `User-Agent` header - Yahoo serves the page
- Shared `requests.Session` for TCP/TLS reuse
- `regularMarketPrice` fallback when `Close` is NaN (market is mid-session)


---

## Frontend architecture

```
<App>
  +-- <AuthProvider>               # user, login(), logout()
        +-- <WatchlistProvider>    # lists, activeId, reload(), refresh()
              +-- <BrowserRouter>
                    +-- /               -> Dashboard
                    +-- /stock/:symbol  -> StockDetail
                    +-- /sector/:name   -> SectorDetail
                    +-- /settings       -> Settings
```

### State model

- **Server state**: plain `fetch` wrappers (`get`, `post`, `mutate`) - no SWR, no TanStack Query
- **Watchlist state**: `WatchlistContext` exposes `lists`, `activeId`, `setActiveId`, `reload`, `refresh`, `version`. Mutations call `refresh()` which bumps the counter so subscribers re-render
- **Auth state**: parallel `AuthContext`, set on login, cleared on logout

### Why no Redux / Zustand

Two cross-tree concerns: auth + watchlist. Context is sufficient. We add a query library only if a future feature needs request-level caching.

---

## Engineering decisions

| Decision | Why |
|---|---|
| JWT in HTTP-only cookie (not localStorage) | XSS-resistant |
| No ORM (raw Motor) | MongoDB queries are simple; abstraction costs more than it saves |
| APScheduler in-process (not Celery) | One cron + threshold evaluator; no broker needed |
| 30s quote cache in MongoDB | Dedupes Yahoo calls across N users with overlapping watchlists |
| Snapshot debounce 5 min | Refresh should not overwrite the "last visit" reference |
| Attention score is pure and deterministic | Easy to test, easy to explain, reasons are the API |
| Static symbol universe in code | Search and add use the same `SUPPORTED` dict - no arbitrary Yahoo validation |
| No websockets | Polling every 30-60s is enough for end-of-day investors |
| CRACO, not eject | All Webpack tweaks in `craco.config.js`; eject creates a maintenance tax |
| Tailwind + Radix (not Material) | Smaller bundle, accessible primitives, one CSS file to theme |


---

## Repository layout

```
watchit/
+-- README.md                   <-- you are here
+-- DEPLOY.md                   <-- Render / Railway / Docker guide
+-- render.yaml                 <-- one-click Render blueprint

+-- backend/
|   +-- server.py              <-- FastAPI app, all routes
|   +-- market.py              <-- Yahoo Finance v8 client
|   +-- attention.py           <-- 0-100 scoring engine
|   +-- snapshot.py            <-- "since last visit" memory
|   +-- alerts.py              <-- per-stock email thresholds
|   +-- digest.py              <-- Monday morning email
|   +-- mailer.py              <-- Resend HTML templates
|   +-- requirements.txt       <-- pinned deps
|   +-- .env.example           <-- env template
|   +-- tests/                 <-- pytest suite

+-- frontend/
    +-- src/
    |   +-- App.js             <-- all routes + contexts
    |   +-- components/ui/     <-- Radix primitives (shadcn-style)
    |   +-- constants/testIds/ <-- stable selectors for E2E
    |   +-- lib/utils.js       <-- cn() helper
    +-- craco.config.js
    +-- tailwind.config.js
    +-- package.json
    +-- .env.example
```

---

## Quick start

```bash
# Backend
cd backend
pip install -r requirements.txt
cp .env.example .env    # fill in MONGO_URL, JWT_SECRET, RESEND_API_KEY
uvicorn server:app --reload --port 8000

# Frontend
cd frontend
yarn install              # or npm install
cp .env.example .env    # set REACT_APP_BACKEND_URL=http://localhost:8000
yarn start               # serves http://localhost:3000
```

Dashboard: http://localhost:3000 - API docs: http://localhost:8000/docs

---

## Deployment

See **[DEPLOY.md](./DEPLOY.md)** for step-by-step on Render, Railway, or a self-hosted VPS.

TL;DR: Backend on Render Web Service (Python, free tier) + Frontend on Render Static Site + MongoDB Atlas M0 cluster + Resend (100 emails/day free).

---

## License

MIT. Use it, fork it, deploy it.

