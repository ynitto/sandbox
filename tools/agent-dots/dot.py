#!/usr/bin/env python3
"""旧パスから goalie.py を呼ぶ互換エントリーポイント。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "goalie"))
import goalie
from goalie import *  # re-export the public API


if __name__ == "__main__":
    sys.exit(goalie.main())
