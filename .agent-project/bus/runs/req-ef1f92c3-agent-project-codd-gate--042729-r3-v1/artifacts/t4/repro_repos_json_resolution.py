#!/usr/bin/env python3
"""t4 検証材料: repos.json の解決パスが regression_cmd/intake_cmd 実行時の cwd と
一致するかを、隔離した一時 git リポジトリ上で再現する。実リポジトリ（sandbox /
sandbox-agent-state）には一切書き込まない。

使い方:
    python3 repro_repos_json_resolution.py

やること:
  1. /tmp に使い捨て git リポジトリを作り、実環境と同じ `root: .agent-project`
     レイアウトを再現する。
  2. tools/agent-project の agent_project パッケージをテストと同じ方法
     （importlib、単体 import 不可のため）でロードし、build_config を呼ぶ。
  3. repos.json を「未生成」「cfg.backlog.parent 直下に生成済み」の2状態で
     cfg.workdir・repo_registry_path・codd-gate 自動推奨コマンドを観測する。
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

AGENT_PROJECT_PKG = Path(
    "/Users/nitto/Workspace/sandbox/tools/agent-project/agent_project"
)


def _load_km():
    spec = importlib.util.spec_from_file_location(
        "agent_project", AGENT_PROJECT_PKG / "__init__.py",
        submodule_search_locations=[str(AGENT_PROJECT_PKG)])
    km = importlib.util.module_from_spec(spec)
    sys.modules["agent_project"] = km
    spec.loader.exec_module(km)
    return km


def _build_cfg(km, repo_dir: str):
    ns = types.SimpleNamespace(root=".agent-project", config=None)
    km.resolve_config(ns)
    return km.build_config(ns)


def main() -> int:
    base = Path("/tmp/ap_repro_t4")
    state = Path("/tmp/ap_repro_t4-agent-state")
    shutil.rmtree(base, ignore_errors=True)
    shutil.rmtree(state, ignore_errors=True)
    base.mkdir(parents=True)
    (base / ".agent-project").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=base, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=base, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=base, check=True)
    (base / ".agent-project" / ".gitkeep").write_text("", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=base, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=base, check=True)

    cwd0 = os.getcwd()
    os.chdir(base)
    try:
        km = _load_km()
        cfg = _build_cfg(km, str(base))
        print("=== state 1: repos.json 未生成 ===")
        print("cfg.workdir        =", cfg.workdir)
        print("cfg.backlog.parent =", cfg.backlog.parent)
        print("repo_registry_path =", km.repo_registry_path(cfg))
        print("regression_cmd     =", cfg.regression_cmd)
        print("intake_cmd         =", cfg.intake_cmd)

        # repos.json を「export_repo_registry が実際に書く場所」＝cfg.backlog.parent 直下に置く。
        repos_path = cfg.backlog.parent / "repos.json"
        repos_path.parent.mkdir(parents=True, exist_ok=True)
        repos_path.write_text(json.dumps({"app": {"url": "git@h:t/a.git"}}), encoding="utf-8")

        cfg2 = _build_cfg(km, str(base))
        print()
        print("=== state 2: repos.json を cfg.backlog.parent 直下に生成後 ===")
        print("cfg.workdir        =", cfg2.workdir)
        print("repo_registry_path =", km.repo_registry_path(cfg2))
        print("regression_cmd     =", cfg2.regression_cmd)
        print("intake_cmd         =", cfg2.intake_cmd)

        print()
        real_yaml_value = "--repos .agent-project/repos.json"
        auto_value = f"--repos {str(km.repo_registry_path(cfg2).relative_to(cfg2.workdir))}"
        print("実環境 .agent/agent-project.yaml の手書き値:", real_yaml_value)
        print("build_config 自動推奨値（同一 vcwd 前提）   :", "--repos " +
              str(Path(os.path.relpath(km.repo_registry_path(cfg2), cfg2.workdir)).as_posix()))
        print("一致するか:", real_yaml_value == auto_value)
    finally:
        os.chdir(cwd0)
        shutil.rmtree(base, ignore_errors=True)
        shutil.rmtree(state, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
