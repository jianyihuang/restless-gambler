from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterable
from datetime import date
from pathlib import Path

from restless_gambler.config import RestlessGamblerConfig
from restless_gambler.domain import (
    BetRecord,
    Market,
    PortfolioSnapshot,
    Position,
    RunArtifact,
    WagerIntent,
)
from restless_gambler.execution import KalshiLiveExecutionAdapter, PaperExecutionAdapter
from restless_gambler.forecast import (
    build_opportunity_diagnostics,
    find_opportunities,
    generate_forecasts,
)
from restless_gambler.market_data import load_market_snapshots
from restless_gambler.research import build_research_notes
from restless_gambler.risk import evaluate_risk

STARTING_CASH = 10_000.0


class RestlessGamblerRunner:
    def __init__(
        self,
        config: RestlessGamblerConfig,
        *,
        blocked_wagers: Iterable[tuple[str, str, str]] | None = None,
        existing_total_exposure: float = 0.0,
        existing_market_exposure: dict[str, float] | None = None,
    ) -> None:
        self.config = config
        self.blocked_wagers = set(blocked_wagers or [])
        self.existing_total_exposure = existing_total_exposure
        self.existing_market_exposure = dict(existing_market_exposure or {})

    def run(self) -> Path:
        artifact = self._run()
        return write_artifact(artifact, self.config.artifacts.output_dir)

    def _run(self) -> RunArtifact:
        timestamp = f"{self.config.as_of.isoformat()}T00:00:00Z"
        run_id = build_run_id(
            self.config.mode,
            self.config.strategy.name,
            self.config.as_of,
        )
        loaded_markets = load_market_snapshots(
            path=self.config.data.markets_path,
            as_of=self.config.as_of,
            min_liquidity=self.config.strategy.min_liquidity,
            max_markets=self.config.strategy.max_markets,
        )
        markets = loaded_markets.markets
        research_notes = build_research_notes(markets)
        forecasts = generate_forecasts(
            markets=markets,
            research_notes=research_notes,
            model=self.config.model,
        )
        opportunity_diagnostics = build_opportunity_diagnostics(
            markets=markets,
            forecasts=forecasts,
            min_expected_value=self.config.strategy.min_expected_value,
        )
        opportunities = find_opportunities(
            markets=markets,
            forecasts=forecasts,
            min_expected_value=self.config.strategy.min_expected_value,
            max_units=self.config.risk.max_units_per_wager,
        )
        if self.config.mode == "research":
            executable_opportunities = opportunities
            blocked_open_ledger_count = 0
        else:
            executable_opportunities = [
                opportunity
                for opportunity in opportunities
                if _wager_key(
                    opportunity.venue,
                    opportunity.market_id,
                    opportunity.outcome_id,
                )
                not in self.blocked_wagers
            ]
            blocked_open_ledger_count = len(opportunities) - len(
                executable_opportunities
            )
        wager_intents = (
            []
            if self.config.mode == "research"
            else build_wager_intents(
                run_id=run_id,
                opportunities=executable_opportunities,
                post_only=self.config.execution.post_only,
                reduce_only=self.config.execution.reduce_only,
            )
        )
        risk_decisions = evaluate_risk(
            intents=wager_intents,
            markets=markets,
            risk=self.config.risk,
            existing_total_exposure=self.existing_total_exposure,
            existing_market_exposure=self.existing_market_exposure,
        )
        executions, bets = self._execute(
            intents=wager_intents,
            risk_decisions=risk_decisions,
            timestamp=timestamp,
        )
        portfolio = apply_bets(
            bets=bets,
            starting_cash=STARTING_CASH,
            markets=markets,
        )

        warnings = []
        if not markets:
            warnings.append("no open markets passed filters")
        if markets and not opportunities:
            warnings.append("no opportunities passed expected-value threshold")
        if blocked_open_ledger_count:
            warnings.append(
                f"blocked {blocked_open_ledger_count} opportunity/opportunities "
                "already open in paper ledger"
            )
        if self.config.mode == "paper" and not bets:
            warnings.append("paper run produced no bets")
        if self.config.mode == "live":
            warnings.append("live mode submitted only Kalshi-supported intents")

        return RunArtifact(
            run_id=run_id,
            timestamp=timestamp,
            git_commit=current_git_commit(),
            runtime_mode=self.config.mode,
            status="completed",
            config=self.config.to_dict(),
            data_source=loaded_markets.data_source(),
            markets=markets,
            research_notes=research_notes,
            forecasts=forecasts,
            opportunity_diagnostics=opportunity_diagnostics,
            opportunities=opportunities,
            wager_intents=wager_intents,
            risk_decisions=risk_decisions,
            executions=executions,
            bets=bets,
            positions=portfolio.positions,
            cash=portfolio.cash,
            equity=portfolio.equity,
            realized_pnl=round(portfolio.equity - STARTING_CASH, 2),
            warnings=warnings,
            errors=[],
        )

    def _execute(
        self,
        *,
        intents: list[WagerIntent],
        risk_decisions,
        timestamp: str,
    ):
        if self.config.mode == "research":
            return [], []
        if self.config.mode == "paper":
            return PaperExecutionAdapter().submit(
                intents=intents,
                risk_decisions=risk_decisions,
                timestamp=timestamp,
            )
        if not self.config.execution.live_trading_enabled:
            msg = "live mode requires RG_LIVE_TRADING_ENABLED=true"
            raise ValueError(msg)
        if not self.config.execution.live_order_placement_confirmed:
            msg = "live mode requires --confirm-live for order placement"
            raise ValueError(msg)
        return KalshiLiveExecutionAdapter(
            base_url=self.config.execution.kalshi_base_url,
        ).submit(
            intents=intents,
            risk_decisions=risk_decisions,
            timestamp=timestamp,
        )


