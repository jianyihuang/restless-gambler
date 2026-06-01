from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from restless_gambler.domain import (
    BetRecord,
    ExecutionRecord,
    RiskDecision,
    WagerIntent,
)
from restless_gambler.kalshi import create_kalshi_order


class PaperExecutionAdapter:
    def submit(
        self,
        *,
        intents: Iterable[WagerIntent],
        risk_decisions: Iterable[RiskDecision],
        timestamp: str,
    ) -> tuple[list[ExecutionRecord], list[BetRecord]]:
        decisions = {decision.client_order_id: decision for decision in risk_decisions}
        executions: list[ExecutionRecord] = []
        bets: list[BetRecord] = []

        for intent in intents:
            decision = decisions[intent.client_order_id]
            if decision.status == "rejected":
                executions.append(
                    ExecutionRecord(
                        client_order_id=intent.client_order_id,
                        venue=intent.venue,
                        product_type=intent.product_type,
                        market_id=intent.market_id,
                        outcome_id=intent.outcome_id,
                        outcome_name=intent.outcome_name,
                        action=intent.action,
                        units=intent.units,
                        limit_price=intent.limit_price,
                        price_format=intent.price_format,
                        status="rejected",
                        submitted_at=timestamp,
                        rejection_reason=decision.reason,
                    )
                )
                continue

            executions.append(
                ExecutionRecord(
                    client_order_id=intent.client_order_id,
                    venue=intent.venue,
                    product_type=intent.product_type,
                    market_id=intent.market_id,
                    outcome_id=intent.outcome_id,
                    outcome_name=intent.outcome_name,
                    action=intent.action,
                    units=intent.units,
                    limit_price=intent.limit_price,
                    price_format=intent.price_format,
                    status="filled",
                    submitted_at=timestamp,
                    filled_units=intent.units,
                    average_fill_price=intent.limit_price,
                )
            )
            bets.append(
                BetRecord(
                    client_order_id=intent.client_order_id,
                    venue=intent.venue,
                    product_type=intent.product_type,
                    market_id=intent.market_id,
                    outcome_id=intent.outcome_id,
                    outcome_name=intent.outcome_name,
                    action=intent.action,
                    units=intent.units,
                    price=intent.limit_price,
                    price_format=intent.price_format,
                    fee=round(intent.estimated_fee, 2),
                    cost=round(intent.estimated_cost, 2),
                    filled_at=timestamp,
                )
            )

        return executions, bets


class KalshiLiveExecutionAdapter:
    def __init__(self, *, base_url: str | None = None) -> None:
        self.base_url = base_url

    def submit(
        self,
        *,
        intents: Iterable[WagerIntent],
        risk_decisions: Iterable[RiskDecision],
        timestamp: str,
    ) -> tuple[list[ExecutionRecord], list[BetRecord]]:
        decisions = {decision.client_order_id: decision for decision in risk_decisions}
        executions: list[ExecutionRecord] = []
        bets: list[BetRecord] = []

        for intent in intents:
            decision = decisions[intent.client_order_id]
            if decision.status == "rejected":
                executions.append(
                    _rejected_execution(
                        intent=intent,
                        timestamp=timestamp,
                        reason=decision.reason,
                    )
                )
                continue

            try:
                payload = build_kalshi_order_payload(intent)
                order = create_kalshi_order(
                    payload=payload,
                    base_url=self.base_url,
                )
            except (OSError, ValueError) as error:
                executions.append(
                    _rejected_execution(
                        intent=intent,
                        timestamp=timestamp,
                        reason=str(error),
                    )
                )
                continue

            execution = _execution_from_kalshi_order(
                intent=intent,
                order=order,
                timestamp=timestamp,
            )
            executions.append(execution)
            bet = _bet_from_kalshi_order(
                intent=intent,
                order=order,
                timestamp=timestamp,
            )
            if bet:
                bets.append(bet)

        return executions, bets


