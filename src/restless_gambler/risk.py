from __future__ import annotations

from collections.abc import Iterable

from restless_gambler.config import RiskConfig
from restless_gambler.domain import Market, RiskDecision, WagerIntent


def evaluate_risk(
    *,
    intents: Iterable[WagerIntent],
    markets: Iterable[Market],
    risk: RiskConfig,
    current_daily_pnl: float = 0.0,
    existing_total_exposure: float = 0.0,
    existing_market_exposure: dict[str, float] | None = None,
) -> list[RiskDecision]:
    open_market_ids = {
        market.market_id for market in markets if market.status == "open"
    }
    seen_ids: set[str] = set()
    approved_total_cost = existing_total_exposure
    approved_market_cost: dict[str, float] = dict(existing_market_exposure or {})
    decisions: list[RiskDecision] = []

    for intent in intents:
        checks: list[str] = []
        rejection: str | None = None
        projected_total_cost = approved_total_cost + intent.estimated_cost
        projected_market_cost = (
            approved_market_cost.get(intent.market_id, 0.0) + intent.estimated_cost
        )

        if risk.kill_switch_enabled:
            rejection = "kill switch enabled"
        elif intent.client_order_id in seen_ids:
            rejection = "duplicate client order id"
        elif intent.venue not in risk.allowed_venues:
            rejection = "venue is not allowed"
        elif intent.market_id not in open_market_ids:
            rejection = "market is not open or not in snapshot"
        elif intent.units <= 0:
            rejection = "wager units must be positive"
        elif intent.units > risk.max_units_per_wager:
            rejection = "units exceed max units per wager"
        elif intent.action == "sell" and not risk.allow_sells:
            rejection = "sell orders are disabled in v1"
        elif intent.action == "lay" and not risk.allow_lays:
            rejection = "lay orders are disabled in v1"
        elif intent.expected_value < risk.min_expected_value:
            rejection = "expected value is below risk threshold"
        elif intent.estimated_cost > risk.max_wager_cost:
            rejection = "wager cost exceeds max wager cost"
        elif projected_market_cost > risk.max_market_exposure:
            rejection = "market exposure exceeds max market exposure"
        elif projected_total_cost > risk.max_total_exposure:
            rejection = "total exposure exceeds max total exposure"
        elif current_daily_pnl < -risk.max_daily_loss:
            rejection = "daily loss exceeds max daily loss"

        seen_ids.add(intent.client_order_id)
        if rejection:
            decisions.append(
                RiskDecision(
                    client_order_id=intent.client_order_id,
                    status="rejected",
                    reason=rejection,
                    checks=checks,
                )
            )
            continue

        checks.extend(
            [
                "kill_switch",
                "duplicate_order",
                "venue",
                "market_status",
                "units",
                "action",
                "expected_value",
                "wager_cost",
                "market_exposure",
                "total_exposure",
                "daily_loss",
            ]
        )
        approved_total_cost = projected_total_cost
        approved_market_cost[intent.market_id] = projected_market_cost
        decisions.append(
            RiskDecision(
                client_order_id=intent.client_order_id,
                status="approved",
                reason="all risk checks passed",
                checks=checks,
            )
        )

    return decisions
