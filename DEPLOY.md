# Deploying WatchIt

## Option 1 — Render.com (Recommended, Free Tier)

Render offers free web services with sleep after 15 min of inactivity, which is fine for a personal market dashboard.

### Prerequisites
1. **GitHub account** with the repo pushed to `https://github.com/HrishitaRaj/watchit`
2. **MongoDB Atlas** free cluster (https://cloud.mongodb.com)

### Step 1 — MongoDB Atlas
1. Create a free cluster in Singapore region
2. Create a database user with password auth
3. Whitelist IP `0.0.0.0/0` (for Render) or use `ALLOW_ALL` for development
4. Get your connection string: `mongodb+srv://user:pass@cluster.mongodb.net`

### Step 2 — Deploy Backend (Render Web Service)
1. Go to https://dashboard.render.com → **New → Web Service**
2. Connect your GitHub repo
3. Configure:
   - **Root Directory**: `backend`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn server:app --host 0.0.0.0 --port $PORT`
   - **Environment**: Python 3.12
   - **Plan**: Free

4. Add Environment Variables (in Render dashboard):
   ```
   MONGO_URL = mongodb+srv://user:pass@cluster.mongodb.net
   DB_NAME = watchit_prod
   JWT_SECRET = (click "Generate" to create a secure secret)
   CORS_ORIGINS = https://your-frontend.onrender.com
   QUOTE_CACHE_TTL_SECONDS = 30
   RESEND_API_KEY = re_xxxxxxxxxxxx
   SENDER_EMAIL = onboarding@resend.dev
   ALERT_COOLDOWN_HOURS = 6
   DIGEST_TIMEZONE = Asia/Kolkata
   DIGEST_HOUR_LOCAL = 8
   LOG_LEVEL = INFO
   ```

5. Deploy — note the URL, e.g. `https://watchit-api.onrender.com`

### Step 3 — Deploy Frontend (Render Static Site)
1. Go to https://dashboard.render.com → **New → Static Site**
2. Connect the same GitHub repo
3. Configure:
   - **Root Directory**: `frontend`
   - **Build Command**: `yarn install && yarn build`
   - **Publish Directory**: `build`
   - **Environment**: Static

4. Add Environment Variable:
   ```
   REACT_APP_BACKEND_URL = https://your-backend-url.onrender.com
   ```

5. Deploy — note the URL, e.g. `https://watchit-frontend.onrender.com`

### Step 4 — Update CORS
After frontend deploys, go back to the backend service and update:
```
CORS_ORIGINS = https://watchit-frontend.onrender.com
```
Then redeploy the backend.

---

## Option 2 — Railway.app

Railway has a simpler UI and auto-detects FastAPI/React.

1. Create project on Railway
2. Add **MongoDB** plugin → get `MONGO_URL`
3. Add **Backend** service → connect repo, set root to `backend`
4. Add **Frontend** service → connect repo, set root to `frontend`
5. Set env vars same as Render above
6. For frontend: `REACT_APP_BACKEND_URL = https://your-backend.up.railway.app`

---

## Option 3 — Docker (Self-hosted / VPS)

```bash
# Build
docker build -t watchit-api -f backend/Dockerfile backend/

# Run
docker run -d -p 8000:8000 \
  -e MONGO_URL=mongodb://host:27017 \
  -e DB_NAME=watchit_prod \
  -e JWT_SECRET=your-secret \
  -e CORS_ORIGINS=https://your-frontend.com \
  -e RESEND_API_KEY=re_xxx \
  watchit-api
```

For the frontend, build the React app and serve with nginx:
```bash
cd frontend && yarn install && yarn build
# serve the build/ folder with nginx or any static file server
```

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `MONGO_URL` | Yes | `mongodb://localhost:27017` | MongoDB connection string |
| `DB_NAME` | No | `watchit_prod` | MongoDB database name |
| `JWT_SECRET` | Yes | — | Secret for JWT tokens (min 32 chars) |
| `CORS_ORIGINS` | Yes | `*` | Comma-separated frontend URLs |
| `QUOTE_CACHE_TTL_SECONDS` | No | `30` | Cache duration for Yahoo Finance quotes |
| `RESEND_API_KEY` | For email | — | Resend API key for weekly digests |
| `SENDER_EMAIL` | For email | `onboarding@resend.dev` | From address for emails |
| `ALERT_COOLDOWN_HOURS` | No | `6` | Hours between repeated price alerts |
| `DIGEST_TIMEZONE` | No | `Asia/Kolkata` | Timezone for weekly digest scheduler |
| `DIGEST_HOUR_LOCAL` | No | `8` | Hour (local time) to send weekly digest |
| `LOG_LEVEL` | No | `INFO` | Python logging level |

## Troubleshooting

**Backend crashes on startup**
- Check that `MONGO_URL` is reachable from the hosting provider
- Verify `JWT_SECRET` is set (not empty)

**CORS errors in browser**
- Ensure `CORS_ORIGINS` includes the exact frontend URL (no trailing slash)
- Both http and https count as different origins

**Yahoo Finance returns 0/unavailable**
- Yahoo Finance v8 API may rate-limit on free tier
- Try increasing `QUOTE_CACHE_TTL_SECONDS` to reduce API calls
- The app falls back to `regularMarketPrice` when markets are open

**Frontend shows "backend unavailable"**
- Make sure `REACT_APP_BACKEND_URL` matches the backend URL exactly
- Rebuild the frontend after changing this env var

**MongoDB connection refused**
- On Render/Railway, MongoDB Atlas needs to whitelist the hosting provider's IPs
- Set `ALLOW_ALL` or add the provider's egress IPs to Atlas IP whitelist
