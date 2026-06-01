from __future__ import annotations

import json
from datetime import date

from restless_gambler.market_data import (
    load_market_snapshots,
    merge_market_snapshot_files,
)
from restless_gambler.mlb_stats import (
    MlbBullpenRest,
    MlbGameTeamContext,
    MlbPitcherStats,
    MlbTeamRecord,
    enrich_mlb_markets_with_team_records,
)
from restless_gambler.research import build_research_notes
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

MLB_EVENT = {
    "id": "mlb-event-1",
    "sport_key": "baseball_mlb",
    "sport_title": "MLB",
    "commence_time": "2026-06-01T23:30:00Z",
    "home_team": "Chicago Cubs",
    "away_team": "St. Louis Cardinals",
    "bookmakers": [
        {
            "key": "draftkings",
            "title": "DraftKings",
            "markets": [
                {
                    "key": "h2h",
                    "outcomes": [
                        {"name": "St. Louis Cardinals", "price": 120},
                        {"name": "Chicago Cubs", "price": -140},
                    ],
                }
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


def test_market_loader_filters_markets_closed_before_snapshot_generation(tmp_path):
    fetch = SportsOddsFetch(
        markets=normalize_sports_event(
            {
                **SPORTS_EVENT,
                "commence_time": "2026-05-31T23:21:00Z",
            }
        ),
        raw_event_count=1,
        base_url="https://api.example.test/v4",
        sport="basketball_nba",
        regions="us",
        markets_requested="h2h,spreads",
        generated_at="2026-06-01T00:00:00Z",
        warnings=[],
    )
    path = write_sports_odds_snapshot(
        output_path=tmp_path / "sports_odds_latest.json",
        fetch=fetch,
    )

    loaded = load_market_snapshots(
        path=path,
        as_of=date(2026, 5, 31),
        min_liquidity=0.0,
        max_markets=10,
    )

    assert loaded.markets == []
    assert "stale market" in loaded.data_source()["warnings"][0]


def test_mlb_record_enrichment_emits_source_backed_signal():
    markets = enrich_mlb_markets_with_team_records(
        normalize_sports_event(MLB_EVENT),
        _mlb_records(),
    )

    notes = build_research_notes(markets)
    signals = notes[0].signals

    assert any(signal.name == "mlb_team_record_strength" for signal in signals)
    cubs_signal = next(
        signal
        for signal in signals
        if signal.name == "mlb_team_record_strength"
        and signal.outcome_id == "chicago-cubs"
    )
    assert cubs_signal.direction > 0
    assert cubs_signal.source == "mlb_stats_api_standings"


def test_mlb_game_context_enrichment_emits_pitcher_and_bullpen_signals():
    markets = enrich_mlb_markets_with_team_records(
        normalize_sports_event(MLB_EVENT),
        _mlb_records(),
        {
            ("chicago-cubs", "st-louis-cardinals", "2026-06-01"): {
                "chicago-cubs": MlbGameTeamContext(
                    team_name="Chicago Cubs",
                    opponent_name="St. Louis Cardinals",
                    game_date="2026-06-01",
                    probable_pitcher=MlbPitcherStats(
                        player_id=1,
                        name="Cubs Starter",
                        era=2.50,
                        whip=1.0,
                        strikeouts_per_9=10.0,
                        walks_per_9=2.0,
                        innings_pitched=60.0,
                        games_started=10,
                    ),
                    bullpen_rest=MlbBullpenRest(
                        team_id=112,
                        team_name="Chicago Cubs",
                        recent_games=1,
                        days_since_last_game=2,
                        last_game_innings=9,
                    ),
                ),
                "st-louis-cardinals": MlbGameTeamContext(
                    team_name="St. Louis Cardinals",
                    opponent_name="Chicago Cubs",
                    game_date="2026-06-01",
                    probable_pitcher=MlbPitcherStats(
                        player_id=2,
                        name="Cardinals Starter",
                        era=4.50,
                        whip=1.4,
                        strikeouts_per_9=7.0,
                        walks_per_9=4.0,
                        innings_pitched=55.0,
                        games_started=10,
                    ),
                    bullpen_rest=MlbBullpenRest(
                        team_id=138,
                        team_name="St. Louis Cardinals",
                        recent_games=3,
                        days_since_last_game=1,
                        last_game_innings=11,
                    ),
                ),
            }
        },
    )

    signals = build_research_notes(markets)[0].signals

    assert any(
        signal.name == "mlb_probable_pitcher_strength"
        and signal.outcome_id == "chicago-cubs"
        and signal.direction > 0
        for signal in signals
    )
    assert any(
        signal.name == "mlb_bullpen_rest_proxy"
        and signal.outcome_id == "chicago-cubs"
        and signal.direction > 0
        for signal in signals
    )


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


def _mlb_records() -> dict[str, MlbTeamRecord]:
    return {
        "chicago-cubs": MlbTeamRecord(
            team_id=112,
            name="Chicago Cubs",
            wins=40,
            losses=20,
            winning_percentage=0.667,
            run_differential=60,
            games_played=60,
        ),
        "st-louis-cardinals": MlbTeamRecord(
            team_id=138,
            name="St. Louis Cardinals",
            wins=28,
            losses=32,
            winning_percentage=0.467,
            run_differential=-20,
            games_played=60,
        ),
    }
