"""設定ファイル — `.agents/agent-amigos.yaml`（agent-project と同じ流儀）。

優先順位は CLI > 設定ファイル > 組み込み既定。環境ごとに決まる値（バス・ノード名・
使う CLI）をファイルに書き、その場限りの上書きだけ CLI で渡す。
PyYAML 無し環境は JSON（同じキー・`agent-amigos.json`）で書ける。

探索順: 1) --config 明示 2) `<cwd>/agent-amigos.*` 3) `<cwd>/.agents/agent-amigos.*`
4) `<cwd>/.agent/agent-amigos.*` 5) `~/.agents/agent-amigos.*`。
プロジェクトローカルの設定があるディレクトリが amigos ノードのホーム（＝既定のバス）に
なり、agent-dashboard の自動発見マーカーも兼ねる。グローバル設定
（`~/.agents/`）のときのホームは cwd。
"""
from __future__ import annotations

import json
import os
import sys

DEFAULT_CONFIG_NAMES = [
    "agent-amigos.yaml",
    "agent-amigos.yml",
    "agent-amigos.json",
]



AGENT_HOME = ".agents"
AGENT_HOME_LEGACY = ".agent"


def _agent_home_dir(root: "str | None" = None) -> str:
    """エージェント共通ホーム `<root>/.agents`。"""
    base = os.path.expanduser(root) if root else os.path.expanduser("~")
    return os.path.join(base, AGENT_HOME)



def agent_home_subdir(env_var: str, *parts: str) -> str:
    """共通ホーム配下の状態ディレクトリ（`$<env_var>` があればそれを最優先）。"""
    override = os.environ.get(env_var)
    if override:
        return os.path.expanduser(override)
    return os.path.join(os.path.expanduser("~"), AGENT_HOME, *parts)

def _global_agent_dir() -> str:
    return str(_agent_home_dir())


# 設定ファイルで上書きできるキーと組み込み既定。ここに無いキーは黙って無視される。
CONFIG_DEFAULTS = {
    "bus": ".",              # 既定はホーム（cwd）自身がローカルバス
    "bus_workdir": None,
    "node_id": None,
    "agent_cli": None,
    # argv 渡しの CLI へ渡せる最大バイト数（既定 100000。超過分は一時ファイルへ退避）。
    # OS/シェルの ARG_MAX はノードごとに違ってよいので、ノードのローカル設定で上書きできる
    # （agent-project / agent-flow と同名のキー。agent-cli-plugin-design.md）。
    "argv_limit": 100000,
    "tags": [],
    "repos": {},             # 担当リポジトリ（repos.schema.json 形）。ロール requires.repos の選別に使う
    "roles": [],             # 応募するロールの絞り込み（空 = 全ロール）
    "interval": 5.0,
    "resume_hours": 12.0,
    "manual_claim": False,   # true: 自動応募しない（commands/ 経由の手動引き受けのみ）
    # 委譲公示板（agent-board）への参加（請負・入札）。board を与えると daemon が板を巡回し、
    # workload=amigos の公示に repos/tags 照合で入札、勝てばオーナーとしてミッションを公示する。
    # 板は「リポジトリ＋契約」だけで処理を持たない（schemas/board.schema.json）。既定 None で無効。
    "board": None,           # 板の場所（ローカル dir / git+<url>）
    "board_workdir": None,   # git+ 板のクローン作業領域（既定は自動）
    "board_lease": 900.0,    # 板入札の lease（秒）
}


def _load_config_file(path: str) -> dict:
    try:
        import yaml  # type: ignore
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        if path.lower().endswith((".yaml", ".yml")):
            print("[agent-amigos] ERROR: YAML 設定には PyYAML が必要です（pip install pyyaml）。"
                  "JSON 設定（agent-amigos.json・同じキー）なら不要です。", file=sys.stderr)
            raise SystemExit(1)
        with open(path, encoding="utf-8") as f:
            return json.load(f)


