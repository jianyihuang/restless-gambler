from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from restless_gambler.kalshi import (
    fetch_kalshi_market,
    kalshi_settlement_outcome,
)
from restless_gambler.persistence import (
    DEFAULT_DB_PATH,
    SettlementResult,
    open_paper_bets,
    settle_paper_bet,
)
from restless_gambler.sports_odds import (
    SportsScoreEvent,
    fetch_sports_scores,
    sports_outcome_id,
)


@dataclass(frozen=True)
class SettlementSyncSummary:
    db_path: str
    venue: str
    checked: int
    settled: int
    open_or_unresolved: int
    errors: list[dict[str, str]]
    settlements: list[dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class MarketSettlementSummary:
    db_path: str
    market_id: str
    checked: int
    settled: int
    settlements: list[dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def settle_market_paper_bets(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    market_id: str,
    winning_outcome_id: str | None = None,
    push: bool = False,
    venue: str | None = None,
    product_type: str | None = None,
    limit: int = 100,
) -> MarketSettlementSummary:
    if push and winning_outcome_id:
        msg = "use either push or winning_outcome_id, not both"
        raise ValueError(msg)
    if not push and not winning_outcome_id:
        msg = "winning_outcome_id is required unless push is true"
        raise ValueError(msg)

    open_bets = open_paper_bets(
        db_path=db_path,
        venue=venue,
        product_type=product_type,
        market_id=market_id,
        limit=limit,
    )
    settlements = [
        settle_paper_bet(
            client_order_id=str(bet["client_order_id"]),
            outcome=_market_settlement_outcome(
                bet_outcome_id=str(bet["outcome_id"]),
                winning_outcome_id=winning_outcome_id,
                push=push,
            ),
            db_path=db_path,
        ).to_dict()
        for bet in open_bets
    ]

    return MarketSettlementSummary(
        db_path=str(db_path),
        market_id=market_id,
        checked=len(open_bets),
        settled=len(settlements),
        settlements=settlements,
    )


def sync_kalshi_paper_settlements(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    base_url: str | None = None,
    limit: int = 100,
    allow_determined: bool = False,
) -> SettlementSyncSummary:
    open_bets = open_paper_bets(
        db_path=db_path,
        venue="kalshi",
        product_type="prediction_contract",
        limit=limit,
    )
    errors: list[dict[str, str]] = []
    settlements: list[dict[str, object]] = []
    open_or_unresolved = 0

    for bet in open_bets:
        client_order_id = str(bet["client_order_id"])
        market_id = str(bet["market_id"])
        outcome_id = str(bet["outcome_id"])
        try:
            raw_market = fetch_kalshi_market(
                ticker=market_id,
                base_url=base_url,
            )
            settlement_outcome = kalshi_settlement_outcome(
                raw_market,
                outcome_id=outcome_id,
                allow_determined=allow_determined,
            )
        except (OSError, ValueError, KeyError) as error:
            errors.append(
                {
                    "client_order_id": client_order_id,
                    "market_id": market_id,
                    "error": str(error),
                }
            )
            continue

        if settlement_outcome is None:
            open_or_unresolved += 1
            continue

        result = settle_paper_bet(
            client_order_id=client_order_id,
            outcome=settlement_outcome,
            db_path=db_path,
        )
        settlements.append(_settlement_payload(result, raw_market))

    return SettlementSyncSummary(
        db_path=str(db_path),
        venue="kalshi",
        checked=len(open_bets),
        settled=len(settlements),
        open_or_unresolved=open_or_unresolved,
        errors=errors,
        settlements=settlements,
    )


def sync_sportsbook_paper_settlements(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    sport: str,
    days_from: int = 3,
    base_url: str | None = None,
    limit: int = 100,
) -> SettlementSyncSummary:
    open_bets = [
        bet
        for bet in open_paper_bets(
            db_path=db_path,
            product_type="sportsbook",
            limit=limit,
        )
        if bet.get("category") == sport
    ]
    score_fetch = fetch_sports_scores(
        sport=sport,
        days_from=days_from,
        base_url=base_url,
    )
    scores_by_event = {event.event_id: event for event in score_fetch.events}
    errors: list[dict[str, str]] = []
    settlements: list[dict[str, object]] = []
    open_or_unresolved = 0

    for bet in open_bets:
        client_order_id = str(bet["client_order_id"])
        market_id = str(bet["market_id"])
        event_id = str(bet.get("event_id") or "")

        score_event = scores_by_event.get(event_id)
        if score_event is None:
            open_or_unresolved += 1
            continue

        try:
            settlement_outcome = _sportsbook_settlement_outcome(
                score_event=score_event,
                bet=bet,
            )
        except ValueError as error:
            errors.append(
                {
                    "client_order_id": client_order_id,
                    "market_id": market_id,
                    "error": str(error),
                }
            )
            continue

        if settlement_outcome is None:
            open_or_unresolved += 1
            continue

        result = settle_paper_bet(
            client_order_id=client_order_id,
            outcome=settlement_outcome,
            db_path=db_path,
        )
        settlements.append(
            {
                **result.to_dict(),
                "event_id": event_id,
                "sport": sport,
                "last_update": score_event.last_update,
            }
        )

    return SettlementSyncSummary(
        db_path=str(db_path),
        venue=f"sportsbook:{sport}",
        checked=len(open_bets),
        settled=len(settlements),
        open_or_unresolved=open_or_unresolved,
        errors=errors,
        settlements=settlements,
    )


def _settlement_payload(
    result: SettlementResult,
    raw_market: dict[str, Any],
) -> dict[str, object]:
    payload = result.to_dict()
    payload["market_status"] = raw_market.get("status")
    payload["market_result"] = raw_market.get("result")
    payload["settlement_ts"] = raw_market.get("settlement_ts")
    return payload


def _market_settlement_outcome(
    *,
    bet_outcome_id: str,
    winning_outcome_id: str | None,
    push: bool,
) -> str:
    if push:
        return "push"
    return "won" if bet_outcome_id == winning_outcome_id else "lost"


def _sportsbook_settlement_outcome(
    *,
    score_event: SportsScoreEvent,
    bet: dict[str, object],
) -> str | None:
    if not score_event.completed:
        return None

    metadata = bet.get("outcome_metadata")
    outcome_metadata = metadata if isinstance(metadata, dict) else {}
    market_id = str(bet["market_id"])
    market_key = str(
        outcome_metadata.get("market_key") or _market_key_from_market_id(market_id)
    )

    if market_key == "h2h":
        return _sportsbook_h2h_settlement_outcome(
            score_event=score_event,
            bet_outcome_id=str(bet["outcome_id"]),
        )
    if market_key == "spreads":
        return _sportsbook_spread_settlement_outcome(
            score_event=score_event,
            outcome_name=str(bet["outcome_name"]),
            outcome_metadata=outcome_metadata,
        )
    if market_key == "totals":
        return _sportsbook_total_settlement_outcome(
            score_event=score_event,
            outcome_name=str(bet["outcome_name"]),
            outcome_metadata=outcome_metadata,
        )
    return None


def _sportsbook_h2h_settlement_outcome(
    *,
    score_event: SportsScoreEvent,
    bet_outcome_id: str,
) -> str | None:
    if not score_event.completed:
        return None
    if len(score_event.scores) < 2:
        msg = f"completed event {score_event.event_id} has fewer than two scores"
        raise ValueError(msg)

    sorted_scores = sorted(score_event.scores.items(), key=lambda item: item[1])
    losing_score = sorted_scores[-2][1]
    winning_team, winning_score = sorted_scores[-1]
    if winning_score == losing_score:
        return "push"
    return "won" if sports_outcome_id(winning_team) == bet_outcome_id else "lost"


def _sportsbook_spread_settlement_outcome(
    *,
    score_event: SportsScoreEvent,
    outcome_name: str,
    outcome_metadata: dict[object, object],
) -> str:
    point = _metadata_point(outcome_metadata)
    team_name = _metadata_raw_name(outcome_metadata, outcome_name, point)
    team_score = _score_for_team(score_event, team_name)
    opponent_score = _opponent_score(score_event, team_name)
    adjusted_score = team_score + point
    if adjusted_score == opponent_score:
        return "push"
    return "won" if adjusted_score > opponent_score else "lost"


def _sportsbook_total_settlement_outcome(
    *,
    score_event: SportsScoreEvent,
    outcome_name: str,
    outcome_metadata: dict[object, object],
) -> str:
    point = _metadata_point(outcome_metadata)
    side = str(outcome_metadata.get("raw_name") or outcome_name).lower()
    total_score = sum(score_event.scores.values())
    if total_score == point:
        return "push"
    if side.startswith("over"):
        return "won" if total_score > point else "lost"
    if side.startswith("under"):
        return "won" if total_score < point else "lost"
    msg = f"unsupported totals outcome: {outcome_name}"
    raise ValueError(msg)


def _market_key_from_market_id(market_id: str) -> str:
    for market_key in ("h2h", "spreads", "totals"):
        if market_id.endswith(f"-{market_key}"):
            return market_key
    return ""


def _metadata_point(outcome_metadata: dict[object, object]) -> float:
    point = outcome_metadata.get("point")
    if point is None:
        msg = "spread/total settlement requires point metadata"
        raise ValueError(msg)
    return float(point)


def _metadata_raw_name(
    outcome_metadata: dict[object, object],
    outcome_name: str,
    point: float,
) -> str:
    raw_name = outcome_metadata.get("raw_name")
    if raw_name:
        return str(raw_name)
    suffix = f" {point:g}"
    return outcome_name.removesuffix(suffix)


def _score_for_team(score_event: SportsScoreEvent, team_name: str) -> float:
    if team_name in score_event.scores:
        return score_event.scores[team_name]
    team_id = sports_outcome_id(team_name)
    for name, score in score_event.scores.items():
        if sports_outcome_id(name) == team_id:
            return score
    msg = f"team score not found for {team_name} in {score_event.event_id}"
    raise ValueError(msg)


def _opponent_score(score_event: SportsScoreEvent, team_name: str) -> float:
    team_id = sports_outcome_id(team_name)
    opponents = [
        score
        for name, score in score_event.scores.items()
        if sports_outcome_id(name) != team_id
    ]
    if len(opponents) != 1:
        msg = (
            "spread settlement requires exactly one opponent in "
            f"{score_event.event_id}"
        )
        raise ValueError(msg)
    return opponents[0]
