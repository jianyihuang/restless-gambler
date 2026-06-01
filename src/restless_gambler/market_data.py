from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

from restless_gambler.domain import Market, OutcomeQuote, PriceFormat, ProductType


@dataclass(frozen=True)
class LoadedMarkets:
    markets: list[Market]
    path: Path
    source: str
    generated_at: str
    warnings: list[str] = field(default_factory=list)

    def data_source(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": self.source,
            "path": str(self.path),
            "generated_at": self.generated_at,
            "market_count": len(self.markets),
        }
        if self.warnings:
            payload["warnings"] = self.warnings
        return payload


def load_market_snapshots(
    *,
    path: Path,
    as_of: date,
    min_liquidity: float,
    max_markets: int,
) -> LoadedMarkets:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_markets = payload.get("markets", [])
    if not isinstance(raw_markets, list):
        msg = "market snapshot must contain a markets list"
        raise ValueError(msg)

    generated_at = str(payload.get("generated_at", ""))
    freshness_reference = _freshness_reference_time(
        generated_at=generated_at,
        as_of=as_of,
    )
    markets = [_parse_market(raw_market) for raw_market in raw_markets]
    open_markets: list[Market] = []
    stale_market_count = 0
    for market in markets:
        close_time = _parse_datetime(market.close_time)
        if market.status != "open" or market.liquidity < min_liquidity:
            continue
        if close_time.date() < as_of or close_time <= freshness_reference:
            stale_market_count += 1
            continue
        open_markets.append(market)

    open_markets.sort(key=lambda market: (-market.liquidity, market.close_time))
    warnings = []
    if stale_market_count:
        warnings.append(
            f"filtered {stale_market_count} stale market(s) at or past close_time"
        )

    return LoadedMarkets(
        markets=open_markets[:max_markets],
        path=path,
        source=str(payload.get("source", "unknown_market_snapshot")),
        generated_at=generated_at,
        warnings=warnings,
    )


def merge_market_snapshot_files(
    *,
    input_paths: list[Path],
    output_path: Path,
) -> Path:
    if not input_paths:
        msg = "at least one input snapshot is required"
        raise ValueError(msg)

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    sources: list[dict[str, object]] = []
    for path in input_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        source = str(payload.get("source", "unknown_market_snapshot"))
        sources.append(
            {
                "source": source,
                "path": str(path),
                "generated_at": str(payload.get("generated_at", "")),
                "market_count": len(payload.get("markets", [])),
            }
        )
        for market in payload.get("markets", []):
            if not isinstance(market, dict):
                continue
            key = (str(market.get("venue", "")), str(market.get("market_id", "")))
            merged[key] = market

    output_payload = {
        "source": "merged_market_snapshot",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "sources": sources,
        "markets": list(merged.values()),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_path


def _parse_market(payload: dict[str, Any]) -> Market:
    outcomes = [_parse_outcome(outcome) for outcome in payload["outcomes"]]
    return Market(
        market_id=str(payload["market_id"]),
        event_id=str(payload["event_id"]),
        venue=str(payload["venue"]),
        product_type=_product_type(payload["product_type"]),
        title=str(payload["title"]),
        category=str(payload["category"]),
        status=str(payload["status"]),
        close_time=str(payload["close_time"]),
        liquidity=float(payload["liquidity"]),
        volume=float(payload["volume"]),
        rules_summary=str(payload["rules_summary"]),
        outcomes=outcomes,
    )


def _parse_outcome(payload: dict[str, Any]) -> OutcomeQuote:
    price = float(payload["price"])
    price_format = _price_format(payload["price_format"])
    return OutcomeQuote(
        outcome_id=str(payload["outcome_id"]),
        name=str(payload["name"]),
        price=price,
        price_format=price_format,
        implied_probability=_implied_probability(price, price_format),
        bid=_optional_float(payload.get("bid")),
        ask=_optional_float(payload.get("ask")),
        metadata=_metadata(payload.get("metadata")),
    )


def _product_type(value: object) -> ProductType:
    if value not in {
        "prediction_contract",
        "sportsbook",
        "betting_exchange",
        "casino_research_only",
    }:
        msg = f"unsupported product type: {value}"
        raise ValueError(msg)
    return value


def _price_format(value: object) -> PriceFormat:
    if value not in {"probability", "american", "decimal"}:
        msg = f"unsupported price format: {value}"
        raise ValueError(msg)
    return value


def _implied_probability(price: float, price_format: PriceFormat) -> float:
    if price_format == "probability":
        if not 0.0 <= price <= 1.0:
            msg = f"invalid probability price: {price}"
            raise ValueError(msg)
        return price
    if price_format == "decimal":
        if price <= 1.0:
            msg = f"invalid decimal price: {price}"
            raise ValueError(msg)
        return 1.0 / price
    if price == 0:
        msg = "american price cannot be zero"
        raise ValueError(msg)
    if price > 0:
        return 100.0 / (price + 100.0)
    return abs(price) / (abs(price) + 100.0)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _metadata(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _parse_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _freshness_reference_time(*, generated_at: str, as_of: date) -> datetime:
    generated_time = _parse_optional_datetime(generated_at)
    if generated_time is not None:
        return generated_time
    return datetime.combine(as_of, time.min, tzinfo=UTC)


def _parse_optional_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return _parse_datetime(value)
    except ValueError:
        return None
