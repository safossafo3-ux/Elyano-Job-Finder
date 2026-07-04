# JobRadar 📡 Global

Autonomous job scout for **80 countries** across **9 regions**. Real-time Telegram alerts. Multi-user.

## What it does

1. User opens dashboard → sees **9 region cards** (Europe, Russia & CIS, Middle East, Asia, Africa, North America, Latin America, Oceania, Balkans)
2. Picks regions → countries appear with checkboxes
3. Picks roles (🛵 Courier / 🏗️ Construction / 🏭 Factory)
4. Clicks **⚡ Start search**
5. Bot scrapes **Indeed** (40+ countries) and **DuckDuckGo** (for countries without Indeed)
6. Each new job is analyzed by **Gemini** (translate, summarize, extract phone, detect "no foreigners")
7. Eligible jobs are sent to the user's Telegram **in real time** with screenshot + English summary + phone

## Setup

### 1. Install

```bash
cd jobradar-project
python -m venv .venv
.venv\Scripts\activate          # Windows
# or: source .venv/bin/activate  # Mac/Linux
pip install -r requirements.txt
playwright install chromium
```

### 2. Get API keys

| Key | How |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Talk to [@BotFather](https://t.me/BotFather), `/newbot`, copy token |
| `TELEGRAM_BOT_USERNAME` | The bot's @username (e.g. `MustafaJobRadar_bot`) — without the @ |
| `TELEGRAM_CHAT_ID` | Send `/start` to your bot, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` → copy your `chat.id` |
| `GEMINI_API_KEY` | Free at https://aistudio.google.com/app/apikey |
| `SESSION_SECRET` | Any random string (e.g. run `python -c "import secrets; print(secrets.token_hex(32))"`) |

### 3. Configure

```bash
cp .env.example .env
# edit .env with your keys
```

### 4. Run

```bash
python run.py
```

Open http://localhost:8000 → click **Login with Telegram** → send `/start` to your bot → enter the 6-digit code.

## How user registration works

1. User opens dashboard → clicks **Login with Telegram**
2. Modal shows link to your bot + a code input field
3. User clicks the link → Telegram opens → user sends `/start`
4. Bot replies with a 6-digit code (valid 10 minutes)
5. User enters the code on the dashboard → session cookie is set (30 days)
6. Each user's scans + notifications are tracked per-user in the DB

Multiple users can register; each gets notifications only for scans they trigger. Scheduled scans (8 AM + 8 PM) notify all registered users.

## Deploy to Railway

### Option A: Connect GitHub (recommended)

1. Push this project to GitHub:
   ```bash
   git init
   git add .
   git commit -m "Initial commit — JobRadar"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/elyano-job-finder.git
   git push -u origin main
   ```
2. Go to https://railway.app → New Project → Deploy from GitHub repo
3. Pick your `elyano-job-finder` repo
4. Railway auto-detects the Dockerfile → builds → deploys
5. In Railway's **Variables** tab, add all env vars from `.env.example`
6. Railway gives you a public URL like `https://elyano-job-finder.up.railway.app`
7. Open that URL → log in with Telegram → start searching

### Option B: Railway CLI

```bash
npm i -g @railway/cli
railway login
railway init
railway up
```

## Architecture

```
                ┌─────────────────┐
                │  FastAPI        │
                │  dashboard      │
                │  (frosted glass │
                │   dark blue UI) │
                └────────┬────────┘
                         │
                         ▼
                ┌─────────────────┐    ┌─────────────┐
                │  Scheduler      │───▶│  Scraper    │
                │  (2x daily +    │    │  Playwright │
                │   on-demand)    │    │  Indeed +   │
                └─────────────────┘    │  DuckDuckGo │
                         │              └──────┬──────┘
                         │                     │
                         ▼                     ▼
                ┌─────────────────┐    ┌─────────────┐
                │  Telegram bot   │◀───│  Gemini LLM │
                │  (polling for   │    │  analyze +  │
                │   /start +      │    │  translate  │
                │   send alerts)  │    │  + phone    │
                └─────────────────┘    └─────────────┘
                         ▲
                         │
                ┌─────────────────┐
                │  SQLite DB      │
                │  (users, jobs,  │
                │   notifications)│
                └─────────────────┘
```

## Coverage

| Region | Countries | Notes |
|---|---|---|
| Europe | 35 | EU 27 + UK, CH, NO, IS + Balkans 5 |
| Russia & CIS | 6 | RU, UA, BY, MD, GE, AM |
| Middle East | 8 | AE, SA, QA, KW, BH, OM, IL, JO |
| Asia | 15 | JP, KR, CN, IN, PK, BD, SG, HK, MY, TH, PH, ID, VN, TW, LK |
| Africa | 8 | ZA, EG, NG, KE, MA, TN, GH, ET |
| North America | 3 | US, CA, MX |
| Latin America | 8 | BR, AR, CL, CO, PE, CR, PA, UY |
| Oceania | 2 | AU, NZ |
| **Total** | **85** | |

## Job sources

- **Indeed** — primary source, covers 50+ countries with one HTML structure
- **DuckDuckGo search** — fallback for countries without Indeed (searches LinkedIn + general web)

## Legal & safe by design

- ✅ Only reads public job ads
- ✅ Sends messages only to users who **explicitly registered** via Telegram
- ✅ Human-in-the-loop: bot drafts alerts, **you** decide which to apply to
- ⚠️ Polite rate: 2 scans/day + max 15 jobs/portal keeps you under anti-bot radars
- ⚠️ For personal use. If you open to many users, add rate limiting per user.

## Troubleshooting

**`GEMINI_API_KEY not set`** — `.env` not loaded. Run `pip install python-dotenv`. Check `/api/diagnostics`.

**Telegram bot doesn't reply to `/start`** — Make sure the bot token is correct and the polling task is running. Check logs for `Telegram polling started`.

**Indeed returns 0 jobs** — Indeed changes their HTML often. Update selectors in `jobradar/scrapers/base.py` → `scrape_indeed()`.

**Login code doesn't arrive** — Make sure you set `TELEGRAM_BOT_USERNAME` and that you sent `/start` to the bot from your Telegram account.

**"No chat_id to notify"** — Either you're not logged in (so scan has no target user) and admin chat isn't set, or your `.env` is missing `TELEGRAM_CHAT_ID`.
