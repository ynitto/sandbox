#!/usr/bin/env python3
"""テストの共有前置き（実装計画 W3-2 の分割で `test_agent_flow.py` から切り出したもの）。

**ここ自体はテストを持たない。** 各 `test_<機能>.py` が先頭で `from _shared import *` して
取り込む共通部分——環境隔離・モジュールのロード・全シャードで使うヘルパを置く。

kiro-cli 不要（stub のみ）。標準ライブラリの unittest で完結する。

    python3 -m unittest discover -s tools/agent-flow/tests      # 全部
    python3 -m unittest tests.test_planner                      # 1 機能だけ
"""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import shutil
import subprocess
import types
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
# 黒箱 CLI e2e が実プロセス起動する薄いエントリポイント（agent_flow/ を起動する shim）。
SCRIPT = HERE.parent / "agent-flow.py"

# stub の擬似実行スリープを無効化してテストを高速化（子プロセスにも継承される）
os.environ["AGENT_FLOW_STUB_SLEEP_MAX"] = "0"

# テストの git コミットを環境のコミット署名設定（commit.gpgsign）から切り離す。
# 署名が有効な環境では署名が間欠的に失敗して `git commit` がコミットを作らず、git バス系の
# テストが偶発的に落ちる。GIT_CONFIG_* で commit.gpgsign=false を上乗せして決定的にする。
os.environ["GIT_CONFIG_COUNT"] = "1"
os.environ["GIT_CONFIG_KEY_0"] = "commit.gpgsign"
os.environ["GIT_CONFIG_VALUE_0"] = "false"

# 自動アップデートは既定 on のため、テスト中にコントリビューターの実 skill-registry.json から
# 更新元が解決されて実ネットワーク/再起動が走るのを防ぐ。存在しないパスを権威指定して registry
# 解決を無効化する（SelfUpdateTests は必要なテストでだけ KIRO_SKILL_REGISTRY を一時上書きする）。
os.environ["KIRO_SKILL_REGISTRY"] = os.path.join(
    tempfile.gettempdir(), "kf-tests-no-such-registry", "skill-registry.json")

# 開発者の cwd の設定ファイル（./agent-flow.yaml / ./.agent/agent-flow.yaml）がテストへ漏れて
# 実環境のバス・state_git を拾うのを防ぐため、中立な一時 cwd で走らせる（agent-project の
# テストと同じ護り。テストは絶対パスだけを使うので cwd に依存しない）。
os.chdir(tempfile.mkdtemp(prefix="kf-tests-cwd-"))

# 開発者の実 agent-control（`~/.agents/control/control.json`）がテストへ漏れるのを防ぐ。
# control は agent_cli / model を**全レイヤより優先して**上書きする（`_control_override`）ため、
# 実ファイルを読むと「既定は kiro-cli のはず」といったテストが開発者の設定次第で落ちる
# （実際に `flow.agent_cli: codex` を設定した環境で AgentCliTests が一斉に落ちていた）。
# 個別に上書きするテストは各自 addCleanup で戻す（ここは既定の隔離先）。
os.environ.setdefault("AGENT_CONTROL_DIR",
                      os.path.join(tempfile.gettempdir(), "kf-tests-no-such-control"))

# 実体は agent_flow/ パッケージ（断片の共有名前空間合成）。単一ファイル時代と同じく
# kf.<name> へのモンキーパッチがそのまま効く。
_PKG = HERE.parent / "agent_flow"
_spec = importlib.util.spec_from_file_location(
    "agent_flow", _PKG / "__init__.py", submodule_search_locations=[str(_PKG)])
kf = importlib.util.module_from_spec(_spec)
sys.modules["agent_flow"] = kf
_spec.loader.exec_module(kf)


def _zero_loose_objects(clone) -> int:
    """電源断で生じる『サイズ 0 の loose object』を模擬する（.git/objects/xx/yy を空に切り詰める）。
    到達可能なオブジェクトが空になるので、以後の add/commit/checkout/pull/fsck が破損で失敗する。"""
    objdir = os.path.join(str(clone), ".git", "objects")
    zeroed = 0
    for sub in os.listdir(objdir):
        d = os.path.join(objdir, sub)
        if len(sub) == 2 and os.path.isdir(d):          # objects/pack・objects/info は対象外
            for name in os.listdir(d):
                p = os.path.join(d, name)
                os.chmod(p, 0o644)  # macOS: git が 0444 で作る loose object に書き込み権限を付与
                open(p, "wb").close()   # 0 バイトへ切り詰め
                zeroed += 1
    return zeroed


def _git_config_get(repo, key) -> str:
    return subprocess.run(["git", "-C", str(repo), "config", "--local", "--get", key],
                          capture_output=True, text=True).stdout.strip()


def _load_executor_plugin(name):
    """executors/<name>.py をテスト用にロードする。"""
    path = HERE.parent / "executors" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"kf_exec_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gl_plugin = _load_executor_plugin("gitlab")


class _Args:
    """doctor の決定的チェック/シグナル/cmd が読む args の最小スタブ。"""
    def __init__(self, bus, **kw):
        self.bus = bus
        self.run_id = None
        self.git = None
        self.git_branch = "main"
        self.git_subdir = ""
        self.model = None
        self.executor = "stub"     # 既定は stub（kiro-cli を要求しない）
        self.planner = "stub"
        self.max_iterations = 3
        self.max_retries = 3
        self.lease = 1800.0
        self.argv_limit = 100000
        self.fix = False
        self.json = False
        self.cleanup_clone = True
        for k, v in kw.items():
            setattr(self, k, v)


def _make_skill_repo(root: str, tool_subdir: str = "tools/agent-flow",
                     installer_body: str = None) -> str:
    """temp に「スキルリポジトリ」を作る: main ブランチに tool_subdir/install.sh を持つ git リポジトリ。
    install.sh は --prefix で渡されたディレクトリに marker を書くだけの最小実装。リポジトリ path を返す。"""
    repo = os.path.join(root, "skillrepo")
    td = os.path.join(repo, tool_subdir)
    os.makedirs(td, exist_ok=True)
    other = os.path.join(repo, "tools", "agent-project")   # sparse 除外の確認用
    os.makedirs(other, exist_ok=True)
    pathlib.Path(other, "FILE.txt").write_text("unrelated\n")
    body = installer_body or (
        "#!/usr/bin/env bash\nset -e\nPREFIX=\"$HOME/.local/bin\"\n"
        "[ \"$1\" = --prefix ] && PREFIX=\"$2\"\nmkdir -p \"$PREFIX\"\n"
        "echo installed > \"$PREFIX/INSTALLED_MARKER\"\n")
    pathlib.Path(td, "install.sh").write_text(body)
    pathlib.Path(td, "agent-flow.py").write_text("# tool body\n")
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    for c in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
              ["git", "commit", "-q", "-m", "init"]):
        subprocess.run(c, cwd=repo, env=env, check=True, capture_output=True)
    return repo


def _commit_change(repo: str, relpath: str, content: str = "x\n") -> None:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    p = os.path.join(repo, relpath)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    pathlib.Path(p).write_text(content)
    subprocess.run(["git", "add", "-A"], cwd=repo, env=env, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "update"], cwd=repo, env=env,
                   check=True, capture_output=True)


import argparse


def _park_args(executor="gitlab", gitlab=None):
    """park & poll 系テスト用の最小 args。resolve_config は通さず必要フィールドだけ与える。"""
    return argparse.Namespace(
        executor=executor,
        gitlab=gitlab if gitlab is not None else {"watch_interval": 90.0, "max_open_issues": 0},
    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
