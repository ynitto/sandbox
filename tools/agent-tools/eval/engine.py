"""ハーネスがエンジンへ触る唯一の口。

ハーネスは本番の実装を**呼ぶ**（写さない）。写しは定義側の変更に静かに置いていかれる
——argv を写して予算の変更に取り残された前科がある。いっぽう直接呼ぶと、**エンジン側に
まだ着地していないシンボル**へ触れた瞬間に `AttributeError` で全 run が起動前に死ぬ。
実際に 2 度踏んだ（`LIST_CONTRACT_ROLES` と `unwrap_list`）。

だから解決を 1 か所へ寄せて、次の 3 つを守る。

1. **欠けても走る。** 使えない機能はその機能だけを落とし、残りの測定は続ける。
2. **黙って落とさない。** 何が欠けたかを `missing()` で返し、起動行と台帳へ載せる
   ——欠けた木で取った数字を、揃った木の数字として読まないため。
3. **写さない。** 揃っている限り本番の実装をそのまま使う。ここに再実装は書かない。
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

_MISSING: "list[str]" = []


def _load():
    import sys
    sys.path.insert(0, str(REPO / "tools/agent-flow"))
    sys.path.insert(0, str(REPO / "tools/agent-tools/agentcore"))
    flow = methods = None
    try:
        import agent_flow as flow  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001 — エンジンが読めない木でも測定は続ける
        _MISSING.append(f"agent_flow ({e})")
    try:
        from agentcore import methods  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        _MISSING.append(f"agentcore.methods ({e})")
    return flow, methods


_FLOW, _METHODS = _load()


def _need(obj, name: str, what: str):
    """あれば返す。無ければ記録して None（呼び出し側が代替へ倒す）。"""
    value = getattr(obj, name, None) if obj is not None else None
    if value is None and what not in _MISSING:
        _MISSING.append(what)
    return value


def missing() -> "list[str]":
    """この木で使えなかったエンジン機能。起動行と台帳へ出すためのもの。"""
    return list(_MISSING)


# ---------------------------------------------------------------- 応答の解釈


def extract_json(text: str):
    """本番が受け取るのと同じ抽出。無ければ素の json だけで受ける（寛容さは落ちる）。"""
    fn = _need(_FLOW, "extract_json", "extract_json（寛容な JSON 抽出）")
    if fn is not None:
        return fn(text)
    return json.loads(text)


def unwrap_list(data):
    """配列を包んだ器を剥がす。無ければ剥がさない（split だけが影響を受ける）。"""
    fn = _need(_FLOW, "unwrap_list", "unwrap_list（配列の器剥がし）")
    return fn(data) if fn is not None else data


def extract_list(text: str):
    """本番の split 専用抽出を使う。古い木では従来の JSON 抽出＋器剥がしへ倒す。"""
    fn = getattr(_FLOW, "extract_list", None)
    return fn(text) if fn is not None else unwrap_list(extract_json(text))


def patterns() -> dict:
    """evaluator へ渡すパターン目録。無ければ空（プロンプトが短くなる）。"""
    return getattr(_FLOW, "PATTERNS", None) or {}


# ---------------------------------------------------------------- 起動形の解決


def _agentcli():
    return _need(_FLOW, "_agentcli", "agentcli（CLI 定義のローダ）")


def cli_name_for(kind: str, base: str = "ollama") -> str:
    """役割 → 本番が起動する CLI 定義名。振り替え規則は写さず本番の解決器を呼ぶ。

    配列契約の振り替え（`list_variant`）を持たない木では JSON 変種へ倒す。倒したことは
    `missing()` に残るので、その木で取った split の数字は「振り替え前」と読める。
    """
    cli = _agentcli()
    if cli is None:
        return base
    list_roles = getattr(_FLOW, "LIST_CONTRACT_ROLES", None)
    list_variant = getattr(cli, "list_variant", None)
    if list_roles is None or list_variant is None:
        _need(None, "", "list_variant（配列契約の振り替え）")
    elif kind in list_roles:
        return list_variant(base)
    json_variant = getattr(cli, "json_variant", None)
    return json_variant(base) if json_variant else base


def load_cmd(name: str, fallback: "list[str]") -> "tuple[list[str], str]":
    """`agents/<name>.json` の command。探索順は本番のローダに任せる。"""
    cli = _agentcli()
    try:
        cmd = cli.load_cli(name).get("command")
        if isinstance(cmd, list) and cmd:
            return [str(a) for a in cmd], f"agents/{name}.json"
    except Exception:  # noqa: BLE001 — 定義が引けないなら既知の既定で測る（測定を止めない）
        pass
    return list(fallback), "fallback（定義を読めませんでした）"


def load_env(name: str) -> "dict[str, str]":
    """`agents/<name>.json` の env。本番と同じ選択済み CLI 定義から読む。"""
    cli = _agentcli()
    try:
        env = cli.load_cli(name).get("env") if cli is not None else None
    except Exception:  # noqa: BLE001 — env が読めなくても既存の測定は止めない
        env = None
    if not isinstance(env, dict):
        return {}
    return {str(key): str(value) for key, value in env.items()}


def headless_cmd(name: str, model: str, prompt: str, **kwargs) -> dict:
    """本番と同じ argv 組み立て。ファイル受け渡し等の新しい口はここを通す。"""
    cli = _agentcli()
    return cli.headless_cmd(cli.load_cli(name), model, prompt, **kwargs)


# ---------------------------------------------------------------- 決定化パイプ（P4 / E6）


def decide_candidates(criteria, facts, tie_break=None):
    """多基準判定の決定的部分。本番（agentcore.nodecontract）を呼ぶ（写さない）。
    無い木では None——呼び出し側がその測定だけを落とす。"""
    try:
        from agentcore import nodecontract  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        if "agentcore.nodecontract" not in str(_MISSING):
            _MISSING.append(f"agentcore.nodecontract ({e})")
        return None
    fn = _need(nodecontract, "decide_candidates", "decide_candidates（決定的判定）")
    return fn(criteria, facts, tie_break=tie_break) if fn is not None else None


# ---------------------------------------------------------------- 手法パック


def method_apply(pack: "dict | None", context: dict) -> dict:
    """手法テキストの合成。適用判定は写さず本番の `select` をそのまま呼ぶ。"""
    if pack is None or _METHODS is None:
        _need(_METHODS, "select", "methods.select（手法の適用判定）") if pack else None
        return {"text": "", "methods": []}
    return _METHODS.select(pack, context, "")


def method_role(purpose: str) -> str:
    return _METHODS.role_for(purpose) if _METHODS is not None else "worker"


def method_cost(cli: str):
    return _METHODS.relative_cost(cli) if _METHODS is not None else 0
