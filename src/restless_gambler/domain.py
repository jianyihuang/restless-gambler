from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

RuntimeMode = Literal["research", "paper", "live"]
ProductType = Literal[
    "prediction_contract",
    "sportsbook",
    "betting_exchange",
    "casino_research_only",
]
PriceFormat = Literal["probability", "american", "decimal"]
SignalKind = Literal["probability_adjustment", "market_quality", "market_context"]
WagerAction = Literal["buy", "sell", "back", "lay", "bet"]
ExecutionStatus = Literal["new", "blocked", "rejected", "cancelled", "filled"]
RiskStatus = Literal["approved", "rejected"]


@dataclass(frozen=True)
class OutcomeQuote:
    outcome_id: str
    name: str
    price: float
    price_format: PriceFormat
    implied_probability: float
    bid: float | None = None
    ask: float | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Market:
    market_id: str
    event_id: str
    venue: str
    product_type: ProductType
    title: str
    category: str
    status: str
    close_time: str
    liquidity: float
    volume: float
    rules_summary: str
    outcomes: list[OutcomeQuote]


@dataclass(frozen=True)
class ResearchSignal:
    market_id: str
    outcome_id: str | None
    kind: SignalKind
    name: str
    direction: float
    magnitude: float
    confidence: float
    source: str
    rationale: str


@dataclass(frozen=True)
class ResearchNote:
    market_id: str
    summary: str
    sources: list[str]
    confidence: float
    signals: list[ResearchSignal] = field(default_factory=list)


@dataclass(frozen=True)
class Forecast:
    market_id: str
    outcome_id: str
    fair_probability: float
    confidence: float
    model_name: str
    rationale: str


@dataclass(frozen=True)
class Opportunity:
    market_id: str
    outcome_id: str
    outcome_name: str
    venue: str
    product_type: ProductType
    action: WagerAction
    fair_probability: float
    entry_price: float
    entry_price_format: PriceFormat
    implied_probability: float
    unit_cost: float
    fee_per_unit: float
    expected_value: float
    edge_before_fees: float
    max_units: int
    reason: str


@dataclass(frozen=True)
class OpportunityDiagnostic:
    market_id: str
    outcome_id: str
    outcome_name: str
    venue: str
    product_type: ProductType
    fair_probability: float
    implied_probability: float
    entry_price: float
    expected_value: float
    min_expected_value: float
    decision: str


@dataclass(frozen=True)
class WagerIntent:
    client_order_id: str
    venue: str
    product_type: ProductType
    market_id: str
    outcome_id: str
    outcome_name: str
    action: WagerAction
    units: int
    limit_price: float
    price_format: PriceFormat
    estimated_fee: float
    estimated_cost: float
    expected_value: float
    post_only: bool
    reduce_only: bool
    reason: str


@dataclass(frozen=True)
class RiskDecision:
    client_order_id: str
    status: RiskStatus
    reason: str
    checks: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ExecutionRecord:
    client_order_id: str
    venue: str
    product_type: ProductType
    market_id: str
    outcome_id: str
    outcome_name: str
    action: WagerAction
    units: int
    limit_price: float
    price_format: PriceFormat
    status: ExecutionStatus
    submitted_at: str
    filled_units: int = 0
    average_fill_price: float | None = None
    rejection_reason: str | None = None
    external_order_id: str | None = None
    venue_order_status: str | None = None
    venue_order_json: dict[str, object] | None = None


@dataclass(frozen=True)
class BetRecord:
    client_order_id: str
    venue: str
    product_type: ProductType
    market_id: str
    outcome_id: str
    outcome_name: str
    action: WagerAction
    units: int
    price: float
    price_format: PriceFormat
    fee: float
    cost: float
    filled_at: str


@dataclass(frozen=True)
class Position:
    market_id: str
    outcome_id: str
    outcome_name: str
    product_type: ProductType
    units: int
    average_price: float
    price_format: PriceFormat
    mark_price: float
    market_value: float


@dataclass(frozen=True)
class PortfolioSnapshot:
    cash: float
    equity: float
    positions: list[Position] = field(default_factory=list)


@dataclass(frozen=True)
class RunArtifact:
    run_id: str
    timestamp: str
    git_commit: str
    runtime_mode: RuntimeMode
    status: str
    config: dict[str, object]
    data_source: dict[str, object]
    markets: list[Market]
    research_notes: list[ResearchNote]
    forecasts: list[Forecast]
    opportunity_diagnostics: list[OpportunityDiagnostic]
    opportunities: list[Opportunity]
    wager_intents: list[WagerIntent]
    risk_decisions: list[RiskDecision]
    executions: list[ExecutionRecord]
    bets: list[BetRecord]
    positions: list[Position]
    cash: float
    equity: float
    realized_pnl: float
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
