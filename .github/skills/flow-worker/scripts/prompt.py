#!/usr/bin/env python3
"""flow-worker — kiro-flow executor=agent 向けの実行系プロンプトビルダー。

kiro-flow の execute_kiro（worker/verify の各 kind）と continue_kiro（evaluator）から
呼び出され、gitlab-idd 由来の実行規律（解釈確定 → 影響範囲 → スコープ厳守 →
自己検証 → 報告契約、独立検算レビュー、受け入れ評価）を織り込んだプロンプトを生成する。

このスクリプトは LLM を呼ばない（決定的・高速）。LLM 呼び出し・役割別エージェント
解決（agents:）・argv スピルは kiro-flow 側の run_kiro が担う。出力契約（verify の
JSON、split の配列、evaluator の decision JSON 等）は kiro-flow の組み込みプロンプト
と同一に保つこと（kiro-flow 側のパーサがそれを前提にしている）。

Usage:
    python3 prompt.py < payload.json    # stdout にプロンプトを出力

payload（JSON）:
    {"role": "worker", "kind": "...", "goal": "...", "request": "...",
     "deps": {"<dep_id>": {"output": "...", "data": ...}},
     "repo_instruction": "...", "artifact_note": "...",
     "workspace": {...} | null, "references": [...]}
    {"role": "evaluator", "request": "...", "results_summary": "...",
     "human_feedback": "...", "patterns_catalog": "...", "max_retries": 3}
"""
from __future__ import annotations

import json
import sys

# --------------------------------------------------------------------------
# kind 別の役割行 — kiro-flow 組み込みプロンプトの役割定義を核として維持する
# （出力契約を含む文言は kiro-flow 側パーサとの互換に必要）。
# --------------------------------------------------------------------------
ROLE_LINES = {
    "work": "ワーカー。次のタスクだけを完了し成果物を出力する。",
    "generate": "生成役。次のタスクの成果候補を作る。並列の他候補と差別化できる切り口を自分で選び、"
                "その切り口を成果の冒頭に一行で明示する。",
    "classify": "分類役。入力を適切なカテゴリへ分類し『class=<ラベル>』形式で出力する。",
    "synthesize": "統合役。依存タスクの成果を統合して 1 つの成果物にまとめる。"
                  "単純結合ではなく、矛盾の解消・重複の統合・全体を貫く構成を行う。",
    "filter": "選別役。依存の候補から基準を満たすものだけを残し、候補ごとに採用/不採用の理由を述べる。"
              '末尾に JSON {"kept": ["<採用した dep id>", ...]} を添える。',
    "judge": "審判役。依存の複数案を比較し最良案を選び理由を述べる。"
             "比較は評価軸（要求適合・正確さ・完成度など、タスクに即して自分で定義）ごとに行い、"
             '末尾に JSON {"winner": "<最良案の dep id>"} を添える。',
    "reduce": "集約役。依存タスクの構造化データを畳み込み、集約結果を JSON で出力する。"
              "要素数を表す count を含める場合は、必ず集約後リストの実際の要素数と一致させること。",
    "split": "分解役。入力を独立に処理できる小片のリストへ分解し、"
             "各要素を文字列とする JSON 配列のみを出力する（例: [\"1-100\", \"101-200\"]）。"
             "説明文は付けず配列だけを返すこと。",
    "map": "map役。ゴールに示された本来のタスクを、与えられた1要素だけに適用して結果を返す。"
           "勝手に別の処理（合計・件数など）に変えないこと。"
           "リスト状の成果は JSON 配列で出力し、後段の集約に渡せるようにする。",
    "verify": "検証役。依存の成果を鵜呑みにせず独立に検算する。",
}

# 実装系 kind（フル実行規律を適用する）。それ以外の集約・選別系は軽量規律のみ。
EXEC_KINDS = ("work", "generate", "map")

