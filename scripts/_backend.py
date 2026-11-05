"""Lets the scripts import the backend package (`app.*`) when run from the repo root.

Every PayPal call the scripts make goes through `backend/app/paypal/` (CLAUDE.md: the only REST
client for PayPal), and settings come from the same `.env` as the backend.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
