from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urljoin

from restless_gambler.domain import Market, OutcomeQuote

MLB_STATS_API_BASE_URL = "https://statsapi.mlb.com/api/v1"


@dataclass(frozen=True)
class MlbTeamRecord:
    team_id: int
    name: str
    wins: int
    losses: int
    winning_percentage: float
    run_differential: int
    games_played: int
    source: str = "mlb_stats_api_standings"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class MlbPitcherStats:
    player_id: int
    name: str
    era: float | None
    whip: float | None
    strikeouts_per_9: float | None
    walks_per_9: float | None
    innings_pitched: float
    games_started: int
    source: str = "mlb_stats_api_pitching"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class MlbBullpenRest:
    team_id: int
    team_name: str
    recent_games: int
    days_since_last_game: int | None
    last_game_innings: int | None
    source: str = "mlb_stats_api_schedule"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class MlbGameTeamContext:
    team_name: str
    opponent_name: str
    game_date: str
    probable_pitcher: MlbPitcherStats | None
    bullpen_rest: MlbBullpenRest

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def fetch_mlb_team_records(
    *,
    season: int | None = None,
    base_url: str = MLB_STATS_API_BASE_URL,
    timeout_seconds: int = 20,
) -> dict[str, MlbTeamRecord]:
    query: dict[str, object] = {
        "leagueId": "103,104",
        "standingsTypes": "regularSeason",
        "hydrate": "team",
    }
    if season is not None:
        query["season"] = season

    url = urljoin(f"{base_url.rstrip('/')}/", "standings")
    url = f"{url}?{urlencode(query)}"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        msg = f"MLB Stats API standings request failed with HTTP {error.code}"
        raise ValueError(msg) from error

    if not isinstance(payload, dict):
        msg = "MLB Stats API standings response was not a JSON object"
        raise ValueError(msg)

    return _records_by_lookup_key(payload)


def fetch_mlb_game_contexts(
    *,
    events: list[dict[str, Any]],
    season: int | None = None,
    base_url: str = MLB_STATS_API_BASE_URL,
    timeout_seconds: int = 20,
) -> dict[tuple[str, str, str], dict[str, MlbGameTeamContext]]:
    event_dates = [_event_date(event) for event in events]
    event_dates = [event_date for event_date in event_dates if event_date is not None]
    if not event_dates:
        return {}

    start_date = min(event_dates) - timedelta(days=4)
    end_date = max(event_dates)
    schedule = _fetch_schedule(
        start_date=start_date,
        end_date=end_date,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
    )
    games = [
        game
        for day in _list_dicts(schedule.get("dates"))
        for game in _list_dicts(day.get("games"))
    ]
    pitcher_cache: dict[int, MlbPitcherStats | None] = {}
    contexts: dict[tuple[str, str, str], dict[str, MlbGameTeamContext]] = {}

    for game in games:
        game_date = _game_date(game)
        if game_date not in event_dates:
            continue
        teams = game.get("teams")
        if not isinstance(teams, dict):
            continue
        home = _team_side(teams, "home")
        away = _team_side(teams, "away")
        if home is None or away is None:
            continue

        home_name = str(home["team"]["name"])
        away_name = str(away["team"]["name"])
        contexts[_game_key(home_name, away_name, game_date.isoformat())] = {
            _safe_token(home_name): MlbGameTeamContext(
                team_name=home_name,
                opponent_name=away_name,
                game_date=game_date.isoformat(),
                probable_pitcher=_probable_pitcher_stats(
                    side=home,
                    season=season,
                    base_url=base_url,
                    timeout_seconds=timeout_seconds,
                    pitcher_cache=pitcher_cache,
                ),
                bullpen_rest=_bullpen_rest(
                    games=games,
                    team=home["team"],
                    game_date=game_date,
                ),
            ),
            _safe_token(away_name): MlbGameTeamContext(
                team_name=away_name,
                opponent_name=home_name,
                game_date=game_date.isoformat(),
                probable_pitcher=_probable_pitcher_stats(
                    side=away,
                    season=season,
                    base_url=base_url,
                    timeout_seconds=timeout_seconds,
                    pitcher_cache=pitcher_cache,
                ),
                bullpen_rest=_bullpen_rest(
                    games=games,
                    team=away["team"],
                    game_date=game_date,
                ),
            ),
        }

    return contexts


