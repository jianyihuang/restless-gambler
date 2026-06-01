from __future__ import annotations

from pathlib import Path

from restless_gambler.config import DEFAULT_MARKETS_PATH, load_config
from restless_gambler.namespace import namespace_report
from restless_gambler.paths import PROJECT_ROOT


def test_namespace_report_has_no_marketforge_conflicts(tmp_path):
    marketforge_root = tmp_path / "marketforge"
    marketforge_root.mkdir()
    (marketforge_root / "pyproject.toml").write_text(
        """
[project]
name = "marketforge"

[project.scripts]
marketforge = "marketforge.cli:main"
""",
        encoding="utf-8",
    )
    (marketforge_root / ".env.example").write_text(
        """
MARKETFORGE_KILL_SWITCH=false
MARKETFORGE_BARS_PATH=data/bars/daily
ALPACA_API_KEY_ID=
ALPACA_API_SECRET_KEY=
""",
        encoding="utf-8",
    )

    report = namespace_report(marketforge_root=marketforge_root)

    assert report["ok"] is True
    assert report["conflicts"] == []
    assert report["restless_gambler"]["console_script"] == "restless-gambler"
    assert report["marketforge"]["console_scripts"] == ["marketforge"]


def test_default_paths_are_project_root_anchored(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text(
        "RG_MARKETS_PATH=/tmp/should-not-be-loaded-from-cwd.json\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RG_MARKETS_PATH", "")

    config = load_config(mode="paper")

    assert config.data.markets_path == DEFAULT_MARKETS_PATH
    assert _is_relative_to(config.data.markets_path, PROJECT_ROOT)
    assert _is_relative_to(config.artifacts.output_dir, PROJECT_ROOT)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True
