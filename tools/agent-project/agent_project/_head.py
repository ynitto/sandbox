# _head.py — 共有 import と最下層の定数（元 agent-project.py の冒頭ブロック）。
# この断片は単体 import しない。agent_project/__init__.py が共有名前空間へ exec 合成する。
from __future__ import annotations

import argparse
import contextlib
import difflib
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
import unicodedata
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

# 共通 git 転送層（設計 §4.1・R1）。board.py の BoardRepo だけでなく stategit.py の direct
# モードも、timeout・資格情報プロンプト抑止といった護りをここから取る。
from agentcore import transport as _transport  # noqa: E402
# JSON の原子的書き出し（tmp へ書いて os.replace）。agent-flow のバス（`runs/<rid>/meta.json`
# 等）は agent-flow 側が全経路この形で書く。agent-project も同じファイルを書く箇所があるので、
# 素の write_text を使うと読み手（別プロセスの agent-flow / dashboard）が途中書きを掴み、
# JSON パースに失敗して「run が存在しない」と誤読する。
from agentcore.protocol import write_json_atomic  # noqa: E402
# 名前空間付き claim（lease 付き入札・(ts, who) タイブレーク）。板への手動入札は
# 自動入札とまったく同じアルゴリズムで書く——UI 側にも常駐体側にも 2 つ目の実装を作らない。
from agentcore import protocol as _protocol  # noqa: E402
# git URL の正規化一致と「このノードのローカルクローン」解決（S3）。同じ判定を
# agent-project / agent-flow(gitcache・board) が別々に実装していて吸収規則が食い違い、
# 同じ 2 つの URL が経路によって一致したりしなかったりしていた（agentcore.nodeid と同型の問題）。
from agentcore import repolocal as _repolocal  # noqa: E402
from agentcore import verifycontract as _verifycontract  # noqa: E402
# エージェント CLI 定義（agents/<name>.json）の読み込みと argv 組み立て（S9）。組み込み
# （kiro/claude/copilot/codex）を含む全 CLI がこの定義で動く。以前は同じ argv 知識が
# agent-project / agent-flow / agent-amigos / dashboard の 4 か所に重複しており、
# 同じ CLI でもツールによってフラグが違う状態になっていた（repolocal と同型の問題）。
from agentcore import agentcli as _agentcli  # noqa: E402
# 指示ドロップ（commands/<name>.json ＋ processed/ ＋ .err）の取り込み規約。プロジェクト配下と
# ノードスコープ（~/.agents/commands/・板の操作）で同じ形を使う——2 実装にすると、利用者から
# 見える挙動（送信済み → 受理済み → 失敗バナー）が 2 種類になる。
from agentcore import commands as _cmddrop  # noqa: E402
# 板（agent-board）の入札選別規則とノード契約バージョン。agent-flow / agent-amigos が
# 「同じ仕様・別実装」で持っていたものの集約先（repolocal と同型の問題）。
from agentcore import board as _boardrules  # noqa: E402
# リトライのバックオフ待ちの唯一の seam（agentcore.transport.backoff_sleep）。素の time.sleep を
# 差し替えると stdlib の subprocess 内部（プロセス終了の 0.001s 倍々ポーリング）にも効いてしまい、
# 高負荷時だけテストが壊れる。リトライ経路はこの名前を通す。
from agentcore.transport import backoff_sleep  # noqa: E402
# node_id（PC の身元）の正規化と既定採番。板・プロジェクト状態・engine のどこから見ても
# 同じ綴りが出ることが不変条件なので、導出はこの 1 実装を通す（P0-3）。設定層（configfile）・
# 常駐体（resident_cli）・doctor が同じ名前を使うため、共有 import のここで束ねる。
from agentcore.nodeid import normalize_node_id, default_node_id  # noqa: E402

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
# offloaded: 委譲公示板へ公示済み・請負側の実行結果待ち。CONSUMABLE ではない
#   （再 submit しない）が「機械が実行中」＝人待ちでもない。次パスでポーリングして終端したら settle する。
TASK_HEADER_RE = re.compile(r"^##\s+(?P<id>\S+?):\s*(?P<title>.*)$")
FIELD_RE = re.compile(r"^-\s+(?P<key>\w+):\s*(?P<val>.*)$")
POLICY_RE = re.compile(r"^(?P<key>deny|pin|defer|offload|gate|protect|route|spec):\s*(?P<val>.+)$")
DR_HEADER_RE = re.compile(r"^##\s+DR-(\d+)\b")
LEARN_RE = re.compile(r"^- learn:\s*(?P<title>.+?)\s*::\s*(?P<guide>.+)$")
# learn のスコープ（W10）: guide 末尾の任意タグ `:: scope=charter:<名前>` / `:: scope=repo:<名前>`。
# タグ無し＝全体。墓標の `:: charter=<名前>` と同じ「末尾タグ」流儀（新しい行形式は増やさない）。
LEARN_SCOPE_RE = re.compile(r"\s*::\s*scope=(?P<kind>charter|repo):(?P<name>\S+)\s*$")
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

