#!/usr/bin/env python3
"""旧パスから runner.py を呼ぶ互換エントリーポイント。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "goalie"))
import runner
from runner import *  # re-export the public API


if __name__ == "__main__":
    sys.exit(runner.main())