def build_wager_intents(
    *,
    run_id: str,
    opportunities: Iterable,
    post_only: bool,
    reduce_only: bool,
) -> list[WagerIntent]:
    intents: list[WagerIntent] = []
    for opportunity in opportunities:
        units = opportunity.max_units
        estimated_fee = round(opportunity.fee_per_unit * units, 4)
        estimated_cost = round((opportunity.unit_cost * units) + estimated_fee, 4)
        client_order_id = "-".join(
            [
                _safe_id(run_id),
                _safe_id(opportunity.market_id),
                _safe_id(opportunity.outcome_id),
                opportunity.action,
            ]
        )
        intents.append(
            WagerIntent(
                client_order_id=client_order_id,
                venue=opportunity.venue,
                product_type=opportunity.product_type,
                market_id=opportunity.market_id,
                outcome_id=opportunity.outcome_id,
                outcome_name=opportunity.outcome_name,
                action=opportunity.action,
                units=units,
                limit_price=opportunity.entry_price,
                price_format=opportunity.entry_price_format,
                estimated_fee=estimated_fee,
                estimated_cost=estimated_cost,
                expected_value=opportunity.expected_value,
                post_only=post_only,
                reduce_only=reduce_only,
                reason=opportunity.reason,
            )
        )
    return intents


def apply_bets(
    *,
    bets: Iterable[BetRecord],
    starting_cash: float,
    markets: Iterable[Market],
) -> PortfolioSnapshot:
    market_lookup = {market.market_id: market for market in markets}
    cash = starting_cash
    positions: list[Position] = []

    for bet in bets:
        cash -= bet.cost
        market = market_lookup[bet.market_id]
        outcome = next(
            outcome
            for outcome in market.outcomes
            if outcome.outcome_id == bet.outcome_id
        )
        mark_price = outcome.bid if outcome.bid is not None else outcome.price
        market_value = (
            bet.units * mark_price
            if market.product_type == "prediction_contract"
            else 0.0
        )
        positions.append(
            Position(
                market_id=bet.market_id,
                outcome_id=bet.outcome_id,
                outcome_name=bet.outcome_name,
                product_type=bet.product_type,
                units=bet.units,
                average_price=bet.price,
                price_format=bet.price_format,
                mark_price=mark_price,
                market_value=round(market_value, 2),
            )
        )

    equity = cash + sum(position.market_value for position in positions)
    return PortfolioSnapshot(
        cash=round(cash, 2),
        equity=round(equity, 2),
        positions=positions,
    )


def write_artifact(artifact: RunArtifact, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = output_dir / f"{artifact.run_id}.json"
    artifact_path.write_text(
        json.dumps(artifact.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return artifact_path


def build_run_id(mode: str, strategy_name: str, as_of: date) -> str:
    return f"{mode}-{strategy_name}-{as_of.strftime('%Y%m%d')}"


def _wager_key(venue: str, market_id: str, outcome_id: str) -> tuple[str, str, str]:
    return venue, market_id, outcome_id


def current_git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip()


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")
