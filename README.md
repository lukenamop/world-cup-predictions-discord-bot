# World Cup Bracket Predictor Bot

A Discord bot for running server-specific 2026 World Cup prediction leagues. Members submit private bracket predictions, admins manage league setup and announcements, official results sync into PostgreSQL, and the bot keeps scores, leaderboards, and generated bracket/group images current.

This README is organized by audience:

1. Server admins setting up and running a tournament on an already-running bot.
2. Server members making predictions and using the general commands.
3. Bot operators deploying, monitoring, testing, and maintaining their own bot instance.

## Server Admins

This section assumes the bot is already running, has been invited to your server, and has synced slash commands. Admin commands require Discord Manage Server permission by default. You can grant role or member overrides through Discord Server Settings > Integrations > Command Permissions.

### Set Up A Tournament

Choose two text channels before setup:

- A prediction announcement channel for public league info, rules, lock deadline, reminders, and open/closed status.
- A leaderboard channel for posted leaderboard snapshots.

Then run setup from the Discord server where the league will live:

```text
/admin setup announcement_channel:#world-cup leaderboard_channel:#leaderboard lock_deadline_utc:2026-06-11T18:00:00Z
```

`/admin setup` attaches the checked-in canonical 2026 World Cup tournament data automatically. Guild admins cannot import alternate tournament JSON files in the MVP. The live results provider is configured by the bot operator, not per server.

`lock_deadline_utc` is optional, but when you provide it, use an ISO-8601 UTC timestamp such as `2026-06-11T18:00:00Z`. If you do not set a lock deadline, predictions auto-lock at the first imported fixture kickoff.

After setup:

```text
/admin status
/admin config
/admin open
/admin post info
```

`/admin status` confirms channels, tournament attachment, prediction status, lock behavior, and command sync status. `/admin config` with no options shows current settings. `/admin open` starts prediction entry, and `/admin post info` posts the public rules/setup message to the configured announcement channel.

### Change Settings

Pass only the settings you want to change:

```text
/admin config lock_deadline_utc:2026-06-11T18:00:00Z
/admin config champion:30 runner_up:20
/admin config announcement_channel:#predictions leaderboard_channel:#leaderboard
```

Clear a configured deadline with either command:

```text
/admin config clear_lock_deadline:True
/admin lock clear:True
```

Use `/admin lock deadline_utc:...` when you only need to set or update the full-bracket lock deadline without changing other settings.

### Run The League

- `/admin open` opens prediction entry for members.
- `/admin close` closes prediction entry without changing the lock deadline.
- `/admin lock [deadline_utc] [clear]` sets or clears the full-bracket lock deadline.
- `/admin recalc` recalculates submitted prediction scores from stored results.
- `/admin post info [channel]` posts league info and rules to the configured announcement channel by default, or to an explicit override channel.
- `/admin post leaderboard [channel]` posts a leaderboard snapshot to the configured leaderboard channel by default, or to an explicit override channel.
- `/admin export` returns a JSON export of submitted predictions and current scores.
- `/admin backup` returns an operator-friendly JSON backup of league settings, active tournament config, predictions, scores, stored results, and recent sync runs.

Examples:

```text
/admin post info channel:#predictions
/admin post leaderboard
/admin export
/admin backup
```

Exports and backups are returned as ephemeral JSON attachments and write audit log rows.

### Scoring And Results

Scoring uses the MVP defaults from `PRODUCT-SPEC.md`: group winner +3, group runner-up +2, advancing third-place team +1, Round of 32 +1, Round of 16 +2, quarter-final +5, semi-final +10, final +15, champion +25, runner-up +15, and third-place +10. Admins can adjust those values with `/admin config`.

Group winner/runner-up and best-third points are awarded only after the relevant group-stage data is complete. Knockout advancement points are team-based: a user gets credit when a predicted team reaches the scored round, even if the exact path differs from their prediction.

Live results are normally synced by the bot process and operator tools. Admins do not manually enter official results in the MVP, but they can run `/admin recalc` to recompute scores from already stored results.

