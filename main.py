"""Entry point for BeichtBot."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import discord

try:  # pragma: no cover - optional dependency for local development convenience
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - handled gracefully when dependency missing
    load_dotenv = None

from beichtbot.bot import BeichtBot


def _load_env_file() -> None:
    """Load environment variables from a local .env file if available."""

    if load_dotenv is None:
        return

    env_path = Path(".env")
    if env_path.exists():
        load_dotenv(env_path)


def main() -> None:
    _load_env_file()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError(
            "Environment variable DISCORD_TOKEN ist nicht gesetzt. "
            "Bitte setze sie oder lege eine .env-Datei mit DISCORD_TOKEN an."
        )
    intents = discord.Intents.default()
    intents.guilds = True
    bot = BeichtBot()
    logging.getLogger("discord").setLevel(logging.WARNING)
    bot.run(token)


if __name__ == "__main__":
    main()
