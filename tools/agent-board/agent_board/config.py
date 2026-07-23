"""config — 設定ファイルの探索・読み込み・ノード id 解決。

優先順位は CLI > 設定ファイル > 組み込み既定（agent-flow / agent-project と同じ流儀）。
設定は agent-board.{yaml,yml,json}（PyYAML 無しなら JSON）。探索は cwd → .agents → ~/.agents。
"""
from __future__ import annotations

import json
import os
import socket

DEFAULT_CONFIG_NAMES = ("agent-board.yaml", "agent-board.yml", "agent-board.json")

CONFIG_DEFAULTS = {
    "board": ".",            # 板の場所: ローカル dir / git+<url>
    "board_workdir": None,   # GitBus のクローン作業領域
    "board_branch": "main",
    "node_id": None,
    "workloads": [],         # 受けられるエンジン（空 = 全部）
    "tags": [],
    "agent_cli": [],         # このノードで使える agent CLI
    "repos": {},             # 担当リポジトリ（repos.schema.json 形）
    "availability": None,
    "max_concurrent": 0,     # 同時落札上限（0 = 無制限）
    "flow_bus": None,        # 落札した flow 委譲の引き渡し先（ローカル flow バス dir）
    "amigos_home": None,     # 落札した amigos 委譲の引き渡し先（ローカル amigos ホーム）
    "interval": 15.0,        # デーモン巡回間隔（秒）
    "lease": 900.0,          # 入札 lease（秒）
}


def _load_file(path: str) -> dict:
    try:
        import yaml  # type: ignore
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        if path.lower().endswith((".yaml", ".yml")):
            raise SystemExit(
                "[agent-board] YAML 設定には PyYAML が必要です。JSON 設定なら不要です。")
        with open(path, encoding="utf-8") as f:
            return json.load(f)


def _agent_home() -> str:
    base = os.path.expanduser("~")
    new, old = os.path.join(base, ".agents"), os.path.join(base, ".agent")
    return old if (not os.path.isdir(new) and os.path.isdir(old)) else new


def find_config(explicit=None) -> "str | None":
    if explicit:
        p = os.path.expanduser(explicit)
        if not os.path.isfile(p):
            raise SystemExit(f"[agent-board] 設定ファイルが見つかりません: {explicit}")
        return p
    for base in (os.getcwd(), os.path.join(os.getcwd(), ".agents"),
                 os.path.join(os.getcwd(), ".agent"), _agent_home()):
        for name in DEFAULT_CONFIG_NAMES:
            cand = os.path.join(base, name)
            if os.path.isfile(cand):
                return cand
    return None


def load_settings(args) -> dict:
    """CLI 引数 > 設定ファイル > 既定 で解決した設定 dict を返す。"""
    path = find_config(getattr(args, "config", None))
    cfg = _load_file(path) if path else {}
    out = {}
    for key, dflt in CONFIG_DEFAULTS.items():
        val = getattr(args, key, None)
        if val is None:
            val = cfg.get(key, dflt)
        out[key] = val
    out["_config_path"] = path
    # tags / agent_cli / workloads はリスト化
    for k in ("tags", "agent_cli", "workloads"):
        v = out[k]
        if isinstance(v, str):
            out[k] = [s for s in v.replace(",", " ").split() if s]
        elif not isinstance(v, list):
            out[k] = []
    return out


def resolve_node_id(args, settings: dict) -> str:
    nid = (getattr(args, "node_id", None) or settings.get("node_id")
           or os.environ.get("AGENT_BOARD_NODE"))
    if nid:
        return str(nid)
    return f"{socket.gethostname()}-{os.urandom(2).hex()}".lower().replace(" ", "-")
