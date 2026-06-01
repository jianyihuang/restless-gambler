from __future__ import annotations

from restless_gambler.config import ModelConfig
from restless_gambler.domain import (
    Forecast,
    Market,
    Opportunity,
    OpportunityDiagnostic,
    OutcomeQuote,
    ResearchNote,
    ResearchSignal,
)

KALSHI_STANDARD_TAKER_FEE_RATE = 0.07


def generate_forecasts(
    *,
    markets: list[Market],
    research_notes: list[ResearchNote],
    model: ModelConfig,
) -> list[Forecast]:
    notes_by_market = {note.market_id: note for note in research_notes}
    forecasts: list[Forecast] = []
    for market in markets:
        for outcome in market.outcomes:
            note = notes_by_market.get(market.market_id)
            adjustment_signals = _matching_probability_signals(note, outcome.outcome_id)
            adjustment = sum(
                signal.direction * signal.magnitude for signal in adjustment_signals
            )
            fair_probability = _clamp_probability(
                outcome.implied_probability + adjustment
            )
            rationale = (
                f"Baseline fair probability starts from implied probability "
                f"{outcome.implied_probability:.3f}, applies adjustment "
                f"{adjustment:+.3f}."
            )
            if note:
                rationale = (
                    f"{rationale} Research confidence {note.confidence:.2f}. "
                    f"{_signal_rationale(adjustment_signals)}"
                )

            forecasts.append(
                Forecast(
                    market_id=market.market_id,
                    outcome_id=outcome.outcome_id,
                    fair_probability=round(fair_probability, 4),
                    confidence=_forecast_confidence(note, adjustment_signals),
                    model_name=f"{model.name}:{model.version}",
                    rationale=rationale,
                )
            )
    return forecasts


def find_opportunities(
    *,
    markets: list[Market],
    forecasts: list[Forecast],
    min_expected_value: float,
    max_units: int,
) -> list[Opportunity]:
    market_by_id = {market.market_id: market for market in markets}
    opportunities: list[Opportunity] = []

    for forecast in forecasts:
        market = market_by_id[forecast.market_id]
        outcome = _find_outcome(market, forecast.outcome_id)
        opportunity = _build_opportunity(
            market=market,
            outcome=outcome,
            fair_probability=forecast.fair_probability,
            min_expected_value=min_expected_value,
            max_units=max_units,
        )
        if opportunity:
            opportunities.append(opportunity)

    opportunities.sort(key=lambda opportunity: opportunity.expected_value, reverse=True)
    return opportunities


def build_opportunity_diagnostics(
    *,
    markets: list[Market],
    forecasts: list[Forecast],
    min_expected_value: float,
) -> list[OpportunityDiagnostic]:
    market_by_id = {market.market_id: market for market in markets}
    diagnostics: list[OpportunityDiagnostic] = []

    for forecast in forecasts:
        market = market_by_id[forecast.market_id]
        outcome = _find_outcome(market, forecast.outcome_id)
        entry_price = outcome.ask if outcome.ask is not None else outcome.price
        fee = estimate_fee_per_unit(market.product_type, entry_price)
        expected_value = _expected_value(
            product_type=market.product_type,
            price=entry_price,
            price_format=outcome.price_format,
            fair_probability=forecast.fair_probability,
            fee=fee,
        )
        decision = (
            "candidate"
            if expected_value >= min_expected_value
            else "expected_value_below_threshold"
        )
        diagnostics.append(
            OpportunityDiagnostic(
                market_id=market.market_id,
                outcome_id=outcome.outcome_id,
                outcome_name=outcome.name,
                venue=market.venue,
                product_type=market.product_type,
                fair_probability=round(forecast.fair_probability, 4),
                implied_probability=round(outcome.implied_probability, 4),
                entry_price=round(entry_price, 4),
                expected_value=round(expected_value, 4),
                min_expected_value=min_expected_value,
                decision=decision,
            )
        )

    return diagnostics


def _build_opportunity(
    *,
    market: Market,
    outcome: OutcomeQuote,
    fair_probability: float,
    min_expected_value: float,
    max_units: int,
) -> Opportunity | None:
    entry_price = outcome.ask if outcome.ask is not None else outcome.price
    unit_cost = _unit_cost(market.product_type, entry_price)
    fee = estimate_fee_per_unit(market.product_type, entry_price)
    edge_before_fees = fair_probability - outcome.implied_probability
    expected_value = _expected_value(
        product_type=market.product_type,
        price=entry_price,
        price_format=outcome.price_format,
        fair_probability=fair_probability,
        fee=fee,
    )
    if expected_value < min_expected_value:
        return None

    action = "buy" if market.product_type == "prediction_contract" else "bet"
    return Opportunity(
        market_id=market.market_id,
        outcome_id=outcome.outcome_id,
        outcome_name=outcome.name,
        venue=market.venue,
        product_type=market.product_type,
        action=action,
        fair_probability=round(fair_probability, 4),
        entry_price=round(entry_price, 4),
        entry_price_format=outcome.price_format,
        implied_probability=round(outcome.implied_probability, 4),
        unit_cost=unit_cost,
        fee_per_unit=fee,
        expected_value=round(expected_value, 4),
        edge_before_fees=round(edge_before_fees, 4),
        max_units=max_units,
        reason=(
            f"{outcome.name} fair probability exceeds market-implied probability "
            f"after estimated fees by {expected_value:.2%}"
        ),
    )


def estimate_fee_per_unit(product_type: str, price: float) -> float:
    if product_type == "prediction_contract":
        return round(KALSHI_STANDARD_TAKER_FEE_RATE * price * (1.0 - price), 4)
    return 0.0


def decimal_odds(price: float, price_format: str) -> float:
    if price_format == "decimal":
        return price
    if price_format == "american":
        if price > 0:
            return 1.0 + (price / 100.0)
        return 1.0 + (100.0 / abs(price))
    if price_format == "probability":
        return 1.0 / price
    msg = f"unsupported price format: {price_format}"
    raise ValueError(msg)


def _expected_value(
    *,
    product_type: str,
    price: float,
    price_format: str,
    fair_probability: float,
    fee: float,
) -> float:
    if product_type == "prediction_contract":
        return fair_probability - price - fee
    return fair_probability * decimal_odds(price, price_format) - 1.0 - fee


def _unit_cost(product_type: str, entry_price: float) -> float:
    if product_type == "prediction_contract":
        return entry_price
    return 1.0


def _find_outcome(market: Market, outcome_id: str) -> OutcomeQuote:
    for outcome in market.outcomes:
        if outcome.outcome_id == outcome_id:
            return outcome
    msg = f"outcome {outcome_id} not found in {market.market_id}"
    raise ValueError(msg)


def _clamp_probability(value: float) -> float:
    return min(0.99, max(0.01, value))


def _matching_probability_signals(
    note: ResearchNote | None,
    outcome_id: str,
) -> list[ResearchSignal]:
    if note is None:
        return []
    return [
        signal
        for signal in note.signals
        if signal.kind == "probability_adjustment"
        and signal.outcome_id in {None, outcome_id}
    ]


def _signal_rationale(signals: list[ResearchSignal]) -> str:
    if not signals:
        return "No independent probability adjustment signal was available."
    names = ", ".join(signal.name for signal in signals)
    return f"Applied probability signal(s): {names}."


def _forecast_confidence(
    note: ResearchNote | None,
    adjustment_signals: list[ResearchSignal],
) -> float:
    if adjustment_signals:
        return round(max(signal.confidence for signal in adjustment_signals), 2)
    if note:
        return round(min(note.confidence, 0.35), 2)
    return 0.3
