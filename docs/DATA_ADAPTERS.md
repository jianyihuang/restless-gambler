# Data Adapters

The adapter contract is:

```text
platform payload -> Market -> OutcomeQuote -> snapshot JSON
```

The rest of the system should not know whether a market came from Kalshi,
a sportsbook, or a betting exchange.

Merge multiple normalized sources before running:

```bash
uv run restless-gambler data merge-snapshots \
  data/markets/kalshi_latest.json \
  data/markets/sports_odds_latest.json \
  --output data/markets/merged_latest.json
```

## Kalshi

Implemented read-only commands:

```bash
uv run restless-gambler data fetch-kalshi --limit 50
uv run restless-gambler run --mode paper --markets-path data/markets/kalshi_latest.json
```

The Kalshi adapter:

- Calls public market-data endpoints only.
- Normalizes `active` markets to core `open` status.
- Converts YES/NO quotes into generic outcomes.
- Uses reported liquidity when present, otherwise falls back to open interest
  or volume as an activity proxy.
- Writes ignored local snapshots under `data/markets/`.

## Sportsbooks

Implemented read-only command for The Odds API:

```bash
uv run restless-gambler data fetch-odds \
  --sport upcoming \
  --regions us \
  --markets h2h \
  --output data/markets/sports_odds_latest.json
```

Set `THE_ODDS_API_KEY` in `.env` before fetching.

The sports odds adapter:

- Calls The Odds API read-only odds endpoint.
- Calls The Odds API read-only scores endpoint for h2h paper settlement.
- Normalizes bookmaker odds into one `sportsbook` market per event/bookmaker
  market.
- Stores bookmaker market/outcome metadata so the research layer can compare
  matching outcomes across books.
- Uses American odds in the snapshot.
- Supports moneyline (`h2h`), spreads, totals, and any compatible provider
  market payload.

When multiple sportsbook venues quote the same event/outcome set, the research
layer computes a no-vig consensus probability and emits
`sportsbook_consensus_no_vig` probability-adjustment signals. Those signals can
move fair probability in paper runs; they are still research signals, not live
sportsbook execution.

Run paper simulations on real bookmaker venue names with an explicit venue
allowlist:

```bash
uv run restless-gambler run \
  --mode paper \
  --markets-path data/markets/sports_odds_latest.json \
  --min-liquidity 0 \
  --allow-snapshot-venues
```

`--allow-snapshot-venues` is rejected in live mode. For narrower paper tests,
repeat `--allowed-venue` instead of allowing every venue in the snapshot.

Settle completed h2h paper bets from scores:

```bash
uv run restless-gambler ledger sync-sportsbook \
  --sport baseball_ncaa \
  --days-from 3
```

The first settlement sync handles moneyline/h2h outcomes only. Spreads and totals
need market-specific grading rules before they should be automated.

Execution should remain paper-only unless a platform provides an official,
legal account API for bet placement.
