from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from world_cup_bot.domain.validation import validate_tournament_config
from world_cup_bot.services.tournament_import import (
    TournamentImportError,
    load_tournament_config,
)


class TournamentValidationTests(unittest.TestCase):
    def test_valid_tournament_config_summarizes_importable_data(self) -> None:
        report = validate_tournament_config(_valid_config())

        self.assertTrue(report.valid, report.errors)
        self.assertIsNotNone(report.summary)
        self.assertEqual(report.summary.team_count, 6)
        self.assertEqual(report.summary.group_count, 3)
        self.assertEqual(report.summary.fixture_count, 3)
        self.assertEqual(report.summary.opening_knockout_matches, 4)
        self.assertEqual(report.summary.third_place_rule_count, 3)

    def test_validation_rejects_missing_group_fixture(self) -> None:
        config = _valid_config()
        config["fixtures"] = config["fixtures"][1:]

        report = validate_tournament_config(config)

        self.assertFalse(report.valid)
        self.assertIn(
            "fixtures missing group A matches: T-A1 vs T-A2",
            report.errors,
        )

    def test_validation_requires_complete_third_place_allocation_table(self) -> None:
        config = _valid_config()
        config["third_place_allocation"]["rules"] = config["third_place_allocation"][
            "rules"
        ][:-1]

        report = validate_tournament_config(config)

        self.assertFalse(report.valid)
        self.assertIn(
            "third_place_allocation.rules must contain 3 rules for this tournament format; found 2",
            report.errors,
        )
        self.assertTrue(
            any(
                error.startswith(
                    "third_place_allocation.rules missing qualifying group sets"
                )
                for error in report.errors
            )
        )

    def test_validation_rejects_duplicate_group_position_bracket_slot(self) -> None:
        config = _valid_config()
        config["bracket"]["round_of_32"][1]["home_source"] = {
            "type": "group_position",
            "group_id": "A",
            "position": 1,
        }

        report = validate_tournament_config(config)

        self.assertFalse(report.valid)
        self.assertIn("duplicate group-position bracket slots: A1", report.errors)
        self.assertIn("bracket.round_of_32 missing group-position slots: B1", report.errors)

    def test_loader_hashes_and_restricts_configs_to_config_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config" / "tournaments" / "test.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(json.dumps(_valid_config()))

            imported = load_tournament_config(
                "config/tournaments/test.json",
                project_root=root,
            )

            self.assertTrue(imported.validation.valid, imported.validation.errors)
            self.assertEqual(len(imported.config_hash), 64)

            with self.assertRaisesRegex(
                TournamentImportError,
                "inside config",
            ):
                load_tournament_config(root / "outside.json", project_root=root)

    def test_checked_in_2026_world_cup_config_is_importable(self) -> None:
        project_root = Path(__file__).resolve().parents[1]

        imported = load_tournament_config(
            "config/tournaments/2026_world_cup.json",
            project_root=project_root,
        )

        report = imported.validation
        self.assertTrue(report.valid, report.errors)
        self.assertIsNotNone(report.summary)
        self.assertEqual(report.summary.team_count, 48)
        self.assertEqual(report.summary.group_count, 12)
        self.assertEqual(report.summary.fixture_count, 72)
        self.assertEqual(report.summary.opening_knockout_matches, 16)
        self.assertEqual(report.summary.third_place_rule_count, 495)
        self.assertEqual(len(imported.config.get("knockout_fixtures", [])), 32)
        self.assertEqual(
            [match["id"] for match in imported.config["bracket"]["round_of_32"]],
            [
                "M074",
                "M077",
                "M073",
                "M075",
                "M083",
                "M084",
                "M081",
                "M082",
                "M076",
                "M078",
                "M079",
                "M080",
                "M086",
                "M088",
                "M085",
                "M087",
            ],
        )
        provider_ids = {
            fixture["provider_match_id"]
            for fixture in imported.config["fixtures"]
            + imported.config["knockout_fixtures"]
        }
        self.assertEqual(len(provider_ids), 104)

        tournament = imported.config["tournament"]
        self.assertIsInstance(tournament, dict)
        source_metadata = tournament.get("source_metadata")
        self.assertIsInstance(source_metadata, dict)
        for key in (
            "tournament_data",
            "bracket_template",
            "third_place_allocation",
            "provider_match_ids",
            "flag_assets",
        ):
            self.assertIn(key, source_metadata)

        for team in imported.config["teams"]:
            self.assertIsInstance(team, dict)
            flag_asset = team.get("flag_asset")
            self.assertIsInstance(flag_asset, str)
            self.assertTrue((project_root / flag_asset).is_file(), flag_asset)
        self.assertTrue((project_root / "assets" / "flags" / "SOURCE.md").is_file())
        self.assertTrue(
            (project_root / "assets" / "flags" / "LICENSE.flag-icons").is_file()
        )


