import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import _backend  # noqa: E402, F401  (puts backend/ on sys.path)
