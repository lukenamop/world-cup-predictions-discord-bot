from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from world_cup_bot.services.tournament_import import (
    DEFAULT_TOURNAMENT_PATH,
    TournamentImportError,
    load_tournament_config,
)


def main() -> int:
    requested_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TOURNAMENT_PATH
    try:
        imported = load_tournament_config(requested_path)
    except TournamentImportError as exc:
        print(exc)
        return 2

    report = imported.validation
    if not report.valid:
        print(f"Invalid tournament config: {imported.path}")
        for error in report.errors:
            print(f"- {error}")
        return 1

    summary = report.summary
    if summary is None:
        print("Tournament config validated but did not produce a summary.")
        return 1

    print(f"Valid tournament config: {summary.name}")
    print(f"- tournament_id: {summary.tournament_id}")
    print(f"- schema_version: {summary.schema_version}")
    print(f"- teams: {summary.team_count}")
    print(f"- groups: {summary.group_count}")
    print(f"- fixtures: {summary.fixture_count}")
    print(f"- round_of_32_matches: {summary.opening_knockout_matches}")
    print(f"- third_place_rules: {summary.third_place_rule_count}")
    print(f"- config_hash: {imported.config_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
