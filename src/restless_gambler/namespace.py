from __future__ import annotations

import tomllib
from pathlib import Path

from restless_gambler.config import DEFAULT_ARTIFACTS_DIR, DEFAULT_MARKETS_PATH
from restless_gambler.paths import DATA_DIR, PROJECT_ROOT
from restless_gambler.persistence import DEFAULT_DB_PATH

RESTLESS_PACKAGE = "restless-gambler"
RESTLESS_IMPORT_PACKAGE = "restless_gambler"
RESTLESS_CLI = "restless-gambler"
RESTLESS_ENV_VARS = {
    "RG_KILL_SWITCH",
    "RG_LIVE_TRADING_ENABLED",
    "RG_MARKETS_PATH",
    "KALSHI_API_KEY_ID",
    "KALSHI_PRIVATE_KEY_PATH",
    "KALSHI_BASE_URL",
    "KALSHI_MARKET_DATA_BASE_URL",
    "THE_ODDS_API_KEY",
    "THE_ODDS_API_BASE_URL",
}


def namespace_report(
    *,
    marketforge_root: Path | None = None,
) -> dict[str, object]:
    marketforge_root = (marketforge_root or Path.home() / "marketforge").expanduser()
    marketforge_root = marketforge_root.resolve()
    restless_paths = {
        "project_root": str(PROJECT_ROOT),
        "default_markets_path": str(DEFAULT_MARKETS_PATH),
        "default_artifacts_dir": str(DEFAULT_ARTIFACTS_DIR),
        "default_data_dir": str(DATA_DIR),
        "default_db_path": str(DEFAULT_DB_PATH),
    }
    marketforge = _marketforge_metadata(marketforge_root)

    conflicts: list[str] = []
    if marketforge.get("project_name") == RESTLESS_PACKAGE:
        conflicts.append("project package name conflicts with MarketForge")
    if RESTLESS_CLI in marketforge.get("console_scripts", []):
        conflicts.append("console script name conflicts with MarketForge")

    env_overlap = sorted(RESTLESS_ENV_VARS & set(marketforge.get("env_vars", [])))
    if env_overlap:
        conflicts.append(f"environment variable overlap: {', '.join(env_overlap)}")

    if _is_relative_to(PROJECT_ROOT, marketforge_root):
        conflicts.append("Restless Gambler project root is inside MarketForge")
    for name, raw_path in restless_paths.items():
        path = Path(raw_path).resolve()
        if _is_relative_to(path, marketforge_root):
            conflicts.append(f"{name} resolves inside MarketForge: {path}")

    return {
        "ok": not conflicts,
        "conflicts": conflicts,
        "restless_gambler": {
            "project_name": RESTLESS_PACKAGE,
            "import_package": RESTLESS_IMPORT_PACKAGE,
            "console_script": RESTLESS_CLI,
            "env_vars": sorted(RESTLESS_ENV_VARS),
            "paths": restless_paths,
        },
        "marketforge": marketforge,
    }


def _marketforge_metadata(root: Path) -> dict[str, object]:
    pyproject_path = root / "pyproject.toml"
    env_example_path = root / ".env.example"
    project_name = ""
    console_scripts: list[str] = []
    if pyproject_path.exists():
        payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        project = payload.get("project", {})
        scripts = project.get("scripts", {})
        project_name = str(project.get("name", ""))
        if isinstance(scripts, dict):
            console_scripts = sorted(str(name) for name in scripts)

    return {
        "exists": root.exists(),
        "root": str(root),
        "project_name": project_name,
        "console_scripts": console_scripts,
        "env_vars": _read_env_example_vars(env_example_path),
    }


def _read_env_example_vars(path: Path) -> list[str]:
    if not path.exists():
        return []
    names: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _value = line.split("=", 1)
        key = key.strip()
        if key:
            names.add(key)
    return sorted(names)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True
