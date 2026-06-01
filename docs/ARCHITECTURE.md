# Architecture

Restless Gambler should behave like a small gambling-market research and
execution system, not a script that blindly places bets.

The control flow is:

```text
Data -> Research -> Forecast -> Opportunity -> Risk Gate -> Execution -> Artifact
```

## Runtime Modes

- `research`: produce notes, forecasts, and opportunities without wagers.
- `paper`: run the full loop against local simulated execution.
- `live`: Kalshi-only API execution behind `RG_LIVE_TRADING_ENABLED=true` and
  per-run `--confirm-live`.

## Product Model

The core model is product-agnostic:

- `prediction_contract`: Kalshi/Polymarket-style YES/NO contracts.
- `sportsbook`: moneyline/spread/total/prop wagers where the stake is locked.
- `betting_exchange`: Betfair-style back/lay markets.
- `casino_research_only`: analysis and simulation only unless a legal API exists.

Venue adapters translate platform-specific objects into generic `Market`,
`OutcomeQuote`, `Forecast`, `Opportunity`, `WagerIntent`, and `ExecutionRecord`
objects.

## Reused MarketForge Patterns

- Dataclass domain objects for typed handoff between layers.
- CLI-first operation so runs are reproducible.
- Environment-based config and a kill switch.
- Risk checks centralized before execution.
- JSON artifacts as the durable audit trail.
- Tests that assert safety gates and reproducible output.

## Safety Boundary

Research and model code may produce notes, probabilities, and wager candidates.
They must not call venue APIs or bypass risk.

The risk gate remains deterministic. A forecast only becomes a wager intent if
expected value clears fees, spread, and configured safety margins.

Research emits structured signals. Only signals with
`kind="probability_adjustment"` move fair probability; market-quality/context
signals are logged for diagnostics and dashboard review. Source-backed signals
currently include sportsbook no-vig consensus across multiple venues for the
same event/outcome set.

## First Execution Target

The first live venue is Kalshi direct API. Sportsbook execution should be added
only for official, eligible, API-supported platforms.

Real placement should require:

- API-only execution, no browser automation.
- Idempotent `client_order_id`s.
- Post-only limit orders by default.
- Reconciliation of local executions/bets against venue state.
- Explicit live trading opt-in and kill-switch support.
