from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from restless_gambler.paths import DATA_DIR

DEFAULT_DB_PATH = DATA_DIR / "restless_gambler.duckdb"
RUN_SCOPED_TABLES = (
    "runs",
    "markets",
    "outcome_quotes",
    "research_notes",
    "research_signals",
    "forecasts",
    "opportunity_diagnostics",
    "opportunities",
    "wager_intents",
    "risk_decisions",
    "executions",
    "bets",
    "positions",
)


@dataclass(frozen=True)
class ImportSummary:
    db_path: str
    artifact_path: str
    run_id: str
    imported_at: str
    counts: dict[str, int]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SettlementResult:
    client_order_id: str
    settlement_status: str
    payout: float
    realized_pnl: float
    settled_at: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def init_database(db_path: Path = DEFAULT_DB_PATH) -> Path:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(db_path)) as con:
        _create_schema(con)
    return db_path


def import_run_artifact(
    *,
    artifact_path: Path,
    db_path: Path = DEFAULT_DB_PATH,
) -> ImportSummary:
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    run_id = str(payload["run_id"])
    imported_at = _now()

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(db_path)) as con:
        _create_schema(con)
        _delete_run(con, run_id)
        _insert_run(con, payload, artifact_path, imported_at)
        _insert_markets(con, run_id, payload)
        _insert_research_notes(con, run_id, payload)
        _insert_research_signals(con, run_id, payload)
        _insert_forecasts(con, run_id, payload)
        _insert_opportunity_diagnostics(con, run_id, payload)
        _insert_opportunities(con, run_id, payload)
        _insert_wager_intents(con, run_id, payload)
        _insert_risk_decisions(con, run_id, payload)
        _insert_executions(con, run_id, payload)
        _insert_bets(con, run_id, payload)
        _insert_positions(con, run_id, payload)
        _insert_ledger_bets(con, run_id, payload)

    return ImportSummary(
        db_path=str(db_path),
        artifact_path=str(artifact_path),
        run_id=run_id,
        imported_at=imported_at,
        counts={
            "markets": len(payload.get("markets", [])),
            "forecasts": len(payload.get("forecasts", [])),
            "opportunity_diagnostics": len(
                payload.get("opportunity_diagnostics", [])
            ),
            "opportunities": len(payload.get("opportunities", [])),
            "wager_intents": len(payload.get("wager_intents", [])),
            "executions": len(payload.get("executions", [])),
            "bets": len(payload.get("bets", [])),
            "positions": len(payload.get("positions", [])),
        },
    )


def summarize_database(db_path: Path = DEFAULT_DB_PATH) -> dict[str, object]:
    _ensure_database_exists(db_path)
    with duckdb.connect(str(db_path), read_only=True) as con:
        table_counts = {
            table: _count(con, table)
            for table in (
                "runs",
                "markets",
                "research_signals",
                "forecasts",
                "opportunity_diagnostics",
                "opportunities",
                "wager_intents",
                "executions",
                "bets",
                "paper_bet_ledger",
            )
        }
        latest_runs = [
            {
                "run_id": row[0],
                "timestamp": row[1],
                "runtime_mode": row[2],
                "markets": row[3],
                "bets": row[4],
                "realized_pnl": row[5],
            }
            for row in con.execute(
                """
                SELECT run_id, timestamp, runtime_mode, market_count, bet_count,
                       realized_pnl
                FROM runs
                ORDER BY timestamp DESC, imported_at DESC
                LIMIT 5
                """
            ).fetchall()
        ]
    return {
        "db_path": str(db_path),
        "table_counts": table_counts,
        "latest_runs": latest_runs,
    }


