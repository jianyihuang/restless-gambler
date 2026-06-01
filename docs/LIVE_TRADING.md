# Live Trading Workflow

Live order placement is implemented for Kalshi prediction contracts only.
Sportsbook and betting-exchange integrations remain paper/manual until a legal
venue API and account workflow are explicit.

## Safety Gates

Live mode requires both:

- `RG_LIVE_TRADING_ENABLED=true` in `.env`
- `--confirm-live` on the exact `run --mode live` command

The default `post_only=true` setting is preserved for live Kalshi orders. That
means live orders should rest instead of crossing the spread unless the config is
changed later.

## Preflight

Use the read-only preflight before any live run:

```bash
uv run restless-gambler live preflight-kalshi \
  --base-url https://external-api.kalshi.com/trade-api/v2
```

This validates credentials and fetches balance, resting orders, and positions.

## Market Snapshot

Fetch current Kalshi markets:

```bash
uv run restless-gambler data fetch-kalshi \
  --limit 50 \
  --output data/markets/kalshi_latest.json
```

## Live Run

Run live only against a real Kalshi snapshot:

```bash
RG_LIVE_TRADING_ENABLED=true uv run restless-gambler run \
  --mode live \
  --confirm-live \
  --markets-path data/markets/kalshi_latest.json \
  --min-liquidity 100 \
  --max-contracts 1 \
  --max-order-cost 5 \
  --persist
```

Live mode rejects non-Kalshi intents. Filled live orders go into the run-scoped
`bets` table when imported, but they do not populate the paper ledger.

## Reconciliation

After a live run, reconcile against Kalshi:

```bash
uv run restless-gambler live reconcile-kalshi \
  --base-url https://external-api.kalshi.com/trade-api/v2
```

This is read-only and prints current orders and positions from Kalshi.

## Current Limits

- The strategy edge is still fixture/baseline driven; do not rely on it for
  profitable live trading.
- There is no automatic cancellation/amend loop yet.
- There is no persisted live order snapshot table yet; live executions are
  captured in run artifacts and the `executions` table.
- Live settlement still depends on reconciliation plus Kalshi market settlement
  sync.
