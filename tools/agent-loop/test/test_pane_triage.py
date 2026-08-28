"""ペイン経路の失敗トリアージと quota 観測（設計 2026-08-27 §7.4-1 / 実装計画 段 7）。

ヘッドレスには `classify_error` による分類があり、quota を見つけたら node-budget の台帳へ
観測行を入れる（`toolloop._tl_failure_hint`）。**ペインには両方が無かった**——定義が
`errors[]` に quota を宣言していても、ペインで枯れた分は誰も読まず、管理面の段判定に
届かない＝ degrade が効かない。この経路で実害が最大の穴だったもの。

ここが見るのは 3 つ。
①ペインで quota が出たとき台帳に `quota` 観測行が入ること、
②`errors[]` の `class` が `send --wait` 以外（＝完了検知の経路）でも効くこと、
③1 ターンに 1 回しか観測しないこと（ポーリングは 2 秒おき）。

tmux もエージェント CLI も起こさない。画面の文字列と定義だけで決まる判定である。
"""
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_loop as al  # noqa: E402

QUOTA_SCREEN = "Error: usage limit reached. resets at 2026-08-28T12:00:00Z\n> "
ENV_SCREEN = "Error: could not connect to ollama\n> "


def _profile(errors):
    """`agents/<名前>.json` を 1 枚作り、そこから CliProfile を組む。"""
    spec = {"command": ["fake-cli"], "interactive": {"command": ["fake-cli", "--tui"]},
            "errors": errors}
    return spec


