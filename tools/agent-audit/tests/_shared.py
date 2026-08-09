# テストの共有前置き — 環境隔離・モジュールのロード・共通ヘルパ（ここ自体はテストを持たない）。
#
# LLM 不要（run_llm はテスト内で差し替える）。標準ライブラリの unittest で完結する。
#   python3 -m unittest discover -s tools/agent-audit/tests   # 全部
#   python3 -m unittest tests.test_collect                    # 1 機能だけ
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# --- 環境隔離（trio の _shared.py と同じ事故防止リスト） ----------------------
# 自己更新が開発者の実 registry・ネットワークへ届かないように。
os.environ["KIRO_SKILL_REGISTRY"] = os.path.join(tempfile.gettempdir(),
                                                 "agent-audit-test-no-registry")
# agent CLI 定義の探索が開発者のホームへ届かないように（プラグイン契約の env）。
os.environ["KIRO_AGENTS_DIR"] = os.path.join(tempfile.gettempdir(),
                                             "agent-audit-test-no-agents")
# **同梱定義（リポジトリの agents/）は KIRO_AGENTS_DIR では消せない**——探索順の最後に
# 必ず入るので、その `session_log.paths` の `~` が開発者の実ストア（~/.claude/projects や
# ~/.local/share/opencode/opencode.db）を指す。実際にその CLI を使っている PC では
# 収集件数が環境依存になり、テストが落ちる。ホームごと一時ディレクトリへ逃がす。
os.environ["HOME"] = tempfile.mkdtemp(prefix="agent-audit-test-home-")
os.environ["USERPROFILE"] = os.environ["HOME"]
# リポジトリルートで走らせない（過去に実リポジトリへ書いた事故の再発防止）。
os.chdir(tempfile.mkdtemp(prefix="agent-audit-tests-"))

from agent_audit import configfile, collect, distill, extract, gccmd, llm, readers, \
    scrub, stats, store, tasksout, tuning, usage, util  # noqa: E402
from agent_audit.cli import main as cli_main  # noqa: E402


class AuditTestCase(unittest.TestCase):
    """一時 audit/budget ディレクトリと Namespace 風 args を用意する基底クラス。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agent-audit-case-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.audit_dir = os.path.join(self.tmp, "audit")
        self.budget_dir = os.path.join(self.tmp, "budget")
        os.makedirs(os.path.join(self.budget_dir, "ledger"), exist_ok=True)

    def make_args(self, **over):
        import argparse
        ns = argparse.Namespace()
        for key, dflt in configfile.CONFIG_DEFAULTS.items():
            setattr(ns, key, dflt if not isinstance(dflt, (dict, list)) else
                    json.loads(json.dumps(dflt)))
        ns.audit_dir = self.audit_dir
        ns.budget_dir = self.budget_dir
        ns.gc_auto = False
        for k, v in over.items():
            setattr(ns, k, v)
        return ns

    def make_store(self):
        return store.Store(self.audit_dir)

    def write_ledger(self, day: str, rows: "list[dict]"):
        path = os.path.join(self.budget_dir, "ledger", f"{day}.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return path


def ledger_row(**over) -> dict:
    row = {"ts": "2026-08-03T10:00:00Z", "workload": "flow", "tool": "agent-flow",
           "seconds": 60.0, "ref": "worker", "node": "pc-a",
           "agent_cli": "claude", "model": "sonnet"}
    row.update(over)
    return row


def claude_session_jsonl(path: str, *, sid="sess-1", cwd="/home/u/repo",
                         t0="2026-08-03T09:59:30Z", t1="2026-08-03T10:00:30Z",
                         tokens=(1000, 200)):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = [
        {"type": "user", "timestamp": t0, "sessionId": sid, "cwd": cwd,
         "message": {"role": "user", "content": "直して"}},
        {"type": "assistant", "timestamp": t1, "sessionId": sid,
         "message": {"role": "assistant", "model": "claude-sonnet-4",
                     "content": [{"type": "text", "text": "直しました"}],
                     "usage": {"input_tokens": tokens[0], "output_tokens": tokens[1]}}},
    ]
    with open(path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return path
