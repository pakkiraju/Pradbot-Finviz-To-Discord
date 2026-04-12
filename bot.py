"""PradBot — Discord bot for Finviz charts and more (slash commands).

Run with:  python bot.py
Requires DISCORD_BOT_TOKEN and FINVIZ_API_KEY in .env.

Optional GUILD_ID: instant guild sync on those server(s). Default is guild-only (no global) so test
servers do not see duplicate slash entries. Set SLASH_SYNC_GLOBAL_ALSO=1 for dual sync (guild + global).
SLASH_GUILD_ONLY=1 overrides that and keeps guild-only only. If GUILD_ID is unset, commands sync globally only.

/scans uses fetch_elite.fetch_scan (same pipeline as post_scans_elite.py). /heatmap uses heatmap_pipeline.build_daily_heatmaps (index universe only). /earnings uses finviz_earnings (Elite v=152 export + earningsdate_today / thisweek filters). /inplay uses finviz_inplay (default: news + liquidity + News URL, screener v=151; Small caps: v=152 screener + News URL, extra float/cap columns). /econ_calendar posts an embed + Open Economic Calendar button (TradingView). /chart uses finviz_chart (1m–1h + D/W/M via chart.ashx p=). /top_opps posts four charts (1m/5m/1h/d) plus a v=152 snapshot embed (same 5-day table style as /quote).
"""

import asyncio
import io
import logging
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import env_setup

env_setup.configure_environment()

import discord
from discord import app_commands

from discord_payload import build_embeds
from fetch_elite import fetch_scan, fetch_scan_with_screener, fetch_top_movers
from finviz_chart import (
    CHART_TIMEFRAME_FILE_TAG,
    CHART_TIMEFRAME_LABELS,
    fetch_chart,
    validate_symbol,
)
from finviz_news import fetch_news
from finviz_options import fetch_options
from finviz_quote import fetch_quote
from finviz_v152_ticker import fetch_v152_ticker_snapshot
from gex_compute import compute_gex
from ev_position_sizing import compute as ev_compute, SizingError, EVResult
from fetch_v152_universe import FINVIZ_V152_SCREENER_URL
from finviz_earnings import (
    EarningsPeriod,
    _fmt_shares_compact,
    fetch_earnings_rows,
    format_earnings_embed_description,
)
from finviz_inplay import (
    fetch_inplay_rows,
    format_inplay_description,
    format_inplay_smallcap_description,
)
from heatmap_pipeline import build_daily_heatmaps
from scan_registry import SCAN_BY_ID, SCANS

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("pradbot")

# ---------------------------------------------------------------------------
# Env
# ---------------------------------------------------------------------------

def _secret(*names: str) -> str:
    """First non-empty env value among ``names`` (strips whitespace / UTF-8 BOM)."""
    for name in names:
        raw = os.environ.get(name)
        if raw is None:
            continue
        v = raw.strip().removeprefix("\ufeff").strip()
        if v:
            return v
    return ""