def enrich_mlb_markets_with_team_records(
    markets: list[Market],
    records_by_key: Mapping[str, MlbTeamRecord],
    contexts_by_game: Mapping[
        tuple[str, str, str],
        Mapping[str, MlbGameTeamContext],
    ]
    | None = None,
) -> list[Market]:
    contexts_by_game = contexts_by_game or {}
    enriched: list[Market] = []
    for market in markets:
        if market.category != "baseball_mlb":
            enriched.append(market)
            continue

        records_by_outcome_id = {
            outcome.outcome_id: _record_for_outcome(outcome, records_by_key)
            for outcome in market.outcomes
        }
        matched_records = {
            outcome_id: record
            for outcome_id, record in records_by_outcome_id.items()
            if record is not None
        }
        if len(matched_records) < 2:
            enriched.append(market)
            continue

        enriched_outcomes = [
            _enrich_outcome(
                outcome=outcome,
                record=records_by_outcome_id.get(outcome.outcome_id),
                opponent_record=_opponent_record(
                    outcome_id=outcome.outcome_id,
                    records_by_outcome_id=matched_records,
                ),
                context=_context_for_outcome(outcome, contexts_by_game),
                opponent_context=_opponent_context_for_outcome(
                    outcome,
                    contexts_by_game,
                ),
            )
            for outcome in market.outcomes
        ]
        enriched.append(replace(market, outcomes=enriched_outcomes))
    return enriched


def infer_mlb_season(events: list[dict[str, Any]]) -> int:
    for event in events:
        commence_time = str(event.get("commence_time") or "")
        if len(commence_time) >= 4 and commence_time[:4].isdigit():
            return int(commence_time[:4])
    return datetime.now(UTC).year


def _records_by_lookup_key(payload: dict[str, Any]) -> dict[str, MlbTeamRecord]:
    records_by_key: dict[str, MlbTeamRecord] = {}
    for division in _list_dicts(payload.get("records")):
        for row in _list_dicts(division.get("teamRecords")):
            team = row.get("team")
            if not isinstance(team, dict):
                continue
            try:
                record = _parse_team_record(row, team)
            except (TypeError, ValueError):
                continue
            for key in _team_lookup_keys(team):
                records_by_key[key] = record
    return records_by_key


def _parse_team_record(
    row: dict[str, Any],
    team: dict[str, Any],
) -> MlbTeamRecord:
    wins = int(row.get("wins") or row.get("leagueRecord", {}).get("wins") or 0)
    losses = int(row.get("losses") or row.get("leagueRecord", {}).get("losses") or 0)
    games_played = int(row.get("gamesPlayed") or wins + losses)
    winning_percentage = _winning_percentage(row, wins=wins, losses=losses)
    return MlbTeamRecord(
        team_id=int(team["id"]),
        name=str(team["name"]),
        wins=wins,
        losses=losses,
        winning_percentage=winning_percentage,
        run_differential=int(row.get("runDifferential") or 0),
        games_played=games_played,
    )


