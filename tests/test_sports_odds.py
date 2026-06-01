from __future__ import annotations

import json
from datetime import date

from restless_gambler.market_data import (
    load_market_snapshots,
    merge_market_snapshot_files,
)
from restless_gambler.sports_odds import (
    SportsOddsFetch,
    normalize_sports_event,
    write_sports_odds_snapshot,
)

SPORTS_EVENT = {
    "id": "event-1",
    "sport_key": "basketball_nba",
    "sport_title": "NBA",
    "commence_time": "2026-06-01T23:30:00Z",
    "home_team": "Boston Celtics",
    "away_team": "Los Angeles Lakers",
    "bookmakers": [
        {
            "key": "draftkings",
            "title": "DraftKings",
            "markets": [
                {
                    "key": "h2h",
                    "outcomes": [
                        {"name": "Los Angeles Lakers", "price": 125},
                        {"name": "Boston Celtics", "price": -145},
                    ],
                },
                {
                    "key": "spreads",
                    "outcomes": [
                        {"name": "Los Angeles Lakers", "price": -110, "point": 2.5},
                        {"name": "Boston Celtics", "price": -110, "point": -2.5},
                    ],
                },
            ],
        }
    ],
}


def test_normalize_sports_event_builds_bookmaker_markets():
    markets = normalize_sports_event(SPORTS_EVENT)

    assert len(markets) == 2
    moneyline = markets[0]
    assert moneyline.product_type == "sportsbook"
    assert moneyline.venue == "draftkings"
    assert moneyline.category == "basketball_nba"
    assert moneyline.status == "open"
    assert moneyline.close_time == "2026-06-01T23:30:00Z"
    assert moneyline.outcomes[0].price_format == "american"
    assert moneyline.outcomes[0].metadata["market_key"] == "h2h"
    assert round(moneyline.outcomes[0].implied_probability, 4) == 0.4444
    assert round(moneyline.outcomes[1].implied_probability, 4) == 0.5918


def test_sports_odds_snapshot_round_trips_through_market_loader(tmp_path):
    fetch = SportsOddsFetch(
        markets=normalize_sports_event(SPORTS_EVENT),
        raw_event_count=1,
        base_url="https://api.example.test/v4",
        sport="basketball_nba",
        regions="us",
        markets_requested="h2h,spreads",
        generated_at="2026-05-31T00:00:00Z",
        warnings=[],
    )
    path = write_sports_odds_snapshot(
        output_path=tmp_path / "sports_odds_latest.json",
        fetch=fetch,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["source"] == "the_odds_api"
    assert len(payload["markets"]) == 2

    loaded = load_market_snapshots(
        path=path,
        as_of=date(2026, 5, 31),
        min_liquidity=0.0,
        max_markets=10,
    )
    assert loaded.data_source()["name"] == "the_odds_api"
    assert len(loaded.markets) == 2


def test_market_snapshot_merge_combines_platform_files(tmp_path):
    sports_fetch = SportsOddsFetch(
        markets=normalize_sports_event(SPORTS_EVENT),
        raw_event_count=1,
        base_url="https://api.example.test/v4",
        sport="basketball_nba",
        regions="us",
        markets_requested="h2h",
        generated_at="2026-05-31T00:00:00Z",
        warnings=[],
    )
    first_path = write_sports_odds_snapshot(
        output_path=tmp_path / "sports_one.json",
        fetch=sports_fetch,
    )
    second_path = write_sports_odds_snapshot(
        output_path=tmp_path / "sports_two.json",
        fetch=sports_fetch,
    )

    merged_path = merge_market_snapshot_files(
        input_paths=[first_path, second_path],
        output_path=tmp_path / "merged.json",
    )
    loaded = load_market_snapshots(
        path=merged_path,
        as_of=date(2026, 5, 31),
        min_liquidity=0.0,
        max_markets=10,
    )

    assert loaded.data_source()["name"] == "merged_market_snapshot"
    assert len(loaded.markets) == 2
