# PradBot-Finviz-To-Discord

This repository ships **two** standalone products that live in the same folder and share library code. Use either or both; they do not depend on each other at runtime.

| Product | What it is | How it talks to Discord |
|--------|------------|-------------------------|
| **PradBot** | Long-running Discord **application** (`bot.py`) | Bot user + slash commands in channels (including **`/heatmap`** via shared pipeline code) |
| **Scan webhook posters** | One-shot **CLI scripts** (`post_scans_elite.py` / `post_scans_free.py`) | Incoming webhooks per channel (URLs in `webhooks.json`) |

**Shared code (not a third product):** The Elite webhook script and PradBot’s **`/scans`** command both use the same pipeline: **`fetch_elite.fetch_scan`** / **`fetch_scan_with_screener`** (for the correct FinViz link, including Top Gainers/Losers), **`scan_registry`**, and **`discord_payload.build_embeds`**. PradBot does **not** execute `post_scans_elite.py`; it calls the same Python functions directly so tables match the Elite poster.

### Recent changes (at a glance)

- **`/top_moc_movers`** — [Massive](https://massive.com) / Polygon only (**`MASSIVE_API_KEY`** or **`POLYGON_API_KEY`**): **grouped daily** → universe sorted by **dollar** day volume (close×volume, default) or **shares** (**`MOC_UNIVERSE_SORT`**), min price via **`MOC_MIN_PRICE`**. **Per-ticker** minute bars **3:49–4:05 PM ET**; **$10m** = Σ(v×close) for **3:50–3:59**; **RV10** = today’s last-10m **share** volume ÷ **average** of the same 10m window on **`MOC_RVOL_LOOKBACK`** prior RTH days (from **SPY** calendar), ≥2 prior **non-zero** days required. After optional **`MOC_MIN_NOTIONAL_10M` / `MOC_MIN_VOL_10M`**, the top **`MOC_RVOL_CANDIDATES`** by **$10m** get historical fetches. Leaderboards sort by **|move|×(1+log $10m)×RV10** (3:50→3:59 and 1m proxy). **Top 20** by **composite 1m** get **v3 trades** (3:59:45→~4:00). Optional **`session_date`**, **`top_n`**. **Copy — symbols**. No **`FINVIZ_API_KEY`**.
- **Copy — symbols** — List-style slash replies that return **many tickers** are followed by a **second embed** titled **Copy — symbols (comma-separated)**: a monospace code block of tickers (e.g. for TradingView watchlists). Covered: **`/scans`**, **`/top_gainers`**, **`/top_losers`**, **`/ah_movers`**, **`/inplay`** (all scanners), **`/earnings`**, **`/ipo`**, **`/top_moc_movers`**. **`/heatmap` does not** include this (universes can be hundreds of names). Very long lists are truncated safely for Discord embed limits.
- **`/inplay` · Earnings** — **%EAVOL** uses **today’s pre-market volume only** vs 21-day ADV for **BMO** (before-open) names; **yesterday AMC** names still use **prior after-hours + today pre-market**. The main embed shows **full detail for up to 10** tickers (highest %EAVOL first); a comma list includes **all** names returned for that screen.
- **`/news`** — Headlines come from the **same `#news-table` list** as [Finviz’s ticker quote page](https://finviz.com/quote.ashx) (parsed HTML), not the Elite **`news_export.ashx?v=1`** CSV (that feed can differ from the quote UI). **No `FINVIZ_API_KEY` required** for normal use; if the table cannot be parsed, the bot falls back to Elite CSV when a key is set.
- **`/econ`** — [Investing.com](https://www.investing.com/economic-calendar/) economic calendar (**US + Canada**, **medium & high** importance, all categories). **`period`** (like `/earnings`): **Today** (default) or **This week (Mon–Sun NY)**. No FinViz key. (Rare **403** — see detail section.)
- **`/ipo`** — [IPOScoop](https://www.iposcoop.com/ipo-calendar/) IPO table (compact labeled lines; no SCOOP columns). **`period`:** **Today** (default) or **Full calendar**. No FinViz key.
- **`/earnings`** — FinViz Elite **v=152** export with **`earningsdate_today`** / **`earningsdate_thisweek`**; export sorted **`o=-marketcap`**. Rows are reordered by **market cap (desc)**, then **session** (before-open / BMO and **8:30 AM** → **BMO**, after-close / AMC and **4:30 PM** → **AMC**, then other times). Monospace tables (ticker, **time**, price, volume, avg vol, change %). **Weekly** view groups rows under **`— Apr 10 —`**-style day headers. Embed links to the matching screener.
- **`/heatmap`** — **Nested treemap** from FinViz **v=152** full export (sector → industry → stocks; size = market cap, color = change %). **Universe** dropdown only: **S&P 500** (default), **NASDAQ 100**, **Dow**, **Russell 2000** (stocks and ETFs in that index column). Can take **1–3 minutes** (large CSV).
- **`/top_opps`** — **Four** FinViz-style timeframes (**1m, 5m, 1h, daily**) as chart PNGs **plus** a **v=152** snapshot embed (OHLCV, gap, **P/E**, **Mkt Cap**, **Share Float**, **Short Float**, **Sector**, **Industry**, **Sector/Theme**, **Country**, news, recent days — float/mcap use FinViz-scale parsing: plain **Market Cap** without a suffix → **millions of USD**; **Shares Float** uses float-specific scaling so megacaps don’t show as **M** instead of **B**). **Default** (no levels): pulls charts from **FinViz** (`FINVIZ_API_KEY`). **Execution study** (optional **entry** + **stop**; **exit** optional): builds **Massive** OHLC charts with **mplfinance** — **dark** theme, **9/21 EMA**, **50/100/200 SMA**, **VWAP** on **1m/5m/1h** only, horizontal **entry / stop / exit** lines; intraday charts use **today’s regular session (ET)** (extended history only warms MAs). **Exit** omitted → before **4:00pm ET** default target = **last traded** (quote close); **at/after 4:00pm ET** = **regular session close** from minute aggregates (fallback: quote). Optional **notes** appear as a field **below the image** on the **last** chart embed. Embed lines: **R:R**, **EV/Share** (at 50% probability), **Side: Long/Short**. Requires **`MASSIVE_API_KEY`** or **`POLYGON_API_KEY`** for study mode.
- **`/inplay`** — FinViz Elite **In play** screener (default): **news today or yesterday**, price **>$1**, avg vol **>1M**, current vol **>500K**, relative vol **>1.5**, sorted by **change %**; table with **`[news](…)`** per row (**v=152** export; screener **v=151**). Optional **`scanner: Small caps`**: market cap **$5M–$2B**, current vol **>1M**, rel vol **>1.5**; embed links the **v=152** screener (same columns as export); wider table with country, market cap, **float** (K/M/B), short float, and **`[news](…)`** per row from the **News URL** column (fallback: quote news tab). Optional **`scanner: Earnings`**: FinViz filter **earnings yesterday AMC | today BMO** plus **avg vol >1M** and **price >$1**; **[Massive](https://massive.com)** REST (`MASSIVE_API_KEY`) computes **%EAVOL** vs **21-day average daily volume**: **BMO** (today pre-market) names use **today 4:00–9:30 AM ET** volume only; **AMC** (yesterday after-hours) names use **prior 4:00–8:00 PM ET + today pre-market**; sorted high→low; tier emojis on each ticker field (🔥 / 🟡 / ⬜); **multi-field** embed (up to **10** tickers in full detail) with company line, **Mkt cap** / **Float** / **Short float** / **Country** (float and mcap shown compact **K/M/B**; plain **Market Cap** cells without a suffix are interpreted as **millions of USD**, matching FinViz’s screener export), sector/theme, EPS/Rev report-quarter lines, guidance, Gap/ATR; needs **`FINVIZ_API_KEY`** + **`MASSIVE_API_KEY`** (or **`POLYGON_API_KEY`**).
- **Slash sync** — **`GUILD_ID`** accepts **comma-separated** IDs for instant guild registration; **default when `GUILD_ID` is set** is **guild-only** (no duplicate slash lines). Set **`SLASH_SYNC_GLOBAL_ALSO=1`** for **guild + global** (other servers within ~1 hour; test guild may briefly show duplicates). **`SLASH_CLEAR_GLOBAL_FOR_DEDUPE`** clears stale globals when not using global sync (see **§5**).
- **`top_gainers` / `top_losers`** — Registered in **`scan_registry.py`** (`ta_topgainers` / `ta_toplosers`); **`fetch_scan_with_screener`** supplies **v=152** screener URLs for embeds. Webhook posters and **`/scans`** share the same pipeline; slash movers are top **10** with optional filters; batch presets cap at **50** rows (**Included Scans**).
- **`/markets` removed** — The experimental multi-futures snapshot slash command and **`finviz_markets.py`** were dropped; **`finviz_chart`** no longer exposes futures-only helpers. Use **`/chart`** per symbol as needed.
---

## Product 1 — PradBot (`bot.py`)

Interactive **slash-command** bot: charts, options, news, quotes, channel purge, and on-demand screener tables.

### PradBot — command overview

| Command | What it does |
|---|---|
| `/chart AAPL` | FinViz candlestick chart (**default: Daily**) |
| `/chart MSFT` | **Timeframe** dropdown: **1m, 3m, 5m, 15m, 30m, 1h**, **Daily**, **Weekly**, **Monthly** |
| `/gex AAPL` | **GEX** (nearest future expiry or optional date) |
| `/zerodte AAPL` | **0DTE** OI-style analysis |
| `/news AAPL` | Latest **5** headlines — same list as the quote page **News** table (public Finviz HTML); Elite CSV only as fallback |
| `/econ` | **Investing.com** — **`period`:** Today (default) or this week Mon–Sun NY; US+CA, medium/high |
| `/ipo` | **IPOScoop** — **`period`:** Today (default) or full calendar; **Copy — symbols** (proposed tickers, TBA omitted) |
| `/quote AAPL` | Chart + OHLCV + **P/E**, **Mkt Cap**, **Float**, **Short Float**, **Sector**, **Industry** + recent days + headlines (v=152 snapshot; cap/float compact **K/M/B/T**) |
| `/scans` | **All scans** or **one** preset (FinViz Elite CSV + same embed style as Elite webhook poster); **Copy — symbols** after each scan’s table(s) |
| `/top_gainers` | Today's **top 10 gaining** stocks by change %; optional price/volume filters; **Copy — symbols** follow-up |
| `/top_losers` | Today's **top 10 losing** stocks by change %; optional price/volume filters; **Copy — symbols** follow-up |
| `/ah_movers` | **Top 5 each**: AH **+3%+** and **−3%+** movers (Elite); **Copy — symbols** follow-up (gainers first, then losers) |
| `/top_moc_movers` | **MOC** — **$10m** notional (last 10m RTH) + **10m RVOL** + composite **close** moves (Massive); optional **session_date**, **top_n**; **`MASSIVE_API_KEY`**; **Copy — symbols** |
| `/earnings` | **Today** or **this week** earnings (FinViz Elite); sorted by **mkt cap** then **session** (BMO/AMC-style); time, price, volumes, change %; **Copy — symbols** follow-up |
| `/inplay` | **In play** (default): news + liquidity + rel vol; **`[news](url)`** per row; **Copy — symbols** follow-up. Optional **Small caps**: cap $5M–$2B, vol + rel vol; **v=152** screener link; extra float/cap columns + **`[news](url)`**. Optional **Earnings**: AMC/BMO screen + **%EAVOL** via Massive (**`MASSIVE_API_KEY`**); up to **10** detailed fields; **Copy — symbols** lists **all** matches |
| `/heatmap` | **Nested treemap** by index universe (S&P 500 default); slow full-export pull (**no** symbol copy embed — too many names) |
| `/top_opps` | **1m / 5m / 1h / daily** charts + snapshot (fundamentals + sector/industry/theme/country); optional **entry**+**stop**, **exit**, **notes**; **Massive** study PNGs with levels & MAs |
| `/evsize` | **EV grade** + **position sizing** (entry, target, stop, win prob, daily risk); letter grade from **EV/R** with **stricter defaults**; optional **`EVSIZE_GRADE_*`** env tuning |
| `/purge` | Delete messages (count or **all**); confirms **all** with buttons; replies warn that deletion is permanent |
| `/help` | Command list (grouped); optional **Documentation** link when **`README_URL`** is set — no host/API-key boilerplate in the embed |

Charts and most FinViz data require a **FinViz Elite** subscription and **`FINVIZ_API_KEY`** in `.env`. **`/news`** uses the public quote page and does **not** need a key unless the HTML fallback fails and you want Elite CSV backup. **`MASSIVE_API_KEY`** (or **`POLYGON_API_KEY`**) is required for **`/inplay`** with **`scanner: Earnings`**, **`/top_moc_movers`**, and for **`/top_opps`** **execution study** mode (entry + stop). **`/top_moc_movers`** does **not** use **`FINVIZ_API_KEY`**. **`/purge`**, **`/evsize`**, **`/econ`**, **`/ipo`**, and **`/news`** (typical path) do not need a FinViz key (`/purge` / `/evsize` need appropriate Discord permissions).

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

Put these in `.env`:

- **`DISCORD_BOT_TOKEN`** — required. From the Developer Portal (**Bot** → token).
- **`FINVIZ_API_KEY`** — required for FinViz-backed commands (`/chart`, `/gex`, `/zerodte`, `/quote`, `/scans`, `/top_gainers`, `/top_losers`, `/earnings`, `/inplay`, `/heatmap`, …). **`/news`** works without it (quote-page HTML); optional for CSV fallback if parsing fails. Not needed if you only use **`/purge`**, **`/evsize`**, **`/econ`**, **`/ipo`**, and **`/news`** (the bot still needs the Discord token to start).
- **`MASSIVE_API_KEY`** — required for **`/inplay`** with **`scanner: Earnings`** (extended-hours volume vs 21-day avg), for **`/top_moc_movers`** (grouped daily + minutes + last-10m notional/RVOL + trades), and for **`/top_opps`** when you pass **entry** + **stop** (Massive aggregates + **mplfinance** charts). [Massive](https://massive.com) REST (`api.massive.com`). You can use **`POLYGON_API_KEY`** instead (same token after Polygon → Massive rebrand). **MOC** tuning: **`MOC_MAX_TICKERS`**, **`MOC_WORKERS`**, **`MOC_UNIVERSE_SORT`** (`dollar`|`shares`), **`MOC_MIN_PRICE`**, **`MOC_MIN_NOTIONAL_10M`**, **`MOC_MIN_VOL_10M`**, **`MOC_MIN_RVOL_10M`**, **`MOC_RVOL_LOOKBACK`**, **`MOC_RVOL_CANDIDATES`** (see “What `/top_moc_movers`” below; composite scan can be **slow** — lower caps if needed).
- **`README_URL`** (optional) — Public URL to your repo **README** (e.g. `https://github.com/org/repo#readme`). Used by **`/help`** as the documentation link. In a local **`.env`**, wrap the value in **quotes** if it contains `#`. On **Railway**, enter the URL **without** extra quote characters in the value field (the bot strips accidental surrounding quotes). Aliases: **`GITHUB_README_URL`**, **`DOCS_URL`**.
- **`GUILD_ID`** (optional) — **test server ID(s)** for **instant** slash updates; **by default** the bot registers **only on those guilds** (no global) so you do not see duplicate slash commands (see **§5**). Set **`SLASH_SYNC_GLOBAL_ALSO=1`** to also sync globally. Use **`SLASH_GUILD_ONLY=1`** to force guild-only if you use **`SLASH_SYNC_GLOBAL_ALSO`** but need to override. Leave **`GUILD_ID`** blank for **global‑only** registration.
- **`/evsize` grading (optional)** — Letter grades use **EV/R** = (EV per share) ÷ (risk per share **L**), not raw dollar EV. Defaults are **stricter** than legacy builds (e.g. **A+** needs about **0.38+** EV/R unless you override). Set **`EVSIZE_GRADE_CONSERVATISM`** to a value **below 1.0** (e.g. **`0.90`**) to apply that factor to **EV/R before the letter only** (models execution drag, cautious sizing, or targets that often do not play out); **Kelly sizing** still uses your entered probability and levels. Per-tier overrides: **`EVSIZE_GRADE_A_PLUS_MIN_EVR`**, **`EVSIZE_GRADE_A_MIN_EVR`**, **`EVSIZE_GRADE_A_MINUS_MIN_EVR`**, **`EVSIZE_GRADE_B_PLUS_MIN_EVR`**, **`EVSIZE_GRADE_B_MIN_EVR`**, **`EVSIZE_GRADE_B_MINUS_MIN_EVR`**, **`EVSIZE_GRADE_C_MIN_EVR`** — see **`ev_position_sizing.py`** (`DEFAULT_GRADE_EVR_THRESHOLDS`).

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

#### 5) Slash command sync (global vs guild — instant updates)

**You do not need to re-invite the bot** when you add or change commands. The delay people hit is Discord’s **global** command propagation, not the invite.

| Mode | `.env` | Behavior |
|------|--------|----------|
| **Global only** | No `GUILD_ID` | Commands register for **all servers**; updates can take **up to ~1 hour** everywhere. |
| **Guild only (default when `GUILD_ID` set)** | `GUILD_ID=<test id(s)>` | **Guild** sync only: instant on listed server(s). **No** global — other servers **won’t** get commands unless you add **`SLASH_SYNC_GLOBAL_ALSO=1`**. |
| **Dual sync (test + global)** | `GUILD_ID=…` and `SLASH_SYNC_GLOBAL_ALSO=1` | Guild sync (instant) **and** global sync (other servers within ~**1 hour**). Your test guild **may show duplicate** slash entries (guild + global). |
| **Force guild-only** | `GUILD_ID=…` and `SLASH_GUILD_ONLY=1` | Overrides **`SLASH_SYNC_GLOBAL_ALSO`**: commands **only** on listed guilds. |

**How to get your server (guild) ID**

1. In Discord, open **User Settings** (gear) → **App Settings** → **Advanced**.
2. Turn **Developer Mode** **On**.
3. Close settings, **right‑click your server icon** (or server name in the list) → **Copy Server ID**.
4. Add one line to `.env`:

```
GUILD_ID=123456789012345678
```

For **multiple servers** with instant sync, use commas (spaces optional):

```
GUILD_ID=111111111111111111,222222222222222222
```

(Use real IDs; they are usually 17–19 digits.)

5. Restart `bot.py`. With **`GUILD_ID` only**, logs show **guild** sync (instant). Add **`SLASH_SYNC_GLOBAL_ALSO=1`** if you also want **global** sync logs and updates on other servers (~1 hour).

**Production without a test guild:** Remove `GUILD_ID` so only **global** sync runs.

**Other servers need commands too:** Set **`SLASH_SYNC_GLOBAL_ALSO=1`** alongside **`GUILD_ID`**, or remove **`GUILD_ID`** and rely on global-only sync.

**Re-invited the bot or joined a new server and see no commands?** With **global** or **dual** sync, wait for **global** propagation (~1 hour). With **guild-only** (`GUILD_ID` without **`SLASH_SYNC_GLOBAL_ALSO`**), only listed guilds have commands — add that server’s ID to **`GUILD_ID`** or enable global sync.

**Duplicate `/command` lines:** Usually from **dual** sync (guild + global) or **stale globals** after changing modes. Prefer **guild-only** (default with **`GUILD_ID`**) or run **once** with **`SLASH_CLEAR_GLOBAL_FOR_DEDUPE=1`** while **not** using **`SLASH_SYNC_GLOBAL_ALSO`** to wipe old global registrations. **Do not** use dedupe when **global** sync is enabled — it would remove commands from servers that only have globals.

#### 6) Run PradBot

```bash
python bot.py
```

You should see `Logged in as …` in the console.

### PradBot — slash reference (detail)

All commands use `/`. Dropdown parameters are shown in **bold**.

| Command | Description |
|---|---|
| `/chart <symbol> [timeframe]` | FinViz chart (**timeframe:** 1m–1h intraday, Daily, Weekly, Monthly) |
| `/gex <symbol> [expiry]` | GEX / options (optional YYYY-MM-DD) |
| `/zerodte <symbol>` | 0DTE analysis |
| `/news <symbol>` | **5** headlines from the quote page **News** table (same order as Finviz); Elite CSV fallback if parse fails and key is set |
| `/econ [period]` | Investing.com (US+CA, medium/high); **period** = today (default) or week; no `FINVIZ_API_KEY` |
| `/ipo [period]` | IPOScoop (no SCOOP columns); **period** = today (default) or full calendar; no `FINVIZ_API_KEY`; **Copy — symbols** |
| `/quote <symbol>` | Quote + chart + news + **P/E**, **Mkt Cap**, **Float**, **Short Float**, **Sector**, **Industry** (Elite v=152) |
| `/top_gainers [min_price] [min_volume]` | Top 10 gainers today; optional price/volume floor; needs `FINVIZ_API_KEY`; **Copy — symbols** |
| `/top_losers [min_price] [min_volume]` | Top 10 losers today; optional price/volume floor; needs `FINVIZ_API_KEY`; **Copy — symbols** |
| `/ah_movers` | Top **5** each: AH **+3%+** and **−3%+**; needs `FINVIZ_API_KEY`; **Copy — symbols** |
| `/top_moc_movers [session_date] [top_n]` | **$10m** + **10m RVOL** + **composite** 3:50↔3:59 & 1m O/C; trade-refine pool; `MASSIVE_API_KEY` or `POLYGON_API_KEY`; **Copy — symbols** |
| `/earnings [period]` | **Today** or **Weekly** earnings (**v=152**); sort **-marketcap** then BMO/AMC-style session; monospace table; needs `FINVIZ_API_KEY`; **Copy — symbols** |
| `/inplay [scanner]` | **In play** — **Default:** news today/yesterday, price >$1, avg vol >1M, vol >500K, rel vol >1.5; **`[news](url)`** per row; screener **v=151**; **Copy — symbols**. **Small caps:** cap $5M–$2B, cur vol >1M, rel vol >1.5; **v=152** screener; country, MCap, float (K/M/B), short %, **`[news](url)`**. **Earnings:** AMC/BMO + liquidity; **%EAVOL** (BMO = PM only vs 21d ADV; AMC = prior AH + PM); up to **10** detailed fields; needs `FINVIZ_API_KEY` + `MASSIVE_API_KEY` (or `POLYGON_API_KEY`) |
| `/heatmap [universe]` | Nested performance treemap: **S&P 500** (default), **NASDAQ 100**, **Dow**, **Russell 2000**; needs `FINVIZ_API_KEY` (no symbol-copy embed) |
| `/top_opps <symbol> [entry] [stop] [exit] [notes]` | Four charts + snapshot. **No levels:** FinViz PNGs. **Entry + stop:** Massive study (`MASSIVE_API_KEY`); **exit** optional (time-based default); **notes** optional (last chart embed, below image) |
| `/evsize <side> <entry> <target> <stop> <probability> <daily_risk> [kelly_fraction]` | EV grade (A+ … D) + Kelly sizing (**¼ / ½ / full** Kelly, default **½**); grade uses **EV/R** thresholds (not dollar EV); optional **`EVSIZE_GRADE_CONSERVATISM`**; ephemeral (or public) |
| `/purge <amount>` | Purge count or **all** (buttons for **all**); needs Manage Messages |
| `/help` | Command list + optional docs link via **`README_URL`** |
| `/scans <scan>` | **All scans** or one preset (**Included Scans**); needs `FINVIZ_API_KEY`; **Copy — symbols** per scan |

**Examples:**

```
/help
/chart symbol:AAPL
/chart symbol:MSFT timeframe:Weekly
/chart symbol:SPY timeframe:5 minute
/chart symbol:TSLA timeframe:1 hour
/gex symbol:AAPL
/zerodte symbol:SPY
/news symbol:TSLA
/econ
/econ period:This week (Mon–Sun NY)
/ipo
/ipo period:Full calendar
/quote symbol:MSFT
/purge amount:10
/purge amount:all
/evsize side:Long entry:185.00 target:195.00 stop:182.00 probability:55 daily_risk:1000
/evsize side:Long entry:185.00 target:195.00 stop:182.00 probability:55 daily_risk:1000 kelly_fraction:Quarter Kelly (¼)
/evsize side:Short entry:420.00 target:400.00 stop:430.00 probability:60 daily_risk:2000
/top_gainers
/top_gainers min_price:5 min_volume:500000
/top_losers
/top_losers min_price:10
/ah_movers
/top_moc_movers
/top_moc_movers session_date:2026-04-17 top_n:15
/inplay
/inplay scanner:Small caps
/inplay scanner:Earnings
/earnings period:Today
/earnings period:Weekly (this week)
/heatmap
/heatmap universe:NASDAQ 100
/top_opps symbol:SNDK
/top_opps symbol:AAPL entry:185.50 stop:182.00
/top_opps symbol:AAPL entry:185.50 stop:182.00 exit:195.00
/top_opps symbol:AAPL entry:185.50 stop:182.00 notes:Gap on earnings beat; trimmed into close
/scans scan:all
/scans scan:jeff_sun_canslim
```

**What `/help` shows:** Embeds a **command list** (grouped by category) and, when **`README_URL`** (or **`GITHUB_README_URL`** / **`DOCS_URL`**) is set, a single **Documentation** link. The description does not repeat Railway/env/API-key setup (that belongs in this README or your host docs). No FinViz key required.

**What `/chart` shows:** Downloads a **candlestick PNG** from **`elite.finviz.com/chart.ashx`** (`ty=c`, `ta=1`, `s=l`) with **`p=`** set from the timeframe: **1 / 3 / 5 / 15 / 30 minute** (`i1`–`i30`), **1 hour** (`h`), **Daily / Weekly / Monthly** (`d` / `w` / `m`). Default is **Daily**. Intraday charts need **FinViz Elite** (real-time / extended-hours behavior per FinViz). Requires `FINVIZ_API_KEY`.

**What `/scans` does:** Uses **`fetch_elite.fetch_scan_with_screener`** (rows + FinViz URL for the embed link), **`discord_payload.build_embeds`** — the **same building blocks** as **`post_scans_elite.py`**, but posts into the channel via the bot. **All scans** sends many messages over several minutes. After each scan’s embed(s), a **Copy — symbols** follow-up lists comma-separated tickers from that scan.

**What `/gex` shows:** Net GEX, call/put walls, gamma flip, P/C ratio, top strikes (OI fallback if no gamma).

**What `/zerodte` shows:** Call/put OI walls, P/C, total OI, top strikes.

**What `/top_gainers` / `/top_losers` show:** A monospace table of the **top 10** stocks by daily change % (gainers sorted highest first, losers most negative first). Columns: ticker, price, change %, volume. Data is pulled from the Elite CSV export using the same column layout as other scans in this repo (`v=141`); the embed **link** opens the **v=152** screener view. Optional **`min_price`** and **`min_volume`** filter before slicing to 10. **`min_volume`** is in **shares** (e.g. `1000000` for one million); the CSV volume column is treated as **thousands** by default and converted to shares for filtering and display. Override with **`FINVIZ_MOVERS_VOLUME_CSV_UNIT=shares`** in `.env` if your export uses full shares. A **Copy — symbols** embed follows with the same **10** tickers, comma-separated. Requires `FINVIZ_API_KEY`.

**What `/ah_movers` shows:** One embed with two tables (**AH +3%+** and **AH −3%+**, up to **5** names each). A **Copy — symbols** follow-up lists tickers (**gainers first**, then losers). Requires `FINVIZ_API_KEY`.

**What `/top_moc_movers` shows:** **`GET /v2/aggs/grouped/.../stocks/{date}`** → US names for the **session**; keeps **close ≥ `MOC_MIN_PRICE`** (default **$1**). Sort: **`MOC_UNIVERSE_SORT`=`dollar`** (default) = day **close×volume** desc, or **`shares`** = day volume. Cap **`MOC_MAX_TICKERS`**. For each, **1m aggs 3:49–4:05 PM ET**: **$10m** = Σ(**v**×**close**) for bars with ET start **3:50–3:59**; **3:50→3:59** % = (close(3:59)−close(3:50))/close(3:50) (last RTH min fallback as before). **1m O/C%** = last RTH **minute** (usually 3:59). Optional gates **`MOC_MIN_NOTIONAL_10M`**, **`MOC_MIN_VOL_10M`**. Top **`MOC_RVOL_CANDIDATES`** by **$10m** get **per-day** minute pulls for the **same 10m** on each of **`MOC_RVOL_LOOKBACK`** prior RTHs (from **SPY** daily list); **RV10** = (today’s 10m share vol) / (mean of prior 10m vols) with **≥2** **non-zero** priors, else **RV10** omitted. Leaderboards: only that **$10m pool** (after min **RV10** if **`MOC_MIN_RVOL_10M`>0**), sorted by **|move%|×(1+log $10m)×max(RV10,0.25)** (or RV10=1 if missing and min RV off). **Top 20** by **composite 1m** get **`/v3/trades`** 3:57–4:00:00.999999; **Trades%** = last print ≤4:00 vs ≤3:59:45. **Default session** = last **completed** SPY day. **No `FINVIZ_API_KEY`**. Runtime grows with **candidates×lookback**; reduce **`MOC_RVOL_CANDIDATES`** or **`MOC_RVOL_LOOKBACK`** if slow.

**What `/earnings` shows:** Pulls the Elite **export.ashx** for **`earningsdate_today`** or **`earningsdate_thisweek`** with **`o=-marketcap`**. Rows are sorted by **market cap (descending)**, then **session** (before-open group first: **BMO**, **before market open**, **8:30 AM**, and other **AM** times; then after-close: **AMC**, **after close**, **4:30 PM**, **PM**). **Time** column labels **BMO** / **AMC** where FinViz uses those or the **8:30 AM** / **4:30 PM** patterns. Tables list **Ticker**, **Time**, **Price**, **Volume**, **AvgVol**, **Chg%** (volumes compact K/M/B). **Weekly** mode inserts **`— Apr 10 —`**-style section lines between days (month + day, no year). Title and embed **URL** match the period (**`&o=-marketcap`** on screener links). Embed footer references **v=152** export. A **Copy — symbols** follow-up lists tickers in table order. Requires `FINVIZ_API_KEY`.

**What `/inplay` shows:** **Default** (`scanner` omitted or **Default**): FinViz filters **news today|yesterday**, **sh_avgvol_o1000** (avg vol >1M), **sh_curvol_o500** (>500K current volume), **sh_price_o1** (>$1), **sh_relvol_o1.5** (rel vol >1.5), ordered by **change %** (descending). **v=152** Elite export (full columns so **News URL** is present); embed **URL** is the **v=151** screener. Up to **20** rows (Symbol, Price, Change, Vol, News) with **`[news](…)`** links; missing **News URL** falls back to the quote **news** tab. **Small caps** (`scanner: Small caps`): **`cap_0.005to2,sh_curvol_o1000,sh_relvol_o1.5`** — market cap **$5M–$2B**, current vol **>1M**, rel vol **>1.5**. Same **v=152** Elite export; embed **title URL** opens the **v=152** screener (with the same custom column set as the export). Table: **Country**, **MCap**, **Float** (K/M/B), **Shrt%**, and **`[news](…)`** using **News URL** when present (else quote **news** tab). **Copy — symbols** follows for the same tickers as the table. **Earnings** (`scanner: Earnings`): FinViz **`earningsdate_yesterdayafter|todaybefore,sh_avgvol_o1000,sh_price_o1`** — embed links the **v=151** screener; CSV from **v=152** export. **[Massive](https://massive.com)** REST (`https://api.massive.com`, Bearer token) loads minute bars for **prior session 16:00–20:00 ET** and **current session 04:00–09:30 ET**, daily bars for **21-trading-day average volume**. **%EAVOL** = extended-hours volume ÷ that average × 100, where **BMO** (before-open) names use **today pre-market (04:00–09:30 ET) only**; **AMC** (after-close) names use **prior AH + today PM**. Matching symbols are shown as **up to 10** embed fields (one per ticker), sorted by **%EAVOL** (descending); field title includes tier emoji by EAVOL band. A **Copy — symbols** embed lists **all** screen matches (same sort), not only the 10 detailed fields. Each field includes fundamentals (e.g. report-quarter EPS/Rev vs est), **Mkt cap** formatted to **K/M/B** (plain numeric **Market Cap** cells without K/M/B are treated as **millions of USD**; plain values **≥ 10,000,000** are treated as full USD), **Float** (K/M/B via the same rules as Small caps), short float %, country, Gap/ATR. Requires **`FINVIZ_API_KEY`** and **`MASSIVE_API_KEY`** (or **`POLYGON_API_KEY`**).

**What `/heatmap` shows:** One or more **PNG** treemap images built from a **v=152** full-universe export, filtered to tickers whose **Index** column matches the chosen benchmark. Embed describes size/color, **as-of** date, and links the FinViz screener. First run can take **1–3 minutes**; increase **`FINVIZ_V152_EXPORT_TIMEOUT_SEC`** if the HTTP fetch times out. There is **no** **Copy — symbols** message (universes can be very large). Requires `FINVIZ_API_KEY`.

**What `/top_opps` shows:** Posts **four** chart images (**1 minute**, **5 minute**, **1 hour**, **daily**) and a **snapshot** embed: **Open/High/Low/Close**, **Volume**, **Avg Vol**, **Rel Vol**, **Change**, **Gap**, **P/E**, **Mkt Cap**, **Share Float**, **Short Float**, **Sector**, **Industry**, **Sector/Theme**, **Country**, **News**, **Recent Days** (same **v=152** single-ticker export as `/quote`, via `finviz_v152_ticker.py`). **Market cap** plain numbers without **K/M/B** are interpreted as **millions of USD** (then shown compact); **float** uses **`_finviz_float_to_shares`** in `finviz_earnings.py` (not the volume thousands rule) so large issuers don’t show **M** instead of **B**. **Without** optional prices: downloads PNGs from FinViz **`chart.ashx`** for each timeframe (needs **`FINVIZ_API_KEY`**). **With** **entry** and **stop** (execution **study** mode): requires **`MASSIVE_API_KEY`** or **`POLYGON_API_KEY`**. Fetches aggregates from [Massive](https://massive.com) REST (`massive_rest.py`), renders candlesticks with **mplfinance** (`top_opps_charts.py`): **nightclouds**-style dark theme; overlays **EMA 9**, **EMA 21**, **SMA 50/100/200**; **session VWAP** on **1m/5m/1h** only (not daily). Intraday panels show **today’s session in US Eastern** (prior days in the fetch are used only to stabilize moving averages). **exit** (target) is optional — if omitted: **before 4:00pm ET** the default is **last traded** (quote **Close**); **at/after 4:00pm ET** the default is **regular session close** from Massive minute bars, with quote close as fallback (embed shows *last traded — default* or *regular session close — default*). Optional **notes** add a **Trade notes** field on the **last** chart embed, **below** the image (truncated to field limits). Chart header includes symbol, timeframe, % change (from quote), last price, volume, and **as-of** time in **ET**; per-chart embed text includes **Entry / Stop / Exit**, **R:R**, **EV/Share=… (at 50% probability)**, and **Side: Long / Short / Mixed** from level ordering. Command description is limited to **100** characters (Discord API).

**What `/evsize` shows:** Takes **long/short**, **entry/target/stop**, **win probability** (0–100), and **daily risk budget** ($). Optional **`kelly_fraction`:** **Quarter**, **Half** (default), or **Full** Kelly — i.e. that fraction of the **full Kelly** share of the daily budget, then **capped at 50%** of the daily budget per trade. Computes R, L, R:L, EV/share, **EV/R** (expected value per dollar of risk at the stop), suggested dollar risk, and share count.

**Letter grade (A+ … D)** is based on **EV/R**, not on dollar EV per share. The bot uses **stricter default thresholds** than older versions so **A+** is uncommon unless the setup is strong on paper (see defaults in **`ev_position_sizing.py`**). Optional **`EVSIZE_GRADE_CONSERVATISM`** (default **1.0**) multiplies **EV/R only for the letter** when you want grades to reflect fear, partial fills, or edge that does not fully realize; **Kelly math** still uses your inputs unless you change probability or levels. Optional **`EVSIZE_GRADE_*_MIN_EVR`** env vars override individual grade cutoffs.

Reply can be **ephemeral** with a **Post to channel** button (in servers) or **public**. No FinViz key needed. Educational tool, not financial advice.

**What `/econ` shows:** One command with a **`period`** option (like **`/earnings`**): default **Today** — Investing.com’s **`getCalendarFilteredData`** with **today’s** date in **`America/New_York`**. Choose **This week (Mon–Sun NY)** for Monday–Sunday in that timezone. Filters: **US + Canada** (`country[]` **5** and **6**), **medium + high** importance (`importance[]` **2** and **3**), **all categories**. Compact labeled events; **Open Economic Calendar** → [Investing.com](https://www.investing.com/economic-calendar/). **`investing_econ_calendar.py`**. Possible **403**. No `FINVIZ_API_KEY`.

**What `/ipo` shows:** **`period`:** **Today** (default) filters IPOScoop rows where the **first date** in *Expected to Trade* matches **today** in **US Eastern**. **Full calendar** shows the whole public table. Compact labeled lines; **`---`** between companies; no SCOOP columns. A **Copy — symbols** follow-up lists **Symbol proposed** values (**TBA** / blanks skipped). **`ipo_calendar.py`**. No `FINVIZ_API_KEY`.

**What `/news` shows:** Up to **5** headlines with links and source labels, in the **same order** as the **News** table on **`finviz.com/quote.ashx?t=…`** (HTML parse of `#news-table`). The Elite **`news_export.ashx?v=1`** CSV is **not** used as the primary source because it can disagree with that list. If the table cannot be read, the bot falls back to **`elite.finviz.com/news_export.ashx`** when **`FINVIZ_API_KEY`** is set. No FinViz key needed for the usual path.

**What `/quote` shows:** Daily chart, **OHLCV** and **change** from `finviz_quote`, plus a **v=152** row (`finviz_v152_ticker.fetch_v152_ticker_snapshot`): **P/E**, **Mkt Cap** (compact **K/M/B/T**; plain FinViz cells without a suffix → **millions of USD**), **Float** and **Short Float** (float uses **`_fmt_finviz_float_shares`**, not volume scaling), **Sector**, **Industry**, **Recent Days** monospace block, and **headlines**.

---

## Product 2 — Scan webhook posters (`post_scans_elite.py` / `post_scans_free.py`)

Separate **batch programs**: no bot token. You configure **Discord incoming webhook URLs** in JSON, run the script (or schedule it), and each configured scan posts to its webhook channel.

- **`post_scans_elite.py`** — FinViz Elite CSV exports; needs **`FINVIZ_API_KEY`** in `.env`.
- **`post_scans_free.py`** — Scrapes public FinViz HTML via [mariostoev/finviz](https://github.com/mariostoev/finviz); **no** API key; slower, rate-limit friendly.

These scripts **do not** start PradBot and **do not** require `DISCORD_BOT_TOKEN`.

**Top Gainers / Top Losers:** Same **`scan_id`** keys as **`/scans`**: `top_gainers`, `top_losers`. Elite uses **`fetch_top_movers`** (`fetch_elite.py`): gainers sorted by **change % descending**; losers by **most negative first** (not by ascending **abs(change)**, which had previously put small losers above large ones). Free uses the public screener page via **`fetch_free`** (no key; may differ slightly from Elite). Each run posts up to **50** tickers; sorting matches the **Included Scans** notes below.

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
    "jeff_sun_canslim": "https://discord.com/api/webhooks/1234567890/ghijkl...",
    "top_gainers": "https://discord.com/api/webhooks/1234567890/...",
    "top_losers": "https://discord.com/api/webhooks/1234567890/..."
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
| `top_gainers` | Top Gainers |
| `top_losers` | Top Losers |

Most presets are sorted by daily change % (descending) and capped at **50** tickers. **Top Losers** is sorted with the most negative changes first. **Top Gainers / Top Losers** use FinViz’s mover presets (`ta_topgainers` / `ta_toplosers`).

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

  # Shared: /scans + post_scans_elite; heatmap_* used by PradBot /heatmap
  scan_registry.py
  fetch_elite.py
  fetch_v152_universe.py
  heatmap_aggregate.py
  heatmap_pipeline.py
  heatmap_treemap.py
  heatmap_figures.py
  discord_payload.py

  # PradBot + Elite poster helpers
  finviz_chart.py
  finviz_earnings.py
  finviz_v152_ticker.py  # v=152 single-ticker snapshot (/quote, /top_opps)
  finviz_inplay.py
  inplay_earnings.py   # /inplay scanner:Earnings (FinViz + Massive %EAVOL)
  moc_movers.py        # /top_moc_movers: $10m, 10m RVOL vs prior RTH, composite, trades
  symbol_list.py       # Comma-separated tickers for Copy — symbols embeds
  top_opps_charts.py   # /top_opps execution study (Massive + mplfinance PNGs)
  massive_rest.py      # Massive.com REST aggregates (no WebSockets)
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

**PradBot — slash commands missing** — With **global** or **dual** sync, wait up to ~1 hour on servers that only get **global** registration. With **`GUILD_ID`** and no **`SLASH_SYNC_GLOBAL_ALSO`**, commands exist **only** on listed guilds. Invite must include **`applications.commands`**. If globals were cleared earlier, restart after fixing `.env`; avoid **`SLASH_CLEAR_GLOBAL_FOR_DEDUPE`** when using **`SLASH_SYNC_GLOBAL_ALSO`**.

**PradBot — `/scans` asks for `FINVIZ_API_KEY`** — Set it in `.env` next to `DISCORD_BOT_TOKEN`.

**PradBot — `/heatmap` times out or returns HTML** — Increase `FINVIZ_V152_EXPORT_TIMEOUT_SEC` (e.g. **300**). Confirm Elite auth and stable network; the v=152 export is a single large CSV.

**PradBot — `/inplay` Earnings asks for Massive** — Set **`MASSIVE_API_KEY`** or **`POLYGON_API_KEY`** in `.env` / Railway (same token works on `api.massive.com`).

**PradBot — `/top_moc_movers` slow or empty** — Lower **`MOC_RVOL_CANDIDATES`**, **`MOC_RVOL_LOOKBACK`**, or **`MOC_MAX_TICKERS`**; increase **`MOC_WORKERS`** cautiously (429 risk). Tighten **`MOC_MIN_NOTIONAL_10M`** to shrink the RVOL pass. If **`MOC_MIN_RVOL_10M`** is high and many names lack 10m history, tables can be empty. Before **4:00 PM ET** on the **session** you request, **minute data is incomplete** — use a **prior** **session_date** or run after the close.

**PradBot — `/top_opps` with entry/stop asks for Massive** — Study charts need **`MASSIVE_API_KEY`** or **`POLYGON_API_KEY`**. Omit entry/stop to use default FinViz-only charts.

**PradBot — `/top_opps` command sync fails (HTTP 400)** — Slash **command `description`** must be **≤ 100 characters** (Discord). Shorten the string in `bot.py` if you edit it.

**PradBot — chart / FinViz errors** — Confirm Elite subscription and `FINVIZ_API_KEY`.

**"No results for this scan"** — Normal on some days for some screens.

**Discord 400 (embed too large)** — Rare; scans cap at 50 rows; large tables split.

**"Gamma data not available"** — Try a further expiry on `/gex`.

---

## License

Data from [FinViz](https://finviz.com) — see their [Terms of Service](https://finviz.com/terms.ashx). Free scraping uses [mariostoev/finviz](https://github.com/mariostoev/finviz).
