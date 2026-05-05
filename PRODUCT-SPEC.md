# World Cup Bracket Predictor Bot Product Spec

This document is the source of truth for product behavior, user experience, command surface, scoring, privacy, and visual design. Implementation rules, repository shape, runtime choices, migrations, and testing expectations live in `AGENTS.md`.

## Purpose

Build a Discord bot that lets each server run its own World Cup bracket prediction league. Members submit group-stage and knockout predictions, entries lock before the tournament starts, official results sync automatically, and the bot keeps scores, leaderboards, and visual bracket views current.

The product should feel simple for casual users and reliable for admins. Users should not need to understand bracket math, allocation tables, or scoring internals to participate.

## Users And Scope

- Server members submit predictions, track rank, review points, and optionally share full brackets.
- Server admins configure league settings, channels, scoring, locks, tournament data, and result sync.
- Bot operators deploy and monitor the bot.

The bot supports multiple Discord guilds from day one. Each guild is its own league with separate settings, predictions, scoring, leaderboards, privacy preferences, and admin configuration.

## Tournament Model

Initial target: 2026 FIFA World Cup format.

- 48 teams.
- 12 groups of 4 teams.
- Each team plays 3 group-stage matches.
- Top 2 teams from each group advance.
- 8 best third-place teams also advance.
- Knockout phase starts with a Round of 32, then Round of 16, quarter-finals, semi-finals, third-place match, and final.

Tournament setup should be adaptable if teams, fixtures, kickoff times, groups, bracket slots, or third-place allocation rules change. Bracket seeding should use the official third-place allocation table.

