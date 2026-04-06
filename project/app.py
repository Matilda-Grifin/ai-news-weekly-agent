from __future__ import annotations

# Streamlit entry: exec UI so widgets render reliably (thin import-only wrappers can show empty main).

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent
sys.path.insert(0, str(_root))

_ui = _root / "ai_news_skill" / "ui" / "app.py"
_source = _ui.read_text(encoding="utf-8")
exec(compile(_source, str(_ui), "exec"), globals())  # noqa: S102
__doc__ = None
