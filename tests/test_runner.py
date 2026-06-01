from __future__ import annotations

import json
from datetime import date

import pytest

from restless_gambler.config import load_config
from restless_gambler.domain import RiskDecision
from restless_gambler.runner import RestlessGamblerRunner, limit_live_order_decisions


def test_paper_runner_writes_reproducible_artifact(tmp_path):
    config = load_config(
        mode="paper",
        as_of=date(2026, 5, 31),
        artifacts_dir=tmp_path,
    )

    first_path = RestlessGamblerRunner(config).run()
    second_path = RestlessGamblerRunner(config).run()
    first_payload = json.loads(first_path.read_text(encoding="utf-8"))
    second_payload = json.loads(second_path.read_text(encoding="utf-8"))

    assert first_path == second_path
    assert first_payload == second_payload
    assert first_payload["runtime_mode"] == "paper"
    assert first_payload["status"] == "completed"
    assert first_payload["data_source"]["name"] == "fixture_cross_gambling_snapshot"
    assert len(first_payload["markets"]) == 4
    assert {market["product_type"] for market in first_payload["markets"]} == {
        "prediction_contract",
        "sportsbook",
    }
    assert len(first_payload["opportunities"]) >= 1
    assert {execution["status"] for execution in first_payload["executions"]} == {
        "filled"
    }
    assert len(first_payload["bets"]) == len(first_payload["executions"])
    assert first_payload["cash"] < 10000.0
    assert first_payload["errors"] == []


def test_research_mode_does_not_create_orders(tmp_path):
    config = load_config(
        mode="research",
        as_of=date(2026, 5, 31),
        artifacts_dir=tmp_path,
    )

    artifact_path = RestlessGamblerRunner(config).run()
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))

    assert payload["runtime_mode"] == "research"
    assert payload["opportunities"]
    assert payload["wager_intents"] == []
    assert payload["executions"] == []
    assert payload["bets"] == []


def test_kill_switch_blocks_paper_orders(tmp_path, monkeypatch):
    monkeypatch.setenv("RG_KILL_SWITCH", "true")
    config = load_config(
        mode="paper",
        as_of=date(2026, 5, 31),
        artifacts_dir=tmp_path,
    )

    artifact_path = RestlessGamblerRunner(config).run()
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))

    assert payload["bets"] == []
    assert {decision["status"] for decision in payload["risk_decisions"]} == {
        "rejected"
    }
    assert {decision["reason"] for decision in payload["risk_decisions"]} == {
        "kill switch enabled"
    }
    assert {execution["status"] for execution in payload["executions"]} == {
        "rejected"
    }


def test_runner_blocks_open_ledger_wager_keys(tmp_path):
    config = load_config(
        mode="paper",
        as_of=date(2026, 5, 31),
        artifacts_dir=tmp_path,
    )
    blocked_wagers = {
        ("paper_sportsbook", "NBA-LAL-BOS-20260601-ML", "lal"),
        ("kalshi", "KXFED-26JUN-CUT", "yes"),
        ("kalshi", "KXNYC-26JUN-HIGH90", "no"),
    }

    artifact_path = RestlessGamblerRunner(
        config,
        blocked_wagers=blocked_wagers,
    ).run()
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))

    assert payload["opportunities"]
    assert payload["wager_intents"] == []
    assert payload["bets"] == []
    assert any("already open in paper ledger" in row for row in payload["warnings"])


def test_live_mode_requires_explicit_enable(tmp_path, monkeypatch):
    monkeypatch.delenv("RG_LIVE_TRADING_ENABLED", raising=False)
    config = load_config(
        mode="live",
        as_of=date(2026, 5, 31),
        artifacts_dir=tmp_path,
    )

    with pytest.raises(ValueError, match="live mode requires"):
        RestlessGamblerRunner(config).run()


def test_live_mode_requires_confirm_live_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("RG_LIVE_TRADING_ENABLED", "true")
    config = load_config(
        mode="live",
        as_of=date(2026, 5, 31),
        artifacts_dir=tmp_path,
    )

    with pytest.raises(ValueError, match="--confirm-live"):
        RestlessGamblerRunner(config).run()


def test_live_order_limit_rejects_approvals_after_cap():
    decisions = [
        RiskDecision(
            client_order_id="order-1",
            status="approved",
            reason="all risk checks passed",
            checks=["risk"],
        ),
        RiskDecision(
            client_order_id="order-2",
            status="approved",
            reason="all risk checks passed",
            checks=["risk"],
        ),
    ]

    limited = limit_live_order_decisions(decisions, max_orders=1)

    assert [decision.status for decision in limited] == ["approved", "rejected"]
    assert limited[1].reason == "max live orders per run reached"
    assert "max_live_orders" in limited[1].checks
