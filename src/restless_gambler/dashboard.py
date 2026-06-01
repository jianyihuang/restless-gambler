from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import streamlit as st

from restless_gambler.persistence import (
    DEFAULT_DB_PATH,
    calibration_summary,
    evaluation_summary,
    init_database,
    ledger_status,
    open_ledger_exposure,
    summarize_database,
)


def main() -> None:
    args = _parse_args()
    st.set_page_config(
        page_title="Restless Gambler",
        page_icon="RG",
        layout="wide",
    )

    db_path = Path(
        st.sidebar.text_input("DuckDB path", value=str(args.db_path))
    ).expanduser()
    if st.sidebar.button("Refresh"):
        st.cache_data.clear()

    data = load_dashboard_data(str(db_path))
    st.title("Restless Gambler")
    st.caption(f"Paper trading dashboard | {db_path}")

    render_metrics(data)

    (
        overview_tab,
        cycle_tab,
        bets_tab,
        calibration_tab,
        activity_tab,
        runs_tab,
        markets_tab,
    ) = st.tabs(
        ["Overview", "Cycle", "Bets", "Calibration", "Activity", "Runs", "Markets"]
    )
    with overview_tab:
        render_overview(data)
    with cycle_tab:
        render_cycle(data)
    with bets_tab:
        render_bets(data["bets"])
    with calibration_tab:
        render_calibration(data["calibration"])
    with activity_tab:
        render_activity(data)
    with runs_tab:
        render_runs(data["runs"])
    with markets_tab:
        render_markets(data)


@st.cache_data(ttl=5)
def load_dashboard_data(db_path_value: str) -> dict[str, Any]:
    db_path = Path(db_path_value).expanduser()
    if not db_path.exists():
        init_database(db_path)

    return {
        "summary": summarize_database(db_path),
        "ledger": ledger_status(db_path),
        "evaluation": evaluation_summary(db_path),
        "calibration": calibration_summary(db_path),
        "exposure": open_ledger_exposure(db_path=db_path),
        "latest_run": _query(db_path, LATEST_RUN_SQL),
        "runs": _query(db_path, RUNS_SQL),
        "bets": _query(db_path, BETS_SQL),
        "executions": _query(db_path, EXECUTIONS_SQL),
        "risk_decisions": _query(db_path, RISK_DECISIONS_SQL),
        "research_signals": _query_optional(db_path, RESEARCH_SIGNALS_SQL),
        "opportunities": _query(db_path, OPPORTUNITIES_SQL),
        "diagnostics": _query(db_path, DIAGNOSTICS_SQL),
        "markets": _query(db_path, MARKETS_SQL),
        "outcomes": _query(db_path, OUTCOMES_SQL),
        "positions": _query(db_path, POSITIONS_SQL),
        "exposure_by_market": _query(db_path, EXPOSURE_SQL),
    }


def render_metrics(data: dict[str, Any]) -> None:
    table_counts = data["summary"]["table_counts"]
    ledger_rows = {
        row["settlement_status"]: row for row in data["ledger"]["summary"]
    }
    open_row = ledger_rows.get("open", {})
    won_row = ledger_rows.get("won", {})
    lost_row = ledger_rows.get("lost", {})
    push_row = ledger_rows.get("push", {})
    settled_count = sum(
        int(row.get("bet_count", 0)) for row in (won_row, lost_row, push_row)
    )
    realized_pnl = sum(
        float(row.get("realized_pnl", 0.0))
        for row in (open_row, won_row, lost_row, push_row)
    )

    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("Runs", table_counts.get("runs", 0))
    col2.metric("Paper Bets", table_counts.get("paper_bet_ledger", 0))
    col3.metric("Open Bets", open_row.get("bet_count", 0))
    col4.metric("Settled", settled_count)
    col5.metric("Open Cost", _money(open_row.get("total_cost", 0.0)))
    col6.metric("Realized PnL", _money(realized_pnl))


