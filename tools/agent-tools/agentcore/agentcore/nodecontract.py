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


# --- 依存が申告した欠落の運搬（集約系）------------------------------------------------
# 統合役（synthesize）は依存の申告した欠落を落とす（実測 2026-08-30: SY2 0/5。1 本は
# 「矛盾や欠落は認められず」と逆の結論まで書いた）。プロンプトの実行規律は「欠落は結論に
# 反映したうえで明記する」と言っているが、**言わせても直らない**——この日の 4 例と同じ。
#
# だから機械が運ぶ。欠落は依存の result に既にある事実なので、モデルに書き写させる必要が
# 無い（F2P・PR1P と同じ形＝モデルは事実、判断と転記は機械）。集められるのは構造化された
# 申告だけである——`{"ok": false}` を返した依存は failed になって `deps_satisfied` を通らず
# 集約役まで届かないので、done の依存が持てる欠落は契約の `warnings`（extract / retrieve）と
# `issues` に限られる。散文だけの申告は機械には拾えない（拾えないことを認めて残す）。

_GAP_KEYS = ("warnings", "issues")
GAP_HEADING = "【引き継いだ欠落】"


def collect_dependency_gaps(dep_results) -> "list[tuple[str, str]]":
    """依存の result から、申告された欠落を `(依存 id, 本文)` で集める。

    見るのは `data.warnings` / `data.issues` の文字列配列だけ。自由記述の本文は読まない
    ——本文から欠落を読み取るのはモデルの仕事で、そこが落ちるから機械を足している。
    """
    gaps: list[tuple[str, str]] = []
    for dep_id, result in (dep_results or {}).items():
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, dict):
            continue
        for key in _GAP_KEYS:
            for item in data.get(key) or []:
                text = str(item).strip()
                if text:
                    gaps.append((str(dep_id), text))
    return gaps


def carry_dependency_gaps(dep_results, text: str, data):
    """集約結果へ、依存が申告した欠落を機械的に追記した `(text, data)` を返す。

    既に本文へ書かれている欠落は重ねない（モデルが運べたぶんはそのまま）。1 件も無ければ
    入力をそのまま返す——`data` を無意味に dict へ変えない。
    """
    gaps = collect_dependency_gaps(dep_results)
    if not gaps:
        return text, data
    missing = [(dep_id, body) for dep_id, body in gaps if body not in (text or "")]
    merged = {**data} if isinstance(data, dict) else {}
    merged["gaps"] = [{"dep": dep_id, "note": body} for dep_id, body in gaps]
    if not missing:
        return text, merged
    lines = "\n".join(f"- [{dep_id}] {body}" for dep_id, body in missing)
    return f"{text}\n\n{GAP_HEADING}（依存の申告から機械が転記）\n{lines}", merged


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
    elif len(kept) == 1 and not undecided:
        # 条件だけで 1 つに絞れたなら順位基準は要らない（judge が tie_break を宣言して
        # いなくても winner を出す。欠測が残る間は従来どおり出さない）。
        winner = str(kept[0]["id"])
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


# --- 判定契約（decision contract）------------------------------------------------------
# filter / judge をモデルに訊かない形にするための宣言。ノード定義が持ち、
# 「モデルは事実だけ抽出 → 機械が :func:`decide_candidates` で判定」の口になる。

FACT_TYPES = ("bool", "int", "string")


