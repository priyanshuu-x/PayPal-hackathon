"""Lets the eval scripts import the backend package (`app.*`) when run from the repo root."""

import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parent
BACKEND = REPO_ROOT / "backend"
DATASETS = EVAL_DIR / "datasets"
REPORTS = EVAL_DIR / "reports"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
