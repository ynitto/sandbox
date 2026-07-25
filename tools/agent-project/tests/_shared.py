"""テストの共有前置き（実装計画 W3-2 の分割で `test_agent_project.py` から切り出したもの）。

**ここ自体はテストを持たない。** 各 `test_<機能>.py` が先頭で `from _shared import *` して
取り込む共通部分——環境隔離（開発者の cwd・設定・control が漏れないようにする）・
`agent_project` パッケージのロード（`km`）・全シャードで使うヘルパを置く。

分割しても `km` は 1 つ（exec 断片合成の単一名前空間）なので、テストの
`km.<name> = ...` モンキーパッチは分割前と同じに効く。

検証対象は案件毎ファイル（backlog/<id>.md）・done でファイル削除・watch 常駐・
フィードバック往復・案件毎の needs/decisions で、agent-flow を呼ばずに確かめる
（agent-flow stub 統合も含む）。

    python -m unittest discover -s tools/agent-project/tests        # 全部
    python -m unittest tests.test_verify                            # 1 機能だけ
"""
import contextlib
import dataclasses
import importlib.util
import io
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import unittest.mock as mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

# テストの git コミットを環境のコミット署名設定（commit.gpgsign）から切り離す。
# 署名が有効な環境では署名処理が間欠的に失敗して `git commit` がコミットを作らず、
# git ベースのテスト（成果参照・差分 verify 等）が偶発的に落ちる。GIT_CONFIG_* で
# この子プロセス（と配下）に commit.gpgsign=false を上乗せして決定的にする（identity は温存）。
os.environ["GIT_CONFIG_COUNT"] = "1"
os.environ["GIT_CONFIG_KEY_0"] = "commit.gpgsign"
os.environ["GIT_CONFIG_VALUE_0"] = "false"

# 自動アップデートは既定 on のため、テスト中にコントリビューターの実 skill-registry.json から
# 更新元が解決されて実ネットワーク/再起動が走るのを防ぐ。存在しないパスを権威指定して registry
# 解決を無効化する（SelfUpdateTests は必要なテストでだけ KIRO_SKILL_REGISTRY を一時上書きする）。
os.environ["KIRO_SKILL_REGISTRY"] = os.path.join(
    tempfile.gettempdir(), "ka-tests-no-such-registry", "skill-registry.json")

# 開発者の cwd の設定ファイル（./agent-project.yaml / ./.agent/agent-project.yaml）がテストへ
# 漏れるのを防ぐため、中立な一時 cwd で走らせる。リポジトリ直下で実行すると root=. の設定を
# 拾い、リポジトリ自体が状態リポジトリ（direct state-git）とみなされて **テストが実リポジトリへ
# コミット/push する**事故になる（2026-07-11 に実際に発生）。テストは絶対パスだけを使うので
# cwd に依存しない。
os.chdir(tempfile.mkdtemp(prefix="kp-tests-cwd-"))

# 開発者の実 agent-control（`~/.agents/control/control.json`）がテストへ漏れるのを防ぐ。
# control は agent_cli / model を**全レイヤより優先して**上書きする（`_control_override`）ため、
# 実ファイルを読むと「既定は kiro のはず」といったテストが開発者の設定次第で落ちる
# （実際に `flow.agent_cli: codex` を設定した環境で AgentCli 系が一斉に落ちていた）。
# 個別に上書きするテストは各自 addCleanup で戻す（ここは既定の隔離先）。
os.environ.setdefault("AGENT_CONTROL_DIR",
                      os.path.join(tempfile.gettempdir(), "kp-tests-no-such-control"))

_PKG = Path(__file__).resolve().parent.parent / "agent_project"
_spec = importlib.util.spec_from_file_location(
    "agent_project", _PKG / "__init__.py", submodule_search_locations=[str(_PKG)])
km = importlib.util.module_from_spec(_spec)
sys.modules["agent_project"] = km
_spec.loader.exec_module(km)

# 黒箱 CLI e2e が実プロセス起動する薄いエントリポイント（agent_project/ を起動する shim）。
_MOD = Path(__file__).resolve().parent.parent / "agent-project.py"


def mkb(d: Path, tid: str, status="ready", verify="true", source="human", title=None, retries=0):
    bd = d / "backlog"
    bd.mkdir(parents=True, exist_ok=True)
    v = f"`{verify}`" if verify else ""
    (bd / f"{tid}.md").write_text(
        f"## {tid}: {title or tid}\n- status: {status}\n- source: {source}\n"
        f"- verify: {v}\n- retries: {retries}\n", encoding="utf-8")


def mk_peer(d: Path, node: str = "pc-peer", availability: str = "draining",
            fresh_after_sec: float = 120.0):
    """他ノードの生存信号 status/<node>.json を置く。

    複数 PC 制御（CAS）は「origin があり、かつ自分以外の生存ノードが観測されている」ときだけ
    有効になる（実装計画 W1-8・`_coordination_active`）。origin を設定しただけでは単独 PC 扱い
    なので、分散モードを模すテストはピアも宣言する。

    既定を availability="draining" にしてあるのは、「排他が要るか」の判定（鮮度だけを見る）と
    「配布先に選ぶか」の判定（active も要求する）の違いを利用するため——ピアとしては数えつつ
    `allocate_distributed_tasks` の配布先には入らないので、既存の配布アサーションを乱さない。"""
    sd = d / "status"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / f"{node}.json").write_text(json.dumps({
        "node": node, "availability": availability,
        "updated_iso": datetime.now(timezone.utc).isoformat(),
        "fresh_after_sec": fresh_after_sec,
    }, ensure_ascii=False), encoding="utf-8")


