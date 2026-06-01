# Session Handoff

Use this when opening a fresh Codex/session so the project can continue without
rediscovering the workflow.

## Current Shape

Restless Gambler is a paper-first gambling research bot. It supports:

- Kalshi read-only market fetches and gated live Kalshi order placement.
- Read-only Kalshi live planning via `live plan-kalshi`; it prints guarded
  would-be order payloads and does not submit orders.
- Persisted Kalshi live reconciliation via `live reconcile-kalshi` and guarded
  resting-order cancellation via `live cancel-kalshi-order`.
- The Odds API sportsbook odds fetches.
- Merged market snapshots across Kalshi and sportsbooks.
- Structured research signals, including sportsbook no-vig consensus.
- Paper execution, DuckDB persistence, paper ledger, dashboard, and settlement.
- Settled paper backtest reporting via `eval backtest`.
- Read-only sportsbook moneyline, spread, and totals settlement sync from The
  Odds API scores.
- `restless-gambler cycle` for the focused MLB paper workflow.
- Latest/closing line tracking for open paper bets via merged snapshots.
- Stale-market guards: market loading rejects open markets whose `close_time`
  is already at or before snapshot `generated_at`, and line sync skips stale
  quotes instead of recording them as latest lines.
- Conservative sportsbook consensus: no-vig consensus requires at least three
  non-extreme books and ignores extreme American odds outliers.
- MLB paper signals can be enriched from MLB Stats API standings during
  `baseball_mlb` odds fetches when that public source is available.

The project intentionally stays separate from `~/marketforge`; run the namespace
doctor before large integration changes.

```bash
uv run restless-gambler doctor namespace
```

## Important Safety Defaults

- Do not print or commit `.env`; real keys live there.
- `.env`, `data/`, and generated run reports are ignored by git.
- GitHub Issues are now the implementation queue. For broad requests like
  "implement next" or "what should we do next", inspect open issues in
  `jianyihuang/restless-gambler` before using the static notes below.
- Default mode is paper.
- Live Kalshi orders require both `RG_LIVE_TRADING_ENABLED=true` and
  `run --mode live --confirm-live`.
- Live Kalshi runs also preflight account state and snapshot freshness before
  order placement: cash reserve, existing resting orders, and snapshot age.
- Live Kalshi submits at most one approved order by default (`--max-orders 1`).
- Live Kalshi reconciliation persists read-only order and position snapshots in
  DuckDB; guarded order cancellation is dry-run unless `--confirm-cancel` is
  passed.
- `--allow-snapshot-venues` is paper/research only and is rejected in live mode.
- Sportsbook execution is paper/manual only. Do not add live sportsbook execution
  without an official legal account API and explicit user approval.
- Dashboard port is `18652`; do not use `18651`.

## Known Local State

- Dashboard URL: `http://localhost:18652`
- Default DB: `data/restless_gambler.duckdb`
- Kalshi snapshot path: `data/markets/kalshi_latest.json`
- Sports odds snapshot path: `data/markets/sports_odds_latest.json`
- Merged snapshot path: `data/markets/merged_latest.json`

The user has configured API keys in `.env`, including `THE_ODDS_API_KEY` and
Kalshi credentials. Validate credentials with read-only commands only.
The user explicitly asked to turn live betting on; local `.env` now has
`RG_LIVE_TRADING_ENABLED=true`. This still is not sufficient to place orders:
live Kalshi placement also requires an explicit `run --mode live --confirm-live`
command. Do not add or run live sportsbook execution.

Use the read-only planner before any live run:

```bash
uv run restless-gambler live plan-kalshi \
  --base-url https://external-api.kalshi.com/trade-api/v2 \
  --max-order-cost 1 \
  --max-contracts 1 \
  --max-orders 1
```

Planner defaults are intentionally tight: one planned order, `$1` max order
cost, `$10` minimum cash reserve, no resting orders, and a market snapshot no
older than `120` seconds.

Persist a read-only live reconciliation snapshot:

```bash
uv run restless-gambler live reconcile-kalshi \
  --base-url https://external-api.kalshi.com/trade-api/v2
```

Inspect a safe resting-order cancel dry-run before any mutation:

```bash
uv run restless-gambler live cancel-kalshi-order \
  --base-url https://external-api.kalshi.com/trade-api/v2 \
  --order-id <kalshi-order-id>
```