## Server Members

Use these commands in the server where the prediction league is running. Prediction entry and edits are private Discord flows; other members do not see your step-by-step selections while you are filling out a bracket.

### Make Or Edit Predictions

- `/predict` starts a private guided prediction session and submits the bracket when completed.
- `/edit` starts a private edit session for an already submitted prediction before lock.
- `/rules` shows scoring and lock behavior.
- `/help` confirms the bot is online and shows developer/operator contact info for issues.

The prediction flow asks you to rank each group, choose the 8 third-place teams you think will advance, and then pick knockout winners round by round. The bot seeds the Round of 32 automatically from your group predictions and the official third-place allocation table.

Completing `/predict` submits the bracket. There is no user-facing saved draft workflow. If you want to fully restart an in-progress session, cancel it and run `/predict` again. If you already submitted, `/edit` opens a replacement flow before lock; your old bracket remains active until the edit is completed and submitted.

All predictions use a full-bracket lock. Group picks, third-place qualifier picks, and knockout picks lock together at the configured deadline, or at the first tournament kickoff if no custom deadline is configured.

### View Picks And Standings

- `/prediction [user]` shows visible champion, runner-up, third-place, and point summary details.
- `/groups [user]` renders a user's submitted group prediction image with result highlighting.
- `/bracket [user]` renders a user's submitted knockout bracket image with result highlighting.
- `/leaderboard [page]` shows paginated shared-rank league standings.
- `/rank [user]` shows a user's current shared rank and point totals after scores have been recalculated.
- `/points [user]` shows a user's group/knockout point breakdown after scores have been recalculated.

Champion, runner-up, third-place picks, and available point totals are visible through `/prediction [user]`. The generated `/groups [user]` and `/bracket [user]` images include concise embed summaries, use checked-in flag assets, and show point/missed badges alongside colors so correctness does not rely on color alone.

Leaderboard ties use shared rank. Users with the same point total receive the same rank, and the next rank skips ahead by the number of tied users.

## Bot Operators

This section is for people deploying or maintaining their own bot instance. Operators own the Discord application, environment variables, database, process supervision, live result provider, migrations, release checks, and operator-only commands.

The current implementation is through Milestone 10 in `PRODUCT-SPEC.md`. Milestone 11 release readiness is tracked with the local release check and staging checklist below.

### Ubuntu PostgreSQL Setup

Install PostgreSQL and create the local database/user:

```bash
sudo apt update
sudo apt install postgresql postgresql-contrib libcairo2
sudo -u postgres createuser --createdb world_cup_bot
sudo -u postgres createdb --owner=world_cup_bot world_cup_bot
sudo -u postgres psql -c "ALTER USER world_cup_bot WITH PASSWORD 'change-me-local-only';"
```

`libcairo2` supports CairoSVG rasterization of checked-in SVG flag assets for generated prediction images. On macOS, install the equivalent native Cairo library with `brew install cairo` before running image-rendering tests locally.

Use this local connection string:

```bash
postgresql://world_cup_bot:change-me-local-only@localhost:5432/world_cup_bot
```

### Local Setup

Use Python 3.12:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Fill in `.env` locally. Never commit real tokens or provider credentials. Load it in each shell before running commands that need bot configuration:

```bash
set -a
. .env
set +a
```

Required environment variables:

- `DISCORD_TOKEN`
- `DATABASE_URL`
- `USER_AGENT`, sent with external provider API requests; include a bot name/version and operator contact, for example `world-cup-predictions-discord-bot/1.0 (contact: you@example.com)`

Optional environment variables:

- `BOT_ENV`, default `development`
- `LOG_LEVEL`, default `INFO`
- `OWNER_USER_IDS`, comma-separated Discord IDs
- `OPERATOR_GUILD_ID`, Discord guild ID where `/operator` commands are registered
- `DEFAULT_TIMEZONE`, default `America/Indiana/Indianapolis`
- `LIVE_RESULTS_PROVIDER`, default `fifa_public_calendar`