def ledger_status(db_path: Path = DEFAULT_DB_PATH) -> dict[str, object]:
    _ensure_database_exists(db_path)
    with duckdb.connect(str(db_path), read_only=True) as con:
        rows = con.execute(
            """
            SELECT settlement_status,
                   COUNT(*) AS bet_count,
                   COALESCE(SUM(cost), 0) AS total_cost,
                   COALESCE(SUM(expected_value * units), 0) AS expected_value_units,
                   COALESCE(SUM(realized_pnl), 0) AS realized_pnl
            FROM paper_bet_ledger
            GROUP BY settlement_status
            ORDER BY settlement_status
            """
        ).fetchall()
        open_bets = [
            {
                "client_order_id": row[0],
                "venue": row[1],
                "product_type": row[2],
                "market_id": row[3],
                "outcome_id": row[4],
                "units": row[5],
                "cost": row[6],
                "expected_value": row[7],
            }
            for row in con.execute(
                """
                SELECT client_order_id, venue, product_type, market_id, outcome_id,
                       units, cost, expected_value
                FROM paper_bet_ledger
                WHERE settlement_status = 'open'
                ORDER BY first_run_id, client_order_id
                LIMIT 20
                """
            ).fetchall()
        ]
    return {
        "db_path": str(db_path),
        "summary": [
            {
                "settlement_status": row[0],
                "bet_count": row[1],
                "total_cost": round(row[2], 2),
                "expected_value_units": round(row[3], 4),
                "realized_pnl": round(row[4], 2),
            }
            for row in rows
        ],
        "open_bets": open_bets,
    }


def open_paper_bets(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    venue: str | None = None,
    product_type: str | None = None,
    market_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, object]]:
    if limit <= 0:
        msg = "limit must be positive"
        raise ValueError(msg)

    _ensure_database_exists(db_path)
    where = ["settlement_status = 'open'"]
    params: list[object] = []
    if venue:
        where.append("venue = ?")
        params.append(venue)
    if product_type:
        where.append("product_type = ?")
        params.append(product_type)
    if market_id:
        where.append("market_id = ?")
        params.append(market_id)
    params.append(limit)

    with duckdb.connect(str(db_path), read_only=True) as con:
        rows = con.execute(
            f"""
            SELECT l.client_order_id, l.first_run_id, l.latest_run_id, l.venue,
                   l.product_type, l.market_id, l.outcome_id, l.outcome_name,
                   l.action, l.units, l.price, l.price_format, l.fee, l.cost,
                   l.expected_value, l.filled_at, m.event_id, m.category,
                   oq.metadata_json
            FROM paper_bet_ledger l
            LEFT JOIN markets m
              ON m.run_id = l.latest_run_id
             AND m.venue = l.venue
             AND m.market_id = l.market_id
            LEFT JOIN outcome_quotes oq
              ON oq.run_id = l.latest_run_id
             AND oq.market_id = l.market_id
             AND oq.outcome_id = l.outcome_id
            WHERE {" AND ".join(f"l.{clause}" for clause in where)}
            ORDER BY l.filled_at, l.client_order_id
            LIMIT ?
            """,
            params,
        ).fetchall()

    return [
        {
            "client_order_id": row[0],
            "first_run_id": row[1],
            "latest_run_id": row[2],
            "venue": row[3],
            "product_type": row[4],
            "market_id": row[5],
            "outcome_id": row[6],
            "outcome_name": row[7],
            "action": row[8],
            "units": row[9],
            "price": row[10],
            "price_format": row[11],
            "fee": row[12],
            "cost": row[13],
            "expected_value": row[14],
            "filled_at": row[15],
            "event_id": row[16],
            "category": row[17],
            "outcome_metadata": _loads_json_object(row[18]),
        }
        for row in rows
    ]


def open_ledger_wager_keys(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    exclude_run_id: str | None = None,
) -> set[tuple[str, str, str]]:
    rows = open_paper_bets(db_path=db_path, limit=1_000_000)
    return {
        (str(row["venue"]), str(row["market_id"]), str(row["outcome_id"]))
        for row in rows
        if exclude_run_id is None
        or (
            row["first_run_id"] != exclude_run_id
            and row["latest_run_id"] != exclude_run_id
        )
    }


def open_ledger_exposure(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    exclude_run_id: str | None = None,
) -> dict[str, object]:
    rows = open_paper_bets(db_path=db_path, limit=1_000_000)
    filtered_rows = [
        row
        for row in rows
        if exclude_run_id is None
        or (
            row["first_run_id"] != exclude_run_id
            and row["latest_run_id"] != exclude_run_id
        )
    ]
    by_market: dict[str, float] = {}
    total_cost = 0.0
    for row in filtered_rows:
        market_id = str(row["market_id"])
        cost = float(row["cost"] or 0.0)
        total_cost += cost
        by_market[market_id] = by_market.get(market_id, 0.0) + cost

    return {
        "total_cost": round(total_cost, 2),
        "by_market": {
            market_id: round(cost, 2) for market_id, cost in sorted(by_market.items())
        },
    }


