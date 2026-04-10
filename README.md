# PradBot-Finviz-To-Discord

This repository ships **two separate products** that live in the same folder and share some library code. You can use **either one**, or **both**; they do not depend on each other at runtime.

| Product | What it is | How it talks to Discord |
|--------|------------|-------------------------|
| **PradBot** | Long-running Discord **application** (`bot.py`) | Bot user + slash commands in channels |
| **Scan webhook posters** | One-shot **CLI scripts** (`post_scans_elite.py` / `post_scans_free.py`) | Incoming webhooks per channel (URLs in `webhooks.json`) |

**Shared code (not a third product):** The Elite webhook script and PradBot’s **`/scans`** command both use the same pipeline: **`fetch_elite.fetch_scan`**, **`scan_registry`**, and **`discord_payload.build_embeds`**. PradBot does **not** execute `post_scans_elite.py`; it calls the same Python functions directly so tables match the Elite poster.

---

## Product 1 — PradBot (`bot.py`)

Interactive **slash-command** bot: charts, options, news, quotes, group screens, channel purge, and on-demand screener tables.

### PradBot — command overview

| Command | What it does |
|---|---|
| `/chart AAPL` | **Daily** FinViz candlestick chart |
| `/chart MSFT Weekly` | **Weekly** chart (timeframe dropdown: Daily, Weekly, Monthly) |
| `/gex AAPL` | **GEX** (nearest future expiry or optional date) |
| `/zerodte AAPL` | **0DTE** OI-style analysis |
| `/news AAPL` | Latest **5** news links |
| `/quote AAPL` | Chart + OHLCV + change + recent days + headlines |
| `/scans` | **All scans** or **one** preset (FinViz Elite CSV + same embed style as Elite webhook poster) |
| `/evsize` | **EV grade** + **position sizing** for a trade (entry, target, stop, win prob, daily risk budget) |
| `/purge` | Delete messages (count or **all**, buttons for **all**) |
| `/groups Sector` | Sector / industry / country / cap aggregates |

Charts and FinViz data require a **FinViz Elite** subscription and **`FINVIZ_API_KEY`** in `.env`. **`/purge`** and **`/evsize`** only need Discord permissions (no FinViz key).

### PradBot — setup

#### 1) Clone repo and install dependencies (if you have not already)

```bash
git clone <your-repo-url>
cd "PradBot-Finviz-To-Discord"
pip install -r requirements.txt
```

#### 2) Environment file for PradBot

```bash
cp .env.example .env
```

Put **both** in `.env`:

- **`DISCORD_BOT_TOKEN`** — required. From the Developer Portal (**Bot** → token).
- **`FINVIZ_API_KEY`** — required for all FinViz-backed commands (`/chart`, `/gex`, `/zerodte`, `/news`, `/quote`, `/groups`, `/scans`). Omit only if you truly only use `/purge` (the bot still needs the Discord token to start).

#### 3) Discord application (you are the app owner)

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) while logged into the account that should **own** the application.
2. **Applications** → **New Application** → name it (e.g. `PradBot`) → **Create**.
3. Left sidebar → **Bot** → **Reset Token** (or **Copy**) → paste into `.env` as `DISCORD_BOT_TOKEN`.

> **Keep the token secret.** Reset it immediately if it leaks.

#### 4) Invite PradBot to your **server (guild)** with Administrator

PradBot is installed **per guild** using an OAuth2 invite URL (not the same thing as “webhook only”).

1. In the Developer Portal, left sidebar → **OAuth2** → **URL Generator**.
2. **Scopes:** enable **`bot`** and **`applications.commands`** (slash commands will not register without `applications.commands`).
3. **Bot permissions:** enable **`Administrator`**. That covers posting embeds and files, reading history, **`/purge`** (Manage Messages), and avoids missing toggles as features grow.
4. Copy the **generated URL**, open it in a browser, sign in as a Discord user who can **manage that server** (e.g. **Manage Server** or **Administrator** on the guild).
5. Choose the **server** → **Authorize**. Approve **bot** and **application commands** access when Discord asks.

After this, PradBot appears in the member list (offline until you run `bot.py`). You do **not** need **Message Content Intent** for these commands (slash + buttons).

#### 5) Slash command sync

On startup, `bot.py` calls global **`tree.sync()`**. Brand-new or changed commands can take **up to ~1 hour** to appear everywhere; later updates are usually faster.

#### 6) Run PradBot

```bash
python bot.py
```

You should see `Logged in as …` in the console.

### PradBot — slash reference (detail)