DISCORD_BOT_TOKEN = _secret("DISCORD_BOT_TOKEN", "DISCORD_TOKEN")
if not DISCORD_BOT_TOKEN:
    logger.critical(
        "DISCORD_BOT_TOKEN not set. Set the env var (e.g. Railway service Variables) "
        "or add it to .env in %s",
        Path(__file__).resolve().parent,
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------
intents = discord.Intents.default()

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

_SCAN_ALL_VALUE = "all"

_SCAN_CHOICES = [
    app_commands.Choice(name="All scans (every preset)", value=_SCAN_ALL_VALUE),
    *[app_commands.Choice(name=s.title[:100], value=s.scan_id) for s in SCANS],
]

# Match post_scans_elite.py spacing between webhook posts
_SCANS_ALL_DELAY_SEC = 1.5


async def _followup_scan_embeds(interaction: discord.Interaction, scan_def) -> tuple[int, int]:
    """Fetch one scan and post embed(s). Returns (row_count, embed_count)."""
    rows, screener_url = await asyncio.to_thread(fetch_scan_with_screener, scan_def)
    embed_dicts = build_embeds(scan_def.title, rows, screener_url=screener_url)
    embeds = [_webhook_embed_dict_to_discord(d) for d in embed_dicts]
    await interaction.followup.send(embed=embeds[0])
    for emb in embeds[1:]:
        await interaction.followup.send(embed=emb)
    return len(rows), len(embeds)


def _webhook_embed_dict_to_discord(em: dict) -> discord.Embed:
    """Convert webhook-style embed dict from discord_payload.build_embeds to discord.Embed."""
    color = em.get("color")
    if color is None:
        color = 0x06B6D4
    embed = discord.Embed(
        title=em.get("title"),
        description=em.get("description"),
        color=color,
    )
    if em.get("url"):
        embed.url = em["url"]
    ts = em.get("timestamp")
    if isinstance(ts, str) and ts:
        try:
            embed.timestamp = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            embed.timestamp = datetime.now(timezone.utc)
    foot = em.get("footer") or {}
    if foot.get("text"):
        embed.set_footer(text=foot["text"])
    return embed


def _parse_guild_ids(raw: str) -> list[int]:
    """Parse GUILD_ID: one ID or comma-separated IDs (whitespace allowed)."""
    out: list[int] = []
    for part in raw.replace(",", " ").split():
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            logger.warning("GUILD_ID skip invalid token %r (digits only)", part)
    return out


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


@bot.event
async def on_ready():
    guild_id_raw = os.environ.get("GUILD_ID", "").strip()
    guild_only = _env_truthy("SLASH_GUILD_ONLY")
    sync_global_also = _env_truthy("SLASH_SYNC_GLOBAL_ALSO")
    # Dual sync only when explicitly requested; avoids duplicate /command rows on test guilds.
    should_sync_global = sync_global_also and not guild_only

    if guild_id_raw:
        guild_ids = _parse_guild_ids(guild_id_raw)
        if not guild_ids:
            logger.error(
                "GUILD_ID has no valid Discord server IDs, got %r — using global sync",
                guild_id_raw,
            )
            await tree.sync()
            logger.info("Synced slash commands globally (invalid GUILD_ID)")
        else:
            for guild_id in guild_ids:
                guild = discord.Object(id=guild_id)
                tree.copy_global_to(guild=guild)
                await tree.sync(guild=guild)
            logger.info(
                "Synced slash commands to guild(s) %s (instant)",
                guild_ids,
            )

            if should_sync_global:
                await tree.sync()
                logger.info(
                    "Also synced slash commands globally (SLASH_SYNC_GLOBAL_ALSO=1) — other servers "
                    "update within ~1 hour; this test guild may show duplicate entries until Discord dedupes"
                )
            else:
                if guild_only:
                    logger.info(
                        "SLASH_GUILD_ONLY=1 — no global sync; only listed guild(s) have commands"
                    )
                else:
                    logger.info(
                        "Slash commands on guild(s) only (no global sync). "
                        "Set SLASH_SYNC_GLOBAL_ALSO=1 to register globally for other servers. "
                        "If commands still appear twice, restart once with SLASH_CLEAR_GLOBAL_FOR_DEDUPE=1 "
                        "to remove stale global registrations."
                    )

            # Optional clear of global commands when we are not syncing globals (dedupe after old dual-sync runs).
            if should_sync_global and _env_truthy("SLASH_CLEAR_GLOBAL_FOR_DEDUPE"):
                logger.warning(
                    "SLASH_CLEAR_GLOBAL_FOR_DEDUPE ignored while global sync is enabled "
                    "(incompatible — would remove commands from servers that only have globals)"
                )
            elif not should_sync_global and _env_truthy("SLASH_CLEAR_GLOBAL_FOR_DEDUPE"):
                app_id = bot.application_id
                if app_id is not None:
                    try:
                        await bot.http.bulk_upsert_global_commands(app_id, [])
                        logger.info(
                            "Cleared global slash commands (SLASH_CLEAR_GLOBAL_FOR_DEDUPE=1)"
                        )
                    except discord.HTTPException as e:
                        logger.warning("Could not clear global slash commands: %s", e)
    else:
        await tree.sync()
        logger.info(
            "Synced slash commands globally (set GUILD_ID for instant guild sync; "
            "add SLASH_SYNC_GLOBAL_ALSO=1 with GUILD_ID for dual sync)"
        )
    logger.info("Logged in as %s (id=%s)", bot.user, bot.user.id)


# ---------------------------------------------------------------------------
# /chart
# ---------------------------------------------------------------------------

_TIMEFRAME_CHOICES = [
    app_commands.Choice(name="1 minute", value="i1"),
    app_commands.Choice(name="3 minute", value="i3"),
    app_commands.Choice(name="5 minute", value="i5"),
    app_commands.Choice(name="15 minute", value="i15"),
    app_commands.Choice(name="30 minute", value="i30"),
    app_commands.Choice(name="1 hour", value="h"),
    app_commands.Choice(name="Daily", value="d"),
    app_commands.Choice(name="Weekly", value="w"),
    app_commands.Choice(name="Monthly", value="m"),
]


@tree.command(
    name="chart",
    description="Post a FinViz candlestick chart (intraday 1m–1h or daily / weekly / monthly)",
)
@app_commands.describe(
    symbol="Ticker symbol (e.g. AAPL, MSFT, BRK.B)",
    timeframe="Chart timeframe",
)
@app_commands.choices(timeframe=_TIMEFRAME_CHOICES)
async def chart_command(interaction: discord.Interaction, symbol: str, timeframe: app_commands.Choice[str] | None = None):
    ticker = validate_symbol(symbol)
    if ticker is None:
        await interaction.response.send_message(f"`{symbol}` doesn't look like a valid ticker symbol.", ephemeral=True)
        return

    tf = timeframe.value if timeframe else "d"
    tf_label = CHART_TIMEFRAME_LABELS.get(tf, "Daily")
    tf_tag = CHART_TIMEFRAME_FILE_TAG.get(tf, "daily")

    await interaction.response.defer()
    data = await asyncio.to_thread(fetch_chart, ticker, tf)

    if data is None:
        await interaction.followup.send(f"Could not fetch chart for **{ticker}**. Check the logs for details.")
        return

    filename = f"{ticker}_{tf_tag}.png"
    file = discord.File(io.BytesIO(data), filename=filename)

    embed = discord.Embed(
        title=f"{ticker} — {tf_label} chart",
        color=0x2ECC71,
        url=f"https://finviz.com/quote.ashx?t={ticker}",
    )
    embed.set_image(url=f"attachment://{filename}")
    embed.set_footer(text="Data from FinViz")

    await interaction.followup.send(embed=embed, file=file)


# ---------------------------------------------------------------------------
# GEX helpers (shared by /gex and /zerodte)
# ---------------------------------------------------------------------------

def _fmt_gex(value: float) -> str:
    abs_v = abs(value)
    sign = "-" if value < 0 else ""
    if abs_v >= 1_000_000:
        return f"{sign}{abs_v / 1_000_000:,.2f}M"
    if abs_v >= 1_000:
        return f"{sign}{abs_v / 1_000:,.1f}K"
    return f"{sign}{abs_v:,.0f}"


def _fmt_oi(value: float) -> str:
    return f"{int(value):,}"


def _build_gex_embed(summary) -> discord.Embed:
    title = f"{summary.symbol} — GEX" if summary.has_gamma else f"{summary.symbol} — OI Analysis"
    embed = discord.Embed(
        title=title,
        color=0x3498DB,
        url=f"https://finviz.com/quote.ashx?t={summary.symbol}&ty=oc",
    )
    embed.add_field(name="Expiry", value=summary.expiry, inline=True)

    if summary.put_call_ratio is not None:
        embed.add_field(name="P/C Ratio", value=str(summary.put_call_ratio), inline=True)

    embed.add_field(
        name="Total OI",
        value=f"Calls: {_fmt_oi(summary.total_call_oi)}  |  Puts: {_fmt_oi(summary.total_put_oi)}",
        inline=False,
    )

    if summary.has_gamma:
        embed.add_field(name="Net GEX", value=_fmt_gex(summary.total_net_gex), inline=True)
        if summary.gamma_flip is not None:
            embed.add_field(name="Gamma Flip", value=f"${summary.gamma_flip:,.2f}", inline=True)
        if summary.call_wall is not None:
            embed.add_field(name="Call Wall", value=f"${summary.call_wall:,.2f}  ({_fmt_gex(summary.call_wall_value)})", inline=True)
        if summary.put_wall is not None:
            embed.add_field(name="Put Wall", value=f"${summary.put_wall:,.2f}  ({_fmt_gex(summary.put_wall_value)})", inline=True)
    else:
        if summary.call_wall is not None:
            embed.add_field(name="Call OI Wall", value=f"${summary.call_wall:,.2f}  ({_fmt_oi(summary.call_wall_value)} OI)", inline=True)
        if summary.put_wall is not None:
            embed.add_field(name="Put OI Wall", value=f"${summary.put_wall:,.2f}  ({_fmt_oi(summary.put_wall_value)} OI)", inline=True)

    if summary.top_strikes:
        header = "Strike     | Call OI  | Put OI   |"
        if summary.has_gamma:
            header = "Strike     | Net GEX    | Call OI  | Put OI   |"

        lines = [header, "-" * len(header)]
        for s in sorted(summary.top_strikes, key=lambda x: x.strike):
            if summary.has_gamma:
                lines.append(
                    f"${s.strike:<9,.2f} | {_fmt_gex(s.net_gex):>10} | {s.call_oi:>8,} | {s.put_oi:>8,} |"
                )
            else:
                lines.append(
                    f"${s.strike:<9,.2f} | {s.call_oi:>8,} | {s.put_oi:>8,} |"
                )

        table = "\n".join(lines)
        if len(table) > 1000:
            table = table[:997] + "..."
        label = "Top Strikes by GEX" if summary.has_gamma else "Top Strikes by OI"
        embed.add_field(name=label, value=f"```\n{table}\n```", inline=False)

    if not summary.has_gamma:
        embed.add_field(
            name="Note",
            value="Gamma data not available — showing open interest walls only.",
            inline=False,
        )

    embed.set_footer(text="Data from FinViz Elite")
    return embed


# ---------------------------------------------------------------------------
# /gex
# ---------------------------------------------------------------------------

@tree.command(name="gex", description="GEX / options analysis for a ticker (nearest future expiry)")
@app_commands.describe(
    symbol="Ticker symbol (e.g. AAPL, SPY)",
    expiry="Specific expiry date in YYYY-MM-DD format (optional)",
)
async def gex_command(interaction: discord.Interaction, symbol: str, expiry: str | None = None):
    ticker = validate_symbol(symbol)
    if ticker is None:
        await interaction.response.send_message(f"`{symbol}` doesn't look like a valid ticker symbol.", ephemeral=True)
        return

    if expiry is not None and not re.match(r"^\d{4}-\d{2}-\d{2}$", expiry):
        await interaction.response.send_message(
            f"Invalid date format `{expiry}`. Use `YYYY-MM-DD` (e.g. `2025-07-18`).",
            ephemeral=True,
        )
        return

    await interaction.response.defer()
    rows = await asyncio.to_thread(fetch_options, ticker, expiry)

    if not rows:
        msg = f"No options data returned for **{ticker}**"
        if expiry:
            msg += f" (expiry {expiry})"
        msg += ". Check that the symbol has listed options and the expiry date is valid."
        await interaction.followup.send(msg)
        return

    used_expiry = expiry or rows[0].expiry or "unknown"
    summary = compute_gex(ticker, used_expiry, rows)

    if summary is None:
        await interaction.followup.send(f"Could not compute GEX for **{ticker}**. No valid strike data found.")
        return

    embed = _build_gex_embed(summary)
    await interaction.followup.send(embed=embed)


# ---------------------------------------------------------------------------
# /zerodte
# ---------------------------------------------------------------------------

@tree.command(name="zerodte", description="0DTE options analysis (OI walls, volume, P/C ratio)")
@app_commands.describe(symbol="Ticker symbol (e.g. AAPL, SPY)")
async def zerodte_command(interaction: discord.Interaction, symbol: str):
    ticker = validate_symbol(symbol)
    if ticker is None:
        await interaction.response.send_message(f"`{symbol}` doesn't look like a valid ticker symbol.", ephemeral=True)
        return

    today = date.today().isoformat()

    await interaction.response.defer()
    rows = await asyncio.to_thread(fetch_options, ticker, today)

    if not rows:
        await interaction.followup.send(
            f"No 0DTE options data for **{ticker}** today ({today}). "
            "The symbol may not have options expiring today."
        )
        return

    summary = compute_gex(ticker, today, rows)
    if summary is None:
        await interaction.followup.send(f"Could not compute 0DTE analysis for **{ticker}**. No valid strike data found.")
        return

    embed = _build_gex_embed(summary)
    embed.title = f"{ticker} — 0DTE Analysis"
    embed.color = 0xE67E22
    await interaction.followup.send(embed=embed)


# ---------------------------------------------------------------------------
# /news
# ---------------------------------------------------------------------------

@tree.command(name="news", description="Latest news articles for a ticker")
@app_commands.describe(symbol="Ticker symbol (e.g. AAPL, TSLA)")
async def news_command(interaction: discord.Interaction, symbol: str):
    ticker = validate_symbol(symbol)
    if ticker is None:
        await interaction.response.send_message(f"`{symbol}` doesn't look like a valid ticker symbol.", ephemeral=True)
        return

    await interaction.response.defer()
    articles = await asyncio.to_thread(fetch_news, ticker, 5)

    if not articles:
        await interaction.followup.send(f"No news found for **{ticker}**.")
        return

    embed = discord.Embed(
        title=f"{ticker} — Latest News",
        color=0x9B59B6,
        url=f"https://finviz.com/quote.ashx?t={ticker}",
    )

    for article in articles:
        date_str = article.date.split(" ")[0] if " " in article.date else article.date
        source_tag = f"  *— {article.source}*" if article.source else ""
        embed.add_field(
            name=date_str,
            value=f"[{article.title}]({article.url}){source_tag}",
            inline=False,
        )

    embed.set_footer(text="Data from FinViz Elite")
    await interaction.followup.send(embed=embed)


# ---------------------------------------------------------------------------
# /econ_calendar (TradingView Economic Calendar)
# ---------------------------------------------------------------------------

TRADINGVIEW_ECON_CALENDAR_URL = "https://www.tradingview.com/economic-calendar/"

# Defaults from TradingView embed-widget-events snippet (informational only).
ECON_CALENDAR_WIDGET_DEFAULTS = {
    "colorTheme": "dark",
    "isTransparent": False,
    "locale": "en",
    "countryFilter": "us,ca",
    "importanceFilter": "-1,0,1",
    "width": 400,
    "height": 550,
}


class EconomicCalendarView(discord.ui.View):
    def __init__(self, url: str):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="Open Economic Calendar", url=url))


