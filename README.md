# Restless Gambler

Paper-first gambling research and execution skeleton.

The first usable loop is:

```text
market snapshots
-> research notes
-> probability forecasts
-> EV opportunities
-> risk gate
-> paper execution
-> JSON run artifact
```

The structure borrows the useful parts of `~/marketforge`: explicit runtime
modes, dataclass domain objects, env-driven config, deterministic paper runs,
centralized risk checks, JSON artifacts, and tests around safety behavior.

## What Exists Now

- `restless-gambler run --mode paper` runs a deterministic paper cycle.
- Fixture prediction-market and sports-moneyline markets live in
  `examples/markets/kalshi_markets.json`.
- Risk checks block wagers when the kill switch is on, edge is too small, cost
  exceeds limits, or the venue/market is not allowed.
- Research notes now include structured signals such as fixture-only probability
  adjustments, sportsbook consensus no-vig probabilities, bid/ask spread
  quality, sportsbook overround, MLB standings-based team strength, probable
  pitcher strength, bullpen-rest proxy, and activity level. Only explicit
  probability-adjustment signals move fair probability.
- Snapshot loading filters stale markets whose close time has already passed by
  the source snapshot timestamp; latest-line sync also skips stale quotes.
- Kalshi live trading is implemented behind explicit safety gates. Sportsbook
  live placement remains paper/manual until a legal venue API is integrated.

## Quickstart

```bash
uv sync
uv run restless-gambler run --mode paper --as-of 2026-05-31
uv run pytest
```

GitHub Actions runs the same default checks on pull requests and pushes to
`main`:

```bash
uv run ruff check .
uv run pytest
```

Without `uv`, use any Python 3.12 environment and install the project in editable
mode.

```bash
python -m pip install -e .
python -m restless_gambler.cli run --mode paper --as-of 2026-05-31
python -m pytest
```

The run writes an artifact under `reports/runs/`.

Run the current end-to-end paper automation cycle:

```bash
uv run restless-gambler cycle \
  --sport baseball_mlb \
  --max-contracts 1 \
  --max-order-cost 1
```

`cycle` fetches Kalshi markets, fetches sportsbook odds, merges snapshots, runs
paper execution with real snapshot venues enabled, persists the run, syncs
latest line snapshots, syncs settlements, and prints a JSON summary. Cycle runs
use timestamped run IDs so repeated automation does not overwrite prior
run-scoped rows.

## Real Kalshi Read-Only Data

Fetch open Kalshi markets into the generic snapshot schema:

```bash
uv run restless-gambler data fetch-kalshi \
  --limit 50 \
  --output data/markets/kalshi_latest.json
```

Run the paper loop on that real snapshot:

```bash
uv run restless-gambler run \
  --mode paper \
  --markets-path data/markets/kalshi_latest.json \
  --min-liquidity 100
```

Persist a run into DuckDB:

```bash
uv run restless-gambler run \
  --mode paper \
  --markets-path data/markets/merged_latest.json \
  --min-liquidity 0 \
  --allow-snapshot-venues \
  --persist
```

When `--persist` is used, the runner checks the paper ledger first and blocks
new paper intents for venue/market/outcome keys that are already open from a
different run. Use `--allow-duplicate-open-ledger` only when intentionally
testing repeated exposure. Persisted runs also count existing open ledger cost
toward total and per-market exposure limits.

Use `--allow-snapshot-venues` only for paper/research simulations when a real
odds snapshot contains venue names such as `draftkings`, `fanduel`, or
`betmgm`. The flag is rejected in live mode; live placement is still limited to
the implemented Kalshi adapter.

Inspect stored runs, the paper ledger, and evaluation summary:

```bash
uv run restless-gambler db status
uv run restless-gambler ledger status
uv run restless-gambler eval summary
uv run restless-gambler eval calibration
uv run restless-gambler eval closing-lines
uv run restless-gambler eval backtest
```

Launch the local dashboard:

```bash
uv run restless-gambler dashboard
```

The default dashboard URL is `http://localhost:18652`.

For future Codex/session continuity, start with `docs/SESSION_HANDOFF.md`.

Check that Restless Gambler is isolated from `~/marketforge`:

```bash
uv run restless-gambler doctor namespace
```

Manually settle a paper bet:

```bash
uv run restless-gambler ledger settle \
  --client-order-id paper-baseline_cross_gambling_ev-20260531-KXFED-26JUN-CUT-yes-buy \
  --outcome won
```

Settle a sportsbook-style paper market by winner:

```bash
uv run restless-gambler ledger settle-market \
  --market-id NBA-LAL-BOS-20260601-ML \
  --winning-outcome-id lal \
  --venue paper_sportsbook \
  --product-type sportsbook
```

Sync finalized Kalshi paper bets from read-only market status:

```bash
uv run restless-gambler ledger sync-kalshi \
  --base-url https://external-api.kalshi.com/trade-api/v2
```

Sync sportsbook moneyline, spread, and totals paper bets from The Odds API
scores:

```bash
uv run restless-gambler ledger sync-sportsbook \
  --sport baseball_mlb \
  --days-from 3
```

Snapshot latest/closing odds for open paper bets from the current merged market
file:

```bash
uv run restless-gambler ledger sync-lines \
  --markets-path data/markets/merged_latest.json
```

Run the settled paper backtest report after paper bets have been settled:

```bash
uv run restless-gambler eval backtest
```

The report reads DuckDB only. It summarizes settled hit rate, realized PnL, ROI,
EV bucket performance, probability-bucket calibration, and latest closing-line
movement when line snapshots exist.

Check production Kalshi credentials without changing `.env`:

```bash
uv run restless-gambler credentials check-kalshi \
  --base-url https://external-api.kalshi.com/trade-api/v2
```

Run a read-only Kalshi live preflight:

```bash
uv run restless-gambler live preflight-kalshi \
  --base-url https://external-api.kalshi.com/trade-api/v2
```

Build a read-only live plan that prints would-be order payloads without placing
anything:

```bash
uv run restless-gambler live plan-kalshi \
  --base-url https://external-api.kalshi.com/trade-api/v2 \
  --max-order-cost 1 \
  --max-contracts 1 \
  --max-orders 1
```

Market-data fetching, preflight, and live planning are read-only. Kalshi live
order placement is implemented behind `RG_LIVE_TRADING_ENABLED=true`,
`run --mode live --confirm-live`, and live preflight guardrails for cash reserve,
resting orders, snapshot age, and max live order count. See
`docs/LIVE_TRADING.md` before using it.

Reconcile live Kalshi order/position state into DuckDB:

```bash
uv run restless-gambler live reconcile-kalshi \
  --base-url https://external-api.kalshi.com/trade-api/v2
```

Dry-run cancellation of a resting Kalshi order:

```bash
uv run restless-gambler live cancel-kalshi-order \
  --base-url https://external-api.kalshi.com/trade-api/v2 \
  --order-id <kalshi-order-id>
```

Fetch sportsbook odds when `THE_ODDS_API_KEY` is configured:

```bash
uv run restless-gambler data fetch-odds \
  --sport upcoming \
  --regions us \
  --markets h2h \
  --output data/markets/sports_odds_latest.json
```

Merge normalized snapshots from multiple sources:

```bash
uv run restless-gambler data merge-snapshots \
  data/markets/kalshi_latest.json \
  data/markets/sports_odds_latest.json \
  --output data/markets/merged_latest.json
```

## Safety Defaults

- Default mode is paper.
- Live Kalshi mode requires `RG_LIVE_TRADING_ENABLED=true` and `--confirm-live`.
- Live Kalshi runs preflight account state and snapshot freshness before orders.
- Live Kalshi submits at most one approved order by default.
- `RG_KILL_SWITCH=true` rejects all wager intents.
- `RG_LIVE_TRADING_ENABLED=false` by default.
- The research/model layer never places wagers directly.
- Core types are product-agnostic: prediction contracts, sportsbooks, betting
  exchanges, and research-only casino analysis can share the same EV/risk path.

## Next Build Steps

1. Add real external research adapters for market rules, official data, and news.
2. Expand source-backed models beyond sportsbook consensus into weather,
   economic, and sport-stat signals.
3. Add richer calibration charts and closing-line history views.
4. Add sport-stat ingestion for the focused MLB paper cycle.
5. Add venue-specific execution only where legal account access and platform
   APIs allow it.
6. Add stricter live Kalshi reconciliation tables and cancellation/amend flows.