Only add `--confirm-cancel` after reviewing the dry-run output.

## Normal End-to-End Paper Workflow

Prefer the single paper cycle command:

```bash
uv run restless-gambler cycle \
  --sport baseball_mlb \
  --max-contracts 1 \
  --max-order-cost 1
```

The cycle command fetches Kalshi, fetches sportsbook odds, merges snapshots,
runs persisted paper execution with snapshot venues allowed, syncs settlements,
syncs latest line snapshots, and prints a compact JSON state summary. Cycle run
IDs include a UTC timestamp suffix, so repeated automation should not overwrite
run-scoped rows.

Manual equivalent:

Fetch Kalshi:

```bash
uv run restless-gambler data fetch-kalshi \
  --limit 50 \
  --output data/markets/kalshi_latest.json
```

Fetch sportsbook odds:

```bash
uv run restless-gambler data fetch-odds \
  --sport baseball_mlb \
  --regions us \
  --markets h2h,spreads,totals \
  --output data/markets/sports_odds_latest.json
```

Merge snapshots:

```bash
uv run restless-gambler data merge-snapshots \
  data/markets/kalshi_latest.json \
  data/markets/sports_odds_latest.json \
  --output data/markets/merged_latest.json
```

Run persisted paper trading against real bookmaker venue names:

```bash
uv run restless-gambler run \
  --mode paper \
  --markets-path data/markets/merged_latest.json \
  --min-liquidity 0 \
  --max-contracts 1 \
  --max-order-cost 1 \
  --allow-snapshot-venues \
  --persist
```

Inspect state:

```bash
uv run restless-gambler db status
uv run restless-gambler ledger status
uv run restless-gambler eval summary
uv run restless-gambler eval calibration
uv run restless-gambler eval closing-lines
uv run restless-gambler eval backtest
```

Launch dashboard:

```bash
uv run restless-gambler dashboard --port 18652
```

## Settlement Workflow

Manual market settlement:

```bash
uv run restless-gambler ledger settle-market \
  --market-id <market-id> \
  --winning-outcome-id <outcome-id> \
  --venue <venue> \
  --product-type sportsbook
```

Kalshi read-only settlement sync:

```bash
uv run restless-gambler ledger sync-kalshi \
  --base-url https://external-api.kalshi.com/trade-api/v2
```

Sportsbook moneyline, spread, and totals scores sync:

```bash
uv run restless-gambler ledger sync-sportsbook \
  --sport baseball_mlb \
  --days-from 3
```

Latest/closing line sync:

```bash
uv run restless-gambler ledger sync-lines \
  --markets-path data/markets/merged_latest.json
```

Settled paper backtest:

```bash
uv run restless-gambler eval backtest
```

## Validation Before Pushing

```bash
uv run ruff check .
uv run pytest
```

GitHub Actions also runs these checks on pull requests and pushes to `main`
with Python 3.12 and `uv sync --locked --group dev`, then runs the namespace
doctor, `uv build`, and a fixture-only paper smoke run.

Current expected baseline after the settled-backtest work is `55 passed`.

## Next Useful Work

Open GitHub Issues are the source of truth for implementation priority. Roadmap
issues track product direction:

- #6: Roadmap: Live Kalshi safety and operations.
- #7: Roadmap: Paper trading, backtesting, and calibration.
- #8: Roadmap: Sports data adapters and source-backed signals.
- #9: Roadmap: Dashboard and decision review UI.
- #10: Roadmap: Persistence, audit trail, and reconciliation.
- #11: Roadmap: Risk management and bankroll controls.
- #12: Roadmap: Platform boundaries and legal execution policy.

Current shippable implementation issues:

- #1: Add settled-result fixtures and paper backtests.
- #2: Add closing-line history charts and richer calibration views.
- #3: Add MLB park, weather, and handedness source-backed signals.
- #5: Codify GitHub issue workflow and platform-integration boundaries.
- #14: Add paper backtest fixture bundles and strategy comparison reports.
- #15: Gate live readiness on settled paper backtest thresholds.
- #16: Expose settled backtest summaries in the dashboard.

When an issue is completed, close it and create follow-up issues only for
concrete out-of-scope work discovered during implementation. Keep all new
platform integrations behind product-specific adapters and risk gates so they do
not conflict with `~/marketforge`.
