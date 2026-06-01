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
  quality, sportsbook overround, and activity level. Only explicit
  probability-adjustment signals move fair probability.
- Kalshi live trading is implemented behind explicit safety gates. Sportsbook
  live placement remains paper/manual until a legal venue API is integrated.

## Quickstart

```bash
uv sync
uv run restless-gambler run --mode paper --as-of 2026-05-31
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

Sync sportsbook h2h paper bets from The Odds API scores:

```bash
uv run restless-gambler ledger sync-sportsbook \
  --sport baseball_ncaa \
  --days-from 3
```

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

Market-data fetching and preflight are read-only. Kalshi live order placement is
implemented behind two gates: `RG_LIVE_TRADING_ENABLED=true` and
`run --mode live --confirm-live`. See `docs/LIVE_TRADING.md` before using it.

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
- `RG_KILL_SWITCH=true` rejects all wager intents.
- `RG_LIVE_TRADING_ENABLED=false` by default.
- The research/model layer never places wagers directly.
- Core types are product-agnostic: prediction contracts, sportsbooks, betting
  exchanges, and research-only casino analysis can share the same EV/risk path.

## Next Build Steps

1. Add real external research adapters for market rules, official data, and news.
2. Expand source-backed models beyond sportsbook consensus into weather,
   economic, and sport-stat signals.
3. Expand sportsbook settlement beyond h2h into spreads and totals.
4. Add calibration/backtest metrics before scaling any real order placement.
5. Add venue-specific execution only where legal account access and platform
   APIs allow it.
6. Add stricter live Kalshi reconciliation tables and cancellation/amend flows.
