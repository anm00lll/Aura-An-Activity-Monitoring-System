import os
import sys
from pathlib import Path

# Ensure we can import the package from ./src
ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aura.main import main as main_full  # noqa: E402
try:
    from aura.ui import main as main_minimal  # noqa: E402
except Exception:
    main_minimal = None  # type: ignore[assignment]

if __name__ == "__main__":
    # Choose UI based on env or args
    use_minimal = False
    # Env override
    if os.environ.get("AURA_UI", "").lower() in {"minimal", "ttk", "simple"}:
        use_minimal = True
    # CLI flag
    if any(arg in ("--minimal", "-m") for arg in sys.argv[1:]):
        use_minimal = True

    if use_minimal and main_minimal is not None:
        raise SystemExit(main_minimal())
    else:
        raise SystemExit(main_full())