# --------------------------------------------------------------------------
# 実行規律 — gitlab-idd worker-role（Phase 2〜5）から GitLab イシュー操作を除いた蒸留
# --------------------------------------------------------------------------
EXEC_DISCIPLINE = """\
【実行規律 — 着手から報告まで】
1. 解釈の確定: タスクを受け入れ条件として読み、何ができたら完了かを先に確定する。
   曖昧な点は途中で人に質問できないため、依存成果・全体文脈から最も妥当な解釈を選び、
   採用した前提を成果報告に必ず明記する（推測を隠さない）。
2. 影響範囲の確認: ワークスペースがある場合、編集の前に変更対象ファイル・依存関係・
   リスク箇所（壊すと波及が大きい箇所）を特定してから、最小の変更で目的を達する。
3. スコープ厳守: タスクの範囲外・許可フォルダ外を変更しない。無関係なリファクタリング・
   「ついで修正」・スタイル変更を混ぜない。範囲外で見つけた問題は直さず報告に記す
   （評価役が別タスク化を判断する）。
4. 自己検証: 完了と宣言する前に、成果を受け入れ条件と 1 項目ずつ突き合わせる。
   コード変更ならテスト・リンタ・型チェックを実行できる環境なら実行し、結果を報告に含める。
   実行できない場合はその旨と、代わりに行った確認（読み合わせ・トレース等）を記す。
   トークン・パスワード等の機密情報を成果物に含めない。
5. 報告契約: 成果本文に (a) 成果そのもの／サマリー (b) 検証内容と結果
   (c) 採用した前提・未解決事項・範囲外で見つけた問題 を含める。
   後続タスクと検証役が成果だけで判断できる自己完結した報告にする。"""

LIGHT_DISCIPLINE = """\
【実行規律】
- 入力（依存タスクの成果）を鵜呑みにしない。明らかな矛盾・重複・欠落に気づいたら、
  結論に反映したうえでその旨を明記する。
- 判断には根拠を添える。恣意的に見える選別・統合は後段の検証で差し戻される。
- 出力契約（形式）を厳守する。後段のタスクはこの形式を前提に機械処理する。"""

# --------------------------------------------------------------------------
# 検証規律 — gitlab-idd レビュー手順（受け入れ条件評価・判定基準）の蒸留
# --------------------------------------------------------------------------
VERIFY_DISCIPLINE = """\
【検証規律 — 独立検算】
1. 独立に再導出する: ワーカーの結論をなぞらず、可能な範囲で自分で結果を導き直して
   突き合わせる。ワークスペースがある場合は実物（ファイル・diff）を確認し、
   テスト・リンタ・型チェックを実行できるなら実行して結果を判定に使う。
2. チェック観点（最低限すべて確認する）:
   (1) タスクの目標・受け入れ条件の全項目が満たされているか
   (2) 件数・合計など集計値の整合
   (3) 抜け漏れ・重複
   (4) 各要素の妥当性の抜き取り検査（全件が無理でも代表を必ず検査する）
   (5) スコープ外の変更・無関係な差分が混入していないか
3. 判定規律: 要求不充足・誤り・破壊的変更などの重大な問題のみ fail とする。
   好み・軽微な改善提案は fail 理由にせず、issues に「(minor)」を付けて残す
   （minor のみなら pass としてよい）。
4. 指摘の粒度: issues の各項目は、再作業者がそのまま着手できるように
   「どこで・何が・どう直すべきか」まで書く。「品質が低い」のような抽象的指摘は書かない。"""

VERIFY_CONTRACT = """\
問題が無ければ『verify=pass』、あれば『verify=fail』と具体的な該当箇所を出力し、
末尾に JSON {"ok": true|false, "issues": ["..."]} を必ず添える。"""

# --------------------------------------------------------------------------
# 評価規律 — gitlab-idd requester-review（受け入れ評価・差し戻し・スコープ外起票）の蒸留
# --------------------------------------------------------------------------
EVAL_DISCIPLINE = """\
【評価規律】
1. 人からの指摘があれば最優先で反映する（新タスク追加、または未着手の待機ノードの差し替え）。
2. 受け入れ評価: 元の要求を受け入れ条件として読み、現在の結果と 1 項目ずつ突き合わせる。
   判定基準 — (a) 要求の機能・内容が満たされている (b) verify がある場合その結果が pass
   (c) 要求範囲外の余計な成果が混ざっていない。1 つでも欠けるなら replan。
3. 差し戻しの具体化: 作り直しタスクの goal には「何が・どこで・どう不足していて・どう直すか」を
   具体的に織り込む（verify の issues や人の指摘をそのまま転記してよい）。
   「もう一度やり直す」のような抽象的な goal を作らない。
4. タスクの膨張禁止: new_tasks に積むのは元の要求の達成に必要なものだけ。
   結果から見つかった改善アイデアは、要求達成に必須でなければ reason に記すに留める。
5. 打ち切り: 同じ完了条件のために作り直しを繰り返しても改善しない場合
   （達成不可能な条件など）は、無理に再タスクを足さず "done" を返す。"""

