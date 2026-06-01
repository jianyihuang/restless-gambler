from __future__ import annotations

import json
from pathlib import Path

from restless_gambler.cli import main


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
