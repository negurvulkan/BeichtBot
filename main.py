"""Entry point for BeichtBot."""
from __future__ import annotations

import logging
import os

import discord

from beichtbot.bot import BeichtBot


def main() -> None:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Environment variable DISCORD_TOKEN ist nicht gesetzt.")
    intents = discord.Intents.default()
    intents.guilds = True
    bot = BeichtBot()
    logging.getLogger("discord").setLevel(logging.WARNING)
    bot.run(token)


if __name__ == "__main__":
    main()
