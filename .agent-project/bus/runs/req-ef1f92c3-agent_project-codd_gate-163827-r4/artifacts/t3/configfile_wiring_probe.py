#!/usr/bin/env python3
"""configfile 層の「配線結果」を1つの JSON へ落とす。3本のツリー（main / branch HEAD / 作業ツリー）で
同じスクリプトを走らせ、diff が空かどうかで振る舞い等価を判定する。

観測するのは t3 の完了条件そのもの:
  1. build_config が返す配線値（regression_cmd / intake_cmd）— 設定読み込みで何が配線されるか
  2. 設定読み込みが provider module を import するか（sys.modules 差分）
  3. どの module がどの順で候補になり、どれが採用されるか
  4. provider 不在時に何が起きるか（例外か、無言の縮退か）

使い方: PYTHONPATH=<repo>/tools/agent-project python3 configfile_wiring_probe.py
"""
import json
import os
import sys
import tempfile
import types
from pathlib import Path

import agent_project as km

PROVIDER_PREFIXES = ("codd_gate",)


def _provider_modules():
    """いま import 済みの provider module 名（設定読み込みの副作用を観測するため）。"""
    return sorted(n for n in sys.modules if n.startswith(PROVIDER_PREFIXES))


def _build(root, yaml_text=None, **cli):
    """resolve_config → build_config を、CLI 引数なし（＝設定ファイル＋既定のみ）で走らせる。"""
    if yaml_text is not None:
        (Path(root) / "agent-project.yaml").write_text(yaml_text, encoding="utf-8")
    ns = types.SimpleNamespace(root=str(root), config=None, **cli)
    cwd = os.getcwd()
    os.chdir(root)                      # _find_config は cwd 起点で探すので合わせる
    try:
        km.resolve_config(ns)
        return km.build_config(ns)
    finally:
        os.chdir(cwd)


def scenario(name, *, repos=False, yaml_text=None, **cli):
    """1 シナリオぶんの配線結果と、その過程で import された provider を記録する。"""
    before = set(_provider_modules())
    with tempfile.TemporaryDirectory() as d:
        if repos:
            (Path(d) / "repos.json").write_text(
                json.dumps({"app": {"url": "git@h:t/a.git"}}), encoding="utf-8")
        try:
            cfg = _build(d, yaml_text=yaml_text, **cli)
            out = {"regression_cmd": cfg.regression_cmd,
                   "intake_cmd": cfg.intake_cmd,
                   "hooks": getattr(cfg, "hooks", "<no attr>")}
        except Exception as e:                       # 設定読み込みは何があっても落ちてはいけない
            out = {"raised": f"{type(e).__name__}: {e}"}
    out["provider_imported_by_config_load"] = sorted(set(_provider_modules()) - before)
    return name, out


def sibling_candidates():
    """sibling 走査の候補を、実装と同じ規則・同じ順序で列挙する（「どの順で」の観測）。"""
    sib = Path(km.__file__).resolve().parent.parent
    if not sib.is_dir():
        return []
    return [p.stem for p in sorted(sib.glob("*.py"))
            if not p.stem.startswith("_") and p.stem.isidentifier()]


def resolution():
    """能力キーごとの採用 module。旧実装（main）は専用リゾルバしか持たないのでそちらを見る。"""
    out = {}
    if hasattr(km, "_hook_provider"):
        for cap in sorted(getattr(km, "HOOK_CAPABILITIES", {})):
            km._HOOK_CACHE.clear()
            mod = km._hook_provider(cap)
            out[cap] = getattr(mod, "__name__", None)
    if hasattr(km, "_codd_gate_wiring_module"):      # main の専用リゾルバ
        mod = km._codd_gate_wiring_module()
        out["<legacy>_codd_gate_wiring_module"] = getattr(mod, "__name__", None)
    return out


def absent_provider():
    """provider が 1 つも無い環境（空ディレクトリを走査先にする）で何が起きるか。"""
    if not hasattr(km, "_hook_scan_siblings"):
        return "<no scan hook>"
    out = {}
    with tempfile.TemporaryDirectory() as empty:
        for cap, required in sorted(getattr(km, "HOOK_CAPABILITIES", {}).items()):
            try:
                mod = km._hook_scan_siblings(required, sib=Path(empty))
                out[cap] = getattr(mod, "__name__", None)
            except Exception as e:
                out[cap] = f"raised {type(e).__name__}: {e}"
    return out


def main():
    scenarios = [
        scenario("bare"),                                        # repos.json も設定も無い
        scenario("repos_json_present", repos=True),              # main はここで自動配線していた
        scenario("explicit_commands", repos=True,
                 yaml_text="regression_cmd: my-regression\nintake_cmd: my-intake\n"),
        scenario("hooks_configured", repos=True,
                 yaml_text="hooks:\n  wiring: codd_gate_wiring\n"),
        scenario("hooks_bogus", repos=True,
                 yaml_text="hooks:\n  wiring: no_such_module\n"),
    ]
    print(json.dumps({
        "build_config": dict(scenarios),
        "sibling_candidates_in_scan_order": sibling_candidates(),
        "capability_resolution": resolution(),
        "absent_provider_scan": absent_provider(),
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
