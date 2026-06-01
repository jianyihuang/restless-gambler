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

## Read-Only Live Plan

Build the exact guarded order payloads without submitting anything:

```bash
uv run restless-gambler live plan-kalshi \
  --base-url https://external-api.kalshi.com/trade-api/v2 \
  --max-order-cost 1 \
  --max-contracts 1 \
  --max-orders 1
```

`plan-kalshi` fetches current Kalshi markets, writes a planning snapshot, builds
would-be order payloads, and prints a readiness report. It never calls the order
placement endpoint. The default live-plan guardrails require:

- at least `$10` cash reserve
- no existing resting orders unless `--allow-resting-orders` is passed
- a market snapshot no older than `120` seconds
- no more than one planned order

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
  --max-orders 1 \
  --persist
```

Actual live runs perform the same account and snapshot preflight before the
runner is allowed to submit orders. The defaults require a `$10` cash reserve,
no existing resting orders, and a snapshot generated in the last `120` seconds.
They also cap live submission to one approved order by default. Use
`--min-cash-reserve`, `--max-snapshot-age-seconds`, `--max-orders`, or
`--allow-resting-orders` only when the tradeoff is intentional.

Live mode rejects non-Kalshi intents. Filled live orders go into the run-scoped
`bets` table when imported, but they do not populate the paper ledger.

## Reconciliation

After a live run, reconcile against Kalshi:

```bash
uv run restless-gambler live reconcile-kalshi \
  --base-url https://external-api.kalshi.com/trade-api/v2
```

This is read-only, prints current orders and positions from Kalshi, and persists
the snapshot to DuckDB by default. Use `--no-persist` only for one-off inspection.
The audit tables are `kalshi_reconciliations`,
`kalshi_reconciliation_orders`, and `kalshi_reconciliation_positions`.

## Guarded Resting-Order Cancel

Cancellation is explicit and dry-run by default:

```bash
uv run restless-gambler live cancel-kalshi-order \
  --base-url https://external-api.kalshi.com/trade-api/v2 \
  --order-id <kalshi-order-id>
```

The command first fetches resting orders and refuses to act unless the order id
is currently resting. To submit the Kalshi cancel request, add
`--confirm-cancel`:

```bash
uv run restless-gambler live cancel-kalshi-order \
  --base-url https://external-api.kalshi.com/trade-api/v2 \
  --order-id <kalshi-order-id> \
  --confirm-cancel
```

Cancel attempts are written to `kalshi_cancel_requests` by default. Confirmed
cancellations also persist a fresh reconciliation snapshot when the follow-up
read succeeds.

## Amend/Reprice Design

No automatic amend or reprice loop is allowed. Repricing should be handled as a
manual cancel-and-replace sequence:

1. Run `live reconcile-kalshi` and inspect the resting order.
2. Dry-run `live cancel-kalshi-order --order-id <id>`.
3. Confirm cancellation only with `--confirm-cancel`.
4. Run `live plan-kalshi` again and inspect the new post-only payload before any
   separate live run.

This avoids aggressive crossing and keeps every live mutation auditable.

## Current Limits

- The strategy edge is still fixture/baseline driven; do not rely on it for
  profitable live trading.
- There is no automatic cancellation/amend loop; live mutation remains explicit.
- Live execution imports link Kalshi order ids/statuses into `executions` when
  the API response includes them.
- Live settlement still depends on reconciliation plus Kalshi market settlement
  sync.
