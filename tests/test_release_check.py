from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "release_check.py"

spec = importlib.util.spec_from_file_location("release_check", SCRIPT_PATH)
assert spec is not None
release_check = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(release_check)


class ReleaseCheckTests(unittest.TestCase):
    def test_parse_env_file_supports_comments_exports_and_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "\n".join(
                    [
                        "# local settings",
                        "DISCORD_TOKEN='token value'",
                        'export BOT_ENV="staging"',
                        "DATABASE_URL=postgresql://user:pass@localhost/db",
                        "IGNORED_LINE",
                        "",
                    ]
                )
            )

            values = release_check.parse_env_file(path)

        self.assertEqual(values["DISCORD_TOKEN"], "token value")
        self.assertEqual(values["BOT_ENV"], "staging")
        self.assertEqual(values["DATABASE_URL"], "postgresql://user:pass@localhost/db")
        self.assertNotIn("IGNORED_LINE", values)

    def test_parse_env_file_returns_empty_values_for_missing_file(self) -> None:
        values = release_check.parse_env_file(Path("/does/not/exist/.env"))

        self.assertEqual(values, {})


if __name__ == "__main__":
    unittest.main()
