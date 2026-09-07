"""Make the project root and tests/ importable without path juggling.

Each test module previously prepended the repository root to sys.path by hand.
This does it once, and also lets tests import the shared builders.
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
for path in (ROOT, os.path.join(ROOT, "tests")):
    if path not in sys.path:
        sys.path.insert(0, path)
