"""同梱定義（agents/*.json）と配布物（~/.agents/agents/）のドリフト検出。

配布物は install.sh が cp で作る写しで、探索順は配布物を同梱より先に解決する。
写しが古いままだと同梱定義への修正が実機に届かない（json_object_only 欠落で plan の
器分岐が実機で不発、readonly_args の think 反転が届かない、が実際に起きた）。この検査が
守るのは「差があれば必ず言う・意図した上書きと未インストール機には言わない」の両立
——過検知に倒すと first-wins の契約（上に置けば同梱定義を上書きできる）へ恒久警告を
浴びせることになる。
"""
import json
import os
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentcore import agentcli  # noqa: E402

# 実在の同梱定義や開発者の ~/.kiro/agents と衝突しない一意な定義名。
_NAME = "drift-probe"


class BundledDriftTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.bundled = base / "repo" / "agents"
        self.bundled.mkdir(parents=True)
        self.home = base / "agents-home"
        self.dist = self.home / "agents"
        self.project = base / "proj"          # agents/ を持たない中立プロジェクト
        self.project.mkdir()
        self._write(self.bundled / f"{_NAME}.json", {"command": ["probe", "{model}"]})
        # 探索順の環境 seam を隔離する（開発機の実配布物・実 $KIRO_AGENTS_DIR を読まない）
        patcher = mock.patch.dict(os.environ,
                                  {"AGENT_PROJECT_AGENTS_HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("KIRO_AGENTS_DIR", None)   # patch.dict が終了時に元へ戻す

    def _write(self, path: Path, data: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def _drift(self):
        return agentcli.bundled_drift(project_dir=self.project, bundled=self.bundled)

    def test_identical_copy_is_silent(self):
        self._write(self.dist / f"{_NAME}.json", {"command": ["probe", "{model}"]})
        self.assertEqual(self._drift(), [])

    def test_stale_copy_is_reported_with_the_resolved_winner(self):
        # 実害の形そのもの: 同梱を直したのに配布物が古い。探索順では配布物が勝つので、
        # 「実機で効いているのはどのファイルか」も所見に持たせる。
        self._write(self.dist / f"{_NAME}.json", {"command": ["probe"]})
        drift = self._drift()
        self.assertEqual([d["name"] for d in drift], [_NAME])
        self.assertEqual(drift[0]["reason"], "differs")
        self.assertEqual(Path(drift[0]["resolved"]),
                         (self.dist / f"{_NAME}.json").resolve())

    def test_missing_copy_is_reported_when_installed(self):
        # 配布 dir がある（＝install 済みの機）のに写しが無い定義は、zipapp 配布では
        # 未知の agent_cli になる。「新しい定義を足して配り忘れる」も同じドリフト。
        self._write(self.dist / "other.json", {"command": ["other"]})
        drift = self._drift()
        self.assertEqual([(d["name"], d["reason"]) for d in drift],
                         [(_NAME, "missing")])

    def test_never_installed_machine_is_silent(self):
        # 配布 dir 自体が無ければ言わない。リポジトリ直接実行では探索順の最後で
        # 同梱定義が解決されるので、写しが無いことは害にならない。
        self.assertEqual(self._drift(), [])

    def test_zipapp_install_without_bundled_dir_is_silent(self):
        # 配布インストール（zipapp）では同梱 dir が解決できない＝配布物が正。
        self._write(self.dist / f"{_NAME}.json", {"command": ["probe"]})
        with mock.patch.object(agentcli, "_bundled_dir", return_value=None):
            self.assertEqual(agentcli.bundled_drift(project_dir=self.project), [])

    def test_intentional_override_is_not_a_drift(self):
        # first-wins は契約: $KIRO_AGENTS_DIR / プロジェクトの agents/ に置いた独自定義が
        # 勝っている名前へ恒久警告を出さない（install.sh 自身がそこを独自定義の置き場と
        # 案内している）。
        self._write(self.dist / f"{_NAME}.json", {"command": ["probe"]})   # 古い写し
        override = Path(self._tmp.name) / "override"
        self._write(override / f"{_NAME}.json", {"command": ["mine"]})
        with mock.patch.dict(os.environ, {"KIRO_AGENTS_DIR": str(override)}):
            self.assertEqual(self._drift(), [])
        self._write(self.project / "agents" / f"{_NAME}.json", {"command": ["mine"]})
        self.assertEqual(self._drift(), [])

    def test_drift_is_caught_even_when_cwd_resolves_the_bundled_file(self):
        # リポジトリ直下で走らせると探索順 2 番目（プロジェクトの agents/）が同梱 dir と
        # 同一になり、この文脈では同梱定義が勝つ。それでも配布物の古さは他の全プロセス
        # （常駐体・zipapp のエンジン）に効き続けるので、所見は出る。
        self._write(self.dist / f"{_NAME}.json", {"command": ["probe"]})
        drift = agentcli.bundled_drift(project_dir=self.bundled.parent,
                                       bundled=self.bundled)
        self.assertEqual([(d["name"], d["reason"]) for d in drift],
                         [(_NAME, "differs")])


if __name__ == "__main__":
    unittest.main()
