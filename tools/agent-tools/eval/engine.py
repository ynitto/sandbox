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
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

_MISSING: "list[str]" = []


def _load():
    import sys
    sys.path.insert(0, str(REPO / "tools/agent-flow"))
    sys.path.insert(0, str(REPO / "tools/agent-tools/agentcore"))
    flow = methods = procgroup = None
    try:
        import agent_flow as flow  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001 — エンジンが読めない木でも測定は続ける
        _MISSING.append(f"agent_flow ({e})")
    try:
        from agentcore import methods  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        _MISSING.append(f"agentcore.methods ({e})")
    try:
        from agentcore import procgroup  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        _MISSING.append(f"agentcore.procgroup ({e})")
    return flow, methods, procgroup


_FLOW, _METHODS, _PROCGROUP = _load()


def _need(obj, name: str, what: str):
    """あれば返す。無ければ記録して None（呼び出し側が代替へ倒す）。"""
    value = getattr(obj, name, None) if obj is not None else None
    if value is None and what not in _MISSING:
        _MISSING.append(what)
    return value


def missing() -> "list[str]":
    """この木で使えなかったエンジン機能。起動行と台帳へ出すためのもの。"""
    return list(_MISSING)


# ---------------------------------------------------------------- 子の起こし方


def run_process(argv, **kwargs):
    """子を**プロセスグループごと**起こし、上限では group ごと落とす。

    `subprocess.run(timeout=…)` は直接の子しか殺さない。ハーネスが起こす agent-herd は
    その下でエージェント CLI（さらにその下で推論クライアント）を起こすので、上限で親だけ
    殺しても孫がパイプを握ったまま `communicate()` が EOF を待つ——**上限が効かない**
    （実測 2026-08-29: 上限 900 秒の実行が 70 分続き、殺したはずの推論が朝まで GPU を
    占めた）。実装は本番（`agentcore.procgroup`）を呼ぶ——写さない。

    無い木では素の `subprocess.run` へ倒す（孫は残る。倒したことは `missing()` に出る）。
    """
    fn = _need(_PROCGROUP, "run", "procgroup.run（プロセスグループごとの打ち切り）")
    if fn is None:
        return subprocess.run(argv, **kwargs)
    return fn(argv, **kwargs)


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


def structured_kinds() -> frozenset:
    """本番が JSON を抽出しようとする kind。ここに無い kind の出力は**本文のまま**下流へ
    渡るので、JSON を抽出できないことを失点として数えてはいけない（classify / synthesize）。"""
    return getattr(_FLOW, "STRUCTURED_KINDS", None) or frozenset()


def patterns() -> dict:
    """evaluator へ渡すパターン目録。無ければ空（プロンプトが短くなる）。"""
    return getattr(_FLOW, "PATTERNS", None) or {}


def validate_node_data(kind: str, data):
    """ノード成果の契約検査。**本番の実装**（`agentcore.nodecontract`）をそのまま呼ぶ。

    契約違反はメッセージ（str）で返す——例外の型をハーネス側へ持ち出すと、本番の
    エラー分類に触らずに済ませられなくなる。この木に実装が無ければ None。
    """
    nc = _need(_FLOW, "_nodecontract", "nodecontract（ノード成果の契約検査）")
    if nc is None:
        return None
    try:
        return nc.validate_node_data(kind, data)
    except Exception as e:  # noqa: BLE001 — NodeDataError（本番も同じ形で弾く）
        return str(e)


def normalize_verify(data, text: str = ""):
    """verify 成果の正規化。**本番の実装**（`waits._normalize_verify`）をそのまま呼ぶ。

    ゲートが実際にどう倒れるかを測るので、写して緩めない——本番は JSON が欠けても
    本文の `verify=pass` / `verify=fail` から `ok` を導き、どちらも無ければ fail に倒す。
    """
    fn = _need(_FLOW, "_normalize_verify", "_normalize_verify（verify 成果の正規化）")
    if fn is None:
        return None
    body = text or (data if isinstance(data, str) else json.dumps(data, ensure_ascii=False,
                                                                  default=str))
    return fn(body, data if isinstance(data, dict) else None)


# ---------------------------------------------------------------- 起動形の解決


def _agentcli():
    return _need(_FLOW, "_agentcli", "agentcli（CLI 定義のローダ）")


def cli_name_for(kind: str, base: str = "ollama") -> str:
    """役割 → 本番が起動する CLI 定義名。振り替え規則は写さず本番の解決器を呼ぶ。

    用途別の変種振り替え（`resolve_variant`）を持たない木では振り替え前の base のまま
    返す。倒したことは `missing()` に残るので、その木で取った split の数字は
    「振り替え前」と読める。

    **役割の許可リストは引かない。** 振り替えるかどうかは定義側の申告（`variants`）が
    決めるので、測る側が別の集合を持つと本番と食い違う（設計 2026-08-27 §3.3 / G2）。
    以前はここが agent-flow の `VARIANT_ELIGIBLE_ROLES` を覗いていた。
    """
    cli = _agentcli()
    if cli is None:
        return base
    resolve_variant = getattr(cli, "resolve_variant", None)
    if resolve_variant is None:
        _need(None, "", "resolve_variant（用途別の変種振り替え）")
        return base
    variant = resolve_variant(base, kind)
    return variant["agent_cli"] if variant else base


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


def agent_readonly(purpose: str) -> bool:
    """この役割を読み取り専用で呼ぶか。**本番の解決器**（`_agent_readonly`）を呼ぶ。

    ここを間違えると道具の有無ごと変わる——`readonly=True` は `--tools` を落とすので、
    本番が道具付きで走らせている役割（retrieve の read セット・base の bash セット）を
    道具ゼロで測ることになる。無い木では False（＝道具あり）へ倒す。
    """
    fn = _need(_FLOW, "_agent_readonly", "_agent_readonly（役割別の readonly 解決）")
    return bool(fn(purpose)) if fn is not None else False


