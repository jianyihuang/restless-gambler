from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin

from restless_gambler.domain import Market, OutcomeQuote
from restless_gambler.env import load_dotenv
from restless_gambler.kalshi import market_to_snapshot_dict
from restless_gambler.mlb_stats import (
    enrich_mlb_markets_with_team_records,
    fetch_mlb_game_contexts,
    fetch_mlb_team_records,
    infer_mlb_season,
)

DEFAULT_ODDS_API_BASE_URL = "https://api.the-odds-api.com/v4"


@dataclass(frozen=True)
class SportsOddsFetch:
    markets: list[Market]
    raw_event_count: int
    base_url: str
    sport: str
    regions: str
    markets_requested: str
    generated_at: str
    warnings: list[str]

    def snapshot_payload(self) -> dict[str, object]:
        return {
            "source": "the_odds_api",
            "generated_at": self.generated_at,
            "base_url": self.base_url,
            "sport": self.sport,
            "regions": self.regions,
            "markets_requested": self.markets_requested,
            "raw_event_count": self.raw_event_count,
            "warnings": self.warnings,
            "markets": [market_to_snapshot_dict(market) for market in self.markets],
        }


@dataclass(frozen=True)
class SportsScoreEvent:
    event_id: str
    sport_key: str
    completed: bool
    home_team: str
    away_team: str
    scores: dict[str, float]
    last_update: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class SportsScoresFetch:
    events: list[SportsScoreEvent]
    raw_event_count: int
    base_url: str
    sport: str
    days_from: int
    generated_at: str


