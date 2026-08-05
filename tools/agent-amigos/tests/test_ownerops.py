"""agent-amigos の単体テスト — ownerops（acceptance: agent の受入判定プロンプト）。

共有の前置き（環境隔離・モジュールのロード・AmigosTestCase）は `_shared.py` にある。

    python -m unittest discover -s tools/agent-amigos/tests
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import *  # noqa: E402,F401,F403 — 共有の前置き（環境隔離・km ロード・共通ヘルパ）

from unittest import mock  # noqa: E402

from agent_amigos import ownerops  # noqa: E402
from agent_amigos.ownerops import acceptance_turn  # noqa: E402
from agentcore.promptrender import dumps_prompt  # noqa: E402


class AcceptancePromptCompressionTests(AmigosTestCase):
    """案 K-1: 受入判定プロンプトの MANIFEST は compact JSON で注入する（内容は不変）。"""

    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.tmp, "home")

    def _drive_to_reviewing(self, mid="am-accept"):
        mid = self.post(mid=mid)
        d = self.daemon(home=self.home)
        for _ in range(14):
            d.cycle()
            if self.phase(mid) in ("reviewing", "done"):
                break
        self.assertEqual(self.phase(mid), "reviewing")
        return mid

    def test_manifest_is_injected_as_compact_json(self):
        mid = self._drive_to_reviewing()
        mp = self.bus.mission(mid)
        mission = load_mission(mp)
        manifest = read_json(mp.manifest())

        captured = {}

        def fake_run_agent(prompt, cli):
            captured["prompt"] = prompt
            return '{"accept": true, "feedback": ""}'

        with mock.patch.object(ownerops.agentcli, "run_agent", fake_run_agent):
            result = acceptance_turn(self.bus, mp, mission, "owner-node",
                                     agent_cli="claude", home=self.home)

        self.assertEqual(result, "accepted")
        prompt = captured["prompt"]
        # 内容は不変（manifest の全キー・値がそのまま含まれる）
        self.assertIn(dumps_prompt(manifest), prompt)
        # 既定区切り（", " / ": "）ではなく compact（","/":"）である
        manifest_line = next(ln for ln in prompt.splitlines() if ln.startswith("{"))
        self.assertNotIn(", ", manifest_line)
        self.assertNotIn(": ", manifest_line)


if __name__ == "__main__":
    unittest.main()
