"""PradBot — Discord bot for Finviz charts and more.

Run with:  python bot.py
Requires DISCORD_BOT_TOKEN and FINVIZ_API_KEY in .env.
"""

import asyncio
import io
import logging
import os
import re
import sys
from pathlib import Path

import discord
from discord.ext import commands

from finviz_chart import fetch_chart, validate_symbol, TIMEFRAMES
from finviz_options import fetch_options
from gex_compute import compute_gex

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

def _load_env():
    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).resolve().parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)
    except ImportError:
        pass

_load_env()

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
if not DISCORD_BOT_TOKEN:
    logger.critical(
        "DISCORD_BOT_TOKEN not set. Add it to .env in %s",
        Path(__file__).resolve().parent,
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    logger.info("Logged in as %s (id=%s)", bot.user, bot.user.id)


# ---------------------------------------------------------------------------
# !chart command
# ---------------------------------------------------------------------------

@bot.command(name="chart")
async def chart_command(ctx: commands.Context, symbol: str | None = None, timeframe: str = "d"):
    """Post a FinViz chart for a ticker.

    Usage:
        !chart AAPL          — daily chart
        !chart MSFT w        — weekly chart
        !chart TSLA m        — monthly chart
    """
    if symbol is None:
        await ctx.reply("Usage: `!chart <SYMBOL> [d|w|m]`\nExample: `!chart AAPL w`")
        return

    ticker = validate_symbol(symbol)
    if ticker is None:
        await ctx.reply(f"`{symbol}` doesn't look like a valid ticker symbol.")
        return

    tf = timeframe.lower()
    if tf not in TIMEFRAMES:
        await ctx.reply(f"Unknown timeframe `{timeframe}`. Use `d` (daily), `w` (weekly), or `m` (monthly).")
        return

    tf_label = {"d": "Daily", "w": "Weekly", "m": "Monthly"}[tf]
    async with ctx.typing():
        data = await asyncio.to_thread(fetch_chart, ticker, tf)

    if data is None:
        await ctx.reply(f"Could not fetch chart for **{ticker}**. Check the logs for details.")
        return

    filename = f"{ticker}_{tf_label.lower()}.png"
    file = discord.File(io.BytesIO(data), filename=filename)

    embed = discord.Embed(
        title=f"{ticker} — {tf_label} Chart",
        color=0x2ECC71,
        url=f"https://finviz.com/quote.ashx?t={ticker}",
    )
    embed.set_image(url=f"attachment://{filename}")
    embed.set_footer(text="Data from FinViz")

    await ctx.reply(embed=embed, file=file)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

@chart_command.error
async def chart_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply("Usage: `!chart <SYMBOL> [d|w|m]`\nExample: `!chart AAPL`")
    else:
        logger.exception("Unhandled error in !chart: %s", error)
        await ctx.reply("Something went wrong. Please try again later.")


# ---------------------------------------------------------------------------
# !gex command
# ---------------------------------------------------------------------------

def _fmt_gex(value: float) -> str:
    """Format a GEX value into a readable string with K/M suffix."""
    abs_v = abs(value)
    sign = "-" if value < 0 else ""
    if abs_v >= 1_000_000:
        return f"{sign}{abs_v / 1_000_000:,.2f}M"
    if abs_v >= 1_000:
        return f"{sign}{abs_v / 1_000:,.1f}K"
    return f"{sign}{abs_v:,.0f}"


def _fmt_oi(value: float) -> str:
    """Format an OI count with comma separators."""
    return f"{int(value):,}"


def _build_gex_embed(summary) -> discord.Embed:
    """Build a Discord embed from a GexSummary."""
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

    # Top strikes table
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
        # Discord field value max is 1024 chars; truncate if needed
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


@bot.command(name="gex")
async def gex_command(ctx: commands.Context, symbol: str | None = None, expiry: str | None = None):
    """Post GEX / options analysis for a ticker.

    Usage:
        !gex AAPL               — nearest expiry
        !gex AAPL 2025-07-18    — specific expiry
    """
    if symbol is None:
        await ctx.reply("Usage: `!gex <SYMBOL> [YYYY-MM-DD]`\nExample: `!gex AAPL` or `!gex SPY 2025-07-18`")
        return

    ticker = validate_symbol(symbol)
    if ticker is None:
        await ctx.reply(f"`{symbol}` doesn't look like a valid ticker symbol.")
        return

    if expiry is not None:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", expiry):
            await ctx.reply(f"Invalid date format `{expiry}`. Use `YYYY-MM-DD` (e.g. `2025-07-18`).")
            return

    async with ctx.typing():
        rows = await asyncio.to_thread(fetch_options, ticker, expiry)

    if not rows:
        msg = f"No options data returned for **{ticker}**"
        if expiry:
            msg += f" (expiry {expiry})"
        msg += ". Check that the symbol has listed options and the expiry date is valid."
        await ctx.reply(msg)
        return

    used_expiry = expiry or rows[0].expiry or "unknown"
    summary = compute_gex(ticker, used_expiry, rows)

    if summary is None:
        await ctx.reply(f"Could not compute GEX for **{ticker}**. No valid strike data found.")
        return

    embed = _build_gex_embed(summary)
    await ctx.reply(embed=embed)


@gex_command.error
async def gex_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply("Usage: `!gex <SYMBOL> [YYYY-MM-DD]`\nExample: `!gex AAPL`")
    else:
        logger.exception("Unhandled error in !gex: %s", error)
        await ctx.reply("Something went wrong. Please try again later.")


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN, log_handler=None)
