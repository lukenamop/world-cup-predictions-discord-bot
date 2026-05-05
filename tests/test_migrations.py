from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from world_cup_bot.data.migrations import discover_migrations


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
