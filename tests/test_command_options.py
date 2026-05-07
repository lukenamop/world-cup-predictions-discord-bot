from __future__ import annotations

import ast
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COG_PATHS = (
    PROJECT_ROOT / "world_cup_bot" / "cogs" / "admin.py",
    PROJECT_ROOT / "world_cup_bot" / "cogs" / "leaderboard.py",
    PROJECT_ROOT / "world_cup_bot" / "cogs" / "operator.py",
    PROJECT_ROOT / "world_cup_bot" / "cogs" / "predictions.py",
)
TYPED_OPTION_PARAMS = {
    "announcement_channel": "discord.TextChannel",
    "leaderboard_channel": "discord.TextChannel",
    "channel": "discord.TextChannel",
    "user": "discord.Member",
}


class CommandOptionMetadataTests(unittest.TestCase):
    def test_command_arguments_have_runtime_option_decorators(self) -> None:
        for path in COG_PATHS:
            with self.subTest(path=path.name):
                tree = ast.parse(path.read_text(), filename=str(path))
                for function in _command_functions(tree):
                    decorated_options = _option_decorator_names(function)
                    for argument in _command_arguments(function):
                        self.assertIn(
                            argument.arg,
                            decorated_options,
                            f"{path.name}:{function.name}.{argument.arg} needs @discord.option metadata.",
                        )

    def test_admin_setup_requires_channels_and_does_not_clear_locks(self) -> None:
        tree = ast.parse(
            (PROJECT_ROOT / "world_cup_bot" / "cogs" / "admin.py").read_text()
        )
        setup_command = _function_by_name(tree, "setup_command")
        defaults = _argument_defaults(setup_command)

        self.assertNotIn("clear_lock_deadline", defaults)
        self.assertNotIn("timezone_name", defaults)
        self.assertNotIn("lock_deadline_local", defaults)
        self.assertIsNone(defaults["announcement_channel"])
        self.assertIsNone(defaults["leaderboard_channel"])
        self.assertIsNotNone(defaults["lock_deadline_utc"])

    def test_command_arguments_use_described_discord_options(self) -> None:
        for path in COG_PATHS:
            with self.subTest(path=path.name):
                tree = ast.parse(path.read_text(), filename=str(path))
                for function in _command_functions(tree):
                    for argument in _command_arguments(function):
                        annotation = ast.unparse(argument.annotation)
                        self.assertIn(
                            "discord.Option",
                            annotation,
                            f"{path.name}:{function.name}.{argument.arg} needs a description.",
                        )

    def test_specific_discord_targets_use_specific_option_types(self) -> None:
        for path in COG_PATHS:
            tree = ast.parse(path.read_text(), filename=str(path))
            for function in _command_functions(tree):
                for argument in _command_arguments(function):
                    expected_type = TYPED_OPTION_PARAMS.get(argument.arg)
                    if expected_type is None:
                        continue

                    annotation = ast.unparse(argument.annotation)
                    self.assertIn(
                        expected_type,
                        annotation,
                        f"{path.name}:{function.name}.{argument.arg} should use {expected_type}.",
                    )

    def test_admin_post_uses_named_subcommands(self) -> None:
        tree = ast.parse(
            (PROJECT_ROOT / "world_cup_bot" / "cogs" / "admin.py").read_text()
        )
        command_names = {function.name for function in _command_functions(tree)}

        self.assertIn("post_info_command", command_names)
        self.assertIn("post_leaderboard_command", command_names)
        self.assertNotIn("post_command", command_names)

    def test_leaderboard_command_responses_are_ephemeral(self) -> None:
        tree = ast.parse(
            (PROJECT_ROOT / "world_cup_bot" / "cogs" / "leaderboard.py").read_text()
        )
        leaderboard_command = _function_by_name(tree, "leaderboard_command")
        responses = [
            node
            for node in ast.walk(leaderboard_command)
            if isinstance(node, ast.Call) and _is_ctx_respond(node.func)
        ]

        self.assertGreater(len(responses), 0)
        for response in responses:
            self.assertTrue(
                _has_true_keyword(response, "ephemeral"),
                "leaderboard_command ctx.respond calls should be ephemeral.",
            )

    def test_admin_post_leaderboard_uses_snapshot_embed(self) -> None:
        tree = ast.parse(
            (PROJECT_ROOT / "world_cup_bot" / "cogs" / "admin.py").read_text()
        )
        announcement_embeds = _async_function_by_name(tree, "_announcement_embeds")
        leaderboard_embed_calls = [
            node
            for node in ast.walk(announcement_embeds)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "leaderboard_embed"
        ]

        self.assertGreater(len(leaderboard_embed_calls), 0)
        for call in leaderboard_embed_calls:
            self.assertTrue(
                _has_true_keyword(call, "snapshot"),
                "admin leaderboard posts should use non-paginated snapshot embeds.",
            )

    def test_help_command_uses_support_contact_instead_of_command_dump(self) -> None:
        source = (PROJECT_ROOT / "world_cup_bot" / "cogs" / "foundation.py").read_text()

        self.assertNotIn('name="Commands"', source)
        self.assertNotIn("`/predict`, `/edit`", source)
        self.assertIn('name="Issues"', source)
        self.assertIn("@lukenamop", source)
        self.assertIn("lukenamop@gmail.com", source)


