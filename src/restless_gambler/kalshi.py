from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from restless_gambler.config import KALSHI_DEMO_BASE_URL, KALSHI_PROD_BASE_URL
from restless_gambler.domain import Market, OutcomeQuote
from restless_gambler.env import load_dotenv


@dataclass(frozen=True)
class KalshiCredentialCheck:
    ok: bool
    status_code: int | None
    base_url: str
    endpoint: str
    key_id_present: bool
    private_key_path_present: bool
    private_key_file_exists: bool
    message: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class KalshiMarketDataFetch:
    markets: list[Market]
    raw_market_count: int
    base_url: str
    generated_at: str
    warnings: list[str]

    def snapshot_payload(self) -> dict[str, object]:
        return {
            "source": "kalshi_api",
            "generated_at": self.generated_at,
            "base_url": self.base_url,
            "raw_market_count": self.raw_market_count,
            "warnings": self.warnings,
            "markets": [market_to_snapshot_dict(market) for market in self.markets],
        }


@dataclass(frozen=True)
class KalshiAccountSnapshot:
    ok: bool
    base_url: str
    balance: dict[str, Any]
    resting_orders: list[dict[str, Any]]
    market_positions: list[dict[str, Any]]
    message: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def check_kalshi_credentials(*, base_url: str | None = None) -> KalshiCredentialCheck:
    load_dotenv()
    resolved_base_url = (
        base_url or os.environ.get("KALSHI_BASE_URL", KALSHI_DEMO_BASE_URL)
    ).rstrip("/")
    key_id = os.environ.get("KALSHI_API_KEY_ID", "").strip()
    private_key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "").strip()
    endpoint = "/portfolio/balance"

    if not key_id or not private_key_path:
        return KalshiCredentialCheck(
            ok=False,
            status_code=None,
            base_url=resolved_base_url,
            endpoint=endpoint,
            key_id_present=bool(key_id),
            private_key_path_present=bool(private_key_path),
            private_key_file_exists=False,
            message="missing KALSHI_API_KEY_ID or KALSHI_PRIVATE_KEY_PATH",
        )

    key_path = Path(private_key_path).expanduser()
    if not key_path.exists():
        return KalshiCredentialCheck(
            ok=False,
            status_code=None,
            base_url=resolved_base_url,
            endpoint=endpoint,
            key_id_present=True,
            private_key_path_present=True,
            private_key_file_exists=False,
            message="private key file does not exist",
        )

    try:
        private_key = load_private_key(key_path)
    except ValueError as error:
        return KalshiCredentialCheck(
            ok=False,
            status_code=None,
            base_url=resolved_base_url,
            endpoint=endpoint,
            key_id_present=True,
            private_key_path_present=True,
            private_key_file_exists=True,
            message=str(error),
        )

    url = urljoin(f"{resolved_base_url}/", endpoint.removeprefix("/"))
    request_path = urlparse(url).path
    timestamp = str(int(time.time() * 1000))
    signature = sign_request(private_key, timestamp, "GET", request_path)
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "KALSHI-ACCESS-KEY": key_id,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
            return KalshiCredentialCheck(
                ok=200 <= response.status < 300,
                status_code=response.status,
                base_url=resolved_base_url,
                endpoint=endpoint,
                key_id_present=True,
                private_key_path_present=True,
                private_key_file_exists=True,
                message="authenticated request succeeded",
            )
    except urllib.error.HTTPError as error:
        return KalshiCredentialCheck(
            ok=False,
            status_code=error.code,
            base_url=resolved_base_url,
            endpoint=endpoint,
            key_id_present=True,
            private_key_path_present=True,
            private_key_file_exists=True,
            message=_safe_error_message(error),
        )
    except OSError as error:
        return KalshiCredentialCheck(
            ok=False,
            status_code=None,
            base_url=resolved_base_url,
            endpoint=endpoint,
            key_id_present=True,
            private_key_path_present=True,
            private_key_file_exists=True,
            message=f"request failed: {error}",
        )


def load_private_key(path: Path) -> rsa.RSAPrivateKey:
    try:
        key = serialization.load_pem_private_key(
            path.read_bytes(),
            password=None,
        )
    except (OSError, ValueError) as error:
        msg = "could not load RSA private key"
        raise ValueError(msg) from error

    if not isinstance(key, rsa.RSAPrivateKey):
        msg = "private key is not an RSA private key"
        raise ValueError(msg)
    return key


