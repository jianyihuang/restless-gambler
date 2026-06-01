from __future__ import annotations

from restless_gambler.domain import RiskDecision, WagerIntent
from restless_gambler.execution import (
    KalshiLiveExecutionAdapter,
    build_kalshi_order_payload,
)


def test_build_kalshi_order_payload_uses_fixed_point_contracts_and_price():
    intent = _intent()

    payload = build_kalshi_order_payload(intent)

    assert payload == {
        "ticker": "KXTEST",
        "client_order_id": "live-test-order",
        "side": "yes",
        "action": "buy",
        "count_fp": "3.00",
        "yes_price_dollars": "0.4200",
        "post_only": True,
        "reduce_only": False,
    }


def test_kalshi_live_adapter_maps_resting_order_without_paper_fill(monkeypatch):
    captured_payloads = []

    def fake_create_kalshi_order(*, payload, base_url=None, timeout_seconds=20):
        captured_payloads.append(payload)
        return {
            "order_id": "order-1",
            "client_order_id": payload["client_order_id"],
            "ticker": payload["ticker"],
            "status": "resting",
            "fill_count_fp": "0.00",
            "remaining_count_fp": "3.00",
            "yes_price_dollars": "0.4200",
            "created_time": "2026-05-31T00:00:00Z",
        }

    monkeypatch.setattr(
        "restless_gambler.execution.create_kalshi_order",
        fake_create_kalshi_order,
    )

    executions, bets = KalshiLiveExecutionAdapter(
        base_url="https://example.test/trade-api/v2",
    ).submit(
        intents=[_intent()],
        risk_decisions=[
            RiskDecision(
                client_order_id="live-test-order",
                status="approved",
                reason="ok",
            )
        ],
        timestamp="2026-05-31T00:00:00Z",
    )

    assert captured_payloads
    assert executions[0].status == "new"
    assert executions[0].filled_units == 0
    assert bets == []


def test_kalshi_live_adapter_records_filled_order(monkeypatch):
    def fake_create_kalshi_order(*, payload, base_url=None, timeout_seconds=20):
        return {
            "order_id": "order-1",
            "client_order_id": payload["client_order_id"],
            "ticker": payload["ticker"],
            "status": "executed",
            "fill_count_fp": "3.00",
            "remaining_count_fp": "0.00",
            "yes_price_dollars": "0.4200",
            "taker_fill_cost_dollars": "1.26",
            "taker_fees_dollars": "0.05",
            "created_time": "2026-05-31T00:00:00Z",
            "last_update_time": "2026-05-31T00:00:01Z",
        }

    monkeypatch.setattr(
        "restless_gambler.execution.create_kalshi_order",
        fake_create_kalshi_order,
    )

    executions, bets = KalshiLiveExecutionAdapter().submit(
        intents=[_intent()],
        risk_decisions=[
            RiskDecision(
                client_order_id="live-test-order",
                status="approved",
                reason="ok",
            )
        ],
        timestamp="2026-05-31T00:00:00Z",
    )

    assert executions[0].status == "filled"
    assert executions[0].filled_units == 3
    assert bets[0].units == 3
    assert bets[0].cost == 1.31


def _intent() -> WagerIntent:
    return WagerIntent(
        client_order_id="live-test-order",
        venue="kalshi",
        product_type="prediction_contract",
        market_id="KXTEST",
        outcome_id="yes",
        outcome_name="Yes",
        action="buy",
        units=3,
        limit_price=0.42,
        price_format="probability",
        estimated_fee=0.05,
        estimated_cost=1.31,
        expected_value=0.05,
        post_only=True,
        reduce_only=False,
        reason="test",
    )
