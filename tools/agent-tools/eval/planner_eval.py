#!/usr/bin/env python3
"""planner（要求 → タスクグラフ）の実力を測る。P9 の最小 eval（計画 2026-08-14 / 2026-08-22 §4.2 B1）。

測るのは**本番の planner そのもの**——flow-planner スキルの `plan.py`（3 段パイプライン）を
本番と同じ引数で子プロセスとして呼び、返ってきたグラフを決定的チェッカーで判定する。
判定役（LLM）は使わない。正解はグラフの**構造**で、要求文を組んだ時点で決まっている
（構成的ラベル）: 順序がある 3 段は鎖になる、独立 3 件 + 統合は fan-out + 統合ノード、
ファイル列挙は split が 1 つだけ、1 行のタイポ修正は 1〜2 ノード。

worker_eval / text_eval と同じ規律:
  - 本番の実装を**呼ぶ**（写さない）。プロンプトはスキルの plan.py が組む。
  - 合否は決定的チェッカーだけ。`--selfcheck` で LLM 抜きに検証できる。
  - `--model` の差し替えだけで別モデルを測れる。

使い方: python3 planner_eval.py [--model gemma4:e4b] [--agent-cli ollama-json] [--repeat 3]
        [--cases PL1,PL2,PL3,PL4] [--granularity auto] [--selfcheck]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
# 本番 planner。既定はリポジトリの正典（配布物 ~/.claude/skills/flow-planner は同じ中身の写し）。
PLAN_SCRIPT = Path(os.environ.get("FLOW_PLANNER_SCRIPT",
                                  str(REPO / ".github/skills/flow-planner/scripts/plan.py")))
LEDGER_DIR = Path(os.environ.get("PLANNER_EVAL_DIR", "/tmp/agent-planner-eval"))
MODEL = "gemma4:e4b"
AGENT_CLI = "ollama-json"     # 本番の planner 変種（agents/aider.json: planner → ollama-json）
WALL_LIMIT = 900.0            # 3 段 × 1 呼び出し。agent-flow は agent_timeout × 3 を見込む
GRANULARITY = "auto"
VALID_KINDS = {"classify", "extract", "filter", "generate", "human", "judge", "map",
               "reduce", "retrieve", "split", "synthesize", "verify", "work"}
PATTERNS = {"classify-and-act", "fan-out-and-synthesize", "adversarial-verification",
            "generate-and-filter", "tournament", "loop-until-done", "map-reduce"}
try:  # 正典があれば上書き（engine と同じ「揃っている限り本番を使う」）
    from agentcore.nodecontract import VALID_KINDS as _VK  # noqa: E402
    VALID_KINDS = set(_VK)
except Exception:  # noqa: BLE001
    pass
if getattr(engine, "_FLOW", None) is not None and hasattr(engine._FLOW, "PATTERNS"):
    PATTERNS = set(engine._FLOW.PATTERNS)

# ------------------------------------------------------------------ 共通の構造検査


def _closure(tasks: list[dict]) -> dict[str, set]:
    """各タスクの推移的依存集合（循環があれば途中で止まる——循環は別に検出する）。"""
    deps = {t["id"]: set(t.get("deps") or []) for t in tasks}
    out: dict[str, set] = {}

    def walk(tid, seen):
        if tid in out:
            return out[tid]
        acc = set()
        for d in deps.get(tid, ()):
            if d in seen:
                continue
            acc.add(d)
            acc |= walk(d, seen | {d})
        out[tid] = acc
        return acc

    for t in tasks:
        walk(t["id"], {t["id"]})
    return out


def _has_cycle(tasks: list[dict]) -> bool:
    deps = {t["id"]: list(t.get("deps") or []) for t in tasks}
    state: dict[str, int] = {}

    def visit(n):
        if state.get(n) == 1:
            return True
        if state.get(n) == 2:
            return False
        state[n] = 1
        if any(visit(d) for d in deps.get(n, ()) if d in deps):
            return True
        state[n] = 2
        return False

    return any(visit(n) for n in deps)


def invariants(data: dict) -> list[str]:
    """どの要求でも満たすべき契約（planner の出力契約そのもの）。"""
    errors = []
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return ["tasks が空"]
    ids = [str(t.get("id")) for t in tasks if isinstance(t, dict)]
    if len(ids) != len(set(ids)):
        errors.append("id が重複")
    known = set(ids)
    for t in tasks:
        if not isinstance(t, dict):
            errors.append("task が dict でない")
            continue
        if not str(t.get("goal") or "").strip():
            errors.append(f"{t.get('id')}: goal が空")
        if str(t.get("kind", "work")) not in VALID_KINDS:
            errors.append(f"{t.get('id')}: 未知の kind {t.get('kind')}")
        bad = [d for d in (t.get("deps") or []) if str(d) not in known]
        if bad:
            errors.append(f"{t.get('id')}: 存在しない deps {bad}")
    if _has_cycle([t for t in tasks if isinstance(t, dict)]):
        errors.append("依存に循環")
    strategy = data.get("strategy") or {}
    unknown = [p for p in (strategy.get("patterns") or []) if p not in PATTERNS]
    if unknown:
        errors.append(f"カタログ外の pattern {unknown}")
    return errors


def _own_text(task: dict) -> str:
    """goal のうち、そのノード自身の仕事を書いた部分（`[out_of_scope]` 行は他ノードのラベルを
    引き合いに出すので外す）。"""
    return "\n".join(line for line in str(task.get("goal") or "").splitlines()
                     if not line.strip().lower().startswith("[out_of_scope]"))


def _norm_id(value: str) -> str:
    return str(value or "").lower().replace("-", "_")


def _find(tasks: list[dict], label: str, labels: tuple = ()) -> "dict | None":
    """ラベルを担うノードを 1 つに決める。goal 本文（out_of_scope 行を除く）に含む → 候補。
    複数なら、他のラベルを含まない（そのラベル専用の）ノードに絞る。goal に無ければ id で
    探す（`t_kirby_a` 等。要求は goal に書けと言っているが、同定できる限り拾う）。
    それでも 1 つに決まらなければ None＝「どれがそのラベルの仕事か機械に分からない」。"""
    hits = [t for t in tasks if label in _own_text(t)]
    if len(hits) > 1:
        others = [o for o in labels if o != label]
        hits = [t for t in hits if not any(o in _own_text(t) for o in others)]
    if not hits:
        hits = [t for t in tasks if _norm_id(label) in _norm_id(t.get("id"))]
    return hits[0] if len(hits) == 1 else None


# ------------------------------------------------------------------ ケース
# 正解がラベルから従うように、ラベルは 1 度だけ定義して要求文へ流し込む。
# 「goal にラベルを含めよ」は要求側の契約であり、planner がそれを守るかも測定の一部。

PL1_LABELS = ("KIRBY-A", "KIRBY-B", "KIRBY-C")
PL1_REQUEST = (
    "三段階で進める。まず {a} として設定ファイルの読込処理を実装する。それが終わったら "
    "{b} として、読込結果を使う変換処理を実装する。最後に {c} として変換処理の単体テストを追加する。"
    "各段は前の段の成果物が無いと着手できない。各タスクの goal にはそのラベル（{a} / {b} / {c}）を"
    "必ずそのまま含めること。"
).format(a=PL1_LABELS[0], b=PL1_LABELS[1], c=PL1_LABELS[2])

PL2_LABELS = ("LIB-X", "LIB-Y", "LIB-Z")
PL2_REQUEST = (
    "候補ライブラリ {x}・{y}・{z} の 3 つを、それぞれ独立に調べる（互いの結果に依存しない。"
    "並行してよい）。3 つの調査が揃ったら、最後に 3 つを同じ観点で比較した表を 1 つにまとめる。"
    "各調査タスクの goal にはそのラベル（{x} / {y} / {z}）を必ずそのまま含めること。"
).format(x=PL2_LABELS[0], y=PL2_LABELS[1], z=PL2_LABELS[2])

PL3_REQUEST = (
    "ディレクトリ notes/ にある各 Markdown ファイル（ITEM-01.md 〜 ITEM-12.md の 12 件）について、"
    "ファイルごとに見出し一覧を抽出する。ファイルごとの処理は互いに独立で、並列にできる。"
    "最後に全ファイル分の見出しを 1 つの索引ファイルにまとめる。"
)

PL4_REQUEST = "README.md の 1 行目にあるタイポ（'Instal' → 'Install'）を直す。それだけ。"


def check_pl1(data: dict) -> tuple[bool, str]:
    tasks = data["tasks"]
    found = {lbl: _find(tasks, lbl, PL1_LABELS) for lbl in PL1_LABELS}
    missing = [lbl for lbl, t in found.items() if t is None]
    if missing:
        return False, f"ラベルを 1 つだけ含むタスクが無い: {missing}"
    closure = _closure(tasks)
    a, b, c = (found[l]["id"] for l in PL1_LABELS)
    if a not in closure[b]:
        return False, f"{PL1_LABELS[1]} が {PL1_LABELS[0]} に依存していない"
    if b not in closure[c]:
        return False, f"{PL1_LABELS[2]} が {PL1_LABELS[1]} に依存していない"
    if any(t.get("kind") == "split" for t in tasks):
        return False, "順序課題に split がある"
    return True, f"鎖 {a}→{b}→{c}（{len(tasks)} ノード）"


def check_pl2(data: dict) -> tuple[bool, str]:
    tasks = data["tasks"]
    found = {lbl: _find(tasks, lbl, PL2_LABELS) for lbl in PL2_LABELS}
    missing = [lbl for lbl, t in found.items() if t is None]
    if missing:
        return False, f"ラベルを 1 つだけ含むタスクが無い: {missing}"
    closure = _closure(tasks)
    ids = [found[l]["id"] for l in PL2_LABELS]
    for i in ids:
        for j in ids:
            if i != j and j in closure[i]:
                return False, f"独立であるべき {i} が {j} に依存"
    joiners = [t for t in tasks if t["id"] not in ids and set(ids) <= closure[t["id"]]]
    if not joiners:
        return False, "3 件すべてに依存する統合ノードが無い"
    patterns = set((data.get("strategy") or {}).get("patterns") or [])
    if "fan-out-and-synthesize" not in patterns:
        return False, f"pattern に fan-out-and-synthesize が無い: {sorted(patterns)}"
    return True, f"fan-out 3 + 統合 {joiners[0]['id']}（{len(tasks)} ノード）"


def check_pl3(data: dict) -> tuple[bool, str]:
    tasks = data["tasks"]
    splits = [t for t in tasks if t.get("kind") == "split"]
    if len(splits) != 1:
        return False, f"split ノードが {len(splits)} 個（1 個であるべき）"
    sid = splits[0]["id"]
    chained = [t["id"] for t in tasks if sid in (t.get("deps") or [])]
    if chained:
        return False, f"split の後ろに静的チェーン {chained}（map/reduce は実行時展開）"
    patterns = set((data.get("strategy") or {}).get("patterns") or [])
    if "map-reduce" not in patterns:
        return False, f"pattern に map-reduce が無い: {sorted(patterns)}"
    return True, f"split {sid} のみ（{len(tasks)} ノード）"


def check_pl4(data: dict) -> tuple[bool, str]:
    """成果物は 1 つ（1 行の修正）。成果ノード（work/generate/map）は 2 つまで許し、
    verify を 1 つ足すまでは過分解と呼ばない（flow-planner の coarse は成果ノード 1〜3 の
    範囲だが、「読む」「特定する」を別ノードに切るのは範囲内でも過分解である）。"""
    tasks = data["tasks"]
    kinds = [t.get("kind", "work") for t in tasks]
    produce = [k for k in kinds if k in ("work", "generate", "map")]
    if not produce:
        return False, "成果ノード（work/generate）が無い"
    if len(produce) > 2:
        return False, f"1 行の修正に成果ノード {len(produce)} 個（過分解）"
    extra = [k for k in kinds if k not in ("work", "generate", "map", "verify")]
    if extra:
        return False, f"成果・verify 以外の kind: {sorted(set(extra))}"
    if len(tasks) > 3:
        return False, f"1 行の修正に {len(tasks)} ノード（過分解）"
    return True, f"{len(tasks)} ノード {sorted(set(kinds))}"


# 宣言（operation.deliverables / decision）は §1・§2 の機構の**唯一の入口**である。
# planner が書かなければ、機械判定も成果物スロット分割も本番で一度も発火しない。
# 判定は本番の検査関数（agentcore.nodecontract）をそのまま呼ぶ——ここで写して緩めると
# 「eval は通るのに本番では剥がされる宣言」を合格にしてしまう。

PL5_REQUEST = (
    "eval/humansize.py に関数 human_bytes(n) を実装し、その単体テストを "
    "eval/test_humansize.py に追加する。成果物はこの 2 ファイルで、ほかは変更しない。"
    "検証は python -m pytest -q eval で行う。"
)

PL6_REQUEST = (
    "集計スクリプトの実装案を 3 つ並列に作り、そのうち**追加の依存ライブラリが要らないもの**"
    "だけを残す。残す条件はこれだけで、ほかの観点（テストの有無・行数など）では絞らない。"
)


# 材料がプロンプト内で完結する要求（ファイルを 1 つも読まない・書かない）。道具を持った
# 小さいモデルは、プロンプトだけで解ける整形をシェルで解こうとして中身を壊す
# （実測: map は本番の起動形 `--tools bash` で 2/5、道具ゼロなら 5/5）。道具を落とす口は
# `node.readonly` の宣言しかないので、**planner が材料の在り処を見て宣言できるか**を測る。
# 役割ごと readonly にはできない——split がファイルを配る flow では map に読み取りが要る。
PL7_REQUEST = (
    "次の 3 件の問い合わせ本文を、それぞれ 1 行（30 字以内）の要約へ整形し、最後に 3 行の"
    "一覧へまとめてください。**本文はこの指示にすべて含まれています**（読むファイルは"
    "ありません）。ファイルは作らず、結果は応答の本文で返します。\n"
    "1) 請求書の PDF が文字化けする。フォント埋め込みが原因らしい。\n"
    "2) ログイン後に前の画面へ戻れない。戻るボタンが無効のまま。\n"
    "3) 月次レポートの合計が 1 日ぶんずれている。集計の締め時刻の問題。"
)


def check_pl7(data: dict) -> tuple[bool, str]:
    """道具の要らないノードに readonly が付くか（`--tools` はこの宣言でしか落ちない）。

    正解は要求から従う（構成的ラベル）: この要求にはディスクを読む・書くノードが 1 つも
    無いので、**全ノードが readonly** である。宣言が届いたかどうかだけを見る——本番の
    受け取り（`_coerce_tasks` / スキルの `normalize_tasks`）を通ったあとの値で判定する。
    """
    tasks = data["tasks"]
    declared = [t for t in tasks if t.get("readonly") is True]
    reads = [t["id"] for t in tasks if t.get("read_allocation")]
    tail = f"・read_allocation を割り付けた: {reads}" if reads else ""
    if not declared:
        return False, f"readonly を宣言したノードが 0/{len(tasks)}{tail}"
    if len(declared) < len(tasks):
        missing = [t["id"] for t in tasks if t.get("readonly") is not True]
        return False, (f"readonly を宣言したのは {len(declared)}/{len(tasks)}"
                       f"（未宣言 {missing}）{tail}")
    return True, f"{len(tasks)} ノードすべて readonly{tail}"


# 機械で確かめられる制約（字数上限・必須の言及）を verification.commands へ落とせるか。
# 制約つき要約の機械は text_eval のチェッカーにしかなく、本番で同じ要求が来たらモデルの
# 自己申告に丸投げだった（§9-2）。本番の機械はシェル（wc / grep）で既に書ける——planner が
# 書くかどうかだけが欠けている（プロンプトへ規則を足した 2026-08-31 の宣言面）。
PL8_REQUEST = (
    "次の 3 件のリリースノート本文（この指示に全文が含まれます）から要点を 1 つの要約へ"
    "まとめ、summary.md へ保存してください。制約は 2 つ: (a) 要約は 220 字以内 "
    "(b) 唯一の破壊的変更である「Python 3.9 サポート終了」へ必ず言及する。\n"
    "本文1: v4.2.0 で Python 3.9 のサポートを終了した（3.10 以上が必須になる）。\n"
    "本文2: 添付 API の再試行間隔を 2 秒から 5 秒へ延ばした。\n"
    "本文3: ログ出力の既定を JSON Lines へ変更した（旧形式はオプションで残る）。"
)


def check_pl8(data: dict) -> tuple[bool, str]:
    """要求の機械で確かめられる制約が verification.commands へ落ちるか。

    正解は要求から従う（構成的ラベル）: summary.md を作るノードの検証コマンドが
    字数上限（220）と必須言及（3.9）を機械で確かめる形になっていること。コマンドの
    綴りは固定しない——数字 220 と 3.9 が**コマンド側**（goal の自由文ではなく）に
    現れていれば、シェルが判定する形になっている。
    """
    tasks = data["tasks"]
    declared = [t for t in _produce_nodes(tasks) if isinstance(t.get("operation"), dict)]
    if not declared:
        return False, "operation を宣言したノードが無い"
    owners = [t for t in declared
              if any("summary.md" in str(d) for d in (t["operation"].get("deliverables") or []))]
    if not owners:
        return False, "summary.md を成果物に宣言したノードが無い"
    for node in owners:
        errors = engine.operation_contract_errors(node["operation"])
        if errors:
            return False, f"{node['id']} の operation が不正（engine が剥がす）: {errors[0]}"
    commands = [" ".join(str(a) for a in argv)
                for node in owners
                for argv in ((node["operation"].get("verification") or {}).get("commands") or [])]
    joined = " ".join(commands)
    if not commands:
        return False, "verification.commands が空（制約が自己申告のまま）"
    missing = [want for want in ("220", "3.9") if want not in joined]
    if missing:
        return False, f"コマンドが制約を確かめていない（{missing} が現れない）: {commands[:2]}"
    return True, f"制約 2 つをコマンドで検査: {commands[:2]}"


def _produce_nodes(tasks: list[dict]) -> list[dict]:
    return [t for t in tasks if t.get("kind", "work") in ("work", "generate")]


PL5_FILES = ("eval/humansize.py", "eval/test_humansize.py")


def check_pl5(data: dict) -> tuple[bool, str]:
    """成果物 2 つの要求で、**1 呼び出し 1 成果物**まで宣言が届くか。

    正解は要求から従う（構成的ラベル）: 要求が名指しした 2 ファイルが、本番の分割器を
    通したあとに**それぞれ別のスロットの唯一の成果物**になっていること。ノードを 1 つに
    まとめて宣言しても 2 つに分けて宣言してもよい（engine が割る）。成果物を作らない
    ノード（調査・締めくくり）に宣言が無いのは減点しない——宣言が要るのは作るノードだけ。
    """
    tasks = data["tasks"]
    # 分割が効くのは work / generate だけ（nodecontract.SPLITTABLE_KINDS）。集約・検証ノードに
    # 付いた宣言はスロットに関係しないので、ここでは見ない。
    declared = [t for t in _produce_nodes(tasks) if isinstance(t.get("operation"), dict)]
    if not declared:
        return False, "operation を宣言したノードが無い（宣言が無ければ機構は発火しない）"
    broken = []
    for node in declared:
        errors = engine.operation_contract_errors(node["operation"])
        if errors is None:
            return False, "operation_contract_errors がこの木に無い"
        if errors:
            broken.append(f"{node['id']}: {errors[0]}")
    if broken:
        return False, f"契約が不正（engine が剥がす）: {broken[:2]}"
    slots = []
    for node in declared:
        slots.extend(engine.split_by_deliverables(node) or [node])
    owners: dict = {}
    for slot in slots:
        files = [str(d) for d in (slot.get("operation", {}).get("deliverables") or [])]
        if len(files) != 1:
            return False, f"{slot['id']} が成果物 {len(files)} 件（1 スロット 1 成果物にならない）"
        owners.setdefault(files[0], []).append(slot["id"])
    missing = [f for f in PL5_FILES if f not in owners]
    if missing:
        return False, f"要求が名指しした成果物にスロットが無い: {missing}"
    duplicated = [f for f in PL5_FILES if len(owners[f]) > 1]
    if duplicated:
        return False, f"同じ成果物を複数のスロットが作る: {duplicated}"
    invented = sorted(set(owners) - set(PL5_FILES))
    if invented:
        return False, f"要求に無い成果物を宣言した: {invented}"
    return True, f"{len(slots)} スロット / 成果物 {sorted(owners)}"


def check_pl6(data: dict) -> tuple[bool, str]:
    """選別を伴う要求で、filter / judge に判定契約が付くか（付かなければモデル判定のまま）。"""
    tasks = data["tasks"]
    gates = [t for t in tasks if t.get("kind") in ("filter", "judge")]
    if not gates:
        return False, "filter / judge ノードが無い"
    declared = [t for t in gates if isinstance(t.get("decision"), dict)]
    if not declared:
        return False, f"decision を宣言した判定ノードが 0/{len(gates)}"
    for node in declared:
        errors = engine.decision_contract_errors(node["decision"])
        if errors is None:
            return False, "decision_contract_errors がこの木に無い"
        if errors:
            return False, f"{node['id']} の decision が不正: {errors[0]}"
        if not (node["decision"].get("criteria") or []):
            return False, f"{node['id']} の criteria が空（残す条件が宣言されていない）"
    if len(declared) < len(gates):
        return False, f"decision を宣言したのは {len(declared)}/{len(gates)} ノード"
    facts = sorted({str(f.get("name")) for node in declared
                    for f in (node["decision"].get("facts") or [])})
    return True, f"判定ノード {len(declared)} 件・facts {facts}"


CASES = {
    "PL1": dict(genre="順序（鎖）", request=PL1_REQUEST, check=check_pl1),
    "PL2": dict(genre="fan-out + 統合", request=PL2_REQUEST, check=check_pl2),
    "PL3": dict(genre="列挙（map-reduce）", request=PL3_REQUEST, check=check_pl3,
                probe_files=[f"notes/ITEM-{i:02d}.md" for i in range(1, 13)]),
    "PL4": dict(genre="単一（過分解の検出）", request=PL4_REQUEST, check=check_pl4),
    "PL5": dict(genre="宣言（成果物スロット）", request=PL5_REQUEST, check=check_pl5),
    "PL6": dict(genre="宣言（判定契約）", request=PL6_REQUEST, check=check_pl6),
    "PL7": dict(genre="宣言（道具の有無）", request=PL7_REQUEST, check=check_pl7),
    "PL8": dict(genre="宣言（機械検査の制約）", request=PL8_REQUEST, check=check_pl8),
}

# ------------------------------------------------------------------ 実行


def _probe_root(case: dict) -> str:
    """列挙 probe の起点。ケースが要求する実ファイルを一時ディレクトリに置く（LLM 無しの走査用）。"""
    root = tempfile.mkdtemp(prefix="planner-eval-")
    for rel in case.get("probe_files") or ():
        p = Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# {p.stem}\n\n## 見出し\n", encoding="utf-8")
    return root


def _skill_env() -> dict:
    """agent-flow が plan.py へ渡すのと同じ環境（agentcore を import できる PYTHONPATH）。
    これが無いと plan.py は組み込み 4 種しか知らず、agents/<name>.json の CLI を組めない。"""
    env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb", "AGENT_OLLAMA_THINK": "off"}
    root = str(REPO / "tools/agent-tools/agentcore")
    prev = env.get("PYTHONPATH", "")
    if root not in prev.split(os.pathsep):
        env["PYTHONPATH"] = root + (os.pathsep + prev if prev else "")
    return env


def call(case: dict) -> "tuple[int, str, str, float]":
    root = _probe_root(case)
    cmd = [sys.executable, str(PLAN_SCRIPT), case["request"], "--agent-cli", AGENT_CLI,
           "--model", MODEL, "--granularity", GRANULARITY, "--review", "false",
           "--probe-root", root]
    started = time.monotonic()
    # 打ち切りはプロセスグループごと（engine.run_process）。plan.py は各フェーズで
    # エージェント CLI（孫プロセス）を起動するので、上限で plan.py だけを殺しても
    # **孫がパイプを握ったまま**で communicate() が EOF を待ち続ける——上限が事実上
    # 効かなくなる（2026-08-29 に 70 分走り続けた。時計は 900s のままだった）。
    # 経過は monotonic で計る（壁時計はマシンのスリープを含む）。
    try:
        p = engine.run_process(cmd, capture_output=True, text=True, timeout=WALL_LIMIT,
                               cwd=root, env=_skill_env())
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        rc, out = -1, (exc.stdout or "")
        err = (exc.stderr or "") + "TIMEOUT"
    return rc, out, err, time.monotonic() - started


def judge(case: dict, rc: int, out: str, err: str, wall: float):
    data = None
    # 上限超過の判定は**壁時計ではなく打ち切りの事実**（rc / TIMEOUT マーカー）で行う。
    # 壁時計はマシンのスリープを含むので、monotonic で計る上限と一致しない——
    # 夜間に走らせると「上限内で終わったのに timeout」と記録されていた。
    if rc == -1 and "TIMEOUT" in (err or ""):
        mode, ok, note = "timeout", False, f"上限超過（{WALL_LIMIT:.0f}s で打ち切り）"
    elif rc != 0:
        mode, ok, note = "cli_error", False, (err.strip()[-160:] or f"rc={rc}")
    elif not out.strip():
        mode, ok, note = "empty", False, "本文が空"
    else:
        try:
            data = json.loads(out)
        except Exception as e:  # noqa: BLE001
            mode, ok, note = "unparsable", False, f"JSON を読めない: {e}"
        else:
            broken = invariants(data)
            if broken:
                mode, ok, note = "contract", False, "契約違反: " + "; ".join(broken[:3])
            else:
                ok, note = case["check"](data)
                mode = "correct" if ok else "wrong"
    return data, mode, ok, note


def run_one(cid: str, i: int) -> dict:
    case = CASES[cid]
    rc, out, err, wall = call(case)
    data, mode, ok, note = judge(case, rc, out, err, wall)
    tasks = (data or {}).get("tasks") or [] if isinstance(data, dict) else []
    strategy = (data or {}).get("strategy") or {} if isinstance(data, dict) else {}
    rec = dict(case=cid, genre=case["genre"], iter=i, model=MODEL, agent_cli=AGENT_CLI,
               granularity=GRANULARITY, ok=ok, mode=mode, wall=round(wall, 1), note=note,
               n_tasks=len(tasks), kinds=sorted({str(t.get("kind")) for t in tasks}),
               patterns=list(strategy.get("patterns") or []),
               granularity_resolved=strategy.get("granularity"),
               # goal は全文で残す（チェッカーを直したとき台帳から再判定できるように）。
               # 宣言（operation / decision）も残す——チェッカーを直したとき台帳から
               # 再判定できるように。goal 全文を残しているのと同じ理由。
               graph=[{"id": t.get("id"), "kind": t.get("kind"), "deps": t.get("deps"),
                       "goal": str(t.get("goal") or ""),
                       **{k: t[k] for k in ("operation", "decision") if isinstance(t.get(k), dict)},
                       **({"readonly": True} if t.get("readonly") is True else {})}
                      for t in tasks],
               stderr_tail=err.strip()[-300:])
    if engine.missing():
        rec["engine_missing"] = engine.missing()
    print(f"  {cid}#{i}: {'PASS' if ok else 'FAIL':4s} {mode:10s} {wall:6.1f}s  "
          f"{len(tasks)} ノード  {note[:70]}", flush=True)
    return rec


# ------------------------------------------------------------------ selfcheck


OP_TWO = {"operation_class": "feature",
          "scope": {"read": ["eval"], "write": ["eval/humansize.py", "eval/test_humansize.py"]},
          "deliverables": ["eval/humansize.py", "eval/test_humansize.py"],
          "verification": {"commands": [["python", "-m", "pytest", "-q", "eval"]]}}


def selfcheck() -> int:
    """チェッカーを LLM 抜きで検証する（正解は通り、典型的な外し方は落ちる）。"""
    def g(tasks, patterns):
        return {"strategy": {"patterns": patterns}, "tasks": tasks}

    good = {
        "PL1": g([{"id": "a", "goal": "KIRBY-A 読込\n[out_of_scope] KIRBY-B の変換", "deps": [],
                   "kind": "work"},
                  {"id": "b", "goal": "[scope] 変換（KIRBY-A の出力を使う）\nKIRBY-B 変換",
                   "deps": ["a"], "kind": "work"},
                  {"id": "t_kirby_c", "goal": "[scope] 変換のテスト", "deps": ["b"], "kind": "work"}],
                 ["fan-out-and-synthesize"]),      # out_of_scope の言及・id だけのラベルも同定できる
        "PL2": g([{"id": "x", "goal": "LIB-X 調査", "deps": [], "kind": "work"},
                  {"id": "y", "goal": "LIB-Y 調査", "deps": [], "kind": "work"},
                  {"id": "z", "goal": "LIB-Z 調査", "deps": [], "kind": "work"},
                  {"id": "s", "goal": "比較表", "deps": ["x", "y", "z"], "kind": "synthesize"}],
                 ["fan-out-and-synthesize"]),
        "PL3": g([{"id": "s", "goal": "notes/ の md を列挙", "deps": [], "kind": "split"}],
                 ["map-reduce"]),
        "PL4": g([{"id": "t", "goal": "README のタイポ修正", "deps": [], "kind": "work"}],
                 ["fan-out-and-synthesize"]),
        "PL5": g([{"id": "t1", "goal": "実装とテスト", "deps": [], "kind": "work",
                   "operation": OP_TWO}], ["fan-out-and-synthesize"]),
        "PL7": g([{"id": "m1", "goal": "3 件を要約", "deps": [], "kind": "map",
                   "readonly": True},
                  {"id": "r", "goal": "一覧へまとめる", "deps": ["m1"], "kind": "reduce",
                   "readonly": True}], ["map-reduce"]),
        "PL6": g([{"id": "g1", "goal": "案 1", "deps": [], "kind": "generate"},
                  {"id": "f", "goal": "条件を満たす案を残す", "deps": ["g1"], "kind": "filter",
                   "decision": {"facts": [{"name": "extra_deps", "type": "bool"}],
                                "criteria": [{"fact": "extra_deps", "op": "eq",
                                              "value": False}]}}],
                 ["generate-and-filter"]),
        "PL8": g([{"id": "t1", "goal": "要約を作る", "deps": [], "kind": "generate",
                   "operation": {"operation_class": "feature",
                                 "scope": {"write": ["summary.md"]},
                                 "deliverables": ["summary.md"],
                                 "verification": {"commands": [
                                     ["bash", "-lc",
                                      "test $(wc -m < summary.md) -le 220 && "
                                      "grep -q '3.9' summary.md"]]}}}],
                 ["fan-out-and-synthesize"]),
    }
    bad = {
        "PL1": [g([{"id": "a", "goal": "KIRBY-A", "deps": [], "kind": "work"},
                   {"id": "b", "goal": "KIRBY-B", "deps": [], "kind": "work"},
                   {"id": "c", "goal": "KIRBY-C", "deps": [], "kind": "work"}],
                  ["fan-out-and-synthesize"]),                       # 鎖が無い（全部並列）
                g([{"id": "a", "goal": "KIRBY-A と KIRBY-B", "deps": [], "kind": "work"},
                   {"id": "c", "goal": "KIRBY-C", "deps": ["a"], "kind": "work"}],
                  ["fan-out-and-synthesize"])],                      # ラベルを 1 タスクに混ぜた
        "PL2": [g([{"id": "x", "goal": "LIB-X", "deps": [], "kind": "work"},
                   {"id": "y", "goal": "LIB-Y", "deps": ["x"], "kind": "work"},
                   {"id": "z", "goal": "LIB-Z", "deps": ["y"], "kind": "work"},
                   {"id": "s", "goal": "表", "deps": ["z"], "kind": "synthesize"}],
                  ["fan-out-and-synthesize"]),                       # 直列にした
                g([{"id": "x", "goal": "LIB-X", "deps": [], "kind": "work"},
                   {"id": "y", "goal": "LIB-Y", "deps": [], "kind": "work"},
                   {"id": "z", "goal": "LIB-Z", "deps": [], "kind": "work"}],
                  ["fan-out-and-synthesize"])],                      # 統合が無い
        "PL3": [g([{"id": "s", "goal": "列挙", "deps": [], "kind": "split"},
                   {"id": "m", "goal": "抽出", "deps": ["s"], "kind": "work"},
                   {"id": "r", "goal": "集約", "deps": ["m"], "kind": "reduce"}],
                  ["map-reduce"]),                                   # 静的チェーン
                g([{"id": "w1", "goal": "ITEM-01", "deps": [], "kind": "work"},
                   {"id": "w2", "goal": "ITEM-02", "deps": [], "kind": "work"}],
                  ["fan-out-and-synthesize"])],                      # split が無い
        "PL4": [g([{"id": "a", "goal": "読む", "deps": [], "kind": "work"},
                   {"id": "b", "goal": "特定", "deps": ["a"], "kind": "generate"},
                   {"id": "c", "goal": "直す", "deps": ["b"], "kind": "work"},
                   {"id": "d", "goal": "確認", "deps": ["c"], "kind": "verify"}],
                  ["fan-out-and-synthesize"]),                       # 過分解（実測 e4b の形）
                g([{"id": "a", "goal": "分類", "deps": [], "kind": "classify"},
                   {"id": "b", "goal": "直す", "deps": ["a"], "kind": "work"}],
                  ["classify-and-act"])],                            # 余計な判定ノード
        "PL5": [g([{"id": "t1", "goal": "実装とテスト", "deps": [], "kind": "work"}],
                  ["fan-out-and-synthesize"]),                       # 宣言が無い（機構が発火しない）
                g([{"id": "t1", "goal": "実装", "deps": [], "kind": "work",
                    "operation": {"operation_class": "feature",
                                  "deliverables": ["eval/humansize.py"]}},
                   {"id": "t2", "goal": "テスト", "deps": ["t1"], "kind": "work"}],
                  ["fan-out-and-synthesize"]),                       # 片方の成果物にスロットが無い
                g([{"id": "t1", "goal": "実装とテストと計画", "deps": [], "kind": "work",
                    "operation": {"operation_class": "feature",
                                  "deliverables": [*PL5_FILES, "plan.md"]}}],
                  ["fan-out-and-synthesize"])],                      # 要求に無い成果物を足した
        "PL6": [g([{"id": "f", "goal": "追加依存の要らない案だけ残す", "deps": [],
                    "kind": "filter"}], ["generate-and-filter"]),    # decision が無い
                g([{"id": "f", "goal": "残す", "deps": [], "kind": "filter",
                    "decision": {"facts": [{"name": "extra_deps", "type": "bool"}],
                                 "criteria": [{"fact": "tests", "op": "eq",
                                               "value": "pass"}]}}],
                  ["generate-and-filter"])],                         # facts に無い fact を条件に
        "PL7": [g([{"id": "m1", "goal": "3 件を要約", "deps": [], "kind": "map"}],
                  ["map-reduce"]),                                     # 宣言が無い（道具付きで走る）
                g([{"id": "m1", "goal": "要約", "deps": [], "kind": "map", "readonly": True},
                   {"id": "r", "goal": "まとめ", "deps": ["m1"], "kind": "reduce"}],
                  ["map-reduce"])],                                    # 一部だけ宣言
        "PL8": [g([{"id": "t1", "goal": "220 字以内・3.9 に言及して summary.md を作る",
                    "deps": [], "kind": "generate",
                    "operation": {"operation_class": "feature",
                                  "scope": {"write": ["summary.md"]},
                                  "deliverables": ["summary.md"]}}],
                  ["fan-out-and-synthesize"]),      # 制約が goal の自由文だけ（自己申告のまま）
                g([{"id": "t1", "goal": "要約を作る", "deps": [], "kind": "generate",
                    "operation": {"operation_class": "feature",
                                  "scope": {"write": ["summary.md"]},
                                  "deliverables": ["summary.md"],
                                  "verification": {"commands": [
                                      ["python", "-m", "pytest", "-q", "tests"]]}}}],
                  ["fan-out-and-synthesize"])],     # コマンドはあるが制約を確かめていない
    }
    contract_bad = [
        g([{"id": "a", "goal": "x", "deps": ["zz"], "kind": "work"}], []),      # 無い deps
        g([{"id": "a", "goal": "x", "deps": ["b"], "kind": "work"},
           {"id": "b", "goal": "y", "deps": ["a"], "kind": "work"}], []),        # 循環
        g([{"id": "a", "goal": "x", "deps": [], "kind": "work"}], ["panel"]),    # カタログ外
        g([{"id": "a", "goal": "x", "deps": [], "kind": "work"},
           {"id": "a", "goal": "y", "deps": [], "kind": "work"}], []),           # id 重複
    ]
    failures = 0
    for cid, case in CASES.items():
        assert not invariants(good[cid]), (cid, invariants(good[cid]))
        ok, note = case["check"](good[cid])
        if not ok:
            print(f"  NG {cid}: 正解が落ちた: {note}"); failures += 1
        for k, sample in enumerate(bad[cid]):
            ok, note = case["check"](sample)
            if ok:
                print(f"  NG {cid} bad#{k}: 外し方が通った"); failures += 1
    for k, sample in enumerate(contract_bad):
        if not invariants(sample):
            print(f"  NG contract#{k}: 契約違反が通った"); failures += 1
    print("selfcheck:", "OK" if not failures else f"{failures} 件 NG")
    return int(bool(failures))


def main() -> None:
    global MODEL, AGENT_CLI, WALL_LIMIT, GRANULARITY
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--agent-cli", default=AGENT_CLI,
                    help="planner に使う CLI 定義名（本番の planner 変種は ollama-json）")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--cases", default=",".join(CASES))
    ap.add_argument("--wall", type=float, default=WALL_LIMIT)
    ap.add_argument("--granularity", default=GRANULARITY,
                    choices=("auto", "coarse", "fine", "finest"))
    ap.add_argument("--selfcheck", action="store_true",
                    help="チェッカーの自己検証だけを行う（LLM を呼ばない）")
    args = ap.parse_args()
    if args.selfcheck:
        raise SystemExit(selfcheck())
    MODEL, AGENT_CLI, WALL_LIMIT, GRANULARITY = (args.model, args.agent_cli, args.wall,
                                                 args.granularity)
    if not PLAN_SCRIPT.exists():
        raise SystemExit(f"planner が見つかりません: {PLAN_SCRIPT}（FLOW_PLANNER_SCRIPT で指定）")
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    ledger = LEDGER_DIR / "ledger.jsonl"
    cids = [c.strip() for c in args.cases.split(",") if c.strip()]
    print(f"model={MODEL} agent_cli={AGENT_CLI} planner={PLAN_SCRIPT}")
    print(f"wall_limit={WALL_LIMIT:.0f}s granularity={GRANULARITY} cases={cids} "
          f"repeat={args.repeat}\n")
    rows = []
    for cid in cids:
        for i in range(1, args.repeat + 1):
            rec = run_one(cid, i)
            rows.append(rec)
            with ledger.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("\n=== 正解率（決定的チェッカー）")
    for cid in cids:
        r = [x for x in rows if x["case"] == cid]
        if not r:
            continue
        n = len(r); ok = sum(1 for x in r if x["ok"])
        walls = sorted(x["wall"] for x in r)
        print(f"  {cid} ({CASES[cid]['genre']}): {ok}/{n}  中央値 {walls[len(walls)//2]:.0f}s  "
              f"様式 {sorted(set(x['mode'] for x in r))}")
    print(f"  合計: {sum(1 for x in rows if x['ok'])}/{len(rows)}")
    print(f"\n台帳: {ledger}")


if __name__ == "__main__":
    main()
