# Integration Boundaries

Restless Gambler is intentionally isolated from `~/marketforge` even though both
projects use similar Python tooling.

## Namespaces

- Console script: `restless-gambler`
- Import package: `restless_gambler`
- Environment prefix: `RG_`
- Local database: `data/restless_gambler.duckdb`

MarketForge keeps:

- Console script: `marketforge`
- Import package: `marketforge`
- Environment prefix: `MARKETFORGE_`
- Broker env vars: `ALPACA_` / `APCA_`
- Local state: `data/state/marketforge.sqlite`

## Path Isolation

Restless Gambler defaults are anchored to the Restless Gambler project root, not
the current working directory. This prevents accidental writes into
`~/marketforge` if `restless-gambler` is invoked while the shell is inside the
MarketForge repo.

Default Restless Gambler write paths:

- `data/markets/`
- `data/restless_gambler.duckdb`
- `reports/runs/`

These resolve under `/Users/jianyihuang/restless-gambler` by default.

## Diagnostic Command

Run:

```bash
uv run restless-gambler doctor namespace
```

The command checks:

- Console script names do not overlap.
- Project/package names do not overlap.
- Environment variables do not overlap.
- Restless Gambler default paths do not resolve inside MarketForge.

Use `--marketforge-root` if MarketForge moves:

```bash
uv run restless-gambler doctor namespace --marketforge-root ~/marketforge
```

## Shared Tooling

Both projects can safely use `uv`, `pytest`, `ruff`, and DuckDB because each
project has its own package name, virtual environment, command namespace, and
project-root-anchored paths.

## Live Trading Boundary

Restless Gambler live trading is Kalshi-only. MarketForge broker credentials
(`ALPACA_` / `APCA_`) are never read by this project, and Restless Gambler live
orders require the `RG_LIVE_TRADING_ENABLED=true` environment gate plus the
per-run `--confirm-live` flag.
