"""Discord bot implementation for BeichtBot."""
from __future__ import annotations

import asyncio
import hmac
import logging
import re
import textwrap
from typing import Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from .config import (
    ConfigStore,
    GuildConfig,
    format_list,
    neutralize_mentions,
    now_ts,
)

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

MENTION_PATTERN = re.compile(r"@(?:everyone|here|&|!|#)")
URL_PATTERN = re.compile(r"https?://")
EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
PHONE_PATTERN = re.compile(r"\b(?:\+\d{1,3}\s?)?(?:\(\d{2,4}\)\s?)?\d{3,4}[-\s]?\d{3,4}\b")
CRISIS_KEYWORDS = {
    "selbstmord",
    "suizid",
    "notfall",
    "ich halte es nicht mehr aus",
    "ich will nicht mehr leben",
    "suicide",
}

DEFAULT_THREAD_NAME = "Beicht-Thread"


class ConfessionModal(discord.ui.Modal, title="Anonyme Beichte"):
    """Modal zum Erfassen einer neuen Beichte."""

    confession: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
        label="Beichte",
        style=discord.TextStyle.long,
        max_length=1800,
        placeholder="Was möchtest du anonym teilen?",
    )
    trigger_words: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
        label="Triggerwörter (optional)",
        style=discord.TextStyle.short,
        required=False,
        placeholder="z.B. Trauer, Verlust",
    )
    allow_replies: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
        label="Antworten erlauben? (ja/nein, optional)",
        style=discord.TextStyle.short,
        required=False,
    )
    lock_thread: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
        label="Thread sperren? (ja/nein, optional)",
        style=discord.TextStyle.short,
        required=False,
    )
    target_channel: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
        label="Ziel-Channel ID (optional)",
        style=discord.TextStyle.short,
        required=False,
    )

    def __init__(self, bot: "BeichtBot", interaction: discord.Interaction) -> None:
        super().__init__()
        self.bot = bot
        self.interaction = interaction

    async def on_submit(self, interaction: discord.Interaction) -> None:  # pragma: no cover - handled by discord
        await self.bot.handle_confession_submission(interaction, self)


class ReportModal(discord.ui.Modal, title="Beitrag melden"):
    """Modal zum Melden eines Beitrags."""

    reason: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
        label="Grund (optional)",
        style=discord.TextStyle.long,
        required=False,
        max_length=400,
    )

    def __init__(self, bot: "BeichtBot", message_id: int) -> None:
        super().__init__()
        self.bot = bot
        self.message_id = message_id

    async def on_submit(self, interaction: discord.Interaction) -> None:  # pragma: no cover
        await self.bot.handle_report_submission(interaction, self.message_id, str(self.reason.value))


class ReplyModal(discord.ui.Modal, title="Anonyme Antwort"):
    """Modal für anonyme Antworten."""

    reply: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
        label="Antwort",
        style=discord.TextStyle.long,
        max_length=1800,
    )
    unlock: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
        label="Thread entsperren? (ja/nein, optional)",
        style=discord.TextStyle.short,
        required=False,
    )

    def __init__(self, bot: "BeichtBot", message_id: int) -> None:
        super().__init__()
        self.bot = bot
        self.message_id = message_id

    async def on_submit(self, interaction: discord.Interaction) -> None:  # pragma: no cover
        await self.bot.handle_reply_submission(
            interaction,
            self.message_id,
            reply=str(self.reply.value),
            unlock=str(self.unlock.value or ""),
        )


