"""doctor — 源泉の到達性・session_log 宣言の有無・未収集 CLI の一覧（設計 §8）。"""
from __future__ import annotations

import os

from .collect import agent_defs_with_session_log
from .configfile import resolve_audit_dir, resolve_budget_dir
from .store import Store, home_relative


def cmd_doctor(args) -> int:
    store = Store(resolve_audit_dir(args))
    print(f"audit ディレクトリ: {home_relative(store.root)}"
          f"{'（存在）' if os.path.isdir(store.root) else '（未作成 — collect が作成します）'}")

    bdir = resolve_budget_dir(args)
    ledger = os.path.join(bdir, "ledger")
    n_ledger = len([n for n in (os.listdir(ledger) if os.path.isdir(ledger) else [])
                    if n.endswith(".jsonl")])
    print(f"node-budget: {home_relative(bdir)}"
          f"（台帳 {n_ledger} ファイル）" if n_ledger else
          f"node-budget: {home_relative(bdir)}（台帳なし — エンジン未使用なら正常）")

    # session_log 宣言の棚卸し: 宣言あり / なし（未収集）を明示する（黙ってスキップしない）
    from agentcore import agentcli as _agentcli
    import glob as _glob
    import json as _json
    all_names = set()
    for d in _agentcli.plugin_dirs():
        if os.path.isdir(d):
            for p in _glob.glob(os.path.join(_glob.escape(d), "*.json")):
                all_names.add(os.path.splitext(os.path.basename(p))[0])
    with_log = dict(agent_defs_with_session_log())
    print("\nエージェント CLI 定義:")
    for name in sorted(all_names):
        if name in with_log:
            slog = with_log[name]["session_log"]
            paths = [os.path.expanduser(p) for p in slog.get("paths") or []]
            found = any(_glob.glob(p) or os.path.exists(p) for p in paths)
            state = "到達可" if found else "パス未検出（この CLI を未使用なら正常）"
            print(f"  {name}: session_log あり（format={slog.get('format')}・{state}）")
        else:
            print(f"  {name}: session_log なし — セッションは未収集になります"
                  "（agents/" + name + ".json への宣言で収集できます）")

    for key in ("flow_buses", "project_roots", "amigos_buses", "loop_logs"):
        vals = getattr(args, key, None) or []
        for v in vals:
            p = os.path.expanduser(str(v))
            state = "OK" if os.path.exists(p) else "見つかりません"
            print(f"{key}: {v} — {state}")

    n_rec = sum(1 for _ in store.iter_records())
    n_obs = sum(1 for _ in store.iter_observations())
    n_ins = sum(1 for _ in store.iter_insights())
    print(f"\nストア: records={n_rec} / observations={n_obs} / insights={n_ins}")
    if getattr(args, "_config_path", None):
        print(f"設定: {home_relative(args._config_path)}")
    else:
        print("設定: なし（組み込み既定で動作。雛形: agent-audit.yaml.example）")
    return 0
