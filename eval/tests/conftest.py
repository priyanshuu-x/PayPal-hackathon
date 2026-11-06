import sys
from pathlib import Path

EVAL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(EVAL))

import _paths  # noqa: E402, F401  (puts backend/ on sys.path)
