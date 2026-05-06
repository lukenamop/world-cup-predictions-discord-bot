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


class OperatorPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_operator_allows_owner_without_administrator(self) -> None:
        operator_module = _load_operator_module_with_fake_discord()
        cog = operator_module.OperatorCog(
            bot=types.SimpleNamespace(
                settings=types.SimpleNamespace(
                    operator_guild_id="999",
                    owner_user_ids=frozenset({"123"}),
                )
            )
        )
        ctx = _FakeContext(
            guild=types.SimpleNamespace(id=999),
            manage_guild=False,
            administrator=False,
        )

        allowed = await cog._ensure_operator(ctx)

        self.assertTrue(allowed)
        self.assertEqual(ctx.responses, [])

    async def test_operator_rejects_wrong_guild(self) -> None:
        operator_module = _load_operator_module_with_fake_discord()
        cog = operator_module.OperatorCog(
            bot=types.SimpleNamespace(
                settings=types.SimpleNamespace(
                    operator_guild_id="999",
                    owner_user_ids=frozenset({"123"}),
                )
            )
        )
        ctx = _FakeContext(
            guild=types.SimpleNamespace(id=111),
            manage_guild=False,
            administrator=True,
        )

        allowed = await cog._ensure_operator(ctx)

        self.assertFalse(allowed)
        self.assertEqual(
            ctx.responses,
            [
                (
                    "Operator commands can only be used in the configured operator server.",
                    True,
                )
            ],
        )


class OperatorSetupTests(unittest.TestCase):
    def test_setup_scopes_registered_cog_command_to_operator_guild(self) -> None:
        operator_module = _load_operator_module_with_fake_discord()
        bot = _FakeBot(
            settings=types.SimpleNamespace(
                operator_guild_id="999",
                owner_user_ids=frozenset(),
            )
        )

        operator_module.setup(bot)

        self.assertEqual(len(bot.cogs), 1)
        self.assertEqual(bot.cogs[0].operator.guild_ids, [999])


