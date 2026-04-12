"""Load local ``.env`` for development. On Railway, use injected variables only."""

from __future__ import annotations

import os
from pathlib import Path

_CONFIGURED = False


def configure_environment() -> None:
    """Call once at process startup (before reading secrets).

    When ``RAILWAY_PROJECT_ID`` is set, Railway has already populated
    ``os.environ``; we skip reading a ``.env`` file so nothing on disk can
    interfere with ``python-dotenv`` parsing.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    if os.environ.get("RAILWAY_PROJECT_ID"):
        return

    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.is_file():
        load_dotenv(env_path, override=False)
