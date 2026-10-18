import os
import sys

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_REPO = os.path.normpath(os.path.join(_ROOT, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if _REPO not in sys.path:
    sys.path.append(_REPO)