def decision_contract_errors(decision) -> "list[str]":
    """判定契約の形式検査。エラーの文字列リスト（空 = 合格）。

    形が崩れた宣言は「無い」のと同じ扱いにする（呼び出し側は従来のモデル判定へ倒す）
    ——半端に効く宣言を作らないため、ここで一度だけ縛る。
    """
    if not isinstance(decision, dict):
        return ["判定契約はオブジェクトである必要があります"]
    errors: list[str] = []
    facts = decision.get("facts")
    names: set = set()
    if not isinstance(facts, list) or not facts:
        errors.append("facts は 1 件以上の配列である必要があります")
    else:
        for spec in facts:
            if not isinstance(spec, dict):
                errors.append("facts の要素はオブジェクトである必要があります")
                continue
            name = str(spec.get("name") or "").strip()
            if not name:
                errors.append("facts[].name は必須です")
                continue
            if name == "id":
                errors.append("facts[].name に id は使えません（候補の識別子と衝突します）")
            names.add(name)
            if spec.get("type") not in FACT_TYPES:
                errors.append(f"facts[{name}].type は {'/'.join(FACT_TYPES)} のいずれかです")
            values = spec.get("values")
            if values is not None and not _str_list(values):
                errors.append(f"facts[{name}].values は空でない文字列の配列である必要があります")
    criteria = decision.get("criteria")
    if not isinstance(criteria, list):
        errors.append("criteria は配列である必要があります")
    else:
        for criterion in criteria:
            if not isinstance(criterion, dict):
                errors.append("criteria の要素はオブジェクトである必要があります")
                continue
            fact = str(criterion.get("fact") or "")
            if names and fact not in names:
                errors.append(f"criteria の fact が facts にありません: {fact or '(空)'}")
            if criterion.get("op") not in ("eq", "ne"):
                errors.append("criteria[].op は eq / ne のいずれかです")
            if "value" not in criterion:
                errors.append(f"criteria[{fact}].value は必須です")
    tie_break = decision.get("tie_break")
    if tie_break is not None:
        if not isinstance(tie_break, dict):
            errors.append("tie_break はオブジェクトである必要があります")
        else:
            fact = str(tie_break.get("fact") or "")
            if names and fact not in names:
                errors.append(f"tie_break の fact が facts にありません: {fact or '(空)'}")
            if tie_break.get("op") not in ("min", "max"):
                errors.append("tie_break.op は min / max のいずれかです")
    return errors


def normalize_facts(decision, data) -> "list[dict]":
    """モデルが返した事実を :func:`decide_candidates` の入力へ正規化する。

    型が宣言と合わない値・宣言外の値は **None のまま渡す**（undecided として機械側が
    扱う）。ここで推測して埋めると、欠測が静かに合否へ化ける。
    """
    facts = data.get("facts") if isinstance(data, dict) else data
    specs = [s for s in (decision.get("facts") or []) if isinstance(s, dict)]
    out: list[dict] = []
    for raw in facts if isinstance(facts, list) else []:
        if not isinstance(raw, dict):
            continue
        rid = str(raw.get("id") or "").strip()
        if not rid:
            continue
        rec: dict = {"id": rid}
        for spec in specs:
            name = str(spec.get("name") or "").strip()
            if not name:
                continue
            value = raw.get(name)
            kind = spec.get("type")
            if kind == "bool":
                rec[name] = value if isinstance(value, bool) else None
            elif kind == "int":
                rec[name] = (value if isinstance(value, int) and not isinstance(value, bool)
                             else None)
            else:
                text = str(value).strip().lower() if isinstance(value, str) else None
                allowed = spec.get("values")
                if text and _str_list(allowed) and text not in [v.lower() for v in allowed]:
                    text = None
                rec[name] = text or None
        out.append(rec)
    return out


def fact_extraction_directive(decision) -> str:
    """判定契約から「事実だけ抽出せよ」の依頼文を作る（filter / judge 共通の 1 実装）。

    条件そのものは載せない——載せるとモデルが判定を始める。載せるのは
    「どの項目を、どの型で書き出すか」だけ。
    """
    lines = []
    for spec in decision.get("facts") or []:
        if not isinstance(spec, dict):
            continue
        name = str(spec.get("name") or "").strip()
        if not name:
            continue
        kind = spec.get("type")
        if kind == "bool":
            shape = "true / false"
        elif kind == "int":
            shape = "整数"
        elif _str_list(spec.get("values")):
            shape = " / ".join(f'"{v}"' for v in spec["values"])
        else:
            shape = "文字列"
        note = str(spec.get("description") or "").strip()
        lines.append(f"- {name}: {shape}" + (f"（{note}）" if note else ""))
    example = ", ".join(f'"{str(s.get("name"))}": ...' for s in (decision.get("facts") or [])
                        if isinstance(s, dict) and str(s.get("name") or "").strip())
    return ("【出力契約】各候補について機械可読の事実だけを抽出する。"
            "**採否の判定・最良案の選択はしない**（判定は機械が別に行う）。\n"
            "出力は JSON " '{"facts":[{"id":"<候補 id>", ' + example + "}, ...]} だけ。\n"
            + "\n".join(lines)
            + "\n入力に現れた候補をすべて facts に含めること。"
              "本文から読み取れない項目は値を書かず、そのキーを省くこと（推測で埋めない）。")