@tree.command(
    name="econ_calendar",
    description="TradingView Economic Calendar (Open Economic Calendar button)",
)
async def econ_calendar_command(interaction: discord.Interaction):
    defaults = ECON_CALENDAR_WIDGET_DEFAULTS
    embed = discord.Embed(
        title="Economic Calendar",
        url=TRADINGVIEW_ECON_CALENDAR_URL,
        color=0x2962FF,
        description=(
            f"**{defaults['colorTheme']}** theme · locale `{defaults['locale']}` · "
            f"countries **{defaults['countryFilter']}** · importance `{defaults['importanceFilter']}` · "
            f"~{defaults['width']}×{defaults['height']}"
        ),
    )
    embed.set_footer(text="Economic Calendar by TradingView")
    await interaction.response.send_message(
        embed=embed,
        view=EconomicCalendarView(TRADINGVIEW_ECON_CALENDAR_URL),
    )
    logger.info("econ_calendar for %s", interaction.user)


# ---------------------------------------------------------------------------
# /purge
# ---------------------------------------------------------------------------


class PurgeAllConfirmView(discord.ui.View):
    """Confirm delete-all using buttons. Text 'yes' replies fail without Message Content intent."""

    def __init__(self, invoker_id: int):
        super().__init__(timeout=15.0)
        self.invoker_id = invoker_id
        self.choice: bool | None = None  # True=purge, False=cancel, None=timeout

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "Only the person who ran `/purge` can use these buttons.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Yes, delete all", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.choice = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.choice = False
        await interaction.response.edit_message(content="Purge cancelled.", view=None)
        self.stop()


