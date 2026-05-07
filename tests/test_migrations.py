from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from world_cup_bot.data.migrations import discover_migrations


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MigrationDiscoveryTests(unittest.TestCase):
    def test_discover_migrations_returns_sorted_numbered_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            (tmp_path / "002_second.sql").write_text("select 2;")
            (tmp_path / "001_first.sql").write_text("select 1;")

            migrations = discover_migrations(tmp_path)

        self.assertEqual(
            [migration.name for migration in migrations],
            ["001_first.sql", "002_second.sql"],
        )

    def test_discover_migrations_rejects_unversioned_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            (tmp_path / "foundation.sql").write_text("select 1;")

            with self.assertRaisesRegex(ValueError, "001_descriptive_name.sql"):
                discover_migrations(tmp_path)

    def test_discover_migrations_rejects_duplicate_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            (tmp_path / "001_first.sql").write_text("select 1;")
            (tmp_path / "001_second.sql").write_text("select 2;")

            with self.assertRaisesRegex(ValueError, "Duplicate migration number: 001"):
                discover_migrations(tmp_path)

    def test_repo_migrations_include_knockout_id_data_migration(self) -> None:
        migrations_path = PROJECT_ROOT / "world_cup_bot" / "data" / "migrations"
        migrations = discover_migrations(migrations_path)
        migration = next(
            migration
            for migration in migrations
            if migration.name == "010_normalize_generated_knockout_ids.sql"
        )

        self.assertIn("update prediction_entries", migration.sql)
        self.assertIn("update prediction_history", migration.sql)
        self.assertIn("'round_of_16'", migration.sql)
        self.assertIn("'quarter_finals'", migration.sql)
        self.assertIn("'semi_finals'", migration.sql)
        self.assertIn("prefix || '-2'", migration.sql)
