from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agentcore import methods


def _pack(**overrides) -> dict:
    """1 手法だけの tuning.json（既定は worker 役割 × ollama 限定）。"""
    method = {
        "id": "path-grounding",
        "description": "触るパスを実在確認してから編集する",
        "enabled": True,
        "when": {"agent_cli": ["ollama", "ollama-read"], "purposes": ["work", "generate"]},
        "fragments": [{"role": "worker", "text": "パスを ls で確かめてから書く。"}],
        "origin": "実測",
    }
    method.update(overrides)
    return {"version": 1, "revision": 1, "enabled": True,
            "profiles": {"default": {}, "external-facing": {"injections": []}},
            "methods": [method]}


def _ctx(purpose: str, agent_cli: str, model: str = "qwen3.5:9b") -> dict:
    return {"engine": "agent-flow", "workload": "flow", "purpose": purpose,
            "role": methods.role_for(purpose), "agent_cli": agent_cli, "model": model,
            "tier": "", "relative_cost": 0.0}


class MethodSelectionTests(unittest.TestCase):
    """手法パックの選別。**当たらない条件で当たらない**ことが要件。

    弱いモデル向けの散文断片が JSON 契約の役割（`ollama-json` へ振り替わる split 等）へ
    混ざると、出力契約そのものを壊す。条件の取りこぼしは品質改善ではなく事故になる。"""

    def test_matching_worker_call_gets_the_fragment_with_a_marker(self):
        app = methods.select(_pack(), _ctx("work", "ollama"), "req-x-r0")
        self.assertEqual(app["methods"], ["path-grounding"])
        self.assertTrue(app["text"].startswith(methods.MARKER))
        self.assertIn("パスを ls で確かめてから書く。", app["text"])

    def test_other_cli_model_and_purpose_get_nothing(self):
        pack = _pack()
        for purpose, cli in (("work", "claude"),      # クラウド CLI には足さない
                             ("split", "ollama-json"),  # JSON 契約の役割へ散文を混ぜない
                             ("verify", "ollama"),      # when.purposes の外
                             ("planner", "ollama")):
            app = methods.select(pack, _ctx(purpose, cli), "req-x-r0")
            self.assertEqual(app["text"], "", f"{purpose}/{cli}")
            self.assertEqual(app["methods"], [], f"{purpose}/{cli}")

    def test_fragment_role_must_match_the_call_role(self):
        # 断片の role は呼び出しの役割で絞る（worker 用の規律が verify の判定文へ出ない）。
        pack = _pack(fragments=[{"role": "verify", "text": "証跡を引用する。"}],
                     when={"agent_cli": ["ollama"]})
        self.assertEqual(methods.select(pack, _ctx("work", "ollama"))["text"], "")
        self.assertIn("証跡", methods.select(pack, _ctx("verify", "ollama"))["text"])

    def test_enable_is_explicit(self):
        """`enabled` を省いた手法は適用しない（フェイルセーフ）。

        手法プリセットは既定 OFF で、有効化は書き手の明示 true に限る
        （スキーマ側も `method.enabled` を `default: false` として同じ契約を書く）。"""
        self.assertEqual(methods.select(_pack(enabled=None), _ctx("work", "ollama"))["text"], "")
        pack = _pack()
        del pack["methods"][0]["enabled"]
        self.assertEqual(methods.select(pack, _ctx("work", "ollama"))["text"], "")

    def test_disabled_file_is_a_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tuning.json"
            path.write_text(json.dumps({**_pack(), "enabled": False}), encoding="utf-8")
            self.assertIsNone(methods.load(str(path)))
            self.assertEqual(methods.select(None, _ctx("work", "ollama"))["text"], "")

    def test_missing_or_broken_file_is_a_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "tuning.json"
            broken.write_text("{ not json", encoding="utf-8")
            self.assertIsNone(methods.load(str(broken)))
            self.assertIsNone(methods.load(str(Path(tmp) / "absent.json")))


class CurrentTierTests(unittest.TestCase):
    """段は agent-control から読む（agent-profiles はエンジンから読まない契約）。

    `when.tiers` が見る値と dashboard が決めた段が別物にならないよう、読み口は 1 つに寄せる。
    """

    def _control(self, tmp: str, payload: dict) -> None:
        Path(tmp, "control.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_tier_comes_from_the_control_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._control(tmp, {"version": 1, "workloads": {
                "flow": {"agent_cli": "ollama", "model": "qwen3.5:9b", "tier": "small"},
                "project": {"agent_cli": "codex"}}})
            self.assertEqual(methods.current_tier(tmp, "flow"), "small")
            self.assertEqual(methods.current_tier(tmp, "project"), "", "段の宣言が無ければ空")
            self.assertEqual(methods.current_tier(tmp, "amigos"), "", "未知のワークロードも空")

    def test_profiles_json_is_not_consulted(self):
        # profiles.json だけがあっても段は見えない（読んでいたら契約違反に戻っている）。
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "profiles.json").write_text(
                json.dumps({"version": 1, "state": {"flow": {"tier": "small"}}}), encoding="utf-8")
            self.assertEqual(methods.current_tier(tmp, "flow"), "")

    def test_missing_or_broken_control_is_a_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(methods.current_tier(tmp, "flow"), "")
            Path(tmp, "control.json").write_text("{ not json", encoding="utf-8")
            self.assertEqual(methods.current_tier(tmp, "flow"), "")


class TrialEvidenceTests(unittest.TestCase):
    """trial は「本当に適用できたとき」だけ名乗る。

    宣言だけで variant を名乗ると、何も注入していない実行がその variant の証拠として
    集計され、比較が「効かなかった」ではなく「測っていない」を測ることになる。
    """

    def _pack_with_trial(self, variant_methods, *, copy_method=True):
        pack = _pack()
        if not copy_method:
            pack["methods"] = []          # カタログにあるだけ = tuning.json へ未複製
        pack["trials"] = [{
            "id": "trial-1",
            "variants": [{"id": "baseline", "methods": []},
                         {"id": "candidate", "methods": variant_methods}],
        }]
        return pack

    def test_variant_referencing_an_uncopied_method_is_not_recorded(self):
        pack = self._pack_with_trial(["path-grounding"], copy_method=False)
        for key in ("k0", "k1", "k2", "k3", "k4", "k5"):
            app = methods.select(pack, _ctx("work", "ollama"), key)
            if app["trial"] and app["trial"]["variant"] == "candidate":
                self.fail("注入できていないのに candidate を名乗った")

    def test_control_arm_declaring_nothing_still_counts(self):
        pack = self._pack_with_trial(["path-grounding"])
        seen = set()
        for i in range(24):
            app = methods.select(pack, _ctx("work", "ollama"), f"key-{i}")
            self.assertIsNotNone(app["trial"], "対照群も適用として数える")
            seen.add(app["trial"]["variant"])
        self.assertEqual(seen, {"baseline", "candidate"}, "両群へ割り付く")

    def test_extra_trials_are_reported_not_silently_dropped(self):
        pack = self._pack_with_trial(["path-grounding"])
        pack["trials"].append({"id": "trial-2", "variants": [
            {"id": "baseline", "methods": []}, {"id": "candidate", "methods": []}]})
        app = methods.select(pack, _ctx("work", "ollama"), "k")
        self.assertEqual(app["ignored_trials"], ["trial-2"])


# `unittest.main()` はファイル末尾に置く——クラス定義より前にあると、直接実行
# （`python test_methods.py`）でそれ以降のクラスが未定義のまま起動して黙ってスキップされる。
if __name__ == "__main__":
    unittest.main()
