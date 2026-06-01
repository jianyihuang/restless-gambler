# Persistence And Paper Ledger

Restless Gambler stores run artifacts in JSON first, then imports them into
DuckDB for querying and evaluation.

## Commands

Initialize the database:

```bash
uv run restless-gambler db init
```

Import an existing run artifact:

```bash
uv run restless-gambler db import-run reports/runs/<run-id>.json
```

Run and persist in one command:

```bash
uv run restless-gambler run \
  --mode paper \
  --markets-path data/markets/merged_latest.json \
  --persist
```

Persisted runs enable the open-ledger duplicate guard by default. Before
creating new paper intents, the runner blocks any venue/market/outcome key that
is already open in `paper_bet_ledger` from a different run. For one-off
experiments, pass `--allow-duplicate-open-ledger` to disable that guard. The
same open-ledger scan also feeds existing total and per-market cost into the
risk gate exposure limits.

Inspect state:

```bash
uv run restless-gambler db status
uv run restless-gambler ledger status
uv run restless-gambler eval summary
uv run restless-gambler eval calibration
```

Launch the local dashboard:

```bash
uv run restless-gambler dashboard
```

The default dashboard URL is `http://localhost:18652`.

Manually settle a paper bet:

```bash
uv run restless-gambler ledger settle \
  --client-order-id <client-order-id> \
  --outcome won
```

Allowed manual settlement outcomes are `won`, `lost`, and `push`.

Settle all open paper bets on one sportsbook market:

```bash
uv run restless-gambler ledger settle-market \
  --market-id NBA-LAL-BOS-20260601-ML \
  --winning-outcome-id lal \
  --venue paper_sportsbook \
  --product-type sportsbook
```

Use `--push` instead of `--winning-outcome-id` when every open bet on that
market should be refunded.

Sync finalized Kalshi paper bets from the market API:

```bash
uv run restless-gambler ledger sync-kalshi \
  --base-url https://external-api.kalshi.com/trade-api/v2
```

By default, Kalshi sync only settles markets that report a finalized/settled
status and a clear binary `result`. Passing `--include-determined` also settles
markets in `determined` or `amended` status, which may be useful for early
paper evaluation but can change if the result is disputed before finalization.

Sync completed sportsbook moneyline, spread, and totals paper bets from The Odds
API scores:

```bash
uv run restless-gambler ledger sync-sportsbook \
  --sport baseball_mlb \
  --days-from 3
```

## Tables

The importer writes run-scoped tables for:

- runs
- markets and outcome quotes
- research notes
- research signals
- forecasts
- opportunity diagnostics
- opportunities
- wager intents
- risk decisions
- executions
- bets
- positions

It also maintains `paper_bet_ledger`, keyed by `client_order_id`, for open and
settled paper bets.

## Current Limits

- Sports settlement sync currently handles The Odds API moneyline, spreads, and
  totals when point metadata is present.
- Kalshi sync is read-only and only uses market status/result. It does not query
  live account positions.
- Calibration metrics stay sparse until enough paper bets settle.
