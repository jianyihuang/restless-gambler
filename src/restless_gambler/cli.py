from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from restless_gambler.config import (
    DEFAULT_ALLOWED_VENUES,
    DEFAULT_ARTIFACTS_DIR,
    DEFAULT_MARKETS_PATH,
    load_config,
)
from restless_gambler.env import load_dotenv
from restless_gambler.kalshi import (
    check_kalshi_credentials,
    fetch_kalshi_market_data,
    fetch_kalshi_orders,
    fetch_kalshi_positions,
    kalshi_account_snapshot,
    write_kalshi_market_snapshot,
)
from restless_gambler.market_data import merge_market_snapshot_files
from restless_gambler.namespace import namespace_report
from restless_gambler.paths import DATA_DIR
from restless_gambler.persistence import (
    DEFAULT_DB_PATH,
    calibration_summary,
    evaluation_summary,
    import_run_artifact,
    init_database,
    ledger_status,
    open_ledger_exposure,
    open_ledger_wager_keys,
    settle_paper_bet,
    summarize_database,
)
from restless_gambler.runner import RestlessGamblerRunner, build_run_id
from restless_gambler.settlement import (
    settle_market_paper_bets,
    sync_kalshi_paper_settlements,
    sync_sportsbook_paper_settlements,
)
from restless_gambler.sports_odds import (
    fetch_sports_odds,
    write_sports_odds_snapshot,
)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return run_command(args)
    if args.command == "cycle":
        return cycle_command(args)
    if args.command == "dashboard":
        return dashboard_command(args)
    if args.command == "data" and args.data_command == "fetch-kalshi":
        return fetch_kalshi_command(args)
    if args.command == "data" and args.data_command == "fetch-odds":
        return fetch_odds_command(args)
    if args.command == "data" and args.data_command == "merge-snapshots":
        return merge_snapshots_command(args)
    if args.command == "artifact" and args.artifact_command == "show":
        return show_artifact_command(args)
    if args.command == "db" and args.db_command == "init":
        return db_init_command(args)
    if args.command == "db" and args.db_command == "import-run":
        return db_import_run_command(args)
    if args.command == "db" and args.db_command == "status":
        return db_status_command(args)
    if args.command == "ledger" and args.ledger_command == "status":
        return ledger_status_command(args)
    if args.command == "ledger" and args.ledger_command == "settle":
        return ledger_settle_command(args)
    if args.command == "ledger" and args.ledger_command == "settle-market":
        return ledger_settle_market_command(args)
    if args.command == "ledger" and args.ledger_command == "sync-kalshi":
        return ledger_sync_kalshi_command(args)
    if args.command == "ledger" and args.ledger_command == "sync-sportsbook":
        return ledger_sync_sportsbook_command(args)
    if args.command == "live" and args.live_command == "preflight-kalshi":
        return live_preflight_kalshi_command(args)
    if args.command == "live" and args.live_command == "reconcile-kalshi":
        return live_reconcile_kalshi_command(args)
    if args.command == "eval" and args.eval_command == "summary":
        return eval_summary_command(args)
    if args.command == "eval" and args.eval_command == "calibration":
        return eval_calibration_command(args)
    if args.command == "doctor" and args.doctor_command == "namespace":
        return doctor_namespace_command(args)
    if args.command == "credentials" and args.credentials_command == "check-kalshi":
        return check_kalshi_command(args)

    parser.print_help()
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="restless-gambler")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="run the gambling-market loop")
    run_parser.add_argument(
        "--mode",
        choices=["research", "paper", "live"],
        default="paper",
        help="runtime mode; paper is the default",
    )
    run_parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        help="run date in YYYY-MM-DD format",
    )
    run_parser.add_argument(
        "--markets-path",
        type=Path,
        default=DEFAULT_MARKETS_PATH,
        help="path to market snapshot JSON",
    )
    run_parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=DEFAULT_ARTIFACTS_DIR,
        help="directory where run artifacts are written",
    )
    run_parser.add_argument(
        "--min-expected-value",
        type=float,
        default=None,
        help="minimum expected value after estimated fees",
    )
    run_parser.add_argument(
        "--min-liquidity",
        type=float,
        default=None,
        help="minimum normalized liquidity/activity required for a market",
    )
    run_parser.add_argument(
        "--max-order-cost",
        type=float,
        default=None,
        dest="max_wager_cost",
        help="maximum cost for one wager intent",
    )
    run_parser.add_argument(
        "--max-contracts",
        "--max-units",
        type=int,
        default=None,
        dest="max_units_per_wager",
        help="maximum contracts/stake units per wager intent",
    )
    run_parser.add_argument(
        "--persist",
        action="store_true",
        help="import the written run artifact into DuckDB after the run",
    )
    run_parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"DuckDB database path; default {DEFAULT_DB_PATH}",
    )
    run_parser.add_argument(
        "--avoid-open-ledger",
        action="store_true",
        help="block paper intents for venue/market/outcome keys already open in DuckDB",
    )
    run_parser.add_argument(
        "--allow-duplicate-open-ledger",
        action="store_true",
        help="disable the open-ledger duplicate guard that is enabled by --persist",
    )
    run_parser.add_argument(
        "--allowed-venue",
        action="append",
        default=None,
        dest="allowed_venues",
        help="additional venue allowed by the risk gate; repeatable",
    )
    run_parser.add_argument(
        "--allow-snapshot-venues",
        action="store_true",
        help="paper/research only: allow every venue present in the market snapshot",
    )
    run_parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="required with --mode live before any live platform order is submitted",
    )

    cycle_parser = subparsers.add_parser(
        "cycle",
        help="run the paper fetch, merge, trade, persist, and settlement workflow",
    )
    cycle_parser.add_argument(
        "--sport",
        default="baseball_mlb",
        help="The Odds API sport key for the focused paper cycle",
    )
    cycle_parser.add_argument(
        "--regions",
        default="us",
        help="comma-delimited bookmaker regions such as us, us2, uk, eu, au",
    )
    cycle_parser.add_argument(
        "--markets",
        default="h2h,spreads,totals",
        help="comma-delimited market keys such as h2h,spreads,totals",
    )
    cycle_parser.add_argument(
        "--bookmakers",
        default=None,
        help="optional comma-delimited bookmaker keys",
    )
    cycle_parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        help="run date in YYYY-MM-DD format",
    )
    cycle_parser.add_argument(
        "--kalshi-limit",
        type=int,
        default=50,
        help="maximum open Kalshi markets to fetch",
    )
    cycle_parser.add_argument(
        "--skip-orderbooks",
        action="store_true",
        help="skip Kalshi per-market orderbook requests",
    )
    cycle_parser.add_argument(
        "--orderbook-depth",
        type=int,
        default=1,
        help="Kalshi orderbook depth to fetch for each market",
    )
    cycle_parser.add_argument(
        "--kalshi-output",
        type=Path,
        default=DATA_DIR / "markets" / "kalshi_latest.json",
        help="path where the normalized Kalshi snapshot is written",
    )
    cycle_parser.add_argument(
        "--sportsbook-output",
        type=Path,
        default=DATA_DIR / "markets" / "sports_odds_latest.json",
        help="path where the normalized sportsbook snapshot is written",
    )
    cycle_parser.add_argument(
        "--merged-output",
        type=Path,
        default=DATA_DIR / "markets" / "merged_latest.json",
        help="path where the merged snapshot is written",
    )
    cycle_parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=DEFAULT_ARTIFACTS_DIR,
        help="directory where run artifacts are written",
    )
    cycle_parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"DuckDB database path; default {DEFAULT_DB_PATH}",
    )
    cycle_parser.add_argument(
        "--min-expected-value",
        type=float,
        default=None,
        help="minimum expected value after estimated fees",
    )
    cycle_parser.add_argument(
        "--min-liquidity",
        type=float,
        default=0.0,
        help="minimum normalized liquidity/activity required for a market",
    )
    cycle_parser.add_argument(
        "--max-order-cost",
        type=float,
        default=1.0,
        dest="max_wager_cost",
        help="maximum cost for one paper wager intent",
    )
    cycle_parser.add_argument(
        "--max-contracts",
        "--max-units",
        type=int,
        default=1,
        dest="max_units_per_wager",
        help="maximum contracts/stake units per paper wager intent",
    )
    cycle_parser.add_argument(
        "--settlement-days-from",
        type=int,
        default=3,
        help="completed-score lookback in days; The Odds API accepts 1 to 3",
    )
    cycle_parser.add_argument(
        "--skip-settlement-sync",
        action="store_true",
        help="skip read-only settlement sync after the paper run",
    )
    cycle_parser.add_argument(
        "--allow-duplicate-open-ledger",
        action="store_true",
        help="disable the open-ledger duplicate guard during the cycle run",
    )

    dashboard_parser = subparsers.add_parser(
        "dashboard",
        help="launch the Streamlit paper-trading dashboard",
    )
    dashboard_parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"DuckDB database path; default {DEFAULT_DB_PATH}",
    )
    dashboard_parser.add_argument(
        "--host",
        default="localhost",
        help="dashboard host",
    )
    dashboard_parser.add_argument(
        "--port",
        type=int,
        default=18652,
        help="dashboard port",
    )

    data_parser = subparsers.add_parser("data", help="fetch and normalize data")
    data_subparsers = data_parser.add_subparsers(dest="data_command")
    kalshi_parser = data_subparsers.add_parser(
        "fetch-kalshi",
        help="fetch open Kalshi markets into the generic snapshot schema",
    )
    kalshi_parser.add_argument(
        "--output",
        type=Path,
        default=DATA_DIR / "markets" / "kalshi_latest.json",
        help="path where the normalized snapshot JSON is written",
    )
    kalshi_parser.add_argument(
        "--base-url",
        default=None,
        help=(
            "Kalshi API base URL; defaults to KALSHI_MARKET_DATA_BASE_URL "
            "or production market data"
        ),
    )
    kalshi_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="maximum open markets to fetch",
    )
    kalshi_parser.add_argument(
        "--skip-orderbooks",
        action="store_true",
        help="skip per-market orderbook requests and use market summary quotes only",
    )
    kalshi_parser.add_argument(
        "--orderbook-depth",
        type=int,
        default=1,
        help="orderbook depth to fetch for each market",
    )

    odds_parser = data_subparsers.add_parser(
        "fetch-odds",
        help="fetch sportsbook odds from The Odds API into the generic schema",
    )
    odds_parser.add_argument(
        "--sport",
        default="upcoming",
        help="The Odds API sport key; upcoming returns live and upcoming events",
    )
    odds_parser.add_argument(
        "--regions",
        default="us",
        help="comma-delimited bookmaker regions such as us, us2, uk, eu, au",
    )
    odds_parser.add_argument(
        "--markets",
        default="h2h",
        help="comma-delimited market keys such as h2h,spreads,totals",
    )
    odds_parser.add_argument(
        "--bookmakers",
        default=None,
        help="optional comma-delimited bookmaker keys",
    )
    odds_parser.add_argument(
        "--output",
        type=Path,
        default=DATA_DIR / "markets" / "sports_odds_latest.json",
        help="path where the normalized sportsbook snapshot JSON is written",
    )

    merge_parser = data_subparsers.add_parser(
        "merge-snapshots",
        help="merge normalized market snapshots from multiple platforms",
    )
    merge_parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="snapshot JSON files to merge",
    )
    merge_parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="path where the merged snapshot JSON is written",
    )

    artifact_parser = subparsers.add_parser("artifact", help="inspect run artifacts")
    artifact_subparsers = artifact_parser.add_subparsers(dest="artifact_command")
    show_parser = artifact_subparsers.add_parser("show", help="print artifact JSON")
    show_parser.add_argument("path", type=Path, help="artifact path")

    db_parser = subparsers.add_parser("db", help="manage persistent DuckDB storage")
    db_subparsers = db_parser.add_subparsers(dest="db_command")
    db_init_parser = db_subparsers.add_parser("init", help="create DuckDB schema")
    db_init_parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"DuckDB database path; default {DEFAULT_DB_PATH}",
    )
    db_import_parser = db_subparsers.add_parser(
        "import-run",
        help="import a run artifact into DuckDB",
    )
    db_import_parser.add_argument("artifact", type=Path, help="run artifact JSON")
    db_import_parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"DuckDB database path; default {DEFAULT_DB_PATH}",
    )
    db_status_parser = db_subparsers.add_parser(
        "status",
        help="summarize persisted runs and table counts",
    )
    db_status_parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"DuckDB database path; default {DEFAULT_DB_PATH}",
    )

    ledger_parser = subparsers.add_parser(
        "ledger",
        help="inspect and settle the paper bet ledger",
    )
    ledger_subparsers = ledger_parser.add_subparsers(dest="ledger_command")
    ledger_status_parser = ledger_subparsers.add_parser(
        "status",
        help="summarize open and settled paper bets",
    )
    ledger_status_parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"DuckDB database path; default {DEFAULT_DB_PATH}",
    )
    ledger_settle_parser = ledger_subparsers.add_parser(
        "settle",
        help="manually settle a paper bet as won, lost, or push",
    )
    ledger_settle_parser.add_argument(
        "--client-order-id",
        required=True,
        help="paper bet client_order_id to settle",
    )
    ledger_settle_parser.add_argument(
        "--outcome",
        required=True,
        choices=["won", "lost", "push"],
        help="settlement result",
    )
    ledger_settle_parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"DuckDB database path; default {DEFAULT_DB_PATH}",
    )
    ledger_settle_market_parser = ledger_subparsers.add_parser(
        "settle-market",
        help="settle every open paper bet for a market by winning outcome",
    )
    ledger_settle_market_parser.add_argument(
        "--market-id",
        required=True,
        help="market_id whose open paper bets should be settled",
    )
    ledger_settle_market_parser.add_argument(
        "--winning-outcome-id",
        default=None,
        help="outcome_id that won; all other open outcomes on the market lose",
    )
    ledger_settle_market_parser.add_argument(
        "--push",
        action="store_true",
        help="settle all open paper bets on the market as push",
    )
    ledger_settle_market_parser.add_argument(
        "--venue",
        default=None,
        help="optional venue filter such as paper_sportsbook",
    )
    ledger_settle_market_parser.add_argument(
        "--product-type",
        default=None,
        help="optional product type filter such as sportsbook",
    )
    ledger_settle_market_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="maximum open bets to settle",
    )
    ledger_settle_market_parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"DuckDB database path; default {DEFAULT_DB_PATH}",
    )
    ledger_sync_kalshi_parser = ledger_subparsers.add_parser(
        "sync-kalshi",
        help="read Kalshi market results and settle matching open paper bets",
    )
    ledger_sync_kalshi_parser.add_argument(
        "--base-url",
        default=None,
        help="Kalshi API base URL; defaults to production market data",
    )
    ledger_sync_kalshi_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="maximum open Kalshi paper bets to check",
    )
    ledger_sync_kalshi_parser.add_argument(
        "--include-determined",
        action="store_true",
        help="also settle markets with determined/amended status before finalization",
    )
    ledger_sync_kalshi_parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"DuckDB database path; default {DEFAULT_DB_PATH}",
    )
    ledger_sync_sportsbook_parser = ledger_subparsers.add_parser(
        "sync-sportsbook",
        help="read The Odds API scores and settle matching sportsbook paper bets",
    )
    ledger_sync_sportsbook_parser.add_argument(
        "--sport",
        required=True,
        help="The Odds API sport key used when odds were fetched",
    )
    ledger_sync_sportsbook_parser.add_argument(
        "--days-from",
        type=int,
        default=3,
        help="completed-score lookback in days; The Odds API accepts 1 to 3",
    )
    ledger_sync_sportsbook_parser.add_argument(
        "--base-url",
        default=None,
        help="The Odds API base URL; defaults to THE_ODDS_API_BASE_URL or v4",
    )
    ledger_sync_sportsbook_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="maximum open sportsbook paper bets to check",
    )
    ledger_sync_sportsbook_parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"DuckDB database path; default {DEFAULT_DB_PATH}",
    )

    eval_parser = subparsers.add_parser(
        "eval",
        help="summarize forecast and paper-bet evaluation metrics",
    )
    eval_subparsers = eval_parser.add_subparsers(dest="eval_command")
    eval_summary_parser = eval_subparsers.add_parser(
        "summary",
        help="summarize diagnostics and paper bets by venue",
    )
    eval_summary_parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"DuckDB database path; default {DEFAULT_DB_PATH}",
    )
    eval_calibration_parser = eval_subparsers.add_parser(
        "calibration",
        help="summarize settled paper-bet calibration and EV bucket performance",
    )
    eval_calibration_parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"DuckDB database path; default {DEFAULT_DB_PATH}",
    )

    live_parser = subparsers.add_parser(
        "live",
        help="read-only live account checks and reconciliation",
    )
    live_subparsers = live_parser.add_subparsers(dest="live_command")
    live_preflight_parser = live_subparsers.add_parser(
        "preflight-kalshi",
        help="verify Kalshi credentials, balance, positions, and resting orders",
    )
    live_preflight_parser.add_argument(
        "--base-url",
        default=None,
        help="Kalshi API base URL; defaults to KALSHI_BASE_URL or demo",
    )
    live_reconcile_parser = live_subparsers.add_parser(
        "reconcile-kalshi",
        help="print current Kalshi orders and positions for reconciliation",
    )
    live_reconcile_parser.add_argument(
        "--base-url",
        default=None,
        help="Kalshi API base URL; defaults to KALSHI_BASE_URL or demo",
    )
    live_reconcile_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="maximum orders/positions to fetch",
    )

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="run local safety and namespace checks",
    )
    doctor_subparsers = doctor_parser.add_subparsers(dest="doctor_command")
    namespace_parser = doctor_subparsers.add_parser(
        "namespace",
        help="check that Restless Gambler does not conflict with MarketForge",
    )
    namespace_parser.add_argument(
        "--marketforge-root",
        type=Path,
        default=Path.home() / "marketforge",
        help="path to the MarketForge repository",
    )

    credentials_parser = subparsers.add_parser(
        "credentials",
        help="validate configured platform credentials",
    )
    credentials_subparsers = credentials_parser.add_subparsers(
        dest="credentials_command"
    )
    kalshi_credentials_parser = credentials_subparsers.add_parser(
        "check-kalshi",
        help="make a signed read-only Kalshi balance request",
    )
    kalshi_credentials_parser.add_argument(
        "--base-url",
        default=None,
        help="Kalshi API base URL to validate against",
    )

    return parser


