# Agents Guide

This document is the source of truth for Codex-relevant implementation rules, technical defaults, repository structure, testing, and documentation expectations. Product behavior, command semantics, scoring, privacy, and visual design live in `PRODUCT-SPEC.md`; read it before product or architecture changes.

## Collaboration

- Preserve user-created changes in the working tree.
- Keep changes small and explain meaningful tradeoffs.
- Prefer the next useful milestone slice over speculative infrastructure.
- Ask for clarification when a decision affects scoring, privacy, lock behavior, or user-visible workflows.
- Keep docs updated when commands, setup, environment variables, schemas, deployment, scoring, or admin workflows change.

## Technical Defaults

- Python 3.12.
- Pycord.
- PostgreSQL with `asyncpg`.
- PM2 process supervision.
- `pyproject.toml` for metadata, dependencies, and tool config.
- Standard `python -m venv` and `pip`; do not require `uv`.
- Standard-library `unittest` for domain and service logic.
- Pillow for the first generated image renderer.

## Repository Shape

Prefer this structure unless implementation pressure justifies a small adjustment:

```text
world_cup_bot/
  bot.py
  settings.py
  logging.py
  cogs/
    admin.py
    predictions.py
    leaderboard.py
    rules.py
  domain/
    bracket.py
    scoring.py
    standings.py
    validation.py
    locks.py
  data/
    database.py
    repositories.py
    migrations.py
  services/
    tournament_import.py
    prediction_service.py
    result_sync_service.py
    leaderboard_service.py
    live_results_client.py
    visual_render_service.py
  ui/
    views.py
    embeds.py
    image_renderer.py
    pagination.py
  jobs/
    reminders.py
assets/
  flags/
config/
tests/
scripts/
pyproject.toml
PRODUCT-SPEC.md
AGENTS.md
README.md
.env.example
ecosystem.config.js
```

## Engineering Rules

- Keep Pycord cogs thin.
- Put bracket, scoring, standings, lock, and validation logic in plain Python modules.
- Make domain code usable from tests without Discord objects.
- Keep persistence behind repositories or service boundaries.
- Use structured config for tournament data and scoring rules.
- Avoid hardcoding team names, group labels, match IDs, dates, or bracket slot mappings in command handlers.
- Only add helpers when they simplify nearby code, reduce meaningful duplication, or add real efficiency.
- Prefer boring, reliable code over clever abstractions.

## Runtime And Configuration

- Keep a production-ready `ecosystem.config.js`.
- PM2 should run the virtualenv Python directly, e.g. `.venv/bin/python -m world_cup_bot.bot`.
- Load secrets from environment variables, never committed files.
- Startup should fail clearly when `DISCORD_TOKEN` or required database configuration is missing.
- Startup should log environment name, database target, guild count if known, and slash command sync status.
- Use one production/global slash command sync path for MVP; do not add dev-guild-specific sync yet.
- Do not require shell-specific startup behavior inside the Python app.
- Avoid tight restart loops; let PM2 handle restarts while logging startup failures clearly.

Environment variables:

- `DISCORD_TOKEN`
- `DATABASE_URL`
- `BOT_ENV`
- `LOG_LEVEL`
- `OWNER_USER_IDS`
- `OPERATOR_GUILD_ID`
- `DEFAULT_TIMEZONE`
- `LIVE_RESULTS_PROVIDER` defaulting to `fifa_public_calendar`
- Provider-specific credentials only when required

Only add Discord application values beyond `DISCORD_TOKEN` when implementation actually requires them.

## PostgreSQL And Migrations

- Do not add Docker or Docker Compose as the local PostgreSQL setup path.
- Document manual Ubuntu PostgreSQL setup first.
- Use `world_cup_bot` as the boring default local database and database user name in examples.
- Use simple numbered SQL migrations, tracked by a lightweight migration table or another explicit repeatable path.
- Do not commit database dumps, local data exports, credentials, or production databases.
- Store Discord IDs as strings.
- Use stable IDs for tournaments, groups, teams, matches, and bracket slots.
- Store timestamps in UTC; convert to guild-configured timezones only for display.
- Preserve enough prediction history to audit submitted picks and score changes.

## Tournament And Result Implementation

- Load tournament data, bracket templates, and the official third-place allocation table from local versioned config.
- The third-place allocation config should cover all 495 possible sets of eight qualifying third-place groups and include source/version metadata.
- Do not fetch bracket allocation rules from the internet at runtime.
- Use a provider adapter for the product-selected live results provider so providers can be replaced or supplemented without changing scoring or command logic.
- Result sync should cache provider responses, store last successful sync state, respect rate limits, and use retry/backoff.
- If provider data exceeds the product delay allowance, log a single warning per delayed match or sync window.
- Treat live result ingestion, recalculation, and operator sync workflows as idempotent.
- Audit log sync runs and admin actions that mutate results, locks, scoring, tournament config, exports, or backups.

## Visual Implementation

- Render generated images from domain/service output; visual code must not decide correctness.
- Use bundled or cached flag assets from `assets/flags/`; committed flag assets must be royalty-free or self-created with source/license notes.
- Do not make generated images the only accessible output; include concise text or embed summaries beside them.
- Regenerate visuals from current data on demand, or cache them only with clear invalidation when predictions, results, or scoring versions change.

## Testing

Before finishing meaningful code changes, run relevant tests. Prefer
`python -m unittest discover tests` for the full local suite, or targeted
`python -m unittest tests.test_module` commands for focused verification.
Minimum expected coverage as features appear:

- Tournament import validation.
- Bracket validation.
- Third-place allocation table validation.
- Group standings and tie-breakers.
- Best third-place qualification.
- Lock deadline behavior.
- Scoring calculations.
- Leaderboard ordering.
- Result sync and recalculation idempotency.
- Generated visual status mapping.

If tests cannot be run, say why in the final response.

## Documentation

Docs should be direct and operational. Avoid large speculative sections unless they guide near-term implementation.

Update docs when adding or changing:

- Slash commands.
- Environment variables.
- PM2 commands.
- PostgreSQL setup or migrations.
- Tournament data schema.
- Scoring config.
- Admin workflows.
- Deployment steps.
- Flag asset source/license notes.

## Security And Privacy

- Never commit Discord tokens, `.env` files with secrets, production databases, PM2 dump files, or provider credentials.
- Do not log tokens, raw environment dumps, or sensitive provider secrets.
- Rely on Discord command permissions for `/admin` access as described in `PRODUCT-SPEC.md`.

## Build Order

Follow the MVP milestone order in `PRODUCT-SPEC.md`.
