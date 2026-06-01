from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from statistics import median

from restless_gambler.domain import Market, OutcomeQuote, ResearchNote, ResearchSignal

MIN_CONSENSUS_VENUES = 3
MAX_CONSENSUS_OUTLIER_DEVIATION = 0.15
MAX_ABS_AMERICAN_ODDS_FOR_CONSENSUS = 5_000


def build_research_notes(markets: list[Market]) -> list[ResearchNote]:
    consensus_signals = build_sportsbook_consensus_signals(markets)
    notes: list[ResearchNote] = []
    for market in markets:
        signals = build_market_signals(
            market,
            extra_signals=consensus_signals.get(market.market_id, []),
        )
        quote_count = len(market.outcomes)
        probability_signal_count = sum(
            1 for signal in signals if signal.kind == "probability_adjustment"
        )
        notes.append(
            ResearchNote(
                market_id=market.market_id,
                summary=(
                    f"{market.product_type} market on {market.venue} with "
                    f"${market.liquidity:,.0f} liquidity, {quote_count} outcomes, "
                    f"{len(signals)} structured signal(s), "
                    f"{probability_signal_count} probability adjustment(s), "
                    f"and rules: {market.rules_summary}"
                ),
                sources=_sources(signals),
                confidence=_note_confidence(signals),
                signals=signals,
            )
        )
    return notes


def build_market_signals(
    market: Market,
    *,
    extra_signals: Iterable[ResearchSignal] = (),
) -> list[ResearchSignal]:
    signals: list[ResearchSignal] = list(extra_signals)
    for outcome in market.outcomes:
        signals.extend(_outcome_signals(market, outcome))

    if market.product_type == "sportsbook":
        overround = sum(outcome.implied_probability for outcome in market.outcomes) - 1
        if overround > 0:
            signals.append(
                ResearchSignal(
                    market_id=market.market_id,
                    outcome_id=None,
                    kind="market_quality",
                    name="sportsbook_overround",
                    direction=-1.0,
                    magnitude=round(overround, 4),
                    confidence=0.8,
                    source="market_snapshot",
                    rationale=(
                        f"Sportsbook implied probabilities include "
                        f"{overround:.2%} overround before any model edge."
                    ),
                )
            )

    signals.append(
        ResearchSignal(
            market_id=market.market_id,
            outcome_id=None,
            kind="market_context",
            name=_liquidity_signal_name(market.liquidity),
            direction=0.0,
            magnitude=round(market.liquidity, 2),
            confidence=0.6,
            source="market_snapshot",
            rationale=f"Market activity/liquidity proxy is {market.liquidity:,.0f}.",
        )
    )
    return signals


def build_sportsbook_consensus_signals(
    markets: list[Market],
) -> dict[str, list[ResearchSignal]]:
    grouped_markets: dict[tuple[object, ...], list[Market]] = defaultdict(list)
    for market in markets:
        if market.product_type != "sportsbook" or market.status != "open":
            continue
        group_key = _sports_consensus_group_key(market)
        if group_key:
            grouped_markets[group_key].append(market)

    signals_by_market: dict[str, list[ResearchSignal]] = defaultdict(list)
    for group in grouped_markets.values():
        valid_group = [
            market for market in group if not _market_has_extreme_price(market)
        ]
        venues = {market.venue for market in valid_group}
        if len(venues) < MIN_CONSENSUS_VENUES:
            continue

        normalized_by_outcome: dict[tuple[object, ...], list[float]] = defaultdict(
            list
        )
        normalized_by_market: dict[str, dict[tuple[object, ...], float]] = {}
        for market in valid_group:
            total_implied = sum(
                outcome.implied_probability for outcome in market.outcomes
            )
            if total_implied <= 0:
                continue
            market_probabilities: dict[tuple[object, ...], float] = {}
            for outcome in market.outcomes:
                outcome_key = _sports_outcome_key(outcome)
                no_vig_probability = outcome.implied_probability / total_implied
                market_probabilities[outcome_key] = no_vig_probability
                normalized_by_outcome[outcome_key].append(no_vig_probability)
            normalized_by_market[market.market_id] = market_probabilities

        consensus = _robust_consensus_probabilities(normalized_by_outcome)
        confidence = _consensus_confidence(len(venues))
        for market in valid_group:
            market_probabilities = normalized_by_market.get(market.market_id, {})
            for outcome in market.outcomes:
                outcome_key = _sports_outcome_key(outcome)
                consensus_probability = consensus.get(outcome_key)
                if (
                    consensus_probability is None
                    or outcome_key not in market_probabilities
                ):
                    continue
                adjustment = consensus_probability - outcome.implied_probability
                if abs(adjustment) < 0.001:
                    continue
                signals_by_market[market.market_id].append(
                    ResearchSignal(
                        market_id=market.market_id,
                        outcome_id=outcome.outcome_id,
                        kind="probability_adjustment",
                        name="sportsbook_consensus_no_vig",
                        direction=1.0 if adjustment > 0 else -1.0,
                        magnitude=round(abs(adjustment), 4),
                        confidence=confidence,
                        source="sportsbook_consensus",
                        rationale=(
                            f"{len(venues)} sportsbook venue(s) imply a no-vig "
                            f"consensus fair probability of "
                            f"{consensus_probability:.2%} for {outcome.name}."
                        ),
                    )
                )

    return dict(signals_by_market)