def build_kalshi_order_payload(intent: WagerIntent) -> dict[str, object]:
    if intent.venue != "kalshi":
        msg = f"live Kalshi adapter cannot place venue {intent.venue}"
        raise ValueError(msg)
    if intent.product_type != "prediction_contract":
        msg = "live Kalshi adapter only supports prediction contracts"
        raise ValueError(msg)
    if intent.action not in {"buy", "sell"}:
        msg = f"live Kalshi adapter does not support action {intent.action}"
        raise ValueError(msg)
    if intent.outcome_id not in {"yes", "no"}:
        msg = f"Kalshi outcome must be yes or no, got {intent.outcome_id}"
        raise ValueError(msg)
    if intent.price_format != "probability":
        msg = "Kalshi live orders require probability price format"
        raise ValueError(msg)
    if intent.units <= 0:
        msg = "Kalshi live orders require positive units"
        raise ValueError(msg)
    if not 0.01 <= intent.limit_price <= 0.99:
        msg = "Kalshi live limit price must be between 0.01 and 0.99"
        raise ValueError(msg)

    payload: dict[str, object] = {
        "ticker": intent.market_id,
        "client_order_id": intent.client_order_id,
        "side": intent.outcome_id,
        "action": intent.action,
        "count_fp": f"{intent.units:.2f}",
        f"{intent.outcome_id}_price_dollars": f"{intent.limit_price:.4f}",
        "post_only": intent.post_only,
        "reduce_only": intent.reduce_only,
    }
    return payload


def _rejected_execution(
    *,
    intent: WagerIntent,
    timestamp: str,
    reason: str,
) -> ExecutionRecord:
    return ExecutionRecord(
        client_order_id=intent.client_order_id,
        venue=intent.venue,
        product_type=intent.product_type,
        market_id=intent.market_id,
        outcome_id=intent.outcome_id,
        outcome_name=intent.outcome_name,
        action=intent.action,
        units=intent.units,
        limit_price=intent.limit_price,
        price_format=intent.price_format,
        status="rejected",
        submitted_at=timestamp,
        rejection_reason=reason,
    )


def _execution_from_kalshi_order(
    *,
    intent: WagerIntent,
    order: dict[str, Any],
    timestamp: str,
) -> ExecutionRecord:
    filled_units = int(_fp_float(order.get("fill_count_fp")))
    remaining_units = _fp_float(order.get("remaining_count_fp"))
    raw_status = str(order.get("status") or "").lower()
    status = "filled" if filled_units > 0 and remaining_units == 0 else "new"
    if raw_status in {"canceled", "cancelled"}:
        status = "cancelled"
    elif raw_status in {"rejected"}:
        status = "rejected"

    return ExecutionRecord(
        client_order_id=intent.client_order_id,
        venue=intent.venue,
        product_type=intent.product_type,
        market_id=intent.market_id,
        outcome_id=intent.outcome_id,
        outcome_name=intent.outcome_name,
        action=intent.action,
        units=intent.units,
        limit_price=intent.limit_price,
        price_format=intent.price_format,
        status=status,
        submitted_at=str(order.get("created_time") or timestamp),
        filled_units=filled_units,
        average_fill_price=_fill_price(intent, order),
        rejection_reason=None if status != "rejected" else raw_status,
        external_order_id=_kalshi_order_id(order),
        venue_order_status=raw_status or None,
        venue_order_json=order,
    )


def _bet_from_kalshi_order(
    *,
    intent: WagerIntent,
    order: dict[str, Any],
    timestamp: str,
) -> BetRecord | None:
    filled_units = int(_fp_float(order.get("fill_count_fp")))
    if filled_units <= 0:
        return None

    cost = _fill_cost(order)
    fee = _fill_fee(order)
    price = _fill_price(intent, order) or intent.limit_price
    return BetRecord(
        client_order_id=intent.client_order_id,
        venue=intent.venue,
        product_type=intent.product_type,
        market_id=intent.market_id,
        outcome_id=intent.outcome_id,
        outcome_name=intent.outcome_name,
        action=intent.action,
        units=filled_units,
        price=price,
        price_format=intent.price_format,
        fee=round(fee, 2),
        cost=round(cost + fee, 2),
        filled_at=str(order.get("last_update_time") or timestamp),
    )


def _fill_price(intent: WagerIntent, order: dict[str, Any]) -> float | None:
    key = f"{intent.outcome_id}_price_dollars"
    value = _optional_float(order.get(key))
    return round(value, 4) if value is not None else None


def _fill_cost(order: dict[str, Any]) -> float:
    return sum(
        _optional_float(order.get(key)) or 0.0
        for key in ("taker_fill_cost_dollars", "maker_fill_cost_dollars")
    )


def _fill_fee(order: dict[str, Any]) -> float:
    return sum(
        _optional_float(order.get(key)) or 0.0
        for key in ("taker_fees_dollars", "maker_fees_dollars")
    )


def _kalshi_order_id(order: dict[str, Any]) -> str | None:
    value = order.get("order_id") or order.get("id")
    return str(value) if value else None


def _fp_float(value: object) -> float:
    return _optional_float(value) or 0.0


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(value)
