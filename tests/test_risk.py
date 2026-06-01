from __future__ import annotations

from dataclasses import replace

from restless_gambler.config import RiskConfig
from restless_gambler.domain import Market, OutcomeQuote, WagerIntent
from restless_gambler.risk import evaluate_risk


def test_risk_rejects_low_expected_value():
    intent = WagerIntent(
        client_order_id="test-wager",
        venue="kalshi",
        product_type="prediction_contract",
        market_id="KXTEST",
        outcome_id="yes",
        outcome_name="Yes",
        action="buy",
        units=1,
        limit_price=0.5,
        price_format="probability",
        estimated_fee=0.02,
        estimated_cost=0.52,
        expected_value=0.01,
        post_only=True,
        reduce_only=False,
        reason="test",
    )
    market = Market(
        market_id="KXTEST",
        event_id="KXTEST",
        venue="kalshi",
        product_type="prediction_contract",
        title="Test market",
        category="test",
        status="open",
        close_time="2026-06-01T00:00:00Z",
        liquidity=10000,
        volume=1000,
        rules_summary="test",
        outcomes=[
            OutcomeQuote(
                outcome_id="yes",
                name="Yes",
                price=0.5,
                price_format="probability",
                implied_probability=0.5,
                bid=0.49,
                ask=0.5,
            )
        ],
    )

    decisions = evaluate_risk(
        intents=[intent],
        markets=[market],
        risk=RiskConfig(min_expected_value=0.03),
    )

    assert decisions[0].status == "rejected"
    assert decisions[0].reason == "expected value is below risk threshold"


def test_risk_counts_existing_open_ledger_exposure():
    intent = WagerIntent(
        client_order_id="test-wager",
        venue="kalshi",
        product_type="prediction_contract",
        market_id="KXTEST",
        outcome_id="yes",
        outcome_name="Yes",
        action="buy",
        units=1,
        limit_price=0.5,
        price_format="probability",
        estimated_fee=0.02,
        estimated_cost=0.52,
        expected_value=0.10,
        post_only=True,
        reduce_only=False,
        reason="test",
    )
    market = Market(
        market_id="KXTEST",
        event_id="KXTEST",
        venue="kalshi",
        product_type="prediction_contract",
        title="Test market",
        category="test",
        status="open",
        close_time="2026-06-01T00:00:00Z",
        liquidity=10000,
        volume=1000,
        rules_summary="test",
        outcomes=[
            OutcomeQuote(
                outcome_id="yes",
                name="Yes",
                price=0.5,
                price_format="probability",
                implied_probability=0.5,
                bid=0.49,
                ask=0.5,
            )
        ],
    )

    total_decisions = evaluate_risk(
        intents=[intent],
        markets=[market],
        risk=RiskConfig(max_total_exposure=1.0),
        existing_total_exposure=0.75,
    )
    market_decisions = evaluate_risk(
        intents=[replace(intent, client_order_id="test-wager-2")],
        markets=[market],
        risk=RiskConfig(max_market_exposure=1.0),
        existing_market_exposure={"KXTEST": 0.75},
    )

    assert total_decisions[0].status == "rejected"
    assert total_decisions[0].reason == "total exposure exceeds max total exposure"
    assert market_decisions[0].status == "rejected"
    assert (
        market_decisions[0].reason == "market exposure exceeds max market exposure"
    )