def fetch_sports_odds(
    *,
    sport: str,
    regions: str = "us",
    markets: str = "h2h",
    bookmakers: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout_seconds: int = 20,
) -> SportsOddsFetch:
    load_dotenv()
    resolved_api_key = api_key or os.environ.get("THE_ODDS_API_KEY", "").strip()
    if not resolved_api_key:
        msg = "THE_ODDS_API_KEY is required to fetch sports odds"
        raise ValueError(msg)

    resolved_base_url = (
        base_url
        or os.environ.get("THE_ODDS_API_BASE_URL", DEFAULT_ODDS_API_BASE_URL)
    ).rstrip("/")
    query: dict[str, object] = {
        "apiKey": resolved_api_key,
        "regions": regions,
        "markets": markets,
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    if bookmakers:
        query["bookmakers"] = bookmakers

    url = urljoin(f"{resolved_base_url}/", f"sports/{sport}/odds")
    url = f"{url}?{urlencode(query)}"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        msg = f"The Odds API request failed with HTTP {error.code}"
        raise ValueError(msg) from error

    if not isinstance(payload, list):
        msg = "The Odds API response was not an event list"
        raise ValueError(msg)

    warnings: list[str] = []
    normalized_markets: list[Market] = []
    normalized_events = [event for event in payload if isinstance(event, dict)]
    skipped_event_count = len(payload) - len(normalized_events)
    if skipped_event_count:
        warnings.append(f"skipped {skipped_event_count} non-object event(s)")
    for event in normalized_events:
        try:
            normalized_markets.extend(normalize_sports_event(event))
        except (KeyError, ValueError) as error:
            warnings.append(f"event skipped {event.get('id')}: {error}")
    if sport == "baseball_mlb" and normalized_markets:
        try:
            season = infer_mlb_season(normalized_events)
            records = fetch_mlb_team_records(season=season)
            contexts = fetch_mlb_game_contexts(
                events=normalized_events,
                season=season,
            )
            normalized_markets = enrich_mlb_markets_with_team_records(
                normalized_markets,
                records,
                contexts,
            )
        except (OSError, ValueError) as error:
            warnings.append(f"MLB enrichment skipped: {error}")

    return SportsOddsFetch(
        markets=normalized_markets,
        raw_event_count=len(payload),
        base_url=resolved_base_url,
        sport=sport,
        regions=regions,
        markets_requested=markets,
        generated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        warnings=warnings,
    )


def fetch_sports_scores(
    *,
    sport: str,
    days_from: int = 3,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout_seconds: int = 20,
) -> SportsScoresFetch:
    load_dotenv()
    if days_from < 1 or days_from > 3:
        msg = "days_from must be between 1 and 3"
        raise ValueError(msg)

    resolved_api_key = api_key or os.environ.get("THE_ODDS_API_KEY", "").strip()
    if not resolved_api_key:
        msg = "THE_ODDS_API_KEY is required to fetch sports scores"
        raise ValueError(msg)

    resolved_base_url = (
        base_url
        or os.environ.get("THE_ODDS_API_BASE_URL", DEFAULT_ODDS_API_BASE_URL)
    ).rstrip("/")
    query: dict[str, object] = {
        "apiKey": resolved_api_key,
        "daysFrom": days_from,
        "dateFormat": "iso",
    }

    url = urljoin(f"{resolved_base_url}/", f"sports/{sport}/scores")
    url = f"{url}?{urlencode(query)}"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        msg = f"The Odds API scores request failed with HTTP {error.code}"
        raise ValueError(msg) from error

    if not isinstance(payload, list):
        msg = "The Odds API scores response was not an event list"
        raise ValueError(msg)

    return SportsScoresFetch(
        events=[
            normalize_sports_score_event(event)
            for event in payload
            if isinstance(event, dict)
        ],
        raw_event_count=len(payload),
        base_url=resolved_base_url,
        sport=sport,
        days_from=days_from,
        generated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )


def normalize_sports_event(event: dict[str, Any]) -> list[Market]:
    event_id = str(event["id"])
    sport_key = str(event.get("sport_key") or "sports")
    sport_title = str(event.get("sport_title") or sport_key)
    home_team = str(event.get("home_team") or "")
    away_team = str(event.get("away_team") or "")
    commence_time = str(event.get("commence_time") or "9999-12-31T23:59:59Z")
    title = f"{away_team} at {home_team}".strip() or event_id

    markets: list[Market] = []
    for bookmaker in _list_value(event.get("bookmakers")):
        book_key = str(bookmaker.get("key") or "")
        if not book_key:
            continue
        book_title = str(bookmaker.get("title") or book_key)
        for book_market in _list_value(bookmaker.get("markets")):
            market_key = str(book_market.get("key") or "")
            outcomes = [
                _normalize_outcome(
                    outcome,
                    market_key=market_key,
                    home_team=home_team,
                    away_team=away_team,
                    commence_time=commence_time,
                )
                for outcome in _list_value(book_market.get("outcomes"))
            ]
            if not outcomes:
                continue
            market_id = _safe_id(f"{book_key}:{event_id}:{market_key}")
            markets.append(
                Market(
                    market_id=market_id,
                    event_id=event_id,
                    venue=book_key,
                    product_type="sportsbook",
                    title=f"{title} {market_key} at {book_title}",
                    category=sport_key,
                    status="open",
                    close_time=commence_time,
                    liquidity=0.0,
                    volume=0.0,
                    rules_summary=(
                        f"{sport_title} sportsbook odds from {book_title}. "
                        "Settlement depends on bookmaker house rules."
                    ),
                    outcomes=outcomes,
                )
            )
    return markets


def normalize_sports_score_event(event: dict[str, Any]) -> SportsScoreEvent:
    return SportsScoreEvent(
        event_id=str(event["id"]),
        sport_key=str(event.get("sport_key") or ""),
        completed=bool(event.get("completed")),
        home_team=str(event.get("home_team") or ""),
        away_team=str(event.get("away_team") or ""),
        scores=_score_map(event.get("scores")),
        last_update=(
            str(event["last_update"]) if event.get("last_update") is not None else None
        ),
        raw=event,
    )


def write_sports_odds_snapshot(
    *,
    output_path: Path,
    fetch: SportsOddsFetch,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(fetch.snapshot_payload(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_path


def _normalize_outcome(
    outcome: dict[str, Any],
    *,
    market_key: str,
    home_team: str,
    away_team: str,
    commence_time: str,
) -> OutcomeQuote:
    name = str(outcome["name"])
    point = outcome.get("point")
    price = float(outcome["price"])
    label = f"{name} {point}" if point is not None else name
    return OutcomeQuote(
        outcome_id=sports_outcome_id(label),
        name=label,
        price=price,
        price_format="american",
        implied_probability=_american_implied_probability(price),
        metadata={
            "raw_name": name,
            "point": point,
            "market_key": market_key,
            "home_team": home_team,
            "away_team": away_team,
            "commence_time": commence_time,
        },
    )


def _american_implied_probability(price: float) -> float:
    if price == 0:
        msg = "american odds cannot be zero"
        raise ValueError(msg)
    if price > 0:
        return 100.0 / (price + 100.0)
    return abs(price) / (abs(price) + 100.0)


def _list_value(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _score_map(value: object) -> dict[str, float]:
    scores: dict[str, float] = {}
    for item in _list_value(value):
        name = item.get("name")
        score = item.get("score")
        if name is None or score is None:
            continue
        try:
            scores[str(name)] = float(score)
        except (TypeError, ValueError):
            continue
    return scores


def sports_outcome_id(value: str) -> str:
    return _safe_id(value)


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-").lower()