def envelope_data(text: str):
    """work / generate の完了 envelope（末尾の `{"ok": ...}`）の受け方。**本番の 1 実装**を呼ぶ
    （`agent.envelope_data`）——写すと、契約が変わった日に測定だけ古い規則で読むことになる。"""
    fn = _need(_FLOW, "envelope_data", "envelope_data（完了 envelope の受け方）")
    return fn(text) if fn is not None else None


def worker_role(kind: str) -> str:
    """kind の役割行（出力契約の本文）。本番の `WORKER_ROLES` をそのまま読む（写さない）。"""
    roles = _need(_FLOW, "WORKER_ROLES", "WORKER_ROLES（kind 別の役割行）")
    if roles is None:
        return ""
    return roles.get(kind) or getattr(_FLOW, "DEFAULT_WORKER_ROLE", "")


def production_argv(name: str, model: str, readonly: bool,
                    fallback: "list[str] | None" = None) -> "tuple[list[str], str]":
    """本番がその役割で実際に起こす argv。**profile の引数（道具・ラウンド上限）まで含む**。

    `agents/<name>.json` の `command` だけを読むと、`--tools read --max-rounds 30`
    （retrieve）や `--think off --tools bash --max-rounds 12`（base）が落ちる——道具の
    無い起動形で測って「この役割は落ちる」と結論する事故になる。組み立ては本番の
    `headless_cmd` に任せ、引けない木では `command` へ倒す（倒したことは missing に残る）。
    """
    cli = _agentcli()
    if cli is not None:
        try:
            built = cli.headless_cmd(cli.load_cli(name), model, "", readonly=readonly)
            argv = [str(a) for a in built.get("argv") or []]
            if argv:
                return argv, f"agents/{name}.json（headless_cmd・readonly={readonly}）"
        except Exception as e:  # noqa: BLE001 — 組み立てられないなら command へ倒す
            if "headless_cmd" not in str(_MISSING):
                _MISSING.append(f"headless_cmd（本番の argv 組み立て: {e}）")
    return load_cmd(name, list(fallback or []))


# ---------------------------------------------------------------- 決定化パイプ（P4 / E6）


def _nodecontract():
    """本番の判定契約モジュール。無い木では None——呼び出し側がその測定だけを落とす。"""
    try:
        from agentcore import nodecontract  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        if "agentcore.nodecontract" not in str(_MISSING):
            _MISSING.append(f"agentcore.nodecontract ({e})")
        return None
    return nodecontract


def decide_candidates(criteria, facts, tie_break=None):
    """多基準判定の決定的部分。本番（agentcore.nodecontract）を呼ぶ（写さない）。"""
    nodecontract = _nodecontract()
    if nodecontract is None:
        return None
    fn = _need(nodecontract, "decide_candidates", "decide_candidates（決定的判定）")
    return fn(criteria, facts, tie_break=tie_break) if fn is not None else None


def fact_extraction_directive(decision):
    """判定契約から抽出依頼文を作る。**本番が投げるのと同じ文面**を測るため写さない。"""
    nodecontract = _nodecontract()
    if nodecontract is None:
        return None
    fn = _need(nodecontract, "fact_extraction_directive", "fact_extraction_directive（抽出依頼文）")
    return fn(decision) if fn is not None else None


def carry_dependency_gaps(dep_results, text: str, data):
    """集約結果へ依存の申告した欠落を運ぶ。本番（agentcore.nodecontract）を呼ぶ（写さない）。

    無い木では入力をそのまま返す——機械が運ばない条件で測ったことは missing() に残る。
    """
    nodecontract = _nodecontract()
    fn = (_need(nodecontract, "carry_dependency_gaps", "carry_dependency_gaps（欠落の運搬）")
          if nodecontract is not None else None)
    return fn(dep_results, text, data) if fn is not None else (text, data)


def operation_contract_errors(contract):
    """処理契約の形式検査。本番（agentcore.nodecontract）を呼ぶ（写さない）。"""
    nodecontract = _nodecontract()
    if nodecontract is None:
        return None
    fn = _need(nodecontract, "operation_contract_errors", "operation_contract_errors（処理契約検査）")
    return fn(contract) if fn is not None else None


def decision_contract_errors(decision):
    """判定契約の形式検査。同上——planner が書いた宣言を本番と同じ規則で受理 / 却下する。"""
    nodecontract = _nodecontract()
    if nodecontract is None:
        return None
    fn = _need(nodecontract, "decision_contract_errors", "decision_contract_errors（判定契約検査）")
    return fn(decision) if fn is not None else None


def split_by_deliverables(node):
    """成果物スロットの機械分割。本番（agentcore.nodecontract）を呼ぶ（写さない）——
    「機械が割った形」を測るのだから、割り方の実装は本番と同じでなければ意味がない。"""
    nodecontract = _nodecontract()
    if nodecontract is None:
        return None
    fn = _need(nodecontract, "split_by_deliverables", "split_by_deliverables（成果物スロット分割）")
    return fn(node) if fn is not None else None


def normalize_facts(decision, data):
    """モデル出力の事実を判定入力へ正規化する（本番と同じ寛容度・同じ欠測の扱い）。"""
    nodecontract = _nodecontract()
    if nodecontract is None:
        return None
    fn = _need(nodecontract, "normalize_facts", "normalize_facts（事実の正規化）")
    return fn(decision, data) if fn is not None else None


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