All commands use `/`. Dropdown parameters are shown in **bold**.

| Command | Description |
|---|---|
| `/chart <symbol> [timeframe]` | FinViz chart (**timeframe:** Daily, Weekly, Monthly) |
| `/gex <symbol> [expiry]` | GEX / options (optional YYYY-MM-DD) |
| `/zerodte <symbol>` | 0DTE analysis |
| `/news <symbol>` | 5 articles with links |
| `/quote <symbol>` | Quote panel + chart + news |
| `/evsize <side> <entry> <target> <stop> <probability> <daily_risk>` | EV grade (A+ … D) + Kelly-based position sizing (ephemeral reply) |
| `/purge <amount>` | Purge count or **all** (buttons for **all**); needs Manage Messages |
| `/scans <scan>` | **All scans** or one preset (**Included Scans**); needs `FINVIZ_API_KEY` |
| `/groups <group> [preset]` | Groups export (**group** / **preset** dropdowns) |

**Examples:**

```
/chart symbol:AAPL
/chart symbol:MSFT timeframe:Weekly
/gex symbol:AAPL
/zerodte symbol:SPY
/news symbol:TSLA
/quote symbol:MSFT
/purge amount:10
/purge amount:all
/evsize side:Long entry:185.00 target:195.00 stop:182.00 probability:55 daily_risk:1000
/evsize side:Short entry:420.00 target:400.00 stop:430.00 probability:60 daily_risk:2000
/scans scan:all
/scans scan:jeff_sun_canslim
/groups group:Sector
/groups group:Industry preset:Valuation
```

**What `/scans` does:** Uses **`fetch_elite.fetch_scan`** and **`discord_payload.build_embeds`** — the **same building blocks** as **`post_scans_elite.py`**, but posts into the channel via the bot. **All scans** sends many messages over several minutes.

**What `/gex` shows:** Net GEX, call/put walls, gamma flip, P/C ratio, top strikes (OI fallback if no gamma).

**What `/zerodte` shows:** Call/put OI walls, P/C, total OI, top strikes.

**What `/evsize` shows:** Takes **long/short**, **entry/target/stop**, **win probability** (0–100), and **daily risk budget** ($). Computes reward (R), risk (L), R:L ratio, EV per share, EV/R, full Kelly fraction, and applies **¼ Kelly** (capped at 50% of daily budget) to suggest a dollar risk for the trade and approximate share count. Grades the setup **A+ through D** based on EV/R. Reply is **ephemeral** (only visible to you). No FinViz key needed. Educational tool, not financial advice.

**What `/news` / `/quote` / `/groups` show:** As before (headlines, combined panel, group tables / CSV when large).

---

## Product 2 — Scan webhook posters (`post_scans_elite.py` / `post_scans_free.py`)

Separate **batch programs**: no bot token. You configure **Discord incoming webhook URLs** in JSON, run the script (or schedule it), and each configured scan posts to its webhook channel.

