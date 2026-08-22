"""doctor — 源泉の到達性・session_log 宣言の有無・未収集 CLI・clean ルールの
スキップと未対応ログバージョンの一覧（仕様書 §5）。"""
from __future__ import annotations

import os

from .collect import agent_defs_with_session_log
from .configfile import INERT_KEYS, resolve_audit_dir, resolve_budget_dir
from .store import Store, home_relative

_CLEAN_SAMPLE_LIMIT = 20


def _print_clean_summary(name: str, slog: dict) -> None:
    """clean 宣言の棚卸し（仕様書 §3）。直近セッションを間引いてサンプルし、
    検出できたログバージョンの分布と、clean 適用中に出た警告（未知ルール名・
    不正な正規表現など）をまとめて表示する。宣言が無ければその旨だけ出す。"""
    clean = slog.get("clean")
    if not isinstance(clean, dict):
        print("      clean: 宣言なし（本文はそのまま収集されます）")
        return
    n_default = len(clean.get("rules") or [])
    versions_cfg = clean.get("versions") or []
    n_versions = len(versions_cfg) if isinstance(versions_cfg, list) else 0
    print(f"      clean: 既定ルール {n_default} 件・バージョン別セット {n_versions} 段"
          f"（version_key={clean.get('version_key') or '未設定（検出しない）'}）")
    from . import readers
    try:
        sample = readers.read_sessions(slog, limit=_CLEAN_SAMPLE_LIMIT)
    except OSError:
        sample = []
    if not sample:
        return
    seen_versions = sorted({s.get("log_version") or "（未検出）" for s in sample})
    warnings = sorted({w for s in sample for w in (s.get("_clean_warnings") or [])})
    print(f"      直近 {len(sample)} セッションのサンプル: 検出バージョン "
          f"{', '.join(seen_versions)}")
    if warnings:
        print("      clean 警告（そのルールだけスキップ済み）:")
        for w in warnings:
            print(f"        - {w}")


def _print_memory_stores(args) -> None:
    """記憶 3 層 + 共有路の到達性（計画 §3.1）。未設定は「未収集」と明示する——
    黙って部分集計を全体と偽らないため、doctor で先に見えるようにしておく。"""
    from .memory import memory_stores_config
    cfg = memory_stores_config(args)
    print("\n記憶ストア（memory-store 源泉）:")
    entries = [("ltm_dirs", d) for d in cfg["ltm_dirs"]]
    entries += [(key, cfg[key]) for key in ("wiki_root", "persona_home", "moltbook_home")]
    if not any(v for _key, v in entries):
        print("  未設定 — 3 層とも未収集です"
              "（agent-audit.yaml の memory_stores: を設定すると集計できます）")
        return
    for key, value in entries:
        if not value:
            print(f"  {key}: 未設定（未収集）")
            continue
        p = os.path.expanduser(str(value))
        print(f"  {key}: {home_relative(p)} — "
              f"{'OK' if os.path.isdir(p) else '見つかりません（collect は exit 2 で止まります）'}")


def _print_inert_keys(cfg: dict) -> None:
    """設定ファイルに書かれているが効かないキーを報告する。

    黙って無視すると「設定したのに効かない」が原因不明の不具合になる。書いた本人は
    効いているつもりでいるぶん、静かに落とすほうが害が大きい。
    """
    found = [k for k in INERT_KEYS if k in (cfg or {})]
    if not found:
        return
    print("\n効かない設定キー:")
    for key in found:
        print(f"  {key}: {INERT_KEYS[key]}")


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
            if found:
                _print_clean_summary(name, slog)
        else:
            print(f"  {name}: session_log なし — セッションは未収集になります"
                  "（agents/" + name + ".json への宣言で収集できます）")

    for key in ("flow_buses", "project_roots", "amigos_buses", "loop_logs"):
        vals = getattr(args, key, None) or []
        for v in vals:
            p = os.path.expanduser(str(v))
            state = "OK" if os.path.exists(p) else "見つかりません"
            print(f"{key}: {v} — {state}")

    _print_memory_stores(args)

    n_rec = sum(1 for _ in store.iter_records())
    n_obs = sum(1 for _ in store.iter_observations())
    n_ins = sum(1 for _ in store.iter_insights())
    print(f"\nストア: records={n_rec} / observations={n_obs} / insights={n_ins}")
    if getattr(args, "_config_path", None):
        print(f"設定: {home_relative(args._config_path)}")
    else:
        print("設定: なし（組み込み既定で動作。雛形: agent-audit.yaml.example）")
    _print_inert_keys(getattr(args, "_config", None) or {})
    return 0
