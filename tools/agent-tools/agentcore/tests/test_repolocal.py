"""agentcore.repolocal の単体テスト（URL 正規化一致 / ノード宣言からのローカル解決）。

    python -m unittest discover -s tools/agent-tools/agentcore/tests
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentcore import repolocal  # noqa: E402


# URL 正規形のゴールデン表（P2-5）。dashboard 側にも同じ表があり
# （`tools/agent-dashboard/test/repo-url-golden.test.js`）、**片方を直したらもう片方が落ちる**。
# UI の応答性のために JS が同じ規則を独自実装している以上、その代償はここで払う——
# 同じ URL が Python と画面で違って読めると、「宣言したのにローカルクローンが使われない」が
# 理由の分からない形で出る。
#
# fs に依存する項目（symlink 解決）は表に載せない（一時パスは決定的でないため。
# それぞれの言語の個別テストで確かめる）。
NORMALIZE_GOLDEN = [
    ("https://h/a/b.git", "https://h/a/b"),
    ("https://h/a/b/", "https://h/a/b"),
    ("https://Example.com/A/B.git", "https://example.com/a/b"),
    ("git@h:t/app.git", "git@h:t/app"),
    ("git@H:T/App", "git@h:t/app"),
    ("h:t/app.git", "h:t/app"),
    ("ssh://git@h:22/t/app.git", "ssh://git@h:22/t/app"),
    ("", ""),
    ("   ", ""),
]


class NormalizeGoldenTests(unittest.TestCase):
    def test_golden_table(self):
        for raw, want in NORMALIZE_GOLDEN:
            self.assertEqual(repolocal.normalize_repo_url(raw), want, raw)


class NormalizeTests(unittest.TestCase):
    """3 者（agent-project / agent-flow gitcache / agent-flow board）で食い違っていた
    吸収規則を 1 つに揃える。同じ 2 つの URL が経路によって一致したりしなかったりしないこと。"""

    def test_trailing_git_and_slash_are_absorbed(self):
        self.assertTrue(repolocal.same_repo("https://h/a/b.git", "https://h/a/b/"))
        self.assertTrue(repolocal.same_repo("git@h:t/app.git", "git@h:t/app"))

    def test_case_is_absorbed(self):
        # ホスト名は DNS 上そもそも大小文字を区別しない。パス側も実運用で揺れる。
        self.assertTrue(repolocal.same_repo("https://Example.com/A/B.git",
                                            "https://example.com/a/b"))

    def test_different_repos_do_not_match(self):
        self.assertFalse(repolocal.same_repo("https://h/a/b.git", "https://h/a/c.git"))

    def test_empty_never_matches(self):
        self.assertFalse(repolocal.same_repo("", ""))
        self.assertFalse(repolocal.same_repo("https://h/a/b", ""))

    def test_local_paths_are_absolutised(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "app"
            repo.mkdir()
            rel = os.path.relpath(str(repo), os.getcwd())
            self.assertTrue(repolocal.same_repo(str(repo), rel),
                            "相対表記と絶対表記は同じ実体を指す")

    def test_scp_style_url_is_not_treated_as_a_path(self):
        # user@host:path / host:path をパスとして絶対化すると cwd 配下に化けて別物になる。
        self.assertEqual(repolocal.normalize_repo_url("git@h:t/app.git"), "git@h:t/app")
        self.assertEqual(repolocal.normalize_repo_url("host.example:team/repo.git"),
                         "host.example:team/repo")

    def test_windows_drive_is_not_treated_as_scp(self):
        # C:\… を host:path と誤認して URL 扱いしない。
        self.assertFalse(repolocal._looks_like_remote_url(r"C:\repos\app"))
        self.assertFalse(repolocal._looks_like_remote_url("C:/repos/app"))


class NormalizeReposTests(unittest.TestCase):
    def test_list_form(self):
        got = repolocal.normalize_repos([{"url": "u", "local": "/l"}])
        self.assertEqual(got, [{"url": "u", "local": "/l"}])

    def test_legacy_mapping_form(self):
        self.assertEqual(repolocal.normalize_repos({"u": "/l"}), [{"url": "u", "local": "/l"}])

    def test_broken_shapes_are_dropped_not_raised(self):
        # 設定ミスでノードを落とさない（解決できなければミラー取得へ落ちるだけ）。
        self.assertEqual(repolocal.normalize_repos(None), [])
        self.assertEqual(repolocal.normalize_repos(["", 5, {"local": "/l"}]), [])

    def test_non_object_host_config_is_empty_not_crash(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "host.yaml"
            path.write_text("- just\n- a list\n", encoding="utf-8")
            self.assertEqual(repolocal._read_config(str(path)), {})
            self.assertEqual(repolocal.load_node_repos(str(path)), [])


class ResolveLocalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="repolocal-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.clone = self.tmp / "mirrors" / "app"
        self.clone.mkdir(parents=True)

    def _host(self, repos) -> Path:
        home = self.tmp / "agents-home"
        home.mkdir(exist_ok=True)
        p = home / "agent-project.host.json"
        p.write_text(json.dumps({"schema_version": 1, "repos": repos}), encoding="utf-8")
        os.environ["AGENT_PROJECT_AGENTS_HOME"] = str(home)
        self.addCleanup(os.environ.pop, "AGENT_PROJECT_AGENTS_HOME", None)
        return p

    def test_resolves_from_node_declaration(self):
        self._host([{"url": "https://h/t/app.git", "local": str(self.clone)}])
        self.assertEqual(repolocal.resolve_local("https://h/t/app"), str(self.clone))

    def test_url_mismatch_misses(self):
        self._host([{"url": "https://h/t/app.git", "local": str(self.clone)}])
        self.assertEqual(repolocal.resolve_local("https://h/t/other.git"), "")

    def test_declared_but_missing_directory_misses(self):
        # 宣言はあるが実体が無いパスを返すと、worker がそこから worktree を切ろうとして失敗する。
        self._host([{"url": "https://h/t/app.git", "local": str(self.tmp / "gone")}])
        self.assertEqual(repolocal.resolve_local("https://h/t/app.git"), "")
        self.assertTrue(repolocal.resolve_local("https://h/t/app.git", require_dir=False),
                        "宣言の有無だけを見たい呼び出しでは返す")

    def test_no_host_config_is_empty_not_error(self):
        os.environ["AGENT_PROJECT_AGENTS_HOME"] = str(self.tmp / "no-such-home")
        self.addCleanup(os.environ.pop, "AGENT_PROJECT_AGENTS_HOME", None)
        self.assertEqual(repolocal.load_node_repos(), [])
        self.assertEqual(repolocal.resolve_local("https://h/t/app.git"), "")

    def test_explicit_repos_argument_skips_host_config(self):
        self.assertEqual(
            repolocal.resolve_local("https://h/t/app.git",
                                    [{"url": "https://h/t/app", "local": str(self.clone)}]),
            str(self.clone))


class MergeLocalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="repolocal-merge-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.clone = self.tmp / "app"
        self.clone.mkdir()
        self.repos = [{"url": "https://h/t/app.git", "local": str(self.clone)}]

    def test_fills_local(self):
        got = repolocal.merge_local({"url": "https://h/t/app.git", "base": "main"}, self.repos)
        self.assertEqual(got["local"], str(self.clone))
        self.assertEqual(got["base"], "main", "他のキーは保つ")

    def test_existing_local_wins(self):
        # 依頼側が明示したパス・上流で解決済みの値を後から握り潰さない。
        spec = {"url": "https://h/t/app.git", "local": "/explicit"}
        self.assertEqual(repolocal.merge_local(spec, self.repos)["local"], "/explicit")

    def test_no_match_leaves_spec_untouched(self):
        spec = {"url": "https://h/t/other.git"}
        self.assertEqual(repolocal.merge_local(spec, self.repos), spec)
        self.assertNotIn("local", repolocal.merge_local(spec, self.repos))

    def test_non_dict_passes_through(self):
        self.assertIsNone(repolocal.merge_local(None, self.repos))

    def test_does_not_mutate_input(self):
        spec = {"url": "https://h/t/app.git"}
        repolocal.merge_local(spec, self.repos)
        self.assertNotIn("local", spec)


class HostReposNormalizationIsSharedTests(unittest.TestCase):
    """host.yaml の `repos:` を読む規則は 1 実装（P2-5）。

    agent-project 側（`resident_cli._normalize_host_repos`）が写しを持っており、mapping 形の
    `local: null` が片方では `"None"` という**文字列のパス**になっていた。`agentcore.repolocal`
    は「同じ宣言が経路によって違って読める」を潰すためのモジュールなので、宣言の読み方を
    別に持つのは動機に反する。"""

    def test_null_local_is_dropped_not_stringified(self):
        got = repolocal.normalize_repos({"https://h/a.git": {"local": None}})
        self.assertEqual(got, [{"url": "https://h/a.git"}])
        self.assertNotIn("None", json.dumps(got))

    def test_shapes(self):
        self.assertEqual(repolocal.normalize_repos({"https://h/a.git": "/m/a"}),
                         [{"url": "https://h/a.git", "local": "/m/a"}])
        self.assertEqual(repolocal.normalize_repos([{"url": "https://h/a.git"}]),
                         [{"url": "https://h/a.git"}])
        self.assertEqual(repolocal.normalize_repos(["https://h/a.git"]),
                         [{"url": "https://h/a.git"}])
        self.assertEqual(repolocal.normalize_repos([{"local": "/m/a"}]), [],
                         "url の無いエントリは照合に使えない")
        self.assertEqual(repolocal.normalize_repos(None), [])
        self.assertEqual(repolocal.normalize_repos("こわれている"), [])


if __name__ == "__main__":
    unittest.main()
