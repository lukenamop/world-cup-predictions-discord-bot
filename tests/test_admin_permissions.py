from __future__ import annotations

import importlib
import sys
import types
import unittest
from datetime import datetime, timezone


class AdminPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_commands_use_discord_permission_overrides(self) -> None:
        admin_module = _load_admin_module_with_fake_discord()
        cog = admin_module.AdminCog(bot=types.SimpleNamespace())
        ctx = _FakeContext(
            guild=object(),
            manage_guild=False,
        )

        allowed = await cog._ensure_admin(ctx)

        self.assertTrue(allowed)
        self.assertEqual(ctx.responses, [])
        self.assertTrue(admin_module.AdminCog.admin.default_member_permissions.manage_guild)

    async def test_admin_commands_still_reject_dm_usage(self) -> None:
        admin_module = _load_admin_module_with_fake_discord()
        cog = admin_module.AdminCog(bot=types.SimpleNamespace())
        ctx = _FakeContext(
            guild=None,
            manage_guild=True,
        )

        allowed = await cog._ensure_admin(ctx)

        self.assertFalse(allowed)
        self.assertEqual(
            ctx.responses,
            [("Admin commands can only be used in a server.", True)],
        )


class AdminLockDeadlineParsingTests(unittest.TestCase):
    def test_parse_utc_datetime_accepts_zulu_utc(self) -> None:
        admin_module = _load_admin_module_with_fake_discord()

        parsed = admin_module._parse_utc_datetime("2026-06-11T18:00:00Z")

        self.assertEqual(parsed, datetime(2026, 6, 11, 18, 0, tzinfo=timezone.utc))

    def test_parse_utc_datetime_rejects_non_utc_offset(self) -> None:
        admin_module = _load_admin_module_with_fake_discord()

        with self.assertRaisesRegex(ValueError, "UTC timestamp"):
            admin_module._parse_utc_datetime("2026-06-11T18:00:00-04:00")


class AdminSetupConfigHelperTests(unittest.TestCase):
    def test_parse_local_datetime_uses_configured_timezone(self) -> None:
        admin_module = _load_admin_module_with_fake_discord()

        parsed = admin_module._parse_local_datetime(
            "2026-06-11 12:00",
            "America/New_York",
        )

        self.assertEqual(parsed, datetime(2026, 6, 11, 16, 0, tzinfo=timezone.utc))

    def test_validate_timezone_name_guides_invalid_values(self) -> None:
        admin_module = _load_admin_module_with_fake_discord()

        with self.assertRaisesRegex(ValueError, "America/New_York"):
            admin_module._validate_timezone_name("Eastern")

    def test_updated_scoring_rules_applies_only_requested_values(self) -> None:
        admin_module = _load_admin_module_with_fake_discord()

        rules = admin_module._updated_scoring_rules(
            baseline={"champion": 25, "runner_up": 15},
            use_default_scoring=False,
            group_winner=None,
            group_runner_up=None,
            group_third_place_qualifier=None,
            round_of_32_advancement=None,
            round_of_16_advancement=None,
            quarter_final_advancement=None,
            semi_final_advancement=None,
            final_advancement=None,
            third_place_winner=None,
            champion=30,
            runner_up=None,
        )

        self.assertEqual(rules["champion"], 30)
        self.assertEqual(rules["runner_up"], 15)


class _FakeContext:
    def __init__(self, *, guild: object | None, manage_guild: bool) -> None:
        self.guild = guild
        self.author = types.SimpleNamespace(
            id=123,
            guild_permissions=types.SimpleNamespace(manage_guild=manage_guild),
        )
        self.responses: list[tuple[str, bool]] = []

    async def respond(self, message: str, *, ephemeral: bool = False) -> None:
        self.responses.append((message, ephemeral))


def _load_admin_module_with_fake_discord() -> types.ModuleType:
    cogs_package = importlib.import_module("world_cup_bot.cogs")
    had_admin_attr = hasattr(cogs_package, "admin")
    previous_admin_attr = getattr(cogs_package, "admin", None)
    module_names = (
        "discord",
        "discord.ext",
        "discord.ext.commands",
        "world_cup_bot.cogs.admin",
    )
    previous_modules = {name: sys.modules.get(name) for name in module_names}

    discord = types.ModuleType("discord")
    discord.Permissions = _FakePermissions
    discord.SlashCommandGroup = _FakeSlashCommandGroup
    discord.ApplicationContext = object
    discord.Bot = object

    discord_ext = types.ModuleType("discord.ext")
    discord_commands = types.ModuleType("discord.ext.commands")
    discord_commands.Cog = object
    discord_ext.commands = discord_commands
    discord.ext = discord_ext

    sys.modules["discord"] = discord
    sys.modules["discord.ext"] = discord_ext
    sys.modules["discord.ext.commands"] = discord_commands
    sys.modules.pop("world_cup_bot.cogs.admin", None)

    try:
        return importlib.import_module("world_cup_bot.cogs.admin")
    finally:
        for name, module in previous_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        if had_admin_attr:
            cogs_package.admin = previous_admin_attr
        elif hasattr(cogs_package, "admin"):
            del cogs_package.admin


class _FakePermissions:
    def __init__(self, *, manage_guild: bool = False) -> None:
        self.manage_guild = manage_guild


class _FakeSlashCommandGroup:
    def __init__(
        self,
        name: str,
        description: str,
        *,
        default_member_permissions: _FakePermissions | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.default_member_permissions = default_member_permissions

    def command(self, **_: object) -> object:
        def decorator(func: object) -> object:
            return func

        return decorator