def find_config(explicit: "str | None" = None, cwd: "str | None" = None) -> "str | None":
    """設定ファイルの探索: 明示 → ./ → ./.agents/ → ./.agent/ → ~/.agents/。"""
    if explicit:
        p = os.path.expanduser(explicit)
        if not os.path.isfile(p):
            raise SystemExit(f"[agent-amigos] 設定ファイルが見つかりません: {explicit}")
        return p
    base = os.path.abspath(cwd or os.getcwd())
    searches = [base, os.path.join(base, ".agents")]
    if os.path.realpath(base) != os.path.realpath(os.path.expanduser("~")):
        searches.append(os.path.join(base, ".agent"))
    searches.append(_global_agent_dir())
    for search in searches:
        for name in DEFAULT_CONFIG_NAMES:
            cand = os.path.join(search, name)
            if os.path.isfile(cand):
                return cand
    return None


def _resolve_home(path: "str | None", cwd: "str | None") -> str:
    """設定パスからノードホームを決める。
    - 無し / `~/.agents/agent-amigos.*` → cwd
    - `<home>/.agents/agent-amigos.*` / `<home>/.agent/agent-amigos.*` → `<home>`
    - それ以外（ルート直下・`--config` 任意パス）→ 設定ファイルの親ディレクトリ
    """
    cwd_abs = os.path.abspath(cwd or os.getcwd())
    if not path:
        return cwd_abs
    abspath = os.path.abspath(path)
    parent = os.path.dirname(abspath)
    if os.path.basename(parent) in (".agents", ".agent"):
        if os.path.abspath(parent) == os.path.abspath(_global_agent_dir()):
            return cwd_abs
        return os.path.dirname(parent)
    return parent


def load_settings(explicit: "str | None" = None, cwd: "str | None" = None) -> dict:
    """設定ファイル → 組み込み既定 の順で埋めた設定 dict を返す。
    `_config_path`（見つかったパス。無ければ None）と `_home`（設定の基準ディレクトリ）を
    載せる。相対パス（bus 等）は _home 基準で解決する側が使う。"""
    path = find_config(explicit, cwd)
    raw = _load_config_file(path) if path else {}
    if not isinstance(raw, dict):
        raise SystemExit(f"[agent-amigos] 設定が不正です（マッピングが必要）: {path}")
    # 旧 `hub:` ブロック（hub 撤去以前の設定）はここで黙って落ちる——CONFIG_DEFAULTS に
    # 無いキーは読まないため、既存の設定ファイルが残っていても起動は壊れない。
    out = {}
    for key, dflt in CONFIG_DEFAULTS.items():
        out[key] = raw.get(key, dflt)
    out["tags"] = [str(t) for t in (out["tags"] or [])]
    out["roles"] = [str(r) for r in (out["roles"] or [])]
    out["repos"] = out["repos"] if isinstance(out["repos"], (dict, list)) else {}
    out["_config_path"] = path
    out["_home"] = _resolve_home(path, cwd)
    return out


def resolve_bus_spec(settings: dict, cli_bus: "str | None") -> str:
    """バス指定の解決: CLI --bus > 環境変数 > 設定ファイル > 既定 `.`（ホーム自身）。
    相対ローカルパスはホーム基準の絶対パスへ。"""
    spec = cli_bus or os.environ.get("AGENT_AMIGOS_BUS") or str(settings.get("bus") or ".")
    # `hub+` はバスとしては廃止済みだが、ここで素通しして `make_bus` に「廃止された」旨を
    # 言わせる。ローカルパス扱いに落とすと `./hub+http:/...` を作りに行って理由が分からない。
    if spec.startswith(("git+", "hub+")):
        return spec
    if not os.path.isabs(os.path.expanduser(spec)):
        return os.path.normpath(os.path.join(settings["_home"], spec))
    return os.path.expanduser(spec)


def state_dir(home: str) -> str:
    """ホーム内の状態領域（commands / designs の親）。"""
    return os.path.join(str(_agent_home_dir(home)), "agent-amigos")


def commands_dir(home: str) -> str:
    """指示のファイル取り込み先（agent-project の commands/ と同じ結合方式）。
    dashboard 等の外部操作者は JSON を 1 ファイル置くだけ — 常駐デーモンが取り込む。"""
    return os.path.join(state_dir(home), "commands")
