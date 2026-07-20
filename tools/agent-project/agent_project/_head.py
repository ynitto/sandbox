# _head.py — 共有 import と最下層の定数（元 agent-project.py の冒頭ブロック）。
# この断片は単体 import しない。agent_project/__init__.py が共有名前空間へ exec 合成する。
from __future__ import annotations

import argparse
import contextlib
import fnmatch
import hashlib
import io
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # 非 POSIX では daemon 検知不可（常に run にフォールバック）
    fcntl = None
try:
    import msvcrt  # Windows のみ。POSIX では None（fcntl を使う）。
except ImportError:
    msvcrt = None

# エージェント共通ホームのディレクトリ名。`.agent` から `.agents` へ改名した
# （複数のエージェントが相乗りする持ち物であることを名前で示す）。
# 旧ホームが残っている環境では、新ホームがまだ無い間だけ旧ホームを使う——両方へ書くと
# 実行制御や予算の状態が分裂し、「どちらか片方だけ正しい」状況が生まれる。
AGENT_HOME = ".agents"
AGENT_HOME_LEGACY = ".agent"


def agent_home_dir(root=None) -> Path:
    """エージェント共通ホーム（既定 ~/.agents）。旧 ~/.agent しか無ければそちらを返す。"""
    base = Path(root).expanduser() if root else Path.home()
    new, old = base / AGENT_HOME, base / AGENT_HOME_LEGACY
    return old if (not new.exists() and old.exists()) else new


def agent_home_subdir(env_var: str, *parts: str) -> Path:
    """共通ホーム配下の状態ディレクトリ（`$<env_var>` があればそれを最優先）。

    **判定はサブディレクトリ単位で行う。** ホーム単位で見ると、`.agents/skills` だけ先に
    作られた環境（スキル導入が先行した）で「新ホームは在る」と判断され、まだ移していない
    `.agent/control` を見失う。項目ごとに実在する方へ寄せれば、移行が部分的に進んだ
    状態でも状態は 1 か所に定まる。"""
    override = os.environ.get(env_var)
    if override:
        return Path(os.path.expanduser(override))
    home = Path.home()
    new, old = home / AGENT_HOME, home / AGENT_HOME_LEGACY
    new_p, old_p = new.joinpath(*parts), old.joinpath(*parts)
    return old_p if (not new_p.exists() and old_p.exists()) else new_p


VALID_STATUS = ("inbox", "draft", "proposed", "ready", "doing", "done", "blocked", "review",
                "offloaded", "rejected")
CONSUMABLE = ("ready", "todo")  # 実行待ち。todo は ready の後方互換エイリアス。draft は消化対象外
# proposed: 実行前レビュー待ち（plan_review・既定 on）。人の承認（approve）で初めて ready になり、
#   差し戻し（needs feedback）は agent-project がタスクを修正して再提案、却下（reject）は廃止＋再計画。
# rejected: 却下済み（archive へ退避される終端。DELIVERY には載せない）。
# offloaded: 実行層 daemon へ非ブロッキングで submit 済み・結果待ち（act_async）。CONSUMABLE ではない
#   （再 submit しない）が「機械が実行中」＝人待ちでもない。次パスでポーリングして終端したら settle する。
TASK_HEADER_RE = re.compile(r"^##\s+(?P<id>\S+?):\s*(?P<title>.*)$")
FIELD_RE = re.compile(r"^-\s+(?P<key>\w+):\s*(?P<val>.*)$")
POLICY_RE = re.compile(r"^(?P<key>deny|pin|defer|offload|gate|protect|route|spec):\s*(?P<val>.+)$")
DR_HEADER_RE = re.compile(r"^##\s+DR-(\d+)\b")
LEARN_RE = re.compile(r"^- learn:\s*(?P<title>.+?)\s*::\s*(?P<guide>.+)$")
# 回避知識（hold/deny 由来）。learn が「どう解けば良いか（auto-resolve 向け）」なのに対し、
# avoid は「この種のタスクは自動実行してはいけない（人の判断が要る）」を運ぶ。投入/triage 時に
# 類似タスクを検出して ready へ落とさず inbox（人の triage）へ寄せる予防リコールに使う。
# 第2グループ名を guide に揃え、learn 用の照合ヘルパ（_best_learn_match）をそのまま再利用する。
AVOID_RE = re.compile(r"^- avoid:\s*(?P<title>.+?)\s*::\s*(?P<guide>.+)$")
LTM_CATEGORY = "agent-project"  # ltm-use home 内のカテゴリ（昇格先サブディレクトリ）
FEEDBACK_MARKER = "## フィードバック"                  # 旧形式（読み取りは継続サポート）
DECISION_MARKER = "## Decision Outcome"               # MADR 形式の決定記入欄（needs の生成はこちら）
FEEDBACK_MARKERS = (FEEDBACK_MARKER, DECISION_MARKER)
CHECKBOX_RE = re.compile(r"^\s*-\s*\[[ xX]\]")        # 確定チェックボックス行（任意状態）
CHECKED_RE = re.compile(r"^\s*-\s*\[[xX]\]")          # チェック済み（= 確定）

# 停止理由
REASON_DRAINED = "drained"  # 消化可能タスクが尽きた（実質完了）
REASON_BUDGET = "budget"    # 予算（サイクル数/実時間）が尽きた
REASON_COST = "cost"        # 予算（トークン/金額）が尽きた
REASON_THROTTLE = "throttle"  # ソフト予算（throttle 比率）超過＝自動スロットル（watch は report へ降格）
