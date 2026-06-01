from __future__ import annotations

import json
from datetime import date

from restless_gambler.config import DEFAULT_ALLOWED_VENUES, load_config
from restless_gambler.persistence import import_run_artifact, ledger_status
from restless_gambler.runner import RestlessGamblerRunner
from restless_gambler.settlement import (
    settle_market_paper_bets,
    sync_kalshi_paper_settlements,
    sync_sportsbook_paper_settlements,
)
from restless_gambler.sports_odds import (
    SportsScoreEvent,
    SportsScoresFetch,
)


def test_sync_kalshi_paper_settlements_updates_finalized_bets(tmp_path, monkeypatch):
    artifact_path = RestlessGamblerRunner(
        load_config(
            mode="paper",
            as_of=date(2026, 5, 31),
            artifacts_dir=tmp_path / "runs",
            min_liquidity=0.0,
        )
    ).run()
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    db_path = tmp_path / "restless.duckdb"
    import_run_artifact(artifact_path=artifact_path, db_path=db_path)
    settled_market_id = next(
        bet["market_id"] for bet in payload["bets"] if bet["venue"] == "kalshi"
    )

    def fake_fetch_kalshi_market(*, ticker, base_url=None, timeout_seconds=20):
        if ticker == settled_market_id:
            return {
                "ticker": ticker,
                "status": "finalized",
                "result": "yes",
                "settlement_ts": "2026-06-01T00:00:00Z",
            }
        return {
            "ticker": ticker,
            "status": "active",
            "result": "",
        }

    monkeypatch.setattr(
        "restless_gambler.settlement.fetch_kalshi_market",
        fake_fetch_kalshi_market,
    )

    summary = sync_kalshi_paper_settlements(db_path=db_path)
    ledger = ledger_status(db_path)

    assert summary.checked == 2
    assert summary.settled == 1
    assert summary.open_or_unresolved == 1
    assert summary.errors == []
    assert any(row["settlement_status"] == "won" for row in ledger["summary"])


def test_settle_market_paper_bets_grades_sportsbook_market(tmp_path):
    artifact_path = RestlessGamblerRunner(
        load_config(
            mode="paper",
            as_of=date(2026, 5, 31),
            artifacts_dir=tmp_path / "runs",
            min_liquidity=0.0,
        )
    ).run()
    db_path = tmp_path / "restless.duckdb"
    import_run_artifact(artifact_path=artifact_path, db_path=db_path)

    summary = settle_market_paper_bets(
        db_path=db_path,
        market_id="NBA-LAL-BOS-20260601-ML",
        winning_outcome_id="lal",
        venue="paper_sportsbook",
        product_type="sportsbook",
    )
    ledger = ledger_status(db_path)

    assert summary.checked == 1
    assert summary.settled == 1
    assert summary.settlements[0]["settlement_status"] == "won"
    assert any(row["settlement_status"] == "won" for row in ledger["summary"])


