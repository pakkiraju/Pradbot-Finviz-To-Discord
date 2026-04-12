"""Load local ``.env`` when present. Platform env vars always win (override=False)."""

from __future__ import annotations

from pathlib import Path

_CONFIGURED = False


def configure_environment() -> None:
    """Call once at process startup (before reading secrets).

    Loads ``.env`` next to this file with ``override=False``: variables already
    set by the host (e.g. Railway dashboard) are never replaced. Missing keys
    can still be filled from ``.env`` (local dev or a checked-in file).
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.is_file():
        load_dotenv(env_path, override=False)