`LIVE_RESULTS_PROVIDER` is operator-level configuration. Individual Discord servers cannot select a live provider. `OPERATOR_GUILD_ID` is optional for normal background sync, but must be set to use `/operator` commands.

### Running The Bot

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

### Health Check

Run the operator health check from the same environment:

```bash
. .venv/bin/activate
set -a
. .env
set +a
python scripts/healthcheck.py
```

The health check validates configuration, connects to PostgreSQL, applies any pending migrations, and runs a simple database query.

### PM2

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

### Operator Discord Commands

Operator commands are registered only in `OPERATOR_GUILD_ID`. Invocation requires Discord Administrator permission in that guild or a user ID listed in `OWNER_USER_IDS`.

- `/operator sync` runs one global live-result sync for all configured guilds.
- `/operator seed-sample` seeds deterministic official-looking sample results through the group stage, Round of 32, and Round of 16 for every active tournament config.
- `/operator seed-predictions guild_id:123456789012345678` creates or replaces three randomized, valid fake prediction entries in the supplied guild ID.
- `/operator sample-bracket guild_id:123456789012345678 predictor:1` renders one seeded sample predictor's knockout bracket image.
- `/operator reset-tournament` shows an irreversible confirmation flow that deletes predictions, prediction history, scores, stored results, sync runs, provider caches, provider warnings, and tie-breaker adjudications for active tournament configs while preserving guild setup and tournament attachments.
- `/operator resolve` records an audited official adjudication for a group or best-third-place tie that cannot be resolved from available match-result criteria.

Examples:

```text
/operator sync
/operator seed-sample
/operator seed-predictions guild_id:123456789012345678
/operator sample-bracket guild_id:123456789012345678 predictor:1
/operator reset-tournament
/operator resolve scope:group group_id:A ordered_team_ids:MEX,RSA,KOR reason:Official FIFA ranking fallback
/operator resolve scope:best_third ordered_team_ids:A3,C3,F3 reason:Official FIFA adjudication
```

Tie-breaker adjudications are stored by tournament ID and config hash, then audited with actor, scope, tied teams, chosen order, criterion, reason, and previous value.

### Tournament Data

Tournament config files are JSON documents under `config/`. The schema is documented in `config/tournament.schema.json`, and runtime validation lives in `world_cup_bot/domain/validation.py`.

Validation checks that a config has:

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

### Live Results

Live results use `LIVE_RESULTS_PROVIDER`, defaulting to `fifa_public_calendar`. The FIFA provider calls the public calendar matches endpoint for the canonical tournament date window using the configured `USER_AGENT`, then stores any provider matches that map to fixture IDs.

The bot starts a 30-minute background sync loop after startup. Scheduled sync does not fetch the provider endpoint before `2026-06-11T00:00:00Z`, the first matchday midnight in UTC. `/operator sync` is an explicit manual override and attempts a provider fetch even before that date.

Once active, scheduled sync fetches once per provider/config feed, applies results to all configured guilds, writes `match_results` and `result_sync_runs`, caches provider response payloads for debugging, logs delayed-provider warnings once, and recalculates scores for submitted predictions.

Official group standings use the 2026 FIFA tie-breaker order: head-to-head points, head-to-head goal difference, head-to-head goals scored, overall goal difference, overall goals scored, team conduct score, then the most recent FIFA/Coca-Cola Men's World Ranking. Best third-place ranking uses points, goal difference, goals scored, team conduct score, then FIFA ranking. If stored results cannot resolve a required tie from deterministic match data, recalculation fails loudly until an operator records the official adjudication with `/operator resolve`.

### Migrations

Migrations are plain numbered SQL files in `world_cup_bot/data/migrations`. They run automatically at bot startup and in `scripts/healthcheck.py`. Applied migration names are tracked in `schema_migrations`.