def run_command(args) -> int:
    if args.mode == "live" and args.allow_snapshot_venues:
        print(
            json.dumps(
                {
                    "error": (
                        "--allow-snapshot-venues is only available in "
                        "paper/research mode"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1

    allowed_venues = _allowed_venues_from_args(args)
    config = load_config(
        mode=args.mode,
        as_of=args.as_of,
        markets_path=args.markets_path,
        artifacts_dir=args.artifacts_dir,
        min_expected_value=args.min_expected_value,
        min_liquidity=args.min_liquidity,
        max_wager_cost=args.max_wager_cost,
        max_units_per_wager=args.max_units_per_wager,
        allowed_venues=allowed_venues,
        confirm_live=args.confirm_live,
    )
    run_id = build_run_id(config.mode, config.strategy.name, config.as_of)
    duplicate_guard_enabled = (
        args.persist or args.avoid_open_ledger
    ) and not args.allow_duplicate_open_ledger
    blocked_wagers = (
        open_ledger_wager_keys(db_path=args.db_path, exclude_run_id=run_id)
        if duplicate_guard_enabled
        else set()
    )
    exposure = (
        open_ledger_exposure(db_path=args.db_path, exclude_run_id=run_id)
        if duplicate_guard_enabled
        else {"total_cost": 0.0, "by_market": {}}
    )
    artifact_path = RestlessGamblerRunner(
        config,
        blocked_wagers=blocked_wagers,
        existing_total_exposure=float(exposure["total_cost"]),
        existing_market_exposure={
            str(market_id): float(cost)
            for market_id, cost in dict(exposure["by_market"]).items()
        },
    ).run()
    print(artifact_path)
    if args.persist:
        summary = import_run_artifact(
            artifact_path=artifact_path,
            db_path=args.db_path,
        )
        print(json.dumps(summary.to_dict(), indent=2, sort_keys=True))
    return 0


def _allowed_venues_from_args(args) -> tuple[str, ...] | None:
    extra_venues = {venue for venue in (args.allowed_venues or []) if venue}
    if args.allow_snapshot_venues:
        extra_venues.update(_snapshot_venues(args.markets_path))
    if not extra_venues:
        return None
    return tuple(sorted(set(DEFAULT_ALLOWED_VENUES) | extra_venues))


def _snapshot_venues(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    markets = payload.get("markets", [])
    if not isinstance(markets, list):
        return set()
    return {
        str(market["venue"])
        for market in markets
        if isinstance(market, dict) and market.get("venue")
    }


def cycle_command(args) -> int:
    warnings: list[str] = []
    try:
        kalshi_fetch = fetch_kalshi_market_data(
            max_markets=args.kalshi_limit,
            include_orderbooks=not args.skip_orderbooks,
            orderbook_depth=args.orderbook_depth,
        )
        kalshi_path = write_kalshi_market_snapshot(
            output_path=args.kalshi_output,
            fetch=kalshi_fetch,
        )
        sports_fetch = fetch_sports_odds(
            sport=args.sport,
            regions=args.regions,
            markets=args.markets,
            bookmakers=args.bookmakers,
        )
        sportsbook_path = write_sports_odds_snapshot(
            output_path=args.sportsbook_output,
            fetch=sports_fetch,
        )
        merged_path = merge_market_snapshot_files(
            input_paths=[kalshi_path, sportsbook_path],
            output_path=args.merged_output,
        )
        allowed_venues = tuple(
            sorted(set(DEFAULT_ALLOWED_VENUES) | _snapshot_venues(merged_path))
        )
        config = load_config(
            mode="paper",
            as_of=args.as_of,
            markets_path=merged_path,
            artifacts_dir=args.artifacts_dir,
            min_expected_value=args.min_expected_value,
            min_liquidity=args.min_liquidity,
            max_wager_cost=args.max_wager_cost,
            max_units_per_wager=args.max_units_per_wager,
            allowed_venues=allowed_venues,
        )
        run_id = build_run_id(config.mode, config.strategy.name, config.as_of)
        duplicate_guard_enabled = not args.allow_duplicate_open_ledger
        blocked_wagers = (
            open_ledger_wager_keys(db_path=args.db_path, exclude_run_id=run_id)
            if duplicate_guard_enabled
            else set()
        )
        exposure = (
            open_ledger_exposure(db_path=args.db_path, exclude_run_id=run_id)
            if duplicate_guard_enabled
            else {"total_cost": 0.0, "by_market": {}}
        )
        artifact_path = RestlessGamblerRunner(
            config,
            blocked_wagers=blocked_wagers,
            existing_total_exposure=float(exposure["total_cost"]),
            existing_market_exposure={
                str(market_id): float(cost)
                for market_id, cost in dict(exposure["by_market"]).items()
            },
        ).run()
        import_summary = import_run_artifact(
            artifact_path=artifact_path,
            db_path=args.db_path,
        )
    except (OSError, ValueError) as error:
        print(json.dumps({"error": str(error)}, indent=2, sort_keys=True))
        return 1

    settlement_sync: dict[str, object] = {}
    if not args.skip_settlement_sync:
        try:
            kalshi_sync = sync_kalshi_paper_settlements(
                db_path=args.db_path,
                limit=100,
            )
            settlement_sync["kalshi"] = kalshi_sync.to_dict()
            if kalshi_sync.errors:
                warnings.append(
                    f"Kalshi settlement sync had {len(kalshi_sync.errors)} error(s)"
                )
        except (OSError, ValueError) as error:
            warnings.append(f"Kalshi settlement sync skipped: {error}")
        if args.sport != "upcoming":
            try:
                sportsbook_sync = sync_sportsbook_paper_settlements(
                    db_path=args.db_path,
                    sport=args.sport,
                    days_from=args.settlement_days_from,
                    limit=100,
                )
                settlement_sync["sportsbook"] = sportsbook_sync.to_dict()
                if sportsbook_sync.errors:
                    warnings.append(
                        "Sportsbook settlement sync had "
                        f"{len(sportsbook_sync.errors)} error(s)"
                    )
            except (OSError, ValueError) as error:
                warnings.append(f"Sportsbook settlement sync skipped: {error}")

    payload = {
        "cycle": {
            "sport": args.sport,
            "regions": args.regions,
            "markets": args.markets,
        },
        "snapshots": {
            "kalshi": str(kalshi_path),
            "sportsbook": str(sportsbook_path),
            "merged": str(merged_path),
        },
        "run": {
            "artifact_path": str(artifact_path),
            "import": import_summary.to_dict(),
        },
        "settlement_sync": settlement_sync,
        "db": summarize_database(args.db_path),
        "ledger": ledger_status(args.db_path),
        "calibration": calibration_summary(args.db_path),
        "warnings": warnings,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def dashboard_command(args) -> int:
    dashboard_path = Path(__file__).with_name("dashboard.py")
    return subprocess.call(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(dashboard_path),
            "--server.address",
            args.host,
            "--server.port",
            str(args.port),
            "--server.headless",
            "true",
            "--",
            "--db-path",
            str(args.db_path),
        ]
    )


def fetch_kalshi_command(args) -> int:
    fetch = fetch_kalshi_market_data(
        base_url=args.base_url,
        max_markets=args.limit,
        include_orderbooks=not args.skip_orderbooks,
        orderbook_depth=args.orderbook_depth,
    )
    output_path = write_kalshi_market_snapshot(
        output_path=args.output,
        fetch=fetch,
    )
    print(output_path)
    if fetch.warnings:
        print(json.dumps({"warnings": fetch.warnings}, indent=2, sort_keys=True))
    return 0


def fetch_odds_command(args) -> int:
    fetch = fetch_sports_odds(
        sport=args.sport,
        regions=args.regions,
        markets=args.markets,
        bookmakers=args.bookmakers,
    )
    output_path = write_sports_odds_snapshot(
        output_path=args.output,
        fetch=fetch,
    )
    print(output_path)
    if fetch.warnings:
        print(json.dumps({"warnings": fetch.warnings}, indent=2, sort_keys=True))
    return 0


def merge_snapshots_command(args) -> int:
    output_path = merge_market_snapshot_files(
        input_paths=args.inputs,
        output_path=args.output,
    )
    print(output_path)
    return 0


def show_artifact_command(args) -> int:
    payload = json.loads(args.path.read_text(encoding="utf-8"))
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def db_init_command(args) -> int:
    path = init_database(args.db_path)
    print(path)
    return 0


def db_import_run_command(args) -> int:
    summary = import_run_artifact(
        artifact_path=args.artifact,
        db_path=args.db_path,
    )
    print(json.dumps(summary.to_dict(), indent=2, sort_keys=True))
    return 0


def db_status_command(args) -> int:
    print(json.dumps(summarize_database(args.db_path), indent=2, sort_keys=True))
    return 0


def ledger_status_command(args) -> int:
    print(json.dumps(ledger_status(args.db_path), indent=2, sort_keys=True))
    return 0


def ledger_settle_command(args) -> int:
    result = settle_paper_bet(
        client_order_id=args.client_order_id,
        outcome=args.outcome,
        db_path=args.db_path,
    )
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0


def ledger_settle_market_command(args) -> int:
    try:
        summary = settle_market_paper_bets(
            db_path=args.db_path,
            market_id=args.market_id,
            winning_outcome_id=args.winning_outcome_id,
            push=args.push,
            venue=args.venue,
            product_type=args.product_type,
            limit=args.limit,
        )
    except ValueError as error:
        print(json.dumps({"error": str(error)}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(summary.to_dict(), indent=2, sort_keys=True))
    return 0


def ledger_sync_kalshi_command(args) -> int:
    summary = sync_kalshi_paper_settlements(
        db_path=args.db_path,
        base_url=args.base_url,
        limit=args.limit,
        allow_determined=args.include_determined,
    )
    print(json.dumps(summary.to_dict(), indent=2, sort_keys=True))
    return 0 if not summary.errors else 1


def ledger_sync_sportsbook_command(args) -> int:
    try:
        summary = sync_sportsbook_paper_settlements(
            db_path=args.db_path,
            sport=args.sport,
            days_from=args.days_from,
            base_url=args.base_url,
            limit=args.limit,
        )
    except (OSError, ValueError) as error:
        print(json.dumps({"error": str(error)}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(summary.to_dict(), indent=2, sort_keys=True))
    return 0 if not summary.errors else 1


def eval_summary_command(args) -> int:
    print(json.dumps(evaluation_summary(args.db_path), indent=2, sort_keys=True))
    return 0


def eval_calibration_command(args) -> int:
    print(json.dumps(calibration_summary(args.db_path), indent=2, sort_keys=True))
    return 0


def live_preflight_kalshi_command(args) -> int:
    credential_check = check_kalshi_credentials(base_url=args.base_url)
    account = (
        kalshi_account_snapshot(base_url=args.base_url)
        if credential_check.ok
        else None
    )
    payload = {
        "credential_check": credential_check.to_dict(),
        "account": account.to_dict() if account else None,
        "live_trading_enabled": (
            "set RG_LIVE_TRADING_ENABLED=true and pass run --confirm-live "
            "before order placement"
        ),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if credential_check.ok and (account is None or account.ok) else 1


def live_reconcile_kalshi_command(args) -> int:
    try:
        orders = fetch_kalshi_orders(
            base_url=args.base_url,
            limit=args.limit,
        )
        positions = fetch_kalshi_positions(
            base_url=args.base_url,
            limit=args.limit,
        )
    except (OSError, ValueError) as error:
        print(json.dumps({"error": str(error)}, indent=2, sort_keys=True))
        return 1

    print(
        json.dumps(
            {
                "orders": orders,
                "positions": positions,
                "order_count": len(orders),
                "position_count": len(positions),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def doctor_namespace_command(args) -> int:
    report = namespace_report(marketforge_root=args.marketforge_root)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


def check_kalshi_command(args) -> int:
    result = check_kalshi_credentials(base_url=args.base_url)
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