def _robust_consensus_probabilities(
    normalized_by_outcome: dict[tuple[object, ...], list[float]],
) -> dict[tuple[object, ...], float]:
    consensus: dict[tuple[object, ...], float] = {}
    for outcome_key, probabilities in normalized_by_outcome.items():
        if len(probabilities) < MIN_CONSENSUS_VENUES:
            continue
        midpoint = median(probabilities)
        filtered = [
            probability
            for probability in probabilities
            if abs(probability - midpoint) <= MAX_CONSENSUS_OUTLIER_DEVIATION
        ]
        if len(filtered) < MIN_CONSENSUS_VENUES:
            continue
        consensus[outcome_key] = sum(filtered) / len(filtered)
    return consensus


def _market_has_extreme_price(market: Market) -> bool:
    return any(
        outcome.price_format == "american"
        and abs(outcome.price) > MAX_ABS_AMERICAN_ODDS_FOR_CONSENSUS
        for outcome in market.outcomes
    )


def _outcome_signals(
    market: Market,
    outcome: OutcomeQuote,
) -> list[ResearchSignal]:
    signals: list[ResearchSignal] = []
    adjustment = float(outcome.metadata.get("baseline_adjustment", 0.0))
    if adjustment:
        signals.append(
            ResearchSignal(
                market_id=market.market_id,
                outcome_id=outcome.outcome_id,
                kind="probability_adjustment",
                name="fixture_baseline_adjustment",
                direction=1.0 if adjustment > 0 else -1.0,
                magnitude=round(abs(adjustment), 4),
                confidence=0.4,
                source="fixture_metadata",
                rationale=(
                    "Fixture-only probability adjustment used for framework "
                    "testing; do not treat as independent live edge."
                ),
            )
        )

    mlb_record_signal = _mlb_team_record_signal(market, outcome)
    if mlb_record_signal is not None:
        signals.append(mlb_record_signal)
    mlb_pitcher_signal = _mlb_probable_pitcher_signal(market, outcome)
    if mlb_pitcher_signal is not None:
        signals.append(mlb_pitcher_signal)
    mlb_bullpen_signal = _mlb_bullpen_rest_signal(market, outcome)
    if mlb_bullpen_signal is not None:
        signals.append(mlb_bullpen_signal)

    if outcome.bid is not None and outcome.ask is not None:
        spread = max(0.0, outcome.ask - outcome.bid)
        signals.append(
            ResearchSignal(
                market_id=market.market_id,
                outcome_id=outcome.outcome_id,
                kind="market_quality",
                name="bid_ask_spread",
                direction=-1.0 if spread >= 0.1 else 0.0,
                magnitude=round(spread, 4),
                confidence=0.7,
                source="market_microstructure",
                rationale=(
                    f"{outcome.name} bid/ask spread is {spread:.2%}; wider "
                    "spreads require stronger edge to overcome execution cost."
                ),
            )
        )

    return signals


def _mlb_team_record_signal(
    market: Market,
    outcome: OutcomeQuote,
) -> ResearchSignal | None:
    if market.category != "baseball_mlb" or market.product_type != "sportsbook":
        return None

    win_pct_edge = _metadata_float(outcome.metadata.get("mlb_record_win_pct_edge"))
    run_diff_edge = _metadata_float(
        outcome.metadata.get("mlb_record_run_diff_per_game_edge")
    )
    team_record = outcome.metadata.get("mlb_team_record")
    opponent_record = outcome.metadata.get("mlb_opponent_record")
    if win_pct_edge is None or run_diff_edge is None:
        return None

    adjustment = max(-0.035, min(0.035, (win_pct_edge * 0.12) + (run_diff_edge * 0.01)))
    if abs(adjustment) < 0.002:
        return None

    team_summary = _record_summary(team_record)
    opponent_summary = _record_summary(opponent_record)
    return ResearchSignal(
        market_id=market.market_id,
        outcome_id=outcome.outcome_id,
        kind="probability_adjustment",
        name="mlb_team_record_strength",
        direction=1.0 if adjustment > 0 else -1.0,
        magnitude=round(abs(adjustment), 4),
        confidence=0.5,
        source="mlb_stats_api_standings",
        rationale=(
            f"MLB standings favor {outcome.name}: {team_summary} versus "
            f"{opponent_summary}."
        ),
    )