def test_sync_sportsbook_paper_settlements_updates_h2h_bets(
    tmp_path,
    monkeypatch,
):
    snapshot_path = tmp_path / "sportsbook_snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "source": "test_sportsbook_snapshot",
                "generated_at": "2026-05-31T00:00:00Z",
                "markets": [
                    {
                        "market_id": "draftkings-event-1-h2h",
                        "event_id": "event-1",
                        "venue": "draftkings",
                        "product_type": "sportsbook",
                        "title": "Liberty Flames at Arkansas Razorbacks h2h",
                        "category": "baseball_ncaa",
                        "status": "open",
                        "close_time": "2026-06-01T23:00:00Z",
                        "liquidity": 100.0,
                        "volume": 100.0,
                        "rules_summary": "Test sportsbook fixture.",
                        "outcomes": [
                            {
                                "outcome_id": "liberty-flames",
                                "name": "Liberty Flames",
                                "price": 100,
                                "price_format": "american",
                                "metadata": {"baseline_adjustment": 0.1},
                            },
                            {
                                "outcome_id": "arkansas-razorbacks",
                                "name": "Arkansas Razorbacks",
                                "price": -110,
                                "price_format": "american",
                                "metadata": {"baseline_adjustment": -0.02},
                            },
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    artifact_path = RestlessGamblerRunner(
        load_config(
            mode="paper",
            as_of=date(2026, 5, 31),
            markets_path=snapshot_path,
            artifacts_dir=tmp_path / "runs",
            min_liquidity=0.0,
            max_wager_cost=1.0,
            max_units_per_wager=1,
            allowed_venues=DEFAULT_ALLOWED_VENUES + ("draftkings",),
        )
    ).run()
    db_path = tmp_path / "restless.duckdb"
    import_run_artifact(artifact_path=artifact_path, db_path=db_path)

    def fake_fetch_sports_scores(*, sport, days_from=3, base_url=None):
        assert sport == "baseball_ncaa"
        assert days_from == 3
        assert base_url is None
        return SportsScoresFetch(
            events=[
                SportsScoreEvent(
                    event_id="event-1",
                    sport_key="baseball_ncaa",
                    completed=True,
                    home_team="Arkansas Razorbacks",
                    away_team="Liberty Flames",
                    scores={
                        "Liberty Flames": 5,
                        "Arkansas Razorbacks": 4,
                    },
                    last_update="2026-06-01T23:00:00Z",
                    raw={},
                )
            ],
            raw_event_count=1,
            base_url="https://api.the-odds-api.com/v4",
            sport="baseball_ncaa",
            days_from=3,
            generated_at="2026-06-02T00:00:00Z",
        )

    monkeypatch.setattr(
        "restless_gambler.settlement.fetch_sports_scores",
        fake_fetch_sports_scores,
    )

    summary = sync_sportsbook_paper_settlements(
        db_path=db_path,
        sport="baseball_ncaa",
    )
    ledger = ledger_status(db_path)

    assert summary.checked == 1
    assert summary.settled == 1
    assert summary.open_or_unresolved == 0
    assert summary.settlements[0]["settlement_status"] == "won"
    assert any(row["settlement_status"] == "won" for row in ledger["summary"])


def test_sync_sportsbook_paper_settlements_grades_spreads_and_totals(
    tmp_path,
    monkeypatch,
):
    snapshot_path = tmp_path / "sportsbook_snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "source": "test_sportsbook_snapshot",
                "generated_at": "2026-05-31T00:00:00Z",
                "markets": [
                    {
                        "market_id": "draftkings-event-1-spreads",
                        "event_id": "event-1",
                        "venue": "draftkings",
                        "product_type": "sportsbook",
                        "title": "Liberty Flames at Arkansas Razorbacks spreads",
                        "category": "baseball_ncaa",
                        "status": "open",
                        "close_time": "2026-06-01T23:00:00Z",
                        "liquidity": 100.0,
                        "volume": 100.0,
                        "rules_summary": "Test sportsbook spread fixture.",
                        "outcomes": [
                            {
                                "outcome_id": "liberty-flames-1.5",
                                "name": "Liberty Flames 1.5",
                                "price": 100,
                                "price_format": "american",
                                "metadata": {
                                    "baseline_adjustment": 0.1,
                                    "raw_name": "Liberty Flames",
                                    "point": 1.5,
                                    "market_key": "spreads",
                                },
                            },
                            {
                                "outcome_id": "arkansas-razorbacks--1.5",
                                "name": "Arkansas Razorbacks -1.5",
                                "price": -110,
                                "price_format": "american",
                                "metadata": {
                                    "baseline_adjustment": -0.02,
                                    "raw_name": "Arkansas Razorbacks",
                                    "point": -1.5,
                                    "market_key": "spreads",
                                },
                            },
                        ],
                    },
                    {
                        "market_id": "draftkings-event-1-totals",
                        "event_id": "event-1",
                        "venue": "draftkings",
                        "product_type": "sportsbook",
                        "title": "Liberty Flames at Arkansas Razorbacks totals",
                        "category": "baseball_ncaa",
                        "status": "open",
                        "close_time": "2026-06-01T23:00:00Z",
                        "liquidity": 100.0,
                        "volume": 100.0,
                        "rules_summary": "Test sportsbook total fixture.",
                        "outcomes": [
                            {
                                "outcome_id": "over-8.5",
                                "name": "Over 8.5",
                                "price": 100,
                                "price_format": "american",
                                "metadata": {
                                    "baseline_adjustment": 0.1,
                                    "raw_name": "Over",
                                    "point": 8.5,
                                    "market_key": "totals",
                                },
                            },
                            {
                                "outcome_id": "under-8.5",
                                "name": "Under 8.5",
                                "price": -110,
                                "price_format": "american",
                                "metadata": {
                                    "baseline_adjustment": -0.02,
                                    "raw_name": "Under",
                                    "point": 8.5,
                                    "market_key": "totals",
                                },
                            },
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    artifact_path = RestlessGamblerRunner(
        load_config(
            mode="paper",
            as_of=date(2026, 5, 31),
            markets_path=snapshot_path,
            artifacts_dir=tmp_path / "runs",
            min_liquidity=0.0,
            max_wager_cost=1.0,
            max_units_per_wager=1,
            allowed_venues=DEFAULT_ALLOWED_VENUES + ("draftkings",),
        )
    ).run()
    db_path = tmp_path / "restless.duckdb"
    import_run_artifact(artifact_path=artifact_path, db_path=db_path)

    def fake_fetch_sports_scores(*, sport, days_from=3, base_url=None):
        return SportsScoresFetch(
            events=[
                SportsScoreEvent(
                    event_id="event-1",
                    sport_key=sport,
                    completed=True,
                    home_team="Arkansas Razorbacks",
                    away_team="Liberty Flames",
                    scores={
                        "Liberty Flames": 5,
                        "Arkansas Razorbacks": 4,
                    },
                    last_update="2026-06-01T23:00:00Z",
                    raw={},
                )
            ],
            raw_event_count=1,
            base_url="https://api.the-odds-api.com/v4",
            sport=sport,
            days_from=days_from,
            generated_at="2026-06-02T00:00:00Z",
        )

    monkeypatch.setattr(
        "restless_gambler.settlement.fetch_sports_scores",
        fake_fetch_sports_scores,
    )

    summary = sync_sportsbook_paper_settlements(
        db_path=db_path,
        sport="baseball_ncaa",
    )
    ledger = ledger_status(db_path)

    assert summary.checked == 2
    assert summary.settled == 2
    assert summary.open_or_unresolved == 0
    assert {row["settlement_status"] for row in summary.settlements} == {"won"}
    assert any(row["settlement_status"] == "won" for row in ledger["summary"])
