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

    def test_admin_post_choices_keep_status_private(self) -> None:
        tree = ast.parse(
            (PROJECT_ROOT / "world_cup_bot" / "cogs" / "admin.py").read_text()
        )
        post_command = _function_by_name(tree, "post_command")

        choices = _option_choices(post_command, "kind")

        self.assertEqual(choices, ["leaderboard", "rules", "lock"])


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


def _is_command_decorator(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Attribute):
        return target.attr in {"slash_command", "command"}
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