def evaluation_summary(db_path: Path = DEFAULT_DB_PATH) -> dict[str, object]:
    _ensure_database_exists(db_path)
    with duckdb.connect(str(db_path), read_only=True) as con:
        diagnostic_rows = con.execute(
            """
            SELECT venue, product_type,
                   COUNT(*) AS diagnostic_count,
                   SUM(CASE WHEN decision = 'candidate' THEN 1 ELSE 0 END)
                     AS candidate_count,
                   AVG(expected_value) AS average_expected_value,
                   MAX(expected_value) AS max_expected_value
            FROM opportunity_diagnostics
            GROUP BY venue, product_type
            ORDER BY candidate_count DESC, average_expected_value DESC
            """
        ).fetchall()
        bet_rows = con.execute(
            """
            SELECT venue, product_type,
                   COUNT(*) AS bet_count,
                   SUM(CASE WHEN settlement_status = 'open' THEN 1 ELSE 0 END)
                     AS open_count,
                   SUM(CASE WHEN settlement_status IN ('won', 'lost', 'push')
                       THEN 1 ELSE 0 END) AS settled_count,
                   SUM(CASE WHEN settlement_status = 'won' THEN 1 ELSE 0 END)
                     AS won_count,
                   SUM(CASE WHEN settlement_status = 'lost' THEN 1 ELSE 0 END)
                     AS lost_count,
                   SUM(CASE WHEN settlement_status = 'push' THEN 1 ELSE 0 END)
                     AS push_count,
                   COALESCE(SUM(cost), 0) AS total_cost,
                   COALESCE(SUM(expected_value * units), 0) AS expected_value_units,
                   COALESCE(SUM(realized_pnl), 0) AS realized_pnl
            FROM paper_bet_ledger
            GROUP BY venue, product_type
            ORDER BY bet_count DESC, total_cost DESC
            """
        ).fetchall()

    return {
        "db_path": str(db_path),
        "diagnostics_by_venue": [
            {
                "venue": row[0],
                "product_type": row[1],
                "diagnostic_count": row[2],
                "candidate_count": row[3],
                "average_expected_value": round(row[4] or 0.0, 4),
                "max_expected_value": round(row[5] or 0.0, 4),
            }
            for row in diagnostic_rows
        ],
        "paper_bets_by_venue": [
            {
                "venue": row[0],
                "product_type": row[1],
                "bet_count": row[2],
                "open_count": row[3],
                "settled_count": row[4],
                "won_count": row[5],
                "lost_count": row[6],
                "push_count": row[7],
                "hit_rate": _hit_rate(won_count=row[5], lost_count=row[6]),
                "total_cost": round(row[8], 2),
                "expected_value_units": round(row[9], 4),
                "realized_pnl": round(row[10], 2),
            }
            for row in bet_rows
        ],
    }