# 共有ファイルへ出してよい本文の単一検査器。入力側は置換してから再検査し、state git は
# commit/push 直前に同じ検査器を fail-closed で使う。
_SHARE_REDACTIONS = (
    ("TOKEN", re.compile(
        r"(?i)(?:\bBearer\s+|\b(?:access[_-]?token|auth[_-]?token|token)\s*[:=]\s*)"
        r"(?!\[REDACTED:)[^\s,;'\"<>]+|\b(?:gh[pousr]_|github_pat_|glpat-|sk-(?:proj-)?)"
        r"[A-Za-z0-9_-]{8,}")),
    ("HOME", re.compile(
        r"(?i)(?:/[Uu]sers/|/home/)[^\s/\\:;,'\"<>]+(?:[/\\][^\s:;,'\"<>]+)*|"
        r"[A-Za-z]:\\Users\\[^\s\\:;,'\"<>]+(?:\\[^\s:;,'\"<>]+)*")),
    ("PROMPT", re.compile(
        r"(?im)(?:\b(?:raw|system)[_-]?prompt\b|生プロンプト)\s*[:=]\s*"
        r"(?!\[REDACTED:)(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;]+)|"
        r"\bRAW_PROMPT_[A-Z0-9_-]+\b")),
    ("CREDENTIAL", re.compile(
        r"(?i)\b(?:password|passwd|api[_-]?key|client[_-]?secret|private[_-]?key|credential)"
        r"\s*[:=]\s*(?!\[REDACTED:)[^\s,;'\"<>]+|"
        r"\bhttps?://[^\s/:@]+:[^\s/@]+@")),
)


class ShareSafetyError(RuntimeError):
    """共有前検査の失敗。秘密値は保持せず、パスと検出区分だけを公開する。"""

    def __init__(self, path: str, categories) -> None:
        cats = ",".join(sorted(set(categories))) or "INSPECTION"
        super().__init__(f"共有前検査に失敗: {path} [{cats}]")


def _share_categories(text: str) -> "list[str]":
    return [category for category, pattern in _SHARE_REDACTIONS if pattern.search(text)]


def assert_share_safe(text: str, path: str = "<text>") -> None:
    """禁止値が無いことを確認する。検査器自身の異常も安全側へ倒す。"""
    try:
        categories = _share_categories(text)
    except Exception:
        raise ShareSafetyError(path, ("INSPECTION",)) from None
    if categories:
        raise ShareSafetyError(path, categories)


def redact_for_share(text, path: str = "<text>") -> str:
    """共有本文の禁止値を置換し、同じ検査器で置換後を再検査する。"""
    try:
        redacted = str(text or "")
        for category, pattern in _SHARE_REDACTIONS:
            redacted = pattern.sub(f"[REDACTED:{category}]", redacted)
        assert_share_safe(redacted, path)
        return redacted
    except ShareSafetyError:
        raise
    except Exception:
        raise ShareSafetyError(path, ("INSPECTION",)) from None

# 停止理由
REASON_DRAINED = "drained"  # 消化可能タスクが尽きた（実質完了）
REASON_BUDGET = "budget"    # 予算（サイクル数/実時間）が尽きた
REASON_COST = "cost"        # 予算（トークン/金額）が尽きた
REASON_THROTTLE = "throttle"  # ソフト予算（throttle 比率）超過＝自動スロットル（watch は report へ降格）
REASON_INFRASTRUCTURE = "infrastructure"  # 所有権など安全に継続できない基盤状態
