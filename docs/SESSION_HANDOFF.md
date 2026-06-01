# Session Handoff

Use this when opening a fresh Codex/session so the project can continue without
rediscovering the workflow.

## Current Shape

Restless Gambler is a paper-first gambling research bot. It supports:

- Kalshi read-only market fetches and gated live Kalshi order placement.
- The Odds API sportsbook odds fetches.
- Merged market snapshots across Kalshi and sportsbooks.
- Structured research signals, including sportsbook no-vig consensus.
- Paper execution, DuckDB persistence, paper ledger, dashboard, and settlement.
- Read-only sportsbook moneyline, spread, and totals settlement sync from The
  Odds API scores.
- `restless-gambler cycle` for the focused MLB paper workflow.
- Latest/closing line tracking for open paper bets via merged snapshots.

The project intentionally stays separate from `~/marketforge`; run the namespace
doctor before large integration changes.

```bash
uv run restless-gambler doctor namespace
```

## Important Safety Defaults

- Do not print or commit `.env`; real keys live there.
- `.env`, `data/`, and generated run reports are ignored by git.
- Default mode is paper.
- Live Kalshi orders require both `RG_LIVE_TRADING_ENABLED=true` and
  `run --mode live --confirm-live`.
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

## Validation Before Pushing

```bash
uv run ruff check .
uv run pytest
```

Current expected baseline after the cycle/line-tracking work is `35 passed`.

## Next Useful Work

1. Add source-backed MLB stat signals beyond no-vig consensus.
2. Add closing-line history charts and richer calibration views.
3. Add historical backtest fixtures once enough settled paper bets exist.
4. Add stricter live Kalshi reconciliation tables and cancellation/amend flows.
5. Keep all new platform integrations behind product-specific adapters and risk
   gates so they do not conflict with `~/marketforge`.
