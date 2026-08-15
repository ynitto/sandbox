# テストの共有前置き（実装計画 W3-2 の分割で test_agent_amigos.py から切り出したもの）。
#
# ここ自体はテストを持たない。各 test_<機能>.py が先頭で `from _shared import *` して
# 取り込む共通部分——環境隔離・モジュールのロード・AmigosTestCase・共通ヘルパを置く。
#
# LLM 不要（stub のみ）。標準ライブラリの unittest で完結する。
#   python3 -m unittest discover -s tools/agent-amigos/tests   # 全部
#   python3 -m unittest tests.test_turns                       # 1 機能だけ
#
# 検証対象（設計書 §9 P0 のコアテスト）:
#   - 役割ミッション表の正規化・検証
#   - claim の決定的タイブレーク（二重アサインなし）・lease 失効 → 再募集
#   - 1 ノード self-staff での E2E（質問/回答 → 成果物 → 承認 → 統合 → 受入）
#   - 差し戻し（reject）ラウンドの再作業
#   - 予算会計（wrap-up の partial 納品 / on_exhausted=fail）
#   - 静穏化（quiescence）収束
#   - アクション封筒の検証（パス逸脱・不正宛先の棄却）
#   - 未回答質問の owner エスカレーション
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# agent-control をモジュールレベルで隔離する（AmigosTestCase を継承しないテストも守る）。
# 開発者の実 ~/.agents/control/control.json に workloads.amigos の上書きがあると、
# stub 指定を実 CLI（例 ollama-verify/gemma4:12b）へ差し替え、テストごとに本物の
# ローカル LLM を呼んでしまう（実測で発覚——1 テスト数分・スイートが終わらない）。
os.environ["AGENT_CONTROL_DIR"] = os.path.join(
    tempfile.gettempdir(), "agent-amigos-test-no-control")

from agent_amigos.assign import claim_role, mirror_roster, winner  # noqa: E402
from agent_amigos.bus import Bus  # noqa: E402
from agent_amigos.daemon import NodeDaemon  # noqa: E402
from agent_amigos import delivery  # noqa: E402
from agent_amigos.delivery import deliveries_dir, delivery_json  # noqa: E402
from agent_amigos.ownerops import accept_mission, reject_mission  # noqa: E402
from agent_amigos.mission import (convergence_state, derive_phase, load_mission,  # noqa: E402
                                  load_roles, normalize_mission, post_mission)
from agent_amigos.messages import read_inbox, unanswered_questions  # noqa: E402
from agent_amigos.runner import AmigoRunner  # noqa: E402
from agent_amigos.util import read_json, safe_relpath, write_json_atomic  # noqa: E402
from agent_amigos import cli  # noqa: E402


def base_spec(**mission_over):
    m = {"title": "t", "goal": "g", "staffing_timeout": 0,
         "convergence": {"done_when": "reviewer-approved", "quiescence_turns": 5},
         "budget": {"execution_minutes": 10}}
    m.update(mission_over)
    return {
        "mission": m,
        "roles": [
            {"id": "architect", "mission": "設計", "deliverables": ["architecture.md"]},
            {"id": "impl", "mission": "実装", "deliverables": ["src/main.py"],
             "collaborates_with": ["architect"]},
            {"id": "reviewer", "mission": "レビュー", "approver": True},
        ],
    }


class AmigosTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amigos-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bus = Bus(os.path.join(self.tmp, "bus"))
        self.design = os.path.join(self.tmp, "design.md")
        with open(self.design, "w", encoding="utf-8") as f:
            f.write("# design\n受入基準: 成果物が揃うこと。\n")
        os.environ["AGENT_AMIGOS_STUB_COST"] = "0.01"
        self.addCleanup(os.environ.pop, "AGENT_AMIGOS_STUB_COST", None)
        os.environ["AGENT_BUDGET_DIR"] = os.path.join(self.tmp, "node-budget")
        self.addCleanup(os.environ.pop, "AGENT_BUDGET_DIR", None)
        # agent-control も隔離する。開発者の実 ~/.agents/control/control.json に
        # workloads.amigos の上書きがあると stub 指定を実 CLI（例 ollama-verify/12b）へ
        # 差し替え、テストごとに本物のローカル LLM を呼んでしまう（実測で発覚）。
        os.environ["AGENT_CONTROL_DIR"] = os.path.join(self.tmp, "control")
        self.addCleanup(os.environ.pop, "AGENT_CONTROL_DIR", None)
        from agent_amigos import control as _control
        _control._CACHE["mtime"] = None
        self.addCleanup(_control._CACHE.__setitem__, "mtime", None)
        # 手番マーカー（PC 単位の同時実行上限の根拠）も実ホームを汚さない場所へ
        self.turns_dir = os.path.join(self.tmp, "turns")
        os.environ["AGENT_AMIGOS_TURNS_DIR"] = self.turns_dir
        self.addCleanup(os.environ.pop, "AGENT_AMIGOS_TURNS_DIR", None)

    def post(self, spec=None, mid="am-test") -> str:
        roles_path = os.path.join(self.tmp, "roles.json")
        with open(roles_path, "w", encoding="utf-8") as f:
            json.dump(spec or base_spec(), f, ensure_ascii=False)
        return post_mission(self.bus, self.design, roles_path, "owner-node", mid)

    def daemon(self, node="owner-node", **kw) -> NodeDaemon:
        return NodeDaemon(self.bus, node, agent_cli="stub", interval=0, **kw)

    def phase(self, mid):
        mp = self.bus.mission(mid)
        return derive_phase(load_mission(mp), load_roles(mp), mp)


if __name__ == "__main__":
    unittest.main()