def _command_functions(tree: ast.AST) -> list[ast.AsyncFunctionDef]:
    functions: list[ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if any(_is_command_decorator(decorator) for decorator in node.decorator_list):
            functions.append(node)
    return functions


def _function_by_name(tree: ast.AST, name: str) -> ast.AsyncFunctionDef:
    for function in _command_functions(tree):
        if function.name == name:
            return function
    raise AssertionError(f"Missing command function: {name}")


def _async_function_by_name(tree: ast.AST, name: str) -> ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"Missing async function: {name}")


def _is_command_decorator(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Attribute):
        return target.attr in {"slash_command", "command"}
    return False


def _is_ctx_respond(node: ast.expr) -> bool:
    if not isinstance(node, ast.Attribute) or node.attr != "respond":
        return False
    value = node.value
    return isinstance(value, ast.Name) and value.id == "ctx"


def _has_true_keyword(call: ast.Call, keyword_name: str) -> bool:
    for keyword in call.keywords:
        if keyword.arg != keyword_name:
            continue
        return isinstance(keyword.value, ast.Constant) and keyword.value.value is True
    return False


def _option_decorator_names(function: ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    for decorator in function.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        if not isinstance(decorator.func, ast.Attribute):
            continue
        if decorator.func.attr != "option":
            continue
        if not decorator.args:
            continue
        name_arg = decorator.args[0]
        if isinstance(name_arg, ast.Constant) and isinstance(name_arg.value, str):
            names.add(name_arg.value)
    return names


def _option_choices(function: ast.AsyncFunctionDef, option_name: str) -> list[str]:
    for decorator in function.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        if not isinstance(decorator.func, ast.Attribute):
            continue
        if decorator.func.attr != "option":
            continue
        if not decorator.args:
            continue
        name_arg = decorator.args[0]
        if not isinstance(name_arg, ast.Constant) or name_arg.value != option_name:
            continue
        for keyword in decorator.keywords:
            if keyword.arg != "choices":
                continue
            if not isinstance(keyword.value, ast.List):
                raise AssertionError(f"{function.name}.{option_name} choices must be a list")
            return [
                element.value
                for element in keyword.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            ]
    raise AssertionError(f"Missing choices for {function.name}.{option_name}")


def _command_arguments(function: ast.AsyncFunctionDef) -> list[ast.arg]:
    return [
        argument
        for argument in function.args.args
        if argument.arg not in {"self", "ctx"} and argument.annotation is not None
    ]


def _argument_defaults(function: ast.AsyncFunctionDef) -> dict[str, ast.expr | None]:
    arguments = function.args.args
    defaults = [None] * (len(arguments) - len(function.args.defaults))
    defaults.extend(function.args.defaults)
    return {
        argument.arg: default
        for argument, default in zip(arguments, defaults)
        if argument.arg not in {"self", "ctx"}
    }


if __name__ == "__main__":
    unittest.main()