def calibration_summary(db_path: Path = DEFAULT_DB_PATH) -> dict[str, object]:
    _ensure_database_exists(db_path)
    with duckdb.connect(str(db_path), read_only=True) as con:
        overall = con.execute(
            """
            WITH settled AS (
                SELECT l.settlement_status, l.realized_pnl, f.fair_probability,
                       CASE
                         WHEN l.settlement_status = 'won' THEN 1.0
                         WHEN l.settlement_status = 'lost' THEN 0.0
                         ELSE NULL
                       END AS actual
                FROM paper_bet_ledger l
                LEFT JOIN forecasts f
                  ON f.run_id = l.latest_run_id
                 AND f.market_id = l.market_id
                 AND f.outcome_id = l.outcome_id
                WHERE l.settlement_status IN ('won', 'lost', 'push')
            )
            SELECT COUNT(*) AS settled_count,
                   COUNT(actual) AS graded_count,
                   SUM(CASE WHEN settlement_status = 'won' THEN 1 ELSE 0 END)
                     AS won_count,
                   SUM(CASE WHEN settlement_status = 'lost' THEN 1 ELSE 0 END)
                     AS lost_count,
                   SUM(CASE WHEN settlement_status = 'push' THEN 1 ELSE 0 END)
                     AS push_count,
                   AVG(actual) AS hit_rate,
                   AVG(POWER(fair_probability - actual, 2)) AS brier_score,
                   COALESCE(SUM(realized_pnl), 0) AS realized_pnl
            FROM settled
            """
        ).fetchone()
        by_venue_rows = con.execute(
            """
            WITH settled AS (
                SELECT l.settlement_status, l.venue, l.product_type,
                       l.realized_pnl, f.fair_probability,
                       CASE
                         WHEN l.settlement_status = 'won' THEN 1.0
                         WHEN l.settlement_status = 'lost' THEN 0.0
                         ELSE NULL
                       END AS actual
                FROM paper_bet_ledger l
                LEFT JOIN forecasts f
                  ON f.run_id = l.latest_run_id
                 AND f.market_id = l.market_id
                 AND f.outcome_id = l.outcome_id
                WHERE l.settlement_status IN ('won', 'lost', 'push')
            )
            SELECT venue, product_type,
                   COUNT(*) AS settled_count,
                   COUNT(actual) AS graded_count,
                   AVG(actual) AS hit_rate,
                   AVG(POWER(fair_probability - actual, 2)) AS brier_score,
                   COALESCE(SUM(realized_pnl), 0) AS realized_pnl
            FROM settled
            GROUP BY venue, product_type
            ORDER BY settled_count DESC, realized_pnl DESC
            """
        ).fetchall()
        by_ev_bucket_rows = con.execute(
            """
            WITH settled AS (
                SELECT l.settlement_status, l.expected_value, l.realized_pnl,
                       f.fair_probability,
                       CASE
                         WHEN l.settlement_status = 'won' THEN 1.0
                         WHEN l.settlement_status = 'lost' THEN 0.0
                         ELSE NULL
                       END AS actual
                FROM paper_bet_ledger l
                LEFT JOIN forecasts f
                  ON f.run_id = l.latest_run_id
                 AND f.market_id = l.market_id
                 AND f.outcome_id = l.outcome_id
                WHERE l.settlement_status IN ('won', 'lost', 'push')
            )
            SELECT CASE
                     WHEN expected_value < 0.03 THEN '<3%'
                     WHEN expected_value < 0.05 THEN '3-5%'
                     WHEN expected_value < 0.10 THEN '5-10%'
                     WHEN expected_value < 0.25 THEN '10-25%'
                     ELSE '25%+'
                   END AS expected_value_bucket,
                   COUNT(*) AS settled_count,
                   COUNT(actual) AS graded_count,
                   AVG(expected_value) AS average_expected_value,
                   AVG(actual) AS hit_rate,
                   AVG(POWER(fair_probability - actual, 2)) AS brier_score,
                   COALESCE(SUM(realized_pnl), 0) AS realized_pnl
            FROM settled
            GROUP BY expected_value_bucket
            ORDER BY MIN(expected_value)
            """
        ).fetchall()

    return {
        "db_path": str(db_path),
        "overall": {
            "settled_count": int(overall[0] or 0),
            "graded_count": int(overall[1] or 0),
            "won_count": int(overall[2] or 0),
            "lost_count": int(overall[3] or 0),
            "push_count": int(overall[4] or 0),
            "hit_rate": _round_optional(overall[5], 4),
            "brier_score": _round_optional(overall[6], 4),
            "realized_pnl": round(overall[7] or 0.0, 2),
        },
        "by_venue": [
            {
                "venue": row[0],
                "product_type": row[1],
                "settled_count": row[2],
                "graded_count": row[3],
                "hit_rate": _round_optional(row[4], 4),
                "brier_score": _round_optional(row[5], 4),
                "realized_pnl": round(row[6] or 0.0, 2),
            }
            for row in by_venue_rows
        ],
        "by_expected_value_bucket": [
            {
                "expected_value_bucket": row[0],
                "settled_count": row[1],
                "graded_count": row[2],
                "average_expected_value": round(row[3] or 0.0, 4),
                "hit_rate": _round_optional(row[4], 4),
                "brier_score": _round_optional(row[5], 4),
                "realized_pnl": round(row[6] or 0.0, 2),
            }
            for row in by_ev_bucket_rows
        ],
    }


