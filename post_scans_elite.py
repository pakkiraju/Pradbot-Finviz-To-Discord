"""Post Finviz scan results to Discord — Elite (API key) version.

Fully self-contained. Reads FINVIZ_API_KEY from the environment (use Railway Variables when hosted).

Usage:
  python post_scans_elite.py
  python post_scans_elite.py --config webhooks.json
  python post_scans_elite.py --dry-run
  python post_scans_elite.py --verbose
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path


def _load_dotenv_if_present() -> None:
    """Load `.env` for local runs (same as bot.py). Production sets vars in the environment."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    base = Path(__file__).resolve().parent
    for candidate in (base / ".env", base.parent / ".env"):
        if candidate.is_file():
            load_dotenv(candidate)
            return


_load_dotenv_if_present()

from scan_registry import SCAN_BY_ID, SCANS
from fetch_elite import fetch_scan_with_screener
from discord_payload import build_embeds, post_to_webhook

logger = logging.getLogger("discord_poster.elite")


def _load_webhooks(path: Path) -> dict[str, str]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    hooks: dict[str, str] = {}
    for k, v in raw.items():
        if isinstance(v, str) and v.startswith("https://"):
            hooks[k] = v
        elif isinstance(v, dict) and v.get("enabled", True):
            url = v.get("url", "")
            if url.startswith("https://"):
                hooks[k] = url
    return hooks


def run(webhooks: dict[str, str], dry_run: bool = False):
    posted = 0
    errors = 0

    for scan in SCANS:
        if scan.scan_id not in webhooks:
            continue

        logger.info("Fetching: %s", scan.title)
        try:
            rows, screener_url = fetch_scan_with_screener(scan)
        except Exception as e:
            logger.error("Fetch failed for %s: %s", scan.scan_id, e)
            errors += 1
            continue

        logger.info("  -> %d rows", len(rows))
        embeds = build_embeds(scan.title, rows, screener_url=screener_url)

        if dry_run:
            for em in embeds:
                logger.info("  [DRY-RUN] embed title=%s, desc_len=%d", em.get("title"), len(em.get("description", "")))
            posted += 1
            continue

        webhook_url = webhooks[scan.scan_id]
        ok = post_to_webhook(webhook_url, embeds)
        if ok:
            posted += 1
            logger.info("  Posted to Discord.")
        else:
            errors += 1
            logger.error("  Failed to post to Discord.")

        time.sleep(1.5)

    logger.info("Done. Posted: %d, Errors: %d", posted, errors)


def main():
    parser = argparse.ArgumentParser(description="Post Finviz Elite scans to Discord")
    parser.add_argument("--config", default="webhooks.json", help="Path to webhooks JSON (default: webhooks.json)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and format but don't POST to Discord")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = Path(__file__).resolve().parent / config_path
    if not config_path.exists():
        logger.error("Webhook config not found: %s", config_path)
        logger.error("Copy webhooks.example.json to webhooks.json and fill in your webhook URLs.")
        sys.exit(1)

    webhooks = _load_webhooks(config_path)
    if not webhooks:
        logger.error("No valid webhook URLs found in %s", config_path)
        sys.exit(1)

    unknown = set(webhooks) - set(SCAN_BY_ID)
    if unknown:
        logger.warning("Unknown scan IDs in config (will be ignored): %s", ", ".join(sorted(unknown)))

    logger.info("Loaded %d webhook(s): %s", len(webhooks), ", ".join(sorted(webhooks)))

    run(webhooks, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
