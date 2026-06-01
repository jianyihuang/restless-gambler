from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
REPORTS_DIR = PROJECT_ROOT / "reports"
EXAMPLES_DIR = PROJECT_ROOT / "examples"


def project_path(*parts: str) -> Path:
    return PROJECT_ROOT.joinpath(*parts)
