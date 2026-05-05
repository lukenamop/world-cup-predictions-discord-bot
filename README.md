# World Cup Bracket Predictor Bot

Discord bot foundation for server-specific World Cup prediction leagues. The current implementation covers Milestone 5 from `PRODUCT-SPEC.md`: configuration, startup logging, PostgreSQL persistence, tournament config validation/import, private prediction draft/submission flows, live result sync, scoring recalculation, leaderboards, prediction summaries, generated bracket/group images, preferences, admin posting, exports, and backups.

## Current Command Surface

- `/help` confirms the bot is online and lists the current prediction commands.
- `/predict` starts or resumes a private guided prediction draft.
- `/edit` starts a private replacement draft for an already submitted prediction before lock. The last submitted bracket remains stored until the replacement draft is submitted.
- `/prediction [user]` shows visible champion, runner-up, third-place, and point summary details.
- `/groups [user]` renders a user's submitted group prediction image with result highlighting when the viewer is the owner or the user has shared full brackets.
- `/bracket [user]` renders a user's submitted knockout bracket image with result highlighting when the viewer is the owner or the user has shared full brackets.
- `/preferences [share_full_bracket]` views or updates whether other members can see full bracket and group images.
- `/leaderboard [page]` shows paginated shared-rank league standings.
- `/rank [user]` shows a user's current shared rank and point totals after scores have been recalculated.
- `/points [user]` shows a user's group/knockout point breakdown after scores have been recalculated.
- `/rules` shows scoring and lock behavior.
- `/admin status` shows setup status for the current server, including active tournament data.
- `/admin import [path] [validate_only]` validates a tournament JSON file under `config/` and imports it for the current server when valid.
- `/admin open` opens prediction entry.
- `/admin close` closes prediction entry without changing the lock deadline.
- `/admin lock [deadline_utc] [clear]` sets or clears the full-bracket lock deadline. Use ISO-8601 UTC timestamps such as `2026-06-11T18:00:00Z`.
- `/admin sync [run]` shows the latest live result sync status, or triggers a manual sync when `run:True`.
- `/admin recalc` recalculates submitted prediction scores from stored results.
- `/admin post [kind] [channel]` posts `leaderboard`, `rules`, `lock`, or `status` snapshots to a channel.
- `/admin export` returns a JSON export of submitted predictions and current scores.
- `/admin backup` returns an operator-friendly JSON backup of league settings, active tournament config, predictions, scores, stored results, and recent sync runs.

Admin commands require Discord Manage Server permission by default. Grant role or member overrides through Discord Server Settings > Integrations > Command Permissions.

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

Group fixtures may include an optional `provider_match_id`. Knockout provider IDs live in optional `knockout_fixtures` entries with the generated match `id`, `round_name`, and `provider_match_id`. Live sync maps football-data.org match IDs to imported fixtures by `provider_match_id` first and by fixture `id` as a fallback. Store provider IDs as strings.

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

## Prediction Entry

Prediction entry is private and draft-based:

- Admins import tournament data, then run `/admin open`.
- `/predict` walks members through group ranking, predicted advancing third-place teams, and knockout winners.
- Group ranking is captured one position at a time so ordering is explicit.
- The Round of 32 is seeded automatically from group predictions, selected third-place qualifiers, and the imported allocation table.
- Drafts save after each step and can also be saved manually.
- `/edit` starts a replacement draft before lock. Existing submitted data is not replaced until the new draft is complete and submitted.
- Predictions lock at `/admin lock deadline_utc:...` when configured; otherwise the first imported fixture kickoff is used as the effective lock.

Prediction storage uses `prediction_entries` for the latest draft/submission and `prediction_history` for revision history.

## Prediction Views And Privacy

Full brackets are private by default. A member can opt in to sharing full group and bracket image views:

```text
/preferences share_full_bracket:True
```

Champion, runner-up, third-place picks, and available point totals remain visible through `/prediction [user]`. The generated `/groups` and `/bracket` images include accessible embed summaries and use explicit `OK`, `X`, and `...` status labels alongside colors so correctness does not rely on color alone.

## Results And Scoring

Live results use `LIVE_RESULTS_PROVIDER`, defaulting to `football_data_org`. For football-data.org, set `LIVE_RESULTS_API_KEY`; the bot calls the v4 competition matches endpoint for the tournament start year and stores any provider matches that map to imported fixture IDs.

Manual sync:

```text
/admin sync run:True
```

The bot also starts a 30-minute background sync loop when `LIVE_RESULTS_API_KEY` is configured. Each sync writes `match_results` and `result_sync_runs`, then recalculates scores for submitted predictions.

Manual recalculation without fetching new provider data:

```text
/admin recalc
```

Scoring uses the MVP defaults from `PRODUCT-SPEC.md`: group winner 3, group runner-up 2, third-place qualifier 1, Round of 32 1, Round of 16 2, quarter-final 5, semi-final 10, final 15, third-place winner 10, champion 25, and runner-up 15. Group winner/runner-up and best-third points are awarded only after the relevant group stage data is complete. Knockout points are team-advancement based: a user gets credit when a predicted team reaches the scored round, even if the exact path differs.

## Admin Posting, Export, And Backup

Admins can post snapshots without manually composing announcements:

```text
/admin post kind:leaderboard
/admin post kind:rules channel:#predictions
/admin post kind:lock
/admin post kind:status
```

Prediction exports and backups are returned as ephemeral JSON attachments and write audit log rows:

```text
/admin export
/admin backup
```

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
