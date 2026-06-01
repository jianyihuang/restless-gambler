from __future__ import annotations

import json
from datetime import date

from restless_gambler.kalshi import (
    KalshiMarketDataFetch,
    kalshi_settlement_outcome,
    market_to_snapshot_dict,
    normalize_kalshi_market,
    write_kalshi_market_snapshot,
)
from restless_gambler.market_data import load_market_snapshots

RAW_KALSHI_MARKET = {
    "ticker": "KXTEST-26JUN-YES",
    "event_ticker": "KXTEST-26JUN",
    "status": "active",
    "title": "Will the fixture resolve yes?",
    "yes_sub_title": "Yes",
    "no_sub_title": "No",
    "close_time": "2026-06-30T00:00:00Z",
    "yes_bid_dollars": "0.4100",
    "yes_ask_dollars": "0.4500",
    "no_bid_dollars": "0.5300",
    "no_ask_dollars": "0.5900",
    "yes_bid_size_fp": "12.00",
    "yes_ask_size_fp": "10.00",
    "volume_fp": "250.00",
    "liquidity_dollars": "0.00",
    "open_interest_fp": "8000.00",
    "rules_primary": "Primary rule.",
    "rules_secondary": "Secondary rule.",
}


def test_normalize_kalshi_market_uses_orderbook_best_bids():
    market = normalize_kalshi_market(
        RAW_KALSHI_MARKET,
        orderbook={
            "yes_dollars": [["0.4200", "20.00"], ["0.4000", "5.00"]],
            "no_dollars": [["0.5500", "7.00"]],
        },
    )

    assert market.market_id == "KXTEST-26JUN-YES"
    assert market.event_id == "KXTEST-26JUN"
    assert market.venue == "kalshi"
    assert market.product_type == "prediction_contract"
    assert market.category == "test"
    assert market.status == "open"
    assert market.liquidity == 8000.0
    yes, no = market.outcomes
    assert yes.outcome_id == "yes"
    assert yes.bid == 0.42
    assert yes.ask == 0.45
    assert yes.price == 0.45
    assert no.outcome_id == "no"
    assert no.bid == 0.55
    assert no.ask == 0.59


def test_kalshi_snapshot_round_trips_through_market_loader(tmp_path):
    market = normalize_kalshi_market(RAW_KALSHI_MARKET)
    fetch = KalshiMarketDataFetch(
        markets=[market],
        raw_market_count=1,
        base_url="https://example.test/trade-api/v2",
        generated_at="2026-05-31T00:00:00Z",
        warnings=[],
    )
    path = write_kalshi_market_snapshot(
        output_path=tmp_path / "kalshi_latest.json",
        fetch=fetch,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["source"] == "kalshi_api"
    assert payload["markets"] == [market_to_snapshot_dict(market)]

    loaded = load_market_snapshots(
        path=path,
        as_of=date(2026, 5, 31),
        min_liquidity=0.0,
        max_markets=5,
    )
    assert loaded.data_source()["name"] == "kalshi_api"
    assert loaded.markets[0].market_id == market.market_id


def test_kalshi_settlement_outcome_requires_finalized_by_default():
    raw_market = {
        **RAW_KALSHI_MARKET,
        "status": "determined",
        "result": "yes",
    }

    assert kalshi_settlement_outcome(raw_market, outcome_id="yes") is None
    assert (
        kalshi_settlement_outcome(
            raw_market,
            outcome_id="yes",
            allow_determined=True,
        )
        == "won"
    )


def test_kalshi_settlement_outcome_maps_yes_no_positions():
    yes_market = {
        **RAW_KALSHI_MARKET,
        "status": "finalized",
        "result": "yes",
    }
    no_market = {
        **RAW_KALSHI_MARKET,
        "status": "finalized",
        "result": "no",
    }

    assert kalshi_settlement_outcome(yes_market, outcome_id="yes") == "won"
    assert kalshi_settlement_outcome(yes_market, outcome_id="no") == "lost"
    assert kalshi_settlement_outcome(no_market, outcome_id="yes") == "lost"
    assert kalshi_settlement_outcome(no_market, outcome_id="no") == "won"
