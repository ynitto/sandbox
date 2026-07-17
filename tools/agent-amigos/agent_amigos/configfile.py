"""設定ファイル — `.kiro/kiro-amigos.yaml`（agent-project と同じ流儀）。

優先順位は CLI > 設定ファイル > 組み込み既定。環境ごとに決まる値（バス・ノード名・
使う CLI・hub 公開）をファイルに書き、その場限りの上書きだけ CLI で渡す。
PyYAML 無し環境は JSON（同じキー・`.kiro/kiro-amigos.json`）で書ける。

探索順: 1) --config 明示 2) `<cwd>/.kiro/kiro-amigos.{yaml,yml,json}`。
kiro-loop の `.kiro/kiro-loop.yaml` と同じ「cwd がそのノードのホーム」規約 —
このファイルがあるディレクトリが amigos ノードのホーム（＝既定のバス・hub データ）になり、
agent-dashboard の自動発見マーカーも兼ねる。
"""
from __future__ import annotations

import json
import os
import sys

DEFAULT_CONFIG_NAMES = [
    os.path.join(".kiro", "kiro-amigos.yaml"),
    os.path.join(".kiro", "kiro-amigos.yml"),
    os.path.join(".kiro", "kiro-amigos.json"),
]

# 設定ファイルで上書きできるキーと組み込み既定。
# ネストの hub: ブロック（serve/host/port/token）は hub_* キーへ平坦化して扱う。
CONFIG_DEFAULTS = {
    "bus": ".",              # 既定はホーム（cwd）自身がローカルバス = hub データ
    "bus_workdir": None,
    "node_id": None,
    "agent_cli": None,
    "tags": [],
    "roles": [],             # 応募するロールの絞り込み（空 = 全ロール）
    "interval": 5.0,
    "resume_hours": 12.0,
    "manual_claim": False,   # true: 自動応募しない（commands/ 経由の手動引き受けのみ）
    "hub_serve": False,      # true: このホームのバスを hub として公開する（cwd を hub に）
    "hub_host": "0.0.0.0",
    "hub_port": 8765,
    "hub_token": None,       # 未指定は環境変数 AGENT_AMIGOS_HUB_TOKEN
}


def _load_config_file(path: str) -> dict:
    try:
        import yaml  # type: ignore
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        if path.lower().endswith((".yaml", ".yml")):
            print("[agent-amigos] ERROR: YAML 設定には PyYAML が必要です（pip install pyyaml）。"
                  "JSON 設定（.kiro/kiro-amigos.json・同じキー）なら不要です。", file=sys.stderr)
            raise SystemExit(1)
        with open(path, encoding="utf-8") as f:
            return json.load(f)


def find_config(explicit: "str | None" = None, cwd: "str | None" = None) -> "str | None":
    """設定ファイルの探索: 1) --config 明示 2) <cwd>/.kiro/kiro-amigos.*。無ければ None。"""
    if explicit:
        p = os.path.expanduser(explicit)
        if not os.path.isfile(p):
            raise SystemExit(f"[agent-amigos] 設定ファイルが見つかりません: {explicit}")
        return p
    base = cwd or os.getcwd()
    for name in DEFAULT_CONFIG_NAMES:
        cand = os.path.join(base, name)
        if os.path.isfile(cand):
            return cand
    return None


def load_settings(explicit: "str | None" = None, cwd: "str | None" = None) -> dict:
    """設定ファイル → 組み込み既定 の順で埋めた設定 dict を返す。
    `_config_path`（見つかったパス。無ければ None）と `_home`（設定の基準ディレクトリ）を
    載せる。相対パス（bus 等）は _home 基準で解決する側が使う。"""
    path = find_config(explicit, cwd)
    raw = _load_config_file(path) if path else {}
    if not isinstance(raw, dict):
        raise SystemExit(f"[agent-amigos] 設定が不正です（マッピングが必要）: {path}")
    hub = raw.get("hub") if isinstance(raw.get("hub"), dict) else {}
    flat = {**raw}
    flat.pop("hub", None)
    for k in ("serve", "host", "port", "token"):
        if k in hub:
            flat[f"hub_{k}"] = hub[k]
    out = {}
    for key, dflt in CONFIG_DEFAULTS.items():
        out[key] = flat.get(key, dflt)
    out["tags"] = [str(t) for t in (out["tags"] or [])]
    out["roles"] = [str(r) for r in (out["roles"] or [])]
    out["_config_path"] = path
    # 設定の基準ディレクトリ: 設定ファイルのホーム（.kiro/ の親）。無ければ cwd
    out["_home"] = (os.path.dirname(os.path.dirname(os.path.abspath(path)))
                    if path else os.path.abspath(cwd or os.getcwd()))
    return out


def resolve_bus_spec(settings: dict, cli_bus: "str | None") -> str:
    """バス指定の解決: CLI --bus > 環境変数 > 設定ファイル。相対ローカルパスは
    ホーム基準の絶対パスへ（`bus: .` = ホーム自身がバス）。"""
    spec = cli_bus or os.environ.get("AGENT_AMIGOS_BUS") or str(settings.get("bus") or ".")
    if spec.startswith(("git+", "hub+")):
        return spec
    if not os.path.isabs(os.path.expanduser(spec)):
        return os.path.normpath(os.path.join(settings["_home"], spec))
    return os.path.expanduser(spec)


def commands_dir(home: str) -> str:
    """指示のファイル取り込み先（agent-project の commands/ と同じ結合方式）。
    dashboard 等の外部操作者は JSON を 1 ファイル置くだけ — 常駐デーモンが取り込む。"""
    return os.path.join(home, ".kiro", "kiro-amigos", "commands")
