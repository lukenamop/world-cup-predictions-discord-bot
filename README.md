# World Cup Bracket Predictor Bot

Discord bot foundation for server-specific World Cup prediction leagues. The current implementation covers Milestone 7 from `PRODUCT-SPEC.md`: configuration, startup logging, PostgreSQL persistence, tournament config validation/import, private prediction submission flows, live result sync, scoring recalculation, leaderboards, prediction summaries, generated bracket/group images, preferences, guided admin setup/configuration, admin posting, exports, and backups.

## Current Command Surface

- `/help` confirms the bot is online and lists the current prediction commands.
- `/predict` starts a private guided prediction session and submits the bracket when completed.
- `/edit` starts a private edit session for an already submitted prediction before lock. The last submitted bracket remains active until the edit session is completed and submitted.
- `/prediction [user]` shows visible champion, runner-up, third-place, and point summary details.
- `/groups [user]` renders a user's submitted group prediction image with result highlighting when the viewer is the owner or the user has shared full brackets.
- `/bracket [user]` renders a user's submitted knockout bracket image with result highlighting when the viewer is the owner or the user has shared full brackets.
- `/preferences [share_full_bracket]` views or updates whether other members can see full bracket and group images.
- `/leaderboard [page]` shows paginated shared-rank league standings.
- `/rank [user]` shows a user's current shared rank and point totals after scores have been recalculated.
- `/points [user]` shows a user's group/knockout point breakdown after scores have been recalculated.
- `/rules` shows scoring and lock behavior.
- `/admin setup [announcement_channel] [leaderboard_channel] [timezone_name] [share_full_bracket_default] [lock_deadline_local] [clear_lock_deadline]` configures the server's prediction announcement channel, leaderboard channel, timezone, privacy default, default scoring, and lock deadline.
- `/admin config [...]` views or updates configured channels, timezone, privacy default, lock deadline, and scoring values after setup.
- `/admin status` shows setup status for the current server, including active tournament data.
- `/admin import [path] [validate_only]` validates a tournament JSON file under `config/` and imports it for the current server when valid.
- `/admin open` opens prediction entry.
- `/admin close` closes prediction entry without changing the lock deadline.
- `/admin lock [deadline_utc] [clear]` sets or clears the full-bracket lock deadline. Use ISO-8601 UTC timestamps such as `2026-06-11T18:00:00Z`.
- `/admin sync [run]` shows the latest live result sync status, or triggers a manual sync when `run:True`.
- `/admin recalc` recalculates submitted prediction scores from stored results.
- `/admin post [kind] [channel]` posts `leaderboard`, `rules`, `lock`, or `status` snapshots to configured channels by default, or to an explicit override channel.
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
- `LIVE_RESULTS_PROVIDER`, default `fifa_public_calendar`

`LIVE_RESULTS_PROVIDER` is operator-level configuration. Individual Discord servers cannot select a live provider yet.

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

Group fixtures may include an optional `provider_match_id`. Knockout provider IDs live in optional `knockout_fixtures` entries with the generated match `id`, `round_name`, and `provider_match_id`. Live sync maps FIFA calendar `IdMatch` values to imported fixtures by `provider_match_id` first and by fixture `id` as a fallback. Store provider IDs as strings.

For the 2026 default format, the third-place allocation table must contain all 495 combinations of 8 qualifying groups from 12 groups. The checked-in `config/tournaments/2026_world_cup.json` is importable launch data with 48 teams, 12 groups, 72 group fixtures, a bracket-ordered Round of 32 template, 32 knockout fixture mappings, and the full allocation table. Source/version metadata for FIFA schedule data, bracket rules, third-place allocation, provider match IDs, and flag assets lives under `tournament.source_metadata`.

The checked-in `provider_match_id` values come from FIFA's public calendar `IdMatch` values. Committed SVG flags are stored in `assets/flags/`; source, license, and FIFA-code mapping notes are in `assets/flags/SOURCE.md`.

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

Prediction entry is private and submit-based:

- Admins run `/admin setup`, import tournament data, then run `/admin open`.
- `/predict` walks members through group ranking, predicted advancing third-place teams, and knockout winners.
- Group ranking is captured one position at a time so ordering is explicit.
- The Round of 32 is seeded automatically from group predictions, selected third-place qualifiers, and the imported allocation table.
- Completing `/predict` submits the bracket. There is no supported user-facing saved draft workflow.
- `/edit` starts a replacement flow before lock. Existing submitted data is not replaced until the edit flow is complete and submitted.
- Predictions lock at `/admin lock deadline_utc:...` when configured; otherwise the first imported fixture kickoff is used as the effective lock.

Prediction storage keeps the latest submitted prediction and `prediction_history` revision rows. Any temporary in-progress state should be treated as an implementation detail, not a user-facing draft feature.

## Prediction Views And Privacy

Full brackets are private by default. A member can opt in to sharing full group and bracket image views:

```text
/preferences share_full_bracket:True
```

Champion, runner-up, third-place picks, and available point totals remain visible through `/prediction [user]`. The generated `/groups` and `/bracket` images include accessible embed summaries and use explicit `OK`, `X`, and `...` status labels alongside colors so correctness does not rely on color alone.

## Results And Scoring

Live results use `LIVE_RESULTS_PROVIDER`, defaulting to `fifa_public_calendar`. The FIFA provider calls the public calendar matches endpoint for the imported tournament date window and stores any provider matches that map to imported fixture IDs.

Manual sync:

```text
/admin sync run:True
```

The bot also starts a 30-minute background sync loop after startup. Each sync writes `match_results` and `result_sync_runs`, then recalculates scores for submitted predictions.

Manual recalculation without fetching new provider data:

```text
/admin recalc
```

Scoring uses the MVP defaults from `PRODUCT-SPEC.md`: group winner 3, group runner-up 2, third-place qualifier 1, Round of 32 1, Round of 16 2, quarter-final 5, semi-final 10, final 15, third-place winner 10, champion 25, and runner-up 15. Admins can adjust those values with `/admin config`. Group winner/runner-up and best-third points are awarded only after the relevant group stage data is complete. Knockout points are team-advancement based: a user gets credit when a predicted team reaches the scored round, even if the exact path differs.

## Admin Setup And Configuration

Run setup from the server where the league will live:

```text
/admin setup announcement_channel:#world-cup leaderboard_channel:#leaderboard timezone_name:America/New_York lock_deadline_local:2026-06-11 12:00
```

The prediction announcement channel is used for public league notices such as rules, lock reminders, prediction open/closed status, and status snapshots. Private prediction entry still happens through ephemeral `/predict` and `/edit` flows.

Timezone values must be IANA names such as `America/New_York`, `America/Chicago`, `America/Denver`, `America/Los_Angeles`, or `UTC`. `lock_deadline_local` is interpreted in the configured server timezone and stored in UTC.

Use `/admin config` with no options to view current settings. Pass only the options you want to change, for example:

```text
/admin config timezone_name:America/Chicago
/admin config share_full_bracket_default:False
/admin config champion:30 runner_up:20
```

## Admin Posting, Export, And Backup

Admins can post snapshots without manually composing announcements. Leaderboards default to the configured leaderboard channel; rules, lock, and status posts default to the configured prediction announcement channel. Pass `channel:` to override the default destination.

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
python -m unittest discover tests
```