class OperatorSyncMessageTests(unittest.TestCase):
    def test_operator_sync_message_surfaces_failures(self) -> None:
        operator_module = _load_operator_module_with_fake_discord()
        report = operator_module.ResultSyncJobReport(
            summaries=[],
            failures=[
                operator_module.ResultSyncFailure(
                    guild_id="guild-1",
                    config_hash="hash",
                    error="provider unavailable",
                )
            ],
            fetched_match_count=0,
        )

        message = operator_module._sync_response_message(report)

        self.assertIn("finished with failures", message)
        self.assertIn("Failed: 1", message)
        self.assertIn("guild-1", message)

    def test_operator_sync_message_uses_reported_fetch_total(self) -> None:
        operator_module = _load_operator_module_with_fake_discord()
        report = operator_module.ResultSyncJobReport(
            summaries=[],
            failures=[],
            fetched_match_count=208,
        )

        message = operator_module._sync_response_message(report)

        self.assertIn("Fetched 208", message)


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
    def test_resolve_lock_deadline_accepts_utc_input(self) -> None:
        admin_module = _load_admin_module_with_fake_discord()

        parsed = admin_module._resolve_lock_deadline(
            existing=None,
            lock_deadline_utc="2026-06-11T18:00:00Z",
            clear_lock_deadline=False,
        )

        self.assertEqual(parsed, datetime(2026, 6, 11, 18, 0, tzinfo=timezone.utc))

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

    def test_lock_embed_includes_deadline_and_prediction_commands(self) -> None:
        admin_module = _load_admin_module_with_fake_discord()
        settings = types.SimpleNamespace(
            predictions_open=True,
            lock_deadline_utc=datetime(2026, 6, 11, 18, 0, tzinfo=timezone.utc),
            timezone="America/New_York",
        )
        tournament = types.SimpleNamespace(tournament_name="Test Cup")

        embed = admin_module._lock_embed(settings=settings, tournament=tournament)

        self.assertEqual(embed.title, "Prediction Lock")
        self.assertIn("predictions are open", embed.description)
        self.assertIn("/predict", _field_value(embed, "Commands"))
        self.assertIn("<t:1781200800:F>", _field_value(embed, "Deadline"))
        self.assertIn("<t:1781200800:R>", _field_value(embed, "Deadline"))

    def test_setup_embed_uses_admin_friendly_labels_and_next_steps(self) -> None:
        admin_module = _load_admin_module_with_fake_discord()
        settings = admin_module.GuildSettings(
            guild_id="guild-1",
            announcement_channel_id="111",
            leaderboard_channel_id="222",
            timezone="America/Chicago",
            live_results_provider="fifa_public_calendar",
            lock_deadline_utc=None,
            predictions_open=False,
            scoring_rules=admin_module._default_scoring_rules(),
            privacy_defaults={"share_full_bracket": True},
            lock_mode=admin_module.LOCK_MODE,
        )
        tournament = admin_module.TournamentEmbedContext(
            tournament_id="fifa-world-cup-2026",
            tournament_name="FIFA World Cup 2026",
            config_hash="3b3023f182d9abcdef",
            first_kickoff_utc=datetime(2026, 6, 11, 19, 0, tzinfo=timezone.utc),
        )

        embed = admin_module._setup_embed(
            settings=settings,
            title="Prediction League Setup Saved",
            tournament=tournament,
        )

        self.assertEqual(_field_value(embed, "Announcements"), "<#111>")
        self.assertEqual(_field_value(embed, "Leaderboard"), "<#222>")
        self.assertIn(
            "Prediction brackets public by default",
            _field_value(embed, "Privacy default"),
        )
        self.assertIn("/preferences", _field_value(embed, "Privacy default"))
        self.assertIn("Auto-locks at first kickoff", _field_value(embed, "Lock deadline"))
        self.assertIn("<t:1781204400:F>", _field_value(embed, "Lock deadline"))
        self.assertIn("<t:1781204400:R>", _field_value(embed, "Lock deadline"))
        self.assertIn(
            "Config `fifa-world-cup-2026`, version `3b3023f182d9`",
            _field_value(embed, "Tournament"),
        )
        self.assertEqual(
            _field_value(embed, "Next steps"),
            "When the league is ready, run `/admin open`. Then run "
            "`/admin post kind: rules`, and `/admin post kind: lock`.",
        )
        self.assertIsNone(embed.footer)
        self.assertNotIn("Live provider", [field.name for field in embed.fields])

    def test_setup_embed_scoring_copy_explains_advancement_points(self) -> None:
        admin_module = _load_admin_module_with_fake_discord()

        value = admin_module._format_scoring_rules(
            admin_module.ScoringRules.from_mapping(admin_module._default_scoring_rules())
        )

        self.assertIn("Group stage points", value)
        self.assertIn("Group winner +3", value)
        self.assertIn("Ro32 +1, Ro16 +2, QF +5, SF +10, F +15", value)
        self.assertIn("Exact placement points", value)
        self.assertIn("Champion +25, runner-up +15, third-place +10", value)

    def test_rules_embed_uses_public_scoring_copy(self) -> None:
        admin_module = _load_admin_module_with_fake_discord()
        settings = admin_module.GuildSettings(
            guild_id="guild-1",
            announcement_channel_id="111",
            leaderboard_channel_id="222",
            timezone="America/Chicago",
            live_results_provider="fifa_public_calendar",
            lock_deadline_utc=None,
            predictions_open=False,
            scoring_rules=admin_module._default_scoring_rules(),
            privacy_defaults={"share_full_bracket": False},
            lock_mode=admin_module.LOCK_MODE,
        )
        tournament = types.SimpleNamespace(tournament_name="FIFA World Cup 2026")

        embed = admin_module._rules_embed(settings=settings, tournament=tournament)

        self.assertEqual(embed.title, "League Rules")
        self.assertEqual(
            embed.description,
            "Pick teams, not scores. Your full bracket locks before the first group "
            "stage match.",
        )
        self.assertEqual(_field_value(embed, "Tournament"), "FIFA World Cup 2026")
        self.assertEqual(
            _field_value(embed, "Bracket visibility"),
            "Full brackets are private by default. Use `/preferences` to share yours.",
        )
        self.assertEqual(
            _field_value(embed, "Group stage points"),
            "Group winner +3, group runner-up +2, advancing third-place team +1",
        )
        self.assertEqual(
            _field_value(embed, "Knockout advancement points"),
            "Awarded if a predicted team reaches the specified round, even if it "
            "gets there by a different path.\n"
            "Ro32 +1, Ro16 +2, QF +5, SF +10, F +15",
        )
        self.assertEqual(
            _field_value(embed, "Exact placement points"),
            "Champion +25, runner-up +15, third-place +10",
        )

    def test_lock_embed_includes_public_tournament_context(self) -> None:
        admin_module = _load_admin_module_with_fake_discord()
        settings = types.SimpleNamespace(
            predictions_open=True,
            lock_deadline_utc=datetime(2026, 6, 11, 18, 0, tzinfo=timezone.utc),
        )
        tournament = types.SimpleNamespace(tournament_name="FIFA World Cup 2026")

        embed = admin_module._lock_embed(settings=settings, tournament=tournament)

        self.assertEqual(embed.title, "Prediction Lock")
        self.assertIn("Submit or edit your bracket", embed.description)
        self.assertEqual(_field_value(embed, "Tournament"), "FIFA World Cup 2026")
        self.assertEqual(_field_value(embed, "Status"), "Open")
        self.assertIn("<t:1781200800:F>", _field_value(embed, "Deadline"))
        self.assertIn("/edit", _field_value(embed, "Commands"))