def sign_request(
    private_key: rsa.RSAPrivateKey,
    timestamp: str,
    method: str,
    path: str,
) -> str:
    message = f"{timestamp}{method.upper()}{path.split('?')[0]}".encode()
    signature = private_key.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("utf-8")


def authenticated_get_json(
    *,
    endpoint: str,
    base_url: str | None = None,
    query: dict[str, object] | None = None,
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    return _authenticated_request_json(
        method="GET",
        endpoint=endpoint,
        base_url=base_url,
        query=query,
        body=None,
        timeout_seconds=timeout_seconds,
    )


def authenticated_post_json(
    *,
    endpoint: str,
    payload: dict[str, object],
    base_url: str | None = None,
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    return _authenticated_request_json(
        method="POST",
        endpoint=endpoint,
        base_url=base_url,
        query=None,
        body=payload,
        timeout_seconds=timeout_seconds,
    )


def create_kalshi_order(
    *,
    payload: dict[str, object],
    base_url: str | None = None,
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    response = authenticated_post_json(
        endpoint="/portfolio/orders",
        payload=payload,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
    )
    order = response.get("order")
    if not isinstance(order, dict):
        msg = "Kalshi create order response did not contain an order object"
        raise ValueError(msg)
    return order


def fetch_kalshi_orders(
    *,
    base_url: str | None = None,
    status: str | None = None,
    ticker: str | None = None,
    limit: int = 100,
    timeout_seconds: int = 20,
) -> list[dict[str, Any]]:
    query: dict[str, object] = {"limit": limit}
    if status:
        query["status"] = status
    if ticker:
        query["ticker"] = ticker
    response = authenticated_get_json(
        endpoint="/portfolio/orders",
        base_url=base_url,
        query=query,
        timeout_seconds=timeout_seconds,
    )
    orders = response.get("orders")
    if not isinstance(orders, list):
        msg = "Kalshi get orders response did not contain an orders list"
        raise ValueError(msg)
    return [order for order in orders if isinstance(order, dict)]


def fetch_kalshi_positions(
    *,
    base_url: str | None = None,
    limit: int = 100,
    timeout_seconds: int = 20,
) -> list[dict[str, Any]]:
    response = authenticated_get_json(
        endpoint="/portfolio/positions",
        base_url=base_url,
        query={"limit": limit, "count_filter": "position,total_traded"},
        timeout_seconds=timeout_seconds,
    )
    positions = response.get("market_positions")
    if not isinstance(positions, list):
        msg = "Kalshi positions response did not contain market_positions"
        raise ValueError(msg)
    return [position for position in positions if isinstance(position, dict)]


def fetch_kalshi_balance(
    *,
    base_url: str | None = None,
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    return authenticated_get_json(
        endpoint="/portfolio/balance",
        base_url=base_url,
        timeout_seconds=timeout_seconds,
    )


def kalshi_account_snapshot(
    *,
    base_url: str | None = None,
    timeout_seconds: int = 20,
) -> KalshiAccountSnapshot:
    resolved_base_url = _authenticated_base_url(base_url)
    try:
        balance = fetch_kalshi_balance(
            base_url=resolved_base_url,
            timeout_seconds=timeout_seconds,
        )
        resting_orders = fetch_kalshi_orders(
            base_url=resolved_base_url,
            status="resting",
            timeout_seconds=timeout_seconds,
        )
        market_positions = fetch_kalshi_positions(
            base_url=resolved_base_url,
            timeout_seconds=timeout_seconds,
        )
    except (OSError, ValueError) as error:
        return KalshiAccountSnapshot(
            ok=False,
            base_url=resolved_base_url,
            balance={},
            resting_orders=[],
            market_positions=[],
            message=str(error),
        )

    return KalshiAccountSnapshot(
        ok=True,
        base_url=resolved_base_url,
        balance=balance,
        resting_orders=resting_orders,
        market_positions=market_positions,
        message="authenticated Kalshi account checks succeeded",
    )


def _authenticated_request_json(
    *,
    method: str,
    endpoint: str,
    base_url: str | None,
    query: dict[str, object] | None,
    body: dict[str, object] | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    load_dotenv()
    resolved_base_url = _authenticated_base_url(base_url)
    key_id = os.environ.get("KALSHI_API_KEY_ID", "").strip()
    private_key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "").strip()
    if not key_id or not private_key_path:
        msg = "missing KALSHI_API_KEY_ID or KALSHI_PRIVATE_KEY_PATH"
        raise ValueError(msg)

    private_key = load_private_key(Path(private_key_path).expanduser())
    url = urljoin(f"{resolved_base_url}/", endpoint.removeprefix("/"))
    if query:
        url = f"{url}?{urlencode(query)}"
    request_path = urlparse(url).path
    timestamp = str(int(time.time() * 1000))
    signature = sign_request(private_key, timestamp, method, request_path)

    payload_bytes = None
    headers = {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-SIGNATURE": signature,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
    }
    if body is not None:
        payload_bytes = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        url,
        data=payload_bytes,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        msg = _safe_error_message(error)
        raise ValueError(msg) from error

    if not isinstance(payload, dict):
        msg = "Kalshi authenticated response was not a JSON object"
        raise ValueError(msg)
    return payload


def _safe_error_message(error: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(error.read().decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return f"authenticated request failed with HTTP {error.code}"

    if isinstance(payload, dict):
        detail = payload.get("error") or payload.get("message") or payload.get("detail")
        if detail:
            return f"authenticated request failed with HTTP {error.code}: {detail}"
    return f"authenticated request failed with HTTP {error.code}"


def fetch_kalshi_market_data(
    *,
    base_url: str | None = None,
    max_markets: int = 100,
    include_orderbooks: bool = True,
    orderbook_depth: int = 1,
    timeout_seconds: int = 20,
) -> KalshiMarketDataFetch:
    if max_markets <= 0:
        msg = "max_markets must be positive"
        raise ValueError(msg)

    resolved_base_url = _market_data_base_url(base_url)
    raw_markets = _fetch_markets(
        base_url=resolved_base_url,
        max_markets=max_markets,
        timeout_seconds=timeout_seconds,
    )
    warnings: list[str] = []
    normalized_markets: list[Market] = []

    for raw_market in raw_markets:
        orderbook = None
        if include_orderbooks:
            try:
                orderbook = _fetch_orderbook(
                    base_url=resolved_base_url,
                    ticker=str(raw_market["ticker"]),
                    depth=orderbook_depth,
                    timeout_seconds=timeout_seconds,
                )
            except (KeyError, OSError, ValueError) as error:
                warnings.append(
                    f"orderbook unavailable for {raw_market.get('ticker')}: {error}"
                )
        try:
            normalized_markets.append(
                normalize_kalshi_market(raw_market, orderbook=orderbook)
            )
        except (KeyError, ValueError) as error:
            warnings.append(f"market skipped {raw_market.get('ticker')}: {error}")

    return KalshiMarketDataFetch(
        markets=normalized_markets,
        raw_market_count=len(raw_markets),
        base_url=resolved_base_url,
        generated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        warnings=warnings,
    )


def fetch_kalshi_market(
    *,
    ticker: str,
    base_url: str | None = None,
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    resolved_base_url = _market_data_base_url(base_url)
    payload = _public_get_json(
        base_url=resolved_base_url,
        endpoint=f"/markets/{ticker}",
        timeout_seconds=timeout_seconds,
    )
    raw_market = payload.get("market")
    if not isinstance(raw_market, dict):
        msg = "Kalshi /markets/{ticker} response did not contain a market object"
        raise ValueError(msg)
    return raw_market


def kalshi_settlement_outcome(
    raw_market: dict[str, Any],
    *,
    outcome_id: str,
    allow_determined: bool = False,
) -> str | None:
    status = str(raw_market.get("status") or "").strip().lower()
    terminal_statuses = {"finalized", "settled"}
    if allow_determined:
        terminal_statuses |= {"determined", "amended"}

    result = _normalize_kalshi_result(
        _first_nonblank(
            raw_market.get("result"),
            raw_market.get("settlement_value"),
            raw_market.get("expiration_value"),
        )
    )
    if result is None:
        return None
    if result == "push":
        return "push"
    if status not in terminal_statuses:
        return None

    normalized_outcome = _normalize_binary_outcome_id(outcome_id, raw_market)
    if normalized_outcome is None:
        return None
    return "won" if normalized_outcome == result else "lost"


def normalize_kalshi_market(
    raw_market: dict[str, Any],
    *,
    orderbook: dict[str, Any] | None = None,
) -> Market:
    market_id = str(raw_market["ticker"])
    event_id = str(raw_market.get("event_ticker") or "")
    yes_bid = _optional_probability(raw_market.get("yes_bid_dollars"))
    yes_ask = _optional_probability(raw_market.get("yes_ask_dollars"))
    no_bid = _optional_probability(raw_market.get("no_bid_dollars"))
    no_ask = _optional_probability(raw_market.get("no_ask_dollars"))

    if orderbook:
        best_yes_bid = _best_bid_from_orderbook(orderbook, "yes_dollars")
        best_no_bid = _best_bid_from_orderbook(orderbook, "no_dollars")
        yes_bid = best_yes_bid if best_yes_bid is not None else yes_bid
        no_bid = best_no_bid if best_no_bid is not None else no_bid

    if yes_ask is None and no_bid is not None:
        yes_ask = round(1.0 - no_bid, 4)
    if no_ask is None and yes_bid is not None:
        no_ask = round(1.0 - yes_bid, 4)

    yes_price = _first_price(yes_ask, yes_bid, raw_market.get("last_price_dollars"))
    no_price = _first_price(no_ask, no_bid, None)

    liquidity = _optional_float(raw_market.get("liquidity_dollars")) or 0.0
    volume = _optional_float(raw_market.get("volume_fp")) or 0.0
    activity = max(
        liquidity,
        _optional_float(raw_market.get("volume_24h_fp")) or 0.0,
        volume,
        _optional_float(raw_market.get("open_interest_fp")) or 0.0,
    )

    return Market(
        market_id=market_id,
        event_id=event_id,
        venue="kalshi",
        product_type="prediction_contract",
        title=str(raw_market.get("title") or market_id),
        category=_infer_category(event_id, market_id),
        status=_normalize_status(raw_market.get("status")),
        close_time=_first_time(
            raw_market.get("close_time"),
            raw_market.get("expiration_time"),
            raw_market.get("latest_expiration_time"),
        ),
        liquidity=activity,
        volume=volume,
        rules_summary=_rules_summary(raw_market),
        outcomes=[
            OutcomeQuote(
                outcome_id="yes",
                name=str(raw_market.get("yes_sub_title") or "Yes"),
                price=yes_price,
                price_format="probability",
                implied_probability=yes_price,
                bid=yes_bid,
                ask=yes_ask,
                metadata={
                    "kalshi_ticker": market_id,
                    "ask_size": raw_market.get("yes_ask_size_fp"),
                    "bid_size": raw_market.get("yes_bid_size_fp"),
                },
            ),
            OutcomeQuote(
                outcome_id="no",
                name=str(raw_market.get("no_sub_title") or "No"),
                price=no_price,
                price_format="probability",
                implied_probability=no_price,
                bid=no_bid,
                ask=no_ask,
                metadata={
                    "kalshi_ticker": market_id,
                },
            ),
        ],
    )


def market_to_snapshot_dict(market: Market) -> dict[str, object]:
    return {
        "market_id": market.market_id,
        "event_id": market.event_id,
        "venue": market.venue,
        "product_type": market.product_type,
        "title": market.title,
        "category": market.category,
        "status": market.status,
        "close_time": market.close_time,
        "liquidity": market.liquidity,
        "volume": market.volume,
        "rules_summary": market.rules_summary,
        "outcomes": [
            {
                "outcome_id": outcome.outcome_id,
                "name": outcome.name,
                "price": outcome.price,
                "price_format": outcome.price_format,
                "bid": outcome.bid,
                "ask": outcome.ask,
                "metadata": outcome.metadata,
            }
            for outcome in market.outcomes
        ],
    }


def write_kalshi_market_snapshot(
    *,
    output_path: Path,
    fetch: KalshiMarketDataFetch,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(fetch.snapshot_payload(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_path


def _fetch_markets(
    *,
    base_url: str,
    max_markets: int,
    timeout_seconds: int,
) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    cursor = ""
    while len(markets) < max_markets:
        page_limit = min(1000, max_markets - len(markets))
        query: dict[str, object] = {
            "status": "open",
            "limit": page_limit,
            "mve_filter": "exclude",
        }
        if cursor:
            query["cursor"] = cursor
        payload = _public_get_json(
            base_url=base_url,
            endpoint="/markets",
            query=query,
            timeout_seconds=timeout_seconds,
        )
        page_markets = payload.get("markets", [])
        if not isinstance(page_markets, list):
            msg = "Kalshi /markets response did not contain a markets list"
            raise ValueError(msg)
        markets.extend(page_markets)
        cursor = str(payload.get("cursor") or "")
        if not cursor or not page_markets:
            break
    return markets[:max_markets]


def _fetch_orderbook(
    *,
    base_url: str,
    ticker: str,
    depth: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    payload = _public_get_json(
        base_url=base_url,
        endpoint=f"/markets/{ticker}/orderbook",
        query={"depth": depth},
        timeout_seconds=timeout_seconds,
    )
    orderbook = payload.get("orderbook_fp") or payload.get("orderbook")
    if not isinstance(orderbook, dict):
        msg = "Kalshi orderbook response did not contain orderbook_fp"
        raise ValueError(msg)
    return orderbook


def _public_get_json(
    *,
    base_url: str,
    endpoint: str,
    query: dict[str, object] | None = None,
    timeout_seconds: int,
) -> dict[str, Any]:
    url = urljoin(f"{base_url.rstrip('/')}/", endpoint.removeprefix("/"))
    if query:
        url = f"{url}?{urlencode(query)}"
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        msg = "Kalshi response was not a JSON object"
        raise ValueError(msg)
    return payload


def _market_data_base_url(base_url: str | None) -> str:
    if base_url:
        return base_url.rstrip("/")
    env_base_url = os.environ.get("KALSHI_MARKET_DATA_BASE_URL", "").strip()
    if env_base_url:
        return env_base_url.rstrip("/")
    return KALSHI_PROD_BASE_URL


def _authenticated_base_url(base_url: str | None) -> str:
    if base_url:
        return base_url.rstrip("/")
    return os.environ.get("KALSHI_BASE_URL", KALSHI_DEMO_BASE_URL).rstrip("/")


def _best_bid_from_orderbook(orderbook: dict[str, Any], key: str) -> float | None:
    levels = orderbook.get(key) or []
    if not isinstance(levels, list) or not levels:
        return None
    prices = [_optional_float(level[0]) for level in levels if isinstance(level, list)]
    numeric_prices = [price for price in prices if price is not None]
    if not numeric_prices:
        return None
    return max(numeric_prices)


def _optional_probability(value: object) -> float | None:
    price = _optional_float(value)
    if price is None:
        return None
    if not 0.0 <= price <= 1.0:
        msg = f"invalid Kalshi probability price: {price}"
        raise ValueError(msg)
    return price


def _optional_float(value: object) -> float | None:
    if value in {None, ""}:
        return None
    return float(value)


def _first_price(*values: object) -> float:
    for value in values:
        price = _optional_probability(value)
        if price is not None:
            return price
    return 0.5


def _first_time(*values: object) -> str:
    for value in values:
        if value:
            return str(value)
    return "9999-12-31T23:59:59Z"


def _rules_summary(raw_market: dict[str, Any]) -> str:
    primary = str(raw_market.get("rules_primary") or "").strip()
    secondary = str(raw_market.get("rules_secondary") or "").strip()
    if primary and secondary:
        return f"{primary}\n\n{secondary}"
    return primary or secondary or "No rules summary supplied by source."


def _infer_category(event_id: str, market_id: str) -> str:
    token = (event_id or market_id).split("-")[0].lower()
    if token.startswith("kx"):
        token = token[2:]
    return token or "kalshi"


def _normalize_status(value: object) -> str:
    status = str(value or "").lower()
    if status == "active":
        return "open"
    return status


def _first_nonblank(*values: object) -> object | None:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _normalize_kalshi_result(value: object) -> str | None:
    if value is None or value == "":
        return None
    text = str(value).strip().lower()
    if text in {"yes", "y", "true", "1"}:
        return "yes"
    if text in {"no", "n", "false", "0"}:
        return "no"
    if text in {"cancelled", "canceled", "void", "push"}:
        return "push"
    return None


def _normalize_binary_outcome_id(
    outcome_id: str,
    raw_market: dict[str, Any],
) -> str | None:
    text = outcome_id.strip().lower()
    yes_aliases = {"yes", "y", str(raw_market.get("yes_sub_title") or "").lower()}
    no_aliases = {"no", "n", str(raw_market.get("no_sub_title") or "").lower()}
    yes_aliases.discard("")
    no_aliases.discard("")
    if text in yes_aliases:
        return "yes"
    if text in no_aliases:
        return "no"
    return None
