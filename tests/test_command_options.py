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


def _command_functions(tree: ast.AST) -> list[ast.AsyncFunctionDef]:
    functions: list[ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if any(_is_command_decorator(decorator) for decorator in node.decorator_list):
            functions.append(node)
    return functions


def _is_command_decorator(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Attribute):
        return target.attr in {"slash_command", "command"}
    return False


def _command_arguments(function: ast.AsyncFunctionDef) -> list[ast.arg]:
    return [
        argument
        for argument in function.args.args
        if argument.arg not in {"self", "ctx"} and argument.annotation is not None
    ]


if __name__ == "__main__":
    unittest.main()