EVAL_CONTRACT = """\
出力は JSON のみ: {"decision":"done"|"replan","reason":"...",\
"new_tasks":[{"id":"...","goal":"...","deps":[],"kind":"work","replaces":"<任意: 差し替える待機ノード id>"}]}
既存 id と重複しない id を使うこと。done のとき new_tasks は空配列。"""


def _trim(text, limit: int) -> str:
    s = str(text or "")
    return s if len(s) <= limit else s[:limit] + "…"


def _format_deps(deps: dict) -> str:
    """依存成果ブロック。kiro-flow 組み込みプロンプトと同じ形式（data は 400 字まで）。"""
    lines = []
    for d, r in (deps or {}).items():
        r = r if isinstance(r, dict) else {"output": r}
        line = f"[{d}] {r.get('output', '')}"
        if r.get("data") is not None:
            line += f"\n  data: {json.dumps(r['data'], ensure_ascii=False, default=str)[:400]}"
        lines.append(line)
    return "\n".join(lines)


def build_worker_prompt(p: dict) -> str:
    """worker/verify の各 kind 向けプロンプト。"""
    kind = str(p.get("kind") or "work")
    role = ROLE_LINES.get(kind, ROLE_LINES["work"])
    parts = [f"あなたは分散 Dynamic Workflow の{role}",
             f"タスク({kind}): {p.get('goal', '')}"]
    if p.get("request"):
        parts.append("【全体文脈】この run の元要求（担当は上記タスクのみ。全体を一人でやり直さない）: "
                     + _trim(p["request"], 400))
    if p.get("repo_instruction"):   # ワークスペース＋参照リポジトリの作業指示（kiro-flow が生成）
        parts.append(str(p["repo_instruction"]))
    if p.get("artifact_note"):      # 中間成果物のファイル受け渡しプロトコル（kiro-flow が生成）
        parts.append(str(p["artifact_note"]))
    if kind == "verify":
        parts.append(VERIFY_DISCIPLINE)
    elif kind in EXEC_KINDS:
        parts.append(EXEC_DISCIPLINE)
    else:
        parts.append(LIGHT_DISCIPLINE)
    deps = _format_deps(p.get("deps") or {})
    if deps:
        parts.append("依存タスクの成果:\n" + deps)
    if kind == "verify":
        parts.append("【出力契約】" + VERIFY_CONTRACT)
    else:
        parts.append("【出力契約】成果物を簡潔に直接出力してください。前置き・作業過程の逐語は書かない。")
    return "\n\n".join(parts)


def build_evaluator_prompt(p: dict) -> str:
    """continue（evaluator-optimizer）向けプロンプト。decision JSON 契約は kiro-flow と同一。"""
    max_retries = int(p.get("max_retries") or 3)
    parts = [
        "あなたは分散 Dynamic Workflow の評価役です。ワークフローパターンを踏まえ、"
        "現在の結果が要求を満たすか判定し、必要なら次のタスクを追加してください"
        "（例: 分類結果に応じた専門タスク、検証 fail の作り直し、統合や追加候補の生成）。"
        "未着手の待機ノードは replaces で差し替えられます"
        "（実行中のノードは触らない＝評価は run が静止したときだけ行われます）。",
        EVAL_DISCIPLINE + f"\n   同一タスクの作り直しは最大 {max_retries} 回まで。",
    ]
    if p.get("patterns_catalog"):
        parts.append("パターン:\n" + str(p["patterns_catalog"]))
    parts.append(EVAL_CONTRACT)
    parts.append(f"元の要求: {p.get('request', '')}")
    if p.get("human_feedback"):
        parts.append("人からの指摘（最優先で反映すること）:\n" + str(p["human_feedback"]))
    parts.append("現在の結果:\n" + str(p.get("results_summary", "")))
    return "\n\n".join(parts)


def build(payload: dict) -> str:
    role = str(payload.get("role") or "worker")
    if role == "evaluator":
        return build_evaluator_prompt(payload)
    return build_worker_prompt(payload)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
        sys.stdout.write(build(payload))
        return 0
    except Exception as e:  # noqa: BLE001 — 失敗は非ゼロ終了（kiro-flow が組み込みへフォールバック）
        print(f"flow-worker prompt error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