def settle_paper_bet(
    *,
    client_order_id: str,
    outcome: str,
    db_path: Path = DEFAULT_DB_PATH,
    settled_at: str | None = None,
) -> SettlementResult:
    if outcome not in {"won", "lost", "push"}:
        msg = "settlement outcome must be one of: won, lost, push"
        raise ValueError(msg)

    init_database(db_path)
    settled = settled_at or _now()
    with duckdb.connect(str(db_path)) as con:
        row = con.execute(
            """
            SELECT product_type, units, price, price_format, cost
            FROM paper_bet_ledger
            WHERE client_order_id = ?
            """,
            [client_order_id],
        ).fetchone()
        if row is None:
            msg = f"unknown paper bet: {client_order_id}"
            raise ValueError(msg)

        product_type, units, price, price_format, cost = row
        payout = _settlement_payout(
            outcome=outcome,
            product_type=product_type,
            units=units,
            price=price,
            price_format=price_format,
            cost=cost,
        )
        realized_pnl = round(payout - cost, 2)
        con.execute(
            """
            UPDATE paper_bet_ledger
            SET settlement_status = ?,
                payout = ?,
                realized_pnl = ?,
                settled_at = ?
            WHERE client_order_id = ?
            """,
            [outcome, payout, realized_pnl, settled, client_order_id],
        )

    return SettlementResult(
        client_order_id=client_order_id,
        settlement_status=outcome,
        payout=payout,
        realized_pnl=realized_pnl,
        settled_at=settled,
    )