@tree.command(name="purge", description="Delete messages from the current channel")
@app_commands.describe(amount='Number of messages to delete, or "all"')
@app_commands.default_permissions(manage_messages=True)
async def purge_command(interaction: discord.Interaction, amount: str):
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message("This command can only be used in text channels.", ephemeral=True)
        return

    bot_perms = channel.permissions_for(channel.guild.me)
    if not bot_perms.manage_messages or not bot_perms.read_message_history:
        await interaction.response.send_message(
            "I need **Manage Messages** and **Read Message History** permissions to purge.",
            ephemeral=True,
        )
        return

    if amount.lower() == "all":
        view = PurgeAllConfirmView(interaction.user.id)
        await interaction.response.send_message(
            "Are you sure you want to delete **all** messages in this channel?\n"
            "Click **Yes, delete all** within 15 seconds to confirm.",
            view=view,
        )
        await view.wait()
        if view.choice is None:
            try:
                await interaction.edit_original_response(
                    content="Purge cancelled (timed out).", view=None
                )
            except discord.HTTPException:
                pass
            return
        if view.choice is False:
            return

        deleted = await channel.purge(limit=None)
        info = await channel.send(f"Purged **{len(deleted)}** messages.")
        await info.delete(delay=5)
        logger.info("purge all: %d messages deleted in #%s by %s", len(deleted), channel.name, interaction.user)
        return

    try:
        count = int(amount)
    except ValueError:
        await interaction.response.send_message(
            'Please provide a number or `all`.\nExample: `/purge 25` or `/purge all`',
            ephemeral=True,
        )
        return

    if count < 1:
        await interaction.response.send_message("Amount must be at least 1.", ephemeral=True)
        return
    if count > 500:
        await interaction.response.send_message(
            "Maximum purge is 500 messages at a time. Use `/purge all` to clear the channel.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    deleted = await channel.purge(limit=count)
    await interaction.followup.send(f"Purged **{len(deleted)}** messages.", ephemeral=True)
    logger.info("purge %d: %d messages deleted in #%s by %s", count, len(deleted), channel.name, interaction.user)


# ---------------------------------------------------------------------------
# /scans  (fetch_elite.fetch_scan + same embeds as webhook poster)
# ---------------------------------------------------------------------------


@tree.command(name="scans", description="Fetch FinViz Elite screener scan(s) (same pipeline as post_scans_elite)")
@app_commands.describe(scan="One preset, or All scans to run every preset in order")
@app_commands.choices(scan=_SCAN_CHOICES)
async def scans_command(interaction: discord.Interaction, scan: app_commands.Choice[str]):
    if not os.environ.get("FINVIZ_API_KEY", "").strip():
        await interaction.response.send_message(
            "Set **FINVIZ_API_KEY** in `.env` to use `/scans`.",
            ephemeral=True,
        )
        return

    if scan.value == _SCAN_ALL_VALUE:
        await interaction.response.defer()
        n = len(SCANS)
        await interaction.followup.send(
            f"Running **{n}** FinViz Elite scans — results post below in order (several minutes; FinViz spacing between scans)."
        )
        ok = 0
        errors = 0
        for idx, scan_def in enumerate(SCANS):
            try:
                row_count, emb_count = await _followup_scan_embeds(interaction, scan_def)
                ok += 1
                logger.info(
                    "scans all [%s]: %d rows, %d embed(s) — %s",
                    scan_def.scan_id,
                    row_count,
                    emb_count,
                    interaction.user,
                )
            except Exception as e:
                logger.exception("fetch_scan failed for %s (all)", scan_def.scan_id)
                errors += 1
                await interaction.followup.send(
                    f"**{scan_def.title}** — failed: `{e}`"
                )
            if idx < n - 1:
                await asyncio.sleep(_SCANS_ALL_DELAY_SEC)
        await interaction.followup.send(
            f"**All scans finished.** Completed **{ok}**/{n}" + (f", **{errors}** error(s)." if errors else ".")
        )
        return

    scan_def = SCAN_BY_ID.get(scan.value)
    if scan_def is None:
        await interaction.response.send_message("Unknown scan — try again.", ephemeral=True)
        return

    await interaction.response.defer()
    try:
        row_count, emb_count = await _followup_scan_embeds(interaction, scan_def)
    except Exception as e:
        logger.exception("fetch_scan failed for %s", scan.value)
        await interaction.followup.send(f"Fetch failed: `{e}`")
        return

    logger.info(
        "scans %s: %d rows, %d embed(s) for %s",
        scan_def.scan_id,
        row_count,
        emb_count,
        interaction.user,
    )


# ---------------------------------------------------------------------------
# /top_gainers  /top_losers
# ---------------------------------------------------------------------------


def _fmt_movers_vol(val) -> str:
    n = None
    raw = str(val).strip().replace(",", "")
    try:
        n = float(raw)
    except (ValueError, TypeError):
        return str(val)
    if n >= 1_000_000:
        return f"{n / 1_000_000:,.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:,.0f}K"
    return f"{n:,.0f}"


def _build_movers_embed(
    kind: str,
    rows: list[dict],
    screener_url: str,
    min_price: float | None,
    min_volume: float | None,
) -> discord.Embed:
    label = "Gainers" if kind == "gainers" else "Losers"
    color = 0x00C853 if kind == "gainers" else 0xFF1744
    title = f"Top {len(rows)} {label}"

    embed = discord.Embed(title=title, color=color, url=screener_url)

    if not rows:
        embed.description = f"*No {label.lower()} matched the filters.*"
    else:
        header = f"{'Ticker':<6} {'Price':>9} {'Chg%':>8} {'Volume':>9}"
        lines = [header, "-" * len(header)]
        for r in rows:
            tk = (r.get("ticker") or "")[:6].ljust(6)
            pr = str(r.get("price") or "").rjust(9)
            ch = str(r.get("change") or "").rjust(8)
            vo = _fmt_movers_vol(r.get("volume") or "").rjust(9)
            lines.append(f"{tk} {pr} {ch} {vo}")
        embed.description = f"```\n{chr(10).join(lines)}\n```"

    filters = []
    if min_price is not None:
        filters.append(f"price >= ${min_price:,.2f}")
    if min_volume is not None:
        filters.append(f"volume >= {_fmt_movers_vol(min_volume)}")
    footer = "Data from FinViz Elite"
    if filters:
        footer += "  |  Filters: " + ", ".join(filters)
    embed.set_footer(text=footer)
    return embed


@tree.command(name="top_gainers", description="Top 10 gaining stocks today (sorted by change %)")
@app_commands.describe(
    min_price="Only show stocks at or above this price (optional)",
    min_volume="Min volume in shares today, e.g. 1000000 for 1M (optional)",
)
async def top_gainers_command(
    interaction: discord.Interaction,
    min_price: float | None = None,
    min_volume: float | None = None,
):
    if not os.environ.get("FINVIZ_API_KEY", "").strip():
        await interaction.response.send_message(
            "Set **FINVIZ_API_KEY** in `.env` to use `/top_gainers`.", ephemeral=True
        )
        return

    await interaction.response.defer()
    rows, screener_url = await asyncio.to_thread(
        fetch_top_movers, "gainers", min_price=min_price, min_volume=min_volume
    )
    embed = _build_movers_embed("gainers", rows, screener_url, min_price, min_volume)
    await interaction.followup.send(embed=embed)
    logger.info("top_gainers: %d rows for %s", len(rows), interaction.user)


@tree.command(name="top_losers", description="Top 10 losing stocks today (sorted by change %)")
@app_commands.describe(
    min_price="Only show stocks at or above this price (optional)",
    min_volume="Min volume in shares today, e.g. 1000000 for 1M (optional)",
)
async def top_losers_command(
    interaction: discord.Interaction,
    min_price: float | None = None,
    min_volume: float | None = None,
):
    if not os.environ.get("FINVIZ_API_KEY", "").strip():
        await interaction.response.send_message(
            "Set **FINVIZ_API_KEY** in `.env` to use `/top_losers`.", ephemeral=True
        )
        return

    await interaction.response.defer()
    rows, screener_url = await asyncio.to_thread(
        fetch_top_movers, "losers", min_price=min_price, min_volume=min_volume
    )
    embed = _build_movers_embed("losers", rows, screener_url, min_price, min_volume)
    await interaction.followup.send(embed=embed)
    logger.info("top_losers: %d rows for %s", len(rows), interaction.user)


_INPLAY_SCANNER_CHOICES = [
    app_commands.Choice(name="Default", value="default"),
    app_commands.Choice(name="Small caps", value="smallcaps"),
]


@tree.command(
    name="inplay",
    description="Stocks in play: news + liquidity (default) or small-cap liquidity screen (FinViz Elite)",
)
@app_commands.describe(
    scanner="Default: news + liquidity screen. Small caps: cap $5M–$2B, vol + rel vol; v=152 screener + per-row news links.",
)
@app_commands.choices(scanner=_INPLAY_SCANNER_CHOICES)
async def inplay_command(
    interaction: discord.Interaction,
    scanner: str = "default",
):
    if not os.environ.get("FINVIZ_API_KEY", "").strip():
        await interaction.response.send_message(
            "Set **FINVIZ_API_KEY** in `.env` to use `/inplay`.", ephemeral=True
        )
        return

    mode = "smallcaps" if scanner == "smallcaps" else "default"

    await interaction.response.defer()
    rows, screener_url = await asyncio.to_thread(fetch_inplay_rows, mode=mode)
    if mode == "smallcaps":
        desc = format_inplay_smallcap_description(rows)
        title = "In play — Small caps"
        footer = (
            "FinViz Elite • v=152 screener • cap $5M–$2B • cur vol >1M • rel vol >1.5 • delayed"
        )
    else:
        desc = format_inplay_description(rows)
        title = "In play"
        footer = (
            "FinViz Elite • news today/yesterday • price >$1 • avg vol >1M • vol >500K • rel vol >1.5 • delayed"
        )
    embed = discord.Embed(
        title=title,
        description=desc,
        url=screener_url,
        color=0x06B6D4,
    )
    embed.set_footer(text=footer)
    await interaction.followup.send(embed=embed)
    logger.info("inplay (%s): %d rows for %s", mode, len(rows), interaction.user)


_EARNINGS_PERIOD_CHOICES = [
    app_commands.Choice(name="Today", value="today"),
    app_commands.Choice(name="Weekly (this week)", value="weekly"),
]


@tree.command(
    name="earnings",
    description="Stocks with earnings today or this week (time, price, volume, avg vol, change)",
)
@app_commands.describe(period="Earnings date filter")
@app_commands.choices(period=_EARNINGS_PERIOD_CHOICES)
async def earnings_command(
    interaction: discord.Interaction,
    period: str = "today",
):
    if not os.environ.get("FINVIZ_API_KEY", "").strip():
        await interaction.response.send_message(
            "Set **FINVIZ_API_KEY** in `.env` to use `/earnings`.", ephemeral=True
        )
        return

    p: EarningsPeriod = "today" if period == "today" else "weekly"
    await interaction.response.defer()
    rows, screener_url = await asyncio.to_thread(fetch_earnings_rows, p)
    desc = format_earnings_embed_description(rows, period=p)
    title = "Earnings — today" if p == "today" else "Earnings — this week"
    embed = discord.Embed(
        title=title,
        description=desc,
        url=screener_url,
        color=0x06B6D4,
    )
    embed.set_footer(text="FinViz Elite • quotes delayed")
    await interaction.followup.send(embed=embed)
    logger.info("earnings %s: %d rows for %s", p, len(rows), interaction.user)


_HEATMAP_UNIVERSE_CHOICES = [
    app_commands.Choice(name="S&P 500 (default)", value="sp500"),
    app_commands.Choice(name="NASDAQ 100", value="ndx100"),
    app_commands.Choice(name="Dow Jones", value="dow"),
    app_commands.Choice(name="Russell 2000", value="russell2000"),
]

async def _run_heatmap_command(
    interaction: discord.Interaction,
    *,
    universe: str,
):
    """FinViz-style nested treemap (v=152 export; slow)."""
    if not os.environ.get("FINVIZ_API_KEY", "").strip():
        await interaction.response.send_message(
            "Set **FINVIZ_API_KEY** in `.env` to use `/heatmap`.", ephemeral=True
        )
        return

    await interaction.response.defer()
    try:
        images, as_of = await asyncio.to_thread(
            build_daily_heatmaps,
            universe=universe,
        )
    except Exception as e:
        logger.exception("heatmap failed for %s", interaction.user)
        await interaction.followup.send(f"Heatmap build failed: `{e}`")
        return

    if not images or as_of is None:
        msg = (
            "Could not build a treemap — the CSV export may have failed (check **FINVIZ_API_KEY** and "
            "`FINVIZ_V152_EXPORT_TIMEOUT_SEC`), or **too few tickers** matched the selected index universe."
        )
        await interaction.followup.send(msg)
        return

    files = [discord.File(io.BytesIO(png), filename=name) for name, png in images]
    filt = f"universe=`{universe}`"
    embed = discord.Embed(
        title="Daily performance treemap",
        description=(
            f"**Size** = market cap · **Color** = change % (delayed). "
            f"**{as_of.isoformat()}**. {filt}\n"
            f"[Open Finviz screener]({FINVIZ_V152_SCREENER_URL})"
        ),
        color=0x06B6D4,
    )
    embed.set_footer(text="Pradly Portal • FinViz Elite • nested squarify layout")
    await interaction.followup.send(embed=embed, files=files)
    logger.info("heatmap: %d file(s) for %s", len(files), interaction.user)


@tree.command(
    name="heatmap",
    description="FinViz-style market treemap by index (S&P 500 default). Size=cap, color=change %. Slow.",
)
@app_commands.choices(universe=_HEATMAP_UNIVERSE_CHOICES)
@app_commands.describe(
    universe="Benchmark / index (default S&P 500; includes stocks and ETFs in that index)",
)
async def heatmap_command(
    interaction: discord.Interaction,
    universe: str = "sp500",
):
    await _run_heatmap_command(
        interaction,
        universe=universe,
    )


# ---------------------------------------------------------------------------
# /quote (replaces the old !SYMBOL shortcut)
# ---------------------------------------------------------------------------

def _fmt_vol(v: int) -> str:
    """Format share/volume counts with K / M / B (aligned with finviz_earnings compact style)."""
    return _fmt_shares_compact(float(v))


def _recent_days_field_value(bars) -> str | None:
    """Monospace Recent Days block matching /quote; None if too long or insufficient bars."""
    if len(bars) <= 1:
        return None
    lines = ["Date       |  Close   |  Volume"]
    lines.append("-" * len(lines[0]))
    for b in bars:
        lines.append(f"{b.date} | ${b.close:>8,.2f} | {_fmt_vol(b.volume):>8}")
    table = "\n".join(lines)
    if len(table) > 1000:
        return None
    return f"```\n{table}\n```"


def _gap_display_str(snapshot, latest, prev_close: float | None) -> str:
    if snapshot is not None and (snapshot.gap_raw or "").strip():
        return snapshot.gap_raw.strip()
    if prev_close and prev_close != 0:
        gp = (latest.open - prev_close) / prev_close * 100
        sign = "+" if gp >= 0 else ""
        return f"{sign}{gp:.2f}%"
    return "N/A"


@tree.command(name="quote", description="Quick quote panel — chart, OHLCV, change, and latest news")
@app_commands.describe(symbol="Ticker symbol (e.g. AAPL, MSFT, BRK.B)")
async def quote_command(interaction: discord.Interaction, symbol: str):
    ticker = validate_symbol(symbol)
    if ticker is None:
        await interaction.response.send_message(f"`{symbol}` doesn't look like a valid ticker symbol.", ephemeral=True)
        return

    await interaction.response.defer()

    bars, chart_data, articles = await asyncio.gather(
        asyncio.to_thread(fetch_quote, ticker, 5),
        asyncio.to_thread(fetch_chart, ticker, "d"),
        asyncio.to_thread(fetch_news, ticker, 3),
    )

    if not bars:
        await interaction.followup.send(f"No quote data found for **{ticker}**.")
        return

    latest = bars[0]
    prev_close = bars[1].close if len(bars) > 1 else None

    if prev_close and prev_close != 0:
        chg = latest.close - prev_close
        chg_pct = (chg / prev_close) * 100
        sign = "+" if chg >= 0 else ""
        change_str = f"{sign}{chg:,.2f} ({sign}{chg_pct:.2f}%)"
    else:
        change_str = "N/A"

    embed = discord.Embed(
        title=f"{ticker} — ${latest.close:,.2f}  {change_str}",
        color=0x1ABC9C,
        url=f"https://finviz.com/quote.ashx?t={ticker}",
    )
    embed.add_field(name="Open", value=f"${latest.open:,.2f}", inline=True)
    embed.add_field(name="High", value=f"${latest.high:,.2f}", inline=True)
    embed.add_field(name="Low", value=f"${latest.low:,.2f}", inline=True)
    embed.add_field(name="Volume", value=_fmt_vol(latest.volume), inline=True)
    embed.add_field(name="Date", value=latest.date, inline=True)

    recent = _recent_days_field_value(bars)
    if recent:
        embed.add_field(name="Recent Days", value=recent, inline=False)

    if articles:
        news_lines = []
        for a in articles:
            src = f" — {a.source}" if a.source else ""
            news_lines.append(f"[{a.title}]({a.url}){src}")
        embed.add_field(name="Latest News", value="\n".join(news_lines), inline=False)

    file = None
    if chart_data:
        filename = f"{ticker}_daily.png"
        file = discord.File(io.BytesIO(chart_data), filename=filename)
        embed.set_image(url=f"attachment://{filename}")

    embed.set_footer(text="Data from FinViz Elite")

    if file:
        await interaction.followup.send(embed=embed, file=file)
    else:
        await interaction.followup.send(embed=embed)


_TOP_OPPS_TIMEFRAMES = ("i1", "i5", "h", "d")


@tree.command(
    name="top_opps",
    description="1m/5m/1h/d charts + snapshot: OHLCV, PE, float, gap, news, and 5-day history.",
)
@app_commands.describe(symbol="Ticker symbol (e.g. AAPL, MSFT, BRK.B)")
async def top_opps_command(interaction: discord.Interaction, symbol: str):
    ticker = validate_symbol(symbol)
    if ticker is None:
        await interaction.response.send_message(f"`{symbol}` doesn't look like a valid ticker symbol.", ephemeral=True)
        return

    await interaction.response.defer()

    results = await asyncio.gather(
        asyncio.to_thread(fetch_chart, ticker, "i1"),
        asyncio.to_thread(fetch_chart, ticker, "i5"),
        asyncio.to_thread(fetch_chart, ticker, "h"),
        asyncio.to_thread(fetch_chart, ticker, "d"),
        asyncio.to_thread(fetch_quote, ticker, 5),
        asyncio.to_thread(fetch_news, ticker, 1),
        asyncio.to_thread(fetch_v152_ticker_snapshot, ticker),
    )
    charts = results[:4]
    bars = results[4]
    articles = results[5]
    snapshot = results[6]

    if not bars:
        await interaction.followup.send(f"No quote data found for **{ticker}**.")
        return

    chart_embeds: list[discord.Embed] = []
    files: list[discord.File] = []
    missing_labels: list[str] = []
    for tf, data in zip(_TOP_OPPS_TIMEFRAMES, charts):
        label = CHART_TIMEFRAME_LABELS.get(tf, tf)
        tag = CHART_TIMEFRAME_FILE_TAG.get(tf, tf)
        fn = f"{ticker}_{tag}.png"
        if data:
            files.append(discord.File(io.BytesIO(data), filename=fn))
            emb = discord.Embed(
                title=ticker,
                color=0x2ECC71,
                url=f"https://finviz.com/quote.ashx?t={ticker}",
            )
            emb.set_image(url=f"attachment://{fn}")
            chart_embeds.append(emb)
        else:
            missing_labels.append(label)

    if chart_embeds:
        await interaction.followup.send(embeds=chart_embeds, files=files)
    else:
        await interaction.followup.send(
            f"Could not load charts for **{ticker}**. Check **FINVIZ_API_KEY** and bot logs."
        )

    latest = bars[0]
    prev_close = bars[1].close if len(bars) > 1 else None

    if prev_close and prev_close != 0:
        chg = latest.close - prev_close
        chg_pct = (chg / prev_close) * 100
        sign = "+" if chg >= 0 else ""
        change_str = f"{sign}{chg:,.2f} ({sign}{chg_pct:.2f}%)"
    else:
        change_str = "N/A"

    avg_vol = snapshot.avg_vol_display if snapshot else "—"
    rel_vol = snapshot.rel_vol_display if snapshot else "—"
    pe = snapshot.pe if snapshot else "—"
    sh_float = snapshot.shares_float_display if snapshot else "—"
    short_f = snapshot.short_float_display if snapshot else "—"

    detail = discord.Embed(
        title=f"{ticker} — snapshot",
        color=0x1ABC9C,
        url=f"https://finviz.com/quote.ashx?t={ticker}",
    )
    detail.add_field(name="Open", value=f"${latest.open:,.2f}", inline=True)
    detail.add_field(name="High", value=f"${latest.high:,.2f}", inline=True)
    detail.add_field(name="Low", value=f"${latest.low:,.2f}", inline=True)
    detail.add_field(name="Close", value=f"${latest.close:,.2f}", inline=True)
    detail.add_field(name="Volume", value=_fmt_vol(latest.volume), inline=True)
    detail.add_field(name="Avg Vol", value=avg_vol, inline=True)
    detail.add_field(name="Rel Vol", value=rel_vol, inline=True)
    detail.add_field(name="Change", value=change_str, inline=True)
    detail.add_field(name="Gap", value=_gap_display_str(snapshot, latest, prev_close), inline=True)
    detail.add_field(name="P/E", value=pe, inline=True)
    detail.add_field(name="Share Float", value=sh_float, inline=True)
    detail.add_field(name="Short Float", value=short_f, inline=True)

    if articles:
        a = articles[0]
        src = f" — {a.source}" if a.source else ""
        news_val = f"[{a.title}]({a.url}){src}"
        if len(news_val) > 1024:
            news_val = news_val[:1021] + "..."
        detail.add_field(
            name="News",
            value=news_val,
            inline=False,
        )
    else:
        detail.add_field(name="News", value="—", inline=False)

    recent = _recent_days_field_value(bars)
    if recent:
        detail.add_field(name="Recent Days", value=recent, inline=False)

    foot_parts = ["Data from FinViz Elite"]
    if missing_labels:
        foot_parts.append("Missing charts: " + ", ".join(missing_labels))
    detail.set_footer(text=" • ".join(foot_parts))

    await interaction.followup.send(embed=detail)


# ---------------------------------------------------------------------------
# /evsize  (EV grade + Kelly-based position sizing)
# ---------------------------------------------------------------------------

_SIDE_CHOICES = [
    app_commands.Choice(name="Long", value="long"),
    app_commands.Choice(name="Short", value="short"),
]

_KELLY_FRACTION_CHOICES = [
    app_commands.Choice(name="Quarter Kelly (¼)", value="0.25"),
    app_commands.Choice(name="Half Kelly (½)", value="0.5"),
    app_commands.Choice(name="Full Kelly", value="1.0"),
]

_GRADE_COLORS = {
    "A+": 0x00C853, "A": 0x00E676, "A-": 0x69F0AE,
    "B+": 0xFFD600, "B": 0xFFEA00, "B-": 0xFFF176,
    "C": 0xFF9100, "D": 0xFF1744,
}

_EVSIZE_VISIBILITY_CHOICES = [
    app_commands.Choice(name="Private — only you (use Post to share)", value="private"),
    app_commands.Choice(name="Public — everyone sees this reply", value="public"),
]


class EvsizeShareView(discord.ui.View):
    """Private /evsize reply: button to send the same embed to the channel for everyone."""

    def __init__(self, embed_to_share: discord.Embed, invoker_id: int):
        super().__init__(timeout=3600.0)
        self.embed_to_share = embed_to_share
        self.invoker_id = invoker_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "Only the person who ran `/evsize` can use this button.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Post to channel", style=discord.ButtonStyle.primary)
    async def post_to_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None:
            await interaction.response.send_message(
                "Sharing works in a server text channel.", ephemeral=True
            )
            return
        ch = interaction.channel
        if not isinstance(ch, discord.TextChannel):
            await interaction.response.send_message(
                "Post to channel only works in a text channel.", ephemeral=True
            )
            return
        me = ch.guild.me
        if me is None:
            await interaction.response.send_message("Could not verify bot permissions.", ephemeral=True)
            return
        perms = ch.permissions_for(me)
        if not perms.send_messages or not perms.embed_links:
            await interaction.response.send_message(
                "I need **Send Messages** and **Embed Links** in this channel to post.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        await ch.send(embed=self.embed_to_share)
        await interaction.edit_original_response(view=None)
        await interaction.followup.send("Posted for everyone in this channel.", ephemeral=True)


def _pct(v: float) -> str:
    return f"{v * 100:+.2f}%"


def _kelly_mode_label(fraction: float) -> str:
    if abs(fraction - 0.25) < 1e-9:
        return "Quarter Kelly (¼)"
    if abs(fraction - 0.5) < 1e-9:
        return "Half Kelly (½)"
    if abs(fraction - 1.0) < 1e-9:
        return "Full Kelly"
    return f"{fraction:.0%} of full Kelly"


def _build_ev_embed(r: EVResult) -> discord.Embed:
    color = _GRADE_COLORS.get(r.grade, 0x546E7A)
    title = f"EV Grade: {r.grade}  —  {r.side.upper()} @ ${r.entry:,.2f}"

    embed = discord.Embed(title=title, color=color)

    embed.add_field(
        name="Setup",
        value=(
            f"**Side:** {r.side.capitalize()}\n"
            f"**Entry:** ${r.entry:,.2f}\n"
            f"**Target:** ${r.target:,.2f}\n"
            f"**Stop:** ${r.stop:,.2f}\n"
            f"**Win prob:** {r.probability:.1f}%"
        ),
        inline=True,
    )

    embed.add_field(
        name="Risk / Reward",
        value=(
            f"**Reward (R):** ${r.reward:,.2f}\n"
            f"**Risk (L):** ${r.risk:,.2f}\n"
            f"**R:L ratio:** {r.b:.2f}\n"
            f"**EV / share:** ${r.ev_per_share:,.4f}\n"
            f"**EV/R:** {_pct(r.evr)}"
        ),
        inline=True,
    )

    if r.f_kelly <= 0:
        sizing_text = (
            "**Full Kelly:** 0% (no edge)\n"
            "**Suggested risk:** $0.00\n"
            "**Shares:** 0\n\n"
            "This setup has **no positive expected value** under the given probability. "
            "Consider passing or improving the R:L ratio."
        )
    else:
        mode = _kelly_mode_label(r.kelly_fraction)
        sizing_text = (
            f"**Mode:** {mode}\n"
            f"**Full Kelly:** {_pct(r.f_kelly)}\n"
            f"**Fraction of daily budget:** {_pct(r.f_trade)}\n"
            f"**Daily budget:** ${r.daily_risk:,.2f}\n"
            f"**Suggested risk:** ${r.suggested_risk:,.2f}\n"
            f"**Shares:** {r.shares:,}"
        )

    embed.add_field(name="Position Sizing", value=sizing_text, inline=False)

    embed.set_footer(
        text="Educational tool only — not financial advice. Fractional Kelly + 50% single-trade cap on daily budget."
    )
    return embed


@tree.command(name="evsize", description="EV grade and position sizing for a trade setup")
@app_commands.describe(
    side="Long or Short",
    entry="Entry price",
    target="Target price",
    stop="Stop-loss price",
    probability="Win probability (0-100%)",
    daily_risk="Max loss budget for the day in USD",
    kelly_fraction="Fraction of full Kelly (default: half)",
    visibility="Private: only you see the reply (Post button in servers). Public: everyone sees this reply.",
)
@app_commands.choices(side=_SIDE_CHOICES)
@app_commands.choices(kelly_fraction=_KELLY_FRACTION_CHOICES)
@app_commands.choices(visibility=_EVSIZE_VISIBILITY_CHOICES)
async def evsize_command(
    interaction: discord.Interaction,
    side: app_commands.Choice[str],
    entry: float,
    target: float,
    stop: float,
    probability: float,
    daily_risk: float,
    kelly_fraction: app_commands.Choice[str] | None = None,
    visibility: app_commands.Choice[str] | None = None,
):
    try:
        fk = float(kelly_fraction.value) if kelly_fraction is not None else 0.5
        result = ev_compute(
            side=side.value,
            entry=entry,
            target=target,
            stop=stop,
            probability=probability,
            daily_risk=daily_risk,
            fractional_kelly=fk,
        )
    except SizingError as e:
        await interaction.response.send_message(str(e), ephemeral=True)
        return

    embed = _build_ev_embed(result)
    vis = visibility.value if visibility is not None else "private"

    if vis == "public":
        await interaction.response.send_message(embed=embed, ephemeral=False)
    else:
        view = None
        if interaction.guild is not None:
            view = EvsizeShareView(embed.copy(), interaction.user.id)
        await interaction.response.send_message(embed=embed, ephemeral=True, view=view)
    logger.info(
        "evsize %s entry=%.2f target=%.2f stop=%.2f prob=%.1f risk=%.2f kelly_frac=%s -> grade=%s $%.2f by %s",
        result.side, entry, target, stop, probability, daily_risk,
        result.kelly_fraction, result.grade, result.suggested_risk, interaction.user,
    )


# ---------------------------------------------------------------------------
# Global error handler
# ---------------------------------------------------------------------------

@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        msg = "You don't have permission to use this command."
    elif isinstance(error, app_commands.BotMissingPermissions):
        msg = "I'm missing the required permissions for this command."
    else:
        logger.exception("Unhandled slash command error: %s", error)
        msg = "Something went wrong. Please try again later."

    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN, log_handler=None)