def cfg_for(d: Path, **kw):
    # 既定 plan_review=False / delivery_review=False（従来動作を検証する既存テスト用）。
    # 実行前レビュー（proposed ゲート）の挙動は TestPlanReview が plan_review=True で検証する。
    base = dict(backlog=d / "backlog", policy=d / "policy.md", decisions=d / "decisions",
                journal=d / "journal.md", needs=d / "needs", workdir=d, bus=d / "bus",
                planner="none", flow_planner="stub", executor="stub", dry_run=True,
                plan_review=False, delivery_review=False)
    base.update(kw)
    return km.Config(**base)


def _submit_feedback(nf: Path, text: str):
    """needs ファイルにフィードバックを書き、確定チェックボックスを [x] にする。"""
    s = nf.read_text(encoding="utf-8").replace("- [ ] 確定", "- [x] 確定")
    nf.write_text(s + f"\n{text}\n", encoding="utf-8")


def _seed_learn(d: Path, src: str, title: str, guide: str):
    """decisions/<src>.md に learn ルールを置く。"""
    (d / "decisions").mkdir(parents=True, exist_ok=True)
    (d / "decisions" / f"{src}.md").write_text(
        f"## DR-1  2026-06-18  actor: alice\n- action  : feedback-resume\n"
        f"- learn: {title} :: {guide}\n", encoding="utf-8")


def _seed_hits(d: Path, src: str, n: int):
    """auto-resolve が src を n 回参照した決定記録を作る（昇格の根拠）。"""
    (d / "decisions").mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (d / "decisions" / f"H{i}.md").write_text(
            f"## DR-1  2026-06-18  actor: auto\n- action  : auto-resolve\n"
            f"- reason  : learned from {src}: なおせ\n", encoding="utf-8")


CHARTER = """# Charter: demo

## goal
CSV を要約する CLI を完成させる。

## constraints
- 標準ライブラリのみ

## assumptions
- 入力は UTF-8

## deliverables
- report.py

## acceptance
- `test -f {flag}`
"""


def write_charter(d: Path, body: str) -> None:
    (d / "charter.md").write_text(body, encoding="utf-8")


def _drained():
    return {"reason": km.REASON_DRAINED, "cycles": 0,
            "counts": {s: 0 for s in km.VALID_STATUS}, "cost": 0.0, "tokens": 0}


def _write_backlog_task(backlog: Path, tid: str, verify: str, title: "str | None" = None):
    """CLI e2e 用に backlog/<id>.md を書く（mkb の最小版・絶対パス前提）。"""
    backlog.mkdir(parents=True, exist_ok=True)
    (backlog / f"{tid}.md").write_text(
        f"## {tid}: {title or tid}\n- status: ready\n- verify: `{verify}`\n", encoding="utf-8")


def _make_skill_repo(root: Path, tool_subdir: str = "tools/agent-project") -> Path:
    """temp に「スキルリポジトリ」を作る: main に tool_subdir/install.sh を持つ git リポジトリ。
    install.sh は --prefix のディレクトリに marker を書くだけの最小実装。リポジトリ path を返す。"""
    repo = root / "skillrepo"
    td = repo / tool_subdir
    td.mkdir(parents=True, exist_ok=True)
    other = repo / "tools" / "agent-flow"           # sparse 除外の確認用
    other.mkdir(parents=True, exist_ok=True)
    (other / "FILE.txt").write_text("unrelated\n")
    # 本体だけでは zipapp を組み立てられない共有物（実物では tools/agent-tools）。
    # cone mode の sparse-checkout は兄弟ディレクトリを含まないので、取れているかを
    # テストで見る対象そのもの。
    core = repo / "tools" / "agent-tools" / "agentcore"
    core.mkdir(parents=True, exist_ok=True)
    (core / "protocol.py").write_text("# shared lib\n")
    # 統合インストーラ（実物では tools/agent-tools/install.sh）。共有物と同じ
    # ディレクトリに置くので、tools/agent-tools を指定すれば一緒に落ちてくる。
    (repo / "tools" / "agent-tools" / "install.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    (td / "install.sh").write_text(
        "#!/usr/bin/env bash\nset -e\nPREFIX=\"$HOME/.local/bin\"\n"
        "[ \"$1\" = --prefix ] && PREFIX=\"$2\"\nmkdir -p \"$PREFIX\"\n"
        "echo installed > \"$PREFIX/INSTALLED_MARKER\"\n")
    (td / "agent-project.py").write_text("# tool body\n")
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    for c in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
              ["git", "commit", "-q", "-m", "init"]):
        subprocess.run(c, cwd=repo, env=env, check=True, capture_output=True)
    return repo


def _commit_change(repo: Path, relpath: str, content: str = "x\n") -> None:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    subprocess.run(["git", "add", "-A"], cwd=repo, env=env, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "update"], cwd=repo, env=env,
                   check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
