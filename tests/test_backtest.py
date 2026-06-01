from __future__ import annotations

import json

import duckdb

from restless_gambler.cli import main
from restless_gambler.persistence import init_database, settled_backtest_summary


def test_settled_backtest_summary_uses_synthetic_settled_bets(tmp_path):
    db_path = tmp_path / "backtest.duckdb"
    _seed_settled_backtest_db(db_path)

    summary = settled_backtest_summary(db_path)

    assert summary["overall"]["settled_count"] == 3
    assert summary["overall"]["graded_count"] == 2
    assert summary["overall"]["hit_rate"] == 0.5
    assert summary["overall"]["realized_pnl"] == -2.0
    assert summary["overall"]["roi"] == -0.08
    assert summary["overall"]["expected_value_units"] == 0.14
    assert summary["overall"]["brier_score"] == 0.2313
    assert {
        row["expected_value_bucket"]
        for row in summary["by_expected_value_bucket"]
    } == {"<3%", "3-5%", "5-10%"}
    assert summary["calibration_by_probability_bucket"][0]["probability_bucket"] == (
        "50-60%"
    )
    assert summary["closing_line_value"] == {
        "tracked_count": 3,
        "average_implied_probability_delta": 0.006667,
        "positive_count": 1,
        "negative_count": 1,
        "unchanged_count": 1,
    }
    assert summary["warnings"] == []


def test_cli_eval_backtest_prints_summary(tmp_path, capsys):
    db_path = tmp_path / "backtest.duckdb"
    _seed_settled_backtest_db(db_path)

    result = main(["eval", "backtest", "--db-path", str(db_path)])
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["overall"]["settled_count"] == 3
    assert payload["closing_line_value"]["tracked_count"] == 3


def test_settled_backtest_summary_has_empty_state_warnings(tmp_path):
    db_path = tmp_path / "empty.duckdb"
    init_database(db_path)

    summary = settled_backtest_summary(db_path)

    assert summary["overall"]["settled_count"] == 0
    assert "no settled paper bets found" in summary["warnings"]
    assert "no closing-line snapshots found" in summary["warnings"][1]


def _seed_settled_backtest_db(db_path):
    init_database(db_path)
    ledger_rows = [
        (
            "bet-1",
            "run-1",
            "run-1",
            "draftkings",
            "sportsbook",
            "event-1-h2h",
            "home",
            "Home",
            "bet",
            1,
            110.0,
            "american",
            0.0,
            10.0,
            0.04,
            "2026-05-01T00:00:00Z",
            "won",
            18.0,
            8.0,
            "2026-05-02T00:00:00Z",
        ),
        (
            "bet-2",
            "run-1",
            "run-1",
            "fanduel",
            "sportsbook",
            "event-2-h2h",
            "away",
            "Away",
            "bet",
            1,
            -105.0,
            "american",
            0.0,
            10.0,
            0.08,
            "2026-05-01T00:00:00Z",
            "lost",
            0.0,
            -10.0,
            "2026-05-02T00:00:00Z",
        ),
        (
            "bet-3",
            "run-1",
            "run-1",
            "betmgm",
            "sportsbook",
            "event-3-h2h",
            "draw",
            "Draw",
            "bet",
            1,
            100.0,
            "american",
            0.0,
            5.0,
            0.02,
            "2026-05-01T00:00:00Z",
            "push",
            5.0,
            0.0,
            "2026-05-02T00:00:00Z",
        ),
        (
            "open-bet",
            "run-1",
            "run-1",
            "draftkings",
            "sportsbook",
            "event-4-h2h",
            "home",
            "Home",
            "bet",
            1,
            100.0,
            "american",
            0.0,
            5.0,
            0.05,
            "2026-05-01T00:00:00Z",
            "open",
            0.0,
            0.0,
            None,
        ),
    ]
    forecast_rows = [
        ("run-1", "event-1-h2h", "home", 0.60, 0.7, "fixture", "fixture"),
        ("run-1", "event-2-h2h", "away", 0.55, 0.7, "fixture", "fixture"),
        ("run-1", "event-3-h2h", "draw", 0.50, 0.7, "fixture", "fixture"),
        ("run-1", "event-4-h2h", "home", 0.55, 0.7, "fixture", "fixture"),
    ]
    line_rows = [
        (
            "bet-1",
            "2026-05-01T23:00:00Z",
            "fixture.json",
            "2026-05-01T22:00:00Z",
            "draftkings",
            "sportsbook",
            "event-1-h2h",
            "home",
            "Home",
            110.0,
            130.0,
            "american",
            0.50,
            0.54,
            0.04,
            "open",
            "2026-05-03T00:00:00Z",
        ),
        (
            "bet-2",
            "2026-05-01T23:00:00Z",
            "fixture.json",
            "2026-05-01T22:00:00Z",
            "fanduel",
            "sportsbook",
            "event-2-h2h",
            "away",
            "Away",
            -105.0,
            -100.0,
            "american",
            0.52,
            0.50,
            -0.02,
            "open",
            "2026-05-03T00:00:00Z",
        ),
        (
            "bet-3",
            "2026-05-01T23:00:00Z",
            "fixture.json",
            "2026-05-01T22:00:00Z",
            "betmgm",
            "sportsbook",
            "event-3-h2h",
            "draw",
            "Draw",
            100.0,
            100.0,
            "american",
            0.50,
            0.50,
            0.0,
            "open",
            "2026-05-03T00:00:00Z",
        ),
    ]

    with duckdb.connect(str(db_path)) as con:
        con.executemany(
            """
            INSERT INTO paper_bet_ledger VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            ledger_rows,
        )
        con.executemany(
            "INSERT INTO forecasts VALUES (?, ?, ?, ?, ?, ?, ?)",
            forecast_rows,
        )
        con.executemany(
            """
            INSERT INTO paper_line_snapshots VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            line_rows,
        )