class BeichtBot(commands.Bot):
    """Discord bot implementing the BeichtBot feature set."""

    def __init__(self, *, config: Optional[ConfigStore] = None) -> None:
        intents = discord.Intents.default()
        intents.message_content = False
        super().__init__(command_prefix="!", intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.config = config or ConfigStore()
        self.cooldowns: Dict[Tuple[int, int], float] = {}
        self.session_tasks: List[asyncio.Task[None]] = []

    async def setup_hook(self) -> None:
        self.tree.add_command(self.beichten)
        self.tree.add_command(self.beichtantwort)
        self.tree.add_command(self.hilfe)
        self.tree.add_command(self.melden)
        self.tree.add_command(self.beichtbot_setup)
        self.tree.add_command(self.beichtbot_kanaele)
        self.tree.add_command(self.beichtbot_woerter)
        self.tree.add_command(self.beichtbot_hash)
        self.tree.add_command(self.beichtbot_stats)
        self.tree.add_command(self.beichtbot_reset)
        self.tree.add_command(self.beichtbot_banner)
        self.tree.add_command(self.beichtbot_nachricht)
        self.tree.add_command(self.beichtbot_cooldown)
        await self.tree.sync()

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------
    def _create_hash(self, user_id: int, message_id: int) -> str:
        digest = hmac.new(self.config.secret.encode("utf-8"), f"{user_id}:{message_id}".encode("utf-8"), "sha256")
        return digest.hexdigest()

    async def _resolve_target_channel(
        self,
        guild: discord.Guild,
        config: GuildConfig,
        channel_id: Optional[int],
    ) -> Optional[discord.TextChannel]:
        if channel_id is None:
            channel_id = config.target_channel_id
        if channel_id is None:
            return None
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return None
        if config.allowed_target_channels and channel.id not in config.allowed_target_channels:
            return None
        return channel

    def _parse_bool(self, value: str, *, default: Optional[bool] = None) -> Optional[bool]:
        if not value:
            return default
        value = value.strip().lower()
        if value in {"ja", "true", "1", "on", "yes"}:
            return True
        if value in {"nein", "false", "0", "off", "no"}:
            return False
        return default

    def _spoiler_content(self, content: str) -> str:
        content = content.strip()
        if not content:
            return content
        if content.startswith("||") and content.endswith("||"):
            return content
        return f"||{content}||"

    def _check_word_lists(self, config: GuildConfig, text: str) -> Optional[str]:
        lowered = text.lower()
        for blocked in config.blacklist:
            if blocked and blocked in lowered:
                return f"Der Begriff `{blocked}` ist in diesem Server blockiert."
        if config.whitelist:
            if not any(word in lowered for word in config.whitelist):
                return "Dein Text enthält keines der notwendigen Schlüsselwörter."
        return None

    def _is_on_cooldown(self, guild_id: int, user_id: int, cooldown: int) -> bool:
        key = (guild_id, user_id)
        expiry = self.cooldowns.get(key, 0)
        if now_ts() < expiry:
            return True
        self.cooldowns[key] = now_ts() + cooldown
        return False

    def _check_pii(self, text: str) -> bool:
        return bool(EMAIL_PATTERN.search(text) or PHONE_PATTERN.search(text))

    def _check_crisis(self, text: str) -> bool:
        lowered = text.lower()
        return any(keyword in lowered for keyword in CRISIS_KEYWORDS)

    async def _notify_mods(
        self,
        config: GuildConfig,
        guild: discord.Guild,
        *,
        message: str,
    ) -> None:
        if not config.mod_channel_id:
            return
        channel = guild.get_channel(config.mod_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            await channel.send(message, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.exception("Failed to notify moderators")

    async def _schedule_autodelete(self, message: discord.Message, minutes: int) -> None:
        async def _delete_later() -> None:
            await asyncio.sleep(minutes * 60)
            try:
                await message.delete()
            except discord.HTTPException:
                log.info("Message %s could not be deleted automatically", message.id)

        self.session_tasks.append(asyncio.create_task(_delete_later()))

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------
    @app_commands.command(name="beichten", description="Anonym im Server posten")
    async def beichten(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Dieser Befehl kann nur in einem Server genutzt werden.", ephemeral=True)
            return
        config = self.config.get_guild_config(interaction.guild.id)
        cooldown = config.cooldown_seconds
        if cooldown > 0 and self._is_on_cooldown(interaction.guild.id, interaction.user.id, cooldown):
            await interaction.response.send_message("Bitte warte, bevor du erneut postest.", ephemeral=True)
            return
        await interaction.response.send_modal(ConfessionModal(self, interaction))

    async def handle_confession_submission(self, interaction: discord.Interaction, modal: ConfessionModal) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Dieser Befehl kann nur in einem Server genutzt werden.", ephemeral=True)
            return
        config = self.config.get_guild_config(interaction.guild.id)

        word_issue = self._check_word_lists(config, str(modal.confession.value))
        if word_issue:
            await interaction.response.send_message(word_issue, ephemeral=True)
            return

        allow_replies = self._parse_bool(str(modal.allow_replies.value), default=True)
        lock_thread = self._parse_bool(str(modal.lock_thread.value), default=config.default_thread_lock)
        channel_id = None
        if modal.target_channel.value:
            try:
                channel_id = int(str(modal.target_channel.value))
            except ValueError:
                await interaction.response.send_message("Die Channel-ID ist ungültig.", ephemeral=True)
                return

        target = await self._resolve_target_channel(interaction.guild, config, channel_id)
        if not target:
            await interaction.response.send_message("Kein gültiger Ziel-Channel konfiguriert.", ephemeral=True)
            return

        confession = neutralize_mentions(str(modal.confession.value))
        trigger_words = [w.strip() for w in str(modal.trigger_words.value).split(",") if w.strip()] if modal.trigger_words.value else []

        message_parts: List[str] = []
        if trigger_words:
            message_parts.append("**TW:** " + ", ".join(trigger_words))
        message_parts.append(self._spoiler_content(confession))
        content = "\n\n".join(message_parts)

        hints: List[str] = []
        if MENTION_PATTERN.search(confession):
            hints.append("Mentions wurden neutralisiert.")
        if URL_PATTERN.search(confession):
            hints.append("Hinweis: Links bitte verantwortungsvoll teilen.")
        pii_detected = self._check_pii(confession)
        crisis_detected = self._check_crisis(confession)
        if pii_detected:
            hints.append("Warnung: Der Text enthält möglicherweise persönliche Daten.")
        if crisis_detected:
            hints.append("Wenn du in Gefahr bist, suche bitte professionelle Hilfe.")

        try:
            message = await target.send(content, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as exc:
            await interaction.response.send_message(f"Fehler beim Posten: {exc}", ephemeral=True)
            return

        self.config.increment_stat(interaction.guild.id, "confessions")
        hash_id = self._create_hash(interaction.user.id, message.id)
        self.config.record_hash(interaction.guild.id, message.id, hash_id)
        self.config.record_flag(
            interaction.guild.id,
            message.id,
            crisis=crisis_detected,
            pii=pii_detected,
        )

        thread: Optional[discord.Thread] = None
        try:
            thread = await message.create_thread(name=DEFAULT_THREAD_NAME)
            if lock_thread:
                await thread.edit(locked=True)
        except discord.HTTPException:
            log.exception("Thread creation failed")

        if config.auto_delete_minutes:
            await self._schedule_autodelete(message, config.auto_delete_minutes)

        if crisis_detected or pii_detected:
            await self._notify_mods(
                config,
                interaction.guild,
                message=textwrap.dedent(
                    f"""
                    ⚠️ Hinweis auf sensiblen Inhalt.
                    Nachricht: https://discord.com/channels/{interaction.guild.id}/{message.channel.id}/{message.id}
                    Krise erkannt: {crisis_detected}
                    PII erkannt: {pii_detected}
                    """
                ).strip(),
            )

        acknowledgement = "Deine Beichte wurde anonym veröffentlicht."
        if hints:
            acknowledgement += "\n" + " ".join(hints)
        if thread and not lock_thread:
            acknowledgement += "\nEin Diskussions-Thread wurde geöffnet."
        await interaction.response.send_message(acknowledgement, ephemeral=True)

    @app_commands.command(name="beichtantwort", description="Anonym auf eine Beichte reagieren")
    @app_commands.describe(nachricht_id="ID der Ursprungsnachricht")
    async def beichtantwort(self, interaction: discord.Interaction, nachricht_id: str) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Nur in Servern verfügbar.", ephemeral=True)
            return
        try:
            message_id = int(nachricht_id)
        except ValueError:
            await interaction.response.send_message("Ungültige Nachricht-ID.", ephemeral=True)
            return
        await interaction.response.send_modal(ReplyModal(self, message_id))

    async def handle_reply_submission(
        self,
        interaction: discord.Interaction,
        message_id: int,
        *,
        reply: str,
        unlock: str,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Nur in Servern verfügbar.", ephemeral=True)
            return
        config = self.config.get_guild_config(interaction.guild.id)
        target = await self._resolve_target_channel(interaction.guild, config, None)
        if not target:
            await interaction.response.send_message("Keine Ziel-Konfiguration gefunden.", ephemeral=True)
            return
        try:
            message = await target.fetch_message(message_id)
        except discord.NotFound:
            await interaction.response.send_message("Nachricht nicht gefunden.", ephemeral=True)
            return
        except discord.HTTPException:
            await interaction.response.send_message("Fehler beim Zugriff auf die Nachricht.", ephemeral=True)
            return

        thread = message.thread
        if not thread:
            try:
                thread = await message.create_thread(name=DEFAULT_THREAD_NAME)
            except discord.HTTPException:
                await interaction.response.send_message("Es konnte kein Thread erstellt werden.", ephemeral=True)
                return

        should_unlock = self._parse_bool(unlock, default=False)
        if should_unlock and thread.locked:
            try:
                await thread.edit(locked=False)
            except discord.HTTPException:
                pass

        reply_text = neutralize_mentions(reply)
        try:
            await thread.send(self._spoiler_content(reply_text), allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            await interaction.response.send_message("Antwort konnte nicht gesendet werden.", ephemeral=True)
            return

        if thread.locked and should_unlock:
            try:
                await thread.edit(locked=True)
            except discord.HTTPException:
                pass

        self.config.increment_stat(interaction.guild.id, "responses")
        await interaction.response.send_message("Antwort wurde anonym veröffentlicht.", ephemeral=True)

    @app_commands.command(name="hilfe", description="Kurzanleitung für den BeichtBot")
    async def hilfe(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(title="BeichtBot Hilfe", colour=discord.Colour.blurple())
        embed.description = textwrap.dedent(
            """
            **/beichten** – öffnet ein anonymes Eingabe-Modal.
            **/beichtantwort** – antworte anonym auf eine bestehende Beichte.
            **/melden** – informiere das Mod-Team über problematische Inhalte.
            Datensicherheit: User-IDs werden nur gehasht gespeichert.
            """
        ).strip()
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="melden", description="Anonym einen Beitrag melden")
    @app_commands.describe(nachricht_id="ID der Nachricht, die gemeldet werden soll")
    async def melden(self, interaction: discord.Interaction, nachricht_id: str) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Nur in Servern verfügbar.", ephemeral=True)
            return
        try:
            message_id = int(nachricht_id)
        except ValueError:
            await interaction.response.send_message("Ungültige Nachricht-ID.", ephemeral=True)
            return
        await interaction.response.send_modal(ReportModal(self, message_id))

    async def handle_report_submission(self, interaction: discord.Interaction, message_id: int, reason: str) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Nur in Servern verfügbar.", ephemeral=True)
            return
        config = self.config.get_guild_config(interaction.guild.id)
        if not config.mod_channel_id:
            await interaction.response.send_message("Es wurde kein Mod-Channel konfiguriert.", ephemeral=True)
            return
        channel = interaction.guild.get_channel(config.mod_channel_id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Mod-Channel ungültig.", ephemeral=True)
            return
        link = f"https://discord.com/channels/{interaction.guild.id}/{config.target_channel_id}/{message_id}"
        text = textwrap.dedent(
            f"""
            🛡️ **Neue Meldung**
            Nachricht: {link}
            Grund: {reason or 'kein Grund angegeben'}
            """
        ).strip()
        try:
            await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            await interaction.response.send_message("Meldung konnte nicht übermittelt werden.", ephemeral=True)
            return
        self.config.increment_stat(interaction.guild.id, "reports")
        await interaction.response.send_message("Danke, das Mod-Team wurde informiert.", ephemeral=True)

    # ------------------------------------------------------------------
    # Admin commands
    # ------------------------------------------------------------------
    @app_commands.command(name="beichtbot-setup", description="BeichtBot konfigurieren")
    @app_commands.describe(
        ziel_channel="Standard-Ziel-Channel",
        mod_channel="Channel für Moderationshinweise",
        cooldown="Cooldown in Sekunden",
        auto_delete="Automatisches Löschen nach Minuten",
        ai_moderation="Einfache KI-Moderation aktivieren",
        thread_lock="Threads standardmäßig sperren",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def beichtbot_setup(
        self,
        interaction: discord.Interaction,
        ziel_channel: discord.TextChannel,
        mod_channel: Optional[discord.TextChannel] = None,
        cooldown: Optional[int] = None,
        auto_delete: Optional[int] = None,
        ai_moderation: Optional[bool] = None,
        thread_lock: Optional[bool] = None,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Nur in Servern verfügbar.", ephemeral=True)
            return
        config = self.config.get_guild_config(interaction.guild.id)
        config.target_channel_id = ziel_channel.id
        config.mod_channel_id = mod_channel.id if mod_channel else config.mod_channel_id
        if cooldown is not None:
            config.cooldown_seconds = max(0, cooldown)
        if auto_delete is not None:
            config.auto_delete_minutes = max(1, auto_delete)
        if ai_moderation is not None:
            config.allow_ai_moderation = ai_moderation
        if thread_lock is not None:
            config.default_thread_lock = thread_lock
        self.config.set_guild_config(config)
        await interaction.response.send_message("Konfiguration gespeichert.", ephemeral=True)

    @app_commands.command(name="beichtbot-kanäle", description="Liste erlaubter Ziel-Channels setzen")
    @app_commands.describe(ids="Kommagetrennte Channel-IDs")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def beichtbot_kanaele(self, interaction: discord.Interaction, ids: Optional[str] = None) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Nur in Servern verfügbar.", ephemeral=True)
            return
        channels: List[int] = []
        if ids:
            for part in ids.split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    channels.append(int(part))
                except ValueError:
                    await interaction.response.send_message(f"Ungültige ID: {part}", ephemeral=True)
                    return
        cfg = self.config.update_allowed_channels(interaction.guild.id, channels)
        allowed = ", ".join(str(cid) for cid in cfg.allowed_target_channels) or "(alle)"
        await interaction.response.send_message(f"Erlaubte Channels: {allowed}", ephemeral=True)

    @app_commands.command(name="beichtbot-wörter", description="Black- und White-List pflegen")
    @app_commands.describe(blacklist="Blockierte Wörter", whitelist="Erforderliche Wörter")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def beichtbot_woerter(
        self,
        interaction: discord.Interaction,
        blacklist: Optional[str] = None,
        whitelist: Optional[str] = None,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Nur in Servern verfügbar.", ephemeral=True)
            return
        blacklist_items = [w.strip() for w in blacklist.split(",") if w.strip()] if blacklist else None
        whitelist_items = [w.strip() for w in whitelist.split(",") if w.strip()] if whitelist else None
        cfg = self.config.set_lists(interaction.guild.id, blacklist=blacklist_items, whitelist=whitelist_items)
        await interaction.response.send_message(
            textwrap.dedent(
                f"""
                Blacklist: {format_list(cfg.blacklist)}
                Whitelist: {format_list(cfg.whitelist)}
                """
            ).strip(),
            ephemeral=True,
        )

    @app_commands.command(name="beichtbot-hash", description="Hash-ID eines Posts anzeigen")
    @app_commands.describe(nachricht_id="ID des Beitrags")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def beichtbot_hash(self, interaction: discord.Interaction, nachricht_id: str) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Nur in Servern verfügbar.", ephemeral=True)
            return
        try:
            message_id = int(nachricht_id)
        except ValueError:
            await interaction.response.send_message("Ungültige ID.", ephemeral=True)
            return
        hash_id = self.config.get_hash(interaction.guild.id, message_id)
        if not hash_id:
            await interaction.response.send_message("Kein Hash gefunden.", ephemeral=True)
            return
        await interaction.response.send_message(f"Hash-ID: `{hash_id}`", ephemeral=True)

    @app_commands.command(name="beichtbot-stats", description="Statistiken anzeigen")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def beichtbot_stats(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Nur in Servern verfügbar.", ephemeral=True)
            return
        cfg = self.config.get_guild_config(interaction.guild.id)
        embed = discord.Embed(title="BeichtBot Statistiken", colour=discord.Colour.dark_gold())
        for key, value in cfg.stats.items():
            embed.add_field(name=key.capitalize(), value=str(value))
        embed.add_field(name="Crisis-Flags", value=str(len(cfg.crisis_flags)))
        embed.add_field(name="PII-Flags", value=str(len(cfg.pii_flags)))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="beichtbot-reset", description="Setzt die Konfiguration zurück")
    @app_commands.checks.has_permissions(administrator=True)
    async def beichtbot_reset(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Nur in Servern verfügbar.", ephemeral=True)
            return
        self.config.reset_guild(interaction.guild.id)
        await interaction.response.send_message("Konfiguration wurde zurückgesetzt.", ephemeral=True)

    @app_commands.command(name="beichtbot-banner", description="Einen Hinweis-Banner setzen")
    @app_commands.describe(text="Text, der über dem Channel angezeigt werden soll")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def beichtbot_banner(self, interaction: discord.Interaction, text: Optional[str] = None) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Nur in Servern verfügbar.", ephemeral=True)
            return
        self.config.set_banner(interaction.guild.id, text)
        await interaction.response.send_message("Banner aktualisiert." if text else "Banner entfernt.", ephemeral=True)

    @app_commands.command(name="beichtbot-nachricht", description="Link zu einer BeichtBot-Nachricht")
    @app_commands.describe(nachricht_id="ID der Nachricht")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def beichtbot_nachricht(self, interaction: discord.Interaction, nachricht_id: str) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Nur in Servern verfügbar.", ephemeral=True)
            return
        cfg = self.config.get_guild_config(interaction.guild.id)
        if not cfg.target_channel_id:
            await interaction.response.send_message("Kein Ziel-Channel konfiguriert.", ephemeral=True)
            return
        try:
            message_id = int(nachricht_id)
        except ValueError:
            await interaction.response.send_message("Ungültige ID.", ephemeral=True)
            return
        url = f"https://discord.com/channels/{interaction.guild.id}/{cfg.target_channel_id}/{message_id}"
        await interaction.response.send_message(url, ephemeral=True)

    @app_commands.command(name="beichtbot-cooldown", description="Cooldown für User zurücksetzen")
    @app_commands.describe(user="User, dessen Cooldown zurückgesetzt werden soll")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def beichtbot_cooldown(self, interaction: discord.Interaction, user: Optional[discord.User] = None) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Nur in Servern verfügbar.", ephemeral=True)
            return
        if user:
            key = (interaction.guild.id, user.id)
            self.cooldowns.pop(key, None)
            await interaction.response.send_message(f"Cooldown für {user.mention} wurde entfernt.", ephemeral=True)
            return
        to_remove = [key for key in self.cooldowns if key[0] == interaction.guild.id]
        for key in to_remove:
            del self.cooldowns[key]
        await interaction.response.send_message("Alle Cooldowns wurden entfernt.", ephemeral=True)


__all__ = ["BeichtBot", "ConfessionModal"]