# --- 成果物スロット（deliverable slots）------------------------------------------------
# 成果物が 2 つ以上ある仕事を丸ごと渡すと、小さいモデルは片方を丸ごと落とす（実測:
# 丸ごと 0/3・人が 2 手順へ割ると 3/3・投げ直しは 0 回）。割り方を人に書かせず、
# 処理契約の deliverables を「1 スロット = 1 回の呼び出し」として機械が割る。

# 割る上限。これを超える宣言は割らずにそのまま渡す（暴走した分解を作らない——
# 上限に当たった事実は呼び出し側がログへ出す）。
MAX_DELIVERABLE_SLOTS = 4
SPLITTABLE_KINDS = frozenset({"work", "generate"})


def deliverable_slot_directive(target: str, others: "list[str]") -> str:
    """1 スロットぶんの依頼文（本番と測定で同じ文面を使うための 1 実装）。"""
    rest = "・".join(others)
    return (f"この手順で作る成果物は `{target}` の 1 つだけ。"
            + (f"ほかの成果物（{rest}）は別の手順で作るので、ここでは作らない・変更しない。"
               if rest else "")
            + "先の手順で作られた成果物があれば、読んで前提にしてよい。")


def split_by_deliverables(node, *, max_slots: int = MAX_DELIVERABLE_SLOTS) -> "list[dict] | None":
    """成果物が 2 つ以上ある work / generate ノードを、1 成果物 1 ノードの直列へ割る。

    割った各ノードは処理契約も絞る（deliverables / scope.write を自分のスロットだけに
    する）ので、局所修正の適格判定（:func:`local_patch_blockers`）とも整合する。
    割れないときは None——呼び出し側は元のノードをそのまま使う。
    """
    if not isinstance(node, dict) or str(node.get("kind") or "work") not in SPLITTABLE_KINDS:
        return None
    contract = node.get("operation")
    if not isinstance(contract, dict) or operation_contract_errors(contract):
        return None
    slots = [str(p) for p in (contract.get("deliverables") or [])]
    if len(slots) < 2 or len(slots) > max(2, max_slots):
        return None
    base_id = str(node.get("id") or "").strip()
    if not base_id:
        return None
    scope = contract.get("scope") or {}
    writes = {_norm(p) for p in (scope.get("write") or [])}
    goal = str(node.get("goal") or "")
    out: list[dict] = []
    for i, target in enumerate(slots):
        others = [s for s in slots if s != target]
        slot_scope = dict(scope)
        # 書込 scope は自分のスロットだけに絞る。ただし宣言外のパスを勝手に足さない
        # （元の宣言に無いなら書込宣言そのものを持たせない）。
        if writes:
            slot_scope["write"] = [target] if _norm(target) in writes else []
            if not slot_scope["write"]:
                del slot_scope["write"]
        slot_contract = {**contract, "deliverables": [target]}
        if slot_scope:
            slot_contract["scope"] = slot_scope
        elif "scope" in slot_contract:
            del slot_contract["scope"]
        out.append({
            **{k: v for k, v in node.items() if k not in ("id", "goal", "deps", "operation")},
            "id": f"{base_id}-d{i + 1}",
            "goal": f"{goal}\n\n{deliverable_slot_directive(target, others)}",
            "deps": [str(d) for d in (node.get("deps") or [])] if i == 0
                    else [f"{base_id}-d{i}"],
            "operation": slot_contract,
        })
    return out
