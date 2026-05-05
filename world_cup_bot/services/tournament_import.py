from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from world_cup_bot.domain.tournament import TournamentValidationReport
from world_cup_bot.domain.validation import validate_tournament_config


DEFAULT_TOURNAMENT_PATH = Path("config/tournaments/2026_world_cup.json")


class TournamentImportError(RuntimeError):
    """Raised when tournament config cannot be loaded safely."""


@dataclass(frozen=True)
class TournamentImport:
    path: Path
    config: Mapping[str, Any]
    config_hash: str
    validation: TournamentValidationReport


def load_tournament_config(
    requested_path: str | Path = DEFAULT_TOURNAMENT_PATH,
    *,
    project_root: Path | None = None,
) -> TournamentImport:
    root = (project_root or Path.cwd()).resolve()
    path = _resolve_config_path(requested_path, root)

    try:
        raw_config = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise TournamentImportError(f"Tournament config not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise TournamentImportError(
            f"Tournament config is not valid JSON: {path}:{exc.lineno}:{exc.colno}"
        ) from exc

    if not isinstance(raw_config, Mapping):
        raise TournamentImportError("Tournament config root must be a JSON object")

    canonical = canonical_tournament_json(raw_config)
    return TournamentImport(
        path=path,
        config=raw_config,
        config_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        validation=validate_tournament_config(raw_config),
    )


def canonical_tournament_json(config: Mapping[str, Any]) -> str:
    return json.dumps(config, sort_keys=True, separators=(",", ":"))


def _resolve_config_path(requested_path: str | Path, project_root: Path) -> Path:
    path = Path(requested_path)
    resolved = path if path.is_absolute() else project_root / path
    resolved = resolved.resolve()
    config_root = (project_root / "config").resolve()

    if resolved != config_root and config_root not in resolved.parents:
        raise TournamentImportError("Tournament config path must be inside config/")

    return resolved
