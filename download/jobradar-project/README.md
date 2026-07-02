# JobRadar 📡

Autonomous job scout for **Eastern Europe** (Serbia, Bosnia, Montenegro, Bulgaria, Romania, North Macedonia, Latvia, Lithuania).

The bot surfs public job portals in each country's local language, looks for **courier (Glovo/Wolt/Bolt/Tazz)**, **construction**, and **factory** jobs, screens out ads that explicitly reject foreigners, and sends you a **Telegram message** with:

- 📷 screenshot of the ad
- 🇬🇧 one-line English summary
- 📞 employer phone number (normalized with the country dial code)
- 🔗 link to the original ad

Scans run **twice a day** (default 8 AM and 8 PM Cairo time) and can also be triggered **on-demand** from the dashboard.

---

## Setup

### 1. Install Python deps

```bash
cd /home/z/my-project
pip install -r requirements.txt
playwright install chromium
```

### 2. Get API keys

| Service | How to get it |
|---|---|
| **Telegram bot token** | Talk to [@BotFather](https://t.me/BotFather), create a bot, copy the token |
| **Telegram chat_id** | Send any message to your new bot, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` — look for `"chat":{"id":...}` |
| **Gemini API key** | Free at https://aistudio.google.com/app/apikey |

### 3. Configure env

```bash
cp .env.example .env
# edit .env and fill in your keys
```

### 4. Run

```bash
python run.py
```

Open http://localhost:8000 in your browser.

---

## How it works

```
┌─────────────┐    ┌──────────────┐    ┌─────────────┐    ┌───────────┐
│  Scheduler  │───▶│   Scraper    │───▶│   Gemini    │───▶│  Telegram │
│ (2x / day   │    │  Playwright  │    │  translate, │    │   sends   │
│  + on-demand│    │  per country │    │  summarize, │    │  photo +  │
│  button)    │    │  per portal  │    │  phone,     │    │  caption  │
└─────────────┘    └──────────────┘    │  filter     │    │  to you   │
                                        └─────────────┘    └───────────┘
                                                │
                                                ▼
                                        ┌─────────────┐
                                        │   SQLite    │
                                        │  (jobs.db)  │
                                        └─────────────┘
                                                ▲
                                                │
                                        ┌─────────────┐
                                        │  Dashboard  │
                                        │  FastAPI    │
                                        │  view/filter│
                                        └─────────────┘
```

---

## Countries & phone codes

| Country | TLD | Dial | Portals |
|---|---|---|---|
| 🇷🇸 Serbia | .rs | +381 | Infostud, HelloWorld, Joberty |
| 🇧🇦 Bosnia | .ba | +387 | Poslovi.ba, MojPosao |
| 🇲🇪 Montenegro | .me | +382 | Poslopi, Oglasi.me |
| 🇧🇬 Bulgaria | .bg | +359 | Jobs.bg, Rabota.bg, JobOffer.bg |
| 🇷🇴 Romania | .ro | +40 | EJobs, Hipo, BestJobs |
| 🇲🇰 N. Macedonia | .mk | +389 | Kariera, Vrabotuvanje |
| 🇱🇻 Latvia | .lv | +371 | CVmarket, CV.lv, SS.lv |
| 🇱🇹 Lithuania | .lt | +370 | CVbankas, CV.lt, Darbas |

---

## Legal & safe by design

- ✅ Only **reads public** job ads — no login bypass, no employer outreach.
- ✅ Sends messages only to **your own Telegram chat** — no spam.
- ✅ Human-in-the-loop: the bot drafts alerts, **you** decide which to apply to.
- ⚠️ Be polite: 2 scans/day + max 20 jobs/portal keeps you off anti-bot radars.

---

## Customizing

### Add or refine a portal's selectors

Edit `jobradar/config.py` → `COUNTRIES` to add a portal, then add its CSS selectors in `jobradar/scrapers/base.py` → `PORTAL_SELECTORS`. If you don't add selectors, the generic fallback will be used.

### Change scan frequency

Edit `.env`:
```
SCAN_CRON_HOURS=6,12,18   # 3x/day
```

### Filter by a different role

Add a new entry to `CATEGORIES` in `jobradar/config.py` with the keyword in each country's language.

---

## Deploy to Railway

1. Push this project to GitHub.
2. Create a new Railway project from the repo.
3. Set all env vars from `.env.example` in Railway's dashboard.
4. Railway will auto-detect Python. Set the start command:
   ```
   python run.py
   ```
5. Railway auto-installs `requirements.txt`. You also need Playwright browsers — add a build script:
   ```bash
   playwright install chromium --with-deps
   ```
   (Add this to a `Procfile` or Railway build command.)
6. Expose port 8000 (Railway does this automatically based on `WEBAPP_PORT`).

---

## Troubleshooting

**Gemini returns 429** → Free tier rate limit. Reduce `MAX_JOBS_PER_PORTAL` or increase delay between requests in `pipeline.py`.

**Telegram `chat not found`** → You haven't started a conversation with your bot yet. Send it `/start` first.

**A portal returns 0 jobs** → Likely the CSS selectors changed. Open the portal in a browser, inspect a job link, and update `PORTAL_SELECTORS` in `scrapers/base.py`.

**Playwright fails to launch** → Run `playwright install chromium` once.

**"no foreigners" filter missed an ad** → Gemini is the primary detector; add the ad's phrase to `FOREIGNER_PHRASES` in `pipeline.py` for a defensive regex backup.