def _create_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            timestamp TEXT,
            git_commit TEXT,
            runtime_mode TEXT,
            status TEXT,
            cash DOUBLE,
            equity DOUBLE,
            realized_pnl DOUBLE,
            market_count INTEGER,
            bet_count INTEGER,
            config_json TEXT,
            data_source_json TEXT,
            warnings_json TEXT,
            errors_json TEXT,
            artifact_path TEXT,
            imported_at TEXT
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS markets (
            run_id TEXT,
            market_id TEXT,
            event_id TEXT,
            venue TEXT,
            product_type TEXT,
            title TEXT,
            category TEXT,
            status TEXT,
            close_time TEXT,
            liquidity DOUBLE,
            volume DOUBLE,
            rules_summary TEXT,
            raw_json TEXT,
            PRIMARY KEY (run_id, venue, market_id)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS outcome_quotes (
            run_id TEXT,
            market_id TEXT,
            outcome_id TEXT,
            name TEXT,
            price DOUBLE,
            price_format TEXT,
            implied_probability DOUBLE,
            bid DOUBLE,
            ask DOUBLE,
            metadata_json TEXT,
            PRIMARY KEY (run_id, market_id, outcome_id)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS research_notes (
            run_id TEXT,
            market_id TEXT,
            summary TEXT,
            sources_json TEXT,
            confidence DOUBLE
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS research_signals (
            run_id TEXT,
            market_id TEXT,
            outcome_id TEXT,
            kind TEXT,
            name TEXT,
            direction DOUBLE,
            magnitude DOUBLE,
            confidence DOUBLE,
            source TEXT,
            rationale TEXT
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS forecasts (
            run_id TEXT,
            market_id TEXT,
            outcome_id TEXT,
            fair_probability DOUBLE,
            confidence DOUBLE,
            model_name TEXT,
            rationale TEXT
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS opportunity_diagnostics (
            run_id TEXT,
            market_id TEXT,
            outcome_id TEXT,
            outcome_name TEXT,
            venue TEXT,
            product_type TEXT,
            fair_probability DOUBLE,
            implied_probability DOUBLE,
            entry_price DOUBLE,
            expected_value DOUBLE,
            min_expected_value DOUBLE,
            decision TEXT
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS opportunities (
            run_id TEXT,
            market_id TEXT,
            outcome_id TEXT,
            outcome_name TEXT,
            venue TEXT,
            product_type TEXT,
            action TEXT,
            fair_probability DOUBLE,
            entry_price DOUBLE,
            entry_price_format TEXT,
            implied_probability DOUBLE,
            unit_cost DOUBLE,
            fee_per_unit DOUBLE,
            expected_value DOUBLE,
            edge_before_fees DOUBLE,
            max_units INTEGER,
            reason TEXT
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS wager_intents (
            run_id TEXT,
            client_order_id TEXT,
            venue TEXT,
            product_type TEXT,
            market_id TEXT,
            outcome_id TEXT,
            outcome_name TEXT,
            action TEXT,
            units INTEGER,
            limit_price DOUBLE,
            price_format TEXT,
            estimated_fee DOUBLE,
            estimated_cost DOUBLE,
            expected_value DOUBLE,
            post_only BOOLEAN,
            reduce_only BOOLEAN,
            reason TEXT,
            PRIMARY KEY (run_id, client_order_id)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS risk_decisions (
            run_id TEXT,
            client_order_id TEXT,
            status TEXT,
            reason TEXT,
            checks_json TEXT
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS executions (
            run_id TEXT,
            client_order_id TEXT,
            venue TEXT,
            product_type TEXT,
            market_id TEXT,
            outcome_id TEXT,
            outcome_name TEXT,
            action TEXT,
            units INTEGER,
            limit_price DOUBLE,
            price_format TEXT,
            status TEXT,
            submitted_at TEXT,
            filled_units INTEGER,
            average_fill_price DOUBLE,
            rejection_reason TEXT
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS bets (
            run_id TEXT,
            client_order_id TEXT,
            venue TEXT,
            product_type TEXT,
            market_id TEXT,
            outcome_id TEXT,
            outcome_name TEXT,
            action TEXT,
            units INTEGER,
            price DOUBLE,
            price_format TEXT,
            fee DOUBLE,
            cost DOUBLE,
            filled_at TEXT
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS positions (
            run_id TEXT,
            market_id TEXT,
            outcome_id TEXT,
            outcome_name TEXT,
            product_type TEXT,
            units INTEGER,
            average_price DOUBLE,
            price_format TEXT,
            mark_price DOUBLE,
            market_value DOUBLE
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_bet_ledger (
            client_order_id TEXT PRIMARY KEY,
            first_run_id TEXT,
            latest_run_id TEXT,
            venue TEXT,
            product_type TEXT,
            market_id TEXT,
            outcome_id TEXT,
            outcome_name TEXT,
            action TEXT,
            units INTEGER,
            price DOUBLE,
            price_format TEXT,
            fee DOUBLE,
            cost DOUBLE,
            expected_value DOUBLE,
            filled_at TEXT,
            settlement_status TEXT,
            payout DOUBLE,
            realized_pnl DOUBLE,
            settled_at TEXT
        )
        """
    )


def _ensure_database_exists(db_path: Path) -> None:
    if not db_path.exists():
        init_database(db_path)


def _delete_run(con: duckdb.DuckDBPyConnection, run_id: str) -> None:
    for table in RUN_SCOPED_TABLES:
        con.execute(f"DELETE FROM {table} WHERE run_id = ?", [run_id])


def _insert_run(
    con: duckdb.DuckDBPyConnection,
    payload: dict[str, Any],
    artifact_path: Path,
    imported_at: str,
) -> None:
    con.execute(
        """
        INSERT INTO runs VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        [
            payload["run_id"],
            payload.get("timestamp"),
            payload.get("git_commit"),
            payload.get("runtime_mode"),
            payload.get("status"),
            payload.get("cash"),
            payload.get("equity"),
            payload.get("realized_pnl"),
            len(payload.get("markets", [])),
            len(payload.get("bets", [])),
            _json(payload.get("config", {})),
            _json(payload.get("data_source", {})),
            _json(payload.get("warnings", [])),
            _json(payload.get("errors", [])),
            str(artifact_path),
            imported_at,
        ],
    )


def _insert_markets(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    payload: dict[str, Any],
) -> None:
    for market in payload.get("markets", []):
        con.execute(
            """
            INSERT INTO markets VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                run_id,
                market.get("market_id"),
                market.get("event_id"),
                market.get("venue"),
                market.get("product_type"),
                market.get("title"),
                market.get("category"),
                market.get("status"),
                market.get("close_time"),
                market.get("liquidity"),
                market.get("volume"),
                market.get("rules_summary"),
                _json(market),
            ],
        )
        for outcome in market.get("outcomes", []):
            con.execute(
                """
                INSERT INTO outcome_quotes VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    run_id,
                    market.get("market_id"),
                    outcome.get("outcome_id"),
                    outcome.get("name"),
                    outcome.get("price"),
                    outcome.get("price_format"),
                    outcome.get("implied_probability"),
                    outcome.get("bid"),
                    outcome.get("ask"),
                    _json(outcome.get("metadata", {})),
                ],
            )


def _insert_research_notes(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    payload: dict[str, Any],
) -> None:
    for note in payload.get("research_notes", []):
        con.execute(
            "INSERT INTO research_notes VALUES (?, ?, ?, ?, ?)",
            [
                run_id,
                note.get("market_id"),
                note.get("summary"),
                _json(note.get("sources", [])),
                note.get("confidence"),
            ],
        )


def _insert_research_signals(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    payload: dict[str, Any],
) -> None:
    for note in payload.get("research_notes", []):
        for signal in note.get("signals", []):
            con.execute(
                """
                INSERT INTO research_signals VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    run_id,
                    signal.get("market_id") or note.get("market_id"),
                    signal.get("outcome_id"),
                    signal.get("kind"),
                    signal.get("name"),
                    signal.get("direction"),
                    signal.get("magnitude"),
                    signal.get("confidence"),
                    signal.get("source"),
                    signal.get("rationale"),
                ],
            )


def _insert_forecasts(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    payload: dict[str, Any],
) -> None:
    for forecast in payload.get("forecasts", []):
        con.execute(
            "INSERT INTO forecasts VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                run_id,
                forecast.get("market_id"),
                forecast.get("outcome_id"),
                forecast.get("fair_probability"),
                forecast.get("confidence"),
                forecast.get("model_name"),
                forecast.get("rationale"),
            ],
        )


def _insert_opportunity_diagnostics(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    payload: dict[str, Any],
) -> None:
    for diagnostic in payload.get("opportunity_diagnostics", []):
        con.execute(
            """
            INSERT INTO opportunity_diagnostics VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                run_id,
                diagnostic.get("market_id"),
                diagnostic.get("outcome_id"),
                diagnostic.get("outcome_name"),
                diagnostic.get("venue"),
                diagnostic.get("product_type"),
                diagnostic.get("fair_probability"),
                diagnostic.get("implied_probability"),
                diagnostic.get("entry_price"),
                diagnostic.get("expected_value"),
                diagnostic.get("min_expected_value"),
                diagnostic.get("decision"),
            ],
        )


def _insert_opportunities(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    payload: dict[str, Any],
) -> None:
    for opportunity in payload.get("opportunities", []):
        con.execute(
            """
            INSERT INTO opportunities VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                run_id,
                opportunity.get("market_id"),
                opportunity.get("outcome_id"),
                opportunity.get("outcome_name"),
                opportunity.get("venue"),
                opportunity.get("product_type"),
                opportunity.get("action"),
                opportunity.get("fair_probability"),
                opportunity.get("entry_price"),
                opportunity.get("entry_price_format"),
                opportunity.get("implied_probability"),
                opportunity.get("unit_cost"),
                opportunity.get("fee_per_unit"),
                opportunity.get("expected_value"),
                opportunity.get("edge_before_fees"),
                opportunity.get("max_units"),
                opportunity.get("reason"),
            ],
        )


def _insert_wager_intents(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    payload: dict[str, Any],
) -> None:
    for intent in payload.get("wager_intents", []):
        con.execute(
            """
            INSERT INTO wager_intents VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                run_id,
                intent.get("client_order_id"),
                intent.get("venue"),
                intent.get("product_type"),
                intent.get("market_id"),
                intent.get("outcome_id"),
                intent.get("outcome_name"),
                intent.get("action"),
                intent.get("units"),
                intent.get("limit_price"),
                intent.get("price_format"),
                intent.get("estimated_fee"),
                intent.get("estimated_cost"),
                intent.get("expected_value"),
                intent.get("post_only"),
                intent.get("reduce_only"),
                intent.get("reason"),
            ],
        )


def _insert_risk_decisions(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    payload: dict[str, Any],
) -> None:
    for decision in payload.get("risk_decisions", []):
        con.execute(
            "INSERT INTO risk_decisions VALUES (?, ?, ?, ?, ?)",
            [
                run_id,
                decision.get("client_order_id"),
                decision.get("status"),
                decision.get("reason"),
                _json(decision.get("checks", [])),
            ],
        )


def _insert_executions(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    payload: dict[str, Any],
) -> None:
    for execution in payload.get("executions", []):
        con.execute(
            """
            INSERT INTO executions VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                run_id,
                execution.get("client_order_id"),
                execution.get("venue"),
                execution.get("product_type"),
                execution.get("market_id"),
                execution.get("outcome_id"),
                execution.get("outcome_name"),
                execution.get("action"),
                execution.get("units"),
                execution.get("limit_price"),
                execution.get("price_format"),
                execution.get("status"),
                execution.get("submitted_at"),
                execution.get("filled_units"),
                execution.get("average_fill_price"),
                execution.get("rejection_reason"),
            ],
        )


def _insert_bets(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    payload: dict[str, Any],
) -> None:
    for bet in payload.get("bets", []):
        con.execute(
            """
            INSERT INTO bets VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                run_id,
                bet.get("client_order_id"),
                bet.get("venue"),
                bet.get("product_type"),
                bet.get("market_id"),
                bet.get("outcome_id"),
                bet.get("outcome_name"),
                bet.get("action"),
                bet.get("units"),
                bet.get("price"),
                bet.get("price_format"),
                bet.get("fee"),
                bet.get("cost"),
                bet.get("filled_at"),
            ],
        )


def _insert_positions(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    payload: dict[str, Any],
) -> None:
    for position in payload.get("positions", []):
        con.execute(
            "INSERT INTO positions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                run_id,
                position.get("market_id"),
                position.get("outcome_id"),
                position.get("outcome_name"),
                position.get("product_type"),
                position.get("units"),
                position.get("average_price"),
                position.get("price_format"),
                position.get("mark_price"),
                position.get("market_value"),
            ],
        )


def _insert_ledger_bets(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    payload: dict[str, Any],
) -> None:
    if payload.get("runtime_mode") != "paper":
        return

    intents = {
        intent.get("client_order_id"): intent
        for intent in payload.get("wager_intents", [])
    }
    for bet in payload.get("bets", []):
        client_order_id = bet.get("client_order_id")
        exists = con.execute(
            "SELECT COUNT(*) FROM paper_bet_ledger WHERE client_order_id = ?",
            [client_order_id],
        ).fetchone()[0]
        if exists:
            con.execute(
                """
                UPDATE paper_bet_ledger
                SET latest_run_id = ?
                WHERE client_order_id = ?
                """,
                [run_id, client_order_id],
            )
            continue

        intent = intents.get(client_order_id, {})
        con.execute(
            """
            INSERT INTO paper_bet_ledger VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                client_order_id,
                run_id,
                run_id,
                bet.get("venue"),
                bet.get("product_type"),
                bet.get("market_id"),
                bet.get("outcome_id"),
                bet.get("outcome_name"),
                bet.get("action"),
                bet.get("units"),
                bet.get("price"),
                bet.get("price_format"),
                bet.get("fee"),
                bet.get("cost"),
                intent.get("expected_value", 0.0),
                bet.get("filled_at"),
                "open",
                0.0,
                0.0,
                None,
            ],
        )


def _settlement_payout(
    *,
    outcome: str,
    product_type: str,
    units: int,
    price: float,
    price_format: str,
    cost: float,
) -> float:
    if outcome == "push":
        return round(cost, 2)
    if outcome == "lost":
        return 0.0
    if product_type == "prediction_contract":
        return round(float(units), 2)
    return round(float(units) * _decimal_odds(price, price_format), 2)


def _decimal_odds(price: float, price_format: str) -> float:
    if price_format == "decimal":
        return price
    if price_format == "probability":
        return 1.0 / price
    if price > 0:
        return 1.0 + (price / 100.0)
    return 1.0 + (100.0 / abs(price))


def _hit_rate(*, won_count: int, lost_count: int) -> float | None:
    graded_count = won_count + lost_count
    if graded_count == 0:
        return None
    return round(won_count / graded_count, 4)


def _round_optional(value: object, digits: int) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _count(con: duckdb.DuckDBPyConnection, table: str) -> int:
    try:
        return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except duckdb.CatalogException:
        return 0


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True)


def _loads_json_object(value: object) -> dict[str, object]:
    if not value:
        return {}
    try:
        payload = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
