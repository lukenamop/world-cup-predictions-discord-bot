# World Cup Bracket Predictor Bot

Discord bot foundation for server-specific World Cup prediction leagues. The current implementation covers Milestone 2 from `PRODUCT-SPEC.md`: configuration, startup logging, PostgreSQL persistence, tournament config validation/import, admin setup status, migrations, and basic bot health visibility.

## Current Command Surface

- `/help` confirms the bot is online and points users to the upcoming prediction flow.
- `/admin status` shows setup status for the current server, including active tournament data.
- `/admin import [path] [validate_only]` validates a tournament JSON file under `config/` and imports it for the current server when valid.

Admin commands require Discord Manage Server permission by default. Grant role or member overrides through Discord Server Settings > Integrations > Command Permissions.

Prediction entry, scoring, leaderboards, generated visuals, and richer admin workflows are intentionally left for later milestones.

## Ubuntu PostgreSQL Setup

Install PostgreSQL and create the local database/user:

```bash
sudo apt update
sudo apt install postgresql postgresql-contrib
sudo -u postgres createuser --createdb world_cup_bot
sudo -u postgres createdb --owner=world_cup_bot world_cup_bot
sudo -u postgres psql -c "ALTER USER world_cup_bot WITH PASSWORD 'change-me-local-only';"
```

Use this local connection string:

```bash
postgresql://world_cup_bot:change-me-local-only@localhost:5432/world_cup_bot
```

## Local Setup

Use Python 3.12:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Fill in `.env` locally. Never commit real tokens or provider credentials. Load it
in each shell before running commands that need bot configuration:

```bash
set -a
. .env
set +a
```

Required environment variables:

- `DISCORD_TOKEN`
- `DATABASE_URL`

Optional environment variables:

- `BOT_ENV`, default `development`
- `LOG_LEVEL`, default `INFO`
- `OWNER_USER_IDS`, comma-separated Discord IDs
- `DEFAULT_TIMEZONE`, default `America/Indiana/Indianapolis`
- `LIVE_RESULTS_PROVIDER`, default `football_data_org`
- `LIVE_RESULTS_API_KEY`, only when a provider requires it

## Running The Bot

```bash
. .venv/bin/activate
set -a
. .env
set +a
python -m world_cup_bot.bot
```

At startup the bot:

- validates required configuration;
- configures structured console logging;
- connects to PostgreSQL;
- applies numbered SQL migrations in `world_cup_bot/data/migrations`;
- records startup/ready health rows;
- logs environment, masked database target, guild count, and slash command sync status.

## Health Check

Run the operator health check from the same environment:

```bash
. .venv/bin/activate
set -a
. .env
set +a
python scripts/healthcheck.py
```

The health check validates configuration, connects to PostgreSQL, applies any pending migrations, and runs a simple database query.

## Tournament Data

Tournament config files are JSON documents under `config/`. The schema is documented in `config/tournament.schema.json`, and runtime validation lives in `world_cup_bot/domain/validation.py`.

Milestone 2 validation checks that a config has:

- tournament identity and schema version;
- the expected number of teams and groups for the configured format;
- complete group membership with every team assigned exactly once;
- complete group-stage fixtures for every pair of teams in each group;
- a Round of 32 bracket template with group-position and third-place slots;
- a third-place allocation table covering every possible qualifying third-place group set.

For the 2026 default format, the third-place allocation table must contain all 495 combinations of 8 qualifying groups from 12 groups. The checked-in `config/tournaments/2026_world_cup.json` is an explicit placeholder and will fail validation until official teams, fixtures, bracket slots, and the local official allocation table are filled in.

Validate a tournament file locally:

```bash
. .venv/bin/activate
python scripts/validate_tournament.py config/tournaments/2026_world_cup.json
```

Admins can validate or import the same file from Discord:

```text
/admin import path:config/tournaments/2026_world_cup.json validate_only:True
/admin import path:config/tournaments/2026_world_cup.json validate_only:False
```

Successful imports are stored in PostgreSQL as immutable config snapshots and marked active for that Discord server. Imports also write an audit log row.

## PM2

Install PM2 on the host and start the production process:

```bash
set -a
. .env
set +a
pm2 start ecosystem.config.js --env production
pm2 logs world-cup-bot
pm2 status world-cup-bot
```

The PM2 config runs `.venv/bin/python -m world_cup_bot.bot` directly and uses a restart delay to avoid tight crash loops.

## Migrations

Migrations are plain numbered SQL files in `world_cup_bot/data/migrations`. They run automatically at bot startup and in `scripts/healthcheck.py`. Applied migration names are tracked in `schema_migrations`.

## Tests

```bash
. .venv/bin/activate
pytest
```
