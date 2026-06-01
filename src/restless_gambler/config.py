from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from restless_gambler.domain import RuntimeMode
from restless_gambler.env import load_dotenv
from restless_gambler.paths import REPORTS_DIR, project_path

DEFAULT_MARKETS_PATH = project_path("examples", "markets", "kalshi_markets.json")
DEFAULT_ARTIFACTS_DIR = REPORTS_DIR / "runs"
DEFAULT_ALLOWED_VENUES = ("kalshi", "paper_sportsbook", "paper_betfair")
KALSHI_PROD_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
KALSHI_DEMO_BASE_URL = "https://external-api.demo.kalshi.co/trade-api/v2"


@dataclass(frozen=True)
class StrategyConfig:
    name: str = "baseline_cross_gambling_ev"
    version: str = "0.1.0"
    min_liquidity: float = 100.0
    min_expected_value: float = 0.03
    max_markets: int = 25


@dataclass(frozen=True)
class ResearchConfig:
    enabled: bool = True
    sources: tuple[str, ...] = ("market_rules", "market_microstructure")


@dataclass(frozen=True)
class ModelConfig:
    name: str = "baseline_implied_probability_adjustment"
    version: str = "0.1.0"


@dataclass(frozen=True)
class RiskConfig:
    allowed_venues: tuple[str, ...] = DEFAULT_ALLOWED_VENUES
    max_wager_cost: float = 250.0
    max_market_exposure: float = 500.0
    max_total_exposure: float = 1_000.0
    max_units_per_wager: int = 25
    max_daily_loss: float = 100.0
    allow_sells: bool = False
    allow_lays: bool = False
    min_expected_value: float = 0.03
    kill_switch_enabled: bool = False


@dataclass(frozen=True)
class ExecutionConfig:
    post_only: bool = True
    reduce_only: bool = False
    live_trading_enabled: bool = False
    live_order_placement_confirmed: bool = False
    kalshi_base_url: str = KALSHI_DEMO_BASE_URL


@dataclass(frozen=True)
class DataConfig:
    markets_path: Path = DEFAULT_MARKETS_PATH


@dataclass(frozen=True)
class ArtifactConfig:
    output_dir: Path = DEFAULT_ARTIFACTS_DIR


@dataclass(frozen=True)
class RestlessGamblerConfig:
    mode: RuntimeMode
    as_of: date
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    research: ResearchConfig = field(default_factory=ResearchConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    data: DataConfig = field(default_factory=DataConfig)
    artifacts: ArtifactConfig = field(default_factory=ArtifactConfig)

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["as_of"] = self.as_of.isoformat()
        payload["data"]["markets_path"] = str(self.data.markets_path)
        payload["artifacts"]["output_dir"] = str(self.artifacts.output_dir)
        return payload


def load_config(
    *,
    mode: RuntimeMode = "paper",
    as_of: date | None = None,
    markets_path: Path | None = None,
    artifacts_dir: Path = DEFAULT_ARTIFACTS_DIR,
    min_expected_value: float | None = None,
    min_liquidity: float | None = None,
    max_wager_cost: float | None = None,
    max_units_per_wager: int | None = None,
    allowed_venues: tuple[str, ...] | None = None,
    confirm_live: bool = False,
) -> RestlessGamblerConfig:
    load_dotenv()

    if mode not in {"research", "paper", "live"}:
        msg = f"unsupported runtime mode: {mode}"
        raise ValueError(msg)

    run_date = as_of or datetime.now(UTC).date()
    data_path = markets_path or _env_path("RG_MARKETS_PATH", DEFAULT_MARKETS_PATH)
    kill_switch_enabled = _env_flag("RG_KILL_SWITCH")
    live_trading_enabled = _env_flag("RG_LIVE_TRADING_ENABLED")
    kalshi_base_url = os.environ.get("KALSHI_BASE_URL", KALSHI_DEMO_BASE_URL)

    strategy_min_ev = (
        min_expected_value
        if min_expected_value is not None
        else StrategyConfig.min_expected_value
    )
    risk_min_ev = strategy_min_ev

    return RestlessGamblerConfig(
        mode=mode,
        as_of=run_date,
        strategy=StrategyConfig(
            min_expected_value=risk_min_ev,
            min_liquidity=min_liquidity
            if min_liquidity is not None
            else StrategyConfig.min_liquidity,
        ),
        risk=RiskConfig(
            allowed_venues=allowed_venues
            if allowed_venues is not None
            else RiskConfig.allowed_venues,
            min_expected_value=risk_min_ev,
            max_wager_cost=max_wager_cost
            if max_wager_cost is not None
            else RiskConfig.max_wager_cost,
            max_units_per_wager=max_units_per_wager
            if max_units_per_wager is not None
            else RiskConfig.max_units_per_wager,
            kill_switch_enabled=kill_switch_enabled,
        ),
        execution=ExecutionConfig(
            live_trading_enabled=live_trading_enabled,
            live_order_placement_confirmed=confirm_live,
            kalshi_base_url=kalshi_base_url,
        ),
        data=DataConfig(markets_path=data_path),
        artifacts=ArtifactConfig(output_dir=artifacts_dir),
    )


def _env_flag(name: str) -> bool:
    value = os.environ.get(name, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    path = Path(value).expanduser()
    return path if path.is_absolute() else project_path(value)