- **`post_scans_elite.py`** — FinViz Elite CSV exports; needs **`FINVIZ_API_KEY`** in `.env`.
- **`post_scans_free.py`** — Scrapes public FinViz HTML via [mariostoev/finviz](https://github.com/mariostoev/finviz); **no** API key; slower, rate-limit friendly.

These scripts **do not** start PradBot and **do not** require `DISCORD_BOT_TOKEN`.

### Webhook posters — setup

#### 1) Same clone / `pip install` as above (one venv is fine)

#### 2) Create webhooks in Discord

Server **Settings** → **Integrations** → **Webhooks** → **New Webhook** per target channel → copy each URL.

#### 3) `webhooks.json`

```bash
cp webhooks.example.json webhooks.json
```

Map **scan IDs** (see **Included Scans** below) to URLs:

```json
{
    "qulla_episodic": "https://discord.com/api/webhooks/1234567890/abcdef...",
    "jeff_sun_canslim": "https://discord.com/api/webhooks/1234567890/ghijkl..."
}
```

Only listed IDs run. **`webhooks.json`** is gitignored.

#### 4) `.env` for Elite poster only

For **`post_scans_elite.py`**, set **`FINVIZ_API_KEY`** in `.env`. The free poster does not need it.

### Webhook posters — run

```bash
python post_scans_elite.py
# or
python post_scans_free.py
```

### Webhook posters — CLI flags

| Flag | Description |
|---|---|
| `--config PATH` | Webhooks JSON (default: `webhooks.json`) |
| `--dry-run` | Fetch and build embeds; do not POST |
| `--verbose` / `-v` | Debug logging |

```bash
python post_scans_free.py --dry-run --verbose
python post_scans_elite.py --config my_webhooks.json
```

### Webhook posters — scheduling

Scripts exit after one run. Use Task Scheduler, cron, etc.:

**Windows:** Task → run `python post_scans_elite.py` (or `post_scans_free.py`) with **Start in** = this folder.

**Linux/macOS:**

```bash
30 16 * * 1-5 cd /path/to/PradBot-Finviz-To-Discord && python post_scans_elite.py
```

### Free vs Elite (webhook posters)

| | Free | Elite |
|---|---|---|
| **API key** | No | Yes (Elite) |
| **Source** | HTML scrape | `elite.finviz.com` CSV |
| **Speed** | ~3–5 min all scans | ~30–60 sec typical |
| **Rate limits** | Aggressive delays + retries | Milder (`FINVIZ_ELITE_DELAY_SEC`) |
| **`tad_*` filters** | Stripped | Full |

---

## Included Scans (shared IDs)

Used as keys in **`webhooks.json`** and as **`/scans`** choices (except the synthetic **All scans** option).

| Scan ID | Name |
|---|---|
| `qulla_episodic` | Qullamaggie — Episodic Pivot |
| `qulla_breakouts` | Qullamaggie — Breakouts |
| `qulla_parabolic_short` | Qullamaggie — Parabolic Short |
| `jeff_sun_canslim` | Jeff Sun — CANSLIM |
| `jeff_sun_high_adr` | Jeff Sun — High ADR% Hottest Stock |
| `jeff_sun_extended_bases` | Jeff Sun — Extended Bases |
| `jeff_sun_1w20` | Jeff Sun — Strongest 1-Week +20% |
| `jeff_sun_4w30` | Jeff Sun — Strongest 1-Month +30% |
| `jeff_sun_4w50` | Jeff Sun — Strongest 1-Month +50% |
| `jeff_sun_13w50` | Jeff Sun — Strongest 3-Month +50% |
| `jeff_sun_26w100` | Jeff Sun — Strongest 6-Month +100% |
| `jeff_sun_ipo` | Jeff Sun — IPO |
| `jeff_sun_high_short_float` | Jeff Sun — High Short Float |
| `jeff_sun_liquid_etfs` | Jeff Sun — Liquid ETFs |
| `julian_komar_strongest` | Julian Komar — Strongest Stocks |
| `earnings_calendar_week` | Earnings Calendar — This Week |

Each scan is sorted by daily change % (descending) and capped at **50** tickers.

---

## File structure (by product)

```
PradBot-Finviz-To-Discord/

  # PradBot
  bot.py                 # PradBot entry (slash commands)
  ev_position_sizing.py  # EV grade + Kelly position sizing (/evsize)

  # Webhook posters
  post_scans_elite.py
  post_scans_free.py
  webhooks.example.json  # Copy → webhooks.json (webhook product only)

  # Shared by /scans and post_scans_elite
  scan_registry.py
  fetch_elite.py
  discord_payload.py

  # PradBot + Elite poster helpers
  finviz_chart.py
  finviz_groups.py
  finviz_options.py
  finviz_news.py
  finviz_quote.py
  gex_compute.py

  # Free webhook poster only
  fetch_free.py

  .env.example
  .gitignore
  requirements.txt
```

---

## Troubleshooting

**Webhook posters — "Webhook config not found"** — Create `webhooks.json` from `webhooks.example.json`.

**Webhook posters — "No valid webhook URLs"** — Use real `https://discord.com/api/webhooks/...` URLs.

**Webhook posters — 429 (free)** — Increase `FINVIZ_FREE_DELAY_SEC` if needed.

**PradBot — slash commands missing** — Wait for global sync (~up to 1 hour first time); restart `bot.py`; invite must include **`applications.commands`**.

**PradBot — `/scans` asks for `FINVIZ_API_KEY`** — Set it in `.env` next to `DISCORD_BOT_TOKEN`.

**PradBot — chart / FinViz errors** — Confirm Elite subscription and `FINVIZ_API_KEY`.

**"No results for this scan"** — Normal on some days for some screens.

**Discord 400 (embed too large)** — Rare; scans cap at 50 rows; large tables split.

**"Gamma data not available"** — Try a further expiry on `/gex`.

---

## License

Data from [FinViz](https://finviz.com) — see their [Terms of Service](https://finviz.com/terms.ashx). Free scraping uses [mariostoev/finviz](https://github.com/mariostoev/finviz).
