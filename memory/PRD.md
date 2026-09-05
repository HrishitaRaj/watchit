# WATCHIT! Product Record

## Original problem statement
Build WATCHIT!, an intelligent Indian-equities watchlist that answers “What happened while I was away?” with calm, explainable attention scores, snapshots, summaries, insights, authentication, and watchlist workflows.

## Architecture decisions
- React 19 frontend with React Router, Recharts-ready visual patterns, and a FastAPI/MongoDB API.
- Mocked Indian market universe keeps the MVP runnable while attention, snapshot-style comparisons, and explanations are demonstrated.
- JWT cookie sessions plus Emergent Google OAuth callback exchange.
- Market attention is computed from explainable movement, volume, sector, and momentum signals.

## Personas and core requirements
- Retail investors checking 20–100 stocks once or twice daily.
- Reduce review time, prioritize meaningful change, avoid advice, provide explainability, support responsive dark mode, and preserve session state.

## Implemented — 2026-09-04
- Email registration/login/logout and persistent JWT sessions.
- Emergent Google login callback handling.
- Since-last-visit dashboard with market summary, attention stocks, changes, quick stats, and concise AI summary endpoint.
- Indian stock search route, detail page with chart/metrics/news/reasons, insights page, and settings page.
- MongoDB-backed users and watchlists with safe response projections.
- Responsive premium dark interface with Outfit, Plus Jakarta Sans, and JetBrains Mono.

## Prioritized backlog
- P0: Persist snapshot items per visit and calculate live comparisons from a market-data adapter.
- P1: Complete watchlist rename/delete/add/remove UI and notification preference persistence.
- P1: Add rate limiting, refresh-token rotation, and structured API error telemetry.
- P2: Replace mocked market data with a licensed Indian-equities feed and add richer historical charts.