class _Ledger:
    """台帳を一時領域へ閉じ、書かれた行を読み返せるようにする。"""

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="al-triage-")
        os.environ["AGENT_BUDGET_DIR"] = self.dir
        return self

    def rows(self):
        path = pathlib.Path(self.dir) / "ledger"
        out = []
        for f in sorted(path.glob("*.jsonl")) if path.is_dir() else []:
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    out.append(json.loads(line))
        return out

    def __exit__(self, *_exc):
        os.environ.pop("AGENT_BUDGET_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)


class _Monitor:
    """SlotMonitor を tmux 抜きで 1 ターン分だけ回す。"""

    def __init__(self, profile, screen, *, state="processing", idle=True):
        self.profile = profile
        self.screen = screen
        self.state = state
        self.idle = idle
        self.completed = []
        self.failed = []

    def run(self):
        semaphore = mock.Mock()
        monitor = al.SlotMonitor(semaphore, slot_timeout_seconds=7200)
        monitor.track("%1", on_complete=lambda: self.completed.append(True),
                      on_failure=lambda: self.failed.append(True),
                      profile=self.profile)
        with monitor._lock:
            monitor._pending["%1"]["state"] = self.state
        done = mock.Mock(returncode=0)
        with mock.patch.object(al, "_capture_pane", return_value=self.screen), \
                mock.patch("subprocess.run", return_value=done), \
                mock.patch.object(type(self.profile), "is_idle",
                                  lambda *_a, **_k: self.idle):
            monitor._check_pane("%1")
        return monitor


class PaneTriageTests(unittest.TestCase):
    def _cli_profile(self, errors):
        from agentcore import agentcli
        spec = agentcli.normalize("fake-cli", _profile(errors), "<test>")
        return al.CliProfile("fake-cli", spec)

    def test_a_quota_screen_lands_an_observation_row_in_the_ledger(self):
        """受入条件その 1。ペインで quota が枯れたら管理面へ届く。"""
        errors = [{"match": "usage limit reached", "class": "quota",
                   "quota_kind": "rate_limit", "hint": "上限に達しました"}]
        with _Ledger() as ledger:
            _Monitor(self._cli_profile(errors), QUOTA_SCREEN).run()
            rows = [r for r in ledger.rows() if r.get("event") == "quota"]
        self.assertEqual(len(rows), 1, "quota 観測行がちょうど 1 本")
        self.assertEqual(rows[0]["quota_kind"], "rate_limit")
        self.assertEqual(rows[0]["agent_cli"], "fake-cli")

    def test_the_reset_time_rides_along_when_the_definition_finds_one(self):
        """復帰時刻が画面から読めれば台帳へ載る（いつ再開できるかは段判定の材料）。"""
        errors = [{"match": "usage limit reached", "class": "quota",
                   "quota_kind": "rate_limit", "hint": ""}]
        with _Ledger() as ledger:
            _Monitor(self._cli_profile(errors), QUOTA_SCREEN).run()
            rows = [r for r in ledger.rows() if r.get("event") == "quota"]
        self.assertEqual(rows[0].get("reset_at"), "2026-08-28T12:00:00Z")

    def test_a_classified_failure_turns_a_completion_into_a_failure(self):
        """受入条件その 2。`errors[]` の class が完了検知の経路でも効く。"""
        errors = [{"match": "could not connect", "class": "env", "hint": "起動してください"}]
        with _Ledger():
            run = _Monitor(self._cli_profile(errors), ENV_SCREEN)
            run.run()
        self.assertEqual(run.failed, [True])
        self.assertEqual(run.completed, [], "画面がエラーなら完了として返さない")

    def test_the_failure_reason_names_the_class(self):
        """`pane_or_timeout` のままだと、画面には quota と出ているのに理由が残らない。"""
        errors = [{"match": "usage limit reached", "class": "quota",
                   "quota_kind": "rate_limit", "hint": ""}]
        with _Ledger():
            monitor = _Monitor(self._cli_profile(errors), QUOTA_SCREEN).run()
        self.assertEqual(monitor.failure_reason("%1"), "quota")

    def test_a_transient_class_stays_a_completion(self):
        """再投入で解ける分類はターンを落とさない（ハーネスの分け方に合わせる）。"""
        errors = [{"match": "could not connect", "class": "transient", "hint": ""}]
        with _Ledger():
            run = _Monitor(self._cli_profile(errors), ENV_SCREEN)
            run.run()
        self.assertEqual(run.completed, [True])
        self.assertEqual(run.failed, [])

    def test_a_clean_screen_changes_nothing(self):
        errors = [{"match": "could not connect", "class": "env", "hint": ""}]
        with _Ledger() as ledger:
            run = _Monitor(self._cli_profile(errors), "できました\n> ")
            run.run()
            self.assertEqual(ledger.rows(), [])
        self.assertEqual(run.completed, [True])

    def test_it_observes_once_per_turn_not_once_per_poll(self):
        """ポーリングは 2 秒おき。素で呼ぶと同じ画面から同じ行を何十本も生む。"""
        errors = [{"match": "usage limit reached", "class": "quota",
                   "quota_kind": "rate_limit", "hint": ""}]
        profile = self._cli_profile(errors)
        with _Ledger() as ledger:
            semaphore = mock.Mock()
            monitor = al.SlotMonitor(semaphore, slot_timeout_seconds=7200)
            monitor.track("%1", on_complete=lambda: None, on_failure=lambda: None,
                          profile=profile)
            with monitor._lock:
                monitor._pending["%1"]["state"] = "processing"
            verdicts = [monitor._triage("%1", profile, QUOTA_SCREEN) for _ in range(3)]
            rows = [r for r in ledger.rows() if r.get("event") == "quota"]
        self.assertEqual(verdicts, [True, False, False], "分類は最初の 1 回だけ")
        self.assertEqual(len(rows), 1)

    def test_a_new_turn_does_not_inherit_the_previous_classification(self):
        errors = [{"match": "usage limit reached", "class": "quota",
                   "quota_kind": "rate_limit", "hint": ""}]
        profile = self._cli_profile(errors)
        with _Ledger():
            monitor = _Monitor(profile, QUOTA_SCREEN).run()
            self.assertEqual(monitor.failure_reason("%1"), "quota")
            monitor.track("%1", on_complete=lambda: None, profile=profile)
            self.assertEqual(monitor.failure_reason("%1"), "")

    def test_a_slot_timeout_that_keeps_watching_does_not_spend_the_triage(self):
        """スロットだけ返して完了を待ち続ける経路はターンの終わりではない。

        ここで分類を使い切ると、本当に終わったときの画面（quota が出ているのはそちら）
        を読めなくなる。
        """
        errors = [{"match": "usage limit reached", "class": "quota",
                   "quota_kind": "rate_limit", "hint": ""}]
        profile = self._cli_profile(errors)
        with _Ledger() as ledger:
            monitor = al.SlotMonitor(mock.Mock(), slot_timeout_seconds=1)
            monitor.track("%1", on_complete=lambda: None, on_failure=lambda: None,
                          profile=profile)
            with monitor._lock:
                monitor._pending["%1"].update(state="processing", acquired_at=0.0)
            done = mock.Mock(returncode=0)
            with mock.patch.object(al, "_capture_pane", return_value="作業中\n"), \
                    mock.patch("subprocess.run", return_value=done), \
                    mock.patch.object(type(profile), "is_idle", lambda *_a, **_k: False):
                monitor._check_pane("%1")   # 走ったまま = タイムアウトでスロットだけ返す
            self.assertEqual(ledger.rows(), [], "まだターンは終わっていない")
            with mock.patch.object(al, "_capture_pane", return_value=QUOTA_SCREEN), \
                    mock.patch("subprocess.run", return_value=done), \
                    mock.patch.object(type(profile), "is_idle", lambda *_a, **_k: True):
                monitor._check_pane("%1")   # ここが本当の終わり
            rows = [r for r in ledger.rows() if r.get("event") == "quota"]
        self.assertEqual(len(rows), 1, "終わったときの画面が分類される")

    def test_a_definition_without_errors_is_a_no_op(self):
        with _Ledger() as ledger:
            run = _Monitor(self._cli_profile([]), QUOTA_SCREEN)
            run.run()
            self.assertEqual(ledger.rows(), [])
        self.assertEqual(run.completed, [True])

    def test_a_broken_classifier_does_not_kill_the_monitor(self):
        """ここが落ちるとスロットが解放されず、ペインが上限を食ったまま誰も進めない。"""
        for broken in (mock.Mock(side_effect=RuntimeError("boom")),
                       mock.Mock(return_value="dict ではない"),
                       mock.Mock(return_value=None)):
            profile = mock.Mock()
            profile.classify_failure = broken
            with _Ledger() as ledger:
                monitor = al.SlotMonitor(mock.Mock(), slot_timeout_seconds=7200)
                monitor.track("%1", profile=profile)
                self.assertFalse(monitor._triage("%1", profile, QUOTA_SCREEN))
                self.assertEqual(ledger.rows(), [])


class ProfileClassifiesWithTheSharedImplementationTests(unittest.TestCase):
    """分類はヘッドレスと同じ 1 実装（`agentcli.classify_error`）を引く。"""

    def test_it_delegates_to_the_definition_loader(self):
        from agentcore import agentcli
        spec = agentcli.normalize(
            "fake-cli", _profile([{"match": "usage limit reached", "class": "quota",
                                   "quota_kind": "rate_limit", "hint": "上限"}]), "<test>")
        profile = al.CliProfile("fake-cli", spec)
        got = profile.classify_failure(QUOTA_SCREEN)
        self.assertEqual(got["class"], "quota")
        self.assertEqual(got["quota_kind"], "rate_limit")
        # 分類そのものは共有実装の戻り値と同じ（違うのは now を渡すぶんの reset_at だけ）。
        shared = agentcli.classify_error(spec, QUOTA_SCREEN, detailed=True, now=None)
        self.assertEqual({k: v for k, v in got.items() if k != "reset_at"},
                         {k: v for k, v in shared.items() if k != "reset_at"})
        self.assertIsNone(shared["reset_at"], "now を渡さなければ復帰時刻は出さない")
        self.assertEqual(got["reset_at"], "2026-08-28T12:00:00Z")

    def test_legacy_without_a_definition_has_nothing_to_classify(self):
        self.assertIsNone(al.CliProfile("legacy", None).classify_failure(QUOTA_SCREEN))


if __name__ == "__main__":
    unittest.main()
