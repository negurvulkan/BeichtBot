"""Configuration management for BeichtBot."""
from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

CONFIG_VERSION = 1


def _default_data() -> Dict[str, object]:
    return {
        "version": CONFIG_VERSION,
        "guilds": {},
        "hash_secret": None,
    }


@dataclass
class GuildConfig:
    """Guild specific configuration for BeichtBot."""

    guild_id: int
    target_channel_id: Optional[int] = None
    mod_channel_id: Optional[int] = None
    allowed_target_channels: Set[int] = field(default_factory=set)
    cooldown_seconds: int = 120
    auto_delete_minutes: Optional[int] = None
    allow_ai_moderation: bool = False
    default_thread_lock: bool = True
    banner_text: Optional[str] = None
    blacklist: Set[str] = field(default_factory=set)
    whitelist: Set[str] = field(default_factory=set)
    stats: Dict[str, int] = field(default_factory=lambda: {
        "confessions": 0,
        "responses": 0,
        "reports": 0,
        "ai_flags": 0,
    })
    hashed_posts: Dict[int, str] = field(default_factory=dict)
    pii_flags: List[int] = field(default_factory=list)
    crisis_flags: List[int] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        data = asdict(self)
        data["allowed_target_channels"] = list(self.allowed_target_channels)
        data["blacklist"] = sorted(self.blacklist)
        data["whitelist"] = sorted(self.whitelist)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "GuildConfig":
        return cls(
            guild_id=int(data["guild_id"]),
            target_channel_id=data.get("target_channel_id"),
            mod_channel_id=data.get("mod_channel_id"),
            allowed_target_channels=set(data.get("allowed_target_channels", [])),
            cooldown_seconds=int(data.get("cooldown_seconds", 120)),
            auto_delete_minutes=data.get("auto_delete_minutes"),
            allow_ai_moderation=bool(data.get("allow_ai_moderation", False)),
            default_thread_lock=bool(data.get("default_thread_lock", True)),
            banner_text=data.get("banner_text"),
            blacklist=set(data.get("blacklist", [])),
            whitelist=set(data.get("whitelist", [])),
            stats=dict(data.get("stats", {})),
            hashed_posts={int(k): v for k, v in data.get("hashed_posts", {}).items()},
            pii_flags=list(map(int, data.get("pii_flags", []))),
            crisis_flags=list(map(int, data.get("crisis_flags", []))),
        )


class ConfigStore:
    """Handles persistence of guild configurations."""

    def __init__(self, path: os.PathLike[str] | str = "beichtbot_state.json") -> None:
        self.path = Path(path)
        self._data: Dict[str, object] = _default_data()
        self._load()

    # ------------------------------------------------------------------
    # JSON handling
    # ------------------------------------------------------------------
    def _load(self) -> None:
        if not self.path.exists():
            self._data = _default_data()
            return
        with self.path.open("r", encoding="utf-8") as fp:
            self._data = json.load(fp)
        if "version" not in self._data:
            self._data["version"] = CONFIG_VERSION
        if "guilds" not in self._data:
            self._data["guilds"] = {}
        if "hash_secret" not in self._data or not self._data["hash_secret"]:
            self._data["hash_secret"] = secrets.token_hex(32)
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as fp:
            json.dump(self._data, fp, indent=2, sort_keys=True)
        temp.replace(self.path)

    # ------------------------------------------------------------------
    # Guild handling
    # ------------------------------------------------------------------
    def get_guild_config(self, guild_id: int) -> GuildConfig:
        guilds = self._data.setdefault("guilds", {})
        raw = guilds.get(str(guild_id))
        if raw is None:
            cfg = GuildConfig(guild_id=guild_id)
            self.set_guild_config(cfg)
            return cfg
        return GuildConfig.from_dict(raw)

    def set_guild_config(self, config: GuildConfig) -> None:
        guilds = self._data.setdefault("guilds", {})
        guilds[str(config.guild_id)] = config.to_dict()
        self._save()

    # ------------------------------------------------------------------
    # Stats and hashes
    # ------------------------------------------------------------------
    def record_hash(self, guild_id: int, message_id: int, hash_id: str) -> None:
        cfg = self.get_guild_config(guild_id)
        cfg.hashed_posts[message_id] = hash_id
        self.set_guild_config(cfg)

    def get_hash(self, guild_id: int, message_id: int) -> Optional[str]:
        cfg = self.get_guild_config(guild_id)
        return cfg.hashed_posts.get(message_id)

    def increment_stat(self, guild_id: int, key: str, amount: int = 1) -> None:
        cfg = self.get_guild_config(guild_id)
        cfg.stats[key] = cfg.stats.get(key, 0) + amount
        self.set_guild_config(cfg)

    def set_banner(self, guild_id: int, text: Optional[str]) -> None:
        cfg = self.get_guild_config(guild_id)
        cfg.banner_text = text
        self.set_guild_config(cfg)

    def set_lists(
        self,
        guild_id: int,
        *,
        blacklist: Optional[Iterable[str]] = None,
        whitelist: Optional[Iterable[str]] = None,
    ) -> GuildConfig:
        cfg = self.get_guild_config(guild_id)
        if blacklist is not None:
            cfg.blacklist = set(map(str.lower, blacklist))
        if whitelist is not None:
            cfg.whitelist = set(map(str.lower, whitelist))
        self.set_guild_config(cfg)
        return cfg

    def update_allowed_channels(self, guild_id: int, channels: Iterable[int]) -> GuildConfig:
        cfg = self.get_guild_config(guild_id)
        cfg.allowed_target_channels = set(channels)
        self.set_guild_config(cfg)
        return cfg

    # ------------------------------------------------------------------
    # Secrets
    # ------------------------------------------------------------------
    @property
    def secret(self) -> str:
        secret = self._data.get("hash_secret")
        if not secret:
            secret = secrets.token_hex(32)
            self._data["hash_secret"] = secret
            self._save()
        return str(secret)

    # ------------------------------------------------------------------
    # Flag tracking
    # ------------------------------------------------------------------
    def record_flag(self, guild_id: int, message_id: int, *, crisis: bool = False, pii: bool = False) -> None:
        cfg = self.get_guild_config(guild_id)
        if crisis:
            cfg.crisis_flags.append(message_id)
            cfg.stats["ai_flags"] = cfg.stats.get("ai_flags", 0) + 1
        if pii:
            cfg.pii_flags.append(message_id)
        self.set_guild_config(cfg)

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------
    def reset_guild(self, guild_id: int) -> None:
        guilds = self._data.get("guilds", {})
        if str(guild_id) in guilds:
            del guilds[str(guild_id)]
            self._save()

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------
    def list_guilds(self) -> List[int]:
        guilds = self._data.get("guilds", {})
        return [int(gid) for gid in guilds.keys()]


def neutralize_mentions(text: str) -> str:
    """Prevent pings by inserting zero width spaces after @."""
    return text.replace("@", "@\u200b")


def format_list(values: Iterable[str]) -> str:
    data = list(values)
    if not data:
        return "-"
    return ", ".join(sorted(data))


def now_ts() -> float:
    return time.monotonic()
