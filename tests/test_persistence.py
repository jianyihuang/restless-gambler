from __future__ import annotations

import json
from datetime import date

import duckdb

from restless_gambler.config import load_config
from restless_gambler.persistence import (
    calibration_summary,
    closing_line_summary,
    evaluation_summary,
    import_run_artifact,
    ledger_status,
    open_ledger_exposure,
    open_ledger_wager_keys,
    open_paper_bets,
    settle_paper_bet,
    summarize_database,
    sync_paper_bet_lines,
)
from restless_gambler.runner import RestlessGamblerRunner


def test_import_run_artifact_populates_duckdb_tables(tmp_path):
    artifact_path = RestlessGamblerRunner(
        load_config(
            mode="paper",
            as_of=date(2026, 5, 31),
            artifacts_dir=tmp_path / "runs",
            min_liquidity=0.0,
        )
    ).run()
    db_path = tmp_path / "restless.duckdb"

    summary = import_run_artifact(
        artifact_path=artifact_path,
        db_path=db_path,
    )
    db_summary = summarize_database(db_path)
    eval_summary = evaluation_summary(db_path)

    assert summary.run_id == "paper-baseline_cross_gambling_ev-20260531"
    assert summary.counts["markets"] == 4
    assert summary.counts["bets"] >= 1
    assert db_summary["table_counts"]["runs"] == 1
    assert db_summary["table_counts"]["bets"] == summary.counts["bets"]
    assert db_summary["table_counts"]["research_signals"] > 0
    assert eval_summary["diagnostics_by_venue"]
    assert open_paper_bets(db_path=db_path)
    assert open_ledger_wager_keys(db_path=db_path)
    exposure = open_ledger_exposure(db_path=db_path)
    assert exposure["total_cost"] > 0
    assert exposure["by_market"]

    with duckdb.connect(str(db_path), read_only=True) as con:
        run_row = con.execute(
            "SELECT runtime_mode, market_count, bet_count FROM runs"
        ).fetchone()
    assert run_row == ("paper", 4, summary.counts["bets"])


def test_ledger_status_and_manual_settlement(tmp_path):
    artifact_path = RestlessGamblerRunner(
        load_config(
            mode="paper",
            as_of=date(2026, 5, 31),
            artifacts_dir=tmp_path / "runs",
            min_liquidity=0.0,
        )
    ).run()
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    db_path = tmp_path / "restless.duckdb"
    import_run_artifact(artifact_path=artifact_path, db_path=db_path)

    before = ledger_status(db_path)
    first_bet_id = payload["bets"][0]["client_order_id"]
    result = settle_paper_bet(
        client_order_id=first_bet_id,
        outcome="won",
        db_path=db_path,
    )
    after = ledger_status(db_path)
    eval_summary = evaluation_summary(db_path)
    calibration = calibration_summary(db_path)
    bet_summary = next(
        row
        for row in eval_summary["paper_bets_by_venue"]
        if row["venue"] == payload["bets"][0]["venue"]
    )

    assert before["summary"][0]["settlement_status"] == "open"
    assert result.client_order_id == first_bet_id
    assert result.settlement_status == "won"
    assert result.payout > 0
    assert any(row["settlement_status"] == "won" for row in after["summary"])
    assert bet_summary["settled_count"] >= 1
    assert bet_summary["won_count"] >= 1
    assert bet_summary["hit_rate"] == 1.0
    assert calibration["overall"]["settled_count"] == 1
    assert calibration["overall"]["graded_count"] == 1
    assert calibration["overall"]["hit_rate"] == 1.0
    assert calibration["overall"]["brier_score"] is not None
    assert calibration["by_expected_value_bucket"]


def test_sync_paper_bet_lines_tracks_latest_prices(tmp_path):
    artifact_path = RestlessGamblerRunner(
        load_config(
            mode="paper",
            as_of=date(2026, 5, 31),
            artifacts_dir=tmp_path / "runs",
            min_liquidity=0.0,
        )
    ).run()
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    db_path = tmp_path / "restless.duckdb"
    import_run_artifact(artifact_path=artifact_path, db_path=db_path)

    first_bet = payload["bets"][0]
    snapshot = {
        "source": "test_latest_lines",
        "generated_at": "2026-06-01T00:00:00Z",
        "markets": payload["markets"],
    }
    for market in snapshot["markets"]:
        if market["market_id"] != first_bet["market_id"]:
            continue
        for outcome in market["outcomes"]:
            if outcome["outcome_id"] != first_bet["outcome_id"]:
                continue
            if outcome["price_format"] == "probability":
                outcome["price"] = min(0.99, float(outcome["price"]) + 0.05)
                outcome["ask"] = outcome["price"]
            elif float(outcome["price"]) > 0:
                outcome["price"] = max(100.0, float(outcome["price"]) - 20.0)
            else:
                outcome["price"] = float(outcome["price"]) - 20.0

    snapshot_path = tmp_path / "latest_lines.json"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    line_sync = sync_paper_bet_lines(
        markets_path=snapshot_path,
        db_path=db_path,
        checked_at="2026-06-01T00:01:00Z",
    )
    summary = closing_line_summary(db_path)

    assert line_sync.matched >= 1
    assert any(
        snapshot["client_order_id"] == first_bet["client_order_id"]
        for snapshot in line_sync.snapshots
    )
    assert summary["overall"]["tracked_count"] >= 1
    assert summary["latest"][0]["client_order_id"]


def test_live_run_import_does_not_populate_paper_ledger(tmp_path):
    artifact_path = RestlessGamblerRunner(
        load_config(
            mode="paper",
            as_of=date(2026, 5, 31),
            artifacts_dir=tmp_path / "runs",
            min_liquidity=0.0,
        )
    ).run()
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    payload["runtime_mode"] = "live"
    live_artifact_path = tmp_path / "live-run.json"
    live_artifact_path.write_text(json.dumps(payload), encoding="utf-8")
    db_path = tmp_path / "restless.duckdb"

    import_run_artifact(artifact_path=live_artifact_path, db_path=db_path)
    db_summary = summarize_database(db_path)

    assert db_summary["table_counts"]["bets"] == len(payload["bets"])
    assert db_summary["table_counts"]["paper_bet_ledger"] == 0
