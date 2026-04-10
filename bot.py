"""PradBot — Discord bot for Finviz charts and more.

Run with:  python bot.py
Requires DISCORD_BOT_TOKEN and FINVIZ_API_KEY in .env.
"""

import asyncio
import io
import logging
import os
import sys
from pathlib import Path

import discord
from discord.ext import commands

from finviz_chart import fetch_chart, validate_symbol, TIMEFRAMES

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
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN, log_handler=None)