def render_overview(data: dict[str, Any]) -> None:
    left, right = st.columns([2, 1])
    with left:
        st.subheader("Latest Runs")
        st.dataframe(data["runs"], use_container_width=True, hide_index=True)
    with right:
        st.subheader("Ledger Summary")
        st.dataframe(
            data["ledger"]["summary"],
            use_container_width=True,
            hide_index=True,
        )

    exposure_frame = data["exposure_by_market"]
    if not exposure_frame.empty:
        st.subheader("Open Exposure By Market")
        st.bar_chart(exposure_frame, x="market_id", y="open_cost")

    st.subheader("Evaluation By Venue")
    st.dataframe(
        data["evaluation"]["paper_bets_by_venue"],
        use_container_width=True,
        hide_index=True,
    )


def render_bets(bets) -> None:
    if bets.empty:
        st.info("No paper bets are persisted yet.")
        return

    filters = st.columns(3)
    statuses = filters[0].multiselect(
        "Status",
        sorted(bets["settlement_status"].dropna().unique()),
        default=sorted(bets["settlement_status"].dropna().unique()),
    )
    venues = filters[1].multiselect(
        "Venue",
        sorted(bets["venue"].dropna().unique()),
        default=sorted(bets["venue"].dropna().unique()),
    )
    product_types = filters[2].multiselect(
        "Product",
        sorted(bets["product_type"].dropna().unique()),
        default=sorted(bets["product_type"].dropna().unique()),
    )

    filtered = bets[
        bets["settlement_status"].isin(statuses)
        & bets["venue"].isin(venues)
        & bets["product_type"].isin(product_types)
    ]
    st.dataframe(filtered, use_container_width=True, hide_index=True)


def render_cycle(data: dict[str, Any]) -> None:
    st.subheader("Latest Cycle State")
    st.dataframe(data["latest_run"], use_container_width=True, hide_index=True)

    rejected = data["risk_decisions"]
    if not rejected.empty:
        rejected = rejected[rejected["status"] == "rejected"]
    st.subheader("Risk Rejections")
    st.dataframe(rejected, use_container_width=True, hide_index=True)

    st.subheader("Open Ledger")
    st.dataframe(data["ledger"]["open_bets"], use_container_width=True, hide_index=True)


def render_calibration(calibration: dict[str, Any]) -> None:
    overall = calibration["overall"]
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Settled", overall["settled_count"])
    col2.metric("Graded", overall["graded_count"])
    col3.metric("Hit Rate", _percent(overall["hit_rate"]))
    col4.metric("Brier", _number(overall["brier_score"]))

    st.subheader("By Venue")
    st.dataframe(calibration["by_venue"], use_container_width=True, hide_index=True)
    st.subheader("By EV Bucket")
    st.dataframe(
        calibration["by_expected_value_bucket"],
        use_container_width=True,
        hide_index=True,
    )


def render_activity(data: dict[str, Any]) -> None:
    st.subheader("Research Signals")
    st.dataframe(data["research_signals"], use_container_width=True, hide_index=True)
    st.subheader("Risk Decisions")
    st.dataframe(data["risk_decisions"], use_container_width=True, hide_index=True)
    st.subheader("Executions")
    st.dataframe(data["executions"], use_container_width=True, hide_index=True)
    st.subheader("Opportunities")
    st.dataframe(data["opportunities"], use_container_width=True, hide_index=True)
    st.subheader("Diagnostics")
    st.dataframe(data["diagnostics"], use_container_width=True, hide_index=True)


def render_runs(runs) -> None:
    st.dataframe(runs, use_container_width=True, hide_index=True)


def render_markets(data: dict[str, Any]) -> None:
    st.subheader("Markets")
    st.dataframe(data["markets"], use_container_width=True, hide_index=True)
    st.subheader("Outcome Quotes")
    st.dataframe(data["outcomes"], use_container_width=True, hide_index=True)
    st.subheader("Latest Positions")
    st.dataframe(data["positions"], use_container_width=True, hide_index=True)


def _query(db_path: Path, sql: str):
    with duckdb.connect(str(db_path), read_only=True) as con:
        return con.execute(sql).fetchdf()


def _query_optional(db_path: Path, sql: str):
    try:
        return _query(db_path, sql)
    except duckdb.CatalogException:
        return pd.DataFrame()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    args, _ = parser.parse_known_args()
    return args