Reference: [FIFA World Cup 2026 tournament format](https://gpcustomersupportfwc2026.tickets.fifa.com/hc/en-gb/articles/28784798873117-8-What-is-the-format-for-the-FIFA-World-Cup-2026-tournament).

## Product Goals

- Make bracket entry fast, guided, private, and hard to submit incorrectly.
- Support the expanded 48-team format cleanly.
- Auto-seed the Round of 32 from group predictions and predicted third-place qualifiers.
- Give admins clear setup, sync, scoring, and posting controls.
- Show live standings and prediction summaries in Discord.
- Render dense group and bracket views as readable generated images.
- Keep MVP small enough to ship before adding richer analytics or polish.

## Non-Goals For MVP

- Real-money betting, odds, wagers, payouts, or gambling mechanics.
- Manual result entry as the primary official-results workflow.
- Multi-sport tournament support.
- Complex web dashboard.
- Public SaaS hosting for unrelated communities.
- AI-generated predictions or recommendation systems.

## Core Experience

### Server Setup

Expected setup flow:

1. Admin sets prediction and leaderboard channels.
2. Admin imports or selects tournament data.
3. Admin accepts or adjusts the suggested scoring defaults.
4. Admin configures the live results source.
5. Admin configures lock deadline.
6. Admin opens predictions.

Setup commands:

- `/admin setup`
- `/admin import`
- `/admin config`
- `/admin sync`
- `/admin open`
- `/admin close`
- `/admin status`

### Prediction Entry

Prediction flow:

1. `/predict` opens a private prediction session.
2. User ranks each group.
3. Bot derives group winners, runners-up, third-place teams, and asks the user to choose the 8 third-place teams they predict will advance.
4. Bot seeds the Round of 32 from group predictions, predicted third-place qualifiers, and the official allocation table.
5. User picks winners round by round until champion, runner-up, third place, and fourth place are known.
6. Bot displays a confirmation summary.
7. Completing the `/predict` flow submits the bracket.

`/edit` updates a submitted bracket until lock. Users should never manually fill the initial Round of 32 bracket.

### Locking

All predictions lock before the tournament starts. MVP lock mode is `full_bracket_lock`: group-stage picks, third-place qualifier picks, and knockout picks all lock at one deadline before the first match.

### Live Result Updates

Official group and knockout results update automatically from a configured live data source.

Preferred default provider: football-data.org. Its free tier lists scores as delayed but does not publish a guaranteed delay. Free-tier data is acceptable if observed result delay stays under 6 hours; if a score is delayed longer, log a single warning for that match or sync window to avoid warning floods. The product should allow upgrading to football-data.org live scores without changing user-facing behavior.

Admins can trigger a manual sync or recalculation, but normal operation should not require manual result entry.

### Leaderboards

Leaderboard views include:

- Rank.
- User.
- Total points.
- Group-stage points.
- Knockout points.
- Champion pick.
- Last updated time.

Leaderboard ties use shared rank. Users with the same point total receive the same rank, and the next rank skips ahead by the number of tied users.

### Sharing And Privacy

Full brackets are private unless the user chooses to share them through `/preferences`. Champion, runner-up, and third-place picks are visible immediately after submission, even before lock.

The bot should only store Discord IDs, cached display names, prediction data, preferences, and operational data required for scoring/audit.

## Prediction Model

Predictions are score-agnostic. Users pick advancing teams, winners, losers, and final placements, not exact scores, margins, or goal totals.

Prediction inputs:

- Group ranking predictions for each group.
- 8 predicted advancing third-place teams.
- Knockout winner predictions by round after the bot seeds the Round of 32.
- Champion, runner-up, third-place winner, and fourth place.

Official result inputs:

- Group match results.
- Computed actual group standings.
- Computed actual third-place qualifiers.
- Knockout winners.
- Third-place match winner.
- Champion and runner-up.

If a predicted team is eliminated earlier than the user expected, later predicted appearances by that team should be marked incorrect automatically.

## Scoring

Scoring is configurable per guild. Setup should offer these suggested defaults:

- Correct group winner: 3 points.
- Correct group runner-up: 2 points.
- Correct group third-place qualifier: 1 point when the actual team was one of the user's 8 predicted advancing third-place teams.
- Correct Round of 32 advancement: 1 point.
- Correct Round of 16 advancement: 2 points.
- Correct quarter-final advancement: 5 points.
- Correct semi-final advancement: 10 points.
- Correct final advancement: 15 points.
- Correct third-place winner: 10 points.
- Correct champion: 25 points.
- Correct runner-up: 15 points.

Knockout scoring is team-advancement based, not slot-exact. Users get credit when a predicted team reaches the relevant round even if the exact bracket path differs from their prediction. Champion points stack with prior knockout advancement points.

## Discord Commands

Commands should not use a universal `/wc` prefix. Use direct public commands and an `/admin` command group.

Public/user commands:

- `/predict`: starts or resumes prediction entry and submits when completed.
- `/edit`: edits a submitted prediction until lock.
- `/bracket [user]`: renders a user's 32-team bracket image with a concise summary.
- `/groups [user]`: renders a user's group-stage prediction image with a concise summary.
- `/prediction [user]`: shows a user's prediction summary.
- `/leaderboard`: shows current rankings.
- `/rank [user]`: shows rank and point breakdown.
- `/points [user]`: shows detailed points.
- `/preferences`: manages personal settings, including full-bracket sharing.
- `/rules`: shows scoring and deadlines.
- `/help`: shows command help.

Admin commands:

- `/admin setup`: configures channels, timezone, and initial settings.
- `/admin import`: imports or validates tournament, fixture, bracket, and allocation-table data.
- `/admin config`: views or updates scoring, privacy defaults, lock mode, live provider, and guild settings.
- `/admin open`: opens prediction entry.
- `/admin close`: closes prediction entry without changing the configured lock deadline.
- `/admin lock`: sets, views, or forces prediction locks.
- `/admin sync`: checks live sync status or triggers manual result sync.
- `/admin recalc`: recalculates scores and leaderboard totals.
- `/admin post`: posts leaderboard, lock, reminder, or rules snapshots to configured channels.
- `/admin export`: exports tournament, prediction, scoring, or leaderboard data.
- `/admin backup`: creates an operator-friendly backup of bot configuration and database state.

Admin commands require Discord Manage Guild permission by default. Additional users and roles can be granted `/admin` access through Discord Server Settings > Integrations > Command Permissions > Role & Member Overrides.

## Discord UX

- Use ephemeral messages for personal prediction entry and noisy admin workflows.
- Use select menus for group ordering and winner choices.
- Use buttons for previous, next, save draft, finish, and cancel.
- Use embeds for rules, summaries, leaderboards, and concise image fallbacks.
- Use pagination for large leaderboards.
- Validate every step before moving users forward.
- Never submit an incomplete or structurally impossible bracket.
- Show lock state before edits.
- Confirm destructive admin actions.
- Keep public messages concise.

## Generated Visuals

Generated visuals are deterministic image attachments from stored prediction/result data, not the source of truth.

Visual outputs:

- Group-stage prediction sheet showing each group, predicted order, predicted qualifiers, and correctness status.
- One full 32-team knockout bracket image showing predicted path, winners, champion, runner-up, third place, and fourth place.
- Result comparison view highlighting correct, incorrect, and pending/unplayed slots as live results arrive.

Visual design:

- Dark mode, not pitch black.
- Simple, clear, sleek, and comfortable inside Discord.
- High enough resolution for mobile users to zoom in.
- Country flag icons next to country names.
- Correct, incorrect, and pending states must be distinguishable without relying on color alone.
- Include user, tournament, prediction status, lock status, and last result sync time.
- Provide concise text/embed summaries alongside image attachments for accessibility and load failures.

## MVP Milestones

### 1. Foundation

- Establish a runnable bot foundation.
- Add configuration, startup, logging, and operator setup docs.
- Add persistence foundation.
- Add health/startup visibility.

### 2. Tournament Data

- Define tournament JSON schema.
- Import teams, groups, fixtures, bracket template, and third-place allocation table.
- Add admin status command.
- Validate incomplete tournament data.

### 3. Prediction Entry

- Add `/predict` and `/edit`.
- Build group and knockout entry flow.
- Save drafts.
- Submit completed predictions at the end of `/predict`.
- Enforce lock deadline.

### 4. Results And Scoring

- Add live results client and sync job.
- Add admin sync status and manual sync.
- Compute official standings and third-place qualifiers.
- Implement scoring, recalculation, and point breakdowns.

### 5. Leaderboards And Polish

- Add leaderboard embeds and pagination.
- Add user bracket/group views.
- Add generated image views with result highlighting.
- Add preferences, admin announcement, export, and backup workflows.
- Tighten errors and documentation.