def _valid_config() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "tournament": {
            "id": "test-cup",
            "name": "Test Cup",
        },
        "format": {
            "group_count": 3,
            "teams_per_group": 2,
            "third_place_qualifiers": 2,
            "opening_knockout_matches": 4,
        },
        "teams": [
            {"id": "T-A1", "name": "Team A1", "country_code": "AA"},
            {"id": "T-A2", "name": "Team A2", "country_code": "AB"},
            {"id": "T-B1", "name": "Team B1", "country_code": "BA"},
            {"id": "T-B2", "name": "Team B2", "country_code": "BB"},
            {"id": "T-C1", "name": "Team C1", "country_code": "CA"},
            {"id": "T-C2", "name": "Team C2", "country_code": "CB"},
        ],
        "groups": [
            {"id": "A", "label": "Group A", "team_ids": ["T-A1", "T-A2"]},
            {"id": "B", "label": "Group B", "team_ids": ["T-B1", "T-B2"]},
            {"id": "C", "label": "Group C", "team_ids": ["T-C1", "T-C2"]},
        ],
        "fixtures": [
            {
                "id": "A-1",
                "stage": "group",
                "group_id": "A",
                "home_team_id": "T-A1",
                "away_team_id": "T-A2",
                "kickoff_utc": "2026-06-11T00:00:00Z",
            },
            {
                "id": "B-1",
                "stage": "group",
                "group_id": "B",
                "home_team_id": "T-B1",
                "away_team_id": "T-B2",
                "kickoff_utc": "2026-06-12T00:00:00+00:00",
            },
            {
                "id": "C-1",
                "stage": "group",
                "group_id": "C",
                "home_team_id": "T-C1",
                "away_team_id": "T-C2",
                "kickoff_utc": "2026-06-13T00:00:00Z",
            },
        ],
        "bracket": {
            "round_of_32": [
                {
                    "id": "R32-1",
                    "home_source": {
                        "type": "group_position",
                        "group_id": "A",
                        "position": 1,
                    },
                    "away_source": {
                        "type": "third_place_slot",
                        "slot_id": "TP-1",
                    },
                },
                {
                    "id": "R32-2",
                    "home_source": {
                        "type": "group_position",
                        "group_id": "B",
                        "position": 1,
                    },
                    "away_source": {
                        "type": "third_place_slot",
                        "slot_id": "TP-2",
                    },
                },
                {
                    "id": "R32-3",
                    "home_source": {
                        "type": "group_position",
                        "group_id": "C",
                        "position": 1,
                    },
                    "away_source": {
                        "type": "group_position",
                        "group_id": "A",
                        "position": 2,
                    },
                },
                {
                    "id": "R32-4",
                    "home_source": {
                        "type": "group_position",
                        "group_id": "B",
                        "position": 2,
                    },
                    "away_source": {
                        "type": "group_position",
                        "group_id": "C",
                        "position": 2,
                    },
                },
            ]
        },
        "third_place_allocation": {
            "source": "Test allocation table",
            "source_version": "test-v1",
            "rules": [
                {
                    "qualifying_groups": ["A", "B"],
                    "slot_assignments": {"TP-1": "A", "TP-2": "B"},
                },
                {
                    "qualifying_groups": ["A", "C"],
                    "slot_assignments": {"TP-1": "A", "TP-2": "C"},
                },
                {
                    "qualifying_groups": ["B", "C"],
                    "slot_assignments": {"TP-1": "B", "TP-2": "C"},
                },
            ],
        },
    }