def _fetch_schedule(
    *,
    start_date: date,
    end_date: date,
    base_url: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    query: dict[str, object] = {
        "sportId": 1,
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "hydrate": "probablePitcher,team,linescore",
    }
    url = urljoin(f"{base_url.rstrip('/')}/", "schedule")
    url = f"{url}?{urlencode(query)}"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        msg = f"MLB Stats API schedule request failed with HTTP {error.code}"
        raise ValueError(msg) from error
    if not isinstance(payload, dict):
        msg = "MLB Stats API schedule response was not a JSON object"
        raise ValueError(msg)
    return payload


def _fetch_pitcher_stats(
    *,
    player_id: int,
    name: str,
    season: int | None,
    base_url: str,
    timeout_seconds: int,
) -> MlbPitcherStats | None:
    query: dict[str, object] = {
        "stats": "season",
        "group": "pitching",
    }
    if season is not None:
        query["season"] = season
    url = urljoin(f"{base_url.rstrip('/')}/", f"people/{player_id}/stats")
    url = f"{url}?{urlencode(query)}"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError:
        return None
    if not isinstance(payload, dict):
        return None

    stat = _first_pitching_stat(payload)
    if stat is None:
        return None
    return MlbPitcherStats(
        player_id=player_id,
        name=name,
        era=_optional_float(stat.get("era")),
        whip=_optional_float(stat.get("whip")),
        strikeouts_per_9=_optional_float(stat.get("strikeoutsPer9Inn")),
        walks_per_9=_optional_float(stat.get("walksPer9Inn")),
        innings_pitched=_innings_float(stat.get("inningsPitched")),
        games_started=int(stat.get("gamesStarted") or 0),
    )


def _first_pitching_stat(payload: dict[str, Any]) -> dict[str, Any] | None:
    for stats in _list_dicts(payload.get("stats")):
        for split in _list_dicts(stats.get("splits")):
            stat = split.get("stat")
            if isinstance(stat, dict):
                return stat
    return None


def _winning_percentage(row: dict[str, Any], *, wins: int, losses: int) -> float:
    raw = row.get("winningPercentage") or row.get("leagueRecord", {}).get("pct")
    if raw not in {None, ""}:
        text = str(raw)
        return float(f"0{text}" if text.startswith(".") else text)
    total = wins + losses
    return wins / total if total else 0.0


def _team_lookup_keys(team: dict[str, Any]) -> set[str]:
    names = {
        team.get("name"),
        team.get("shortName"),
        team.get("teamName"),
        team.get("clubName"),
        team.get("locationName"),
        team.get("abbreviation"),
        team.get("fileCode"),
    }
    location = team.get("locationName")
    club = team.get("clubName")
    if location and club:
        names.add(f"{location} {club}")
    return {_safe_token(str(name)) for name in names if name}


def _record_for_outcome(
    outcome: OutcomeQuote,
    records_by_key: Mapping[str, MlbTeamRecord],
) -> MlbTeamRecord | None:
    raw_name = outcome.metadata.get("raw_name")
    for name in (raw_name, outcome.name):
        key = _safe_token(str(name or ""))
        if key in records_by_key:
            return records_by_key[key]
    return None


def _opponent_record(
    *,
    outcome_id: str,
    records_by_outcome_id: Mapping[str, MlbTeamRecord],
) -> MlbTeamRecord | None:
    opponents = [
        record
        for candidate_outcome_id, record in records_by_outcome_id.items()
        if candidate_outcome_id != outcome_id
    ]
    return opponents[0] if len(opponents) == 1 else None


def _enrich_outcome(
    *,
    outcome: OutcomeQuote,
    record: MlbTeamRecord | None,
    opponent_record: MlbTeamRecord | None,
    context: MlbGameTeamContext | None,
    opponent_context: MlbGameTeamContext | None,
) -> OutcomeQuote:
    if record is None or opponent_record is None:
        return outcome

    games_played = max(record.games_played, 1)
    opponent_games_played = max(opponent_record.games_played, 1)
    metadata = dict(outcome.metadata)
    metadata["mlb_team_record"] = record.to_dict()
    metadata["mlb_opponent_record"] = opponent_record.to_dict()
    metadata["mlb_record_win_pct_edge"] = round(
        record.winning_percentage - opponent_record.winning_percentage,
        4,
    )
    metadata["mlb_record_run_diff_per_game_edge"] = round(
        (record.run_differential / games_played)
        - (opponent_record.run_differential / opponent_games_played),
        4,
    )
    if context is not None and opponent_context is not None:
        metadata["mlb_game_context"] = context.to_dict()
        metadata["mlb_opponent_game_context"] = opponent_context.to_dict()
        pitcher_edge = _pitcher_edge(
            context.probable_pitcher,
            opponent_context.probable_pitcher,
        )
        bullpen_edge = _bullpen_rest_edge(
            context.bullpen_rest,
            opponent_context.bullpen_rest,
        )
        if pitcher_edge is not None:
            metadata["mlb_probable_pitcher_edge"] = pitcher_edge
        if bullpen_edge is not None:
            metadata["mlb_bullpen_rest_edge"] = bullpen_edge
    return replace(outcome, metadata=metadata)


def _context_for_outcome(
    outcome: OutcomeQuote,
    contexts_by_game: Mapping[
        tuple[str, str, str],
        Mapping[str, MlbGameTeamContext],
    ],
) -> MlbGameTeamContext | None:
    context_map = contexts_by_game.get(_game_key_from_outcome(outcome))
    if context_map is None:
        return None
    raw_name = str(outcome.metadata.get("raw_name") or outcome.name)
    return context_map.get(_safe_token(raw_name))


def _opponent_context_for_outcome(
    outcome: OutcomeQuote,
    contexts_by_game: Mapping[
        tuple[str, str, str],
        Mapping[str, MlbGameTeamContext],
    ],
) -> MlbGameTeamContext | None:
    context_map = contexts_by_game.get(_game_key_from_outcome(outcome))
    if context_map is None:
        return None
    team_key = _safe_token(str(outcome.metadata.get("raw_name") or outcome.name))
    opponents = [
        context
        for candidate_key, context in context_map.items()
        if candidate_key != team_key
    ]
    return opponents[0] if len(opponents) == 1 else None


def _game_key_from_outcome(outcome: OutcomeQuote) -> tuple[str, str, str]:
    home_team = str(outcome.metadata.get("home_team") or "")
    away_team = str(outcome.metadata.get("away_team") or "")
    commence_time = str(outcome.metadata.get("commence_time") or "")
    game_date = _date_from_iso(commence_time)
    return _game_key(home_team, away_team, game_date.isoformat() if game_date else "")


def _game_key(home_team: str, away_team: str, game_date: str) -> tuple[str, str, str]:
    return _safe_token(home_team), _safe_token(away_team), game_date


def _event_date(event: dict[str, Any]) -> date | None:
    return _date_from_iso(str(event.get("commence_time") or ""))


def _game_date(game: dict[str, Any]) -> date:
    official_date = str(game.get("officialDate") or "")
    if official_date:
        return date.fromisoformat(official_date)
    parsed = _date_from_iso(str(game.get("gameDate") or ""))
    return parsed or datetime.now(UTC).date()


def _date_from_iso(value: str) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _team_side(
    teams: dict[str, Any],
    side: str,
) -> dict[str, Any] | None:
    team_side = teams.get(side)
    if not isinstance(team_side, dict):
        return None
    team = team_side.get("team")
    return team_side if isinstance(team, dict) else None


def _probable_pitcher_stats(
    *,
    side: dict[str, Any],
    season: int | None,
    base_url: str,
    timeout_seconds: int,
    pitcher_cache: dict[int, MlbPitcherStats | None],
) -> MlbPitcherStats | None:
    probable_pitcher = side.get("probablePitcher")
    if not isinstance(probable_pitcher, dict):
        return None
    player_id = probable_pitcher.get("id")
    if player_id is None:
        return None
    player_id = int(player_id)
    if player_id not in pitcher_cache:
        pitcher_cache[player_id] = _fetch_pitcher_stats(
            player_id=player_id,
            name=str(probable_pitcher.get("fullName") or player_id),
            season=season,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )
    return pitcher_cache[player_id]


def _bullpen_rest(
    *,
    games: list[dict[str, Any]],
    team: dict[str, Any],
    game_date: date,
) -> MlbBullpenRest:
    team_id = int(team["id"])
    previous_games = [
        game
        for game in games
        if _game_date(game) < game_date
        and _game_involves_team(game, team_id)
        and str(game.get("status", {}).get("abstractGameState") or "") == "Final"
    ]
    recent_games = [
        game
        for game in previous_games
        if (game_date - _game_date(game)).days <= 3
    ]
    if not previous_games:
        return MlbBullpenRest(
            team_id=team_id,
            team_name=str(team["name"]),
            recent_games=0,
            days_since_last_game=None,
            last_game_innings=None,
        )

    latest_game = max(previous_games, key=_game_date)
    return MlbBullpenRest(
        team_id=team_id,
        team_name=str(team["name"]),
        recent_games=len(recent_games),
        days_since_last_game=(game_date - _game_date(latest_game)).days,
        last_game_innings=_game_innings(latest_game),
    )


def _game_involves_team(game: dict[str, Any], team_id: int) -> bool:
    teams = game.get("teams")
    if not isinstance(teams, dict):
        return False
    return any(
        isinstance(team_side, dict)
        and isinstance(team_side.get("team"), dict)
        and int(team_side["team"].get("id") or 0) == team_id
        for team_side in teams.values()
    )


def _game_innings(game: dict[str, Any]) -> int | None:
    linescore = game.get("linescore")
    if not isinstance(linescore, dict):
        return None
    current_inning = linescore.get("currentInning")
    if current_inning is not None:
        return int(current_inning)
    innings = linescore.get("innings")
    return len(innings) if isinstance(innings, list) else None


def _pitcher_edge(
    pitcher: MlbPitcherStats | None,
    opponent_pitcher: MlbPitcherStats | None,
) -> float | None:
    if pitcher is None or opponent_pitcher is None:
        return None
    components = [
        _edge_component(opponent_pitcher.era, pitcher.era, 0.01),
        _edge_component(opponent_pitcher.whip, pitcher.whip, 0.03),
        _edge_component(
            pitcher.strikeouts_per_9,
            opponent_pitcher.strikeouts_per_9,
            0.003,
        ),
        _edge_component(opponent_pitcher.walks_per_9, pitcher.walks_per_9, 0.002),
    ]
    components = [component for component in components if component is not None]
    if not components:
        return None
    return round(max(-0.04, min(0.04, sum(components))), 4)


def _edge_component(
    better_value: float | None,
    worse_value: float | None,
    weight: float,
) -> float | None:
    if better_value is None or worse_value is None:
        return None
    return (better_value - worse_value) * weight


def _bullpen_rest_edge(
    rest: MlbBullpenRest,
    opponent_rest: MlbBullpenRest,
) -> float:
    return round(
        max(-0.025, min(0.025, _rest_score(rest) - _rest_score(opponent_rest))),
        4,
    )


def _rest_score(rest: MlbBullpenRest) -> float:
    days_rest = min(rest.days_since_last_game or 0, 3)
    extra_innings = max(0, (rest.last_game_innings or 9) - 9)
    return (days_rest * 0.01) - (rest.recent_games * 0.005) - (extra_innings * 0.006)


def _optional_float(value: object) -> float | None:
    if value in {None, "", ".---"}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _innings_float(value: object) -> float:
    if value in {None, ""}:
        return 0.0
    text = str(value)
    if "." not in text:
        return float(text)
    whole, outs = text.split(".", 1)
    return float(whole) + (int(outs[:1] or 0) / 3.0)


def _list_dicts(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _safe_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
