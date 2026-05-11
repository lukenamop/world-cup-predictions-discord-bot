from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEZONE = "America/Indiana/Indianapolis"
DEFAULT_LIVE_RESULTS_PROVIDER = "fifa_public_calendar"
VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class SettingsError(RuntimeError):
    """Raised when runtime configuration is missing or invalid."""


@dataclass(frozen=True)
class AppSettings:
    discord_token: str
    database_url: str
    user_agent: str
    bot_env: str
    log_level: str
    owner_user_ids: frozenset[str]
    operator_guild_id: str | None
    default_timezone: str
    live_results_provider: str

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        require_secrets: bool = True,
    ) -> "AppSettings":
        source = os.environ if env is None else env
        discord_token = _clean(source.get("DISCORD_TOKEN"))
        database_url = _clean(source.get("DATABASE_URL"))
        user_agent = _clean(source.get("USER_AGENT"))

        missing: list[str] = []
        if require_secrets and not discord_token:
            missing.append("DISCORD_TOKEN")
        if require_secrets and not database_url:
            missing.append("DATABASE_URL")
        if require_secrets and not user_agent:
            missing.append("USER_AGENT")
        if missing:
            names = ", ".join(missing)
            raise SettingsError(f"Missing required environment variable(s): {names}")
        if database_url:
            _validate_database_url(database_url)

        timezone = _clean(source.get("DEFAULT_TIMEZONE")) or DEFAULT_TIMEZONE
        _validate_timezone(timezone)

        log_level = (_clean(source.get("LOG_LEVEL")) or "INFO").upper()
        if log_level not in VALID_LOG_LEVELS:
            raise SettingsError(f"Invalid LOG_LEVEL: {log_level}")

        return cls(
            discord_token=discord_token or "",
            database_url=database_url or "",
            user_agent=user_agent or "",
            bot_env=_clean(source.get("BOT_ENV")) or "development",
            log_level=log_level,
            owner_user_ids=_parse_owner_ids(source.get("OWNER_USER_IDS", "")),
            operator_guild_id=_parse_optional_discord_id(
                source.get("OPERATOR_GUILD_ID"),
                name="OPERATOR_GUILD_ID",
            ),
            default_timezone=timezone,
            live_results_provider=(
                _clean(source.get("LIVE_RESULTS_PROVIDER"))
                or DEFAULT_LIVE_RESULTS_PROVIDER
            ),
        )

    @property
    def database_log_target(self) -> str:
        return mask_database_url(self.database_url)


def mask_database_url(database_url: str) -> str:
    if not database_url:
        return "<unset>"

    parts = urlsplit(database_url)
    if not parts.scheme:
        return "<invalid-url>"

    host = parts.hostname or "<host-missing>"
    port = f":{parts.port}" if parts.port else ""
    database = parts.path.lstrip("/") or "<database-missing>"
    username = parts.username or "<user-missing>"
    return f"{parts.scheme}://{username}:***@{host}{port}/{database}"


def _validate_database_url(database_url: str) -> None:
    parts = urlsplit(database_url)
    if parts.scheme not in {"postgresql", "postgres"}:
        raise SettingsError("Invalid DATABASE_URL: expected postgresql:// URL")
    if not parts.username:
        raise SettingsError("Invalid DATABASE_URL: missing database user")
    if not parts.hostname:
        raise SettingsError("Invalid DATABASE_URL: missing database host")
    if not parts.path.lstrip("/"):
        raise SettingsError("Invalid DATABASE_URL: missing database name")
    try:
        parts.port
    except ValueError as exc:
        raise SettingsError("Invalid DATABASE_URL: invalid database port") from exc


def _parse_owner_ids(raw_value: str | None) -> frozenset[str]:
    if not raw_value:
        return frozenset()

    values = []
    for item in raw_value.split(","):
        value = item.strip()
        if value:
            values.append(value)
    return frozenset(values)


def _parse_optional_discord_id(raw_value: str | None, *, name: str) -> str | None:
    value = _clean(raw_value)
    if value is None:
        return None
    if not value.isdigit():
        raise SettingsError(f"Invalid {name}: expected a Discord snowflake ID")
    return value


def _validate_timezone(timezone: str) -> None:
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise SettingsError(f"Invalid DEFAULT_TIMEZONE: {timezone}") from exc


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
