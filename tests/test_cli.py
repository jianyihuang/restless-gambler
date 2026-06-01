from __future__ import annotations

import json
from pathlib import Path

from restless_gambler.cli import main
from restless_gambler.domain import Market, OutcomeQuote
from restless_gambler.kalshi import KalshiMarketDataFetch
from restless_gambler.settlement import SettlementSyncSummary
from restless_gambler.sports_odds import SportsOddsFetch


def test_cli_run_prints_artifact_path(tmp_path, capsys):
    result = main(
        [
            "run",
            "--mode",
            "paper",
            "--as-of",
            "2026-05-31",
            "--artifacts-dir",
            str(tmp_path),
        ]
    )
    output = capsys.readouterr().out.strip()

    assert result == 0
    assert output
    assert Path(output).exists()


def test_cli_run_allows_snapshot_venues_for_paper_only(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("RG_KILL_SWITCH", "false")
    snapshot_path = tmp_path / "sportsbook_snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "source": "test_sportsbook_snapshot",
                "generated_at": "2026-05-31T00:00:00Z",
                "markets": [
                    {
                        "market_id": "TEST-CBB-ARK-LIB-ML",
                        "event_id": "TEST-CBB-ARK-LIB",
                        "venue": "draftkings",
                        "product_type": "sportsbook",
                        "title": "Arkansas Razorbacks at Liberty Flames moneyline",
                        "category": "basketball",
                        "status": "open",
                        "close_time": "2026-06-01T23:00:00Z",
                        "liquidity": 100.0,
                        "volume": 100.0,
                        "rules_summary": "Test sportsbook fixture.",
                        "outcomes": [
                            {
                                "outcome_id": "liberty",
                                "name": "Liberty Flames",
                                "price": 100,
                                "price_format": "american",
                                "metadata": {"baseline_adjustment": 0.1},
                            },
                            {
                                "outcome_id": "arkansas",
                                "name": "Arkansas Razorbacks",
                                "price": -110,
                                "price_format": "american",
                                "metadata": {"baseline_adjustment": -0.02},
                            },
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = main(
        [
            "run",
            "--mode",
            "paper",
            "--as-of",
            "2026-05-31",
            "--markets-path",
            str(snapshot_path),
            "--artifacts-dir",
            str(tmp_path / "artifacts"),
            "--min-liquidity",
            "0",
            "--max-contracts",
            "1",
            "--max-order-cost",
            "1",
            "--allow-snapshot-venues",
        ]
    )
    output = capsys.readouterr().out.strip()
    artifact_path = Path(output)
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))

    assert result == 0
    assert payload["bets"]
    assert payload["bets"][0]["venue"] == "draftkings"
    assert payload["risk_decisions"][0]["status"] == "approved"
    assert "draftkings" in payload["config"]["risk"]["allowed_venues"]


def test_cli_run_rejects_snapshot_venues_in_live(capsys):
    result = main(["run", "--mode", "live", "--allow-snapshot-venues"])
    output = capsys.readouterr().out

    assert result == 1
    assert "--allow-snapshot-venues" in output


def test_cli_cycle_runs_paper_workflow(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("RG_KILL_SWITCH", "false")

    def fake_fetch_kalshi_market_data(**kwargs):
        assert kwargs["max_markets"] == 10
        return KalshiMarketDataFetch(
            markets=[],
            raw_market_count=0,
            base_url="https://kalshi.example.test",
            generated_at="2026-05-31T00:00:00Z",
            warnings=[],
        )

    def fake_fetch_sports_odds(**kwargs):
        assert kwargs["sport"] == "baseball_mlb"
        assert kwargs["markets"] == "h2h,spreads,totals"
        return SportsOddsFetch(
            markets=[
                Market(
                    market_id="draftkings-event-1-h2h",
                    event_id="event-1",
                    venue="draftkings",
                    product_type="sportsbook",
                    title="Liberty Flames at Arkansas Razorbacks h2h",
                    category="baseball_mlb",
                    status="open",
                    close_time="2026-06-01T23:00:00Z",
                    liquidity=100.0,
                    volume=100.0,
                    rules_summary="Test sportsbook fixture.",
                    outcomes=[
                        OutcomeQuote(
                            outcome_id="liberty-flames",
                            name="Liberty Flames",
                            price=100.0,
                            price_format="american",
                            implied_probability=0.5,
                            metadata={
                                "baseline_adjustment": 0.1,
                                "raw_name": "Liberty Flames",
                                "market_key": "h2h",
                            },
                        ),
                        OutcomeQuote(
                            outcome_id="arkansas-razorbacks",
                            name="Arkansas Razorbacks",
                            price=-110.0,
                            price_format="american",
                            implied_probability=0.5238,
                            metadata={
                                "baseline_adjustment": -0.02,
                                "raw_name": "Arkansas Razorbacks",
                                "market_key": "h2h",
                            },
                        ),
                    ],
                )
            ],
            raw_event_count=1,
            base_url="https://api.example.test/v4",
            sport="baseball_mlb",
            regions="us",
            markets_requested="h2h,spreads,totals",
            generated_at="2026-05-31T00:00:00Z",
            warnings=[],
        )

    def fake_sync_kalshi_paper_settlements(**kwargs):
        return SettlementSyncSummary(
            db_path=str(kwargs["db_path"]),
            venue="kalshi",
            checked=0,
            settled=0,
            open_or_unresolved=0,
            errors=[],
            settlements=[],
        )

    def fake_sync_sportsbook_paper_settlements(**kwargs):
        return SettlementSyncSummary(
            db_path=str(kwargs["db_path"]),
            venue=f"sportsbook:{kwargs['sport']}",
            checked=1,
            settled=0,
            open_or_unresolved=1,
            errors=[],
            settlements=[],
        )

    monkeypatch.setattr(
        "restless_gambler.cli.fetch_kalshi_market_data",
        fake_fetch_kalshi_market_data,
    )
    monkeypatch.setattr(
        "restless_gambler.cli.fetch_sports_odds",
        fake_fetch_sports_odds,
    )
    monkeypatch.setattr(
        "restless_gambler.cli.sync_kalshi_paper_settlements",
        fake_sync_kalshi_paper_settlements,
    )
    monkeypatch.setattr(
        "restless_gambler.cli.sync_sportsbook_paper_settlements",
        fake_sync_sportsbook_paper_settlements,
    )

    result = main(
        [
            "cycle",
            "--as-of",
            "2026-05-31",
            "--kalshi-limit",
            "10",
            "--kalshi-output",
            str(tmp_path / "kalshi.json"),
            "--sportsbook-output",
            str(tmp_path / "sportsbook.json"),
            "--merged-output",
            str(tmp_path / "merged.json"),
            "--artifacts-dir",
            str(tmp_path / "runs"),
            "--db-path",
            str(tmp_path / "restless.duckdb"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["cycle"]["sport"] == "baseball_mlb"
    assert "-cycle-" in payload["run"]["import"]["run_id"]
    assert payload["run"]["import"]["counts"]["bets"] == 1
    assert payload["line_sync"]["matched"] == 1
    assert payload["closing_lines"]["overall"]["tracked_count"] == 1
    assert payload["settlement_sync"]["sportsbook"]["checked"] == 1
    assert Path(payload["snapshots"]["merged"]).exists()
