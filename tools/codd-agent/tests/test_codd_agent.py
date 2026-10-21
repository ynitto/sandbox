"""codd-agent の結合テスト。実装・設計書の 2 リポジトリを一時フォルダに作り、下請けスクリプトを通す。

LLM は呼ばない。アクションがやる判断（計画を書く・変える）は、テストが代わりにファイルを書いて進める。
graphify は PATH に置いたスタブで差し替え、呼ばれ方（自動更新の有無）を記録する。
"""

from __future__ import annotations

import atexit
import datetime as dt
import hashlib
import io
import json
import marshal
import re
import os
import shutil
import stat
import time
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile
from contextlib import redirect_stdout
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent
REPO = TOOL.parent.parent
sys.path.insert(0, str(TOOL))

import init  # noqa: E402
import install  # noqa: E402

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}

# graphify のスタブ。引数を記録し、更新・検索の作業ファイルを出力先に作る。
GRAPHIFY_STUB = """#!/bin/sh
echo "$PWD $@" >> "{log}"
out_dir="${{GRAPHIFY_OUT:-graphify-out}}"
mkdir -p "$out_dir"
echo "$1" > "$out_dir/manifest.json"
case "$1" in
  update) if [ -f "{fixtures}/$(basename "$PWD").json" ]; then cp "{fixtures}/$(basename "$PWD").json" "$out_dir/graph.json";
          else echo '{{}}' > "$out_dir/graph.json"; fi ;;
  query) echo "NODE $2 [src=docs/api.md loc=L3]" ;;
  affected) echo "Affected nodes for $2"; echo "- use() [calls] src/use.py:L4" ;;
esac
"""

# context-mode のスタブ。引数と索引の置き場所（CONTEXT_MODE_DIR）を記録し、検索では一致した節を返す。
CONTEXT_MODE_STUB = """#!/bin/sh
echo "$1 $CONTEXT_MODE_DIR $*" >> "{log}"
case "$1" in
  index) mkdir -p "$CONTEXT_MODE_DIR/content"; echo "Indexed 2 files" ;;
  search) echo "## 1. hello"; echo "Source: codd:$PWD/docs/api.md"; echo "Type: prose"; echo
          echo "## 2. secret"; echo "Source: codd:$PWD/secret/notes.md" ;;
esac
"""

PLAN = ".plans/2026-10-01-0000-test.md"

PLAN_ALIGNED = """\
# 変更の計画

## やりたいこと

hello にログを足す。

## 守る決まり

なし

## 使ったスキルと道具

なし

## 参照先の前提

- hello は整数を返す（根拠: docs/api.md#hello）

## 参照先の制約

- hello は 1 を返す（根拠: docs/api.md:3）

## 参照先のその他

なし

## ずれ

なし

## 自分の変更案

- src/app.py — `hello` の中でログを出す

## 参照先の変更案

なし

## 影響範囲

なし

## テストの変更案

- 変更不要: このリポジトリにテストはまだ無い（例の小さなリポジトリ）

## 今回やらないこと

なし
"""

PLAN_DRIFT = """\
# 変更の計画

## やりたいこと

hello が 2 を返すようにする。

## 守る決まり

- docs/api.md — 文書の書式: 今の見出しの並び（hello の節）と書き方を保つ

## 使ったスキルと道具

なし

## 参照先の前提

- hello は整数を返す（根拠: docs/api.md）

## 参照先の制約

- hello は 1 を返す（根拠: docs/api.md:3）

## 参照先のその他

- なし

## ずれ

- 制約: hello は 1 を返す（根拠: docs/api.md:3） — やりたいことは 2 を返す（src/app.py）

## 自分の変更案

- src/app.py — `hello` が 2 を返す

## 参照先の変更案

- docs/api.md — `hello` の戻り値を 2 と書き直す

## 影響範囲

- src/app.py — hello の戻り値

## テストの変更案

- 変更不要: このリポジトリにテストはまだ無い（例の小さなリポジトリ）

## 今回やらないこと

- `hello` の引数を足す — 別の回に回す
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}).stdout


def commit(repo: Path, files: dict[str, str], message: str) -> None:
    for rel, body in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


# codd.py を毎回コンパイルし直さない（スクリプトとして起動すると .pyc が使われず、1 回 0.1 秒ほどかかる）。
# 中身ごとにコンパイルしたものを控え、別のプロセスで動かすことは変えない。
_COMPILED: dict[str, str] = {}
_COMPILED_DIR = Path(tempfile.mkdtemp(prefix="codd-compiled-"))
atexit.register(shutil.rmtree, _COMPILED_DIR, True)
_BOOT = ("import marshal, sys, __main__; c, p = sys.argv[1], sys.argv[2]; sys.argv = [p, *sys.argv[3:]]; "
         "sys.path[0] = __import__('os').path.dirname(p); __main__.__file__ = p; "
         "exec(marshal.loads(open(c, 'rb').read()), __main__.__dict__)")

# 報告だけを出す（main を動かさずに読み込み、cmd_report を呼ぶ）。
_REPORT_BOOT = ("import marshal, sys, types, argparse; c, p = sys.argv[1], sys.argv[2]; "
                "sys.path[0] = __import__('os').path.dirname(p); m = types.ModuleType('codd_report'); m.__file__ = p; "
                "sys.modules['codd_report'] = m; exec(marshal.loads(open(c, 'rb').read()), m.__dict__)\n"
                "try:\n    sys.exit(m.cmd_report(m.Ctx(m.repo_root(__import__('pathlib').Path.cwd())), argparse.Namespace()))\n"
                "except m.CoddError as exc:\n    print(f'ERROR {exc}', file=sys.stderr); sys.exit(2)")


def compiled(script: Path) -> str:
    body = script.read_bytes()
    key = hashlib.sha256(body).hexdigest()
    if key not in _COMPILED:
        out = _COMPILED_DIR / f"{key}.bin"
        out.write_bytes(marshal.dumps(compile(body, str(script), "exec")))
        _COMPILED[key] = str(out)
    return _COMPILED[key]


class CoddTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # 置いたばかりの 2 つのリポジトリは、どのテストも同じ。1 度だけ作り、テストごとに写す。
        cls.template = Path(tempfile.mkdtemp())
        impl, design = cls.template / "impl", cls.template / "design"
        for repo in (impl, design):
            repo.mkdir()
            git(repo, "init", "-q", "-b", "main")
        commit(impl, {"src/app.py": "def hello():\n    return 1\n"}, "init")
        commit(design, {"docs/api.md": "# API\n\n## hello\n\nhello は 1 を返す。\n\n実装は `src/app.py`（`src/` の下）。\n"}, "init")
        init.init_repo(impl, "impl", ["../design"])
        init.init_repo(design, "design", ["../impl"])
        for repo in (impl, design):
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "add codd")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.template, True)

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.impl = self.tmp / "impl"
        self.design = self.tmp / "design"
        for name in ("impl", "design"):
            shutil.copytree(self.template / name, self.tmp / name, symlinks=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.log = self.tmp / "graphify.log"

    def run_pa(self, repo: Path, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, **GIT_ENV, "PATH": f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin",
               "HOME": str(self.tmp / "home")}   # 利用者のホームのスキルを拾わない
        script = repo / ".statemachine/codd/codd.py"
        return subprocess.run([sys.executable, "-c", _BOOT, compiled(script), str(script), *args],
                              cwd=repo, capture_output=True, text=True, env=env)

    def report(self, repo: Path) -> subprocess.CompletedProcess:
        """終わりの報告を、記録を書かずに見る（コマンドには無い。`record` が出すものと同じ）。"""
        env = {**os.environ, **GIT_ENV, "PATH": f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin",
               "HOME": str(self.tmp / "home")}
        script = repo / ".statemachine/codd/codd.py"
        return subprocess.run([sys.executable, "-c", _REPORT_BOOT, compiled(script), str(script)],
                              cwd=repo, capture_output=True, text=True, env=env)

    def use_graphify_stub(self) -> None:
        stub = self.bin / "graphify"
        stub.write_text(GRAPHIFY_STUB.format(log=self.log, fixtures=self.tmp / "graphs"), encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    def calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.is_file() else []

    def write_plan(self, text: str, read: bool = True) -> None:
        (self.impl / ".plans").mkdir(parents=True, exist_ok=True)
        (self.impl / PLAN).write_text(text, encoding="utf-8")
        if read:
            self.read_up(self.impl)

    def read_up(self, repo: Path, *terms: str) -> None:
        """計画の前にアクションがすること: 参照先を探し、守る決まりを読み込む。"""
        args = [a for t in (terms or ("hello",)) for a in ("--term", t)]
        r = self.run_pa(repo, "explore", *args)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.run_pa(repo, "guide", "--all")

    def assert_plan_ok(self, repo: Path | None = None, ok: bool = True) -> None:
        """計画の検査を通し、確認で利用者が OK と答えたことを控える（ok=False なら控えない）。"""
        r = self.run_pa(repo or self.impl, "verify-plan")
        self.assertEqual(r.returncode, 0, r.stderr)
        if ok:
            self.assertEqual(self.run_pa(repo or self.impl, "decide", "OK").returncode, 0)

    def set_check(self, repo: Path, command: list[str], key: str = "check") -> None:
        path = repo / ".statemachine/codd/codd.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg[key] = command
        path.write_text(json.dumps(cfg), encoding="utf-8")

    # ------------------------------------------------------------ 探す

    def test_explore_without_graphify_uses_grep(self) -> None:
        r = self.run_pa(self.impl, "explore", "--term", "hello")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("FOUND 1 files (graphify: not-installed)", r.stdout)
        report = (self.impl / ".codd/explore.md").read_text(encoding="utf-8")
        self.assertIn("docs/api.md:3:## hello", report)
        self.assertIn("- (context-mode が無いので引いていない。`install.py` で入れる)", report)
        self.assertIn("全文検索: context-mode が無いので引いていない", r.stdout)   # 必ず入れるもの。壊れたまま気づかれないように
        self.assertEqual(self.run_pa(self.impl, "explore").returncode, 2)  # 語が無い

    def test_graphify_installed_in_home_but_not_on_path_is_found(self) -> None:
        # uv tool・pipx・pip --user で入れて PATH に足していない端末でも、ホームの置き場所から見つける。
        self.use_graphify_stub()
        home_bin = self.tmp / "home/.local/bin"
        home_bin.mkdir(parents=True)
        (self.bin / "graphify").rename(home_bin / "graphify")
        r = self.run_pa(self.impl, "explore", "--term", "hello")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("not-installed", r.stdout)
        self.assertTrue(any(" update . --force" in c for c in self.calls()), self.calls())

    def test_windows_pip_user_scripts_of_any_python_version_are_searched(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            scripts = Path(temp) / "Python/Python312/Scripts"
            scripts.mkdir(parents=True)
            with mock.patch.dict(os.environ, {"APPDATA": temp}):
                self.assertIn(str(scripts), install.user_bin_dirs())

    def test_installer_finds_graphify_in_home_and_tells_path_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home_bin = Path(temp) / ".local/bin"
            home_bin.mkdir(parents=True)
            exe = home_bin / "graphify"
            exe.write_text("#!/bin/sh\necho graphify 1.0\n", encoding="utf-8")
            exe.chmod(0o755)
            out = io.StringIO()
            with mock.patch.dict(os.environ, {"HOME": temp, "PATH": "/nonexistent"}), \
                 mock.patch.object(install.Path, "home", return_value=Path(temp)), redirect_stdout(out):
                self.assertEqual(install.find_tool("graphify"), str(exe))
                self.assertTrue(install.install_tool(install.GRAPHIFY, upgrade=False))
        self.assertIn("graphify 1.0", out.getvalue())
        self.assertIn("が PATH にありません", out.getvalue())

    def test_explore_uses_context_mode_cli_and_rebuilds_index_only_when_repo_changes(self) -> None:
        # MCP サーバは通さず CLI を直接呼ぶ。索引は自分の .codd/context-mode/ に置く。
        log = self.tmp / "context-mode.log"
        stub = self.bin / "context-mode"
        stub.write_text(CONTEXT_MODE_STUB.format(log=log), encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        commit(self.design, {"secret/notes.md": "hello\n"}, "outside the scope")
        cfg_path = self.impl / ".statemachine/codd/codd.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["refs"][0]["scope"] = ["docs"]
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "explore", "--term", "hello").returncode, 0)
        report = (self.impl / ".codd/explore.md").read_text(encoding="utf-8")
        self.assertIn("### 全文検索（context-mode）", report)
        self.assertIn("- docs/api.md — hello", report)
        self.assertNotIn("secret/notes.md", report)   # scope の外は捨てる
        calls = log.read_text(encoding="utf-8").splitlines()
        self.assertEqual([c.split()[0] for c in calls], ["index", "search"])
        self.assertTrue(all(c.split()[1].startswith(str(self.impl / ".codd/context-mode")) for c in calls))
        # 受け持つ範囲の文書だけを索引にする（マシンのファイルが検索の上位を埋めないように）。
        self.assertIn("--include docs/**", calls[0])
        self.assertIn("--exclude .statemachine/codd/**", calls[0])
        self.assertIn("--ext .adoc,", calls[0])
        self.run_pa(self.impl, "explore", "--term", "world")   # 中身が同じなら索引を作り直さない
        self.assertEqual([c.split()[0] for c in log.read_text(encoding="utf-8").splitlines()],
                         ["index", "search", "search"])
        commit(self.design, {"docs/new.md": "# new\n"}, "change")
        self.run_pa(self.impl, "explore", "--term", "world")
        self.assertEqual(log.read_text(encoding="utf-8").splitlines()[-2].split()[0], "index")

    def test_explore_keeps_three_lines_per_file_without_git_max_count(self) -> None:
        commit(self.design, {"docs/many.md": "".join(f"hello {i}\n" for i in range(6))}, "many")
        self.assertEqual(self.run_pa(self.impl, "explore", "--term", "hello").returncode, 0)
        report = (self.impl / ".codd/explore.md").read_text(encoding="utf-8")
        self.assertEqual(report.count("docs/many.md:"), 3)   # 1 ファイル 3 行までは保つ

    def test_explore_prints_only_this_runs_matches(self) -> None:
        # 2 回目の explore は、前の回で出したファイルを出し直さない（控えには残す）。
        commit(self.design, {"docs/other.md": "# other\n\nworld を返す。\n"}, "other")
        self.run_pa(self.impl, "explore", "--term", "hello")
        r = self.run_pa(self.impl, "explore", "--term", "world")
        self.assertIn("docs/other.md:3", r.stdout)
        self.assertNotIn("docs/api.md", r.stdout)
        log = json.loads((self.impl / ".codd/explore.json").read_text(encoding="utf-8"))
        self.assertIn("docs/api.md", next(iter(log.values()))["files"])

    def test_a_failing_git_grep_is_not_taken_as_no_match(self) -> None:
        # 古い git が知らないオプションを渡したときのように、git grep が 1 以外で落ちたら「該当なし」にしない。
        shim = self.bin / "git"
        shim.write_text('#!/bin/sh\nfor a in "$@"; do [ "$a" = grep ] && { echo "error: unknown option" >&2; exit 129; }; done\n'
                        f'exec {shutil.which("git")} "$@"\n', encoding="utf-8")
        shim.chmod(0o755)
        r = self.run_pa(self.impl, "explore", "--term", "hello")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("git grep が失敗しました（終了コード 129）", r.stderr)
        self.assertNotIn("該当なし", (self.impl / ".codd/explore.md").read_text(encoding="utf-8")
                         if (self.impl / ".codd/explore.md").is_file() else "")

    def test_graph_is_rebuilt_only_when_the_repo_changes(self) -> None:
        self.use_graphify_stub()
        r = self.run_pa(self.impl, "explore", "--term", "hello")
        self.assertIn("graphify: updated", r.stdout)
        self.assertIn("- docs/api.md", (self.impl / ".codd/explore.md").read_text(encoding="utf-8"))
        first = self.calls()
        self.assertEqual(first[0], f"{self.design.resolve()} update . --force")   # 参照先の中で作り、
        self.assertTrue((self.impl / ".codd/graph/ref-design/graph.json").is_file())  # 自分の側に置く
        self.assertFalse((self.design / "graphify-out").exists())
        self.assertIn("query hello --graph", first[1])
        manifest = self.impl / ".codd/graph/ref-design/manifest.json"
        self.assertEqual(manifest.read_text(encoding="utf-8").strip(), "query")

        r = self.run_pa(self.impl, "explore", "--term", "hello")
        self.assertIn("graphify: fresh", r.stdout)
        self.assertFalse(any(" update " in c for c in self.calls()[len(first):]))
        self.assertFalse((self.design / "graphify-out").exists())
        self.assertEqual(manifest.read_text(encoding="utf-8").strip(), "query")

        # 参照先が変わったら（コミットでも、作業中の変更でも）作り直す。
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\n変えた。\n\n実装は `src/app.py`（`src/` の下）。\n", encoding="utf-8")
        self.assertIn("graphify: updated", self.run_pa(self.impl, "explore", "--term", "hello").stdout)
        commit(self.design, {"docs/new.md": "# new\n"}, "add")
        self.assertIn("graphify: updated", self.run_pa(self.impl, "explore", "--term", "hello").stdout)

    def test_impact_searches_own_repo_with_affected(self) -> None:
        self.use_graphify_stub()
        # まだコミットしていない呼び出し元も拾う（graphify の affected と git grep の両方）。
        (self.impl / "src/use.py").write_text("from app import hello\n\nhello()\n", encoding="utf-8")
        r = self.run_pa(self.impl, "impact", "--term", "hello")
        self.assertIn("FOUND 2 files (graphify: updated)", r.stdout)
        self.assertEqual(self.calls()[0], f"{self.impl.resolve()} update . --force")
        self.assertIn("affected hello --graph", self.calls()[1])
        self.assertEqual((self.impl / ".codd/graph/own/manifest.json").read_text(encoding="utf-8").strip(),
                         "affected")
        self.assertFalse((self.impl / "graphify-out").exists())
        report = (self.impl / ".codd/impact.md").read_text(encoding="utf-8")
        self.assertIn("- src/use.py:3:hello()", report)
        self.assertIn("## 候補のファイル\n\n- src/use.py\n- src/app.py", report)

    def test_searches_are_reused_while_the_repo_is_unchanged(self) -> None:
        self.use_graphify_stub()
        r = self.run_pa(self.impl, "impact", "--term", "hello")
        self.assertIn("graphify: updated", r.stdout)
        first = len(self.calls())
        # 計画を直して検査し直すたびに引き直さない（中身と語が同じなら前の結果を使う）。
        r = self.run_pa(self.impl, "impact", "--term", "hello")
        self.assertIn("FOUND", r.stdout)
        self.assertEqual(len(self.calls()), first)
        (self.impl / "src/use.py").write_text("from app import hello\n\nhello()\n", encoding="utf-8")
        r = self.run_pa(self.impl, "impact", "--term", "hello")   # 中身が変われば引き直す
        self.assertIn("src/use.py", (self.impl / ".codd/impact.md").read_text(encoding="utf-8"))
        self.assertGreater(len(self.calls()), first)

    def test_graphify_off(self) -> None:
        self.use_graphify_stub()
        path = self.impl / ".statemachine/codd/codd.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg["graphify"] = "off"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        self.assertIn("graphify: off", self.run_pa(self.impl, "explore", "--term", "hello").stdout)
        self.assertEqual(self.calls(), [])

    # ------------------------------------------------------------ グラフで影響を測る（必須・要判断・参考）

    def write_graphs(self) -> None:
        """graphify update のスタブが写すグラフ。自分: hello ← greet ← wrap（構文）と、文書から読み取ったつながり。
        参照先: docs/guide.md の用語が hello に関係する（推定 0.9）。どちらのファイルも hello とは書いていない。"""
        commit(self.impl, {"src/greet.py": "from app import hello\n\n\ndef greet():\n    return hello()\n",
                           "src/wrap.py": "from greet import greet\n\n\ndef wrap():\n    return greet()\n",
                           "README.txt": "挨拶の流れの説明。\n", "CHANGES.txt": "変更の記録。\n"}, "callers")
        commit(self.design, {"docs/guide.md": "# 手引き\n\n挨拶の手順。\n"}, "guide")
        sem = "codd-semantic"
        impl = {"nodes": [
            {"id": "app_hello", "label": "hello()", "source_file": "src/app.py", "source_location": "L1", "_origin": "ast"},
            {"id": "greet_greet", "label": "greet()", "source_file": "src/greet.py", "source_location": "L4", "_origin": "ast"},
            {"id": "wrap_wrap", "label": "wrap()", "source_file": "src/wrap.py", "source_location": "L4", "_origin": "ast"},
            {"id": f"{sem}:README.txt:flow", "label": "挨拶の流れ", "file_type": "concept", "source_file": "README.txt",
             "source_location": "L1", "_origin": sem},
            {"id": f"{sem}:CHANGES.txt:log", "label": "変更の記録", "file_type": "concept", "source_file": "CHANGES.txt",
             "source_location": "L1", "_origin": sem}],
            "links": [
            {"source": "greet_greet", "target": "app_hello", "relation": "calls", "confidence": "EXTRACTED",
             "source_file": "src/greet.py", "source_location": "L5", "_origin": "ast"},
            {"source": "wrap_wrap", "target": "greet_greet", "relation": "calls", "confidence": "EXTRACTED",
             "source_file": "src/wrap.py", "source_location": "L5", "_origin": "ast"},
            {"source": f"{sem}:README.txt:flow", "target": "app_hello", "relation": "conceptually_related_to",
             "confidence": "INFERRED", "confidence_score": 0.9, "source_file": "README.txt", "_origin": sem},
            {"source": f"{sem}:CHANGES.txt:log", "target": "app_hello", "relation": "conceptually_related_to",
             "confidence": "INFERRED", "confidence_score": 0.6, "source_file": "CHANGES.txt", "_origin": sem}]}
        design = {"nodes": [
            {"id": f"{sem}:docs/guide.md:ref:hello", "label": "hello", "source_file": "docs/guide.md", "_mention": True,
             "_origin": sem},
            {"id": f"{sem}:docs/guide.md:steps", "label": "挨拶の手順", "file_type": "concept",
             "source_file": "docs/guide.md", "source_location": "L3", "_origin": sem}],
            "links": [
            {"source": f"{sem}:docs/guide.md:steps", "target": f"{sem}:docs/guide.md:ref:hello",
             "relation": "conceptually_related_to", "confidence": "INFERRED", "confidence_score": 0.9,
             "source_file": "docs/guide.md", "source_location": "L3", "_origin": sem}]}
        (self.tmp / "graphs").mkdir(exist_ok=True)
        for name, graph in (("impl", impl), ("design", design)):
            (self.tmp / "graphs" / f"{name}.json").write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")

    def test_graph_hits_are_split_by_how_sure_they_are(self) -> None:
        self.use_graphify_stub()
        self.write_graphs()
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1, r.stdout)
        plan = (self.impl / PLAN).read_text(encoding="utf-8")
        # 必須: 構文で 2 歩先（wrap.py は hello と書いていない）
        self.assertIn("- src/wrap.py — 未判断（グラフで `hello` につながる（calls））", plan)
        # 要判断: 推定 0.9 で 1 歩。自分のファイルと、別のリポジトリの同じ名前の言及からたどった参照先の文書
        self.assertIn("- README.txt — 未判断（意味のつながり（推定 0.90・conceptually_related_to）: 「挨拶の流れ」と `hello`）",
                      plan)
        self.assertIn("未判断: design:docs/guide.md:3（意味のつながり（推定 0.90・conceptually_related_to）: 「挨拶の手順」と `hello`）",
                      plan)
        # 参考: 弱い推定は計画に出さず、測った結果のファイルにだけ残す
        self.assertNotIn("CHANGES.txt", plan)
        report = (self.impl / ".codd/impact.md").read_text(encoding="utf-8")
        self.assertIn("#### 参考\n\n- CHANGES.txt:1 — 参考（推定 0.60", report)

    def test_a_dismissed_guess_is_not_asked_again_until_the_file_changes(self) -> None:
        self.use_graphify_stub()
        self.write_graphs()
        self.write_plan(PLAN_ALIGNED)
        self.run_pa(self.impl, "verify-plan")
        judged = PLAN_ALIGNED.replace(
            "## 影響範囲\n\nなし\n",
            "## 影響範囲\n\n- src/greet.py, src/wrap.py — 変更不要: 戻り値を使うだけ\n- README.txt — 変更不要: 流れの説明だけ\n"
        ).replace("## 参照先のその他\n\nなし\n", "## 参照先のその他\n\n- 関係なし: docs/guide.md — 手順の説明だけ\n")
        self.write_plan(judged, read=False)
        self.assert_plan_ok()
        dismissed = json.loads((self.impl / ".codd/graph/dismissed.json").read_text(encoding="utf-8"))
        # 文字列の一致で当たった src/greet.py も控える（グラフでしか当たらない src/wrap.py は控えない）
        self.assertEqual(sorted(dismissed), ["design|docs/guide.md", "|README.txt", "|src/greet.py"])
        # 次の回（別の計画）: 同じ語から同じファイルに当たっても、変わっていなければ要判断に出さない（必須のものは出す）
        (self.impl / PLAN).unlink()
        again = self.impl / ".plans/2026-10-02-0000-again.md"
        again.write_text(PLAN_ALIGNED, encoding="utf-8")
        self.run_pa(self.impl, "verify-plan")
        plan = again.read_text(encoding="utf-8")
        self.assertIn("- src/wrap.py — 未判断", plan)
        self.assertIn("- src/greet.py — 未判断（グラフで", plan)   # 文字列の一致は判断済みでも、グラフの必須は訊く
        self.assertNotIn("README.txt", plan)
        self.assertNotIn("docs/guide.md", plan)
        self.assertIn("前の計画で判断済み", (self.impl / ".codd/impact.md").read_text(encoding="utf-8"))
        # ファイルが変われば、また訊く
        (self.impl / "README.txt").write_text("挨拶の流れの説明。書き足した。\n", encoding="utf-8")
        again.write_text(PLAN_ALIGNED, encoding="utf-8")
        self.run_pa(self.impl, "verify-plan")
        self.assertIn("- README.txt — 未判断（意味のつながり", again.read_text(encoding="utf-8"))

    def test_graphify_setting_must_be_auto_off_or_semantic(self) -> None:
        for value, word in (({"semantic": 1}, "semantic は session か"), ({"semantic": "session", "x": 1}, "知らない項目"),
                            ("yes", "auto か off")):
            self.set_config(self.impl, graphify=value)
            r = self.run_pa(self.impl, "show")
            self.assertEqual(r.returncode, 2)
            self.assertIn(word, r.stderr)

    def use_fake_graphify_python(self) -> None:
        """graphify の Python の部品の代わり（意味の抽出の段取りを確かめる。LLM は呼ばない）。"""
        pkg = self.tmp / "pylib" / "graphify"
        pkg.mkdir(parents=True)
        files = {
            "__init__.py": "",
            "detect.py": textwrap.dedent("""\
                import os
                from pathlib import Path
                def detect(root, **kw):
                    docs = []
                    for d, dirs, names in os.walk(root):
                        dirs[:] = [x for x in dirs if not x.startswith(".")]
                        docs += [str(Path(d) / n) for n in names if n.endswith((".md", ".txt", ".yaml"))]
                    return {"files": {"document": sorted(docs), "paper": []}}
                """),
            "cache.py": textwrap.dedent("""\
                import hashlib, json, os
                from pathlib import Path
                def _path():
                    return Path(os.environ["GRAPHIFY_OUT"]) / "cache" / "semantic.json"
                def _load():
                    try:
                        return json.loads(_path().read_text(encoding="utf-8"))
                    except OSError:
                        return {}
                def _rel(p, root):
                    return Path(p).resolve().relative_to(Path(root).resolve()).as_posix()
                def _hash(p):
                    return hashlib.sha256(Path(p).read_bytes()).hexdigest()
                def check_semantic_cache(files, root=".", prompt_file=None, **kw):
                    cache, nodes, edges, uncached = _load(), [], [], []
                    for f in files:
                        hit = cache.get(_rel(f, root))
                        if hit and hit["hash"] == _hash(f):
                            nodes += hit["nodes"]; edges += hit["edges"]
                        else:
                            uncached.append(f)
                    return nodes, edges, [], uncached
                def save_semantic_cache(nodes, edges, hyperedges=None, root=".", allowed_source_files=(), **kw):
                    cache = _load()
                    for f in allowed_source_files:
                        rel = _rel(f, root)
                        cache[rel] = {"hash": _hash(f), "nodes": [n for n in nodes if n["source_file"] == rel],
                                      "edges": [e for e in edges if e["source_file"] == rel]}
                    _path().parent.mkdir(parents=True, exist_ok=True)
                    _path().write_text(json.dumps(cache), encoding="utf-8")
                    return len(allowed_source_files)
                """),
            "build.py": "def build_from_json(ext, root=None, **kw):\n    return {'nodes': ext['nodes'], 'links': ext['edges']}\n",
            "cluster.py": "def cluster(G):\n    return {}\n",
            "export.py": textwrap.dedent("""\
                import json
                def to_json(G, communities, path, force=False, built_at_commit=None, **kw):
                    open(path, "w", encoding="utf-8").write(json.dumps(G))
                    return True
                """),
        }
        for name, body in files.items():
            (pkg / name).write_text(body, encoding="utf-8")
        (pkg / "skills/agents/references").mkdir(parents=True)
        (pkg / "skills/agents/references/extraction-spec.md").write_text("# spec\n", encoding="utf-8")
        patcher = mock.patch.dict(os.environ, {"PYTHONPATH": str(pkg.parent)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_graph_hands_documents_to_the_llm_in_chunks_and_merges_them(self) -> None:
        self.use_graphify_stub()
        self.use_fake_graphify_python()
        commit(self.design, {"docs/notes.txt": "挨拶は朝だけ。\n"}, "notes")
        self.set_config(self.impl, graphify={"semantic": "session"})
        # 計画の検査は、この回にグラフを用意したかを確かめる
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertIn("意味のグラフを、この回にまだ用意していません", r.stdout + r.stderr)

        r = self.run_pa(self.impl, "graph")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("PENDING 意味の抽出が要る文書が 2 files（1 束）"), r.stdout)
        self.assertIn("graph --chunk 束の名前", r.stdout)
        r = self.run_pa(self.impl, "graph", "--chunk", "ref-design-1")
        self.assertIn("- docs/api.md", r.stdout)
        self.assertIn("- docs/notes.txt", r.stdout)
        self.assertIn("extraction-spec.md", r.stdout)
        out = self.impl / ".codd/graph/ref-design/semantic/ref-design-1.json"
        self.assertIn(str(out), r.stdout)
        self.assertFalse((self.design / "graphify-out").exists())   # 参照先に何も書かない

        # 書かなかった・壊れた束は取り込まず、その束だけやり直させる
        r = self.run_pa(self.impl, "graph", "--merge")
        self.assertEqual(r.returncode, 1)
        self.assertIn("- ref-design-1: JSON がありません", r.stdout)
        out.write_text(json.dumps({"nodes": [
            {"id": "morning", "label": "朝の挨拶", "file_type": "concept", "source_file": "docs/notes.txt"},
            {"id": "h", "label": "hello", "file_type": "code", "source_file": ""},
            {"id": "elsewhere", "label": "よそ", "source_file": "docs/other.md"}],
            "edges": [{"source": "morning", "target": "h", "relation": "conceptually_related_to",
                       "confidence": "INFERRED", "confidence_score": 0.9, "source_file": "docs/notes.txt"}]},
            ensure_ascii=False), encoding="utf-8")
        r = self.run_pa(self.impl, "graph", "--merge")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("OK graph"), r.stdout)
        graph = json.loads((self.impl / ".codd/graph/ref-design/graph.json").read_text(encoding="utf-8"))
        ids = {n["id"] for n in graph["nodes"]}
        # 渡したファイルのノードだけを残し、よその名前は「言及」のノードにして、別のリポジトリの同じ名前とつなぐ
        self.assertIn("codd-semantic:docs/notes.txt:morning", ids)
        self.assertIn("codd-semantic:docs/notes.txt:ref:hello", ids)
        self.assertNotIn("codd-semantic:docs/other.md:elsewhere", ids)
        # 2 回目は、変わっていない文書を抜き出し直さない
        self.assertTrue(self.run_pa(self.impl, "graph").stdout.startswith("OK graph"))

        r = self.run_pa(self.impl, "verify-plan")
        self.assertNotIn("意味のグラフを", r.stdout + r.stderr)
        self.assertIn("未判断: design:docs/notes.txt（意味のつながり（推定 0.90", (self.impl / PLAN).read_text(encoding="utf-8"))

    # ------------------------------------------------------------ 計画の検査

    def test_verify_plan_accepts_aligned_and_drift_plans(self) -> None:
        self.assertEqual(self.run_pa(self.impl, "verify-plan").returncode, 1)  # まだ無い
        tpl = (self.impl / ".statemachine/codd/templates/plan.md").read_text(encoding="utf-8")
        self.write_plan(tpl)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("見出しの中身が空です", r.stderr)
        # 実装を参照先に合わせる直しは、ずれがあっても参照先の変更案が「なし」でよい
        align_impl = PLAN_DRIFT.replace("- docs/api.md — `hello` の戻り値を 2 と書き直す", "なし")
        for plan in (PLAN_ALIGNED, PLAN_DRIFT, align_impl):
            self.write_plan(plan)
            r = self.run_pa(self.impl, "verify-plan")
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_verify_plan_rejects_inconsistent_plans(self) -> None:
        cases = {
            "実在する根拠のパスがありません": PLAN_ALIGNED.replace("docs/api.md#hello", "docs/nowhere.md"),
            "ずれがあるのに、自分の変更案も参照先の変更案も「なし」": PLAN_DRIFT.replace(
                "- docs/api.md — `hello` の戻り値を 2 と書き直す", "なし").replace(
                "- src/app.py — `hello` が 2 を返す", "なし"),
            # 自分も変えるなら影響は測って書き足すので、自分の変更案が「なし」のときだけ求める
            "影響範囲が「なし」": PLAN_DRIFT.replace("- src/app.py — hello の戻り値", "なし").replace(
                "- src/app.py — `hello` が 2 を返す", "なし"),
            "自分のリポジトリに実在するパスがありません": PLAN_DRIFT.replace(
                "- src/app.py — hello の戻り値", "- src/gone.py — 戻り値"),
            "`…` で囲んでください": PLAN_DRIFT.replace("`hello`", "hello"),
            "見出しの順番": PLAN_ALIGNED.replace("## 参照先の前提", "## tmp").replace(
                "## 参照先の制約", "## 参照先の前提").replace("## tmp", "## 参照先の制約"),
        }
        for expected, plan in cases.items():
            with self.subTest(expected=expected):
                self.write_plan(plan)
                r = self.run_pa(self.impl, "verify-plan")
                self.assertEqual(r.returncode, 1)
                self.assertIn(expected, r.stderr)

    def test_template_comments_are_read_as_none(self) -> None:
        # ひな形のコメントのままの見出しは「なし」と読む（「なし」と書かせるだけの往復をしない）。
        # やりたいこと・自分の変更案・テストの変更案は、書き忘れと取り違えないよう必ず書かせる。
        tpl = (self.impl / ".statemachine/codd/templates/plan.md").read_text(encoding="utf-8")
        filled = PLAN_ALIGNED.split("## 自分の変更案")[1].split("## 参照先の変更案")[0].strip()
        plan = (tpl.replace("## やりたいこと\n", "## やりたいこと\n\nhello にログを足す。\n", 1)
                .replace("## 自分の変更案\n", f"## 自分の変更案\n\n{filled}\n", 1)
                .replace("## テストの変更案\n", "## テストの変更案\n\n- 変更不要: テストはまだ無い\n", 1))
        self.write_plan(plan)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertNotIn("見出しの中身が空です", r.stdout + r.stderr)
        self.assertNotIn("見出しの中身が空です",
                         self.run_pa(self.impl, "verify-plan").stderr)
        self.write_plan(tpl.replace("## やりたいこと\n", "## やりたいこと\n\nhello にログを足す。\n", 1))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertIn("見出しの中身が空です: ## 自分の変更案", r.stderr)
        self.assertIn("見出しの中身が空です: ## テストの変更案", r.stderr)
        self.assertNotIn("見出しの中身が空です: ## 参照先の制約", r.stderr)

    def test_ref_change_without_drift_is_allowed(self) -> None:
        # やりたいことが参照先も変えるなら、ずれが無くても参照先の変更案を書いてよい（無いずれを作らせない）。
        self.write_plan(PLAN_ALIGNED.replace(
            "## 参照先の変更案\n\nなし", "## 参照先の変更案\n\n- docs/api.md — `hello` がログを出すと書き足す"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertNotIn("ずれが「なし」なのに", r.stdout + r.stderr)

    # ------------------------------------------------------------ 変えたあとの検査

    def test_verify_apply_reuses_the_graph_built_before_the_change(self) -> None:
        # 変えたあとの検査は、変える前のグラフを使う。やり直すたびに全体を作り直して待たせない。
        self.use_graphify_stub()
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        self.assertIn(f"{self.impl.resolve()} update . --force", self.calls())
        before = len(self.calls())
        (self.impl / "src/app.py").write_text("def hello():\n    print('hi')\n    return 1\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        after = self.calls()[before:]
        self.assertFalse(any(c.startswith(f"{self.impl.resolve()} update") for c in after), after)
        # グラフは graphify を呼ばずに、変える前の graph.json を読んで辿る
        self.assertIn("### グラフ（根拠の強さごと）", (self.impl / ".codd/impact-after.md").read_text(encoding="utf-8"))

    def test_without_a_test_command_show_and_report_say_tests_did_not_run(self) -> None:
        # test が無いと検査はテストを動かさずに通る。「通った」をテストまで通ったと読ませない。
        self.assertIn("テストのコマンド: 未設定", self.run_pa(self.impl, "show").stdout)
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('hi')\n    return 1\n", encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 0)
        self.assertIn("- テスト: 動かしていない（自分のテストのコマンドが未設定）", self.report(self.impl).stdout)
        self.set_config(self.impl, test=[sys.executable, "-c", "pass"])
        self.assertNotIn("テストのコマンド: 未設定", self.run_pa(self.impl, "show").stdout)
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 0)
        self.assertNotIn("動かしていない", self.report(self.impl).stdout)

    def test_init_suggests_a_test_command_without_writing_it(self) -> None:
        repo = self.tmp / "guess"
        repo.mkdir()
        self.assertIsNone(init.guess_test(repo))
        (repo / "tests").mkdir()
        (repo / "tests/test_a.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")
        self.assertEqual(init.guess_test(repo), "python -m pytest")
        (repo / "tests/test_a.py").write_text("import unittest\n", encoding="utf-8")
        self.assertEqual(init.guess_test(repo), "python -m unittest")
        (repo / "go.mod").write_text("module x\n", encoding="utf-8")
        self.assertEqual(init.guess_test(repo), "go test ./...")
        (repo / "package.json").write_text('{"scripts": {"test": "node --test"}}', encoding="utf-8")
        self.assertEqual(init.guess_test(repo), "npm test")
        git(repo, "init", "-q")
        out = io.StringIO()
        with redirect_stdout(out):
            init.main([str(repo), "--side", "impl", "--ref", "../design", "--no-agents"])
        self.assertIn('設定するなら --test "npm test"', out.getvalue())
        self.assertNotIn("test", json.loads((repo / ".statemachine/codd/codd.json").read_text(encoding="utf-8")))

    def test_init_suggests_protecting_test_settings_without_writing_them(self) -> None:
        repo = self.tmp / "guess"
        (repo / "node_modules/x").mkdir(parents=True)
        (repo / "node_modules/x/conftest.py").write_text("\n", encoding="utf-8")
        self.assertEqual(init.guess_protect(repo), [])   # 依存の置き場の中は候補にしない
        (repo / "tests/unit").mkdir(parents=True)
        (repo / ".github/workflows").mkdir(parents=True)
        for rel in ("tests/unit/conftest.py", "jest.config.ts", "package.json", "requirements-dev.txt"):
            (repo / rel).write_text("\n", encoding="utf-8")
        self.assertEqual(init.guess_protect(repo), ["**/conftest.py", "jest.config.*", ".github/workflows/", "package.json",
                                                    "requirements*.txt"])
        git(repo, "init", "-q")
        out = io.StringIO()
        with redirect_stdout(out):
            init.main([str(repo), "--side", "impl", "--ref", "../design", "--no-agents"])
        self.assertIn('"protect": ["**/conftest.py", "jest.config.*", ".github/workflows/"', out.getvalue())
        self.assertNotIn("protect", json.loads((repo / ".statemachine/codd/codd.json").read_text(encoding="utf-8")))

    def test_verify_apply_own_only(self) -> None:
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("自分のリポジトリが変わっていません", r.stderr)

        (self.impl / "src/app.py").write_text("def hello():\n    print('hi')\n    return 1\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("own=changed refs=none", r.stdout)

        # 変更案が「なし」なのに参照先を変えたら落とす。
        (self.design / "docs/api.md").write_text("勝手に変えた\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("参照先の変更案に design は無いのに、design が変わっています", r.stderr)

    def test_verify_apply_both_and_runs_each_check(self) -> None:
        self.write_plan(PLAN_DRIFT)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("design を変えるはずなのに、design が変わっていません", r.stderr)

        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。\n\n実装は `src/app.py`（`src/` の下）。\n", encoding="utf-8")
        # 各リポジトリの検査は、それぞれの codd.json の check。
        self.set_check(self.impl, [sys.executable, "-c", "import sys; sys.exit('return 2' not in open('src/app.py').read())"])
        self.set_check(self.design, [sys.executable, "-c", "import sys; sys.exit('3 を返す' not in open('docs/api.md').read())"])
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("design（設計書）の検査が失敗しました", r.stderr)
        self.assertNotIn("実装の検査が失敗しました", r.stderr)

        self.set_check(self.design, [sys.executable, "-c", "import sys; sys.exit('2 を返す' not in open('docs/api.md').read())"])
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("own=changed refs=design", r.stdout)

    def test_files_the_checks_regenerate_are_not_changes(self) -> None:
        # 検査コマンド（図を作り直すなど）が作り直したファイルは、エージェントの変更に数えない。
        # 数えると、検査を通し直すたびに「計画に無いファイル」で止まる。
        commit(self.design, {"docs/images/hello.png": "old\n"}, "image")
        shot = ("import pathlib, time; p = pathlib.Path('docs/images'); p.mkdir(parents=True, exist_ok=True); "
                "(p / 'hello.png').write_text(str(time.time())); (p / 'new.png').write_text(str(time.time()))")
        self.set_check(self.design, [sys.executable, "-c", shot])
        self.set_check(self.impl, [sys.executable, "-c", "open('report.txt', 'w').write('ok')"])
        self.write_plan(PLAN_DRIFT)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。\n\n実装は `src/app.py`（`src/` の下）。\n", encoding="utf-8")
        for _ in range(2):   # 2 回目は、1 回目の検査が作り直したファイルがある
            r = self.run_pa(self.impl, "verify-apply")
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.impl / "report.txt").is_file())

        # エージェントが同じファイルを手で変えたら、変更に数える。
        (self.impl / "report.txt").write_text("edited by hand", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("計画に無いファイルを変えています", r.stderr)
        self.assertIn("report.txt", r.stderr)

    def test_planned_binaries_may_come_out_the_same(self) -> None:
        # 作り直すと計画に挙げたバイナリが、同じバイト列のままでも、変え残しとして止めない。
        (self.design / "docs/images").mkdir(parents=True, exist_ok=True)
        (self.design / "docs/images/fig.bin").write_bytes(b"BIN\0same")
        (self.design / "docs/spec.bin").write_bytes(b"BIN\0spec")
        commit(self.design, {"docs/text.txt": "same\n", "docs/figures.md": "# 図\n\n![図](images/fig.bin)\n"}, "binary")
        plan = PLAN_DRIFT.replace("- docs/api.md — `hello` の戻り値を 2 と書き直す",
                                  "- docs/api.md — `hello` の戻り値を 2 と書き直す\n- docs/images/fig.bin — 作り直す")
        self.write_plan(plan)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。\n\n実装は `src/app.py`（`src/` の下）。\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("docs/images/fig.bin — 作り直しても同じ", self.report(self.impl).stdout)
        # 文字のファイルは、変えないなら「変更不要」と書かせる（作り直しで同じになったとはみなさない）
        self.write_plan(plan.replace("docs/images/fig.bin — 作り直す", "docs/text.txt — 作り直す"))
        self.assert_plan_ok()
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("docs/text.txt", r.stderr)
        # 文書が貼っていない単独のバイナリ（表計算のファイルなど）も、変えないなら「変更不要」と書かせる
        self.write_plan(plan.replace("docs/images/fig.bin — 作り直す", "docs/spec.bin — 表を直す"))
        self.assert_plan_ok()
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("docs/spec.bin", r.stderr)

    # ------------------------------------------------------------ 影響範囲を測る

    def add_caller(self) -> None:
        commit(self.impl, {"src/use.py": "from app import hello\n\nprint(hello())\n",
                           "src/other.py": "def helloWorld():\n    return 0\n"}, "caller")

    def test_verify_plan_measures_impact_of_ref_change(self) -> None:
        self.add_caller()
        self.write_plan(PLAN_DRIFT)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("「未判断」として書き足しました", r.stderr)
        self.assertIn("src/use.py", r.stderr)
        self.assertNotIn("src/other.py", r.stderr)  # 語単位で引くので helloWorld は拾わない
        self.assertFalse((self.impl / ".codd/issues.json").exists())   # 未判断の書き足しは振り返りの出来事に数えない
        report = (self.impl / ".codd/impact.md").read_text(encoding="utf-8")
        self.assertIn("- src/use.py", report)
        # 測ったファイルは計画に「未判断」として書き足される。判断しないままでは通らない。
        self.assertIn("- src/use.py — 未判断（変わる名前が出てくる）", (self.impl / PLAN).read_text(encoding="utf-8"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("「未判断」の項目が 1 件残っています", r.stderr)

        self.write_plan(PLAN_DRIFT.replace("- src/app.py — hello の戻り値",
                                           "- src/app.py — hello の戻り値\n- src/use.py — 変更不要: 表示するだけ"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("影響範囲を測った: 2 files", r.stdout)

    def as_previous_plan(self, repo) -> None:
        """控えた判断を、前の計画が下したものにする（この計画の判断は計画そのものにあり、使い回さない）。"""
        path = repo / ".codd/graph/dismissed.json"
        rec = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({k: {**v, "plan": ".plans/old.md"} for k, v in rec.items()}), encoding="utf-8")

    def test_verify_plan_measures_impact_of_own_change_too(self) -> None:
        # 参照先を変えない計画でも、自分の変更で動く名前の呼び出し元を漏れなく扱わせる。
        self.add_caller()
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("「未判断」として書き足しました", r.stderr)
        self.assertIn("src/use.py", r.stderr)
        # 「なし」だった見出しは、書き足した項目に置き換わる。エージェントは印を判断に書き換えるだけでよい。
        plan = (self.impl / PLAN).read_text(encoding="utf-8")
        self.assertIn("## 影響範囲\n\n- src/use.py — 未判断（変わる名前が出てくる）\n\n## ", plan)
        (self.impl / PLAN).write_text(plan.replace("未判断（変わる名前が出てくる）", "変更不要: 戻り値は同じ"),
                                      encoding="utf-8")
        self.assert_plan_ok()
        self.as_previous_plan(self.impl)
        self.write_plan(PLAN_ALIGNED.replace("## 影響範囲\n\nなし", "## 影響範囲\n\n- src/use.py — 変更不要: 戻り値は同じ"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 0, r.stderr)
        # 前の計画で「変更不要」とし、変わっていない src/use.py は測った結果に入れない（同じ判断を求めない）
        self.assertIn("影響範囲を測った: 1 files", r.stdout)
        self.assertIn("前の判断を使い回した: 1 files", r.stdout)
        self.assertIn("参照先で触れている: 1 files", r.stdout)

    def test_many_hits_are_all_kept_and_too_many_stop_the_plan(self) -> None:
        # 1 語 20 行で黙って打ち切らない。20 を超える呼び出し元も、すべて「未判断」として書き足す
        commit(self.impl, {f"src/use{i:02}.py": "from app import hello\n" for i in range(25)}, "callers")
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        plan = (self.impl / PLAN).read_text(encoding="utf-8")
        self.assertEqual(plan.count("— 未判断"), 25, plan)
        # 判断の上限を超えたら、捨てずに止め、どの語が多いかと次の手を示す（計画には書き足さない）
        commit(self.impl, {f"src/more{i:02}.py": "from app import hello\n" for i in range(20)}, "more callers")
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("上限 40 行を超えています（`hello` が 46 ファイル）", r.stderr)
        self.assertIn("名前を具体的にするか、計画を分けて", r.stderr)
        self.assertNotIn("未判断", (self.impl / PLAN).read_text(encoding="utf-8"))

    def test_documents_that_only_mention_a_name_are_judged_together(self) -> None:
        # 名前に触れているだけの文書は、フォルダごとに 1 行にまとめて判断させる（1 件ずつ判断させない）
        commit(self.impl, {f"notes/n{i}.md": f"# メモ {i}\n\n`hello` を呼ぶ画面。\n" for i in range(3)}, "notes")
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        plan = (self.impl / PLAN).read_text(encoding="utf-8")
        self.assertIn("- notes/n0.md, notes/n1.md, notes/n2.md — 未判断（名前に触れているだけの文書 3 件", plan)
        self.assertIn("書き足しました。", r.stderr)
        # 判断も 1 行で書ける。変えたあとの検査も、その 1 行を変更不要として読む
        self.write_plan(PLAN_ALIGNED.replace(
            "## 影響範囲\n\nなし", "## 影響範囲\n\n- notes/n0.md, notes/n1.md, notes/n2.md — 変更不要: 名前に触れているだけ"))
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 1  # log\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        # 回の手間を記録に残す（案 7）
        self.assertIn("- 手間: 計画の検査 2 回・書き足した未判断 1 行・変えたあとの検査 1 回", self.report(self.impl).stdout)

    def test_reference_documents_that_only_mention_a_name_are_judged_together(self) -> None:
        # 参照先の、名前に触れているだけの文書も 1 行にまとめる。「関係なし」も 1 行で書け、次の回は訊かない
        commit(self.design, {f"docs/screens/s{i}.md": f"# 画面 {i}\n\n`hello` を呼ぶ画面。\n" for i in range(3)}, "screens")
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        group = "design:docs/screens/s0.md, design:docs/screens/s1.md, design:docs/screens/s2.md"
        self.assertIn(f"- 未判断: {group}（名前に触れているだけの文書 3 件", (self.impl / PLAN).read_text(encoding="utf-8"))
        self.write_plan(PLAN_ALIGNED.replace("## 参照先のその他\n\nなし\n", f"## 参照先のその他\n\n- 関係なし: {group} — 画面名だけ\n"))
        self.assert_plan_ok()
        self.write_plan(PLAN_ALIGNED)   # 同じ計画から判断を消せば、また訊く
        self.assertEqual(self.run_pa(self.impl, "verify-plan").returncode, 1)
        self.as_previous_plan(self.impl)
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertNotIn("docs/screens/", (self.impl / PLAN).read_text(encoding="utf-8"))
        self.assertIn("前の判断を使い回した", r.stdout + r.stderr)

    def test_names_over_the_limit_are_reported_not_dropped(self) -> None:
        names = " ".join(f"`name{i:02}`" for i in range(14))
        self.write_plan(PLAN_ALIGNED.replace("`hello` の中でログを出す", f"`hello` の中でログを出す（{names}）"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("次の名前は測っていません: name11, name12, name13", r.stderr)
        r = self.run_pa(self.impl, "explore", *[a for i in range(13) for a in ("--term", f"w{i}")])
        self.assertEqual(r.returncode, 2)
        self.assertIn("残りは explore をもう一度実行して探してください: w12", r.stderr)

    def test_elided_names_in_the_plan_are_measured_or_rewritten(self) -> None:
        # `…hello…` と写しても hello で測る（影響が 0 件のまま承認まで進まない）
        self.add_caller()
        self.write_plan(PLAN_ALIGNED.replace("`hello`", "`…hello…`"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("src/use.py — 未判断", (self.impl / PLAN).read_text(encoding="utf-8"))
        self.write_plan(PLAN_ALIGNED.replace("`hello`", "`he…lo`"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("名前を省略の記号（…）で縮めずに", r.stderr)
        self.assertIn("he…lo", r.stderr)

    def test_verify_plan_finds_ref_files_the_own_change_touches(self) -> None:
        # 自分の変更で動く名前に触れている参照先のファイルを読まずに計画したら落とす（逆向きの漏れ）。
        commit(self.design, {"docs/guide.md": "# 使い方\n\n`hello` を呼ぶ。\n"}, "guide")
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("「未判断」として書き足しました", r.stderr)
        self.assertIn("docs/guide.md", r.stderr)
        self.assertNotIn("docs/api.md", r.stderr)  # 根拠に挙げたものは扱った
        # 一致した行を添えて書き足す（その前後だけを読めば判断できる）
        self.assertIn("- 未判断: design:docs/guide.md:3（自分の変更で変わる名前が出てくる） 「hello を呼ぶ。」",
                      (self.impl / PLAN).read_text(encoding="utf-8"))
        self.assertIn("docs/guide.md", (self.impl / ".codd/ref-impact.md").read_text(encoding="utf-8"))
        self.write_plan(PLAN_ALIGNED.replace("## 参照先のその他\n\nなし",
                                             "## 参照先のその他\n\n- 関係なし: 呼び方の例だけ（根拠: docs/guide.md）"))
        self.assert_plan_ok()

    def test_verify_apply_remeasures_from_the_actual_ref_change(self) -> None:
        self.add_caller()
        commit(self.impl, {"src/bye.py": "def goodbye_world():\n    return 3\n"}, "bye")
        plan = PLAN_DRIFT.replace("- src/app.py — hello の戻り値",
                                  "- src/app.py — hello の戻り値\n- src/use.py — 表示を直す")
        self.write_plan(plan)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        # 参照先は計画に無い見出しまで足した。実際の差分から測るので、その影響も拾う。
        (self.design / "docs/api.md").write_text(
            "# API\n\n## hello\n\nhello は 2 を返す。\n\n## goodbye_world\n\n3 を返す。\n\n実装は `src/app.py`（`src/` の下）。\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("直していないファイル", r.stderr)
        self.assertIn("src/use.py", r.stderr)   # 影響範囲に挙げたが直していない
        self.assertIn("src/bye.py", r.stderr)   # 計画に無かった変更の影響
        self.assertNotIn("src/app.py,", r.stderr)
        self.assertTrue((self.impl / ".codd/impact-after.md").is_file())

        (self.impl / "src/use.py").write_text("from app import hello\n\nprint('v', hello())\n", encoding="utf-8")
        # 「変更不要」とするのは計画を直すこと。変えた分を残して、計画の検査を通し直してから確かめる。
        self.write_plan(plan.replace("- src/use.py — 表示を直す", "- src/use.py — 表示を直す\n- src/bye.py — 変更不要: 名前だけ同じ別物"))
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("利用者が確かめたものではありません", r.stderr)
        self.assertIn("変えた分は残して、計画を直す", self.run_pa(self.impl, "advise").stdout)
        self.assert_plan_ok()
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("impact=3 files", r.stdout)

    def test_protected_files_change_only_with_the_users_ok(self) -> None:
        # 参照先が決めた「人の承認が要るファイル」は、確認で OK と答えた計画に挙げたときだけ変えてよい。
        path = self.design / ".statemachine/codd/codd.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg["protect"] = ["docs/api.md"]
        path.write_text(json.dumps(cfg), encoding="utf-8")
        self.set_config(self.impl, protect=["src/legacy/"])
        commit(self.impl, {"src/legacy/old.py": "OLD = 1\n"}, "legacy")
        self.assertIn("人の承認が要るファイル", self.run_pa(self.impl, "show").stdout)
        self.assertIn("src/legacy/, design:docs/api.md", self.run_pa(self.impl, "show").stdout)
        self.write_plan(PLAN_DRIFT)
        self.assert_plan_ok(ok=False)
        r = self.run_pa(self.impl, "summary")
        self.assertIn("## 人の承認が要るファイル\n\n- design:docs/api.md", r.stdout)
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。\n\n実装は `src/app.py`（`src/` の下）。\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")   # 確認で OK と答えていない
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"人の承認が要るファイルを、承認を得ずに変えています（戻してください。変えるなら計画に挙げ、"
                      f"確認で利用者の承認を得てから）: design:docs/api.md", r.stderr)
        advice = self.run_pa(self.impl, "advise").stdout
        self.assertNotIn("AUTO", advice)   # 訊かずにやり直させない
        self.assertIn("計画に挙げて確認で承認する", advice)
        self.run_pa(self.impl, "decide", "OK")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        log = json.loads((self.impl / ".codd/decisions.json").read_text(encoding="utf-8"))
        self.assertEqual(log[-1]["note"], "承認したファイル: design:docs/api.md")
        # 計画に無い人の承認が要るファイルは、「追加」と申告しても変えさせない。
        (self.impl / "src/legacy/old.py").write_text("OLD = 2\n", encoding="utf-8")
        (self.impl / ".codd/apply.md").write_text("## 計画との違い\n\n- src/legacy/old.py — 追加: ついでに直す\n",
                                                  encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("承認を得ずに変えています（戻してください。変えるなら計画に挙げ、確認で利用者の承認を得てから）: "
                      "src/legacy/old.py", r.stderr)
        # 計画を退けたら、承認も消える。
        (self.impl / "src/legacy/old.py").write_text("OLD = 1\n", encoding="utf-8")
        self.run_pa(self.impl, "decide", "NG", "--note", "やり直して")
        self.assertFalse((self.impl / ".codd/approved-plan").exists())

    def test_protect_takes_globs(self) -> None:
        # protect は exclude と同じ書き方（`*` はフォルダをまたがず、`**/` はまたぐ）。
        path = self.design / ".statemachine/codd/codd.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg["protect"] = ["docs/*.md"]
        path.write_text(json.dumps(cfg), encoding="utf-8")
        self.set_config(self.impl, protect=["src/**/legacy_*.py"])
        commit(self.impl, {"src/v1/legacy_orders.py": "OLD = 1\n", "src/v1/orders.py": "NEW = 1\n"}, "legacy")
        self.write_plan(PLAN_DRIFT)
        self.assert_plan_ok()
        self.assertIn("## 人の承認が要るファイル\n\n- design:docs/api.md", self.run_pa(self.impl, "summary").stdout)
        self.run_pa(self.impl, "decide", "OK")
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。\n\n実装は `src/app.py`（`src/` の下）。\n", encoding="utf-8")
        (self.impl / "src/v1/legacy_orders.py").write_text("OLD = 2\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("承認を得ずに変えています", r.stderr)
        self.assertIn("src/v1/legacy_orders.py", r.stderr)
        self.assertNotIn("design:docs/api.md", r.stderr.split("承認を得ずに変えています", 1)[1].split("\n", 1)[0])

    def test_a_protected_file_that_breaks_is_asked_not_retried(self) -> None:
        # 人の承認が要るファイルを「変更不要」として変え、テストがそのファイルで落ちたら、訊かずに変え直させない
        # （変え直すと、そのファイルを直して次の検査で止まるだけになる）。計画の段で、未判断の行にもそう添える。
        commit(self.impl, {"src/legacy.py": "from src.app import hello\n\nV1 = hello()\n"}, "legacy")
        self.set_config(self.impl, protect=["src/legacy.py"])
        failing = (f"import sys; print('  File \"{self.impl}/src/legacy.py\", line 3'); "
                   "print('NameError: hello'); sys.exit(1)")
        self.set_config(self.impl, test=[sys.executable, "-c", failing])
        self.write_plan(PLAN_ALIGNED)
        self.run_pa(self.impl, "verify-plan")
        plan = (self.impl / PLAN).read_text(encoding="utf-8")
        self.assertIn("- src/legacy.py — 未判断（変わる名前が出てくる。人の承認が要るファイル", plan)
        self.write_plan(plan.replace(next(ln for ln in plan.splitlines() if ln.startswith("- src/legacy.py")),
                                     "- src/legacy.py — 変更不要: 戻り値は変えない"))
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('hi')\n    return 1\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("人の承認が要るファイルがあります（テストか検査が、このファイルで落ちています", r.stderr)
        self.assertIn("src/legacy.py", r.stderr)
        self.assertNotIn("AUTO", self.run_pa(self.impl, "advise").stdout)

    def test_a_qualified_new_name_is_tested_by_its_last_part(self) -> None:
        # `status.COMPLETED` を足す計画は、テストの変更案に `COMPLETED` と書けば足りる。まだ無いファイルとも読まない。
        commit(self.impl, {"tests/__init__.py": ""}, "tests")
        plan = self.with_tests(PLAN_ALIGNED.replace("- src/app.py — `hello` の中でログを出す",
                                                    "- src/app.py — `hello` が `status.COMPLETED` を返す"),
                               "- tests/test_app.py — `hello` が `COMPLETED` を返すことを確かめる")
        self.write_plan(plan)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 0, r.stderr)
        (self.impl / "src/app.py").write_text("COMPLETED = 'COMPLETED'\n\n\ndef hello():\n    return COMPLETED\n",
                                              encoding="utf-8")
        (self.impl / "tests/test_app.py").write_text("from src.app import hello\n\nassert hello() == 'COMPLETED'\n",
                                                     encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertNotIn("status.COMPLETE", r.stderr)    # 変えていないファイルとして求めない

    def test_a_small_tail_joins_the_previous_batch(self) -> None:
        # 手順の無いファイルが少しだけ余ったら、1 ファイルのために段を増やさない。
        files = {f"src/m{i}.py": f"def m{i}():\n    return {i}\n" for i in range(5)}
        commit(self.impl, files, "modules")
        self.set_config(self.impl, batch_files=4)
        items = "\n".join(f"- {rel} — `m{i}` を直す" for i, rel in enumerate(files))
        self.write_plan(self.with_tests(PLAN_ALIGNED.replace("- src/app.py — `hello` の中でログを出す", items),
                                        "- 変更不要: 戻り値だけで、テストはまだ無い"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("段に分ける", r.stdout)    # 4 と 1 に分けない

    def test_an_unrelated_line_may_name_what_the_file_lacks(self) -> None:
        # 「関係なし」の理由は、根拠のファイルに無い今回の名前を挙げるのが自然。名前の照合で落とさない。
        self.write_plan(PLAN_ALIGNED.replace("## 参照先のその他\n\nなし",
                                             "## 参照先のその他\n\n- 関係なし: docs/api.md:3 — `hello` の説明で、`greet` には触れない"))
        self.assert_plan_ok()

    def test_apply_needs_the_plan_that_passed_and_was_not_rejected(self) -> None:
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        self.assertEqual(self.run_pa(self.impl, "decide", "NG", "--note", "やり直して").returncode, 0)
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")   # 止まったところから飛ばして来ても、退けた計画では通さない
        self.assertEqual(r.returncode, 1)
        self.assertIn("利用者が確かめたものではありません", r.stderr)
        self.assertEqual(self.run_pa(self.impl, "verify-plan").returncode, 0)
        self.assertNotIn("利用者が確かめたものではありません", self.run_pa(self.impl, "verify-apply").stderr)

    # ------------------------------------------------------------ パスのつながり

    def test_verify_plan_follows_path_links_both_ways(self) -> None:
        # 参照先の文書が自分の変更案のファイルをパスで指しているなら、名前が一致しなくても計画で扱わせる。
        commit(self.design, {"docs/map.md": "# 対応表\n\n実装は [app](../impl/src/app.py) と `src/app.py`。\n"
                                            "```\nsrc/app.py はコードブロックの中なので数えない\n```\n"}, "map")
        # 後半で使う。計画を通したあとにコミットすると、この回の変更に数える（途中でコミットしても取りこぼさない）。
        commit(self.impl, {"src/client.py": "# coherence: doc=docs/api.md\ndef call():\n    return 0\n"}, "client")
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("「未判断」として書き足しました", r.stderr)
        self.assertIn("docs/map.md", r.stderr)
        self.assertIn("- 未判断: design:docs/map.md（自分の変えるファイルとパスでつながっている）",
                      (self.impl / PLAN).read_text(encoding="utf-8"))
        self.assertIn("docs/map.md", (self.impl / ".codd/trace.md").read_text(encoding="utf-8"))
        self.write_plan(PLAN_ALIGNED.replace("## 参照先のその他\n\nなし",
                                             "## 参照先のその他\n\n- 関係なし: 置き場所の一覧だけ（根拠: docs/map.md）"))
        self.assert_plan_ok()

        # 自分のファイルに書いた注記で、参照先の変更案のファイルとつながる（逆向き）。
        plan = PLAN_DRIFT.replace("## 参照先のその他\n\n- なし",
                                  "## 参照先のその他\n\n- 関係なし: 置き場所の一覧だけ（根拠: docs/map.md）")
        self.write_plan(plan)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("- src/client.py — 未判断（参照先の変更案のファイルとパスでつながっている）",
                      (self.impl / PLAN).read_text(encoding="utf-8"))
        self.assertIn("src/client.py", r.stderr)
        plan = plan.replace("- src/app.py — hello の戻り値", "- src/app.py — hello の戻り値\n- src/client.py — 変更不要: 呼ぶだけ")
        self.write_plan(plan)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。\n\n実装は `src/app.py`（`src/` の下）。\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_judgments_on_path_links_are_reused_on_the_next_run(self) -> None:
        # パスでつながって判断したファイルも、前の計画で判断し、そのあと変わっていなければ次の回に訊かない
        commit(self.design, {"docs/map.md": "# 対応表\n\n実装は `src/app.py`。\n"}, "map")
        self.write_plan(PLAN_ALIGNED)
        self.assertEqual(self.run_pa(self.impl, "verify-plan").returncode, 1)
        self.assertIn("design:docs/map.md（自分の変えるファイルとパスでつながっている）", (self.impl / PLAN).read_text(encoding="utf-8"))
        self.write_plan(PLAN_ALIGNED.replace("## 参照先のその他\n\nなし",
                                             "## 参照先のその他\n\n- 関係なし: 置き場所の一覧だけ（根拠: docs/map.md）"))
        self.assert_plan_ok()
        self.as_previous_plan(self.impl)
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("docs/map.md", (self.impl / PLAN).read_text(encoding="utf-8"))
        self.assertIn("前の判断を使い回した: 1 files", r.stdout)
        self.run_pa(self.impl, "decide", "OK")
        (self.impl / "src/app.py").write_text("def hello():\n    return 1  # log\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")   # 変えたあとの検査も、使い回した判断で通す
        self.assertEqual(r.returncode, 0, r.stderr)
        # 文書が変われば、また訊く
        commit(self.design, {"docs/map.md": "# 対応表\n\n実装は `src/app.py`（入口）。\n"}, "map2")
        self.write_plan(PLAN_ALIGNED)
        self.assertEqual(self.run_pa(self.impl, "verify-plan").returncode, 1)

    def test_verify_apply_catches_broken_and_dangling_paths(self) -> None:
        commit(self.impl, {"src/old.py": "def old():\n    return 0\n"}, "old")
        commit(self.design, {"docs/map.md": "# 対応表\n\n- `src/old.py` は古い入口\n"}, "map")
        plan = (PLAN_DRIFT
                .replace("- src/app.py — `hello` が 2 を返す", "- src/app.py — `hello` が 2 を返す\n- src/old.py — `old` を消す")
                .replace("## 参照先のその他\n\n- なし", "## 参照先のその他\n\n- 古い入口の一覧（根拠: docs/map.md）"))
        self.write_plan(plan)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.impl / "src/old.py").unlink()
        (self.design / "docs/api.md").write_text(
            "# API\n\n## hello\n\nhello は 2 を返す。詳しくは [手順](steps.md)。\n\n\n実装は `src/app.py`（`src/` の下）。\n"
            "```\n例: `path/to/nothing.md`\n```\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("書き足したパスが、どのリポジトリにもありません", r.stderr)
        self.assertIn("docs/api.md:5 → steps.md", r.stderr)
        self.assertNotIn("nothing.md", r.stderr)          # コードブロックの中は数えない
        self.assertIn("消したファイルを、まだ指しているところがあります", r.stderr)
        self.assertIn("docs/map.md:3 → src/old.py", r.stderr)
        self.assertTrue((self.impl / ".codd/trace-after.md").is_file())

    def test_a_new_line_to_a_deleted_file_is_reported_once(self) -> None:
        # 書き足した行が消したファイルを指すときは、「消したファイルを、まだ指している」だけに挙げる。
        commit(self.impl, {"src/old.py": "def old():\n    return 0\n"}, "old")
        plan = PLAN_DRIFT.replace("- src/app.py — `hello` が 2 を返す",
                                  "- src/app.py — `hello` が 2 を返す\n- src/old.py — `old` を消す")
        self.write_plan(plan)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.impl / "src/old.py").unlink()
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。古い入口は `src/old.py`。\n\n実装は `src/app.py`（`src/` の下）。\n",
                                                 encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("消したファイルを、まだ指しているところがあります", r.stderr)
        self.assertNotIn("書き足したパスが、どのリポジトリにもありません", r.stderr)
        self.assertEqual(r.stderr.count("docs/api.md:5"), 1)

    def test_report_lists_changes_without_path_links(self) -> None:
        # コードは文書とつながっていないと通らない。コード以外（設定など）は、つながっていなければ報告で知らせる。
        # フォルダを書いた文書があれば、コード以外（マイグレーションなど）もつながっている
        commit(self.impl, {"settings.toml": "level = 1\n", "db/migrations/001.sql": "create table t (a int);\n"}, "settings")
        commit(self.design, {"docs/db.md": "# DB\n\n実装は `db/migrations/` に置く。\n"}, "db")
        plan = PLAN_ALIGNED.replace("- src/app.py — `hello` の中でログを出す",
                                    "- src/app.py — `hello` の中でログを出す\n- settings.toml — ログの水準を上げる\n"
                                    "- db/migrations/002.sql — ログの表を足す")
        self.write_plan(plan)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('hi')\n    return 1\n", encoding="utf-8")
        (self.impl / "settings.toml").write_text("level = 2\n", encoding="utf-8")
        (self.impl / "db/migrations/002.sql").write_text("create table logs (a int);\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.report(self.impl)
        self.assertIn("## 参照先とパスでつながっていない変更\n\n- settings.toml\n", r.stdout)
        self.assertNotIn("- src/app.py\n", r.stdout.split("## 参照先とパスでつながっていない変更", 1)[1])
        self.assertNotIn("002.sql", r.stdout.split("## 参照先とパスでつながっていない変更", 1)[1])

    def test_code_without_docs_must_get_one_like_its_neighbors(self) -> None:
        # コードには対応する文書が 1 つ以上ある前提。無ければ、近いコードの文書を見本に作る（足す）まで通さない。
        commit(self.impl, {"lib/pay.py": "def pay():\n    return 0\n"}, "pay")
        plan = PLAN_ALIGNED.replace("- src/app.py — `hello` の中でログを出す",
                                    "- src/app.py — `hello` の中でログを出す\n- lib/pay.py — `pay` にログを足す")
        self.write_plan(plan)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("対応する文書の無いコードです", r.stderr)
        self.assertIn("lib/pay.py（近い粒度の見本: design:docs/api.md（src/app.py に対応））", r.stderr)
        self.assertNotIn("src/app.py（近い", r.stderr)   # 文書のあるコードは挙げない
        self.assertFalse(self.run_pa(self.impl, "advise").stdout.startswith("AUTO"))   # 計画は同じステートのやり直しで直させ、ここでは訊く

        # 行に文書の名前があるだけ（変えない文書）では、作る項目にならない。
        self.write_plan(plan.replace("`pay` にログを足す", "`pay` にログを足す（仕様は design:docs/api.md）"))
        self.assertIn("対応する文書の無いコードです", self.run_pa(self.impl, "verify-plan").stderr)

        # 見本に倣って文書を作る項目を変更案に書けば、計画は通る。
        plan = plan.replace("## 参照先の変更案\n\nなし",
                            "## 参照先の変更案\n\n- docs/pay.md — `lib/pay.py` の文書を docs/api.md に倣って新しく書き、`pay` の節を置く")
        plan = plan.replace("## 守る決まり\n\nなし",
                            "## 守る決まり\n\n- docs/api.md — 文書の書式: 見本。見出しの並びと書き方を docs/pay.md でも同じにする")
        self.write_plan(plan)
        self.assert_plan_ok()

        # 作った文書がコードのパスを書いていなければ、変えたあとも止まる。
        (self.impl / "src/app.py").write_text("def hello():\n    print('hi')\n    return 1\n", encoding="utf-8")
        (self.impl / "lib/pay.py").write_text("def pay():\n    print('pay')\n    return 0\n", encoding="utf-8")
        (self.design / "docs/pay.md").write_text("# pay\n\n## pay\n\npay は 0 を返す。\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("対応する文書の無いコードです", r.stderr)
        self.assertIn("lib/pay.py", r.stderr)
        (self.design / "docs/pay.md").write_text("# pay\n\n## pay\n\npay は 0 を返す。\n\n実装は `lib/pay.py`。\n",
                                                 encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertNotIn("対応する文書の無いコードです", r.stderr)

    def test_code_to_delete_or_tool_settings_need_no_doc(self) -> None:
        # 消すコードと道具の設定（vite.config.ts など）には文書を求めない。消すと書いて消さなければ、変えたあとに止める。
        commit(self.impl, {"lib/old.py": "def old():\n    return 0\n", "vite.config.ts": "export default {}\n"}, "old")
        plan = PLAN_ALIGNED.replace("- src/app.py — `hello` の中でログを出す",
                                    "- src/app.py — `hello` の中でログを出す\n- lib/old.py — 使っていないので消す\n"
                                    "- vite.config.ts — 出力先を変える")
        self.write_plan(plan)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('hi')\n    return 1\n", encoding="utf-8")
        (self.impl / "vite.config.ts").write_text("export default { base: '/' }\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertIn("対応する文書の無いコードです", r.stderr)   # lib/old.py が残っている
        self.assertIn("lib/old.py", r.stderr)
        self.assertNotIn("vite.config.ts（", r.stderr)
        (self.impl / "lib/old.py").unlink()
        self.assertNotIn("対応する文書の無いコードです", self.run_pa(self.impl, "verify-apply").stderr)

    def test_code_without_any_doc_to_follow_says_so(self) -> None:
        # 見本になる文書がどこにも無ければ、そう伝える（作る手順は同じ）。
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 1 を返す。\n", encoding="utf-8")
        git(self.design, "commit", "-qam", "unlink")
        self.write_plan(PLAN_ALIGNED.replace("（根拠: docs/api.md:3）", "（根拠: docs/api.md:5）"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("src/app.py（見本になる文書がありません。文書は design（設計書） に置く）", r.stderr)
        # 文書の要らないコードは、設定の no_doc で外せる
        cfg = self.impl / ".statemachine/codd/codd.json"
        cfg.write_text(json.dumps({**json.loads(cfg.read_text(encoding="utf-8")), "no_doc": ["src/"]}), encoding="utf-8")
        r = self.run_pa(self.impl, "verify-plan")
        self.assertNotIn("対応する文書の無いコードです", r.stderr)

    def test_new_code_needs_its_own_doc_beside_an_overview(self) -> None:
        # `src/` を書いただけの概要の文書があっても、隣のコードがファイルごとの文書を持つなら、新しいコードにも文書が要る。
        commit(self.design, {"docs/api.md": "# API\n\n## hello\n\nhello は 1 を返す。\n\n実装は `src/app.py`。\n",
                             "docs/overview.md": "# 概要\n\nコードは `src/` に置く。\n"}, "overview")
        plan = self.with_tests(PLAN_ALIGNED.replace(
            "- src/app.py — `hello` の中でログを出す",
            "- src/app.py — `hello` の中でログを出す\n- src/pay.py — 新しく作り、`pay` を置く"),
            "- `pay` — 変更不要: 例")
        self.write_plan(plan)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("対応する文書の無いコードです", r.stderr)
        self.assertIn("src/pay.py（近い粒度の見本: design:docs/api.md（src/app.py に対応））", r.stderr)

        plan = plan.replace("## 参照先の変更案\n\nなし",
                            "## 参照先の変更案\n\n- docs/pay.md — `src/pay.py` の文書を docs/api.md に倣って新しく書き、`pay` の節を置く")
        plan = plan.replace("## 守る決まり\n\nなし",
                            "## 守る決まり\n\n- docs/api.md — 文書の書式: 見本。見出しの並びと書き方を docs/pay.md でも同じにする")
        self.write_plan(plan)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('hi')\n    return 1\n", encoding="utf-8")
        (self.impl / "src/pay.py").write_text("def pay():\n    return 0\n", encoding="utf-8")
        (self.design / "docs/pay.md").write_text("# pay\n\n## pay\n\npay は 0 を返す。\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertIn("対応する文書の無いコードです", r.stderr)   # 作った文書がコードのパスを書いていない
        (self.design / "docs/pay.md").write_text("# pay\n\n## pay\n\npay は 0 を返す。\n\n実装は `src/pay.py`。\n",
                                                 encoding="utf-8")
        self.assertNotIn("対応する文書の無いコードです", self.run_pa(self.impl, "verify-apply").stderr)

    def test_new_code_in_a_folder_documented_as_a_whole_needs_no_new_doc(self) -> None:
        # フォルダごとに文書を書くプロジェクトでは、そのフォルダの文書が新しいコードの文書になる。
        commit(self.impl, {"src/orders/a.py": "def a():\n    return 0\n"}, "orders")
        commit(self.design, {"docs/orders.md": "# 注文\n\n実装は `src/orders/` に置く。\n"}, "orders")
        self.write_plan(self.with_tests(PLAN_ALIGNED.replace(
            "- src/app.py — `hello` の中でログを出す",
            "- src/app.py — `hello` の中でログを出す\n- src/orders/b.py — 新しく作り、`b` を置く"),
            "- `b` — 変更不要: 例"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertNotIn("対応する文書の無いコードです", r.stderr)

    # ------------------------------------------------------------ 止まったとき

    def test_advise_proposes_next_steps(self) -> None:
        self.add_caller()
        self.write_plan(PLAN_ALIGNED)
        self.assertEqual(self.run_pa(self.impl, "verify-plan").returncode, 1)
        # 計画の段は訊かずに戻さない（同じステートのやり直しを使い切ってから stuck に来るので、ここで訊く）
        r = self.run_pa(self.impl, "advise")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("# 計画の検査で止まりました", r.stdout)
        self.assertIn("src/use.py", r.stdout)
        self.assertIn("直すか「変更不要」か関係が無いかを決めてもらいます", r.stdout)
        self.assertIn("1. 計画を練り直す（勧め） → `PLAN`", r.stdout)
        self.assertNotIn("`APPLY`", r.stdout)   # 計画が通っていないので、変える段へは進めない

        self.write_plan(PLAN_ALIGNED.replace("## 影響範囲\n\nなし", "## 影響範囲\n\n- src/use.py — 変更不要: 戻り値は同じ"))
        self.assert_plan_ok()
        self.assertFalse((self.impl / ".codd/problems.json").exists())   # 通ったら理由は消える
        (self.impl / "src/app.py").write_text("def hello():\n    return 1  # log\n", encoding="utf-8")
        (self.impl / "src/extra.py").write_text("x = 1\n", encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 1)
        # 計画に無い変更は、戻すか申告すれば直せるので、まず訊かずに変え直す（同じ指摘が残れば訊く）
        self.assertTrue(self.run_pa(self.impl, "advise").stdout.startswith("AUTO APPLY\n"))
        r = self.run_pa(self.impl, "advise")
        self.assertIn("# 変えたあとの検査で止まりました", r.stdout)
        self.assertIn("1. 計画はそのままで、変え直す（勧め） → `APPLY`", r.stdout)
        self.assertIn("変えた分は残して、計画を直す → `PLAN`\n", r.stdout)
        self.assertIn("変えた分を戻して、計画から練り直す → `PLAN`。先に `python3 .statemachine/codd/codd.py rollback` を実行",
                      r.stdout)
        self.assertIn("`STOP`", r.stdout)

        # 設定の誤りで止まっても、理由と次の手は示す。
        cfg = self.impl / ".statemachine/codd/codd.json"
        good = cfg.read_text(encoding="utf-8")
        cfg.write_text(json.dumps({"side": "impl", "refs": [{"path": "../nowhere"}]}), encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 2)
        r = self.run_pa(self.impl, "advise")
        self.assertIn("設定か環境の誤りです", r.stdout)
        self.assertIn("refs を直してください", r.stdout)
        cfg.write_text(good, encoding="utf-8")

    def test_failures_needing_no_human_retry_without_asking(self) -> None:
        # テストが落ちただけなら、利用者に訊かずに変え直す（同じ段で 2 回まで）。人の判断が要るものが混じれば訊く。
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        self.set_check(self.impl, [sys.executable, "-c", "import sys; sys.exit('テストが落ちた')"])
        (self.impl / "src/app.py").write_text("def hello():\n    return 1  # log\n", encoding="utf-8")
        for n in (1, 2):   # やり直すたびに落ち方が変わる（直しかけている）なら、訊かずに続ける
            self.set_check(self.impl, [sys.executable, "-c", f"import sys; sys.exit('テストが落ちた {n}')"])
            self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 1)
            r = self.run_pa(self.impl, "advise")
            self.assertTrue(r.stdout.startswith("AUTO APPLY\n"), r.stdout)
            self.assertIn(f"{n}/2 回目", (self.impl / ".codd/decisions.json").read_text(encoding="utf-8"))
        self.set_check(self.impl, [sys.executable, "-c", "import sys; sys.exit('テストが落ちた 3')"])
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 1)
        r = self.run_pa(self.impl, "advise")
        self.assertNotIn("AUTO", r.stdout)                  # 上限を超えたら訊く
        self.assertIn("1. 計画はそのままで、変え直す（勧め） → `APPLY`", r.stdout)
        # 通れば数え直す。計画で挙げていないファイルを足したことが混じれば最初から訊く。
        self.set_check(self.impl, [sys.executable, "-c", "pass"])
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 0, self.run_pa(self.impl, "verify-apply").stderr)
        self.set_check(self.impl, [sys.executable, "-c", "import sys; sys.exit('テストが落ちた')"])
        (self.impl / "src/extra.py").write_text("x = 1\n", encoding="utf-8")
        (self.impl / ".codd/apply.md").write_text("## 計画との違い\n\n- src/extra.py — 追加: 値を分けた\n", encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 1)
        self.assertNotIn("AUTO", self.run_pa(self.impl, "advise").stdout)

    def test_a_command_that_cannot_run_is_asked_as_config(self) -> None:
        # コマンドが動かせない失敗は、実装を直しても通らない。訊かずにやり直させず、設定か環境の誤りとして訊く。
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 1  # log\n", encoding="utf-8")
        for command in (["no-such-command-for-codd"],
                        [sys.executable, "-c", "print('sh: jest: command not found'); raise SystemExit(1)"]):
            self.set_config(self.impl, test=command)
            r = self.run_pa(self.impl, "verify-apply")
            self.assertEqual(r.returncode, 1)
            self.assertIn("テストか検査のコマンドを動かせませんでした", r.stderr)
            r = self.run_pa(self.impl, "advise")
            self.assertNotIn("AUTO", r.stdout)
            self.assertIn("設定か環境の誤りです", r.stdout)
        # 1 つの要素に空白ごと書いたコマンドは、引数ごとに分ける書き方を添える
        self.set_config(self.impl, test=["python3 -m unittest discover"])
        r = self.run_pa(self.impl, "verify-apply")
        self.assertIn('引数ごとに分けて書く: ["python3", "-m", "unittest", "discover"]', r.stderr)
        # CLI のテストが期待値として同じ文言を出して落ちたのは、テストの失敗のまま（訊かずに直させる）
        self.set_config(self.impl, test=[sys.executable, "-c",
                                         "print('FAILED test_cli - expected: sh: x: command not found'); raise SystemExit(1)"])
        r = self.run_pa(self.impl, "verify-apply")
        self.assertIn("検査が失敗しました", r.stderr)
        self.assertNotIn("動かせませんでした", r.stderr)
        # 長い出力は末尾だけを見せ、全文は .codd/logs/ に残して検索で引かせる（テストをやり直して全文を読ませない）。
        self.set_config(self.impl, test=[sys.executable, "-c",
                                         "print('\\n'.join(f'ok {i}' for i in range(40))); print('FAILED test_x'); raise SystemExit(1)"])
        r = self.run_pa(self.impl, "verify-apply")
        self.assertIn("全文: .codd/logs/", r.stderr)
        self.assertIn("開かずに grep で関係する行だけを読む", r.stderr)   # context-mode が無ければ grep
        log = next((self.impl / ".codd/logs").glob("*.log")).read_text(encoding="utf-8")
        self.assertIn("ok 0\n", log)

    def test_tests_changed_after_a_failure_are_asked(self) -> None:
        # 落ちたあとにテストのほうを変えたら、テストを合わせて通したかもしれない。訊かずにやり直させず利用者に訊く。
        commit(self.impl, {"tests/test_app.py": "from src.app import hello\n\n\ndef test_hello():\n    assert hello() == 1\n"},
               "test")
        self.write_plan(PLAN_ALIGNED.replace("- 変更不要: このリポジトリにテストはまだ無い（例の小さなリポジトリ）",
                                             "- tests/test_app.py — `hello` のログを確かめる"))
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 2  # log\n", encoding="utf-8")
        (self.impl / "tests/test_app.py").write_text(
            "from src.app import hello\n\n\ndef test_hello():\n    assert hello() == 1  # log\n", encoding="utf-8")
        check = "import sys, pathlib; sys.exit('FAILED test_hello' if '== 1' in pathlib.Path('tests/test_app.py').read_text() else 0)"
        self.set_config(self.impl, test=[sys.executable, "-c", check])
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("検査が失敗しました", r.stderr)
        self.assertTrue(self.run_pa(self.impl, "advise").stdout.startswith("AUTO APPLY"))
        # 実装でなくテストの期待値を書き換えた
        (self.impl / "tests/test_app.py").write_text(
            "from src.app import hello\n\n\ndef test_hello():\n    assert hello() == 2  # log\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("検査が落ちたあとに、テストのファイルを変えています", r.stderr)
        self.assertIn("tests/test_app.py（消した行 1・足した行 1）", r.stderr)
        r = self.run_pa(self.impl, "advise")
        self.assertNotIn("AUTO", r.stdout)
        self.assertIn("1. 計画はそのままで、変え直す（勧め） → `APPLY`", r.stdout)
        self.assertIn("テストの変更を認めて続ける → `APPLY`。先に `python3 .statemachine/codd/codd.py accept`", r.stdout)
        # 練り直した計画を確認で認めたら、それまでのテストの変更は利用者が見たもの（落ちた控えと比べない）
        self.assertTrue((self.impl / ".codd/failed-tests.json").exists())
        self.run_pa(self.impl, "decide", "OK")
        self.assertFalse((self.impl / ".codd/failed-tests.json").exists())
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        # また落ち、テストのほうを変えたら訊く
        (self.impl / "tests/test_app.py").write_text(
            "from src.app import hello\n\n\ndef test_hello():\n    assert hello() == 1  # log 2\n", encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 1)
        (self.impl / "tests/test_app.py").write_text(
            "from src.app import hello\n\n\ndef test_hello():\n    assert hello() == 2  # log 2\n", encoding="utf-8")
        self.assertIn("検査が落ちたあとに、テストのファイルを変えています", self.run_pa(self.impl, "verify-apply").stderr)
        # 利用者が認めたら、その中身で続ける
        self.assertEqual(self.run_pa(self.impl, "accept").returncode, 0)
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.impl / ".codd/failed-tests.json").exists())   # 通れば控えを消す

    def test_affected_tests_that_did_not_run_are_asked(self) -> None:
        # テストのコマンドが通っても、響くテストの結果が evidence に無ければ止める。evidence が無ければ報告で知らせる。
        commit(self.impl, {"tests/test_app.py": "from src.app import hello\n\n\ndef test_hello():\n    assert hello() == 1\n"},
               "test")
        self.write_plan(PLAN_ALIGNED.replace("- 変更不要: このリポジトリにテストはまだ無い（例の小さなリポジトリ）",
                                             "- tests/test_app.py — `hello` のログを確かめる"))
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 1  # log\n", encoding="utf-8")
        (self.impl / "tests/test_app.py").write_text(
            "from src.app import hello\n\n\ndef test_hello():\n    assert hello() == 1  # log\n", encoding="utf-8")
        self.set_config(self.impl, test=[sys.executable, "-c", "pass"])
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("変更が響くテストが動いたかは確かめていない（テストの結果（evidence）が扱っていないテスト。自分 1 件）",
                      self.report(self.impl).stdout)

        def writes(status: str, file: str) -> list[str]:
            item = {"kind": "behavior", "id": "hello", "title": "hello", "status": status, "file": file}
            return [sys.executable, "-c", "import json; json.dump({'version': 1, 'root': '..', 'items': ["
                    + json.dumps(item) + "]}, open('results/ui-evidence.json', 'w'))"]
        self.write_evidence([])
        for status, file in (("passed", "tests/test_other.py"), ("skipped", "tests/test_app.py")):
            self.set_config(self.impl, test=writes(status, file))
            r = self.run_pa(self.impl, "verify-apply")
            self.assertEqual(r.returncode, 1)
            self.assertIn("変更が響くテストが動いていません", r.stderr)
            self.assertNotIn("AUTO", self.run_pa(self.impl, "advise").stdout)
        self.set_config(self.impl, test=writes("passed", "tests/test_app.py"))
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        # evidence が e2e のケース（別の種類）しか書かないなら、単体テストは止めずに報告で知らせる
        (self.impl / "results/ui-evidence.json").unlink()
        self.set_config(self.impl, test=writes("passed", "e2e/login.yaml"))
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("確かめていない（テストの結果（evidence）が扱っていないテスト。自分 1 件）",
                      self.report(self.impl).stdout)
        # 動かないものと利用者が認めれば通し、報告に残す
        self.set_config(self.impl, test=writes("skipped", "tests/test_app.py"))
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 1)
        self.assertEqual(self.run_pa(self.impl, "accept").returncode, 0)
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("動かないまま、利用者が認めて続けた（tests/test_app.py）", self.report(self.impl).stdout)

    def test_a_test_failing_before_the_change_is_asked_not_retried(self) -> None:
        # 変える前から同じところで落ちるテストは、この回の変更のせいではない。エージェントに直させず訊き、
        # 認めたら同じ失敗だけを通す（新しい失敗は止める）。
        commit(self.impl, {"check.py": "import sys, pathlib\n"
                                       "print('FAILED test_old - broken since long ago')\n"
                                       "if 'return 2' in pathlib.Path('src/app.py').read_text():\n"
                                       "    print('FAILED test_new - value changed')\n"
                                       "sys.exit(1)\n"}, "broken test")
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        self.set_config(self.impl, test=[sys.executable, "check.py"])
        (self.impl / "src/app.py").write_text("def hello():\n    return 1  # log\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("変える前から同じところで落ちています", r.stderr)
        r = self.run_pa(self.impl, "advise")
        self.assertNotIn("AUTO", r.stdout)
        self.assertIn("1. 前から落ちていたものとして続ける（勧め） → `APPLY`。先に `python3 .statemachine/codd/codd.py accept`",
                      r.stdout)
        self.assertEqual(self.run_pa(self.impl, "accept").returncode, 0)
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("変える前から落ちていたものを、利用者が認めて続けた", self.report(self.impl).stdout)
        # 新しい失敗が混じれば、これまでどおりこの回の失敗として止める（訊かずに直させる側）
        (self.impl / "src/app.py").write_text("def hello():\n    return 2  # log\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("検査が失敗しました", r.stderr)
        self.assertNotIn("変える前から同じところで", r.stderr)
        self.assertEqual(git(self.impl, "worktree", "list").count("\n"), 1)   # 一時的な worktree は残さない

    def test_a_failure_that_cannot_be_judged_before_the_change_is_asked(self) -> None:
        # 変える前の写し（一時的な worktree）には、コミットしない依存が無い。別の理由で落ちたら、前から落ちていたかは
        # 分からないと言って利用者に訊く（今の失敗として直させない）
        commit(self.impl, {".gitignore": "deps/\n",
                           "check.py": "import sys, pathlib\n"
                                       "if not pathlib.Path('deps').is_dir():\n"
                                       "    print(\"Error: Cannot find module 'deps'\")\n"
                                       "    sys.exit(1)\n"
                                       "print('FAILED test_x - value')\n"
                                       "sys.exit(1)\n"}, "test needs deps")
        (self.impl / "deps").mkdir()
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        self.set_config(self.impl, test=[sys.executable, "check.py"])
        (self.impl / "src/app.py").write_text("def hello():\n    return 1  # log\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("変える前から落ちていたかを判定できません", r.stderr)
        self.assertIn("Cannot find module 'deps'", r.stderr)
        self.assertIn("写しにつないだ依存のフォルダ: なし", r.stderr)   # 何をつないだかを添える（環境を直す手がかり）
        r = self.run_pa(self.impl, "advise")
        self.assertNotIn("AUTO", r.stdout)
        # 設定の誤りではないので、失敗の中身を見て前からの失敗として続けることもできる
        self.assertIn("前から落ちていたものとして続ける → `APPLY`", r.stdout)
        self.assertEqual(self.run_pa(self.impl, "accept").returncode, 0)
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        # 環境の理由で落ちた変える前の結果は控えない（環境を直せば、次は動かし直す）
        self.assertNotIn("Cannot find module", (self.impl / ".codd/before-runs.json").read_text(encoding="utf-8")
                         if (self.impl / ".codd/before-runs.json").exists() else "")

    def test_ignored_dependencies_are_linked_into_the_copy_before_the_change(self) -> None:
        self.check_dependencies_linked("node_modules/\n")

    def test_untracked_dependencies_are_linked_into_the_copy_before_the_change(self) -> None:
        # 無視していない（追跡もしていない）node_modules は、印のときに作業中だったファイルとして写しに入る
        self.check_dependencies_linked("")

    def check_dependencies_linked(self, ignore: str) -> None:
        # 無視されている依存のフォルダ（node_modules など）は、変える前の写しへつなぐ。前からの失敗を見分けられ、
        # 写しを消してもつないだ先の依存は消えない
        commit(self.impl, {".gitignore": ignore,
                           "check.py": "import sys, pathlib\n"
                                       "if not pathlib.Path('node_modules/dep/index.js').is_file():\n"
                                       "    print(\"'dep' is not recognized as an internal or external command\")\n"
                                       "    sys.exit(1)\n"
                                       "print('FAILED test_old - broken since long ago')\n"
                                       "sys.exit(1)\n"}, "test needs node_modules")
        (self.impl / "node_modules/dep").mkdir(parents=True)
        (self.impl / "node_modules/dep/index.js").write_text("\n", encoding="utf-8")
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        self.set_config(self.impl, test=[sys.executable, "check.py"])
        (self.impl / "src/app.py").write_text("def hello():\n    return 1  # log\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("変える前から同じところで落ちています", r.stderr)
        self.assertNotIn("判定できません", r.stderr)
        self.assertTrue((self.impl / "node_modules/dep/index.js").is_file())
        self.assertEqual(git(self.impl, "worktree", "list").count("\n"), 1)

    def test_apply_log_does_not_carry_over_to_the_next_run(self) -> None:
        (self.impl / ".codd").mkdir(exist_ok=True)
        (self.impl / ".codd/apply.md").write_text("# 前の回の記録\n", encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "draft", "--name", "next-run").returncode, 0)
        self.assertFalse((self.impl / ".codd/apply.md").exists())

    def test_declared_differences_from_the_plan_need_no_replanning(self) -> None:
        commit(self.impl, {"src/other.py": "LEVEL = 1\n", "src/third.py": "X = 1\n"}, "more")
        self.write_plan(PLAN_ALIGNED.replace(
            "- src/app.py — `hello` の中でログを出す", "- src/app.py — `hello` の中でログを出す\n- src/other.py — ログの段を直す")
            .replace("## 影響範囲\n\nなし", "## 影響範囲\n\n- src/third.py — 変更不要: 名前が似ているだけ"))
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 1  # log\n", encoding="utf-8")
        (self.impl / "src/third.py").write_text("X = 2\n", encoding="utf-8")
        (self.impl / "src/notes.txt").write_text("ログの書き方\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("src/other.py", r.stderr)
        self.assertIn("追加: 理由", r.stderr)
        # 変え残し・計画に無い変更は、計画を直さずに申告で済む。まずは訊かずに変え直させる。
        self.assertTrue(self.run_pa(self.impl, "advise").stdout.startswith("AUTO APPLY\n"))
        (self.impl / ".codd/apply.md").write_text(
            "## 計画との違い\n\n- src/other.py — 変更不要: 段はもう正しかった\n"
            "- src/third.py — 追加: 定数をそろえた\n- src/notes.txt — 追加: 書き方を残した\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        # 計画で挙げたファイル（third.py）は訊かずに認め、挙げていないもの（notes.txt）だけを利用者に確かめる。
        self.assertIn("計画で挙げていないファイルを足しました", r.stderr)
        self.assertIn("src/notes.txt — 書き方を残した", r.stderr)
        self.assertNotIn("src/third.py", r.stderr)
        self.assertNotIn("src/other.py", r.stderr)
        r = self.run_pa(self.impl, "advise")
        self.assertNotIn("AUTO", r.stdout)
        self.assertIn("1. 足したファイルを認めて続ける（勧め） → `APPLY`。先に `python3 .statemachine/codd/codd.py accept` を実行",
                      r.stdout)
        r = self.run_pa(self.impl, "accept")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_pa(self.impl, "verify-apply")   # 計画は書き直さないので、確認からやり直さない
        self.assertEqual(r.returncode, 0, r.stderr)
        report = self.report(self.impl).stdout
        self.assertIn("- src/other.py — 変更不要（変える段で判断）: 段はもう正しかった", report)
        self.assertIn("- src/notes.txt — 変えた（利用者が認めた: 書き方を残した）", report)
        self.assertIn("- src/third.py — 変えた（変える段で足した: 定数をそろえた）", report)

    def test_waiving_files_outside_the_plan_shows_in_the_report(self) -> None:
        # 計画に無いファイルを変える段で変更不要としても、黙って通さず報告に出す（計画で挙げたものは各側の行に出る）
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 1  # log\n", encoding="utf-8")
        (self.impl / ".codd/apply.md").write_text(
            "## 計画との違い\n\n- src/third.py、src/app.py — 変更不要: 名前が似ているだけ\n", encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 0)
        report = self.report(self.impl).stdout
        self.assertIn("## 変える段で変更不要とした、計画に無いファイル\n\n- src/third.py — 変更不要: 名前が似ているだけ", report)
        self.assertNotIn("src/app.py — 変更不要", report.split("計画に無いファイル")[1])   # 計画で挙げたものは各側の行に出る

    def test_same_spelling_names_in_refs_are_declared_unrelated(self) -> None:
        # 変える段で足した名前が、参照先の関係の無いファイルにも同じ綴りで出てくる。人に訊かず、申告で済ませる。
        commit(self.design, {"docs/zoom.md": "# ズーム\n\n`is_selectable` でズームできるかを決める。\n"}, "zoom")
        commit(self.impl, {"src/zoom.py": "# is_selectable はズームの判定\n"}, "zoom")
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text(
            "def hello():\n    return 1  # log\n\n\ndef is_selectable():\n    return True\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("名前に触れている参照先のファイルを、計画で扱っていません", r.stderr)
        self.assertIn("docs/zoom.md", r.stderr)
        self.assertIn("直していないファイルがあります", r.stderr)    # 自分の側も同じ綴りに当たる
        self.assertIn("src/zoom.py", r.stderr)
        self.assertIn("名前ごとに「- `名前` — 関係なし: 理由」", r.stderr)
        self.assertTrue(self.run_pa(self.impl, "advise").stdout.startswith("AUTO APPLY\n"))
        own = "\n- src/zoom.py — 変更不要: ズームの判定"
        # 名前ごとに 1 行書けば、その名前だけで当たったファイル（参照先も自分も）はまとめて済む。
        for line in ("- `is_selectable` — 関係なし: ズームの判定で、候補の選択とは別物",
                     "- docs/zoom.md — 関係なし: ズームの判定で、候補の選択とは別物" + own,
                     "- design:docs/zoom.md — 関係なし: ズームの判定で、候補の選択とは別物" + own):
            with self.subTest(line=line):
                (self.impl / ".codd/apply.md").write_text(f"## 計画との違い\n\n{line}\n", encoding="utf-8")
                r = self.run_pa(self.impl, "verify-apply")
                self.assertEqual(r.returncode, 0, r.stderr)
                report = self.report(self.impl).stdout
                self.assertIn("## 変える段で関係なしとした名前・ファイル", report)
                self.assertIn("関係なし: ズームの判定で、候補の選択とは別物", report)

    def test_names_removed_but_still_written_in_refs_stop_the_apply(self) -> None:
        # 参照先の文書を直しても、消した名前を書いた行が残っていれば止める（人には訊かない）。
        commit(self.impl, {"src/app.py": "def hello():\n    return 1\n\n\ndef make_greeting():\n    return 'hi'\n"},
               "greet")
        commit(self.design, {"docs/api.md": "# API\n\n## hello\n\nhello は 1 を返す。\n\n\n実装は `src/app.py`（`src/` の下）。\n"
                                            "挨拶は `make_greeting` で作る。\n"}, "greet")
        self.write_plan(PLAN_DRIFT)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text(
            "def hello():\n    return 2\n", encoding="utf-8")
        (self.design / "docs/api.md").write_text(
            "# API\n\n## hello\n\nhello は 2 を返す。\n\n挨拶は `make_greeting` で作る。\n\n実装は `src/app.py`（`src/` の下）。\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("消した名前を、まだ書いているところがあります", r.stderr)
        self.assertIn("`make_greeting` — design:docs/api.md:7", r.stderr)
        self.assertTrue(self.run_pa(self.impl, "advise").stdout.startswith("AUTO APPLY\n"))
        (self.design / "docs/api.md").write_text(
            "# API\n\n## hello\n\nhello は 2 を返す。\n\n実装は `src/app.py`（`src/` の下）。\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_committing_midway_still_measures_this_runs_changes(self) -> None:
        # 変える段の途中でコミットしても、この回の変更（変える前の印から）で影響を測る。
        commit(self.design, {"docs/zoom.md": "# ズーム\n\n`is_selectable` でズームできるかを決める。\n"}, "zoom")
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text(
            "def hello():\n    return 1  # log\n\n\ndef is_selectable():\n    return True\n", encoding="utf-8")
        git(self.impl, "add", "-A")
        git(self.impl, "commit", "-q", "-m", "途中")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("名前に触れている参照先のファイルを、計画で扱っていません", r.stderr)
        self.assertIn("docs/zoom.md", r.stderr)

    def test_tests_wait_until_the_change_itself_is_complete(self) -> None:
        mark = self.tmp / "checked"
        self.set_check(self.impl, [sys.executable, "-c", f"open({str(mark)!r}, 'a').write('x')"])
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        (self.impl / "src/extra.py").write_text("x = 1\n", encoding="utf-8")   # 変え残しと計画に無い変更
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("上の指摘を直したあとの検査で動かします", r.stdout)
        self.assertFalse(mark.exists())   # 直せばどのみち動かし直すので、重い検査を先に回さない
        (self.impl / "src/extra.py").unlink()
        (self.impl / "src/app.py").write_text("def hello():\n    return 1  # log\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(mark.exists())    # 通すときは必ず動かす

    def test_several_checks_run_in_order_and_stop_at_the_first_failure(self) -> None:
        log = self.tmp / "ran"
        step = lambda name, code: [sys.executable, "-c",  # noqa: E731
                                   f"open({str(log)!r}, 'a').write({name!r}); raise SystemExit({code})"]
        self.set_check(self.impl, [step("a", 0), step("b", 1), step("c", 0)])
        r = self.run_pa(self.impl, "show")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count("自分の検査: "), 3)
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 1  # log\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("検査が失敗しました", r.stderr)
        ran = log.read_text(encoding="utf-8")   # 落ちた b は、変える前の中身でもう一度動く
        self.assertTrue(ran.startswith("ab"), ran)
        self.assertNotIn("c", ran)               # 落ちたあとのコマンドは動かさない
        log.unlink()
        self.set_check(self.impl, [step("a", 0), step("c", 0)])
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(log.read_text(encoding="utf-8"), "ac")

    def test_several_tests_all_run_even_when_one_fails(self) -> None:
        # test は check と同じ書き方でコマンドを並べられるが、互いに独立なので落ちても残りを動かす（失敗を一度に返す）
        log = self.tmp / "ran"
        step = lambda name, code: [sys.executable, "-c",  # noqa: E731
                                   f"open({str(log)!r}, 'a').write({name!r}); raise SystemExit({code})"]
        self.set_check(self.impl, [step("a", 1), step("b", 0), step("c", 1)], key="test")
        self.set_check(self.impl, [step("x", 0)])
        r = self.run_pa(self.impl, "show")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count("自分のテスト: "), 3)
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 1  # log\n", encoding="utf-8")
        log.unlink(missing_ok=True)
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        ran = log.read_text(encoding="utf-8")   # 落ちた a と c は、変える前の中身でもう一度動く
        for name in "abcx":
            self.assertIn(name, ran)
        self.assertTrue(ran.startswith("a"), ran)
        log.unlink()
        self.set_check(self.impl, [step("a", 0), step("c", 0)], key="test")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(log.read_text(encoding="utf-8"), "acx")

    def test_rechecking_the_plan_keeps_the_work_already_done(self) -> None:
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 1  # log\n", encoding="utf-8")
        (self.impl / "src/log.py").write_text("def log(m):\n    print(m)\n", encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 1)   # log.py は計画に無い
        self.write_plan(self.with_tests(PLAN_ALIGNED.replace(
            "- src/app.py — `hello` の中でログを出す", "- src/app.py — `hello` の中でログを出す\n- src/log.py — `log` を足す"),
            "- `log` — 変更不要: print を包むだけ"))
        for _ in range(2):   # 検査し直すたびに印を今の中身へ取り直すと、変え終えた app.py が「まだ変えていない」になる
            self.assert_plan_ok()
        r = self.run_pa(self.impl, "verify-apply")   # 前の印から数えるので、残した変更がそのまま効く
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("own=changed", r.stdout)

        # 回を終えたら（計画を記録として閉じたら）、次の回は今の中身から数える。
        # 確かめる流れを勧めたあと断られても、次の回にまた訊かない（3 回ごと）。
        (self.impl / ".codd/runs-since-review.json").write_text('{"runs": 3}', encoding="utf-8")
        r = self.run_pa(self.impl, "record")
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("REVIEW", r.stdout)
        (self.impl / ".plans/2099-01-01-0000-next.md").write_text(
            PLAN_ALIGNED.replace("`hello` の中でログを出す", "`hello` の戻り値を 3 にする"), encoding="utf-8")
        self.read_up(self.impl)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 3  # log\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertNotIn("計画に無いファイル", r.stderr)   # 前の回の log.py は数えない

    def test_replanning_after_changing_checks_evidence_before_the_change(self) -> None:
        # 変えたあとに止まって練り直すと、根拠（行・見出し・名前）は変える前のファイルを指している。
        plan = PLAN_DRIFT.replace("- hello は整数を返す（根拠: docs/api.md）",
                                  "- `hello` は整数を返す（根拠: docs/api.md:3-5#hello）")
        self.write_plan(plan)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hi():\n    return 2\n", encoding="utf-8")   # hello を消した
        (self.design / "docs/api.md").write_text("# API\n\nhi は 2 を返す。\n\n実装は `src/app.py`（`src/` の下）。\n", encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 1)
        self.write_plan(plan.replace("と書き直す", "と書き直し、名前を hi にする"))
        r = self.run_pa(self.impl, "verify-plan")
        for wrong in ("行目はありません", "見出し #hello がありません", "見当たりません", "実在する根拠のパスがありません",
                      "新しく足す"):
            self.assertNotIn(wrong, r.stderr)
        # 回を終えたあとの計画は、今の中身で確かめる。
        self.assertEqual(self.run_pa(self.impl, "record").returncode, 0)
        (self.impl / ".plans/2099-01-01-0000-next.md").write_text(plan, encoding="utf-8")
        self.read_up(self.impl)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertIn("見当たりません: `hello`", r.stderr)

    def test_writing_a_new_plan_after_changing_keeps_the_work_already_done(self) -> None:
        # 変えたあとに止まって `draft --new` で書き直しても、変えた分は前の印から数える。
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 1  # log\n", encoding="utf-8")
        (self.impl / "src/log.py").write_text("def log(m):\n    print(m)\n", encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 1)   # log.py は計画に無い
        # 同じことの書き直しか別のことかは codd に分からないので、--keep で明かさせる（別のことに使うと前の変更が混ざる）
        r = self.run_pa(self.impl, "draft", "--new", "--name", "with-log")
        self.assertEqual(r.returncode, 1)
        self.assertIn("もう変えたファイルがあります", r.stderr)
        self.assertIn("record --stop", r.stderr)
        self.assertTrue(any((self.impl / ".plans").glob("*.md")))   # 前の計画は消さない
        r = self.run_pa(self.impl, "draft", "--new", "--keep", "--name", "with-log")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("変える前の印から数えます", r.stdout)
        new = next((self.impl / ".plans").glob("*-with-log.md"))
        new.write_text(self.with_tests(PLAN_ALIGNED.replace(
            "- src/app.py — `hello` の中でログを出す", "- src/app.py — `hello` の中でログを出す\n- src/log.py — `log` を足す"),
            "- `log` — 変更不要: print を包むだけ"), encoding="utf-8")
        self.assert_plan_ok()
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("own=changed", r.stdout)

    def test_replanning_after_changing_keeps_the_format_before_the_change(self) -> None:
        # 変えたあとに練り直しても、書式は変える前の見出しの並びで控える（変えた見出しを書式として控え直さない）。
        self.write_plan(PLAN_DRIFT)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.design / "docs/api.md").write_text("# API\n\nhello は 2 を返す。\n\n実装は `src/app.py`（`src/` の下）。\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertIn("文書の書式（見出しの並び）が今の書式から外れています", r.stderr)
        self.write_plan(PLAN_DRIFT.replace("と書き直す", "と書き直す（表も直す）"))
        self.run_pa(self.impl, "verify-plan")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertIn("文書の書式（見出しの並び）が今の書式から外れています: design:docs/api.md — ## hello", r.stderr)

    def test_rollback_restores_the_state_before_the_change(self) -> None:
        commit(self.impl, {"src/gone.py": "g = 1\n"}, "gone")
        (self.impl / "src/wip.py").write_text("wip = 1\n", encoding="utf-8")   # 計画より前から作業中
        self.write_plan(PLAN_DRIFT)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.impl / "src/wip.py").write_text("wip = 2\n", encoding="utf-8")
        (self.impl / "src/new.py").write_text("n = 1\n", encoding="utf-8")
        (self.impl / "src/gone.py").unlink()
        (self.design / "docs/api.md").write_text("変えた\n", encoding="utf-8")
        r = self.run_pa(self.impl, "rollback")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("変える前に戻しました: 5 files", r.stdout)
        self.assertEqual((self.impl / "src/app.py").read_text(encoding="utf-8"), "def hello():\n    return 1\n")
        self.assertEqual((self.impl / "src/wip.py").read_text(encoding="utf-8"), "wip = 1\n")   # 作業中の中身へ
        self.assertFalse((self.impl / "src/new.py").exists())
        self.assertEqual((self.impl / "src/gone.py").read_text(encoding="utf-8"), "g = 1\n")
        self.assertIn("hello は 1 を返す", (self.design / "docs/api.md").read_text(encoding="utf-8"))
        # 途中でコミットされたら、戻さずに知らせる。
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        git(self.impl, "commit", "-q", "-am", "mid")
        r = self.run_pa(self.impl, "rollback")
        self.assertEqual(r.returncode, 1)
        self.assertIn("途中でコミットされた", r.stderr)

    # ------------------------------------------------------------ リポジトリのスキル・カスタムエージェント

    def test_external_home_skills_are_read_without_agent_cli(self) -> None:
        for folder, name in ((".kiro/skills", "graphify"), (".copilot/skills", "caveman")):
            path = self.tmp / "home" / folder / name / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text(f"---\nname: {name}\ndescription: external skill\n---\nExternal content\n", encoding="utf-8")
            r = self.run_pa(self.impl, "guide", name)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("External content", r.stdout)
            log = json.loads((self.impl / ".codd/skills-read.json").read_text(encoding="utf-8"))
            self.assertEqual(Path(log[name]["path"]), path)

    def test_repo_skills_are_used_without_config(self) -> None:
        commit(self.impl, {".agents/skills/tdd-lite/SKILL.md":
                           "---\nname: tdd-lite\ndescription: テストを先に書く\n---\n\n# tdd-lite\n"}, "skill")
        r = self.run_pa(self.impl, "show")
        self.assertIn("リポジトリのスキル", r.stdout)
        self.assertIn("`tdd-lite` — テストを先に書く（.agents/skills/tdd-lite/SKILL.md）", r.stdout)
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()   # 使わないリポジトリのスキルを、1 つずつ断らせない
        # 使うと書いたスキルは、codd.py skill で読み込んでいなければ落とす（エージェントの自動選択に頼らない）。
        plan = PLAN_ALIGNED.replace("## 使ったスキルと道具\n\nなし",
                                    "## 使ったスキルと道具\n\n- `tdd-lite` — 変えるときにテストを先に書く")
        self.write_plan(plan)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("スキルを読み込んでいません", r.stderr)
        r = self.run_pa(self.impl, "guide", "tdd-lite")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("# tdd-lite", r.stdout)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 1  # log\n", encoding="utf-8")
        (self.impl / ".codd/apply.md").write_text("- `tdd-lite` — テストを先に書いた\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")   # 計画のときに読んだだけでは、変えるときに読んだことにならない
        self.assertEqual(r.returncode, 1)
        self.assertIn(".codd/apply.md に挙げたスキルを読み込んでいません", r.stderr)
        self.run_pa(self.impl, "guide", "tdd-lite")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.run_pa(self.impl, "guide", "nothing").returncode, 1)
        # 置き場所は skill_dirs で変えられ、[] で使わない。
        path = self.impl / ".statemachine/codd/codd.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg["skill_dirs"] = []
        path.write_text(json.dumps(cfg), encoding="utf-8")
        self.assertNotIn("リポジトリのスキル", self.run_pa(self.impl, "show").stdout)

    def test_draft_places_template_and_keeps_existing_plan(self) -> None:
        # 計画は一度に全文を書かせず、ひな形を置いて見出しごとに書かせる（応答の長さの上限で止まらないように）。
        # 名前は日時と英語の短い名前で一意にする。
        r = self.run_pa(self.impl, "draft")
        self.assertEqual(r.returncode, 2)
        self.assertIn("英語の短い名前", r.stderr)
        self.assertEqual(self.run_pa(self.impl, "draft", "--name", "ログを足す").returncode, 2)
        r = self.run_pa(self.impl, "draft", "--name", "add-hello-log")
        self.assertEqual(r.returncode, 0, r.stderr)
        plans = list((self.impl / ".plans").glob("*.md"))
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertRegex(plan.name, r"^\d{4}-\d{2}-\d{2}-\d{4}-add-hello-log\.md$")
        template = (self.impl / ".statemachine/codd/templates/plan.md").read_text(encoding="utf-8")
        self.assertEqual(plan.read_text(encoding="utf-8"), template)
        # 書きかけのままでは計画の検査を通らない（コメントだけの見出しは空として落ちる）。
        self.read_up(self.impl)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("見出しの中身が空です", r.stdout + r.stderr)
        plan.write_text(PLAN_ALIGNED, encoding="utf-8")
        self.assertIn("計画はもうあります", self.run_pa(self.impl, "draft", "--name", "other").stdout)
        self.assertEqual(plan.read_text(encoding="utf-8"), PLAN_ALIGNED)
        self.run_pa(self.impl, "draft", "--new", "--name", "add-hello-log")
        plans = list((self.impl / ".plans").glob("*.md"))
        self.assertEqual(len(plans), 1)    # 進めていた計画は捨てて置き直す（同じ時刻なら -2 が付く）
        self.assertEqual(plans[0].read_text(encoding="utf-8"), template)

    def test_summary_is_short_and_points_to_the_plan(self) -> None:
        self.assertEqual(self.run_pa(self.impl, "summary").returncode, 1)
        many = "\n".join(f"- src/m{i}.py — {'長い説明' * 40}" for i in range(20))
        self.write_plan(PLAN_ALIGNED.replace("## 影響範囲\n\nなし", f"## 影響範囲\n\n{many}"), read=False)
        r = self.run_pa(self.impl, "summary")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn(f"全文: {PLAN}", out)
        self.assertIn("## やりたいこと\n\nhello にログを足す。", out)
        self.assertIn("## 自分の変更案", out)
        self.assertIn(f"- ほか 8 件（{PLAN}）", out)
        self.assertNotIn("src/m12.py", out)
        self.assertTrue(all(len(ln) <= 120 for ln in out.splitlines()))
        # 根拠の見出しは件数だけ（全文は貼らない）。
        self.assertIn("根拠: 参照先の前提", out)
        self.assertNotIn("変更不要", out)
        self.assertNotIn("## 参照先の前提", out)

    @unittest.skipIf(os.name == "nt", "偽の uv を sh で作る")
    def test_install_puts_middleware_on_the_terminal_and_init_places_codd(self) -> None:
        # install.py は外部のミドルウェア（graphify など）を端末に入れる。codd はリポジトリに init.py で置く。
        bin_dir = self.tmp / "tools-bin"
        bin_dir.mkdir()
        uv_log = self.tmp / "uv.log"
        uv = bin_dir / "uv"
        uv.write_text(f'#!/bin/sh\necho "$@" >> {uv_log}\n'
                      f'printf \'#!/bin/sh\\necho "graphify 1.0"\\n\' > {bin_dir}/graphify\n'
                      f'chmod +x {bin_dir}/graphify\n', encoding="utf-8")
        uv.chmod(0o755)
        # context-mode は必須。npm で入れる（スタブの npm が context-mode のスタブを置く）。
        npm_log = self.tmp / "npm.log"
        npm = bin_dir / "npm"
        npm.write_text(f'#!/bin/sh\necho "$@" >> {npm_log}\n'
                       f'printf \'#!/bin/sh\\necho "Usage:"\\n\' > {bin_dir}/context-mode\n'
                       f'chmod +x {bin_dir}/context-mode\n', encoding="utf-8")
        npm.chmod(0o755)
        env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin", "HOME": str(self.tmp / "home")}
        run = lambda *a: subprocess.run([sys.executable, str(TOOL / "install.py"), *a], capture_output=True,
                                        text=True, env=env)
        r = run("--no-skills")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(uv_log.read_text(encoding="utf-8").split(), ["tool", "install", "graphifyy"])
        self.assertIn("✓ graphify: graphify 1.0", r.stdout)
        self.assertEqual(npm_log.read_text(encoding="utf-8").split(), ["install", "-g", "context-mode"])
        self.assertIn(f"✓ context-mode: {bin_dir / 'context-mode'}", r.stdout)
        self.assertIn("✓ git:", r.stdout)
        self.assertNotIn("webui-test", r.stdout)
        r = run("--no-skills")   # 入っていれば入れ直さない
        self.assertEqual(uv_log.read_text(encoding="utf-8").split(), ["tool", "install", "graphifyy"])
        self.assertEqual(npm_log.read_text(encoding="utf-8").split(), ["install", "-g", "context-mode"])
        run("--upgrade", "--no-skills")
        self.assertEqual(uv_log.read_text(encoding="utf-8").split()[-3:], ["tool", "upgrade", "graphifyy"])
        self.assertEqual(npm_log.read_text(encoding="utf-8").split()[-3:], ["install", "-g", "context-mode@latest"])
        # context-mode を入れられなければ、必須なので失敗にする。
        npm.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        (bin_dir / "context-mode").unlink()
        r = run("--no-skills")
        self.assertEqual(r.returncode, 1)
        self.assertIn("✗ context-mode を入れられませんでした（npm（Node.js 22.5 以上）が要ります。必須です）", r.stdout)
        # 端末に入れても、リポジトリには何も置かない。
        app = self.tmp / "app"
        app.mkdir()
        git(app, "init", "-q", "-b", "main")
        self.assertFalse((app / ".statemachine").exists())
        # 以前の使い方（install.py にリポジトリを渡す）は init.py に渡す。
        rc = install.main([str(app), "--side", "impl", "--ref", "../design", "--no-agents", "--no-discover-rules"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads((app / ".statemachine/codd/codd.json").read_text(encoding="utf-8"))["side"], "impl")

    def test_middleware_installs_external_skills_for_both_ides_without_agent_cli(self) -> None:
        with mock.patch.object(install, "setup_caveman", return_value=True) as caveman, \
             mock.patch.object(install, "version", return_value="graphify 1.0"), \
             mock.patch.object(install.shutil, "which", side_effect=lambda name, **_: f"/tools/{name}" if name in ("graphify", "git") else None), \
             mock.patch.object(install.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            self.assertEqual(install.main([]), 0)
        self.assertEqual(caveman.call_args_list,
                         [mock.call("kiro", force=False), mock.call("copilot", force=False)])
        self.assertEqual(run.call_args_list,
                         [mock.call(["/tools/graphify", "install", "--platform", agent], timeout=install.TIMEOUT)
                          for agent in ("kiro", "copilot")])

    def test_middleware_agent_option_and_upgrade_do_not_delegate_to_repo_init(self) -> None:
        with mock.patch.object(install, "install_middleware", return_value=0) as middleware:
            self.assertEqual(install.main(["--agent", "kiro", "--upgrade"]), 0)
            middleware.assert_called_once_with(True, agents=["kiro"], skills=True)
        with mock.patch.object(install, "install_middleware", return_value=0) as middleware:
            self.assertEqual(install.main(["--no-skills"]), 0)
            middleware.assert_called_once_with(False, agents=install.AGENT_KINDS, skills=False)

    def test_middleware_reports_failed_skill_registration_and_still_installs_other_skills(self) -> None:
        with mock.patch.object(install, "setup_caveman", return_value=True) as caveman, \
             mock.patch.object(install, "version", return_value="graphify 1.0"), \
             mock.patch.object(install, "run_first", return_value="uv"), \
             mock.patch.object(install.shutil, "which", return_value="/tools/graphify"), \
             mock.patch.object(install.subprocess, "run", side_effect=[subprocess.TimeoutExpired("graphify", 600),
                                                                      mock.Mock(returncode=0)]) as run:
            self.assertEqual(install.install_middleware(upgrade=True, agents=("kiro", "copilot")), 1)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(caveman.call_args_list,
                         [mock.call("kiro", force=True), mock.call("copilot", force=True)])

    def test_middleware_runs_from_standalone_distribution_without_root_installer(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tool = root / "distribution with spaces/tools/codd-agent"
            tool.mkdir(parents=True)
            script = tool / "install.py"
            shutil.copy2(TOOL / "install.py", script)
            self.assertFalse((tool.parent.parent / "install.py").exists())
            archive_data = io.BytesIO()
            with zipfile.ZipFile(archive_data, "w") as archive:
                archive.writestr("caveman-main/skills/caveman/SKILL.md", "official skill")
                archive.writestr("caveman-main/skills/caveman/references/detail.md", "official reference")
                archive.writestr("caveman-main/LICENSE", "official license")
            # Run from an unrelated working directory with no installer or agent CLIs.
            runner = root / "run.py"
            runner.write_text(textwrap.dedent(f"""\
                import io, runpy
                from unittest import mock
                with mock.patch.dict("os.environ", {{"USERPROFILE": {str(root / 'home')!r}}}), \
                     mock.patch("shutil.which", side_effect=lambda name, **_: name if name in ("graphify", "context-mode") else None), \
                     mock.patch("subprocess.run", return_value=mock.Mock(returncode=0, stdout="graphify 1.0", stderr="")), \
                     mock.patch("urllib.request.urlopen", side_effect=lambda *a, **k: io.BytesIO({archive_data.getvalue()!r})):
                    runpy.run_path({str(script)!r}, run_name="__main__")
                """), encoding="utf-8")
            run = subprocess.run([sys.executable, str(runner)], cwd=root, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            for agent in ("kiro", "copilot"):
                dest = root / f"home/.{agent}/skills/caveman"
                self.assertEqual((dest / "SKILL.md").read_text(), "official skill")
                self.assertEqual((dest / "references/detail.md").read_text(), "official reference")
                self.assertEqual((dest / "LICENSE").read_text(), "official license")

    def test_middleware_caveman_preserves_existing_skill_on_failed_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"USERPROFILE": home}):
            dest = Path(home) / ".kiro/skills/caveman"
            dest.mkdir(parents=True)
            (dest / "SKILL.md").write_text("user skill")
            with mock.patch.object(install.urllib_request, "urlopen", side_effect=OSError("offline")) as fetch:
                self.assertTrue(install.setup_caveman("kiro"))
                fetch.assert_not_called()
                self.assertFalse(install.setup_caveman("kiro", force=True))
            self.assertEqual((dest / "SKILL.md").read_text(), "user skill")

    def test_middleware_rejects_invalid_caveman_archive_before_writing(self) -> None:
        cases = [None, {"skills/caveman/README.md": "missing skill"},
                 {"skills/caveman/SKILL.md": "skill", "skills/caveman/../../escape": "bad path"}]
        for files in cases:
            with self.subTest(files=files), tempfile.TemporaryDirectory() as home:
                data = io.BytesIO(b"not a zip")
                if files is not None:
                    data = io.BytesIO()
                    with zipfile.ZipFile(data, "w") as archive:
                        for path, body in files.items():
                            archive.writestr(f"caveman-main/{path}", body)
                dest = Path(home) / ".copilot/skills/caveman"
                dest.mkdir(parents=True)
                (dest / "SKILL.md").write_text("user skill")
                with mock.patch.dict(os.environ, {"USERPROFILE": home}), \
                     mock.patch.object(install.urllib_request, "urlopen", return_value=io.BytesIO(data.getvalue())):
                    self.assertFalse(install.setup_caveman("copilot", force=True))
                self.assertEqual((dest / "SKILL.md").read_text(), "user skill")
                self.assertFalse((Path(home) / ".copilot/escape").exists())

    def test_install_writes_custom_agents(self) -> None:
        kiro = json.loads((self.impl / ".kiro/agents/codd.json").read_text(encoding="utf-8"))
        self.assertEqual(kiro["name"], "codd")
        self.assertIn("skill://~/.kiro/skills/caveman/SKILL.md", kiro["resources"])
        self.assertFalse((self.impl / ".statemachine/codd/skills").exists())
        self.assertFalse((self.impl / ".kiro/skills").exists())
        self.assertFalse((self.impl / ".github/skills").exists())
        self.assertIn("必ず codd のステートマシン", kiro["prompt"])
        self.assertEqual(kiro["hooks"]["agentSpawn"][0]["command"], "python3 .statemachine/codd/codd.py show")
        copilot = (self.impl / ".github/agents/codd.agent.md").read_text(encoding="utf-8")
        self.assertTrue(copilot.startswith("---\nname: codd\ndescription: "))
        self.assertIn("必ず codd のステートマシン", copilot)
        # 応答の長さに上限があるエージェントでも止まらないよう、全文を貼らず分けて書くことを指示する。
        self.assertIn("the response hit the length limit", copilot)
        self.assertIn("codd.py summary", copilot)
        # 「チェックして」は、本流とは別のレビューのステートマシンで回す（直さない）。
        self.assertIn("## 確かめる・次の計画", copilot)
        self.assertIn(".statemachine/codd-review/workflow.yaml", copilot)
        self.assertIn("file://.statemachine/codd-review/workflow.yaml", kiro["resources"])
        # 定期実行の道具が 1 つのタスクとして並べられるよう、本流と別のフォルダに置く
        self.assertTrue((self.impl / ".statemachine/codd-review/workflow.yaml").is_file())
        self.assertTrue((self.impl / ".statemachine/codd-review/actions/ahead.md").is_file())
        self.assertFalse((self.impl / ".statemachine/codd/review.yaml").exists())
        # 変える段を渡すサブエージェント。モデルは書かない（呼び出し元と同じモデルで動かす）。
        self.assertIn("agents: [codd-apply, codd-graph]", copilot)
        graph_worker = (self.impl / ".github/agents/codd-graph.agent.md").read_text(encoding="utf-8")
        self.assertTrue(graph_worker.startswith("---\nname: codd-graph\n"))
        self.assertIn("codd.py graph --chunk", graph_worker)
        self.assertEqual(json.loads((self.impl / ".kiro/agents/codd-graph.json").read_text(encoding="utf-8"))["name"],
                         "codd-graph")
        worker = (self.impl / ".github/agents/codd-apply.agent.md").read_text(encoding="utf-8")
        self.assertTrue(worker.startswith("---\nname: codd-apply\n"))
        self.assertIn("codd.py batch --worker", worker)
        kiro_worker = json.loads((self.impl / ".kiro/agents/codd-apply.json").read_text(encoding="utf-8"))
        self.assertEqual(kiro_worker["name"], "codd-apply")
        self.assertNotIn("model", kiro_worker)
        self.assertNotIn("model:", worker.split("---")[1])
        # エージェントのファイルはマシンの一部なので、影響範囲や変えたファイルに数えない。
        self.run_pa(self.impl, "impact", "--term", "statemachine")
        self.assertNotIn("codd.agent.md", (self.impl / ".codd/impact.md").read_text(encoding="utf-8"))
        other = self.tmp / "other"
        other.mkdir()
        git(other, "init", "-q", "-b", "main")
        init.init_repo(other, "impl", ["../design"], discover=False, agents=())
        self.assertFalse((other / ".kiro").exists())
        self.assertFalse((other / ".github").exists())

    def test_init_keeps_the_links_found_before(self) -> None:
        # 見つけたつながりの控えは codd.json と同じく、置き直しても消さない（スクリプトだけ入れ替える）
        links = self.impl / ".statemachine/codd/links.json"
        links.write_text('{"links": []}\n', encoding="utf-8")
        (self.impl / ".statemachine/codd/old.py").write_text("", encoding="utf-8")
        init.init_repo(self.impl, None, None, discover=False)
        self.assertEqual(links.read_text(encoding="utf-8"), '{"links": []}\n')
        self.assertFalse((self.impl / ".statemachine/codd/old.py").exists())

    def test_init_uses_only_explicit_check(self) -> None:
        cfg = self.impl / ".statemachine/codd/codd.json"
        self.assertNotIn("check", json.loads(cfg.read_text(encoding="utf-8")))
        webui = ("serve: { command: npm start, url: http://localhost:3000 }\ncheck:\n  cases: [tests/e2e]\n"
                 "envs: { local: {} }\n")
        # 既にある codd.json には、あとから置いた webui-test の設定を勝手に書き足さない。
        (self.impl / "webui-test.config.yaml").write_text(webui, encoding="utf-8")
        init.init_repo(self.impl, None, None, discover=False)
        self.assertNotIn("check", json.loads(cfg.read_text(encoding="utf-8")))
        # 初めて置くときも、ほかの道具の設定からコマンドを推測しない。
        app = self.tmp / "app"
        app.mkdir()
        git(app, "init", "-q", "-b", "main")
        (app / "webui-test.config.yaml").write_text(webui, encoding="utf-8")
        init.init_repo(app, "impl", ["../design"], discover=False)
        self.assertNotIn("check", json.loads((app / ".statemachine/codd/codd.json").read_text(encoding="utf-8")))
        init.init_repo(self.impl, None, None, discover=False, check="webui-test check")
        r = self.run_pa(self.impl, "show")
        self.assertIn("変えたあとに実行するもの", r.stdout)
        self.assertIn("自分の検査: webui-test check", r.stdout)
        # 手で書いた検査は上書きしない。--check で書き換え、"" で消す。
        init.init_repo(self.impl, None, None, discover=False, check="python3 -m pytest -q")
        self.assertEqual(json.loads(cfg.read_text(encoding="utf-8"))["check"], ["python3", "-m", "pytest", "-q"])
        init.init_repo(self.impl, None, None, discover=False)
        self.assertEqual(json.loads(cfg.read_text(encoding="utf-8"))["check"], ["python3", "-m", "pytest", "-q"])
        init.init_repo(self.impl, None, None, discover=False, check="")
        self.assertNotIn("check", json.loads(cfg.read_text(encoding="utf-8")))
        # 単体テストは webui-test ではなく codd の test に書く。--test で書き、"" で消す。
        init.init_repo(self.impl, None, None, discover=False, test="npm test")
        self.assertEqual(json.loads(cfg.read_text(encoding="utf-8"))["test"], ["npm", "test"])
        self.assertIn("自分のテスト: npm test", self.run_pa(self.impl, "show").stdout)
        init.init_repo(self.impl, None, None, discover=False, test="")
        self.assertNotIn("test", json.loads(cfg.read_text(encoding="utf-8")))
        # 繰り返して渡すと、書いた順に動かすコマンドの並びになる（check も同じ）。
        init.init_repo(self.impl, None, None, discover=False, test=["npm test", "npm run e2e"],
                       check=["npm run lint", "npm run build"])
        cfg_now = json.loads(cfg.read_text(encoding="utf-8"))
        self.assertEqual(cfg_now["test"], [["npm", "test"], ["npm", "run", "e2e"]])
        self.assertEqual(cfg_now["check"], [["npm", "run", "lint"], ["npm", "run", "build"]])

    def test_verify_apply_needs_a_verified_plan(self) -> None:
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("計画を検査したときの印がありません", r.stderr)
        self.assert_plan_ok()
        self.assertTrue((self.impl / ".codd/before.json").is_file())

    # ------------------------------------------------------------ 設定・設置・定義

    def test_missing_ref_is_reported(self) -> None:
        cfg = self.impl / ".statemachine/codd/codd.json"
        cfg.write_text(json.dumps({"side": "impl", "refs": [{"path": "../nowhere"}]}), encoding="utf-8")
        r = self.run_pa(self.impl, "explore", "--term", "x")
        self.assertEqual(r.returncode, 2)
        self.assertIn("refs を直してください", r.stderr)

    def test_install_is_idempotent_and_replaces_old_files(self) -> None:
        stale = self.impl / ".statemachine/codd/actions/old.md"
        stale.write_text("old", encoding="utf-8")
        init.init_repo(self.impl, None, None)
        self.assertFalse(stale.exists())
        cfg = json.loads((self.impl / ".statemachine/codd/codd.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg, {"side": "impl", "refs": [{"path": "../design"}], "guides": [], "graphify": "auto"})
        # 既にある codd.json は、手で書いた形のまま残す（並べ直し・書き足しもしない）。
        path = self.impl / ".statemachine/codd/codd.json"
        hand = ('{"side": "impl", "refs": [{"name": "design", "path": "../design", "guides": [{"use": "docs/r.md"}],'
                ' "scope": ["docs"]}],\n "graphify": "auto"}\n')
        path.write_text(hand, encoding="utf-8")
        init.init_repo(self.impl, None, None)
        self.assertEqual(path.read_text(encoding="utf-8"), hand)
        init.init_repo(self.impl, "impl", ["../design"])  # 同じ値を渡しても書き直さない
        self.assertEqual(path.read_text(encoding="utf-8"), hand)
        # --ref で参照先を入れ替えても、同じ名前の参照先に手で書いた guides・scope は残す。
        init.init_repo(self.impl, None, ["design=../design-v2"])
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["refs"],
                         [{"name": "design", "path": "../design-v2", "guides": [{"use": "docs/r.md"}], "scope": ["docs"]}])
        self.assertEqual((self.impl / ".gitignore").read_text(encoding="utf-8").splitlines().count(".codd/"), 1)
        self.assertEqual((self.impl / ".graphifyignore").read_text(encoding="utf-8").splitlines(),
                         [".statemachine/codd/", ".statemachine/codd-review/", ".plans/", ".github/", ".kiro/"])
        fresh = self.tmp / "fresh"
        fresh.mkdir()
        git(fresh, "init", "-q")
        init.init_repo(fresh, "impl", [], discover=False, agents=())   # 設計書のフォルダも無ければ、文書を持たないリポジトリ
        self.assertEqual(json.loads((fresh / ".statemachine/codd/codd.json").read_text(encoding="utf-8"))["refs"], [])

    def test_graph_leaves_out_folders_no_side_takes_care_of(self) -> None:
        # エージェントの設定（隠しフォルダ）と、自分も参照先も受け持たないフォルダを graphify の索引に入れない
        mono = self.tmp / "mono"
        for rel in ("src/a.py", "docs/a.md", "tools/x.py", ".agents/skill.md"):
            (mono / rel).parent.mkdir(parents=True, exist_ok=True)
            (mono / rel).write_text("x\n", encoding="utf-8")
        git(mono, "init", "-q")
        init.init_repo(mono, "impl", None, scope=["src"], ref_scopes=["docs=docs"], discover=False, agents=())
        lines = (mono / ".graphifyignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".agents/", lines)
        self.assertIn("tools/", lines)
        self.assertNotIn("src/", lines)
        self.assertNotIn("docs/", lines)

    # ------------------------------------------------------------ 参照先が複数

    def add_second_ref(self) -> Path:
        api = self.tmp / "api"
        api.mkdir()
        git(api, "init", "-q", "-b", "main")
        commit(api, {"docs/api.md": "# API\n\n## hello\n\nHTTP でも hello を返す。\n\n実装は `src/app.py`（`src/` の下）。\n",
                     "spec/hello.md": "# hello\n"}, "init")
        init.init_repo(api, "design", ["../impl"])
        self.set_check(api, [sys.executable, "-c", "print('api ok')"])
        init.init_repo(self.impl, None, ["design=../design", "api=../api"])
        return api

    def test_explore_searches_every_ref(self) -> None:
        self.add_second_ref()
        r = self.run_pa(self.impl, "explore", "--term", "hello")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("FOUND 3 files (graphify: design=not-installed, api=not-installed)", r.stdout)
        report = (self.impl / ".codd/explore.md").read_text(encoding="utf-8")
        for line in ("- design:docs/api.md", "- api:docs/api.md", "- api:spec/hello.md"):
            self.assertIn(line, report)
        r = self.run_pa(self.impl, "explore", "--term", "hello", "--ref", "api")
        self.assertIn("FOUND 2 files (graphify: api=not-installed)", r.stdout)
        self.assertEqual(self.run_pa(self.impl, "explore", "--term", "x", "--ref", "nope").returncode, 2)

    def test_citations_name_the_ref_when_ambiguous(self) -> None:
        self.add_second_ref()
        # docs/api.md は両方の参照先にあるので、名前なしでは決まらない。
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("`名前:パス` で書いてください", r.stderr)
        self.write_plan(PLAN_ALIGNED.replace("docs/api.md#hello", "design:docs/api.md#hello")
                        .replace("docs/api.md:3", "api:docs/api.md:3")
                        .replace("## 参照先のその他\n\nなし", "## 参照先のその他\n\n- 見出しだけ（根拠: api:spec/hello.md）"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 0, r.stderr)
        # spec/hello.md は api にしか無いので、名前なしでよい。
        self.write_plan(PLAN_ALIGNED.replace("docs/api.md#hello", "spec/hello.md")
                        .replace("docs/api.md:3", "design:docs/api.md:3")
                        .replace("## 参照先のその他\n\nなし", "## 参照先のその他\n\n- HTTP の口（根拠: api:docs/api.md）"))
        self.assert_plan_ok()

    def test_verify_apply_checks_each_ref_against_the_plan(self) -> None:
        api = self.add_second_ref()
        plan = (PLAN_DRIFT.replace("（根拠: docs/api.md）", "（根拠: design:docs/api.md）")
                .replace("docs/api.md:3", "design:docs/api.md:3")
                .replace("- docs/api.md — `hello`", "- api:docs/api.md — `hello`")
                .replace("- docs/api.md — 文書の書式", "- api:docs/api.md — 文書の書式")
                .replace("## 参照先のその他\n\n- なし", "## 参照先のその他\n\n- 見出しだけ（根拠: api:spec/hello.md）"))
        self.write_plan(plan)
        self.assert_plan_ok()
        self.run_pa(self.impl, "explore", "--term", "hello")
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        # 計画は api を変える。design を変えてしまい、api を変えていない。
        (self.design / "docs/api.md").write_text("勝手に変えた\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("api を変えるはずなのに、api が変わっていません", r.stderr)
        self.assertIn("参照先の変更案に design は無いのに、design が変わっています", r.stderr)

        git(self.design, "checkout", "--", ".")
        (api / "docs/api.md").write_text("# API\n\n## hello\n\nHTTP でも 2 を返す。\n\n実装は `src/app.py`（`src/` の下）。\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("refs=api", r.stdout)

    def test_doc_format_is_a_rule_to_keep(self) -> None:
        # 文書の今の書式（見出しの並び）はコードの決まりと同じ。守る決まりに見本を挙げ、変えたあとも守る。
        self.write_plan(PLAN_DRIFT.replace("- docs/api.md — 文書の書式: 今の見出しの並び（hello の節）と書き方を保つ", "なし"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("「未判断」として書き足しました", r.stderr)
        # 見本と見出しの並びを守る決まりに書き足す（formats.md を開かなくても守り方を書ける）
        self.assertIn("- design:docs/api.md — 未判断: design:docs/api.md の書式の見本（見出し: hello）",
                      (self.impl / PLAN).read_text(encoding="utf-8"))
        self.assertIn("  - ## hello", (self.impl / ".codd/formats.md").read_text(encoding="utf-8"))
        self.write_plan(PLAN_DRIFT)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.design / "docs/api.md").write_text("# API\n\nhello は 2 を返す。\n\n実装は `src/app.py`（`src/` の下）。\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("文書の書式（見出しの並び）が今の書式から外れています: design:docs/api.md — ## hello", r.stderr)
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。\n\n実装は `src/app.py`（`src/` の下）。\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertNotIn("文書の書式", r.stderr)
        # 見出しを変えると計画に `## 見出し` と書いたなら、その見出しは外れてよい。
        self.write_plan(PLAN_DRIFT.replace("と書き直す", "と書き直し、`## hello` を `## hello()` にする"))
        self.assert_plan_ok()
        (self.design / "docs/api.md").write_text("# API\n\n## hello()\n\nhello は 2 を返す。\n\n実装は `src/app.py`（`src/` の下）。\n", encoding="utf-8")
        self.assertNotIn("文書の書式", self.run_pa(self.impl, "verify-apply").stderr)

    def test_new_doc_follows_the_format_of_its_folder(self) -> None:
        commit(self.design, {
            "docs/screens/login.md": "# ログイン\n\n## 目的\n\nx\n\n## 画面\n\nx\n\n## 入力\n\nx\n",
            "docs/screens/home.md": "# ホーム\n\n## 目的\n\nx\n\n## 画面\n\nx\n\n## 操作\n\nx\n",
            "docs/screens/README.md": "# 画面の一覧\n\n## 一覧\n",
        }, "screens")
        plan = PLAN_DRIFT.replace("- docs/api.md — `hello`", "- docs/screens/hello.md — 新しく書く。`hello`")
        self.write_plan(plan)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("design:docs/screens/home.md — 未判断: design:docs/screens/hello.md の書式の見本（見出し: 目的 / 画面）",
                      (self.impl / PLAN).read_text(encoding="utf-8"))
        formats = (self.impl / ".codd/formats.md").read_text(encoding="utf-8")
        self.assertIn("  - ## 目的\n  - ## 画面\n", formats)
        self.assertNotIn("## 一覧", formats)   # 索引の README は見本にしない
        self.write_plan(plan.replace("## 守る決まり\n\n- docs/api.md", "## 守る決まり\n\n- docs/screens/login.md — 書式\n- docs/api.md"))
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.design / "docs/screens/hello.md").write_text("# hello\n\n## 画面\n\nx\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertIn("docs/screens/hello.md — ## 目的", r.stderr)

    def write_evidence(self, items: list[dict]) -> None:
        cfg = self.impl / ".statemachine/codd/codd.json"
        config = json.loads(cfg.read_text(encoding="utf-8"))
        config["evidence"] = ["results/ui-evidence.json"]
        cfg.write_text(json.dumps(config), encoding="utf-8")
        out = self.impl / "results"
        out.mkdir(exist_ok=True)
        (out / ".gitignore").write_text("*\n", encoding="utf-8")
        (out / "ui-evidence.json").write_text(json.dumps({"version": 1, "root": "..", "items": items}, ensure_ascii=False), encoding="utf-8")

    def test_evidence_requires_explicit_paths_and_preserves_settings(self) -> None:
        cfg = self.impl / ".statemachine/codd/codd.json"
        out = self.impl / "results"
        out.mkdir()
        (out / "ui-evidence.json").write_text(json.dumps({"items": self.evidence_items(850)}), encoding="utf-8")
        # ファイルがあっても、パスを指定するまで読み込まない。
        self.assertIn("evidence）がありません", self.run_pa(self.impl, "evidence").stdout)
        r = subprocess.run([sys.executable, str(TOOL / "init.py"), str(self.impl), "--no-discover-rules",
                            "--no-agents", "--evidence", "results/ui-evidence.json", "--evidence", "other/*.json"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("自分: 2 件（results/ui-evidence.json）", self.run_pa(self.impl, "evidence").stdout)
        init.init_repo(self.impl, None, None, discover=False)
        self.assertEqual(json.loads(cfg.read_text())["evidence"], ["results/ui-evidence.json", "other/*.json"])
        init.init_repo(self.impl, None, None, discover=False, evidence=[""])
        self.assertEqual(json.loads(cfg.read_text())["evidence"], [])
        self.assertIn("evidence）がありません", self.run_pa(self.impl, "evidence").stdout)

    def evidence_items(self, load: int, status: str = "passed") -> list[dict]:
        base = {"file": "tests/login.yaml", "doc": ["design:docs/api.md"]}
        return [{"id": "login/S-01", "kind": "behavior", "title": "ログイン できる", "status": status, **base},
                {"id": "login/S-01/load", "kind": "metric", "title": "ログイン（読み込み）", "value": load, "unit": "ms", **base}]

    def test_test_evidence_is_written_into_docs_and_kept_current(self) -> None:
        # テストで得たもの（振る舞い・時間）を文書の印で写し、今と違えば・目安を超えれば止める。
        commit(self.design, {"docs/api.md": "# API\n\n## hello\n\nhello は 1 を返す。\n\n"
                             "読み込み: <!-- evidence: login/S-01/load max=1000 -->800 ms<!-- /evidence -->\n\n"
                             "確かめた振る舞い:<!-- evidence: login/* --><!-- /evidence -->\n"}, "marks")
        self.write_evidence(self.evidence_items(850))
        r = self.run_pa(self.impl, "evidence")
        self.assertEqual(r.returncode, 1, r.stdout)       # 一覧の印はまだ空
        self.assertIn("自分: 2 件", r.stdout)
        self.assertIn("docs/api.md:9 login/*（書いてある 空 → 今 - ✓ ログイン できる - 850 ms）", r.stdout)
        self.assertNotIn("login/S-01/load（", r.stdout)    # 2 割までの揺れは同じとみなす
        r = self.run_pa(self.impl, "evidence", "--write", "design:docs/api.md")
        self.assertEqual(r.returncode, 0, r.stdout)
        text = (self.design / "docs/api.md").read_text(encoding="utf-8")
        self.assertIn("-->\n- ✓ ログイン できる\n- 850 ms\n<!--", text)
        self.assertIn("-->800 ms<!--", text, "揺れの幅の中なら書き換えない")
        self.write_evidence(self.evidence_items(1300, "failed"))
        r = self.run_pa(self.impl, "evidence")
        self.assertEqual(r.returncode, 1)
        self.assertIn("1300 ms で、目安の 1000 を超えています", r.stdout)
        self.assertIn("確かめた振る舞いが失敗しています: ログイン できる", r.stdout)
        self.assertIn("書いてある 800 ms → 今 1300 ms", r.stdout)

    def test_evidence_examples_in_code_fences_are_not_real_marks(self) -> None:
        # 書き方の例（コードブロック・`…` の中の印）は、一覧・検査・書き戻しのどれにも数えない。
        example = ("```markdown\n"
                   "読み込み: <!-- evidence: login/S-01/load max=10 -->999 ms<!-- /evidence -->\n"
                   "無い id: <!-- evidence: nothing/here -->例<!-- /evidence -->\n"
                   "閉じ忘れの例: <!-- evidence: login/S-01/load -->\n"
                   "```\n")
        inline = "書き方: `<!-- evidence: inline/only -->値<!-- /evidence -->`\n"
        commit(self.design, {"docs/api.md": "# API\n\n## hello\n\n" + example + inline +
                             "\n読み込み: <!-- evidence: login/S-01/load -->1 ms<!-- /evidence -->\n"}, "marks")
        self.write_evidence(self.evidence_items(850))
        r = self.run_pa(self.impl, "evidence")
        self.assertIn("文書の印: 1 件", r.stdout)                    # 一覧: 本物の 1 つだけ
        self.assertNotIn("nothing/here", r.stdout)                   # 検査: 例の知らない id で落とさない
        self.assertNotIn("inline/only", r.stdout)
        self.assertNotIn("目安の 10 を超えています", r.stdout)       # 例の目安で落とさない
        self.assertIn("docs/api.md:12 login/S-01/load（書いてある 1 ms → 今 850 ms）", r.stdout)
        r = self.run_pa(self.impl, "evidence", "--write")
        self.assertIn("写し直した: design:docs/api.md", r.stdout)
        text = (self.design / "docs/api.md").read_text(encoding="utf-8")
        self.assertIn(example + inline, text)                        # 書き戻し: 例はそのまま
        self.assertIn("\n読み込み: <!-- evidence: login/S-01/load -->850 ms<!-- /evidence -->\n", text)
        self.assertEqual(self.run_pa(self.impl, "evidence").returncode, 0)

    def setup_changed_outputs(self) -> tuple[bytes, bytes]:
        """文書の側に、テストの出力（前の版）の写しを貼った文書を置く。計画のあとで change_outputs を呼ぶと、
        テストの出力が新しい版になる（計画のときから変わったものだけが、この回の変化）。"""
        old, new, gone = b"PNG-v1 login", b"PNG-v2 login", b"PNG-old menu"
        sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
        commit(self.design, {"docs/login.md": "# ログイン\n\n![ログイン](images/login.png)\n",
                             "docs/menu.md": "# メニュー\n\n<img src=\"images/menu.png\">\n"}, "outputs")
        (self.design / "docs/images").mkdir(parents=True, exist_ok=True)
        (self.design / "docs/images/login.png").write_bytes(old)
        (self.design / "docs/images/menu.png").write_bytes(gone)
        git(self.design, "add", "-A")
        git(self.design, "commit", "-q", "-m", "copies")
        outputs = self.impl / "results/out"
        outputs.mkdir(parents=True)
        (outputs / "login.png").write_bytes(old)
        (outputs / "menu.png").write_bytes(gone)
        # 種類の名前は道具が決める（codd は sha256 を持つ項目をテストが作ったファイルとして扱う）
        base = {"kind": "snapshot", "file": "tests/login.yaml"}
        self.write_evidence([
            {**base, "id": "login/S-01/login", "status": "same", "path": "results/out/login.png",
             "sha256": sha(old), "history": [sha(old)]},
            {**base, "id": "menu/S-01/menu", "status": "same", "path": "results/out/menu.png",
             "sha256": sha(gone), "history": [sha(gone)]},
        ])

        def change() -> None:
            (outputs / "login.png").write_bytes(new)
            (outputs / "login-diff.png").write_bytes(b"diff")
            self.write_evidence([
                {**base, "id": "login/S-01/login", "status": "changed", "path": "results/out/login.png",
                 "sha256": sha(new), "history": [sha(new), sha(old)], "diff": "results/out/login-diff.png"},
                {**base, "id": "menu/S-01/menu", "status": "removed", "path": "results/out/menu.png",
                 "sha256": sha(gone), "history": [sha(gone)]},
            ])
        self.change_outputs = change
        return old, new

    def test_unforeseen_output_changes_stop_instead_of_replacing_copies(self) -> None:
        # 計画が見込んでいない出力の変化（巻き込み・崩れかもしれない）は、写しを黙って差し替えずに止めて訊く。
        old, new = self.setup_changed_outputs()
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('hello')\n    return 1\n", encoding="utf-8")
        self.change_outputs()
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("計画に無いテストの出力が変わりました", r.stderr)
        self.assertIn("docs/images/login.png ← login/S-01/login（貼っている文書: docs/login.md）（見比べる: results/out/login-diff.png）",
                      r.stderr)
        self.assertEqual((self.design / "docs/images/login.png").read_bytes(), old)
        advice = self.run_pa(self.impl, "advise").stdout
        self.assertNotIn("AUTO", advice)                          # 人に訊く
        self.assertIn("意図した変化として認め、写しを差し替える（勧め）", advice)
        # 利用者が意図した変化と認めたら、次の検査で差し替える
        self.assertEqual(self.run_pa(self.impl, "accept").returncode, 0)
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)   # ハーネスの差し替えは「計画に無い変更」に数えない
        self.assertEqual((self.design / "docs/images/login.png").read_bytes(), new)
        self.assertIn("テストの出力から: design:docs/images/login.png ← login/S-01/login（リンクしている文書: docs/login.md）", r.stdout)
        self.assertIn("docs/images/menu.png — テストが作らなくなった出力です（menu/S-01/menu）。リンクしている文書: docs/menu.md",
                      r.stdout)
        report = self.report(self.impl)
        self.assertEqual(report.returncode, 0, report.stdout)
        self.assertIn("## テストの出力から差し替えたファイル", report.stdout)
        # もう一度検査しても、差し替えた写しは今の版なので何もしない
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 0)

    def test_output_changes_of_planned_tests_replace_copies(self) -> None:
        # 計画のテスト（テストの変更案）が作る出力の変化は、見込んだ変化なので訊かずに差し替える。
        commit(self.impl, {"tests/login.yaml": "suite: ログイン\n"}, "case")
        _, new = self.setup_changed_outputs()
        plan = PLAN_ALIGNED.replace("- 変更不要: このリポジトリにテストはまだ無い（例の小さなリポジトリ）",
                                    "- tests/login.yaml — `hello` のログを確かめる手順を足す")
        self.write_plan(plan)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('hello')\n    return 1\n", encoding="utf-8")
        (self.impl / "tests/login.yaml").write_text("suite: ログイン\n# hello のログ\n", encoding="utf-8")
        self.change_outputs()
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.design / "docs/images/login.png").read_bytes(), new)

    def test_copies_stale_before_this_run_are_reported_not_stopped(self) -> None:
        # 計画のときから出力が変わっていない項目の古い写しは、この回が起こした変化ではない。止めず、差し替えず、知らせる。
        old, new = self.setup_changed_outputs()
        self.change_outputs()   # 計画の前から、出力は新しい版
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('hello')\n    return 1\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.design / "docs/images/login.png").read_bytes(), old)
        self.assertIn("docs/images/login.png — この回の前から古い写しです（login/S-01/login。差し替えていません）", r.stdout)

    def test_outputs_are_not_replaced_from_a_failing_run(self) -> None:
        # テストか検査が落ちた回の出力では、見込んだ変化でも差し替えない（壊れた出力を文書に写さない）。
        commit(self.impl, {"tests/login.yaml": "suite: ログイン\n"}, "case")
        old, _ = self.setup_changed_outputs()
        plan = PLAN_ALIGNED.replace("- 変更不要: このリポジトリにテストはまだ無い（例の小さなリポジトリ）",
                                    "- tests/login.yaml — `hello` のログを確かめる手順を足す")
        self.write_plan(plan)
        self.assert_plan_ok()
        self.set_check(self.impl, [sys.executable, "-c", "raise SystemExit(1)"])
        (self.impl / "src/app.py").write_text("def hello():\n    print('hello')\n    return 1\n", encoding="utf-8")
        (self.impl / "tests/login.yaml").write_text("suite: ログイン\n# hello のログ\n", encoding="utf-8")
        self.change_outputs()
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertEqual((self.design / "docs/images/login.png").read_bytes(), old)

    def test_a_copy_of_a_current_output_is_never_taken_for_another_items_old_version(self) -> None:
        # A の今の出力と、B の前の版が同じ中身（同じ空の画面など）でも、A の写しを B の新しい版で上書きしない。
        same, other = b"BIN\0empty", b"BIN\0filled"
        sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
        commit(self.design, {"docs/a.md": "# A\n\n![a](images/a.bin)\n"}, "doc")
        (self.design / "docs/images").mkdir(parents=True, exist_ok=True)
        (self.design / "docs/images/a.bin").write_bytes(same)
        git(self.design, "add", "-A")
        git(self.design, "commit", "-q", "-m", "copy")
        out = self.impl / "results/out"
        out.mkdir(parents=True)
        (out / "a.bin").write_bytes(same)
        (out / "b.bin").write_bytes(same)
        items = lambda b_sha, b_hist: [  # noqa: E731
            {"id": "a", "path": "results/out/a.bin", "sha256": sha(same), "history": [sha(same)], "file": "tests/a.yaml"},
            {"id": "b", "path": "results/out/b.bin", "sha256": b_sha, "history": b_hist, "file": "tests/b.yaml"}]
        self.write_evidence(items(sha(same), [sha(same)]))
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('hello')\n    return 1\n", encoding="utf-8")
        (out / "b.bin").write_bytes(other)
        self.write_evidence(items(sha(other), [sha(other), sha(same)]))
        r = self.run_pa(self.impl, "verify-apply")
        self.assertNotIn("計画に無いテストの出力が変わりました", r.stderr)
        self.assertEqual((self.design / "docs/images/a.bin").read_bytes(), same)

    def test_failed_tests_show_their_reason_and_file(self) -> None:
        # 検査が落ちたら、テストが今回書いた結果から、落ちた振る舞いの理由と見るファイルを出す。
        self.write_evidence([{"kind": "behavior", "id": "old/S-09", "title": "前の回の失敗", "status": "failed",
                              "error": "前の回"}])
        os.utime(self.impl / "results/ui-evidence.json", (time.time() - 3600, time.time() - 3600))
        self.set_check(self.impl, [sys.executable, "-c", "raise SystemExit(1)"])
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('hello')\n    return 1\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("落ちたテスト（", r.stderr)   # 前の回に書かれたままの結果は、今の失敗と取り違えない
        write = ("import json; json.dump({'version': 1, 'root': '..', 'items': [{'kind': 'behavior', 'id': 'login/S-01', "
                 "'title': 'ログイン 正しい ID で進める', 'status': 'failed', 'error': 'Timeout: 商品一覧 が出ない', "
                 "'path': 'results/out/failure.png'}, {'kind': 'behavior', 'id': 'login/S-02', 'title': '通る', "
                 "'status': 'passed'}]}, open('results/ui-evidence.json', 'w')); "
                 "json.dump([{'file': 'x.js'}], open('results/lint.json', 'w')); raise SystemExit(1)")
        self.set_check(self.impl, [sys.executable, "-c", write])
        cfg = self.impl / ".statemachine/codd/codd.json"   # 結果の置き場に、配列の JSON（ほかの道具の出力）があっても読める
        cfg.write_text(json.dumps({**json.loads(cfg.read_text(encoding="utf-8")), "evidence": ["results/*.json"]}), encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("落ちたテスト（テストが書いた結果から。", r.stderr)
        self.assertIn("✗ ログイン 正しい ID で進める — Timeout: 商品一覧 が出ない（見る: results/out/failure.png）", r.stderr)
        failed = r.stderr.split("落ちたテスト（", 1)[1]
        self.assertNotIn("通る", failed)
        self.assertNotIn("前の回の失敗", failed)

    def test_tracked_test_outputs_are_not_files_to_fix(self) -> None:
        # 結果ファイルと撮った画面を git で管理していても、直すファイル・影響範囲・計画に無い変更に数えない。
        old, new = b"PNG\0v1 hello", b"PNG\0v2 hello"
        sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
        (self.impl / "results/screens").mkdir(parents=True)
        (self.impl / "results/screens/hello.png").write_bytes(old)
        self.write_evidence([{"kind": "image", "id": "hello/screen", "title": "hello の画面", "path": "results/screens/hello.png",
                              "sha256": sha(old), "history": [sha(old)], "file": "tests/hello.yaml"}])
        (self.impl / "results/.gitignore").unlink()
        git(self.impl, "add", "-A")
        git(self.impl, "commit", "-q", "-m", "results")
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertNotIn("results/", r.stderr + (self.impl / PLAN).read_text(encoding="utf-8"))
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('hello')\n    return 1\n", encoding="utf-8")
        (self.impl / "results/screens/hello.png").write_bytes(new)   # テストを動かすと撮り直す
        self.write_evidence([{"kind": "image", "id": "hello/screen", "title": "hello の画面", "path": "results/screens/hello.png",
                              "sha256": sha(new), "history": [sha(new), sha(old)], "file": "tests/hello.yaml"}])
        (self.impl / "results/.gitignore").unlink()
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("results/", r.stderr)

    def test_report_keeps_planned_tests_in_the_test_section(self) -> None:
        commit(self.impl, {"tests/test_app.py": "from src.app import hello\nassert hello() == 1\n"}, "test")
        plan = PLAN_ALIGNED.replace("- 変更不要: このリポジトリにテストはまだ無い（例の小さなリポジトリ）",
                                    "- tests/test_app.py — `hello` のログも確かめる")
        self.write_plan(plan)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('hi')\n    return 1\n", encoding="utf-8")
        (self.impl / "tests/test_app.py").write_text("from src.app import hello\nassert hello() == 1  # log\n", encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 0)
        report = self.report(self.impl).stdout
        self.assertNotIn("tests/test_app.py — 変えた（影響範囲）", report)   # テストの変更案のものは「テスト」の節だけに
        self.assertIn("## テスト\n\n- tests/test_app.py — 変えた", report)
        self.assertNotIn("- src/app.py — 直した", report)                 # 変更案で変えたファイル自身は影響範囲に出さない

    def test_a_test_moved_to_a_new_path_counts_as_planned(self) -> None:
        # 「…に移す」と書いたテストの移し先も、自分の変更案と同じく計画に挙げたファイルに数える。
        commit(self.impl, {"tests/test_app.py": "from src.app import hello\nassert hello() == 1\n"}, "test")
        plan = PLAN_ALIGNED.replace("- 変更不要: このリポジトリにテストはまだ無い（例の小さなリポジトリ）",
                                    "- tests/test_app.py — tests/test_hello.py に移し、`hello` のログも確かめる")
        self.write_plan(plan)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('hi')\n    return 1\n", encoding="utf-8")
        (self.impl / "tests/test_app.py").rename(self.impl / "tests/test_hello.py")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_adding_a_new_doc_with_its_screenshot_needs_no_invented_impact(self) -> None:
        # 足すだけの変更: 影響範囲は理由だけの「変更不要」でよく、計画の文書に貼った画像は文書の添付として認める。
        # 前からある見出し（`## hello`）を写しただけなら、変わった名前に数えない。
        plan = (PLAN_DRIFT
                .replace("- docs/api.md — `hello` の戻り値を 2 と書き直す",
                         "- docs/api.md — `hello` の戻り値を 2 と書き直す\n- docs/hello2.md — `hello` の画面の仕様書を足す")
                .replace("- src/app.py — hello の戻り値", "- 変更不要: 文書を足すだけで、今あるファイルの振る舞いは変わらない")
                .replace("- 変更不要: このリポジトリにテストはまだ無い（例の小さなリポジトリ）",
                         "- 変更不要: このリポジトリにテストはまだ無い（例の小さなリポジトリ）\n"
                         "- `hello の画面` — 変更不要: 仕様書の題名で、確かめる振る舞いは無い"))
        self.write_plan(plan)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。\n\n実装は `src/app.py`（`src/` の下）。\n", encoding="utf-8")
        (self.design / "docs/images").mkdir(parents=True, exist_ok=True)
        (self.design / "docs/images/hello2.png").write_bytes(b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR hello2")
        (self.design / "docs/hello2.md").write_text("# hello の画面\n\n## hello\n\n![画面](images/hello2.png)\n",
                                                   encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("docs/images/hello2.png — 変えた（計画の文書がリンクしているファイル）", self.report(self.impl).stdout)

    def test_plan_must_handle_docs_showing_results_of_affected_tests(self) -> None:
        commit(self.impl, {"tests/login.yaml": "suite: ログイン\n"}, "case")
        commit(self.design, {"docs/perf.md": "# 性能\n\n- <!-- evidence: login/S-01/load -->800 ms<!-- /evidence -->\n"},
               "perf")
        self.write_evidence(self.evidence_items(800))
        plan = PLAN_ALIGNED.replace("- 変更不要: このリポジトリにテストはまだ無い（例の小さなリポジトリ）",
                                    "- tests/login.yaml — `hello` のログも確かめる")
        self.write_plan(plan)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("変更が響くテストの結果を写している文書が、計画にありません", r.stderr)
        self.assertIn("docs/perf.md", r.stderr)
        evidence = (self.impl / ".codd/evidence.md").read_text(encoding="utf-8")
        self.assertIn("login/S-01/load — metric: 800 ms", evidence)
        self.write_plan(plan.replace("## 参照先のその他\n\nなし",
                                     "## 参照先のその他\n\n- 読み込みの時間を写している（根拠: docs/perf.md）"))
        self.assert_plan_ok()
        # 変えたあと、時間が大きく変わったら写し直させ、報告に変化を出す。
        (self.impl / "src/app.py").write_text("def hello():\n    print('hello')\n    return 1\n", encoding="utf-8")
        (self.impl / "tests/login.yaml").write_text("suite: ログイン（ログも）\n", encoding="utf-8")
        self.write_evidence(self.evidence_items(1600))
        r = self.run_pa(self.impl, "verify-apply")
        self.assertIn("文書に写したテストの結果が今と違います", r.stderr)
        self.assertIn("書いてある 800 ms → 今 1600 ms", r.stderr)
        self.run_pa(self.impl, "verify-plan")   # 練り直しても、比べるのは変える前に控えたもの
        report = self.report(self.impl).stdout
        self.assertIn("## テストで得たものの変化（計画のときと比べて）", report)
        self.assertIn("login/S-01/load — 800 → 1600 ms", report)

    def test_stale_marks_in_unrelated_docs_do_not_block_apply(self) -> None:
        # 計画に無い文書にもとからある古い印は、直すと計画に無いファイルを変えることになるので、ここでは止めない（lint が拾う）。
        commit(self.design, {"docs/other.md": "# other\n\n<!-- evidence: login/S-01/load -->100 ms<!-- /evidence -->\n"},
               "marks")
        self.write_evidence(self.evidence_items(850))
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 1 # changed\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("docs/other.md", self.run_pa(self.impl, "lint", "--no-test").stdout)

    def test_ref_change_to_a_new_file_is_allowed(self) -> None:
        plan = PLAN_DRIFT.replace("- docs/api.md — `hello`", "- docs/hello.md — 新しく書く。`hello`")
        self.write_plan(plan)
        self.assertEqual(self.run_pa(self.impl, "verify-plan").returncode, 0)

    def test_legacy_config_is_rewritten_by_init(self) -> None:
        # 前の版の書き方は読まずに止め、init.py で置き直すと guides と refs に書き換える。
        cfg = self.impl / ".statemachine/codd/codd.json"
        legacy = {"side": "impl", "ref_path": "../design", "rules": ["CLAUDE.md"],
                  "skills": {"plan": ["domain-modeler"], "apply": ["tdd", "domain-modeler"]},
                  "tools": {"plan": ["github"]}}
        cfg.write_text(json.dumps(legacy), encoding="utf-8")
        r = self.run_pa(self.impl, "show")
        self.assertEqual(r.returncode, 2)
        self.assertIn("前の版の書き方があります: ref_path, rules, skills, tools", r.stderr)
        init.init_repo(self.impl, None, None)
        self.assertEqual(json.loads(cfg.read_text(encoding="utf-8")),
                         {"side": "impl", "refs": [{"path": "../design"}],
                          "guides": [{"use": "skill:domain-modeler"}, {"use": "skill:tdd", "when": {"phase": "apply"}},
                                     {"use": "tool:github", "when": {"phase": "plan"}}, {"use": "CLAUDE.md"}]})
        self.assertIn("FOUND 1 files", self.run_pa(self.impl, "explore", "--term", "hello").stdout)
        # 参照先の項目（refs[].skills・refs[].rules）も書き換える。参照先のスキルは参照先を変えるとき（apply）に使う。
        cfg.write_text(json.dumps({"side": "impl", "refs": [{"path": "../design", "skills": ["spec-editor"],
                                                             "rules": ["docs/r.md"]}]}), encoding="utf-8")
        self.assertIn("refs[].skills", init.migrate_legacy(json.loads(cfg.read_text(encoding="utf-8"))))
        init.init_repo(self.impl, None, None)
        self.assertEqual(json.loads(cfg.read_text(encoding="utf-8"))["refs"],
                         [{"path": "../design", "guides": [{"use": "skill:spec-editor", "when": {"phase": "apply"}},
                                                           {"use": "docs/r.md"}]}])

    # ------------------------------------------------------------ 使うスキル

    def test_show_lists_skills_for_each_phase(self) -> None:
        cfg_path = self.impl / ".statemachine/codd/codd.json"
        self.set_config(self.impl, skills={"plan": ["domain-modeler"], "apply": ["tdd", "plugin:refactor"]})
        self.set_config(self.design, skills={"apply": ["doc-writer"]})
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

        r = self.run_pa(self.impl, "show")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("- design: 設計書", r.stdout)
        self.assertIn("使うスキルと道具（計画を練るとき）:\n  - 自分: `domain-modeler` スキル", r.stdout)
        self.assertIn("  - 自分: `tdd` スキル、`plugin:refactor` スキル", r.stdout)
        # 参照先を変えるときは、参照先に置いた codd.json の変えるときのスキル。
        self.assertIn("  - design を変えるとき: `doc-writer` スキル", r.stdout)

        # codd.json の refs[].guides に書けば、参照先の手順に足す（参照先が決めた手順は外せない）。
        cfg["refs"] = [{"name": "design", "path": "../design",
                        "guides": [{"use": "skill:spec-editor", "when": {"phase": "apply"}}]}]
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        r = self.run_pa(self.impl, "show", "--phase", "apply")
        self.assertIn("  - design を変えるとき: `doc-writer` スキル、`spec-editor` スキル", r.stdout)
        self.assertNotIn("計画を練るとき", r.stdout)

    def test_bad_skill_guides_are_reported(self) -> None:
        cfg_path = self.impl / ".statemachine/codd/codd.json"
        for guide in ({"use": "skill:bad name"}, {"use": "skill:tdd", "when": {"phase": "review"}}):
            with self.subTest(guide=guide):
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                cfg["guides"] = [guide]
                cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
                r = self.run_pa(self.impl, "show")
                self.assertEqual(r.returncode, 2)
                self.assertIn("guides[0]", r.stderr)

    # ------------------------------------------------------------ 根拠を箇所まで確かめる

    def test_evidence_points_at_real_lines_headings_and_names(self) -> None:
        cases = {
            "9 行目はありません": PLAN_ALIGNED.replace("docs/api.md:3", "docs/api.md:9"),
            "3-12 行目はありません": PLAN_ALIGNED.replace("docs/api.md:3", "docs/api.md:3-12"),
            "見出し #goodbye がありません": PLAN_ALIGNED.replace("docs/api.md#hello", "docs/api.md#goodbye"),
            "根拠のファイルに見当たりません: `hello_world`": PLAN_ALIGNED.replace(
                "- hello は整数を返す", "- `hello_world` は整数を返す"),
        }
        for expected, plan in cases.items():
            with self.subTest(expected=expected):
                self.write_plan(plan)
                r = self.run_pa(self.impl, "verify-plan")
                self.assertEqual(r.returncode, 1)
                self.assertIn(expected, r.stderr)
        # 参照先の名前を付けた根拠でも、そのファイルで名前を探す。見当たらなければ探したファイルを示す。
        self.write_plan(PLAN_ALIGNED.replace("- hello は整数を返す（根拠: docs/api.md#hello）",
                                             "- `hello_world` は整数を返す（根拠: design:docs/api.md:3）"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertIn("見当たりません: `hello_world`（探したファイル: design:docs/api.md。", r.stderr)
        self.write_plan(PLAN_ALIGNED.replace("- hello は整数を返す（根拠: docs/api.md#hello）",
                                             "- `hello()` は整数を返す（根拠: `design:docs/api.md:3`）"))
        self.assert_plan_ok()
        # 書かれている名前・実在する行と見出しなら通る（`hello()` は hello として探す）。
        self.write_plan(PLAN_ALIGNED.replace("- hello は整数を返す", "- `hello()` は整数を返す")
                        .replace("docs/api.md:3", "docs/api.md:3-5").replace("#hello", "#HELLO"))
        self.assert_plan_ok()

    def test_own_change_items_name_own_files(self) -> None:
        self.write_plan(PLAN_ALIGNED.replace("- src/app.py — `hello` の中でログを出す", "- ログを出す"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("自分の変更案の項目に、自分のリポジトリのパスがありません", r.stderr)
        # まだ無いファイル（親のフォルダはある）は書ける。
        self.write_plan(PLAN_ALIGNED.replace("- src/app.py — `hello` の中でログを出す",
                                             "- src/app.py — `hello` でログを出す\n- src/log.py — 新しく書く"))
        self.assert_plan_ok()

    def test_new_files_may_go_in_new_folders(self) -> None:
        # まだ無いフォルダの下にも、新しいファイルを作る計画を書ける（自分も参照先も）。途中がファイルのパスは認めない。
        self.write_plan(PLAN_ALIGNED.replace("- src/app.py — `hello` の中でログを出す",
                                             "- src/app.py — `hello` でログを出す\n- src/app.py/x.py — 新しく書く"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertIn("自分のリポジトリのパスがありません", r.stderr)
        self.assertIn("src/app.py/x.py", r.stderr)
        plan = self.with_tests(PLAN_ALIGNED.replace(
            "- src/app.py — `hello` の中でログを出す",
            "- src/app.py — `hello` の中でログを出す\n- lib/pay/core.py — 新しく作り、`pay` を置く"),
            "- `pay` — 変更不要: 例")
        self.write_plan(plan)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertNotIn("自分のリポジトリのパスがありません", r.stderr)
        self.assertIn("対応する文書の無いコードです", r.stderr)   # 新しいフォルダのコードにも文書が要る
        self.assertIn("lib/pay/core.py（近い粒度の見本: design:docs/api.md（src/app.py に対応））", r.stderr)
        plan = plan.replace("## 参照先の変更案\n\nなし",
                            "## 参照先の変更案\n\n- docs/pay/core.md — `lib/pay/core.py` の文書を見本に倣って新しく書き、`pay` の節を置く")
        plan = plan.replace("## 守る決まり\n\nなし",
                            "## 守る決まり\n\n- docs/api.md — 文書の書式: 見本。見出しの並びと書き方を docs/pay/core.md でも同じにする")
        self.write_plan(plan)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('hi')\n    return 1\n", encoding="utf-8")
        (self.impl / "lib/pay").mkdir(parents=True)
        (self.impl / "lib/pay/core.py").write_text("def pay():\n    return 0\n", encoding="utf-8")
        (self.design / "docs/pay").mkdir()
        (self.design / "docs/pay/core.md").write_text("# pay\n\n## pay\n\npay は 0 を返す。\n\n実装は `lib/pay/core.py`。\n",
                                                      encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)

    # ------------------------------------------------------------ 1 回で終わる大きさ

    def test_plan_must_fit_in_one_run(self) -> None:
        path = self.impl / ".statemachine/codd/codd.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg["max_files"] = 1
        path.write_text(json.dumps(cfg), encoding="utf-8")
        self.write_plan(PLAN_DRIFT)   # src/app.py と design の docs/api.md で 2 つ
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("1 回で変えるファイルが 2 あり、上限 1 を超えています", r.stderr)
        self.assertIn("## 今回やらないこと", r.stderr)
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        self.assertIn("1 回で変えるファイルの上限: 1", self.run_pa(self.impl, "show").stdout)
        tpl = PLAN_ALIGNED.replace("## 今回やらないこと\n\nなし\n", "")
        self.write_plan(tpl)
        self.assertIn("見出しがありません: ## 今回やらないこと", self.run_pa(self.impl, "verify-plan").stderr)

    # ------------------------------------------------------------ 計画に無い変更を止める

    def test_apply_is_split_into_batches(self) -> None:
        # 変えるファイルが多い計画は段に分けて変え、段ごとに変え残しを止める。全体の検査は最後の段で。
        self.set_config(self.impl, batch_files=1)
        self.write_plan(self.with_tests(PLAN_ALIGNED.replace(
            "- src/app.py — `hello` の中でログを出す", "- src/app.py — `hello` の中でログを出す\n- src/log.py — `log` を足す"),
            "- `log` — 変更不要: print を包むだけ"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("2 段に分ける", r.stdout)
        self.run_pa(self.impl, "decide", "OK")
        out = self.run_pa(self.impl, "batch").stdout
        self.assertIn("段 1/2", out)
        self.assertIn("- src/app.py", out)
        self.assertNotIn("- src/log.py", out)

        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("段 1/2 のファイルをまだ変えていません", r.stderr)
        self.assertIn("AUTO APPLY", self.run_pa(self.impl, "advise").stdout)   # 変え残しは訊かずにやり直す
        (self.impl / "src/app.py").write_text("def hello():\n    print('hi')\n    return 1\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("MORE "), r.stdout)
        self.assertEqual(r.stderr, "")       # 第 1 行（stderr を優先して読まれる）を MORE にする
        out = self.run_pa(self.impl, "batch").stdout
        self.assertIn("段 2/2（最後の段", out)
        self.assertIn("- src/log.py", out)

        # 最後の段は全体を検査する。
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("自分の変更案のファイルをまだ変えていません", r.stderr)
        (self.impl / "src/log.py").write_text("def log(m):\n    print(m)\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("OK "), r.stdout)

    def test_workflow_goes_to_the_short_step_for_the_next_batch(self) -> None:
        # 2 段目からは短い指示（apply_more）で変え、段ごとに読み直す量を抑える。
        text = (TOOL / "machine/workflow.yaml").read_text(encoding="utf-8")
        more = 'condition_rule: "equals:check_ok:true;startswith:check_output:MORE", priority: 1}'
        self.assertIn("{from: apply, to: apply_more, " + more, text)
        self.assertIn("{from: apply_more, to: apply_more, " + more, text)
        short = (TOOL / "machine/actions/apply-more.md").read_text(encoding="utf-8")
        self.assertLess(len(short), len((TOOL / "machine/actions/apply.md").read_text(encoding="utf-8")) / 3)

    def test_verify_apply_rejects_files_outside_the_plan(self) -> None:
        self.write_plan(PLAN_DRIFT)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.impl / "src/extra.py").write_text("x = 1\n", encoding="utf-8")
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。\n\n実装は `src/app.py`（`src/` の下）。\n", encoding="utf-8")
        (self.design / "docs/other.md").write_text("# other\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("計画に無いファイルを変えています", r.stderr)
        self.assertIn("src/extra.py", r.stderr)
        self.assertIn("design で参照先の変更案に無いファイルを変えています", r.stderr)
        self.assertIn("docs/other.md", r.stderr)
        (self.impl / "src/extra.py").unlink()
        (self.design / "docs/other.md").unlink()
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)

    # ------------------------------------------------------------ テストもコード・仕様書と同じに扱う

    TESTS_WAIVED = "- 変更不要: このリポジトリにテストはまだ無い（例の小さなリポジトリ）"

    def with_tests(self, plan: str, body: str) -> str:
        return plan.replace(self.TESTS_WAIVED, body)

    def test_plan_must_handle_tests_hit_by_the_change(self) -> None:
        # 名前で響く単体テストと、仕様書をパスで指している e2e のケース（同じ側のコードを指すものも）。
        commit(self.impl, {
            "tests/test_app.py": "from src.app import hello\n\ndef test_hello():\n    assert hello() == 1\n",
            "tests/e2e/hello.yaml": "# coherence: doc=docs/api.md\nsuite: hello\n",
            "tests/e2e/page.yaml": "# coherence: code=src/app.py\nsuite: page\n",
        }, "tests")
        # 手でテストを動かして出来た .pyc（.gitignore が無いと追跡外のファイルとして見える）はテストに数えない。
        pyc = self.impl / "tests/__pycache__/test_app.cpython-312.pyc"
        pyc.parent.mkdir()
        pyc.write_bytes(b"\0\0hello\0")
        self.write_plan(PLAN_DRIFT)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("「未判断」として書き足しました", r.stderr)
        self.assertNotIn("__pycache__", r.stderr)
        pyc.unlink()
        pyc.parent.rmdir()
        (self.impl / ".codd/run-start.json").unlink()   # 消した .pyc を、計画の段で変えたものに数えない
        plan = (self.impl / PLAN).read_text(encoding="utf-8")
        tests_part = plan.split("## テストの変更案", 1)[1].split("\n## ", 1)[0]
        for rel in ("tests/test_app.py", "tests/e2e/hello.yaml", "tests/e2e/page.yaml"):
            self.assertIn(rel, r.stderr)
            self.assertIn(f"- {rel} — 未判断", tests_part)   # テストはテストの変更案に（影響範囲には入れない）
            self.assertEqual(plan.count(f"- {rel} — "), 1)
        report = (self.impl / ".codd/tests.md").read_text(encoding="utf-8")
        self.assertIn("tests/test_app.py — 名前", report)
        self.assertIn("tests/e2e/hello.yaml — つながり", report)
        self.write_plan(self.with_tests(PLAN_DRIFT, "\n".join([
            "- tests/test_app.py — `hello` が 2 を返すことを確かめるように直す",
            "- tests/e2e/hello.yaml — 2 を確かめるように直す",
            "- tests/e2e/page.yaml — 変更不要: 戻り値を見ていない",
        ])))
        self.assert_plan_ok()

        # 変えたあと: 挙げたテストを変えていなければ落とし、単体テスト（test）も codd が実行する。
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。\n\n実装は `src/app.py`（`src/` の下）。\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("## テストの変更案 のテストをまだ変えていません", r.stderr)
        self.assertIn("tests/test_app.py", r.stderr)
        self.assertNotIn("直していないファイルがあります", r.stderr)   # 同じテストを 2 回挙げない
        (self.impl / "tests/test_app.py").write_text(
            "from src.app import hello\n\ndef test_hello():\n    assert hello() == 2\n", encoding="utf-8")
        (self.impl / "tests/e2e/hello.yaml").write_text("# coherence: doc=docs/api.md\nsuite: hello 2\n",
                                                        encoding="utf-8")
        path = self.impl / ".statemachine/codd/codd.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg["test"] = [sys.executable, "-c", "print('unit failed'); raise SystemExit(3)"]
        path.write_text(json.dumps(cfg), encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("実装のテストの検査が失敗しました（3）", r.stderr)
        self.assertIn("unit failed", r.stderr)
        cfg["test"] = [sys.executable, "-c", "print('unit ok')"]
        path.write_text(json.dumps(cfg), encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        report = self.report(self.impl).stdout
        self.assertIn("## テスト", report)
        self.assertIn("tests/test_app.py — 変えた", report)
        self.assertNotIn("tests/e2e/page.yaml — 変更不要", report)

    def test_test_plan_does_not_treat_search_paths_as_changes(self) -> None:
        commit(self.impl, {"tests/test_app.py": "hello()\n",
                           "tests/search_input.txt": "fixture\n",
                           "tests/search_output.txt": "result\n"}, "tests")
        self.set_config(self.impl, max_files=2)
        body = ("- `tests/test_app.py` — `hello` のログを確かめる。検索は "
                "`rg -n needle tests/search_input.txt > tests/search_output.txt`\n"
                "- 検索: `rg -n needle tests/search_input.txt > tests/search_output.txt`\n"
                "- `git grep needle -- tests/search_input.txt`\n"
                "- 変更不要: 参照先のテストは無い。検索は `rg needle design:docs/api.md`\n"
                "\n```sh\nrg needle tests/search_input.txt > tests/search_output.txt\n"
                "- tests/search_output.txt\n```\n")
        self.write_plan(self.with_tests(PLAN_ALIGNED, body))
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('hello')\n    return 1\n")
        (self.impl / "tests/test_app.py").write_text("hello() # check log\n")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        report = self.report(self.impl).stdout
        self.assertNotIn("search_input.txt", report)
        self.assertNotIn("search_output.txt", report)
        self.assertNotIn("docs/api.md — まだ", report)
        self.assertIn("tests/test_app.py — 変えた", report)

    def test_test_plan_preserves_explicit_multiple_and_reference_targets(self) -> None:
        commit(self.impl, {"tests/test_a.py": "fixture\n", "tests/test_b.py": "fixture\n"}, "tests")
        commit(self.design, {"tests/test_ref.py": "fixture\n"}, "tests")
        self.write_plan(self.with_tests(PLAN_ALIGNED,
                        "- `tests/test_a.py`, tests/test_b.py, design:tests/test_ref.py — ケースを足す"))
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 1 # changed\n")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        for rel in ("tests/test_a.py", "tests/test_b.py", "tests/test_ref.py"):
            self.assertIn(rel, r.stderr)

    def test_test_plan_requires_a_reason_after_unchanged(self) -> None:
        commit(self.impl, {"tests/test_app.py": "fixture\n"}, "tests")
        for body in ("- 変更不要", "- 変更不要:", "- 変更不要：   ",
                     "- tests/test_app.py — 変更不要", "- tests/test_app.py — 変更不要: 。",
                     "- 変更不要: `rg hello src/app.py > tests/search_output.txt`"):
            with self.subTest(body=body):
                self.write_plan(self.with_tests(PLAN_ALIGNED, body))
                r = self.run_pa(self.impl, "verify-plan")
                self.assertEqual(r.returncode, 1)
                self.assertIn("変更不要の後に理由がありません", r.stderr)
        # テストの自動測定を切っても、理由の欠けた判断は通さない。
        self.set_config(self.impl, tests=[])
        self.set_config(self.design, tests=[])
        self.write_plan(self.with_tests(PLAN_ALIGNED, "- 変更不要:"))
        self.assertIn("変更不要の後に理由がありません", self.run_pa(self.impl, "verify-plan").stderr)

    def test_no_change_is_read_the_same_before_and_after_changing(self) -> None:
        # 「変更不要」は計画の検査と変えたあとの検査で同じに読む。書き方は 2 つ（パスが先・変更不要: が先）。
        self.add_caller()
        commit(self.impl, {"tests/test_app.py": "from src.app import hello\n"}, "tests")
        base = PLAN_DRIFT.replace("- src/app.py — hello の戻り値", "- src/app.py — hello の戻り値\n{impact}")
        tests = "- 変更不要: tests/test_app.py — 戻り値を見ていない"

        # ほかの言い回しで「変えない」と書くと、計画の検査で書き直させる（変えたあとの検査は変更不要と読まないため）。
        self.write_plan(self.with_tests(base.format(impact="- src/use.py — 直さない（表示は変わらない）"), tests))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("変えないという判断に読めます", r.stderr)
        self.write_plan(self.with_tests(base.format(impact="- src/use.py — 変更不要"), tests))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertIn("## 影響範囲 の変更不要の後に理由がありません", r.stderr)

        self.write_plan(self.with_tests(base.format(impact="- 変更不要: src/use.py — 表示は変わらない"), tests))
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。\n\n実装は `src/app.py`（`src/` の下）。\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_paths_in_the_reason_are_not_waived(self) -> None:
        # 変更不要の対象は先頭に並べたパスだけ。理由に出てくるパスまで変更不要にしない。
        self.add_caller()
        commit(self.impl, {"src/other.py": "from app import hello\n\nprint(hello())\n"}, "other")
        self.write_plan(PLAN_DRIFT.replace("- src/app.py — hello の戻り値",
                                           "- src/app.py — hello の戻り値\n- src/use.py — 変更不要: src/other.py と同じく表示だけ"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("- src/other.py — 未判断", (self.impl / PLAN).read_text(encoding="utf-8"))

    def test_tests_section_cannot_be_none_when_something_changes(self) -> None:
        self.write_plan(self.with_tests(PLAN_ALIGNED, "なし"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("## テストの変更案 にテストの判断がありません", r.stderr)

    def links(self) -> list[dict]:
        path = self.impl / ".statemachine/codd/links.json"
        return json.loads(path.read_text(encoding="utf-8"))["links"] if path.is_file() else []

    def apply_ok(self, plan: str, files: dict[str, str]) -> None:
        """計画を確認まで通し、files のとおりに変えて、変えたあとの検査を通す。"""
        self.write_plan(plan)
        self.assert_plan_ok()
        for rel, text in {"src/app.py": "def hello():\n    print('a')\n    return 1\n", **files}.items():
            (self.impl / rel).write_text(text, encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_link_found_while_planning_is_kept_once_the_change_passes(self) -> None:
        # 測る道具では見つからないファイルを、利用者の指摘で影響範囲に足した。そのつながりを、変えたあとの検査を
        # 通ったときに控える（計画の検査の時点では書かない。確認で外したり、やめたりするかもしれない）。
        commit(self.impl, {"src/settings.py": "LEVEL = 'info'\n"}, "settings")
        why = "ログの段を足す（利用者の指摘。設定の既定値を読むところが app の出力の段と揃っていないと、"\
              "本番だけ詳しいログが出なくなるので、変えるたびに見る。直すときは README の設定の節も読み直す）"
        plan = PLAN_ALIGNED.replace("## 影響範囲\n\nなし",
                                    f"## 影響範囲\n\n- src/settings.py — {why}")
        self.write_plan(plan)
        self.assert_plan_ok(ok=False)
        self.assertEqual(self.links(), [])
        self.apply_ok(plan, {"src/settings.py": "LEVEL = 'debug'\n"})
        self.assertEqual(self.links(), [{"when": ["src/app.py"], "then": "src/settings.py",
                                         "why": why, "plan": PLAN}])   # 長い理由も途中で切らない
        # 測って見つかったファイル（文書がパスで指す docs/api.md）は控えない
        self.assertNotIn("docs/api.md", (self.impl / ".statemachine/codd/links.json").read_text(encoding="utf-8"))

    def test_a_link_from_a_stopped_run_is_not_kept(self) -> None:
        commit(self.impl, {"src/settings.py": "LEVEL = 'info'\n"}, "settings")
        self.write_plan(PLAN_ALIGNED.replace("## 影響範囲\n\nなし",
                                             "## 影響範囲\n\n- src/settings.py — ログの段を足す（利用者の指摘）"))
        self.assert_plan_ok(ok=False)
        self.assertEqual(self.run_pa(self.impl, "decide", "STOP").returncode, 0)
        self.assertEqual(self.links(), [])

    def write_links(self, *rows: dict) -> None:
        (self.impl / ".statemachine/codd/links.json").write_text(json.dumps({"links": list(rows)}), encoding="utf-8")

    def test_a_kept_link_is_asked_next_time_and_dropped_when_not_needed(self) -> None:
        commit(self.impl, {"src/settings.py": "LEVEL = 'info'\n", "src/extra.py": "X = 1\n"}, "settings")
        self.write_links({"when": ["src/app.py", "src/extra.py"], "then": "src/settings.py", "why": "ログの段を足す"})
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("- src/settings.py — 未判断（前の回に見つけたつながり（.statemachine/codd/links.json）: ログの段を足す）",
                      (self.impl / PLAN).read_text(encoding="utf-8"))
        # 「変更不要」と判断したら、当たった起点からのつながりだけを外す（古い発見を訊き続けない）
        self.apply_ok(PLAN_ALIGNED.replace("## 影響範囲\n\nなし",
                                           "## 影響範囲\n\n- src/settings.py — 変更不要: 段は変えない"), {})
        self.assertEqual([r["when"] for r in self.links()], [["src/extra.py"]])

    def test_a_kept_link_that_is_fixed_again_does_not_widen_its_sources(self) -> None:
        # 控えから訊いたものは新しい発見ではない。計画の他のファイルまで起点に広げない。
        commit(self.impl, {"src/settings.py": "LEVEL = 'info'\n", "src/extra.py": "X = 1\n"}, "settings")
        self.write_links({"when": ["src/app.py"], "then": "src/settings.py", "why": "ログの段を足す"})
        plan = PLAN_ALIGNED.replace("- src/app.py — `hello` の中でログを出す",
                                    "- src/app.py — `hello` の中でログを出す\n- src/extra.py — 値を変える")
        plan = plan.replace("## 影響範囲\n\nなし", "## 影響範囲\n\n- src/settings.py — 段を足す")
        self.apply_ok(plan, {"src/extra.py": "X = 2\n", "src/settings.py": "LEVEL = 'debug'\n"})
        self.assertEqual([r["when"] for r in self.links()], [["src/app.py"]])

    def test_a_ref_doc_relied_on_is_kept_as_a_link_without_writing_paths_into_it(self) -> None:
        # 決まりや仕様書は単独で配ることがあるので、コードのパスを書かせずに、つながりを自分の側に控える。
        commit(self.design, {"docs/style.md": "# 書き方\n\nログは info で出す。\n"}, "style")
        self.apply_ok(PLAN_ALIGNED.replace("## 参照先の前提\n\n",
                                           "## 参照先の前提\n\n- ログは info で出す（根拠: docs/style.md:3）\n"), {})
        self.assertEqual([(r["when"], r["then"]) for r in self.links()], [(["src/app.py"], "design:docs/style.md")])
        self.assertNotIn("src/app.py", (self.design / "docs/style.md").read_text(encoding="utf-8"))

    def test_a_kept_ref_link_marked_unrelated_is_dropped(self) -> None:
        commit(self.design, {"docs/style.md": "# 書き方\n\nログは info で出す。\n"}, "style")
        self.write_links({"when": ["src/app.py"], "then": "design:docs/style.md", "why": "書き方"})
        self.write_plan(PLAN_ALIGNED)
        self.assertEqual(self.run_pa(self.impl, "verify-plan").returncode, 1)
        self.assertIn("- 未判断: design:docs/style.md（前の回に見つけたつながり", (self.impl / PLAN).read_text(encoding="utf-8"))
        self.apply_ok(PLAN_ALIGNED.replace("## 参照先のその他\n\nなし",
                                           "## 参照先のその他\n\n- 関係なし: docs/style.md — ログの段は変えない"), {})
        self.assertEqual(self.links(), [])

    def test_links_from_files_changed_beyond_the_plan_are_checked_after_applying(self) -> None:
        # 計画に無いファイルを変える段で足しても、そのファイルから伸びる見つけたつながりの先を確かめる
        # （計画の検査は計画のファイルからしか引かない）。
        commit(self.impl, {"src/extra.py": "X = 1\n", "src/settings.py": "LEVEL = 'info'\n"}, "more")
        commit(self.design, {"docs/style.md": "# 書き方\n\nログは info で出す。\n"}, "style")
        links = self.impl / ".statemachine/codd/links.json"
        links.write_text(json.dumps({"links": [
            {"when": ["src/extra.py"], "then": "src/settings.py", "why": "段を合わせる"},
            {"when": ["src/extra.py"], "then": "design:docs/style.md", "why": "書き方"}]}), encoding="utf-8")
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('a')\n    return 1\n", encoding="utf-8")
        (self.impl / "src/extra.py").write_text("X = 2\n", encoding="utf-8")
        apply_md = self.impl / ".codd/apply.md"
        apply_md.write_text("## 計画との違い\n\n- src/extra.py — 追加: 値をそろえた\n", encoding="utf-8")
        self.run_pa(self.impl, "verify-apply")
        self.assertEqual(self.run_pa(self.impl, "accept").returncode, 0)
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("前の回に見つけたつながり（.statemachine/codd/links.json）の先にある自分のファイルを、直していません", r.stderr)
        self.assertIn("src/settings.py", r.stderr)
        self.assertIn("先にある参照先のファイルを、計画で扱っていません", r.stderr)
        self.assertIn("docs/style.md", r.stderr)
        apply_md.write_text("## 計画との違い\n\n- src/extra.py — 追加: 値をそろえた\n"
                            "- src/settings.py — 変更不要: 段は変えない\n- docs/style.md — 関係なし: ログの書き方は変えない\n",
                            encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        # 変える段で「変更不要」「関係なし」とされたつながりも、当たった起点を外す
        self.assertEqual(self.links(), [{"when": ["src/app.py"], "then": "src/extra.py", "why": "値をそろえた", "plan": PLAN}])

    def test_a_file_added_while_applying_is_kept_as_a_link(self) -> None:
        commit(self.impl, {"src/settings.py": "LEVEL = 'info'\n"}, "settings")
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        self.assertEqual(self.links(), [])
        (self.impl / "src/app.py").write_text("def hello():\n    return 1  # log\n", encoding="utf-8")
        (self.impl / "src/settings.py").write_text("LEVEL = 'debug'\n", encoding="utf-8")
        (self.impl / ".codd/apply.md").write_text("## 計画との違い\n\n- src/settings.py — 追加: `LEVEL` を合わせた\n",
                                                  encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 1)   # 計画に無いファイルは確かめる
        self.assertEqual(self.run_pa(self.impl, "accept").returncode, 0)
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.links(), [{"when": ["src/app.py"], "then": "src/settings.py",
                                         "why": "LEVEL を合わせた", "plan": PLAN}])

    def test_tests_section_is_needed_when_only_the_impact_fixes_a_file(self) -> None:
        # 影響範囲で直すだけのファイルも変える。テストの判断は同じく要る。
        plan = PLAN_ALIGNED.replace("- src/app.py — `hello` の中でログを出す", "なし")
        plan = plan.replace("## 影響範囲\n\nなし", "## 影響範囲\n\n- src/app.py — `hello` の中でログを出す")
        self.write_plan(self.with_tests(plan, "なし"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("## テストの変更案 にテストの判断がありません", r.stderr)

    def test_files_fixed_in_the_impact_are_measured_like_planned_ones(self) -> None:
        # 影響範囲で直すファイルも変える。そのファイルと対になるテストを、計画のときに測る（変えたあとまで待たない）。
        self.add_caller()
        commit(self.impl, {"tests/test_use.py": "import use\n"}, "tests")
        plan = PLAN_ALIGNED.replace("## 影響範囲\n\nなし", "## 影響範囲\n\n- src/use.py — ログに合わせて表示を直す")
        self.write_plan(plan)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("- tests/test_use.py — 未判断", (self.impl / PLAN).read_text(encoding="utf-8"))

    def test_a_non_test_file_is_not_a_test_decision(self) -> None:
        # テストのファイルでないものを挙げても、テストの判断に数えない。
        self.write_plan(self.with_tests(PLAN_ALIGNED, "- src/app.py — ログが出るか確かめる"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("## テストの変更案 にテストの判断がありません", r.stderr)
        commit(self.impl, {"tests/test_app.py": "from src.app import hello\n"}, "tests")
        (self.impl / ".codd/run-start.json").unlink()   # 利用者がテストを足してから、計画を練り直す
        self.write_plan(self.with_tests(PLAN_ALIGNED, "- tests/test_app.py — `hello` がログを出すか確かめる"))
        self.assert_plan_ok()

    def test_apply_catches_tests_hit_by_names_changed_beyond_the_plan(self) -> None:
        commit(self.impl, {"tests/test_util.py": "from src.app import greet\n"}, "tests")
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        # 計画に無い名前（greet）まで足すと、それを使うテストが響く。
        (self.impl / "src/app.py").write_text("def hello():\n    print('hi')\n    return 1\n\n\ndef greet():\n"
                                              "    return 'hi'\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("変更が響くテストのうち、直していないファイルがあります", r.stderr)
        self.assertIn("tests/test_util.py", r.stderr)
        # 直せと言われて直したテストは、計画に無くても「計画に無いファイル」で止めない（直しては止まる、を繰り返さない）。
        (self.impl / "tests/test_util.py").write_text("from src.app import greet\n\n\ndef test_greet():\n"
                                                      "    assert greet() == 'hi'\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertNotIn("計画に無いファイル", r.stderr)
        self.assertNotIn("計画で挙げていないファイルを足しました", r.stderr)

    def test_spec_change_from_the_design_side_reaches_impl_tests(self) -> None:
        # 仕様書の側から始めても、実装の側の単体テストと e2e のケースを最初の検査から「テストの変更案」で扱わせる。
        commit(self.impl, {
            "tests/test_app.py": "from src.app import hello\n\ndef test_hello():\n    assert hello() == 1\n",
            "tests/e2e/hello.yaml": "# coherence: doc=docs/api.md\nsuite: hello\n",
        }, "tests")
        plan = (PLAN_ALIGNED.replace("hello にログを足す。", "hello の説明に補足を足す。")
                .replace("（根拠: docs/api.md#hello）", "（根拠: src/app.py）")
                .replace("（根拠: docs/api.md:3）", "（根拠: src/app.py:2）")
                .replace("## 守る決まり\n\nなし", "## 守る決まり\n\n- docs/api.md — 文書の書式: 今の見出しの並びを保つ")
                .replace("- src/app.py — `hello` の中でログを出す", "- docs/api.md — `hello` の説明に補足を足す"))
        (self.design / ".plans").mkdir(parents=True, exist_ok=True)
        (self.design / PLAN).write_text(plan, encoding="utf-8")
        self.read_up(self.design)
        r = self.run_pa(self.design, "verify-plan")
        self.assertEqual(r.returncode, 1)
        text = (self.design / PLAN).read_text(encoding="utf-8")
        tests_part = text.split("## テストの変更案", 1)[1].split("\n## ", 1)[0]
        others = text.split("## 参照先のその他", 1)[1].split("\n## ", 1)[0]
        for rel in ("tests/test_app.py", "tests/e2e/hello.yaml"):
            self.assertIn(f"- impl:{rel} — 未判断", tests_part)
            self.assertNotIn(rel, others)   # 根拠の見出しで「関係なし」と片付けさせない
        (self.design / PLAN).write_text(self.with_tests(plan, "\n".join([
            "- tests/test_app.py — 変更不要: 戻り値は変わらない",
            "- tests/e2e/hello.yaml — 補足の文言を確かめるように直す",
        ])), encoding="utf-8")
        self.assert_plan_ok(self.design)

        # 変えたあと: 実装の側のテストも直させ、実装の側の test を codd が動かす。
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 1 を返す。いつも同じ値。\n\n実装は `src/app.py`（`src/` の下）。\n",
                                                 encoding="utf-8")
        r = self.run_pa(self.design, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("tests/e2e/hello.yaml", r.stderr)
        (self.impl / "tests/e2e/hello.yaml").write_text("# coherence: doc=docs/api.md\nsuite: hello always\n",
                                                        encoding="utf-8")
        self.set_config(self.impl, test=[sys.executable, "-c", "print('impl unit failed'); raise SystemExit(4)"])
        r = self.run_pa(self.design, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("impl unit failed", r.stderr)
        self.set_config(self.impl, test=[sys.executable, "-c", "print('ok')"])
        r = self.run_pa(self.design, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_new_names_need_tests(self) -> None:
        # 今あるテストに当たらない新しい名前は、足すテストを計画させ、変えたあとも確かめる。
        plan = PLAN_ALIGNED.replace("- src/app.py — `hello` の中でログを出す", "- src/app.py — `greet` を足す")
        self.write_plan(plan)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("新しく足す `greet` を確かめるテストが ## テストの変更案 にありません", r.stderr)
        self.write_plan(self.with_tests(plan, "- src/test_greet.py — `greet` が挨拶を返すことを確かめるケースを足す"))
        self.assert_plan_ok()
        self.write_plan(self.with_tests(plan, "- `greet` — 変更不要: 試しに足すだけで、どこからも呼ばない"))
        self.assert_plan_ok()

        # 計画に無い新しい関数まで足すと、確かめるテストが無いので落とす。
        (self.impl / "src/app.py").write_text("def hello():\n    return 1\n\n\ndef greet():\n    return 'hi'\n\n\n"
                                              "def welcome():\n    return 'welcome'\n\n\ndef _inner():\n"
                                              "    return 0\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("新しく足した名前を確かめるテストがありません", r.stderr)
        self.assertIn("`welcome`（src/app.py）", r.stderr)
        self.assertNotIn("`greet`（", r.stderr)    # 計画が扱っている
        self.assertNotIn("`_inner`", r.stderr)     # 内側の名前は除く
        (self.impl / "src/app.py").write_text("def hello():\n    return 1\n\n\ndef greet():\n    return 'hi'\n",
                                              encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_e2e_cases_are_found_by_file_name_and_text(self) -> None:
        # e2e のケースはコードの名前ではなく、画面の文言や URL で書かれる。ファイル名と文字列で拾う。
        commit(self.impl, {
            "src/page.js": "export function renderHello() {\n  return '<h1>Hello page</h1>'\n}\n",
            "tests/e2e/page.yaml": "suite: page\nsteps:\n  - goto: /hello\n",
            "tests/e2e/top.yaml": "suite: top\nsteps:\n  - expect: <h1>Hello page</h1>\n",
        }, "page")
        plan = PLAN_ALIGNED.replace("- src/app.py — `hello` の中でログを出す", "- src/page.js — `renderHello` の見出しを変える")
        self.write_plan(plan)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        report = (self.impl / ".codd/tests.md").read_text(encoding="utf-8")
        self.assertIn("tests/e2e/page.yaml — ファイル名", report)
        self.assertNotIn("top.yaml", report)       # 計画の段階では文言が分からない
        self.write_plan(self.with_tests(plan, "- tests/e2e/page.yaml — 変更不要: 見出しを見ていない"))
        self.assert_plan_ok()
        (self.impl / "src/page.js").write_text(
            "export function renderHello() {\n  return '<h1>Welcome page</h1>'\n}\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("tests/e2e/top.yaml", r.stderr)
        report = (self.impl / ".codd/tests-after.md").read_text(encoding="utf-8")
        self.assertIn("tests/e2e/top.yaml — 文字列", report)

    def test_tests_can_be_turned_off(self) -> None:
        path = self.impl / ".statemachine/codd/codd.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg["tests"] = []
        path.write_text(json.dumps(cfg), encoding="utf-8")
        # 参照先にも置いてあるので、参照先の tests も空にする。
        dpath = self.design / ".statemachine/codd/codd.json"
        dcfg = json.loads(dpath.read_text(encoding="utf-8"))
        dcfg["tests"] = []
        dpath.write_text(json.dumps(dcfg), encoding="utf-8")
        self.write_plan(self.with_tests(PLAN_ALIGNED, "なし"))
        self.assert_plan_ok()

    def test_files_changed_before_the_plan_are_not_counted(self) -> None:
        (self.impl / "src/wip.py").write_text("x = 1\n", encoding="utf-8")   # 計画より前からの作業中の変更
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('hi')\n    return 1\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)

    # ------------------------------------------------------------ 実装と設計書が同じリポジトリ

    def make_mono(self, **kw) -> Path:
        mono = self.tmp / "mono"
        mono.mkdir()
        git(mono, "init", "-q", "-b", "main")
        commit(mono, {"src/app.py": "def hello():\n    return 1\n",
                      "docs/api.md": "# API\n\n## hello\n\nhello は 1 を返す。\n\n実装は `src/app.py`（`src/` の下）。\n"}, "init")
        init.init_repo(mono, "impl", ["docs=."], **kw)
        return mono

    def test_single_repo_init_without_refs(self) -> None:
        # --ref を渡さなければ、同じリポジトリの docs/ を参照先にし、自分はその外の全部を受け持つ。
        mono = self.tmp / "mono"
        mono.mkdir()
        git(mono, "init", "-q", "-b", "main")
        doc = "# API\n\n## hello\n\nhello は {} を返す。\n\n実装は `src/app.py`（`src/` の下）。\n"
        commit(mono, {"src/app.py": "def hello():\n    return 1\n", "docs/api.md": doc.format(1)}, "init")
        init.init_repo(mono, None, None, discover=False, agents=())
        cfg = json.loads((mono / ".statemachine/codd/codd.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["side"], "impl")
        self.assertNotIn("scope", cfg)
        self.assertEqual(cfg["refs"], [{"name": "docs", "path": ".", "scope": ["docs"]}])
        r = self.run_pa(mono, "show")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("除外: docs/", r.stdout)
        r = self.run_pa(mono, "explore", "--term", "hello")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("FOUND 1 files", r.stdout)   # 参照先（設計書）を探すのは docs の中だけ
        self.run_pa(mono, "guide", "--all")
        (mono / ".plans").mkdir(parents=True, exist_ok=True)
        (mono / PLAN).write_text(PLAN_DRIFT, encoding="utf-8")
        self.assert_plan_ok(mono)
        (mono / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (mono / "docs/api.md").write_text(doc.format(2), encoding="utf-8")
        r = self.run_pa(mono, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("own=changed refs=docs", r.stdout)   # 同じリポジトリでも自分と設計書を分けて測る
        # 指定した設計書のフォルダが無ければ、止まる
        bare = self.tmp / "bare"
        bare.mkdir()
        git(bare, "init", "-q")
        with self.assertRaises(SystemExit) as e:
            init.init_repo(bare, None, None, discover=False, agents=(), ref_scopes=["docs=spec"])
        self.assertIn("--ref-scope docs=", str(e.exception))

    def test_code_only_repo_keeps_code_and_tests_coherent(self) -> None:
        # docs/ も無ければ文書を持たないリポジトリとして置き、文書は求めずに、実装とテストの一貫性だけを取る
        repo = self.tmp / "solo"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        commit(repo, {"src/app.py": "def hello():\n    return 1\n",
                      "tests/test_app.py": "from src.app import hello\n\nassert hello() == 1\n"}, "init")
        init.init_repo(repo, None, None, discover=False, agents=(), test=f"{sys.executable} -c pass")
        self.assertEqual(json.loads((repo / ".statemachine/codd/codd.json").read_text(encoding="utf-8"))["refs"], [])
        r = self.run_pa(repo, "show")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("参照先: なし", r.stdout)
        self.read_up(repo)
        plan = (PLAN_ALIGNED.replace("- hello は整数を返す（根拠: docs/api.md#hello）", "なし")
                .replace("- hello は 1 を返す（根拠: docs/api.md:3）", "なし"))
        (repo / ".plans").mkdir(parents=True, exist_ok=True)
        # テストの判断はやはり要る（響くテストを挙げさせる）
        (repo / PLAN).write_text(plan.replace("- 変更不要: このリポジトリにテストはまだ無い（例の小さなリポジトリ）", ""),
                                 encoding="utf-8")
        r = self.run_pa(repo, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("対応する文書の無いコード", r.stderr)
        (repo / PLAN).write_text(plan.replace("- 変更不要: このリポジトリにテストはまだ無い（例の小さなリポジトリ）",
                                              "- tests/test_app.py — ログを出しても 1 を返すことを確かめる"), encoding="utf-8")
        self.assert_plan_ok(repo)
        (repo / "src/app.py").write_text("def hello():\n    print('a')\n    return 1\n", encoding="utf-8")
        (repo / "tests/test_app.py").write_text("from src.app import hello\n\nassert hello() == 1  # log\n",
                                                encoding="utf-8")
        r = self.run_pa(repo, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("refs=none", r.stdout)

    def test_same_repo_needs_scopes(self) -> None:
        mono = self.make_mono()
        r = self.run_pa(mono, "show")
        self.assertEqual(r.returncode, 2)
        self.assertIn("同じリポジトリです", r.stderr)
        init.init_repo(mono, None, None, scope=["src"], ref_scopes=["docs=src/docs"])
        r = self.run_pa(mono, "show")
        self.assertEqual(r.returncode, 2)
        self.assertIn("scope が重なっています", r.stderr)

    def test_own_rules_are_found_only_near_the_scope(self) -> None:
        # 同じリポジトリのほかの道具の決まりは拾わない（自分の scope の中と、その上のフォルダだけ）。
        mono = self.make_mono(scope=["tools/app"], ref_scopes=["docs=docs"])
        commit(mono, {"CONTRIBUTING.md": "# 手引き\n", "tools/coding-rules.md": "# 共通の規約\n",
                      "tools/app/style-guide.md": "# 書き方\n", "tools/other/rules.md": "# 別の道具の決まり\n"},
               "rules")
        r = self.run_pa(mono, "show")
        self.assertEqual(r.returncode, 0, r.stderr)
        for rel in ("CONTRIBUTING.md", "tools/coding-rules.md", "tools/app/style-guide.md"):
            self.assertIn(rel, r.stdout)
        self.assertNotIn("tools/other/rules.md", r.stdout)

    def test_same_repo_with_scopes(self) -> None:
        mono = self.make_mono(scope=["src"], ref_scopes=["docs=docs"])
        cfg = json.loads((mono / ".statemachine/codd/codd.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["scope"], ["src"])
        self.assertEqual(cfg["refs"], [{"name": "docs", "path": ".", "scope": ["docs"]}])
        commit(mono, {"docs/coding-rules.md": "# コーディングルール\n"}, "rules")
        r = self.run_pa(mono, "show")
        self.assertIn("- docs: 設計書", r.stdout)
        self.assertIn("  - docs:docs/coding-rules.md", r.stdout)   # 参照先の候補として挙がる（自分の分に重ねない）
        self.assertNotIn("  - docs/coding-rules.md", r.stdout)
        self.assertIn("受け持つフォルダ: docs", r.stdout)
        # 同じリポジトリの参照先が受け継いだ protect は、`docs:` を付けてもう一度並べない
        self.set_config(mono, protect=["docs/adr/*.md"])
        r = self.run_pa(mono, "show")
        self.assertIn("人の承認が要るファイル", r.stdout)
        self.assertEqual(r.stdout.count("docs/adr/*.md"), 1, r.stdout)

        # 参照先を探すのは docs の中だけ（src/app.py の hello は拾わない）。
        r = self.run_pa(mono, "explore", "--term", "hello")
        self.assertIn("FOUND 1 files", r.stdout)
        self.run_pa(mono, "guide", "--all")
        # 根拠は参照先の scope の中、影響範囲は自分の scope の中だけを認める。
        plan = PLAN_DRIFT
        (mono / ".plans").mkdir(parents=True, exist_ok=True)
        (mono / PLAN).write_text(plan.replace("（根拠: docs/api.md）", "（根拠: src/app.py）"),
                                            encoding="utf-8")
        r = self.run_pa(mono, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("参照先に実在する根拠のパスがありません", r.stderr)
        (mono / PLAN).write_text(plan, encoding="utf-8")
        self.assert_plan_ok(mono)

        # 同じリポジトリの中でも、自分と参照先の変更を scope で分けて測る。
        (mono / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        r = self.run_pa(mono, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("docs を変えるはずなのに、docs が変わっていません", r.stderr)
        (mono / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。\n\n実装は `src/app.py`（`src/` の下）。\n", encoding="utf-8")
        r = self.run_pa(mono, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("own=changed refs=docs", r.stdout)

    def test_names_outside_every_scope_are_reported_without_stopping(self) -> None:
        # CI の設定などは、どの scope にも入らない。止めはしないが、改名で黙って壊れないように知らせる。
        mono = self.make_mono(scope=["src"], ref_scopes=["docs=docs"])
        commit(mono, {".github/workflows/ci.yml": "env:\n  hello: 1\n"}, "ci")
        (mono / ".plans").mkdir(parents=True, exist_ok=True)
        (mono / PLAN).write_text(PLAN_DRIFT, encoding="utf-8")
        self.run_pa(mono, "guide", "--all")
        self.run_pa(mono, "explore", "--term", "hello")
        r = self.run_pa(mono, "verify-plan")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("受け持ちのフォルダ（scope）の外にも、変わる名前が出てくるファイルがあります", r.stdout)
        self.assertIn(".github/workflows/ci.yml", r.stdout)
        self.assertNotIn("src/app.py", r.stdout.split("知らせ:")[1].splitlines()[0])   # scope の中のものは挙げない
        self.assertIn("受け持ちの外で、変わる名前が出てくるファイル", self.run_pa(mono, "summary").stdout)
        # 「今回やらないこと」に回したものは、確認でもう確かめない
        text = (mono / PLAN).read_text(encoding="utf-8")
        (mono / PLAN).write_text(re.sub(r"## 今回やらないこと\n\n[^#]*", "## 今回やらないこと\n\n"
                                        "- .github/workflows/ci.yml — 環境変数で別物\n\n", text), encoding="utf-8")
        self.assertNotIn("受け持ちの外で、変わる名前が出てくるファイル", self.run_pa(mono, "summary").stdout)

    def test_exclude_filters_each_side_within_scope(self) -> None:
        commit(self.impl, {"src/settings.json": "hello", "src/config/local.yaml": "hello",
                           "src/nested/settings.json": "hello", "src/keep.py": "hello"}, "settings")
        commit(self.design, {"docs/settings.json": "hello", "docs/config/local.yaml": "hello"}, "settings")
        # 設定を変える前に記録された検索結果も、その後の表示に残さない。
        self.run_pa(self.impl, "explore", "--term", "hello")
        self.set_config(self.impl, scope=["src"], exclude=["src/*.json", "src/config/"],
                        refs=[{"name": "design", "path": "../design", "scope": ["docs"],
                               "exclude": ["**/*.json", "docs/config"]}])
        r = self.run_pa(self.impl, "impact", "--term", "hello")
        self.assertEqual(r.returncode, 0, r.stderr)
        report = (self.impl / ".codd/impact.md").read_text()
        self.assertNotIn("src/settings.json", report)
        self.assertNotIn("src/config/local.yaml", report)
        self.assertIn("src/nested/settings.json", report)  # * はフォルダをまたがない
        self.assertIn("src/keep.py", report)
        r = self.run_pa(self.impl, "explore", "--term", "hello")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("FOUND 1 files", r.stdout)
        self.assertNotIn("settings.json", r.stdout)
        self.assertNotIn("settings.json", (self.impl / ".codd/explore.md").read_text())
        self.assertIn("除外: src/*.json, src/config/", self.run_pa(self.impl, "show").stdout)

    def test_exclude_ignores_tracked_deleted_and_untracked_changes(self) -> None:
        commit(self.impl, {"src/settings.json": "hello"}, "settings")
        commit(self.design, {"docs/settings.json": "hello"}, "settings")
        self.set_config(self.impl, exclude=["**/*.json"],
                        refs=[{"name": "design", "path": "../design", "exclude": ["**/*.json"]}])
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        (self.impl / "src/settings.json").unlink()
        (self.impl / "src/new.json").write_text("hello")
        (self.design / "docs/settings.json").write_text("changed hello")
        (self.impl / "src/app.py").write_text("def hello():\n    return 1 # changed\n")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("own=changed refs=none", r.stdout)
        # 途中でコミットした場合の変更一覧にも適用する。
        git(self.impl, "add", "-A")
        git(self.impl, "commit", "-q", "-m", "apply")
        git(self.design, "add", "-A")
        git(self.design, "commit", "-q", "-m", "settings")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_excluded_files_cannot_be_planned_or_cited(self) -> None:
        self.set_config(self.impl, exclude=["src/app.py"])
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("自分の変更案の項目に", r.stderr)
        self.set_config(self.impl, exclude=[],
                        refs=[{"name": "design", "path": "../design", "exclude": ["docs/api.md"]}])
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("根拠のパスがありません", r.stderr)

    def test_exclude_filters_graphify_output_and_test_files(self) -> None:
        self.use_graphify_stub()
        commit(self.impl, {"src/use.py": "hello", "tests/config.test.py": "hello",
                           "tests/app.test.py": "hello"}, "tests")
        self.set_config(self.impl, exclude=["src/use.py", "**/config.test.py"],
                        refs=[{"name": "design", "path": "../design", "exclude": ["docs/api.md"]}])
        self.assertIn("自分 1 files", self.run_pa(self.impl, "show").stdout)
        self.run_pa(self.impl, "impact", "--term", "hello")
        self.assertNotIn("src/use.py:L4", (self.impl / ".codd/impact.md").read_text())
        r = self.run_pa(self.impl, "explore", "--term", "hello")
        self.assertIn("FOUND 0 files", r.stdout)
        self.assertNotIn("src=docs/api.md", (self.impl / ".codd/explore.md").read_text())

    def test_exclude_does_not_hide_rules(self) -> None:
        commit(self.impl, {"AGENTS.md": "# Rules\n", "docs/coding-rules.md": "# Rules\n"}, "rules")
        commit(self.design, {"AGENTS.md": "# Rules\n", "docs/design-rules.md": "# Rules\n"}, "rules")
        self.set_config(self.impl, exclude=["**/*.md"], rules=["docs/coding-rules.md"],
                        refs=[{"name": "design", "path": "../design", "exclude": ["**/*.md"]}])
        r = self.run_pa(self.impl, "guide", "--all")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("# 守る決まり AGENTS.md", r.stdout)
        self.assertIn("# 守る決まり design:AGENTS.md", r.stdout)
        self.assertIn("# 守る決まり docs/coding-rules.md", r.stdout)
        self.assertIn("design:docs/design-rules.md", self.run_pa(self.impl, "show").stdout)

    def test_exclude_globs_match_root_nested_and_character_classes(self) -> None:
        files = {p: "hello" for p in ("settings.json", "src/settings.json", "src/config1.yaml",
                                      "src/configA.yaml", "src/x.ini", "src/xx.ini")}
        commit(self.impl, files, "settings")
        self.set_config(self.impl, exclude=["**/*.json", "src/config[0-9].yaml", "**/?.ini"])
        r = self.run_pa(self.impl, "impact", "--term", "hello")
        self.assertEqual(r.returncode, 0, r.stderr)
        report = (self.impl / ".codd/impact.md").read_text()
        for p in ("settings.json", "src/settings.json", "src/config1.yaml", "src/x.ini"):
            self.assertNotIn("- " + p, report)
        self.assertIn("- src/configA.yaml", report)
        self.assertIn("- src/xx.ini", report)

    def test_exclude_validation(self) -> None:
        for value in ("*.json", [1], [""], ["../secret"], ["/tmp/config"], ["C:/config"], ["!keep.py"]):
            for ref in (False, True):
                with self.subTest(value=value, ref=ref):
                    self.set_config(self.impl, exclude=[] if ref else value,
                                    refs=[{"name": "design", "path": "../design", **({"exclude": value} if ref else {})}])
                    r = self.run_pa(self.impl, "show")
                    self.assertEqual(r.returncode, 2, r.stderr)
                    self.assertIn("exclude", r.stderr)

    def test_init_exclude_options_replace_clear_and_preserve(self) -> None:
        r = subprocess.run([sys.executable, str(TOOL / "init.py"), str(self.impl),
                            "--exclude", "**/*.json", "--exclude", ".github/",
                            "--ref-exclude", "design=**/*.yaml", "--ref-exclude", "design=docs/config/"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        path = self.impl / ".statemachine/codd/codd.json"
        cfg = json.loads(path.read_text())
        self.assertEqual(cfg["exclude"], ["**/*.json", ".github/"])
        self.assertEqual(cfg["refs"][0]["exclude"], ["**/*.yaml", "docs/config/"])
        init.init_repo(self.impl, None, ["design=../design"])
        self.assertEqual(json.loads(path.read_text())["refs"][0]["exclude"], cfg["refs"][0]["exclude"])
        init.init_repo(self.impl, None, None, exclude=[""], ref_excludes=["design="])
        cfg = json.loads(path.read_text())
        self.assertEqual(cfg["exclude"], [])
        self.assertEqual(cfg["refs"][0]["exclude"], [])

    def test_unknown_config_keys_are_reported(self) -> None:
        path = self.impl / ".statemachine/codd/codd.json"
        for patch, word in (({"refz": []}, "refz"), ({"refs": [{"path": "../design", "scopes": ["docs"]}]}, "scopes"),
                            ({"max_files": 0}, "max_files")):
            with self.subTest(word=word):
                cfg = {"side": "impl", "refs": [{"path": "../design"}], **patch}
                path.write_text(json.dumps(cfg), encoding="utf-8")
                r = self.run_pa(self.impl, "show")
                self.assertEqual(r.returncode, 2)
                self.assertIn(word, r.stderr)

    # ------------------------------------------------------------ 決まり・スキル・道具を確かに使う

    def set_config(self, repo: Path, **values) -> None:
        path = repo / ".statemachine/codd/codd.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg.update(values)
        init.migrate_legacy(cfg)   # 短く書けるよう、前の版の書き方（rules・skills）も受けて guides に直す
        path.write_text(json.dumps(cfg), encoding="utf-8")

    def test_plan_must_name_every_rule_file(self) -> None:
        commit(self.impl, {"CLAUDE.md": "# 約束\n", "docs/style.md": "# 書き方\n"}, "rules")
        commit(self.design, {"CLAUDE.md": "# 設計書の約束\n"}, "rules")
        self.set_config(self.impl, rules=["docs/style.md"])
        r = self.run_pa(self.impl, "show")
        self.assertIn("  - CLAUDE.md\n  - docs/style.md\n  - design:CLAUDE.md", r.stdout)
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("従う手順に、決まりのファイルを読んで挙げてください", r.stderr)
        # 自分にも同じ名前があるので、参照先の決まりは名前付きで挙げる。
        self.write_plan(PLAN_ALIGNED.replace("## 守る決まり\n\nなし",
                                             "## 守る決まり\n\n- CLAUDE.md — テストを通す\n- docs/style.md — 敬体"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("design:CLAUDE.md", r.stderr)
        self.assertNotIn("docs/style.md", r.stderr)
        self.write_plan(PLAN_ALIGNED.replace("## 守る決まり\n\nなし",
                                             "## 守る決まり\n\n- CLAUDE.md — テストを通す\n- docs/style.md — 敬体\n"
                                             "- design:CLAUDE.md — 用語をそろえる"))
        self.assert_plan_ok()

    def test_rule_files_must_be_read_through_the_machine(self) -> None:
        commit(self.impl, {"CLAUDE.md": "# 約束\n\nテストを通す。\n"}, "rules")
        plan = PLAN_ALIGNED.replace("## 守る決まり\n\nなし", "## 守る決まり\n\n- CLAUDE.md — テストを通す")
        self.write_plan(plan, read=False)
        self.run_pa(self.impl, "explore", "--term", "hello")
        r = self.run_pa(self.impl, "verify-plan")   # 挙げただけで、読み込んでいない
        self.assertEqual(r.returncode, 1)
        self.assertIn("守る決まりのファイルを読み込んでいません", r.stderr)
        r = self.run_pa(self.impl, "guide", "--all")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("# 守る決まり CLAUDE.md", r.stdout)
        self.assertIn("テストを通す。", r.stdout)
        # 練り直しで呼び直しても、読み込み済みで変わっていないものは出し直さない（--again で出す）。
        r = self.run_pa(self.impl, "guide", "--all")
        self.assertIn("CLAUDE.md（この回で読み込み済み・変わっていない", r.stdout)
        self.assertNotIn("テストを通す。", r.stdout)
        self.assertIn("テストを通す。", self.run_pa(self.impl, "guide", "--all", "--again").stdout)
        self.assert_plan_ok()
        # 読み込んだあとに決まりが変わったら、読み直させる。
        (self.impl / "CLAUDE.md").write_text("# 約束\n\n型を付ける。\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("読み直す）: CLAUDE.md", r.stderr)
        self.assertEqual(self.run_pa(self.impl, "guide", "nothing.md").returncode, 1)

    def test_the_reading_procedure_is_read_even_without_rule_files(self) -> None:
        # 読み書きの手順は codd が持つ。決まりのファイルの無いリポジトリでも、読まずに計画を通さない
        self.write_plan(PLAN_ALIGNED, read=False)
        self.run_pa(self.impl, "explore", "--term", "hello")
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("読み直す）: .statemachine/codd/rules/reading.md", r.stderr)
        r = self.run_pa(self.impl, "guide", "--all")
        self.assertIn("# 読み書きの手順", r.stdout)
        self.assertIn("ファイルを丸ごと開かない", r.stdout)
        self.assert_plan_ok()   # 計画の「従う手順」に挙げなくてよい

    def test_a_rule_section_named_by_heading_is_read_and_required(self) -> None:
        # `use: "パス.md#見出し"` のいつも守る手順も、その節を読み込ませ、計画に挙げさせる（黙って効かなくならない）。
        commit(self.impl, {"docs/rules.md": "# 決まり\n\n## Naming Rules\n\nsnake_case にする。\n\n## 他\n\n関係ない節\n"},
               "rules")
        commit(self.design, {"style.md": "# 書き方\n\n## 表\n\n型の列を書く。\n"}, "rules")
        self.set_config(self.impl, guides=[{"use": "docs/rules.md#naming-rules"}, {"use": "design:style.md#表"},
                                           {"use": "docs/rules.md#無い節"}])
        r = self.run_pa(self.impl, "show")
        self.assertIn("  - docs/rules.md#naming-rules\n", r.stdout)
        self.assertIn("  - design:style.md#表\n", r.stdout)
        self.assertNotIn("docs/rules.md#naming-rules に当たるファイルがありません", r.stdout)
        self.assertIn("docs/rules.md#無い節（見出し「無い節」がありません）", r.stdout)
        plan = PLAN_ALIGNED.replace("## 守る決まり\n\nなし", "## 守る決まり\n\n- docs/rules.md — snake_case\n"
                                                              "- design:style.md — 型の列")
        self.write_plan(plan, read=False)
        self.run_pa(self.impl, "explore", "--term", "hello")
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("読み込んでいません", r.stderr)
        r = self.run_pa(self.impl, "guide", "--all")
        self.assertIn("snake_case にする。", r.stdout)
        self.assertNotIn("関係ない節", r.stdout)        # 見出しで指した節だけ
        self.assertIn("型の列を書く。", r.stdout)
        self.assertIn("型の列を書く。", self.run_pa(self.impl, "guide", "design:style.md#表").stdout)
        self.assert_plan_ok()

    def test_plan_needs_exploring_and_handling_what_was_found(self) -> None:
        commit(self.design, {"docs/guide.md": "# 使い方\n\nログは標準出力に出す。\n"}, "guide")
        self.write_plan(PLAN_ALIGNED, read=False)
        self.run_pa(self.impl, "guide", "--all")
        r = self.run_pa(self.impl, "verify-plan")   # 探さずに「なし」で済ませる計画は通さない
        self.assertEqual(r.returncode, 1)
        self.assertIn("参照先を探していません: design", r.stderr)
        self.run_pa(self.impl, "explore", "--term", "hello", "--term", "ログ")
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("「未判断」として書き足しました", r.stderr)
        self.assertIn("docs/guide.md", r.stderr)
        self.assertNotIn("docs/api.md,", r.stderr)   # 根拠に挙げたものは扱った
        self.write_plan(PLAN_ALIGNED.replace("## 参照先のその他\n\nなし",
                                             "## 参照先のその他\n\n- ログは標準出力に出す（根拠: docs/guide.md）"),
                        read=False)
        self.assert_plan_ok()
        # 終わりの報告で、探した・読んだ記録は消える（次の回に持ち越さない）。
        (self.impl / "src/app.py").write_text("def hello():\n    print('log')\n    return 1\n", encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 0)
        self.report(self.impl)
        self.assertFalse((self.impl / ".codd/explore.json").exists())
        self.assertFalse((self.impl / ".codd/rules-read.json").exists())

    def test_unrelated_found_files_can_be_waived_in_one_line(self) -> None:
        # 見つかったファイルは一致した行で判断し、関係しないものは 1 行にまとめて扱える（全文を読ませない）。
        commit(self.design, {"docs/a.md": "# A\n\nhello の綴りの話。\n", "docs/b.md": "# B\n\nhello world の例。\n"},
               "more")
        self.write_plan(PLAN_ALIGNED.replace(
            "## 参照先のその他\n\nなし",
            "## 参照先のその他\n\n- 関係なし: docs/a.md:3, docs/b.md:3 — 綴りと例の話で、hello の振る舞いには触れない"),
            read=False)
        self.read_up(self.impl, "hello")
        self.assert_plan_ok()
        plan = (self.impl / PLAN).read_text(encoding="utf-8")
        (self.impl / PLAN).write_text(plan.replace(", docs/b.md:3", ""), encoding="utf-8")
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("docs/b.md", r.stderr)

    def test_plan_and_apply_must_use_configured_skills_and_tools(self) -> None:
        self.set_config(self.impl, skills={"plan": ["domain-modeler"], "apply": ["tdd"]},
                        tools={"plan": ["github"], "apply": []})
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("`domain-modeler`, `github`", r.stderr)
        used = PLAN_ALIGNED.replace("## 使ったスキルと道具\n\nなし",
                                    "## 使ったスキルと道具\n\n- `domain-modeler` — 用語を確かめた\n- `github` — 関連 PR を見た")
        self.write_plan(used)
        self.assert_plan_ok()
        self.assertIn("道具: `github`", self.run_pa(self.impl, "show").stdout)

        (self.impl / "src/app.py").write_text("def hello():\n    print('hi')\n    return 1\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("スキルの手順で見直していません", r.stderr)
        (self.impl / ".codd/apply.md").write_text("- 先にテストを書いた\n", encoding="utf-8")
        self.assertIn("`tdd`", self.run_pa(self.impl, "verify-apply").stderr)
        # 名前だけ書いても通さない。変えたファイルを挙げさせ、手順と差分を並べた資料で見直させる。
        (self.impl / ".codd/apply.md").write_text("- `tdd` — 先にテストを書いた\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("スキルの手順で見直していません", r.stderr)
        review = (self.impl / ".codd/skill-review.md").read_text(encoding="utf-8")
        self.assertIn("## `tdd`（自分）", review)
        self.assertIn("+    print('hi')", review)
        self.assertTrue(self.run_pa(self.impl, "advise").stdout.startswith("AUTO APPLY\n"))   # 訊かずに見直させる
        (self.impl / ".codd/apply.md").write_text("- `tdd` — src/app.py: 先にテストを書いた\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.impl / ".codd/skill-review.md").exists())

    def test_batch_hands_over_the_skills_before_changing(self) -> None:
        commit(self.impl, {".agents/skills/tdd-lite/SKILL.md":
                           "---\nname: tdd-lite\ndescription: テストを先に書く\n---\n\n# tdd-lite\n\n先にテストを書く。\n"}, "skill")
        self.set_config(self.impl, skills={"plan": [], "apply": ["tdd-lite"]})
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        r = self.run_pa(self.impl, "batch")   # 変える前に手順が目に入り、読み込んだと控える
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("## 変えるときに従う手順", r.stdout)
        self.assertIn("先にテストを書く。", r.stdout)
        r = self.run_pa(self.impl, "batch")   # 同じ回で読み込み済みなら、名前だけ
        self.assertIn("この回で読み込み済み（その手順に従う）: `tdd-lite`", r.stdout)
        self.assertNotIn("先にテストを書く。", r.stdout)
        (self.impl / "src/app.py").write_text("def hello():\n    return 1  # log\n", encoding="utf-8")
        (self.impl / ".codd/apply.md").write_text("- `tdd-lite` — src/app.py: テストを先に書いた\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_subagent_hands_each_batch_over(self) -> None:
        # subagent が true なら、親は段を codd-apply に渡すだけで、手順の全文を読まない。
        # 渡された側（batch --worker）は新しい文脈なので、読み込み済みの手順も毎回全文で受け取る。
        commit(self.impl, {".agents/skills/tdd-lite/SKILL.md":
                           "---\nname: tdd-lite\ndescription: テストを先に書く\n---\n\n# tdd-lite\n\n先にテストを書く。\n"}, "skill")
        self.set_config(self.impl, skills={"plan": [], "apply": ["tdd-lite"]}, subagent=True)
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        r = self.run_pa(self.impl, "batch")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("`codd-apply` サブエージェントに渡す", r.stdout)
        self.assertIn("codd.py batch --worker", r.stdout)   # 呼べないときは自分で変える
        self.assertNotIn("先にテストを書く。", r.stdout)
        for _ in range(2):
            r = self.run_pa(self.impl, "batch", "--worker")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("段に分けていません。次のファイルを、この 1 回ですべて変えてください", r.stdout)
            self.assertIn("- src/app.py", r.stdout)   # 変えるファイルを並べる（既定の段の大きさで自分で分けると読ませない）
            self.assertIn("先にテストを書く。", r.stdout)
        (self.impl / "src/app.py").write_text("def hello():\n    return 1  # log\n", encoding="utf-8")
        (self.impl / ".codd/apply.md").write_text("- `tdd-lite` — src/app.py: テストを先に書いた\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")   # 渡された側が読み込んだ記録で、検査が通る
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_worker_on_another_stage_stops_without_changing(self) -> None:
        # 渡す文には段の番号を書く。渡された側の作業フォルダの記録（batches.json）が別の段なら、変えずに止める
        self.set_config(self.impl, subagent=True)
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        r = self.run_pa(self.impl, "batch")
        self.assertIn("batch --worker --stage 1", r.stdout)
        self.assertIn(f"作業フォルダ: {self.impl}", r.stdout)
        self.assertEqual(self.run_pa(self.impl, "batch", "--worker", "--stage", "1").returncode, 0)
        r = self.run_pa(self.impl, "batch", "--worker", "--stage", "2")
        self.assertEqual(r.returncode, 1)
        self.assertIn("段が食い違っています: 渡されたのは段 2", r.stderr)

    def test_files_waived_in_own_changes_are_not_batched(self) -> None:
        # 自分の変更案で「変更不要」と書いたファイルは、変える段に入れない（変えずに止まる・変え残しと読ませない）
        commit(self.impl, {"src/other.py": "X = 1\n"}, "other")
        self.write_plan(PLAN_ALIGNED.replace("- src/app.py — `hello` の中でログを出す",
                                             "- src/app.py — `hello` の中でログを出す\n- src/other.py — 変更不要: 定数だけ"))
        self.assert_plan_ok()
        batches = json.loads((self.impl / ".codd/batches.json").read_text(encoding="utf-8"))["batches"]
        files = [rel for b in batches for _, rel in b]
        self.assertIn("src/app.py", files)
        self.assertNotIn("src/other.py", files)

    def test_subagent_must_be_true_or_false(self) -> None:
        self.set_config(self.impl, subagent="yes")
        r = self.run_pa(self.impl, "show")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("subagent は true か false です", r.stderr)

    def use_guides(self, plan: str, *lines: str) -> str:
        return plan.replace("## 使ったスキルと道具\n\nなし", "## 使ったスキルと道具\n\n" + "\n".join(lines))

    def test_guides_run_when_a_matching_file_is_changed(self) -> None:
        # 特定のファイルを作る・変えるときの手順。計画に挙がった時点で読み込ませ、変えたら当たったファイルごとに記録させる。
        commit(self.impl, {".agents/skills/py-style/SKILL.md":
                           "---\nname: py-style\ndescription: Python の書き方\n---\n\n型ヒントを付ける。\n"}, "skill")
        self.set_config(self.impl, guides=[{"use": "skill:py-style", "when": {"files": ["src/**/*.py"]}},
                                           {"use": "skill:unused", "when": {"files": ["docs/"]}}])
        self.assertIn("  - src/**/*.py → `py-style`（codd.json）", self.run_pa(self.impl, "show").stdout)
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("ファイルに決められた手順", r.stderr)
        self.assertIn("`py-style`（src/app.py）", r.stderr)
        self.assertNotIn("unused", r.stderr)   # 当たらない glob は求めない
        self.write_plan(self.use_guides(PLAN_ALIGNED, "- `py-style` — 型ヒントを付けると決めた"))
        r = self.run_pa(self.impl, "verify-plan")   # 挙げるだけでは通さない。読み込ませる
        self.assertEqual(r.returncode, 1)
        self.assertIn("スキルを読み込んでいません", r.stderr)
        self.run_pa(self.impl, "guide", "skill:py-style")
        self.assert_plan_ok()

        r = self.run_pa(self.impl, "batch")
        self.assertIn("`py-style` の手順で変えるファイル", r.stdout)
        self.assertIn("src/app.py", r.stdout)
        (self.impl / "src/app.py").write_text("def hello() -> int:\n    return 1  # log\n", encoding="utf-8")
        (self.impl / "src/extra.py").write_text("X: int = 1\n", encoding="utf-8")
        (self.impl / ".codd/apply.md").write_text(
            "- `py-style` — src/app.py: 型ヒントを付けた\n\n## 計画との違い\n\n- src/extra.py — 追加: 定数を分けた\n",
            encoding="utf-8")
        self.run_pa(self.impl, "accept")
        r = self.run_pa(self.impl, "verify-apply")   # 計画に無く足したファイルにも、手順を効かせる
        self.assertEqual(r.returncode, 1)
        self.assertIn("`py-style`", r.stderr)
        self.assertIn("src/extra.py", (self.impl / ".codd/skill-review.md").read_text(encoding="utf-8"))
        (self.impl / ".codd/apply.md").write_text(
            "- `py-style` — src/app.py, src/extra.py: 型ヒントを付けた\n\n## 計画との違い\n\n"
            "- src/extra.py — 追加: 定数を分けた\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_guides_of_a_ref_apply_whichever_repo_calls(self) -> None:
        # 参照先のファイルの手順は、参照先に置いた codd.json で決める。どのリポジトリから変えても同じ手順が効く。
        commit(self.design, {".agents/skills/api-doc/SKILL.md":
                             "---\nname: api-doc\ndescription: API の書き方\n---\n\n戻り値を表で書く。\n"}, "skill")
        self.set_config(self.design, guides=[{"use": "skill:api-doc", "when": {"files": ["docs/**/*.md"]}}])
        self.assertIn("  - design:docs/**/*.md → `api-doc`", self.run_pa(self.impl, "show").stdout)
        self.write_plan(PLAN_DRIFT)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("`api-doc`（design:docs/api.md）", r.stderr)   # 参照先が 1 つでも名前を付ける（自分のファイルと見分ける）
        r = self.run_pa(self.impl, "guide", "api-doc")   # 参照先に置いたスキルも名前で読める
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("戻り値を表で書く。", r.stdout)
        self.write_plan(self.use_guides(PLAN_DRIFT, "- `api-doc` — 戻り値を表で書く"), read=False)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。\n\n実装は `src/app.py`（`src/` の下）。\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("`api-doc`", r.stderr)
        (self.impl / ".codd/apply.md").write_text("- `api-doc` — design:docs/api.md: 戻り値を書いた\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_guide_document_placed_with_front_matter_needs_answers_and_its_check(self) -> None:
        # 先頭に `codd:` を書いた手順の文書は、置くだけで効く。見出しで節だけを読ませ、確かめることに 1 項目ずつ答えさせ、
        # 手順の確かめるコマンドも動かす。
        commit(self.impl, {
            ".agents/guides/py.md": "---\ncodd:\n  files: [\"src/*.py\"]\n  change: [update]\n"
                                    "  check: [\"python3\", \"-c\", \"import sys; sys.exit('# log' not in open('src/app.py').read())\"]\n"
                                    "---\n\n# Python の手順\n\n## 確かめること\n\n- [ ] 戻り値を変えていない\n",
            "docs/notes.md": "# 書き方\n\n## 節\n\n節の中身\n\n## 別\n\n別の中身\n"}, "guide")
        self.set_config(self.impl, guides=[{"use": "docs/notes.md#節", "when": {"files": ["src/new_*.py"], "change": ["create"]},
                                            "asks": ["名前を書いた"]}])
        r = self.run_pa(self.impl, "show")
        self.assertIn("src/*.py update → .agents/guides/py.md", r.stdout)
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn(".agents/guides/py.md（src/app.py）", r.stderr)
        self.assertNotIn("docs/notes.md", r.stderr)    # 作るファイルだけに効く手順は、変えるファイルには効かない
        self.write_plan(self.use_guides(PLAN_ALIGNED, "- .agents/guides/py.md — 戻り値を変えない"))
        self.assertIn("手順の文書を読み込んでいません", self.run_pa(self.impl, "verify-plan").stderr)
        r = self.run_pa(self.impl, "guide", "docs/notes.md#節")
        self.assertIn("節の中身", r.stdout)
        self.assertNotIn("別の中身", r.stdout)          # 見出しで指した節だけ
        self.assertEqual(self.run_pa(self.impl, "guide", "docs/notes.md#無い").returncode, 1)
        self.run_pa(self.impl, "guide", ".agents/guides/py.md")
        self.assert_plan_ok()
        self.assertIn("- [ ] 戻り値を変えていない", self.run_pa(self.impl, "batch").stdout)

        (self.impl / "src/app.py").write_text("def hello():\n    return 1  # note\n", encoding="utf-8")
        (self.impl / ".codd/apply.md").write_text("- .agents/guides/py.md — src/app.py: 書き直した\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("手順の確かめることに答えていません", r.stderr)
        (self.impl / ".codd/apply.md").write_text(
            "- .agents/guides/py.md — src/app.py: 書き直した\n  - [x] 戻り値を変えていない — src/nothing.py\n",
            encoding="utf-8")
        self.assertIn("どの側にもありません", self.run_pa(self.impl, "verify-apply").stderr)
        (self.impl / ".codd/apply.md").write_text(
            "- .agents/guides/py.md — src/app.py: 書き直した\n  - [x] 戻り値を変えていない — src/app.py\n",
            encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")      # 申告が通っても、手順の確かめるコマンドで止める
        self.assertEqual(r.returncode, 1)
        self.assertIn("手順 .agents/guides/py.md の検査が失敗しました", r.stderr)
        (self.impl / "src/app.py").write_text("def hello():\n    return 1  # log\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_batches_are_split_by_the_guides_that_apply(self) -> None:
        # 効く手順の組ごとに段を分ける。手順の無いファイルは手順のある段の空きに詰める。
        files = {f"src/m{i}.sql": "--\n" for i in range(3)}
        files.update({f"src/p{i}.py": "x = 1\n" for i in range(3)})
        commit(self.impl, files, "files")
        self.set_config(self.impl, batch_files=4, guides=[{"use": "tool:sqlfmt", "when": {"files": ["**/*.sql"]}}])
        plan = PLAN_ALIGNED.replace("- src/app.py — `hello` の中でログを出す",
                                    "\n".join(f"- {p} — `hello` の中でログを出す" for p in ["src/app.py", *sorted(files)]))
        self.write_plan(self.use_guides(plan, "- `sqlfmt` — 整える"))
        self.assert_plan_ok()
        batches = json.loads((self.impl / ".codd/batches.json").read_text(encoding="utf-8"))["batches"]
        self.assertEqual([[rel for _, rel in b] for b in batches],
                         [["src/m0.sql", "src/m1.sql", "src/m2.sql", "src/app.py"], ["src/p0.py", "src/p1.py", "src/p2.py"]])
        self.assertIn("`sqlfmt` の手順の段", self.run_pa(self.impl, "batch").stdout)

    def test_an_old_plan_with_two_headings_is_read_as_one(self) -> None:
        commit(self.impl, {"CLAUDE.md": "# 約束\n"}, "rules")
        self.write_plan(PLAN_ALIGNED.replace("## 守る決まり\n\nなし", "## 守る決まり\n\n- CLAUDE.md — 短く書く"))
        self.assert_plan_ok()
        text = (self.impl / PLAN).read_text(encoding="utf-8")
        self.assertIn("## 従う手順\n\n- CLAUDE.md — 短く書く", text)
        self.assertNotIn("## 使ったスキルと道具", text)

    def test_a_caller_can_add_guides_to_a_ref_and_optional_guides_are_not_forced(self) -> None:
        commit(self.impl, {"docs/how-to-write-api.md": "# API の書き方\n\n- [ ] 例を 1 つ書いた\n"}, "guide")
        self.set_config(self.impl, guides=[{"use": "skill:lint-helper", "when": {"terms": ["lint"]}},
                                           {"use": "tool:gh", "must": False}],
                        refs=[{"name": "design", "path": "../design",
                               "guides": [{"use": "docs/how-to-write-api.md", "when": {"files": ["docs/*.md"]}}]}])
        r = self.run_pa(self.impl, "show")
        self.assertIn("  - design:docs/*.md → docs/how-to-write-api.md", r.stdout)
        self.assertIn("関係すれば使う手順", r.stdout)
        self.assertIn("（語: lint）", r.stdout)
        self.write_plan(PLAN_DRIFT)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertIn("docs/how-to-write-api.md（design:docs/api.md）", r.stderr)   # 呼び出し元が足した手順も効く
        self.assertNotIn("lint-helper", r.stderr)                            # 強制しない手順は求めない
        self.assertNotIn("`gh`", r.stderr)
        self.run_pa(self.impl, "guide", "docs/how-to-write-api.md")
        self.write_plan(self.use_guides(PLAN_DRIFT, "- docs/how-to-write-api.md — 例を足す"), read=False)
        self.assert_plan_ok()
        self.assertIn("- [ ] 例を 1 つ書いた", self.run_pa(self.impl, "batch").stdout)

    def test_guides_that_cannot_be_read_are_not_demanded(self) -> None:
        # 無い文書・無い見出し・何にも当たらない glob の手順は、読み込めずエージェントには直せない。求めずに show で知らせる。
        commit(self.impl, {"docs/notes.md": "# 書き方\n"}, "guide")
        self.set_config(self.impl, guides=[{"use": "docs/missing.md", "when": {"files": ["src/*.py"]}},
                                           {"use": "docs/notes.md#無い", "when": {"files": ["src/*.py"]}},
                                           {"use": "docs/rules/*.md", "when": {"files": ["src/*.py"]}}])
        r = self.run_pa(self.impl, "show")
        self.assertIn("docs/missing.md が読めません", r.stdout)
        self.assertIn("docs/notes.md#無い が読めません", r.stdout)
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()

    def test_a_caller_guide_for_a_ref_is_read_where_the_document_is(self) -> None:
        # refs[].guides の文書は、参照先にあれば参照先から読む（refs[].rules と同じ）。
        commit(self.design, {"docs/style.md": "# 書き方\n\n表で書く。\n"}, "guide")
        commit(self.impl, {"docs/mine.md": "# 呼び出し元の約束\n"}, "rule")
        self.set_config(self.impl, refs=[{"name": "design", "path": "../design", "guides": [{"use": "docs/mine.md"}]}])
        r = self.run_pa(self.impl, "show")   # 呼び出し元にしか無い文書は、呼び出し元の決まりとして読む
        self.assertIn("  - docs/mine.md\n", r.stdout)
        self.assertNotIn("当たるファイルがありません", r.stdout)
        self.set_config(self.impl, refs=[{"name": "design", "path": "../design",
                                          "guides": [{"use": "docs/style.md", "when": {"files": ["docs/*.md"]}}]}])
        self.assertNotIn("読めません", self.run_pa(self.impl, "show").stdout)
        self.write_plan(self.use_guides(PLAN_DRIFT, "- design:docs/style.md — 表で書く"))
        self.assertIn("design:docs/style.md", self.run_pa(self.impl, "verify-plan").stderr)   # 読み込ませる
        r = self.run_pa(self.impl, "guide", "docs/style.md")   # 参照先の名前を付け忘れても、ある側から読む
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assert_plan_ok()

    def test_a_guide_for_renaming_is_handed_over_before_changing(self) -> None:
        # 消す・名前を変えるときの手順は、変える前の見込み（あるファイルは「変える」）では当たらない。変える前に
        # 条件付きで渡し、変えたあとは git に足していない新しいファイルとの名前の変更も見分ける。
        commit(self.impl, {"src/old.py": "X = 1\n",
                           "docs/move.md": "# 動かす\n\n- [ ] 呼び出し元を直した\n"}, "guide")
        self.set_config(self.impl, guides=[{"use": "docs/move.md", "when": {"files": ["src/*.py"], "change": ["rename", "delete"]}}])
        plan = PLAN_ALIGNED.replace("- src/app.py — `hello` の中でログを出す",
                                    "- src/app.py — `hello` の中でログを出す\n- src/old.py — `hello` の中でログを出す")
        self.write_plan(plan)
        self.assert_plan_ok()      # 変える見込みのファイルには、名前を変える手順を求めない
        r = self.run_pa(self.impl, "batch")
        self.assertIn("docs/move.md の手順は、名前を変える・消すときだけ効く", r.stdout)
        (self.impl / "src/app.py").write_text("def hello():\n    return 1  # log\n", encoding="utf-8")
        (self.impl / "src/old.py").rename(self.impl / "src/new.py")
        (self.impl / ".codd/apply.md").write_text("## 計画との違い\n\n- src/new.py — 追加: 名前を変えた\n", encoding="utf-8")
        self.run_pa(self.impl, "accept")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("docs/move.md", r.stderr)
        self.assertIn("- src/new.py", (self.impl / ".codd/skill-review.md").read_text(encoding="utf-8"))  # 名前を変えた先も
        # 手順の行の下に、ファイルと答えを 1 行ずつ書いてよい。消したファイルも根拠に書ける。
        (self.impl / ".codd/apply.md").write_text(
            "- docs/move.md — 名前を変えた:\n  - src/old.py → src/new.py\n"
            "  - [x] 呼び出し元を直した — src/old.py を指すところは無い\n\n"
            "## 計画との違い\n\n- src/new.py — 追加: 名前を変えた\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_guides_can_point_into_a_ref_by_its_name(self) -> None:
        # `参照先の名前:パス`・`skill:参照先の名前:名前` で、ほかのリポジトリの文書・スキルを手順にできる。
        commit(self.design, {"docs/style.md": "# 書き方\n\n表で書く。\n",
                             ".agents/skills/api-doc/SKILL.md":
                             "---\nname: api-doc\ndescription: API の書き方\n---\n\n戻り値を表で書く。\n"}, "guide")
        self.set_config(self.impl, guides=[{"use": "design:docs/style.md"}])
        r = self.run_pa(self.impl, "show")    # いつも読む決まりも、参照先の名前で指せる
        self.assertIn("  - design:docs/style.md\n", r.stdout)
        self.assertNotIn("当たるファイルがありません", r.stdout)
        self.set_config(self.impl, guides=[{"use": "design:docs/style.md", "when": {"files": ["src/*.py"]}},
                                           {"use": "skill:design:api-doc", "when": {"files": ["src/*.py"]}}])
        self.assertNotIn("読めません", self.run_pa(self.impl, "show").stdout)
        self.write_plan(self.use_guides(PLAN_ALIGNED, "- design:docs/style.md — 表で書く", "- `design:api-doc` — 表で書く"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("読み込んでいません", r.stderr)
        self.assertEqual(self.run_pa(self.impl, "guide", "design:docs/style.md", "design:api-doc").returncode, 0)
        self.assert_plan_ok()
        self.assertIn("表で書く。", self.run_pa(self.impl, "batch").stdout)
        (self.impl / "src/app.py").write_text("def hello():\n    return 1  # log\n", encoding="utf-8")
        (self.impl / ".codd/apply.md").write_text(
            "- design:docs/style.md — src/app.py: 表で書いた\n- `design:api-doc` — src/app.py: 表で書いた\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_ref_skill_may_be_named_with_or_without_the_ref(self) -> None:
        # 参照先のスキルの手順は、`api-doc` とも `design:api-doc` とも書ける。どちらで読み込んでも読み込んだと数える。
        commit(self.design, {".agents/skills/api-doc/SKILL.md":
                             "---\nname: api-doc\ndescription: API の書き方\n---\n\n戻り値を表で書く。\n"}, "skill")
        self.set_config(self.design, guides=[{"use": "skill:api-doc", "when": {"files": ["docs/**/*.md"]}}])
        self.write_plan(self.use_guides(PLAN_DRIFT, "- `design:api-doc` — 戻り値を表で書く"))
        self.assertEqual(self.run_pa(self.impl, "guide", "design:api-doc").returncode, 0)
        self.assert_plan_ok()
        self.run_pa(self.impl, "guide", "design:api-doc")   # 変える段で読み込む（名前の書き方は問わない）
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。\n\n実装は `src/app.py`（`src/` の下）。\n", encoding="utf-8")
        (self.impl / ".codd/apply.md").write_text("- `design:api-doc` — design:docs/api.md: 戻り値を書いた\n",
                                                  encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_bad_guides_config_is_reported(self) -> None:
        for value in ({"use": "x.md"}, [{"use": "../x.md"}], [{"use": "skill:bad name"}], [{"use": "x.md", "when": {"phase": "x"}}],
                      [{"use": "x.md", "when": {"change": ["move"]}}], [{"use": "tool:gh", "check": ["x"]}], [{"use": "x.md", "y": 1}]):
            with self.subTest(value=value):
                self.set_config(self.impl, guides=value)
                r = self.run_pa(self.impl, "show")
                self.assertEqual(r.returncode, 2)
                self.assertIn("guides", r.stderr)

    # ------------------------------------------------------------ 最後までやり切る

    def test_verify_apply_needs_every_planned_file(self) -> None:
        self.write_plan(self.with_tests(PLAN_ALIGNED.replace(
            "- src/app.py — `hello` の中でログを出す",
            "- src/app.py — `hello` の中でログを出す\n- src/log.py — `hello` から使うログを新しく書く"),
            "- `log` — 変更不要: print を包むだけ"))
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('hi')\n    return 1\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("自分の変更案のファイルをまだ変えていません", r.stderr)
        self.assertIn("src/log.py", r.stderr)
        (self.impl / "src/log.py").write_text("def log(m):\n    print(m)\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_verify_apply_catches_callers_of_a_renamed_function(self) -> None:
        # 計画より広く（名前まで）変えたら、その呼び出し元を直したかを実際の差分から測る。
        self.add_caller()
        commit(self.impl, {"src/greet_user.py": "from app import greet\n"}, "another caller")
        self.write_plan(PLAN_ALIGNED.replace("## 影響範囲\n\nなし", "## 影響範囲\n\n- src/use.py — 変更不要: 戻り値は同じ"))
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def greet():\n    return 1\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("直していないファイル", r.stderr)
        self.assertIn("src/greet_user.py", r.stderr)

    def test_unchanged_quoted_words_on_edited_lines_are_not_measured(self) -> None:
        # 直した行に元からある `…` は変わった名前ではない。拾うと、無関係なファイルを「直していない」として必ず落とす。
        commit(self.impl, {"docs/use.md": "# use\n\nrun `make build` then `hello`.\n",
                           "src/other.py": "# make build\n", "src/renamed.py": "# old-flag\n"}, "docs")
        self.write_plan(PLAN_ALIGNED.replace(
            "- src/app.py — `hello` の中でログを出す",
            "- src/app.py — `hello` の中でログを出す\n- docs/use.md — `hello` の説明を足す"))
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('x')\n    return 1\n", encoding="utf-8")
        (self.impl / "docs/use.md").write_text("# use\n\nrun `make build` then `hello` (logs).\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        # 足した・消した `…` は今までどおり測る。
        (self.impl / "docs/use.md").write_text("# use\n\nrun `make build` then `hello` with `old-flag`.\n",
                                               encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("src/renamed.py", r.stderr)
        self.assertNotIn("src/other.py", r.stderr)

    def test_plan_compacts_waivers_without_losing_verification(self) -> None:
        commit(self.impl, {"src/a.py": "hello()\n", "src/b.py": "hello()\n",
                           "tests/test_a.py": "hello()\n", "tests/test_b.py": "hello()\n"}, "callers")
        self.set_config(self.impl, max_files=1)
        plan = PLAN_ALIGNED.replace("## 影響範囲\n\nなし",
                                   "## 影響範囲\n\n- src/a.py — 変更不要: 契約は同じ\n"
                                   "- src/b.py — 変更不要: 契約は同じ")
        plan = plan.replace("- 変更不要: このリポジトリにテストはまだ無い（例の小さなリポジトリ）",
                            "- tests/test_a.py — 変更不要: 期待値は同じ\n"
                            "- tests/test_b.py — 変更不要: 期待値は同じ")
        self.write_plan("<!-- 説明 -->\n" + plan)
        self.assert_plan_ok()
        compact = (self.impl / PLAN).read_text()
        self.assertNotIn("<!--", compact)
        self.assertNotIn("変更不要", compact)
        self.assertNotIn("src/a.py", compact)
        self.assertNotIn("tests/test_a.py", compact)
        self.assert_plan_ok()  # 繰り返し検査しても形が変わらない
        self.assertEqual((self.impl / PLAN).read_text(), compact)
        (self.impl / "src/app.py").write_text("def hello():\n    return 1 # changed\n")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.report(self.impl)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("変更不要", r.stdout)
        self.assertNotIn("src/a.py", r.stdout)
        self.assertNotIn("tests/test_a.py", r.stdout)
        self.assertEqual(self.run_pa(self.impl, "record").returncode, 0)
        self.assertNotIn("変更不要", (self.impl / PLAN).read_text())

    def test_replanning_keeps_waivers_hidden_from_the_plan(self) -> None:
        # 通ったときに計画から省いた変更不要の判断は、確認で NG になって計画を直しても失わない
        # （失うと、判断済みのファイルがまた未判断になり、テストの変更案が「なし」で必ず落ちる）。
        commit(self.impl, {"src/a.py": "hello()\n"}, "callers")
        self.write_plan(PLAN_ALIGNED.replace("## 影響範囲\n\nなし", "## 影響範囲\n\n- src/a.py — 変更不要: 契約は同じ"))
        self.assert_plan_ok()
        self.assertNotIn("変更不要", (self.impl / PLAN).read_text())
        self.run_pa(self.impl, "decide", "NG", "--note", "info で出して")
        plan = self.impl / PLAN
        plan.write_text(plan.read_text().replace("hello にログを足す。", "hello にログを info で足す。"))
        self.assert_plan_ok()
        saved = json.loads((self.impl / ".codd/plan-check.json").read_text())["text"]
        self.assertIn("- src/a.py — 変更不要: 契約は同じ", saved)
        self.assertIn("- 変更不要: このリポジトリにテストはまだ無い", saved)
        self.assertIn("info で足す", saved)

    def test_replanning_restores_a_waiver_whose_file_is_only_mentioned(self) -> None:
        # 練り直した計画の別の見出しにパスが出てくるだけなら、省いた判断を戻す（戻さないとまた未判断になる）
        commit(self.impl, {"src/a.py": "hello()\n"}, "callers")
        self.write_plan(PLAN_ALIGNED.replace("## 影響範囲\n\nなし", "## 影響範囲\n\n- src/a.py — 変更不要: 契約は同じ"))
        self.assert_plan_ok(ok=False)
        self.run_pa(self.impl, "decide", "NG", "--note", "info で出して")
        plan = self.impl / PLAN
        plan.write_text(plan.read_text().replace("hello にログを足す。", "hello にログを info で足す（src/a.py と同じ）。"))
        self.assert_plan_ok()
        saved = json.loads((self.impl / ".codd/plan-check.json").read_text())["text"]
        self.assertIn("- src/a.py — 変更不要: 契約は同じ", saved)

    def test_plan_keeps_distinct_waiver_reasons(self) -> None:
        commit(self.impl, {"src/a.py": "hello()\n", "src/b.py": "hello()\n"}, "callers")
        self.write_plan(PLAN_ALIGNED.replace("## 影響範囲\n\nなし",
                       "## 影響範囲\n\n- src/a.py — 変更不要: 契約は同じ\n"
                       "- src/b.py — 変更不要: 表示だけ"))
        self.assert_plan_ok()
        text = (self.impl / PLAN).read_text()
        self.assertNotIn("変更不要", text)
        saved = json.loads((self.impl / ".codd/plan-check.json").read_text())["text"]
        self.assertIn("- src/a.py — 変更不要: 契約は同じ", saved)
        self.assertIn("- src/b.py — 変更不要: 表示だけ", saved)
        self.assert_plan_ok()

    def test_hidden_plan_judgments_cannot_change_after_approval(self) -> None:
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        saved = self.impl / ".codd/plan-check.json"
        value = json.loads(saved.read_text())
        value["text"] = value["text"].replace("このリポジトリにテストはまだ無い", "理由を書き換えた")
        saved.write_text(json.dumps(value))
        (self.impl / "src/app.py").write_text("def hello():\n    return 1 # changed\n")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("利用者が確かめたものではありません", r.stderr)

    def test_legacy_plan_records_are_excluded_and_not_read(self) -> None:
        commit(self.impl, {"src/app.py": "def hello():\n    return 1 # hello\n"}, "outside codd")
        for folder in ("docs/.plans", "docs/.plan"):
            old = folder + "/2026-10-01-0000-old.md"
            commit(self.impl, {old: "# hello\n\n- src/app.py\n\n## 結果\n"}, "old record")
        r = self.run_pa(self.impl, "lint", "--no-test")
        self.assertIn("codd を通らずに入った変更（", r.stdout)
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        self.run_pa(self.design, "explore", "--term", "hello")
        self.assertNotIn("docs/.plan", (self.design / ".codd/explore.md").read_text())
        (self.impl / "docs/.plans/2026-10-01-0000-old.md").write_text("changed")
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_plan_is_kept_as_a_decision_record(self) -> None:
        # 計画は .plans/日時-名前.md に書き、終わりに確認の答えと結果を書き足して、その名前のまま残す。
        self.write_plan(PLAN_DRIFT)
        self.assert_plan_ok(ok=False)
        self.run_pa(self.impl, "decide", "NG", "--note", "戻り値の説明も直して")
        self.assert_plan_ok(ok=False)   # 同じ回の練り直しでは同じ計画を直す
        self.run_pa(self.impl, "decide", "OK")
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。\n\n実装は `src/app.py`（`src/` の下）。\n", encoding="utf-8")
        # 計画のフォルダは変えたファイルに数えない（記録のために置くもの）。
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 0)
        self.assertEqual(self.report(self.impl).returncode, 0)
        # 記録に要らないもの（使わなかったスキル・関係なしとしたファイル・中身が「なし」の見出し）は残さない。
        plan = self.impl / PLAN
        plan.write_text(plan.read_text(encoding="utf-8").replace(
            "## 従う手順\n\n",
            "## 従う手順\n\n- `tdd-lite` — 使わない: テストは無い\n- `grep` — 呼び出し元を探した\n").replace(
            "## 参照先のその他\n\n- なし", "## 参照先のその他\n\n- 関係なし: docs/api.md:1 — 題名だけ\n  続きの説明"),
            encoding="utf-8")
        # 前に確かめてから変える流れを 3 回終えたら、確かめる流れを勧める（done のアクションが利用者に尋ねる）。
        (self.impl / ".codd/runs-since-review.json").write_text('{"runs": 2}', encoding="utf-8")
        r = self.run_pa(self.impl, "record")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("REVIEW 確かめる流れを回さずに、変える流れを 3 回終えました。", r.stdout)
        records = list((self.impl / ".plans").glob("*.md"))
        self.assertEqual(records, [plan])     # 置いたときの名前のまま残す
        self.assertIn(f"計画の記録: {PLAN}", r.stdout)
        text = plan.read_text(encoding="utf-8")
        self.assertIn("## 自分の変更案", text)
        self.assertIn("- `grep` — 呼び出し元を探した", text)
        self.assertNotIn("使わない", text)
        self.assertNotIn("関係なし", text)
        self.assertNotIn("続きの説明", text)
        self.assertNotIn("## 参照先のその他", text)          # 中身が無くなった見出しは消す
        self.assertNotIn("<!--", text)
        self.assertRegex(text, r"## 確認と判断\n\n- [-0-9: ]+ NG（計画を直す）: 戻り値の説明も直して\n- [-0-9: ]+ OK（計画で進める）")
        self.assertIn("## 結果\n\n- 変えたあとの検査: 通った", text)
        self.assertIn("### 自分（実装）\n", text)
        self.assertNotIn(str(self.tmp), text)      # この端末だけの絶対パスは、コミットする記録に残さない
        self.assertFalse((self.impl / ".codd/decisions.json").exists())
        self.assertEqual(self.run_pa(self.impl, "record").returncode, 1)   # 記録は 1 回だけ
        # 終わった回の記録は書き換えさせない（次の回の検査で落とす）。
        git(self.impl, "add", "-A")
        git(self.impl, "commit", "-q", "-m", "change")
        plan.write_text(text.replace("通った", "通らなかった"), encoding="utf-8")
        (self.impl / ".plans/2026-10-02-0000-next.md").write_text(PLAN_ALIGNED, encoding="utf-8")
        self.read_up(self.impl)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("終わった回の計画の記録を書き換えています", r.stderr)

    def test_discarding_a_committed_unfinished_plan_is_not_a_record_change(self) -> None:
        # 途中の計画がコミットされたあとで `draft --new` で書き直しても、終わった回の記録とは見なさない。
        self.write_plan(PLAN_ALIGNED)
        git(self.impl, "add", "-A")
        git(self.impl, "commit", "-q", "-m", "wip")
        r = self.run_pa(self.impl, "draft", "--new", "--name", "retry")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.impl / PLAN).exists())
        new = next((self.impl / ".plans").glob("*-retry.md"))
        new.write_text(PLAN_ALIGNED, encoding="utf-8")
        self.read_up(self.impl)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertNotIn("終わった回の計画の記録", r.stderr)
        self.assertEqual(r.returncode, 0, r.stderr)
        # 捨てた計画を戻してしまっても、記録のときに消す（次の回に、それが進めている計画にならない）。
        git(self.impl, "checkout", "--", PLAN)
        r = self.run_pa(self.impl, "record")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"捨てた計画を消しました: {PLAN}", r.stdout)
        self.assertEqual(list((self.impl / ".plans").glob("*.md")), [new])

    def test_record_reports_and_closes_the_plan_in_one_command(self) -> None:
        # 終わり（done・stopped）は record だけを呼ぶ。report を別に呼ばなくても、報告を出して結果を計画に書く。
        for name in ("done.md", "stopped.md"):
            action = (self.impl / ".statemachine/codd/actions" / name).read_text(encoding="utf-8")
            self.assertIn("codd.py record", action)
            self.assertNotIn("codd.py report\n", action)
        self.write_plan(PLAN_DRIFT)
        self.assert_plan_ok(ok=False)
        self.run_pa(self.impl, "decide", "OK")
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。\n\n実装は `src/app.py`（`src/` の下）。\n", encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 0)
        r = self.run_pa(self.impl, "record")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("- 変えたあとの検査: 通った", r.stdout)       # 報告をそのまま伝えられる
        self.assertIn(f"計画の記録: {PLAN}", r.stdout)
        text = (self.impl / PLAN).read_text(encoding="utf-8")
        self.assertIn("## 結果\n\n- 変えたあとの検査: 通った", text)
        self.assertIn("- src/app.py — 変えた", text)

    def test_next_draft_closes_a_finished_plan_left_without_a_record(self) -> None:
        # 前の回が記録を書かずに終わると、次の回がその計画と前の回の印を引き継ぎ、前の回の変更で落ちていた。
        self.write_plan(PLAN_DRIFT)
        self.assert_plan_ok(ok=False)
        self.run_pa(self.impl, "decide", "OK")
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。\n\n実装は `src/app.py`（`src/` の下）。\n", encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 0)
        self.assertEqual(self.report(self.impl).returncode, 0)   # record を呼ばずに終わった
        for repo in (self.impl, self.design):
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "previous run")
        r = self.run_pa(self.impl, "draft", "--name", "next")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"前の回の計画を、記録として閉じました: {PLAN}", r.stdout)
        self.assertIn("## 結果\n\n- 変えたあとの検査: 通った", (self.impl / PLAN).read_text(encoding="utf-8"))
        new = next((self.impl / ".plans").glob("*-next.md"))
        new.write_text(PLAN_ALIGNED, encoding="utf-8")
        self.read_up(self.impl)
        self.assert_plan_ok(ok=False)
        self.run_pa(self.impl, "decide", "OK")
        (self.impl / "src/app.py").write_text("def hello():\n    print('hello')\n    return 2\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)   # 前の回の design の変更を、この回のものと数えない
        self.assertEqual(self.run_pa(self.impl, "record").returncode, 0)
        decided = new.read_text(encoding="utf-8").split("## 確認と判断", 1)[1].split("## 結果", 1)[0]
        self.assertEqual(decided.count("OK（計画で進める）"), 1)   # 前の回の答えを混ぜない

    def test_next_draft_closes_a_stuck_plan_the_user_committed(self) -> None:
        # 変えたあとの検査で止まったまま、利用者が自分で直してコミットし、ブランチを分けずに次の依頼へ進んだ。
        # 計画を引き継ぐと、前のコミットをこの回の「計画に無い変更」と数えて落ちていた。
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('a')\n    return 1\n", encoding="utf-8")
        (self.impl / "src/extra.py").write_text("X = 1\n", encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 1)
        git(self.impl, "add", "-A")
        git(self.impl, "commit", "-q", "-m", "fixed by hand")
        r = self.run_pa(self.impl, "draft", "--name", "next")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"前の回の計画を、記録として閉じました: {PLAN}", r.stdout)
        self.assertIn("STOP（やめる）: 変える前の印のあとにコミットされ", (self.impl / PLAN).read_text(encoding="utf-8"))
        new = next((self.impl / ".plans").glob("*-next.md"))
        new.write_text(PLAN_ALIGNED, encoding="utf-8")
        self.read_up(self.impl)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('b')\n    return 1\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 0, r.stderr)   # 前のコミットの src/extra.py を数えない

    def test_a_commit_midway_with_work_left_keeps_the_same_run(self) -> None:
        # 作業中の変更が残っていれば、途中でコミットしただけの同じ回として計画を引き継ぐ。
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('a')\n    return 1\n", encoding="utf-8")
        (self.impl / "src/extra.py").write_text("X = 1\n", encoding="utf-8")
        git(self.impl, "add", "src/app.py")
        git(self.impl, "commit", "-q", "-m", "midway")
        r = self.run_pa(self.impl, "draft", "--name", "next")
        self.assertIn(f"計画はもうあります: {PLAN}", r.stdout)
        self.assertIn("src/extra.py", self.run_pa(self.impl, "verify-apply").stderr)

    def test_changing_waits_until_the_users_ok_is_logged(self) -> None:
        # 確認の答え（decide OK）を控えずに変える段へ来たら、変え始める前に止める（推し量って控えない）。
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok(ok=False)
        r = self.run_pa(self.impl, "batch")
        self.assertEqual(r.returncode, 1)
        self.assertIn("計画の確認の答えが控えられていません", r.stderr)
        (self.impl / "src/app.py").write_text("def hello():\n    print('hello')\n    return 1\n", encoding="utf-8")
        self.assertIn("計画の確認の答えが控えられていません", self.run_pa(self.impl, "verify-apply").stderr)
        self.run_pa(self.impl, "decide", "OK")
        self.assertEqual(self.run_pa(self.impl, "batch").returncode, 0)
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 0)

    def test_stopped_run_is_recorded_as_stopped_without_a_logged_answer(self) -> None:
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        r = self.run_pa(self.impl, "record", "--stop")   # decide STOP を呼ばずに、やめたときの段に来た
        self.assertEqual(r.returncode, 0, r.stderr)
        text = (self.impl / PLAN).read_text(encoding="utf-8")
        self.assertIn("STOP（やめる）: 答えの控えが無かった", text)
        self.assertIn("- 変えたあとの検査: 利用者がやめた", text)

    def test_files_changed_while_planning_stop_before_the_baseline(self) -> None:
        # 計画の段で変えたファイルは、印に入ると変えたあとの検査が数えない。印を取る前に止め、利用者に確かめる。
        self.write_plan(PLAN_ALIGNED)
        (self.impl / "src/extra.py").write_text("x = 1\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("計画を練っている間に、ファイルが変わっています", r.stderr)
        self.assertIn("src/extra.py", r.stderr)
        self.assertFalse((self.impl / ".codd/before.json").exists())
        advice = self.run_pa(self.impl, "advise").stdout
        self.assertNotIn("AUTO", advice)   # 利用者の変更かもしれない。訊かずに戻させない
        self.assertIn("利用者が変えたものとして認め", advice)
        # 利用者が自分で変えたと答えたら、計画の前からの変更として認めて続ける
        self.assertEqual(self.run_pa(self.impl, "accept").returncode, 0)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('hi')\n    return 1\n", encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 0)

    def test_changes_after_the_baseline_are_still_counted_when_replanning(self) -> None:
        # 確認で NG のあと、計画の段で変えたものは、取り直さない印から変えたあとの検査が数える。
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok(ok=False)
        self.run_pa(self.impl, "decide", "NG", "--note", "直して")
        (self.impl / "src/extra.py").write_text("x = 1\n", encoding="utf-8")
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    print('hi')\n    return 1\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("src/extra.py", r.stderr)

    def test_reading_logs_do_not_outlive_a_run_stopped_before_the_plan_passed(self) -> None:
        self.write_plan("# 変更の計画\n\n## やりたいこと\n\nx\n")
        self.assertEqual(self.run_pa(self.impl, "verify-plan").returncode, 1)
        self.run_pa(self.impl, "decide", "STOP")
        self.assertEqual(self.run_pa(self.impl, "record", "--stop").returncode, 0)
        left = {p.name for p in (self.impl / ".codd").iterdir()}
        for name in ("explore.json", "rules-read.json", "problems.json", "run-start.json"):
            self.assertNotIn(name, left)

    def test_draft_keeps_the_plan_while_the_run_goes_on(self) -> None:
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok(ok=False)
        self.run_pa(self.impl, "decide", "NG", "--note", "直して")
        r = self.run_pa(self.impl, "draft", "--name", "again")
        self.assertIn(f"計画はもうあります: {PLAN}", r.stdout)
        self.assertNotIn("## 結果", (self.impl / PLAN).read_text(encoding="utf-8"))

    def test_record_when_stopped_before_changing(self) -> None:
        self.write_plan(PLAN_ALIGNED)
        self.run_pa(self.impl, "decide", "STOP", "--note", "今回はやめる")
        r = self.run_pa(self.impl, "record")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = next((self.impl / ".plans").glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("STOP（やめる）: 今回はやめる", text)
        self.assertIn("## 結果\n\n- 変えていない", text)

    def test_record_does_not_close_a_run_the_check_still_stops(self) -> None:
        # 変えたあとの検査が止めたまま閉じると、止めた理由も消えて続けられない。やめるなら --stop
        self.write_plan(PLAN_DRIFT)
        self.assert_plan_ok()
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 1)
        r = self.run_pa(self.impl, "record")
        self.assertEqual(r.returncode, 1)
        self.assertIn("検査が止めたままです", r.stderr)
        self.assertTrue((self.impl / ".codd/problems.json").is_file())
        self.assertEqual(self.run_pa(self.impl, "record", "--stop").returncode, 0)

    def test_report_summarises_the_result(self) -> None:
        self.write_plan(PLAN_DRIFT)
        self.assert_plan_ok()
        self.assertEqual(self.report(self.impl).returncode, 1)  # まだ変えたあとの検査を通っていない
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。\n\n実装は `src/app.py`（`src/` の下）。\n", encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 0)
        r = self.report(self.impl)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("- 変えたあとの検査: 通った", r.stdout)
        self.assertIn("- src/app.py — 変えた", r.stdout)
        self.assertIn("## design（設計書）", r.stdout)
        self.assertIn("- docs/api.md — 変えた", r.stdout)
        self.assertIn("- `hello` の引数を足す — 別の回に回す", r.stdout)
        self.assertTrue((self.impl / ".codd/report.md").is_file())
        (self.impl / "src/app.py").write_text("def hello():\n    return 3\n", encoding="utf-8")
        r = self.report(self.impl)
        self.assertEqual(r.returncode, 1)
        self.assertIn("通ったあとに、さらに変わっている", r.stdout)

    def test_rules_in_the_ref_can_be_configured(self) -> None:
        commit(self.design, {"docs/coding-rules.md": "# コーディングルール\n\n関数名は動詞で始める。\n"}, "rules")
        self.set_config(self.impl, refs=[{"name": "design", "path": "../design", "rules": ["docs/coding-rules.md"]}])
        self.assertIn("  - design:docs/coding-rules.md", self.run_pa(self.impl, "show").stdout)
        self.write_plan(PLAN_ALIGNED)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("docs/coding-rules.md", r.stderr)
        self.write_plan(PLAN_ALIGNED.replace("## 守る決まり\n\nなし",
                                             "## 守る決まり\n\n- docs/coding-rules.md — `hello` は動詞で始まる"))
        self.assert_plan_ok()

    def test_rules_are_discovered_and_written(self) -> None:
        commit(self.design, {"docs/coding-rules.md": "# コーディングルール\n",
                             "docs/conventions/naming.md": "# 名前\n",
                             "docs/guide.md": "# コーディング規約\n\n本文\n",
                             "CHANGELOG.md": "# rules の変更履歴\n",
                             # スキルの中の決まり（そのスキルを使うときに読むもの）は候補にしない。
                             ".agents/skills/react/SKILL.md": "---\nname: react\n---\n# React のルール\n",
                             ".agents/skills/react/rules/naming.md": "# 名前のルール\n",
                             # 日付で始まる計画や記録は、語が当たっても決まりではない。
                             "docs/plans/2026-08-15-execution-policy-design.md": "# 実行ポリシー\n"}, "rules")
        r = self.run_pa(self.impl, "show")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("決まりの候補（設定に無い", r.stdout)
        for rel in ("docs/coding-rules.md", "docs/conventions/naming.md", "docs/guide.md"):
            self.assertIn(f"  - design:{rel}", r.stdout)
        self.assertNotIn("docs/api.md", r.stdout)
        self.assertNotIn("CHANGELOG.md", r.stdout)
        self.assertNotIn("  - design:.agents/skills", r.stdout)
        self.assertNotIn("execution-policy", r.stdout)
        # 書くとすべて書く。書いたものは決まりになり、候補からは消える。
        r = self.run_pa(self.impl, "show", "--write-rules")
        self.assertIn("3 件を codd.json に書きました", r.stdout)
        cfg = json.loads((self.impl / ".statemachine/codd/codd.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["refs"], [{"path": "../design", "name": "design", "guides": [
            {"use": "docs/coding-rules.md"}, {"use": "docs/conventions/naming.md"}, {"use": "docs/guide.md"}]}])
        r = self.run_pa(self.impl, "show")
        self.assertIn("  - design:docs/coding-rules.md", r.stdout.split("決まりの候補")[0])
        self.assertNotIn("決まりの候補", r.stdout)

    def test_install_discovers_rules(self) -> None:
        commit(self.design, {"docs/coding-rules.md": "# コーディングルール\n"}, "rules")
        app = self.tmp / "app"
        app.mkdir()
        git(app, "init", "-q", "-b", "main")
        init.init_repo(app, "impl", ["../design"])
        path = app / ".statemachine/codd/codd.json"
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["refs"][0]["guides"], [{"use": "docs/coding-rules.md"}])
        # 既にある codd.json には探し直して書き足さない（手で消した決まりが戻らない）。
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg["refs"][0]["guides"] = []
        path.write_text(json.dumps(cfg), encoding="utf-8")
        init.init_repo(app, None, None)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["refs"][0]["guides"], [])

    def test_rules_accept_globs(self) -> None:
        commit(self.design, {"docs/rules/coding.md": "# a\n", "docs/rules/naming/api.md": "# b\n",
                             "docs/rules/notes.txt": "x\n", "docs/api-rules.md": "# c\n"}, "rules")
        self.set_config(self.impl, refs=[{"name": "design", "path": "../design",
                                          "rules": ["docs/rules/**/*.md", "docs/*-rules.md", "docs/gone/*.md"]}])
        r = self.run_pa(self.impl, "show")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("に挙げる）:\n  - design:docs/rules/coding.md\n  - design:docs/rules/naming/api.md\n"
                      "  - design:docs/api-rules.md\n", r.stdout)
        self.assertNotIn("notes.txt", r.stdout)
        self.assertIn("! design:docs/gone/*.md に当たるファイルがありません", r.stdout)
        self.assertNotIn("設定に書く）:\n  - design:docs/rules", r.stdout)  # glob で当たったものは候補に出さない
        self.write_plan(PLAN_ALIGNED.replace("## 守る決まり\n\nなし",
                                             "## 守る決まり\n\n- docs/rules/coding.md — 動詞で始める"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("docs/rules/naming/api.md", r.stderr)
        self.set_config(self.impl, rules=["../outside.md"])
        self.assertEqual(self.run_pa(self.impl, "show").returncode, 2)

    def test_scoped_long_folder_still_finds_tests_and_skips_machine_files(self) -> None:
        # git 2.43 などでは、scope の最初のフォルダ名が .codd より長いと `:(exclude).codd` を付けた ls-files が
        # 何も返さない。除外は自前で行い、テストを取りこぼさない。マシンのファイル（計画など）は数えない。
        commit(self.impl, {"services/api/handler.py": "def hello():\n    return 1\n",
                           "services/api/test_handler.py": "from handler import hello\n"}, "service")
        self.set_config(self.impl, scope=["services"])
        self.assertIn("自分 1 files", self.run_pa(self.impl, "show").stdout)
        (self.impl / ".plans").mkdir(parents=True, exist_ok=True)
        (self.impl / PLAN).write_text("# 下書き\n", encoding="utf-8")
        self.set_config(self.impl, scope=[])
        r = self.run_pa(self.impl, "explore", "--term", "下書き")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(".plans", (self.impl / ".codd/explore.md").read_text(encoding="utf-8"))

    def test_graph_is_not_rebuilt_when_only_the_plan_changes(self) -> None:
        # 練り直しで計画を書き直すたびに、グラフを作り直さない（作り直しは長くかかる）。
        self.use_graphify_stub()
        self.assertIn("graphify: updated", self.run_pa(self.impl, "explore", "--term", "hello").stdout)
        (self.impl / ".plans").mkdir(parents=True, exist_ok=True)
        (self.impl / PLAN).write_text("# 計画\n", encoding="utf-8")
        self.set_config(self.design, graphify="auto")
        r = self.run_pa(self.design, "explore", "--term", "hello")   # 設計書の側から自分（impl）を引く
        self.assertIn("graphify: updated", r.stdout)
        (self.impl / PLAN).write_text("# 計画を書き直した\n", encoding="utf-8")
        self.assertIn("graphify: fresh", self.run_pa(self.design, "explore", "--term", "hello").stdout)
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        self.assertIn("graphify: updated", self.run_pa(self.design, "explore", "--term", "hello").stdout)

    def test_verify_plan_reports_everything_at_once_and_briefly(self) -> None:
        # 形の指摘があっても測り、1 回で出し切る。出す長さは実行ハーネスがやり直しに渡す 2000 文字に収める。
        self.add_caller()
        # フォルダを分ける（同じフォルダの言及だけの文書は 1 行にまとまる）
        commit(self.design, {f"docs/n{i}/n{i}.md": f"# n{i}\n\n`hello` の話 {i}\n" for i in range(30)}, "many")
        self.write_plan(PLAN_ALIGNED.replace("## ずれ\n\nなし", "## ずれ\n\n根拠の無いずれ"))
        r = self.run_pa(self.impl, "verify-plan")
        self.assertEqual(r.returncode, 1)
        first = r.stderr.splitlines()[0]
        self.assertRegex(first, r"^NG 計画の検査: \d+ 件（全文: \.codd/problems\.json）$")
        self.assertIn("ずれ は箇条書きにしてください", r.stderr)   # 形の指摘と
        self.assertIn("「未判断」として書き足しました", r.stderr)   # 測った結果を同じ回に
        self.assertLessEqual(len(r.stderr), 2000)
        texts = [p["text"] for p in json.loads((self.impl / ".codd/problems.json").read_text(encoding="utf-8"))["problems"]]
        named = lambda text: len(re.findall(r"docs/n\d+/n\d+\.md", text))
        self.assertGreater(sum(named(t) for t in texts), named(r.stderr))   # 全文は控えに残る
        plan = (self.impl / PLAN).read_text(encoding="utf-8")
        self.assertIn("- src/use.py — 未判断", plan)
        self.assertIn("- 未判断: design:docs/n0/n0.md:3", plan)

    # ------------------------------------------------------------ 点検（本流とは別）

    def test_lint_finds_what_the_main_flow_cannot_see(self) -> None:
        r = self.run_pa(self.impl, "lint", "--no-test")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)   # 置いたあとに何も変わっていない
        self.assertTrue(r.stdout.startswith("OK 点検"))

        commit(self.impl, {"src/app.py": "def hello():\n    return 3\n",
                           "src/__pycache__/app.cpython-313.pyc": "x"}, "codd を通さずに変えた")
        commit(self.design, {
            "docs/guide.md": "# 使い方\n\n[呼ぶもの](../src/old.py)\n\n書き方: `[説明](../src/none.py)`\n\n"
                             "```\n<!-- evidence: x -->1<!-- /evidence -->\n```\n",
            "docs/specs/a.md": "# A\n\n## 目的\n\n## 振る舞い\n",
            "docs/specs/b.md": "# B\n\n## 目的\n\n## 振る舞い\n",
            "docs/specs/c.md": "# C\n\n## 目的\n",
        }, "設計書を足した")
        before = git(self.impl, "status", "--porcelain") + git(self.design, "status", "--porcelain")
        r = self.run_pa(self.impl, "lint", "--no-test")
        self.assertEqual(r.returncode, 1, r.stderr)
        text = (self.impl / ".codd/lint.md").read_text(encoding="utf-8")
        self.assertIn("docs/guide.md:3 → ../src/old.py", text)                # 壊れたパス
        self.assertNotIn("none.py", text)                                     # `…` の中のリンクは書き方の例
        self.assertIn("## テストの結果を写した文書\n\n- なし", text)            # コードブロックの中の印も例
        self.assertIn("docs/specs/c.md — 欠けている見出し: 振る舞い", text)  # 書式
        self.assertNotIn("docs/specs/a.md —", text.split("## 文書の書式")[1].split("##")[0])
        self.assertIn("src/app.py", text.split("## codd を通らなかった変更")[1])   # 記録の無い変更
        self.assertNotIn("__pycache__", text)                                   # コミットしたキャッシュは数えない
        self.assertIn("## 本流に渡すやりたいこと", text)
        self.assertIn("codd を通らずに入った変更", r.stdout)
        self.assertEqual(r.stdout.count("codd を通らずに入った変更"), 2)   # コミットごとに 1 行
        # 点検は何も直さない（書くのは作業フォルダだけ）
        self.assertEqual(before, git(self.impl, "status", "--porcelain") + git(self.design, "status", "--porcelain"))

        # 結果の無い計画（進めている・捨てた計画）に出てきても、codd を通った変更とは数えない。
        commit(self.impl, {".plans/2026-10-01-0000-x.md": "## 自分の変更案\n\n- src/app.py — 戻り値\n"}, "途中の計画")
        text = (self.run_pa(self.impl, "lint", "--no-test"), (self.impl / ".codd/lint.md").read_text(encoding="utf-8"))[1]
        self.assertIn("src/app.py", text.split("## codd を通らなかった変更")[1])
        commit(self.impl, {".plans/2026-10-01-0000-x.md": "## 自分の変更案\n\n- src/app.py — 戻り値\n\n## 結果\n"}, "記録")
        text = (self.run_pa(self.impl, "lint", "--no-test"), (self.impl / ".codd/lint.md").read_text(encoding="utf-8"))[1]
        self.assertNotIn("src/app.py", text.split("## codd を通らなかった変更")[1])  # 記録に出てくれば通った変更

    def test_lint_finds_changes_made_after_codd_passed(self) -> None:
        # 記録には検査を通った中身（git の blob）が残り、点検はコミットされた中身と突き合わせる。
        self.write_plan(PLAN_DRIFT)
        self.assert_plan_ok()
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        (self.design / "docs/api.md").write_text("# API\n\n## hello\n\nhello は 2 を返す。\n\n実装は `src/app.py`（`src/` の下）。\n", encoding="utf-8")
        self.assertEqual(self.run_pa(self.impl, "verify-apply").returncode, 0)
        self.assertEqual(self.run_pa(self.impl, "record").returncode, 0)
        record = (self.impl / PLAN).read_text(encoding="utf-8")
        passed = git(self.impl, "hash-object", "src/app.py").strip()[:12]
        rows = record.split("### 検査を通った中身\n\n")[1]
        self.assertIn(f"- `src/app.py` — {passed}", rows)
        self.assertRegex(rows, r"- `design:docs/api.md` — [0-9a-f]{12}")   # 参照先で変えたものも残す

        # 検査を通した中身のままコミットすれば、どちらにも挙がらない。
        git(self.impl, "add", "-A")
        git(self.impl, "commit", "-q", "-m", "codd で変えた")
        git(self.design, "add", "-A")
        git(self.design, "commit", "-q", "-m", "codd で変えた")
        r = self.run_pa(self.impl, "lint", "--no-test")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = (self.impl / ".codd/lint.md").read_text(encoding="utf-8")
        self.assertIn("## codd を通ったあとに変わった変更\n\n- なし", text)

        # 通ったあとに手で変えたものは、記録があっても「通ったあとに変わった変更」に挙がる。
        commit(self.impl, {"src/app.py": "def hello():\n    return 3\n"}, "手で直した")
        r = self.run_pa(self.impl, "lint", "--no-test")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        text = (self.impl / ".codd/lint.md").read_text(encoding="utf-8")
        self.assertIn("src/app.py", text.split("## codd を通ったあとに変わった変更")[1].split("##")[0])
        self.assertNotIn("src/app.py", text.split("## codd を通らなかった変更")[1].split("##")[0])
        self.assertNotIn("docs/api.md", text.split("## codd を通ったあとに変わった変更")[1].split("##")[0])
        self.assertIn("codd を通ったあとに変わった変更（", r.stdout)

        # 消したファイルも、記録で消したことになっていなければ挙がる。中身を検査を通した版に戻せば、挙がらない。
        commit(self.impl, {"src/app.py": "def hello():\n    return 2\n"}, "戻した")
        self.assertEqual(self.run_pa(self.impl, "lint", "--no-test").returncode, 0)
        (self.impl / "src/app.py").unlink()
        git(self.impl, "add", "-A")
        git(self.impl, "commit", "-q", "-m", "消した")
        self.run_pa(self.impl, "lint", "--no-test")
        text = (self.impl / ".codd/lint.md").read_text(encoding="utf-8")
        self.assertIn("src/app.py（いまは無い）", text.split("## codd を通ったあとに変わった変更")[1])

    def test_lint_runs_the_tests_of_both_sides(self) -> None:
        config = self.impl / ".statemachine/codd/codd.json"
        data = json.loads(config.read_text(encoding="utf-8"))
        data["test"] = [sys.executable, "-c", "import sys; print('boom'); sys.exit(3)"]
        config.write_text(json.dumps(data), encoding="utf-8")
        r = self.run_pa(self.impl, "lint")
        self.assertEqual(r.returncode, 1)
        self.assertIn("自分のテスト", r.stdout)
        self.assertIn("boom", (self.impl / ".codd/lint.md").read_text(encoding="utf-8"))

    # ------------------------------------------------------------ レビュー（頼まれて確かめる。本流とは別）

    def review_answer(self, line: str) -> None:
        with (self.impl / ".codd/review/answers.md").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def review_items(self) -> dict[str, dict]:
        state = json.loads((self.impl / ".codd/review/state.json").read_text(encoding="utf-8"))
        return {i["id"]: i for i in state["items"]}

    def test_review_decides_items_and_checks_every_answer(self) -> None:
        commit(self.impl, {"src/app.py": "def hello():\n    return 2\n\n\ndef bye():\n    return 0\n"}, "bye")
        (self.impl / ".codd").mkdir(exist_ok=True)
        (self.impl / ".codd/runs-since-review.json").write_text('{"runs": 3}', encoding="utf-8")
        r = self.run_pa(self.impl, "review", "start", "--path", "src/app.py", "--no-test")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("OK レビュー: ファイル 2 件"), r.stdout)   # コードと、つながる文書
        self.assertFalse((self.impl / ".codd/runs-since-review.json").exists())   # 勧めるまでの回数を数え直す
        self.run_pa(self.impl, "guide", "--all")
        items = self.review_items()
        kinds = {(i["kind"], i["name"]) for i in items.values()}
        # 文書も述べている名前は突き合わせ、文書・テストに出てこない名前は漏れ、コードのファイルには設計を問う
        self.assertLessEqual({("矛盾", "hello"), ("漏れ", "bye"), ("漏れ", "hello#test"), ("漏れ", "bye#test"),
                              ("設計の不備", "src/app.py")}, kinds)
        self.assertEqual(next(iter(items.values()))["kind"], "設計の不備")   # 1 ファイルの上限で落とさないよう先に置く
        by_name = {i["name"]: rid for rid, i in items.items()}
        # 示していない項目には答えさせない（弱いモデルが読まずにまとめて答えるのを止める）
        self.review_answer(f"- {by_name['hello']} 問題なし: src/app.py:1 — 読んだ")
        r = self.run_pa(self.impl, "review", "verify", "--part", "items")
        self.assertEqual(r.returncode, 1)
        self.assertIn("`codd.py review next` で見ずに答えています", r.stdout)
        self.assertIn("答えていない項目", r.stdout)
        (self.impl / ".codd/review/answers.md").write_text("# 項目への答え\n\n", encoding="utf-8")
        first = self.run_pa(self.impl, "review", "next").stdout
        self.assertTrue(first.startswith("ITEM R01"))
        self.assertEqual(self.run_pa(self.impl, "review", "next").stdout, first)   # 答えるまで同じ項目を示す
        shown = self.run_pa(self.impl, "review", "next", "--id", by_name["hello"]).stdout
        self.assertIn("hello は 1 を返す。", shown)          # 相手の側の箇所を前後ごと示す
        # 根拠は項目のファイルの実在する行。問題には「やりたいこと」が要る
        self.review_answer(f"- {by_name['hello']} 問題: docs/api.md:99 — 実装は 2、設計書は 1")
        r = self.run_pa(self.impl, "review", "verify", "--part", "items")
        self.assertIn("行までのファイルです", r.stdout)
        self.assertIn("やりたいこと", r.stdout)
        (self.impl / ".codd/review/answers.md").write_text("# 項目への答え\n\n", encoding="utf-8")
        self.review_answer(f"- {by_name['hello']} 問題: docs/api.md:5 — 実装は 2、設計書は 1\n"
                           "  - やりたいこと: hello の戻り値を、実装と設計書で合わせたい")
        self.assertIn("R99 という項目はありません", self.run_pa(self.impl, "review", "next", "--id", "R99").stderr)
        self.run_pa(self.impl, "review", "next", "--id", by_name["bye"])   # 示し直してから答えてもよい
        self.review_answer(f"- {by_name['bye']} 問題なし: README.md:1 — 内部の名前")
        r = self.run_pa(self.impl, "review", "verify", "--part", "items")
        self.assertIn("ファイルがありません", r.stdout)
        text = (self.impl / ".codd/review/answers.md").read_text(encoding="utf-8")
        (self.impl / ".codd/review/answers.md").write_text(text.replace("README.md:1", "src/app.py:5"), encoding="utf-8")
        while not (shown := self.run_pa(self.impl, "review", "next").stdout).startswith("DONE"):
            rid = shown.split()[1].split("（")[0]
            self.review_answer(f"- {rid} 判断できない: src/app.py:1 — 決めた文書が無い")
        self.assertEqual(self.run_pa(self.impl, "review", "verify", "--part", "items").returncode, 0)
        # 自由レビューも要る（無ければ「なし: 理由」）
        r = self.run_pa(self.impl, "review", "verify")
        self.assertIn("## 自由レビュー", r.stdout)
        self.review_answer("\n## 自由レビュー\n\n- 設計の不備: src/app.py:5 — bye は何も確かめない\n"
                           "  - やりたいこと: bye の役目を決めたい")
        self.assertEqual(self.run_pa(self.impl, "review", "verify").returncode, 0)
        r = self.run_pa(self.impl, "review", "report")
        self.assertTrue(r.stdout.startswith("FOUND レビュー: 漏れ 0、矛盾 1、設計の不備 1、その他 0、判断できない 3"))
        report = (self.impl / ".codd/review.md").read_text(encoding="utf-8")
        self.assertIn("- hello の戻り値を、実装と設計書で合わせたい", report.split("## 本流に渡すやりたいこと")[1])
        self.assertIn("- bye の役目を決めたい", report)
        # codd を通らなかった変更は lint が挙げるが、このレビューが確かめたので「確かめたい」を本流に渡さない
        self.assertIn("codd を通らなかった変更 1", report)
        self.assertNotIn("codd を通らずに入った変更", report.split("## 本流に渡すやりたいこと")[1])
        # 何も直さない
        self.assertEqual(git(self.impl, "status", "--porcelain").strip(), "")

    def test_review_reads_the_rules_for_design_items(self) -> None:
        commit(self.impl, {"docs/rules.md": "# 決まり\n\n- 失敗は例外で伝える\n"}, "rules")
        self.set_config(self.impl, guides=[{"use": "docs/rules.md"}])
        self.run_pa(self.impl, "guide", "--all")   # レビューの前に読んだものは数えない
        self.run_pa(self.impl, "review", "start", "--path", "src/app.py", "--no-test")
        for _ in self.review_items():
            rid = self.run_pa(self.impl, "review", "next").stdout.split()[1].split("（")[0]
            self.review_answer(f"- {rid} 問題なし: src/app.py:1 — 読んだ")
        r = self.run_pa(self.impl, "review", "verify", "--part", "items")
        # 読み書きの手順は、どのリポジトリでも読ませる
        self.assertIn("守る決まりと codd の読み書きの手順を読んでいません: .statemachine/codd/rules/reading.md, docs/rules.md", r.stdout)
        self.run_pa(self.impl, "guide", "--all")
        self.assertEqual(self.run_pa(self.impl, "review", "verify", "--part", "items").returncode, 0)
        # レビューで読んだものを、あとで始める本流の計画の検査で数えない
        self.run_pa(self.impl, "review", "report")
        self.assertFalse((self.impl / ".codd/rules-read.json").exists())

    def test_review_looks_at_constants_and_skips_empty_files(self) -> None:
        commit(self.impl, {"src/app.py": "TIMEOUT_SEC = 10\n\n\ndef hello():\n    return 2\n", "src/__init__.py": ""}, "cfg")
        commit(self.design, {"docs/api.md": "## hello\n\n`hello` は 1 を返す。待ちは `TIMEOUT_SEC`（30 秒）。\n\n"
                                            "`restock` もある。\n\n実装は `src/app.py`。\n"}, "doc")
        self.run_pa(self.impl, "review", "start", "--path", "src", "--no-test")
        state = json.loads((self.impl / ".codd/review/state.json").read_text(encoding="utf-8"))
        self.assertNotIn("src/__init__.py", state["targets"])            # 空のファイルには見るものが無い
        self.assertTrue(any(t.endswith("docs/api.md") for t in state["targets"]))   # コードを挙げても、つながる文書を見る
        kinds = {(i["kind"], i["name"]) for i in state["items"]}
        self.assertIn(("矛盾", "TIMEOUT_SEC"), kinds)                     # 設定の値の食い違い
        self.assertIn(("漏れ", "restock"), kinds)                         # 文書にだけある名前

    def test_review_test_gaps_cite_tests_and_same_roots_are_not_repeated(self) -> None:
        commit(self.impl, {"src/app.py": "def hello():\n    return 2\n\n\ndef bye():\n    return 0\n",
                           "tests/test_app.py": "from src.app import bye\n\n\ndef test_bye():\n    assert bye() == 0\n"}, "t")
        self.run_pa(self.impl, "review", "start", "--path", "src/app.py", "--no-test")
        self.run_pa(self.impl, "guide", "--all")
        by_name = {i["name"]: rid for rid, i in self.review_items().items()}
        for name, rid in by_name.items():
            self.run_pa(self.impl, "review", "next", "--id", rid)
            if name == "hello#test":   # テストが無いことの根拠に、テストのファイルを挙げてよい
                self.review_answer(f"- {rid} 問題: tests/test_app.py:1 — bye しか呼ばない\n  - やりたいこと: hello のテストを足したい")
            elif name == "hello":
                self.review_answer(f"- {rid} 問題: src/app.py:2 — 実装は 2\n  - やりたいこと: hello の戻り値を合わせたい")
            elif name == "bye":
                self.review_answer(f"- {rid} 問題: src/app.py:5 — 文書に無い\n  - やりたいこと: {by_name['hello']} と同じ")
            else:
                self.review_answer(f"- {rid} 問題なし: src/app.py:1 — 読んだ")
        r = self.run_pa(self.impl, "review", "verify", "--part", "items")
        self.assertEqual(r.returncode, 0, r.stdout)
        out = self.run_pa(self.impl, "review", "report").stdout
        self.assertTrue(out.startswith("FOUND レビュー"), out)   # 見つけたことを、検査の失敗と取り違えない
        asks = out.split("## 本流に渡すやりたいこと")[1]
        self.assertIn("hello のテストを足したい", asks)
        self.assertNotIn("と同じ", asks)
        self.assertIn(f"と同じ（hello の戻り値を合わせたい）", out)   # 指した項目のやりたいことを添えて読めるようにする

    def test_review_skips_names_nobody_outside_sees(self) -> None:
        commit(self.impl, {"src/app.py": "LIMIT = 3\n\n\ndef hello(person):\n    return person\n",
                           "src/lib.js": "const cache = new Map();\n\nexport function lookup(id) {\n  return cache.get(id);\n}\n"},
               "names")
        commit(self.design, {"docs/api.md": "`hello(person)` は `person` を返す。上限は `LIMIT`。`lookup` もある。\n\n"
                                            "実装は `src/app.py` と `src/lib.js`。\n"}, "doc")
        self.run_pa(self.impl, "review", "start", "--path", "src", "--no-test")
        names = {i["name"] for i in self.review_items().values()}
        self.assertNotIn("person", names)      # 引数の名前は、定義した hello の項目が見る
        self.assertNotIn("cache", names)       # export するモジュールの、export しない変数
        self.assertIn("LIMIT", names)
        self.assertNotIn("LIMIT#test", names)  # 定数は使う側のテストで確かめる
        self.assertIn("lookup", names)

    def test_review_rest_looks_at_what_the_cap_left_and_keeps_asks(self) -> None:
        files = {f"src/m{i:02d}.py": f"def f{i:02d}():\n    return {i}\n" for i in range(25)}
        commit(self.impl, files, "many")
        self.run_pa(self.impl, "review", "start", "--path", "src", "--no-test")
        state = json.loads((self.impl / ".codd/review/state.json").read_text(encoding="utf-8"))
        self.assertTrue(state["skipped_files"])
        items = self.review_items()
        for rid in items:
            self.run_pa(self.impl, "review", "next", "--id", rid)
            loc = items[rid]["locs"][0]
            line = f"- {rid} 問題: {loc[1]}:{loc[2]} — 見た" + "\n  - やりたいこと: 先の回で見つけた" if rid == "R01" else \
                f"- {rid} 問題なし: {loc[1]}:{loc[2]} — 見た"
            self.review_answer(line)
        out = self.run_pa(self.impl, "review", "report").stdout
        self.assertIn("続きは `codd.py review start --rest`", out)
        skipped = state["skipped_files"]
        self.run_pa(self.impl, "review", "start", "--path", ".", "--no-test")   # 全体はどの側も見る
        whole = json.loads((self.impl / ".codd/review/state.json").read_text(encoding="utf-8"))
        shown = [loc[1] for it in whole["items"] for loc in it["locs"]]   # 項目の箇所に出たものは見たものに数える
        self.assertTrue(any("docs/api.md" in t for t in whole["targets"] + whole["skipped_files"] + shown))
        (self.impl / ".codd/review/state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        (self.impl / ".codd/review/answers.md").write_text("# 項目への答え\n\n" + "".join(
            f"- {rid} 問題: {items[rid]['locs'][0][1]}:{items[rid]['locs'][0][2]} — 見た\n  - やりたいこと: 先の回で見つけた\n"
            if rid == "R01" else f"- {rid} 問題なし: {items[rid]['locs'][0][1]}:{items[rid]['locs'][0][2]} — 見た\n"
            for rid in items), encoding="utf-8")
        r = self.run_pa(self.impl, "review", "start", "--rest", "--no-test")
        self.assertTrue(r.stdout.startswith("OK"), r.stdout + r.stderr)
        rest = json.loads((self.impl / ".codd/review/state.json").read_text(encoding="utf-8"))
        self.assertTrue(set(skipped) <= set(rest["targets"]) | set(rest["skipped_files"]))   # 見なかったものだけを見る
        self.assertEqual(rest["carried_asks"], ["先の回で見つけた"])                          # 前の回の見つけたものを失わない
        self.assertIn("先の回で見つけた", self.run_pa(self.impl, "review", "report").stdout.split("## 本流に渡すやりたいこと")[1])

    def test_review_says_none_when_nothing_to_look_at(self) -> None:
        commit(self.impl, {"setup.cfg": "[metadata]\n"}, "cfg")   # 名前も文書も持たないファイルには項目が出ない
        r = self.run_pa(self.impl, "review", "start", "--path", "setup.cfg", "--no-test")
        self.assertTrue(r.stdout.startswith("NONE"), r.stdout + r.stderr)
        self.assertNotIn("答えの検査が通っていません", self.run_pa(self.impl, "review", "report").stdout)
        r = self.run_pa(self.impl, "review", "start", "--path", "nowhere", "--no-test")
        self.assertIn("に当たるファイルがありません", r.stderr)

    def test_review_machine_has_one_way_to_run(self) -> None:
        # チェックと手入れで回し方を分けない（毎回グラフを用意し、次の計画まで立てる）
        text = (TOOL / "review/workflow.yaml").read_text(encoding="utf-8")
        self.assertNotIn("mode", text)
        self.assertIn("{from: free, to: ahead}", text)
        self.assertIn("args: [.statemachine/codd/codd.py, next, --verify]", text)
        self.assertIn("codd.py graph", (TOOL / "review/actions/start.md").read_text(encoding="utf-8"))

    def test_review_goes_further_back_when_nothing_changed_recently(self) -> None:
        r = self.run_pa(self.impl, "review", "start", "--no-test")
        self.assertIn("範囲: 直近 7 日に変わったもの", r.stdout, r.stderr)
        old = (dt.datetime.now() - dt.timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%S")
        for repo in (self.impl, self.design):   # どちらの側も 60 日前のコミットだけにする
            subprocess.run(["git", "filter-branch", "-f", "--env-filter",
                            f"export GIT_AUTHOR_DATE={old} GIT_COMMITTER_DATE={old}"],
                           cwd=repo, check=True, capture_output=True, env={**os.environ, **GIT_ENV,
                                                                            "FILTER_BRANCH_SQUELCH_WARNING": "1"})
        r = self.run_pa(self.impl, "review", "start", "--no-test")
        self.assertTrue(r.stdout.startswith("OK"), r.stdout + r.stderr)
        self.assertIn("範囲: 直近 90 日に変わったもの", r.stdout)
        # 期間を挙げたら遡らない
        r = self.run_pa(self.impl, "review", "start", "--since", "7 days ago", "--no-test")
        self.assertTrue(r.stdout.startswith("NONE"), r.stdout + r.stderr)

    def test_review_folder_is_part_of_the_machine(self) -> None:
        # 置いた codd-review のフォルダは、変えたファイルにも探す対象にも数えない
        self.run_pa(self.impl, "impact", "--term", "自由レビュー")   # 確かめる手順にだけ出てくる語
        self.assertNotIn(".statemachine/codd-review/", (self.impl / ".codd/impact.md").read_text(encoding="utf-8"))

    def test_plans_made_ahead_are_listed_and_taken_over(self) -> None:
        self.assertTrue(self.run_pa(self.impl, "next").stdout.startswith("NONE"))
        r = self.run_pa(self.impl, "draft", "--ahead", "--name", "hello-returns-two")
        self.assertEqual(r.returncode, 0, r.stderr)
        ahead = next((self.impl / ".plans/next").glob("*-hello-returns-two.md"))
        # 立てておいた計画は、進めている計画でも記録でもない（本流の回を始めず、印も取らない）
        self.assertFalse(list((self.impl / ".plans").glob("*.md")))
        self.assertFalse((self.impl / ".codd/run-start.json").exists())
        r = self.run_pa(self.impl, "next", "--verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("## やりたいこと を書いていません", r.stdout)
        text = ahead.read_text(encoding="utf-8")
        ahead.write_text(text.replace("<!-- 1〜3 行。1 回で終わる大きさに絞る（残りは「今回やらないこと」へ） -->",
                                      "hello の戻り値を 2 にしたい"), encoding="utf-8")
        self.run_pa(self.impl, "draft", "--ahead", "--name", "same-again")
        twin = next((self.impl / ".plans/next").glob("*-same-again.md"))
        twin.write_text(ahead.read_text(encoding="utf-8"), encoding="utf-8")
        self.assertIn("同じやりたいことです", self.run_pa(self.impl, "next", "--verify").stdout)
        twin.unlink()
        self.assertEqual(self.run_pa(self.impl, "next", "--verify").returncode, 0)
        self.assertIn(f"- {ahead.name} — hello の戻り値を 2 にしたい", self.run_pa(self.impl, "next").stdout)
        commit(self.impl, {"src/app.py": "def hello():\n    return 3\n"}, "changed after")   # 立てたあとに変わった
        self.assertIn("立てたあとに 1 ファイル変わった", self.run_pa(self.impl, "next").stdout)
        # 本流が引き継ぐと、進めている計画になり、next/ から消える
        r = self.run_pa(self.impl, "draft", "--next", ahead.name)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("立てておいた計画から始めます", r.stdout)
        self.assertIn("立てたあとに変わったファイル", r.stdout)   # 読み直す箇所を示す
        self.assertIn("  - src/app.py", r.stdout)
        plans = list((self.impl / ".plans").glob("*-hello-returns-two.md"))
        self.assertEqual(len(plans), 1)
        self.assertIn("hello の戻り値を 2 にしたい", plans[0].read_text(encoding="utf-8"))
        self.assertFalse(ahead.exists())
        self.assertIn("計画はもうあります", self.run_pa(self.impl, "draft", "--name", "other").stdout)
        self.assertIn("ありません", self.run_pa(self.impl, "draft", "--next", "nothing", "--new").stderr)

    def test_plans_made_ahead_are_capped(self) -> None:
        for i in range(5):
            self.assertEqual(self.run_pa(self.impl, "draft", "--ahead", "--name", f"p{i}").returncode, 0)
        r = self.run_pa(self.impl, "draft", "--ahead", "--name", "p5")
        self.assertEqual(r.returncode, 1)
        self.assertIn("上限（5）", r.stderr)

    def test_review_machine_passes_engine_validation(self) -> None:
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML がありません")
        runner = REPO / ".github/skills/statemachine-use/scripts/run_machine.py"
        r = subprocess.run([sys.executable, str(runner), str(TOOL / "review/workflow.yaml"), "--dry-run"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_workflow_gives_checks_enough_time(self) -> None:
        # 実行ハーネスの既定（120 秒）では、グラフの作り直しやテストで切られて「落ちた」扱いになる。
        text = (TOOL / "machine/workflow.yaml").read_text(encoding="utf-8")
        found = dict(re.findall(r"args: \[\.statemachine/codd/codd\.py, (verify-\w+)\], timeout_sec: (\d+)", text))
        self.assertGreaterEqual(int(found["verify-plan"]), 900)
        self.assertGreaterEqual(int(found["verify-apply"]), 1800)

    def test_workflow_lets_the_user_stop_wherever_it_asks(self) -> None:
        # 利用者に訊く段（計画の確認・止まったときの相談）では、どちらでもやめられる。
        text = (TOOL / "machine/workflow.yaml").read_text(encoding="utf-8")
        self.assertIn('{from: confirm, to: stopped, condition_rule: "startswith:answer:STOP"', text)
        self.assertIn('{from: stuck, to: stopped, condition_rule: "startswith:choice:STOP"', text)

    def test_retro_runs_the_prompt_after_issue_notes(self) -> None:
        # 終わりの振り返りは課題メモの指示だけ。設定のプロンプトがあれば、材料のありかと一緒にそのあとに出す。
        r = self.run_pa(self.impl, "retro")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("# 課題メモ", r.stdout)
        self.assertIn("## この回の出来事\n\n- なし", r.stdout)
        self.assertIn("利用者が指摘したこと", r.stdout)   # 控えた出来事が無くても、会話の中の指摘は問題として拾う
        self.assertNotIn("# 振り返り", r.stdout)
        cfg = self.impl / ".statemachine/codd/codd.json"
        config = json.loads(cfg.read_text(encoding="utf-8"))
        cfg.write_text(json.dumps({**config, "retro": "KPT で振り返る"}), encoding="utf-8")
        self.write_plan(PLAN_ALIGNED)
        self.assert_plan_ok()
        self.assertEqual(self.run_pa(self.impl, "record").returncode, 0)
        r = self.run_pa(self.impl, "retro")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("RETRO\n"), r.stdout)
        self.assertIn("利用者に訊かずに", r.stdout)
        self.assertIn(f"計画の記録（確認の答えと結果）: {PLAN}", r.stdout)
        self.assertTrue(r.stdout.rstrip().endswith("## プロンプト\n\nKPT で振り返る"), r.stdout)
        # 長いプロンプトは .md に書いてパスを指せる
        commit(self.impl, {".agents/codd-retro.md": "# 振り返り\n\nwiki に残す\n"}, "retro")
        cfg.write_text(json.dumps({**config, "retro": ".agents/codd-retro.md"}), encoding="utf-8")
        self.assertIn("## プロンプト\n\n# 振り返り\n\nwiki に残す", self.run_pa(self.impl, "retro").stdout)
        cfg.write_text(json.dumps({**config, "retro": ".agents/none.md"}), encoding="utf-8")
        self.assertIn("retro に書いたファイルがありません", self.run_pa(self.impl, "retro").stderr)
        cfg.write_text(json.dumps({**config, "retro": ["x"]}), encoding="utf-8")
        self.assertIn("retro は", self.run_pa(self.impl, "retro").stderr)

    def test_retro_lists_what_went_wrong_for_issue_notes(self) -> None:
        # 止まった・退けたことを控え、課題メモ（.plans/retro/）の材料として今あるメモと一緒に渡す。次の回で数え直す。
        self.assertNotEqual(self.run_pa(self.impl, "verify-plan").returncode, 0)   # 計画が無いので止まる
        self.write_plan(PLAN_ALIGNED)
        self.assertEqual(self.run_pa(self.impl, "decide", "NG", "--note", "名前が違う").returncode, 0)
        self.assert_plan_ok()
        self.assertEqual(self.run_pa(self.impl, "record").returncode, 0)
        r = self.run_pa(self.impl, "retro")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("RETRO\n"), r.stdout)
        self.assertIn("# 課題メモ", r.stdout)
        self.assertIn("計画の検査で止まった（1 件）", r.stdout)
        self.assertIn("計画を直すと答えた（NG）: 名前が違う", r.stdout)
        self.assertIn(f"- 計画の記録: {PLAN}", r.stdout)
        self.assertIn("## 今ある課題メモ\n\n- なし", r.stdout)
        self.assertIn("## 同じファイルを変えた過去の記録\n\n- なし", r.stdout)
        self.assertIn("codd の作りの問題", r.stdout)
        self.assertNotIn("# 振り返り", r.stdout)   # 設定のプロンプトが無ければ課題メモだけ
        # 同じファイルを変えた過去の記録を、食い違いを見る材料に渡す（関係の無い記録は渡さない）
        (self.impl / ".plans/2026-09-01-0000-old.md").write_text(
            "# 変更の計画\n\n## 自分の変更案\n\n- src/app.py — ログを出さない\n\n## 結果\n\n- 変えた\n", encoding="utf-8")
        (self.impl / ".plans/2026-09-02-0000-other.md").write_text(
            "# 変更の計画\n\n## 自分の変更案\n\n- src/other.py — x\n\n## 結果\n\n- 変えた\n", encoding="utf-8")
        for old in ("2026-09-01-0000-old", "2026-09-02-0000-other"):   # 前の回に書いた記録（この回の記録は最後に書いたもの）
            os.utime(self.impl / f".plans/{old}.md", (1, 1))
        r = self.run_pa(self.impl, "retro")
        self.assertIn("## 同じファイルを変えた過去の記録\n\n- .plans/2026-09-01-0000-old.md\n\n", r.stdout)
        self.assertNotIn("other.md", r.stdout)
        (self.impl / ".plans/retro").mkdir()
        (self.impl / ".plans/retro/stale-heading.md").write_text(
            "# 見出しの改名を拾えない\n\n- 原因: 見出しを語で探していない\n- 直す先: codd の手順\n- 起きた回: 2026-10-19 x\n",
            encoding="utf-8")
        r = self.run_pa(self.impl, "retro")
        self.assertIn("- .plans/retro/stale-heading.md — 見出しの改名を拾えない（原因: 見出しを語で探していない）（codd の手順）", r.stdout)
        self.assertIn("課題と原因は、それぞれ 1 行で簡潔に書く", r.stdout)
        # 参照先のリポジトリのメモも、同じ問題を重ねないように並べる
        (self.design / ".plans/retro").mkdir(parents=True)
        (self.design / ".plans/retro/x.md").write_text("# 参照先のメモ\n", encoding="utf-8")
        self.assertIn("- design:.plans/retro/x.md — 参照先のメモ", self.run_pa(self.impl, "retro").stdout)
        # 課題メモは計画に数えない。新しい回を始めると、前の回の出来事は消える
        r = self.run_pa(self.impl, "draft", "--name", "next-one")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ひな形を置きました: .plans/", r.stdout)
        # 未判断の書き足しは、計画の検査のいつもの流れなので出来事に数えない
        self.assertIn("## この回の出来事\n\n- なし", self.run_pa(self.impl, "retro").stdout)

    def test_workflow_ends_with_the_retro_after_reporting(self) -> None:
        # 報告（done・stopped）のあと、どちらからも終端の振り返りへ進む。振り返りは訊かずに進める。
        text = (TOOL / "machine/workflow.yaml").read_text(encoding="utf-8")
        self.assertIn("  - {from: done, to: retro}\n", text)
        self.assertIn("  - {from: stopped, to: retro}\n", text)
        retro = text.split("  retro:\n", 1)[1].split("\n\n", 1)[0]
        self.assertIn("terminal: true", retro)
        self.assertEqual(text.count("terminal: true"), 1)
        action = (TOOL / "machine/actions/retro.md").read_text(encoding="utf-8")
        self.assertIn("利用者が指摘したこと", action)
        self.assertIn("`.plans/retro/`", action)
        self.assertIn("利用者に訊かずに進めます", action)

    def test_workflow_passes_engine_validation(self) -> None:
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML がありません")
        runner = REPO / ".github/skills/statemachine-use/scripts/run_machine.py"
        r = subprocess.run([sys.executable, str(runner), str(TOOL / "machine/workflow.yaml"), "--dry-run"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


    # ------------------------------------------------------------ シナリオの再生で見つけたもの（tests/scenarios/）

    RENAME_PLAN = PLAN_ALIGNED.replace("hello にログを足す。", "hello を greet に改める。").replace(
        "- src/app.py — `hello` の中でログを出す", "- src/app.py — `hello` を `greet` に改める")

    def test_a_removed_name_left_in_an_unplanned_ref_is_asked(self) -> None:
        # 計画で変えない参照先に、消した名前が残っている。変える段だけでは直せない（直せば計画に無い変更）ので訊く。
        self.write_plan(self.with_tests(self.RENAME_PLAN, "- `greet` — 変更不要: 名前だけ変える"))
        self.assert_plan_ok(ok=False)
        self.run_pa(self.impl, "decide", "OK")
        (self.impl / "src/app.py").write_text("def greet():\n    return 1\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("計画で変えないファイルがまだ書いています", r.stderr)
        self.assertIn("docs/api.md", r.stderr)
        self.assertNotIn("AUTO", self.run_pa(self.impl, "advise").stdout)
        # 計画の根拠にしたファイルは、関係なしとして済ませない
        (self.impl / ".codd/apply.md").write_text("## 計画との違い\n\n- `hello` — 関係なし: 別物\n", encoding="utf-8")
        self.assertIn("計画で変えないファイルがまだ書いています", self.run_pa(self.impl, "verify-apply").stderr)
        # 今回やらないと決めたものは残っていてよい
        self.write_plan(self.with_tests(self.RENAME_PLAN, "- `greet` — 変更不要: 名前だけ変える").replace(
            "## 今回やらないこと\n\nなし", "## 今回やらないこと\n\n- docs/api.md の名前を直す — 設計書は別の回"))
        self.assert_plan_ok(ok=False)
        self.run_pa(self.impl, "decide", "OK")
        (self.impl / ".codd/apply.md").unlink()
        r = self.run_pa(self.impl, "verify-apply")
        self.assertNotIn("消した名前", r.stderr)

    def test_report_after_stop_says_the_user_stopped(self) -> None:
        self.write_plan(PLAN_DRIFT)
        self.assert_plan_ok(ok=False)
        self.run_pa(self.impl, "decide", "OK")
        (self.impl / "src/app.py").write_text("def hello():\n    return 2\n", encoding="utf-8")
        self.run_pa(self.impl, "decide", "STOP")
        self.assertEqual(self.run_pa(self.impl, "rollback").returncode, 0)
        r = self.report(self.impl)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("利用者がやめた（変えた分は戻した）", r.stdout)
        self.assertNotIn("やり直してください", r.stdout)

    def test_a_guide_document_loads_by_its_name(self) -> None:
        commit(self.design, {".agents/guides/api-doc.md": "---\nname: api-doc\ncodd:\n  files: [\"docs/*.md\"]\n---\n\n# API の文書\n"},
               "guide")
        r = self.run_pa(self.impl, "guide", "api-doc")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("API の文書", r.stdout)
        self.assertIn("design:.agents/guides/api-doc.md", self.run_pa(self.impl, "show").stdout)

    def test_a_guide_check_runs_with_unanswered_asks(self) -> None:
        # 手順の確かめることに答えていないときも、手順の check を一緒に動かす（答えたあとで落ち、やり直しが増えないように）
        commit(self.impl, {".agents/guides/mig.md": "---\ncodd:\n  files: [\"db/*.sql\"]\n  check: [\"false\"]\n---\n\n"
                                                    "# マイグレーション\n\n- [ ] 戻す SQL を書いた\n",
                           "db/001.sql": "create table t1 (id int);\n"}, "guide")
        plan = self.with_tests(PLAN_ALIGNED, "- `t2` — 変更不要: 表を作るだけ").replace(
            "- src/app.py — `hello` の中でログを出す", "- db/002.sql — `t2` を作る").replace(
            "## 守る決まり\n\nなし", "## 守る決まり\n\n- .agents/guides/mig.md — 戻す SQL を書く")
        self.run_pa(self.impl, "guide", ".agents/guides/mig.md")
        self.write_plan(plan)
        self.assert_plan_ok(ok=False)
        self.run_pa(self.impl, "decide", "OK")
        (self.impl / "db/002.sql").write_text("select 1;\n", encoding="utf-8")
        r = self.run_pa(self.impl, "verify-apply")
        self.assertEqual(r.returncode, 1)
        self.assertIn("確かめることに答えていません", r.stderr)
        self.assertIn("検査が失敗しました", r.stderr)

    def test_a_guide_document_may_be_named_in_the_plan(self) -> None:
        # 「従う手順」には、guide で読み込むときと同じ名前（`mig`）で挙げてよい
        commit(self.impl, {".agents/guides/mig.md": "---\ncodd:\n  files: [\"db/*.sql\"]\n---\n\n# マイグレーション\n",
                           "db/001.sql": "create table t1 (id int);\n"}, "guide")
        plan = self.with_tests(PLAN_ALIGNED, "- `t2` — 変更不要: 表を作るだけ").replace(
            "- src/app.py — `hello` の中でログを出す", "- db/002.sql — `t2` を作る").replace(
            "## 守る決まり\n\nなし", "## 守る決まり\n\n- mig — 番号を連番にする")
        self.run_pa(self.impl, "guide", "mig")
        self.write_plan(plan)
        r = self.run_pa(self.impl, "verify-plan")
        self.assertNotIn("決められた手順を、「従う手順」に挙げていません", r.stderr)


if __name__ == "__main__":
    unittest.main()
