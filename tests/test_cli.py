from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb

from restless_gambler.cli import main
from restless_gambler.domain import Market, OutcomeQuote
from restless_gambler.kalshi import (
    KalshiAccountSnapshot,
    KalshiCredentialCheck,
    KalshiMarketDataFetch,
)
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


def test_cli_live_plan_kalshi_prints_payload_without_order_placement(
    tmp_path,
    capsys,
    monkeypatch,
):
    monkeypatch.setenv("RG_KILL_SWITCH", "false")
    monkeypatch.setenv("RG_LIVE_TRADING_ENABLED", "true")
    monkeypatch.setattr(
        "restless_gambler.cli.check_kalshi_credentials",
        _successful_kalshi_credentials,
    )
    monkeypatch.setattr(
        "restless_gambler.cli.kalshi_account_snapshot",
        lambda base_url=None: _kalshi_account(resting_orders=[]),
    )
    monkeypatch.setattr(
        "restless_gambler.cli.fetch_kalshi_market_data",
        lambda **kwargs: _kalshi_plan_fetch(),
    )

    result = main(
        [
            "live",
            "plan-kalshi",
            "--base-url",
            "https://kalshi.example.test",
            "--snapshot-output",
            str(tmp_path / "kalshi_plan.json"),
            "--min-liquidity",
            "0",
            "--max-order-cost",
            "1",
            "--max-contracts",
            "1",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["readiness"]["approved_live_intent_count"] == 1
    assert payload["planned_orders"][0]["payload"]["ticker"] == "KXPLAN"
    assert payload["planned_orders"][0]["payload"]["post_only"] is True
    assert payload["order_placement"] == "not attempted; plan-kalshi is read-only"


def test_cli_live_plan_kalshi_rejects_existing_resting_orders(
    tmp_path,
    capsys,
    monkeypatch,
):
    monkeypatch.setenv("RG_KILL_SWITCH", "false")
    monkeypatch.setenv("RG_LIVE_TRADING_ENABLED", "true")
    monkeypatch.setattr(
        "restless_gambler.cli.check_kalshi_credentials",
        _successful_kalshi_credentials,
    )
    monkeypatch.setattr(
        "restless_gambler.cli.kalshi_account_snapshot",
        lambda base_url=None: _kalshi_account(resting_orders=[{"order_id": "1"}]),
    )
    monkeypatch.setattr(
        "restless_gambler.cli.fetch_kalshi_market_data",
        lambda **kwargs: _kalshi_plan_fetch(),
    )

    result = main(
        [
            "live",
            "plan-kalshi",
            "--snapshot-output",
            str(tmp_path / "kalshi_plan.json"),
            "--min-liquidity",
            "0",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["planned_orders"] == []
    assert any(
        guardrail["name"] == "resting_orders" and guardrail["status"] == "failed"
        for guardrail in payload["guardrails"]
    )
    assert payload["risk_decisions"][0]["reason"] == (
        "live guardrail failed: resting_orders"
    )


def test_cli_live_run_rejects_resting_orders_before_runner(
    tmp_path,
    capsys,
    monkeypatch,
):
    monkeypatch.setenv("RG_KILL_SWITCH", "false")
    monkeypatch.setenv("RG_LIVE_TRADING_ENABLED", "true")
    snapshot_path = tmp_path / "kalshi_live.json"
    snapshot_path.write_text(
        json.dumps(_kalshi_plan_fetch().snapshot_payload()),
        encoding="utf-8",
    )

    class FailingRunner:
        def __init__(self, *args, **kwargs):
            pass

        def run(self):
            raise AssertionError("runner should not run when guardrails fail")

    monkeypatch.setattr(
        "restless_gambler.cli.check_kalshi_credentials",
        _successful_kalshi_credentials,
    )
    monkeypatch.setattr(
        "restless_gambler.cli.kalshi_account_snapshot",
        lambda base_url=None: _kalshi_account(resting_orders=[{"order_id": "1"}]),
    )
    monkeypatch.setattr("restless_gambler.cli.RestlessGamblerRunner", FailingRunner)

    result = main(
        [
            "run",
            "--mode",
            "live",
            "--confirm-live",
            "--markets-path",
            str(snapshot_path),
            "--min-liquidity",
            "0",
            "--max-order-cost",
            "1",
            "--max-contracts",
            "1",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 1
    assert payload["ready"] is False
    assert payload["order_placement"] == "not attempted"
    assert any(
        guardrail["name"] == "resting_orders" and guardrail["status"] == "failed"
        for guardrail in payload["guardrails"]
    )


def test_cli_live_reconcile_kalshi_persists_snapshot(tmp_path, capsys, monkeypatch):
    db_path = tmp_path / "restless.duckdb"
    monkeypatch.setattr(
        "restless_gambler.cli.fetch_kalshi_orders",
        lambda **kwargs: [
            {
                "order_id": "order-1",
                "client_order_id": "client-1",
                "ticker": "KXPLAN",
                "status": "resting",
                "remaining_count_fp": "1.00",
            }
        ],
    )
    monkeypatch.setattr(
        "restless_gambler.cli.fetch_kalshi_positions",
        lambda **kwargs: [{"ticker": "KXPLAN", "position": "1"}],
    )

    result = main(
        [
            "live",
            "reconcile-kalshi",
            "--base-url",
            "https://kalshi.example.test",
            "--db-path",
            str(db_path),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["persisted"]["order_count"] == 1
    assert payload["persisted"]["position_count"] == 1
    with duckdb.connect(str(db_path), read_only=True) as con:
        order_count = con.execute(
            "SELECT COUNT(*) FROM kalshi_reconciliation_orders"
        ).fetchone()[0]
    assert order_count == 1


def test_cli_live_cancel_kalshi_order_defaults_to_dry_run(
    tmp_path,
    capsys,
    monkeypatch,
):
    db_path = tmp_path / "restless.duckdb"

    def fake_fetch_kalshi_orders(**kwargs):
        assert kwargs["status"] == "resting"
        return [{"order_id": "order-1", "ticker": "KXPLAN", "status": "resting"}]

    def fail_cancel(**kwargs):
        raise AssertionError("dry-run cancel should not call Kalshi DELETE")

    monkeypatch.setattr(
        "restless_gambler.cli.fetch_kalshi_orders",
        fake_fetch_kalshi_orders,
    )
    monkeypatch.setattr("restless_gambler.cli.cancel_kalshi_order", fail_cancel)

    result = main(
        [
            "live",
            "cancel-kalshi-order",
            "--base-url",
            "https://kalshi.example.test",
            "--order-id",
            "order-1",
            "--db-path",
            str(db_path),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["dry_run"] is True
    assert payload["confirmation_required"] == "--confirm-cancel"
    with duckdb.connect(str(db_path), read_only=True) as con:
        row = con.execute(
            """
            SELECT order_id, dry_run, confirmed, result_status
            FROM kalshi_cancel_requests
            """
        ).fetchone()
    assert row == ("order-1", True, False, "dry_run")


def test_cli_live_cancel_kalshi_order_requires_resting_order(
    tmp_path,
    capsys,
    monkeypatch,
):
    monkeypatch.setattr(
        "restless_gambler.cli.fetch_kalshi_orders",
        lambda **kwargs: [{"order_id": "other-order", "status": "resting"}],
    )

    result = main(
        [
            "live",
            "cancel-kalshi-order",
            "--order-id",
            "order-1",
            "--db-path",
            str(tmp_path / "restless.duckdb"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 1
    assert payload["result_status"] == "not_found"
    assert "resting Kalshi orders" in payload["error"]


def test_cli_live_cancel_kalshi_order_confirmed_calls_api(
    tmp_path,
    capsys,
    monkeypatch,
):
    db_path = tmp_path / "restless.duckdb"
    fetch_calls = []

    def fake_fetch_kalshi_orders(**kwargs):
        fetch_calls.append(kwargs.get("status"))
        if kwargs.get("status") == "resting":
            return [{"order_id": "order-1", "ticker": "KXPLAN", "status": "resting"}]
        return [{"order_id": "order-1", "ticker": "KXPLAN", "status": "canceled"}]

    monkeypatch.setattr(
        "restless_gambler.cli.fetch_kalshi_orders",
        fake_fetch_kalshi_orders,
    )
    monkeypatch.setattr(
        "restless_gambler.cli.fetch_kalshi_positions",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(
        "restless_gambler.cli.cancel_kalshi_order",
        lambda **kwargs: {
            "order": {
                "order_id": kwargs["order_id"],
                "ticker": "KXPLAN",
                "status": "canceled",
            },
            "reduced_by_fp": "1.00",
        },
    )

    result = main(
        [
            "live",
            "cancel-kalshi-order",
            "--order-id",
            "order-1",
            "--confirm-cancel",
            "--db-path",
            str(db_path),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert fetch_calls == ["resting", None]
    assert payload["dry_run"] is False
    assert payload["result_status"] == "canceled"
    assert payload["persisted_reconciliation"]["order_count"] == 1


def _successful_kalshi_credentials(base_url=None) -> KalshiCredentialCheck:
    return KalshiCredentialCheck(
        ok=True,
        status_code=200,
        base_url=base_url or "https://kalshi.example.test",
        endpoint="/portfolio/balance",
        key_id_present=True,
        private_key_path_present=True,
        private_key_file_exists=True,
        message="ok",
    )


def _kalshi_account(*, resting_orders: list[dict[str, object]]):
    return KalshiAccountSnapshot(
        ok=True,
        base_url="https://kalshi.example.test",
        balance={"balance_dollars": "12.00", "balance": 1200},
        resting_orders=resting_orders,
        market_positions=[],
        message="ok",
    )


def _kalshi_plan_fetch() -> KalshiMarketDataFetch:
    now = datetime.now(UTC)
    return KalshiMarketDataFetch(
        markets=[
            Market(
                market_id="KXPLAN",
                event_id="KXPLAN",
                venue="kalshi",
                product_type="prediction_contract",
                title="Plan fixture",
                category="test",
                status="open",
                close_time=(now + timedelta(days=1)).isoformat().replace(
                    "+00:00",
                    "Z",
                ),
                liquidity=100.0,
                volume=10.0,
                rules_summary="test",
                outcomes=[
                    OutcomeQuote(
                        outcome_id="yes",
                        name="Yes",
                        price=0.3,
                        price_format="probability",
                        implied_probability=0.3,
                        bid=0.28,
                        ask=0.3,
                        metadata={"baseline_adjustment": 0.1},
                    ),
                    OutcomeQuote(
                        outcome_id="no",
                        name="No",
                        price=0.72,
                        price_format="probability",
                        implied_probability=0.72,
                        bid=0.7,
                        ask=0.72,
                    ),
                ],
            )
        ],
        raw_market_count=1,
        base_url="https://kalshi-market-data.example.test",
        generated_at=now.isoformat().replace("+00:00", "Z"),
        warnings=[],
    )
