#!/usr/bin/env python3
"""Launch Kotha Bondhu (কথাবন্ধু).

    python ai_version/run.py                 # open in your browser
    python ai_version/run.py --port 9000
    python ai_version/run.py --no-browser
    python ai_version/run.py --models D:\\kotha-bondhu-models
"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from server import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
