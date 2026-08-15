"""Shared agent-flow node vocabulary and result contracts.

処理契約（operation contract, 設計 2026-08-15 §3.4）と局所修正条件の機械判定
（§2.1 / §8.3）もここが 1 実装で持つ。planner の自由文 goal だけで候補を決めない——
scope・成果物数・既存検査を構造化して照合する（柱3 / C9・C10）。
"""
from __future__ import annotations

import posixpath


VALID_KINDS = frozenset({
    "work", "generate", "classify", "synthesize", "verify",
    "filter", "judge", "reduce", "split", "map",
    "human", "extract", "retrieve",
})
PLANNER_KINDS = VALID_KINDS - {"human"}
STRUCTURED_KINDS = frozenset({
    "split", "map", "reduce", "filter", "judge", "verify", "extract", "retrieve",
})


class NodeDataError(ValueError):
    pass


def _warnings(data: dict) -> None:
    warnings = data.get("warnings", [])
    if not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings):
        raise NodeDataError("warnings は文字列配列である必要があります")


def validate_node_data(kind: str, data):
    if kind == "human":
        if not isinstance(data, dict):
            raise NodeDataError("human data はオブジェクトである必要があります")
        iid = str(data.get("interaction_id") or "")
        if len(iid) != 19 or not iid.startswith("ix-") or any(c not in "0123456789abcdef" for c in iid[3:]):
            raise NodeDataError("human interaction_id が不正です")
        if data.get("outcome") not in {"approved", "rejected", "selected", "submitted", "defaulted", "expired"}:
            raise NodeDataError("human outcome が不正です")
        if not str(data.get("actor") or "").strip() or not isinstance(data.get("answer"), dict):
            raise NodeDataError("human actor と answer は必須です")
        return data
    if kind not in ("extract", "retrieve"):
        return data
    key = "records" if kind == "extract" else "sources"
    if not isinstance(data, dict) or not isinstance(data.get(key), list):
        if kind == "retrieve":
            raise NodeDataError("retrieve data.sources は配列である必要があります")
        raise NodeDataError("extract data.records は配列である必要があります")
    _warnings(data)
    if kind == "retrieve":
        for source in data["sources"]:
            if not isinstance(source, dict):
                raise NodeDataError("retrieve source はオブジェクトである必要があります")
            for field in ("id", "uri", "title", "locator", "excerpt", "digest"):
                if not str(source.get(field) or "").strip():
                    raise NodeDataError(f"retrieve source.{field} は必須です")
        return data
    for record in data["records"]:
        if not isinstance(record, dict) or not isinstance(record.get("fields"), dict):
            raise NodeDataError("extract record.fields はオブジェクトである必要があります")
        evidence = record.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise NodeDataError("extract record.evidence は1件以上必要です")
        for item in evidence:
            if not isinstance(item, dict) or any(
                not str(item.get(key) or "").strip() for key in ("source_id", "locator", "excerpt")
            ):
                raise NodeDataError("extract evidence には source_id / locator / excerpt が必要です")
    return data


# --- 処理契約（operation contract, §3.4）----------------------------------------------

# 局所修正（T2/T4 相当）の変更量の目安。事前判定でなく、実行後の diff 検算で使う。
LOCAL_PATCH_MAX_LINES = 30

_SCOPE_KEYS = ("read", "write", "protected")


def _str_list(value) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, str) and item.strip() for item in value)


def operation_contract_errors(contract) -> "list[str]":
    """処理契約の形式検査。エラーの文字列リスト（空 = 合格）。

    operation_class だけを信頼して候補を選ばない——scope / deliverables / verification の
    構造化された条件を同時に照合するための最低限の形をここで縛る。
    """
    if not isinstance(contract, dict):
        return ["処理契約はオブジェクトである必要があります"]
    errors: list[str] = []
    if not isinstance(contract.get("operation_class"), str) or not contract["operation_class"].strip():
        errors.append("operation_class は必須です")
    scope = contract.get("scope", {})
    if not isinstance(scope, dict):
        errors.append("scope はオブジェクトである必要があります")
    else:
        for key in _SCOPE_KEYS:
            if key in scope and not _str_list(scope[key]):
                errors.append(f"scope.{key} は空でない文字列の配列である必要があります")
    for key in ("deliverables", "acceptance"):
        if key in contract and not _str_list(contract[key]):
            errors.append(f"{key} は空でない文字列の配列である必要があります")
    verification = contract.get("verification")
    if verification is not None:
        commands = verification.get("commands") if isinstance(verification, dict) else None
        if not isinstance(commands, list) or not all(
                _str_list(argv) for argv in commands):
            errors.append("verification.commands は argv（文字列配列）の配列である必要があります")
    return errors


def _norm(path: str) -> str:
    return posixpath.normpath(str(path).replace("\\", "/")).lstrip("./")