def _money(value: object) -> str:
    return f"${float(value or 0.0):,.2f}"


def _percent(value: object) -> str:
    if value is None:
        return "-"
    return f"{float(value):.1%}"


def _number(value: object) -> str:
    if value is None:
        return "-"
    return f"{float(value):.4f}"


LATEST_RUN_SQL = """
SELECT run_id, timestamp, runtime_mode, status, market_count, bet_count,
       data_source_json, warnings_json, errors_json, artifact_path, imported_at
FROM runs
ORDER BY timestamp DESC, imported_at DESC
LIMIT 1
"""


RUNS_SQL = """
SELECT run_id, timestamp, runtime_mode, status, market_count, bet_count,
       cash, equity, realized_pnl, imported_at
FROM runs
ORDER BY timestamp DESC, imported_at DESC
LIMIT 100
"""

BETS_SQL = """
SELECT client_order_id, settlement_status, venue, product_type, market_id,
       outcome_id, outcome_name, action, units, price, price_format, fee, cost,
       expected_value, payout, realized_pnl, filled_at, settled_at,
       first_run_id, latest_run_id
FROM paper_bet_ledger
ORDER BY COALESCE(settled_at, filled_at) DESC, client_order_id
LIMIT 1000
"""

EXECUTIONS_SQL = """
SELECT run_id, client_order_id, venue, product_type, market_id, outcome_id,
       outcome_name, action, units, limit_price, price_format, status,
       filled_units, average_fill_price, rejection_reason, submitted_at
FROM executions
ORDER BY submitted_at DESC, run_id DESC
LIMIT 1000
"""

RISK_DECISIONS_SQL = """
SELECT run_id, client_order_id, status, reason, checks_json
FROM risk_decisions
ORDER BY run_id DESC, status DESC, client_order_id
LIMIT 1000
"""

RESEARCH_SIGNALS_SQL = """
SELECT run_id, market_id, outcome_id, kind, name, direction, magnitude,
       confidence, source, rationale
FROM research_signals
ORDER BY run_id DESC, market_id, outcome_id, kind, name
LIMIT 1000
"""

OPPORTUNITIES_SQL = """
SELECT run_id, venue, product_type, market_id, outcome_id, outcome_name, action,
       fair_probability, entry_price, implied_probability, expected_value,
       edge_before_fees, max_units, reason
FROM opportunities
ORDER BY run_id DESC, expected_value DESC
LIMIT 1000
"""

DIAGNOSTICS_SQL = """
SELECT run_id, venue, product_type, market_id, outcome_id, outcome_name,
       fair_probability, implied_probability, entry_price, expected_value,
       min_expected_value, decision
FROM opportunity_diagnostics
ORDER BY run_id DESC, expected_value DESC
LIMIT 1000
"""

MARKETS_SQL = """
SELECT run_id, venue, product_type, market_id, event_id, title, category,
       status, close_time, liquidity, volume
FROM markets
ORDER BY run_id DESC, liquidity DESC
LIMIT 1000
"""

OUTCOMES_SQL = """
SELECT run_id, market_id, outcome_id, name, price, price_format,
       implied_probability, bid, ask
FROM outcome_quotes
ORDER BY run_id DESC, market_id, outcome_id
LIMIT 1000
"""

POSITIONS_SQL = """
SELECT run_id, market_id, outcome_id, outcome_name, product_type, units,
       average_price, price_format, mark_price, market_value
FROM positions
WHERE run_id = (
    SELECT run_id
    FROM runs
    ORDER BY timestamp DESC, imported_at DESC
    LIMIT 1
)
ORDER BY market_value DESC
LIMIT 1000
"""

EXPOSURE_SQL = """
SELECT market_id, venue, product_type, COUNT(*) AS open_bets,
       ROUND(SUM(cost), 2) AS open_cost,
       ROUND(SUM(expected_value * units), 4) AS expected_value_units
FROM paper_bet_ledger
WHERE settlement_status = 'open'
GROUP BY market_id, venue, product_type
ORDER BY open_cost DESC
LIMIT 100
"""


if __name__ == "__main__":
    main()
