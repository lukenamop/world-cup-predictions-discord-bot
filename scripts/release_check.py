from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_TOURNAMENT_FILE = PROJECT_ROOT / "config" / "tournaments" / "2026_world_cup.json"


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def format_command(command: Iterable[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def run_step(name: str, command: list[str], *, env: dict[str, str]) -> bool:
    print(f"\n==> {name}", flush=True)
    print(format_command(command), flush=True)
    completed = subprocess.run(command, cwd=PROJECT_ROOT, env=env, check=False)
    if completed.returncode == 0:
        print(f"PASS: {name}")
        return True

    print(f"FAIL: {name} exited with {completed.returncode}")
    return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the repeatable local checks for Milestone 11 release readiness."
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help="Environment file to load for subprocesses; defaults to .env.",
    )
    parser.add_argument(
        "--skip-env-file",
        action="store_true",
        help="Do not load an env file; use only the current process environment.",
    )
    parser.add_argument(
        "--skip-db",
        action="store_true",
        help="Skip scripts/healthcheck.py for machines without local PostgreSQL.",
    )
    parser.add_argument(
        "--tournament-file",
        type=Path,
        default=DEFAULT_TOURNAMENT_FILE,
        help="Tournament JSON to validate.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env = os.environ.copy()
    if not args.skip_env_file:
        env.update(parse_env_file(args.env_file))

    python = sys.executable
    steps: list[tuple[str, list[str]]] = [
        ("Unit test suite", [python, "-m", "unittest", "discover", "tests"]),
        (
            "Tournament validation",
            [python, "scripts/validate_tournament.py", str(args.tournament_file)],
        ),
    ]
    if not args.skip_db:
        steps.append(
            ("Database health and migration smoke", [python, "scripts/healthcheck.py"])
        )

    results = [run_step(name, command, env=env) for name, command in steps]
    if all(results):
        print("\nRelease checks passed.")
        return 0

    print("\nRelease checks failed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