def _under(path: str, prefixes) -> bool:
    return any(path == p or path.startswith(p.rstrip("/") + "/")
               for p in (_norm(q) for q in prefixes))


def _creates_test_schema_doc(path: str) -> bool:
    name = posixpath.basename(path)
    parts = path.split("/")
    if any(part in ("tests", "test", "docs", "doc", "schemas") for part in parts[:-1]):
        return True
    if name.startswith("test_") or name.endswith(("_test.py", ".schema.json")):
        return True
    return name.endswith((".md", ".rst"))


def decide_candidates(criteria, facts, *, tie_break=None) -> dict:
    """多基準 filter / judge の決定的判定部（P4 決定化パイプ。旧計画 §P4 / 実装計画 E6）。

    モデルに多基準判定を訊かない——モデル（extract・適格 6/6）または構造化生成が出した
    機械可読の事実 ``facts = [{"id", <fact>: value}, ...]`` に対し、``criteria``
    （``[{"fact","op","value"}]``・AND）を機械が適用する。

    - op は ``eq`` / ``ne``。``tie_break={"fact","op":"min"|"max"}`` は kept が
      複数のときの順位基準（同値は id 昇順）。
    - **事実が欠けた候補は落とさず undecided へ**——欠測を静かに合格 / 不合格にしない。
      undecided が残る限り winner は出さない（誤った確定より人 / 上位へ返す）。
    """
    kept: list = []
    undecided: list = []
    for fact in facts or []:
        if not isinstance(fact, dict) or not str(fact.get("id") or "").strip():
            continue
        verdicts = []
        for criterion in criteria or []:
            value = fact.get(criterion.get("fact"))
            if value is None:
                verdicts.append(None)
            elif criterion.get("op") == "ne":
                verdicts.append(value != criterion.get("value"))
            else:  # 既定 eq
                verdicts.append(value == criterion.get("value"))
        if any(v is False for v in verdicts):
            continue
        (undecided if any(v is None for v in verdicts) else kept).append(fact)
    winner = None
    if isinstance(tie_break, dict) and kept and not undecided:
        key = tie_break.get("fact")
        ranked = [f for f in kept
                  if isinstance(f.get(key), (int, float)) and not isinstance(f.get(key), bool)]
        if len(ranked) == len(kept):    # 順位基準に欠測があるなら winner を出さない
            reverse = tie_break.get("op") == "max"
            ranked.sort(key=lambda f: (-f[key] if reverse else f[key], str(f["id"])))
            winner = str(ranked[0]["id"])
    return {"kept": [str(f["id"]) for f in kept],
            "undecided": [str(f["id"]) for f in undecided],
            "winner": winner}


def local_patch_blockers(contract, *, existing_paths=None) -> "list[str]":
    """局所修正（§2.1 の適格条件）を満たさない理由のリスト（空 = 機械判定で適格）。

    機械が事前に測れる条件だけを判定する: 書込 1 ファイル・成果物 1 つ・既存の
    検査コマンドあり・テスト / schema / 文書の新規作成でない・protected 外・
    （``existing_paths`` があれば）既存ファイルへの変更。変更量の目安
    （:data:`LOCAL_PATCH_MAX_LINES`）は実行後の diff 検算で使い、ここでは見ない。
    「複数モジュールの設計判断や広い探索を含まない」は機械では測れないので、
    planner の分解粒度（scope 宣言）に委ねる。
    """
    errors = operation_contract_errors(contract)
    if errors:
        return ["処理契約が不正です: " + "; ".join(errors[:2])]
    blockers: list[str] = []
    scope = contract.get("scope") or {}
    writes = [_norm(p) for p in (scope.get("write") or [])]
    if len(writes) != 1:
        blockers.append(f"書込 scope は 1 ファイルである必要があります（{len(writes)} 件）")
    deliverables = [_norm(p) for p in (contract.get("deliverables") or [])]
    if len(deliverables) != 1:
        blockers.append(f"成果物は 1 つである必要があります（{len(deliverables)} 件）")
    elif writes and deliverables[0] not in writes:
        blockers.append("成果物が書込 scope に含まれていません")
    verification = contract.get("verification") or {}
    if not verification.get("commands"):
        blockers.append("既存テストまたは決定的 probe（verification.commands）がありません")
    protected = scope.get("protected") or []
    for path in writes:
        if _under(path, protected):
            blockers.append(f"protected path への書込です: {path}")
        if _creates_test_schema_doc(path):
            blockers.append(f"テスト / schema / 文書は局所修正の対象外です: {path}")
        if existing_paths is not None and path not in {_norm(p) for p in existing_paths}:
            blockers.append(f"新規ファイルの作成は局所修正の対象外です: {path}")
    return blockers