class _FakeContext:
    def __init__(
        self,
        *,
        guild: object | None,
        manage_guild: bool,
        administrator: bool = False,
    ) -> None:
        self.guild = guild
        self.author = types.SimpleNamespace(
            id=123,
            guild_permissions=types.SimpleNamespace(
                administrator=administrator,
                manage_guild=manage_guild,
            ),
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
    discord.Color = _FakeColor
    discord.Embed = _FakeEmbed
    discord.File = object
    discord.Option = _FakeOption
    discord.option = _fake_option_decorator
    discord.Permissions = _FakePermissions
    discord.SlashCommandGroup = _FakeSlashCommandGroup
    discord.ApplicationContext = object
    discord.Bot = object
    discord.TextChannel = object

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


def _load_operator_module_with_fake_discord() -> types.ModuleType:
    cogs_package = importlib.import_module("world_cup_bot.cogs")
    had_operator_attr = hasattr(cogs_package, "operator")
    previous_operator_attr = getattr(cogs_package, "operator", None)
    module_names = (
        "discord",
        "discord.ext",
        "discord.ext.commands",
        "world_cup_bot.cogs.operator",
    )
    previous_modules = {name: sys.modules.get(name) for name in module_names}

    discord = types.ModuleType("discord")
    discord.Option = _FakeOption
    discord.option = _fake_option_decorator
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
    sys.modules.pop("world_cup_bot.cogs.operator", None)

    try:
        return importlib.import_module("world_cup_bot.cogs.operator")
    finally:
        for name, module in previous_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        if had_operator_attr:
            cogs_package.operator = previous_operator_attr
        elif hasattr(cogs_package, "operator"):
            del cogs_package.operator


class _FakePermissions:
    def __init__(self, *, manage_guild: bool = False) -> None:
        self.manage_guild = manage_guild


def _FakeOption(input_type: object, description: str, **kwargs: object) -> object:
    return types.SimpleNamespace(
        input_type=input_type,
        description=description,
        **kwargs,
    )


def _fake_option_decorator(
    name: str,
    input_type: object | None = None,
    **kwargs: object,
) -> object:
    def decorator(func: object) -> object:
        return func

    return decorator


class _FakeColor:
    @staticmethod
    def green() -> str:
        return "green"

    @staticmethod
    def blurple() -> str:
        return "blurple"

    @staticmethod
    def gold() -> str:
        return "gold"


class _FakeEmbed:
    def __init__(
        self,
        *,
        title: str | None = None,
        description: str | None = None,
        color: object | None = None,
    ) -> None:
        self.title = title
        self.description = description
        self.color = color
        self.fields: list[types.SimpleNamespace] = []
        self.footer: str | None = None

    def add_field(self, *, name: str, value: object, inline: bool = True) -> None:
        self.fields.append(
            types.SimpleNamespace(name=name, value=str(value), inline=inline)
        )

    def set_footer(self, *, text: str) -> None:
        self.footer = text


def _field_value(embed: _FakeEmbed, name: str) -> str:
    for field in embed.fields:
        if field.name == name:
            return field.value
    raise AssertionError(f"Missing embed field: {name}")


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
        self.guild_ids: list[int] | None = None

    def command(self, **_: object) -> object:
        def decorator(func: object) -> object:
            return func

        return decorator


class _FakeBot:
    def __init__(self, *, settings: object) -> None:
        self.settings = settings
        self.cogs: list[object] = []

    def add_cog(self, cog: object) -> None:
        self.cogs.append(cog)