def _mlb_probable_pitcher_signal(
    market: Market,
    outcome: OutcomeQuote,
) -> ResearchSignal | None:
    if market.category != "baseball_mlb" or market.product_type != "sportsbook":
        return None

    edge = _metadata_float(outcome.metadata.get("mlb_probable_pitcher_edge"))
    context = outcome.metadata.get("mlb_game_context")
    opponent_context = outcome.metadata.get("mlb_opponent_game_context")
    if edge is None or abs(edge) < 0.002:
        return None

    return ResearchSignal(
        market_id=market.market_id,
        outcome_id=outcome.outcome_id,
        kind="probability_adjustment",
        name="mlb_probable_pitcher_strength",
        direction=1.0 if edge > 0 else -1.0,
        magnitude=round(abs(edge), 4),
        confidence=0.5,
        source="mlb_stats_api_pitching",
        rationale=(
            f"Probable pitching matchup for {outcome.name}: "
            f"{_pitcher_summary(context)} versus "
            f"{_pitcher_summary(opponent_context)}."
        ),
    )


def _mlb_bullpen_rest_signal(
    market: Market,
    outcome: OutcomeQuote,
) -> ResearchSignal | None:
    if market.category != "baseball_mlb" or market.product_type != "sportsbook":
        return None

    edge = _metadata_float(outcome.metadata.get("mlb_bullpen_rest_edge"))
    context = outcome.metadata.get("mlb_game_context")
    opponent_context = outcome.metadata.get("mlb_opponent_game_context")
    if edge is None or abs(edge) < 0.002:
        return None

    return ResearchSignal(
        market_id=market.market_id,
        outcome_id=outcome.outcome_id,
        kind="probability_adjustment",
        name="mlb_bullpen_rest_proxy",
        direction=1.0 if edge > 0 else -1.0,
        magnitude=round(abs(edge), 4),
        confidence=0.45,
        source="mlb_stats_api_schedule",
        rationale=(
            f"Recent schedule bullpen-rest proxy for {outcome.name}: "
            f"{_bullpen_summary(context)} versus "
            f"{_bullpen_summary(opponent_context)}."
        ),
    )


def _metadata_float(value: object) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _record_summary(value: object) -> str:
    if not isinstance(value, dict):
        return "record unavailable"
    name = value.get("name", "team")
    wins = value.get("wins", "?")
    losses = value.get("losses", "?")
    run_differential = value.get("run_differential", "?")
    return f"{name} {wins}-{losses}, run differential {run_differential}"


def _pitcher_summary(value: object) -> str:
    if not isinstance(value, dict):
        return "pitcher unavailable"
    pitcher = value.get("probable_pitcher")
    if not isinstance(pitcher, dict):
        return "pitcher unavailable"
    name = pitcher.get("name", "pitcher")
    era = pitcher.get("era")
    whip = pitcher.get("whip")
    return f"{name}, ERA {era}, WHIP {whip}"


def _bullpen_summary(value: object) -> str:
    if not isinstance(value, dict):
        return "rest unavailable"
    rest = value.get("bullpen_rest")
    if not isinstance(rest, dict):
        return "rest unavailable"
    recent_games = rest.get("recent_games", "?")
    days_since_last_game = rest.get("days_since_last_game", "?")
    last_game_innings = rest.get("last_game_innings", "?")
    return (
        f"{recent_games} recent game(s), {days_since_last_game} day(s) rest, "
        f"last game {last_game_innings} inning(s)"
    )


def _sports_consensus_group_key(market: Market) -> tuple[object, ...] | None:
    if not market.outcomes:
        return None
    outcome_keys = tuple(
        sorted(_sports_outcome_key(outcome) for outcome in market.outcomes)
    )
    return market.event_id, outcome_keys


def _sports_outcome_key(outcome: OutcomeQuote) -> tuple[object, ...]:
    raw_name = outcome.metadata.get("raw_name")
    name = str(raw_name or outcome.name)
    point = outcome.metadata.get("point")
    return _safe_token(name), _point_key(point)


def _point_key(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _safe_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _consensus_confidence(venue_count: int) -> float:
    if venue_count >= 5:
        return 0.65
    if venue_count >= 3:
        return 0.6
    return 0.55


def _sources(signals: list[ResearchSignal]) -> list[str]:
    sources = {"market_snapshot", "rules_summary"}
    sources.update(signal.source for signal in signals)
    return sorted(sources)


def _note_confidence(signals: list[ResearchSignal]) -> float:
    probability_signals = [
        signal.confidence
        for signal in signals
        if signal.kind == "probability_adjustment"
    ]
    if probability_signals:
        return round(max(probability_signals), 2)
    return 0.35


def _liquidity_signal_name(liquidity: float) -> str:
    if liquidity >= 100_000:
        return "high_activity_market"
    if liquidity >= 10_000:
        return "moderate_activity_market"
    return "thin_activity_market"