Migration `010_normalize_generated_knockout_ids.sql` normalizes legacy generated knockout match IDs in stored predictions and prediction history after the Round of 16 ID format was made contiguous. Migration `011_share_prediction_images.sql` removes obsolete visibility preference storage for the current command behavior.

### Tests

```bash
. .venv/bin/activate
python -m unittest discover tests
```

### Release Readiness

Milestone 11 release checks should be run from a Python 3.12 virtualenv with dependencies installed and `.env` populated for the target environment. The repeatable local checks are:

```bash
. .venv/bin/activate
python scripts/release_check.py
```

The release check runs the full unit suite, validates the canonical tournament file, loads `.env` for subprocesses, and runs the database health/migration smoke test. It exits non-zero if any check fails.

For machines that do not have local PostgreSQL ready yet, run the non-database portion while setting up PostgreSQL manually as described above:

```bash
. .venv/bin/activate
python scripts/release_check.py --skip-db
```

Before calling a release ready, also run the database-backed check against a local or staging PostgreSQL database:

```bash
. .venv/bin/activate
python scripts/healthcheck.py
```

Confirm these startup and process behaviors before release:

- Missing `DISCORD_TOKEN`, `DATABASE_URL`, or `USER_AGENT` fails startup with a clear configuration error.
- A bad `DATABASE_URL` fails startup or health check clearly without logging secrets.
- Normal startup logs environment name, masked database target, guild count when ready, and slash-command sync status.
- `pm2 start ecosystem.config.js --env production` runs `.venv/bin/python -m world_cup_bot.bot`, restarts with delay, and shows the `world-cup-bot` process in `pm2 status`.

### Discord Staging Checklist

Use a staging Discord application and guild. Populate `.env` locally with `DISCORD_TOKEN`, `DATABASE_URL`, `USER_AGENT`, `OPERATOR_GUILD_ID`, and `OWNER_USER_IDS` as needed. Invite the bot with application command permissions, then start it:

```bash
. .venv/bin/activate
set -a
. .env
set +a
python -m world_cup_bot.bot
```

Run this manual flow in the staging guild and record any unexpected response:

1. Run `/help` and confirm the bot responds.
2. Run `/admin setup announcement_channel:#predictions leaderboard_channel:#leaderboard lock_deadline_utc:2026-06-11T18:00:00Z` and confirm the canonical 2026 tournament is attached.
3. Run `/admin status` and verify setup, channels, lock, and tournament status.
4. Run `/admin config` with no options and verify configured scoring and lock behavior.
5. Run `/admin open` and verify prediction entry is open.
6. Run `/admin post info`; confirm it posts league info and rules in the announcement channel.
7. Run `/predict`, complete a full bracket, and confirm submission succeeds only after the final confirmation.
8. Run `/prediction`, `/groups`, and `/bracket` for yourself; confirm concise embeds appear with image attachments for the image commands.
9. Run `/edit`, change at least one submitted pick, complete the flow, and confirm the previous submission remains active until final confirmation.
10. From another test user, run `/groups user:<submitter>` and `/bracket user:<submitter>` and confirm image views are visible.
11. Run `/leaderboard`, `/rank`, and `/points`; before official results, confirm submitted predictors appear with zero or pending point details.
12. Run `/operator sync` in the configured operator guild and verify it completes or reports provider availability clearly.
13. Run `/admin recalc` and confirm it is idempotent when repeated.
14. Run `/admin post leaderboard` and confirm it posts to the leaderboard channel.
15. Run `/admin export` and `/admin backup`; confirm each returns an ephemeral JSON attachment and writes an audit row.
16. Run `/admin lock deadline_utc:2026-05-01T00:00:00Z`; confirm `/predict` or `/edit` is blocked by lock behavior.
17. Run `/admin close`; confirm prediction entry is closed without changing the configured lock deadline.
18. Restart the bot and confirm it reconnects, reapplies no duplicate migrations, logs ready state, and preserves guild setup and submitted prediction data.

Keep staging credentials and exported JSON attachments out of git. Do not commit `.env`, production data, provider secrets, PM2 dumps, or database exports.
