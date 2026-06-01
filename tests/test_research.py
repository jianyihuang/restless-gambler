from __future__ import annotations

from restless_gambler.config import ModelConfig
from restless_gambler.domain import Market, OutcomeQuote
from restless_gambler.forecast import generate_forecasts
from restless_gambler.research import (
    build_market_signals,
    build_research_notes,
    build_sportsbook_consensus_signals,
)


def test_build_market_signals_extracts_probability_adjustments_and_quality():
    market = _market()

    signals = build_market_signals(market)

    assert any(
        signal.kind == "probability_adjustment"
        and signal.outcome_id == "yes"
        and signal.direction == 1.0
        and signal.magnitude == 0.08
        for signal in signals
    )
    assert any(signal.name == "bid_ask_spread" for signal in signals)
    assert any(signal.name == "moderate_activity_market" for signal in signals)


def test_forecast_applies_only_probability_adjustment_signals():
    market = _market()
    notes = build_research_notes([market])

    forecasts = generate_forecasts(
        markets=[market],
        research_notes=notes,
        model=ModelConfig(),
    )
    yes_forecast = next(
        forecast for forecast in forecasts if forecast.outcome_id == "yes"
    )
    no_forecast = next(
        forecast for forecast in forecasts if forecast.outcome_id == "no"
    )

    assert yes_forecast.fair_probability == 0.58
    assert no_forecast.fair_probability == 0.52
    assert "fixture_baseline_adjustment" in yes_forecast.rationale
    assert "No independent probability adjustment" in no_forecast.rationale


def test_sportsbook_consensus_signals_use_no_vig_cross_book_prices():
    markets = _sportsbook_markets()

    signals_by_market = build_sportsbook_consensus_signals(markets)
    notes = build_research_notes(markets)
    forecasts = generate_forecasts(
        markets=markets,
        research_notes=notes,
        model=ModelConfig(),
    )

    assert set(signals_by_market) == {
        "book-a-event-1-h2h",
        "book-b-event-1-h2h",
        "book-c-event-1-h2h",
    }
    assert any(
        signal.name == "sportsbook_consensus_no_vig"
        and signal.outcome_id == "lakers"
        and signal.direction > 0
        for signal in signals_by_market["book-b-event-1-h2h"]
    )
    book_b_lakers = next(
        forecast
        for forecast in forecasts
        if forecast.market_id == "book-b-event-1-h2h"
        and forecast.outcome_id == "lakers"
    )
    assert book_b_lakers.fair_probability > 0.41
    assert "sportsbook_consensus_no_vig" in book_b_lakers.rationale


def test_sportsbook_consensus_requires_three_non_extreme_books():
    markets = _sportsbook_markets()[:2] + [
        Market(
            market_id="book-outlier-event-1-h2h",
            event_id="event-1",
            venue="book_outlier",
            product_type="sportsbook",
            title="Lakers at Celtics h2h at Outlier Book",
            category="basketball_nba",
            status="open",
            close_time="2026-06-01T23:30:00Z",
            liquidity=0,
            volume=0,
            rules_summary="outlier",
            outcomes=[
                _sports_outcome("lakers", "Los Angeles Lakers", 10000),
                _sports_outcome("celtics", "Boston Celtics", -20000),
            ],
        )
    ]

    assert build_sportsbook_consensus_signals(markets) == {}


def _market() -> Market:
    return Market(
        market_id="KXTEST",
        event_id="KXTEST",
        venue="kalshi",
        product_type="prediction_contract",
        title="Test market",
        category="test",
        status="open",
        close_time="2026-06-01T00:00:00Z",
        liquidity=25_000,
        volume=1_000,
        rules_summary="test rules",
        outcomes=[
            OutcomeQuote(
                outcome_id="yes",
                name="Yes",
                price=0.5,
                price_format="probability",
                implied_probability=0.5,
                bid=0.48,
                ask=0.5,
                metadata={"baseline_adjustment": 0.08},
            ),
            OutcomeQuote(
                outcome_id="no",
                name="No",
                price=0.52,
                price_format="probability",
                implied_probability=0.52,
                bid=0.5,
                ask=0.52,
            ),
        ],
    )


def _sportsbook_markets() -> list[Market]:
    return [
        Market(
            market_id="book-a-event-1-h2h",
            event_id="event-1",
            venue="book_a",
            product_type="sportsbook",
            title="Lakers at Celtics h2h at Book A",
            category="basketball_nba",
            status="open",
            close_time="2026-06-01T23:30:00Z",
            liquidity=0,
            volume=0,
            rules_summary="book A",
            outcomes=[
                _sports_outcome("lakers", "Los Angeles Lakers", 125),
                _sports_outcome("celtics", "Boston Celtics", -145),
            ],
        ),
        Market(
            market_id="book-b-event-1-h2h",
            event_id="event-1",
            venue="book_b",
            product_type="sportsbook",
            title="Lakers at Celtics h2h at Book B",
            category="basketball_nba",
            status="open",
            close_time="2026-06-01T23:30:00Z",
            liquidity=0,
            volume=0,
            rules_summary="book B",
            outcomes=[
                _sports_outcome("lakers", "Los Angeles Lakers", 150),
                _sports_outcome("celtics", "Boston Celtics", -160),
            ],
        ),
        Market(
            market_id="book-c-event-1-h2h",
            event_id="event-1",
            venue="book_c",
            product_type="sportsbook",
            title="Lakers at Celtics h2h at Book C",
            category="basketball_nba",
            status="open",
            close_time="2026-06-01T23:30:00Z",
            liquidity=0,
            volume=0,
            rules_summary="book C",
            outcomes=[
                _sports_outcome("lakers", "Los Angeles Lakers", 130),
                _sports_outcome("celtics", "Boston Celtics", -150),
            ],
        ),
    ]


def _sports_outcome(outcome_id: str, name: str, price: float) -> OutcomeQuote:
    implied_probability = (
        100.0 / (price + 100.0)
        if price > 0
        else abs(price) / (abs(price) + 100.0)
    )
    return OutcomeQuote(
        outcome_id=outcome_id,
        name=name,
        price=price,
        price_format="american",
        implied_probability=implied_probability,
        metadata={
            "raw_name": name,
            "point": None,
            "market_key": "h2h",
        },
    )
