#!/usr/bin/env python3
"""backlog-planner — charter をレビュー可能な粒度のバックログへ分解するプロンプトを組み立てる。

LLM は呼ばない。**プロンプトを組み立てるだけ**で、実行・予算管理・失敗トリアージは
agent-project 側が持つ（backlog-verifier と同じ形）。

Usage:
    echo '<入力 JSON>' | python3 prompt.py
    → プロンプト本文を stdout に出力

入出力契約は ../SKILL.md を参照。
"""
from __future__ import annotations

import json
import sys

# 分解の粒度（設定 granularity）。agent-project の PLAN_GRANULARITY_DIRECTIVES と語彙を揃える。
_GRANULARITY = {
    "coarse": ("各タスクはユーザーストーリー相当の**意味のある成果のかたまり**にすること"
               "（独立に着手でき、単体で価値ある成果になり、独立に検証できる = INVEST）。"
               "1 ファイル・1 関数・単一の実装手順のレベルまでは刻まない。"
               "目安はプロジェクト全体で 3〜10 件。"),
    "fine": "各タスクは単機能・単モジュール程度の変更セットにすること（目安 8〜20 件）。",
    "finest": ("各タスクは機械的に検証できる最小単位（1 ファイル/1 関数/1 観点）まで"
               "原子的に分解すること。"),
}


def _block(title: str, body: str) -> str:
    body = (body or "").strip()
    return f"\n## {title}\n{body}\n" if body else ""


def _existing_block(existing: "list[dict]") -> str:
    """既存タスク一覧。**人が確定させたもの（edited=human）を明示する**——プランナーが
    それを「まだ無い」と思って作り直すと、人の編集が毎回上書き提案で埋もれる。"""
    lines = []
    for t in existing[:200]:
        if not isinstance(t, dict):
            continue
        title = str(t.get("title") or "").strip()
        if not title:
            continue
        marks = [str(t.get("status") or "").strip() or "?"]
        if str(t.get("edited") or "") == "human":
            marks.append("**人が確定済み・作り直さない**")
        line = f"- {title}（{' / '.join(marks)}）"
        summary = str(t.get("summary") or "").strip()
        if summary:
            line += f" — {summary[:120]}"
        lines.append(line)
    return "\n".join(lines)


def _tombstone_block(tombstones: "list[dict]") -> str:
    lines = []
    for t in tombstones[:100]:
        if not isinstance(t, dict):
            continue
        title = str(t.get("title") or "").strip()
        if not title:
            continue
        reason = str(t.get("reason") or "").strip()
        lines.append(f"- {title}" + (f" — 却下理由: {reason[:160]}" if reason else ""))
    return "\n".join(lines)


def build_prompt(spec: dict) -> str:
    gran = str(spec.get("granularity") or "coarse").lower()
    gran_directive = _GRANULARITY.get(gran, _GRANULARITY["coarse"])
    existing = [t for t in (spec.get("existing") or []) if isinstance(t, dict)]
    tombstones = [t for t in (spec.get("tombstones") or []) if isinstance(t, dict)]
    notes = str(spec.get("notes") or "").strip()
    retry = str(spec.get("retry") or "").strip()

    head = (
        "あなたはプロジェクトを実行可能なタスクへ分解するプランナーです。"
        "以下の憲章を、**それぞれ独立に検証できるタスク**へ分解してください。\n\n"
        "このバックログは、タスクグラフを作る前に**人がレビューして直します**。"
        "人が「これは要る/要らない」「ここは違う」を判断できるだけの材料を、"
        "タスク自身に書いてください。判断できない記述は、承認するしかなくなり"
        "レビューが形骸化します。\n\n"
        f"分解の粒度: {gran_directive}\n"
    )

    body = _block("憲章", str(spec.get("charter") or ""))
    body += _block("書込先の担当範囲（owns）", str(spec.get("owns") or ""))
    body += _block("リポジトリの文脈（作業概要の「変更対象」はこれを根拠に書く）",
                   str(spec.get("repo_context") or ""))
    body += _block("プロジェクトの恒常ルール", str(spec.get("rules") or ""))
    body += _block("既存タスク（**これらと重複する項目は出力しない**）", _existing_block(existing))
    body += _block("却下・削除済み（墓標。**同じものを再提案しない**。"
                   "違う切り口なら出してよいが、なぜ違うのかを why に書くこと）",
                   _tombstone_block(tombstones))
    body += _block("観点メモ（人が書き溜めたもの。ここからもタスクを起こす）", notes)
    if retry:
        body += _block("⚠ 前回の出力に不足がありました（**必須項目をすべて埋めて出し直してください**）",
                       retry)

    tail = (
        "\n## 出力\n"
        "**JSON 配列のみ**を出力してください（前置き・後書き・コードフェンス外の説明は不要）。\n\n"
        "各要素の必須項目:\n"
        '- `"title"`: タスクの題\n'
        '- `"why"`: このタスクが憲章のどの目標に効くか（1〜2 文）\n'
        '- `"desc"`: **作業概要**。① 変更対象（リポジトリと主要ファイル/モジュールの見込み）'
        "② 作業ステップの概略（3〜7 行）③ 影響範囲。憶測で書かず、上のリポジトリ文脈に"
        "根拠が無いなら「要調査」と明記すること\n"
        '- `"acceptance"`: **受入基準の配列**（自然文 3〜7 項目）。'
        "「〜が動作する」「〜のテストが通る」「〜を壊していない」のように、"
        "**検証エージェントが実際にコマンドを実行して確かめられる**粒度で書くこと。"
        "検証エージェントはこれを 1 項目ずつ証跡付きで判定し、全 pass のみが完了の根拠になります\n"
        '- `"size"`: `"S"` / `"M"` / `"L"`（規模感）\n'
        '- `"workspace"`: 唯一の書込先リポジトリ名（**その基準が触るパスの owns を持つ repo**）\n\n'
        "任意の項目:\n"
        '- `"refs"`: 読むだけの参照リポジトリ名の配列（書込先にはしない）\n'
        '- `"scope"` / `"out_of_scope"` / `"hints"`: 触ってよい範囲 / やらないこと / 実装の手がかり\n'
        '- `"after"`: 先行タスクの `title` の配列（この配列内のタスクのみ。循環は不可）\n'
        '- `"verify"`: 終了コード 0 を PASS とみなすシェルコマンド。**書けるときだけ**書く'
        "（『履歴』でなく『望む最終状態/差分』を見ること）。書けないなら省いてよい"
        "——受入基準があるので、無理に曖昧なコマンドを作らないでください\n"
        '- `"cohort_items"`: 同じ手順を多数の対象に繰り返すタスクは 1 件ずつ列挙せず、'
        '`"…{item}…"` の 1 件にまとめて対象一覧をここに置く\n\n'
        "例:\n"
        '```json\n'
        '[{"title": "CLI チャットの起動先を選べるようにする",\n'
        '  "why": "憲章の「どのリポジトリでも即座に作業を始められる」に直接効く",\n'
        '  "desc": "変更対象: agent-dashboard（renderer/sections/chat.js, main/nodeRepos.js）⏎ '
        '手順: ①候補の解決関数を足す ②起動ボタンにドロップダウンを付ける ③解決不能を非活性表示 ⏎ '
        '影響範囲: 起動経路のみ。既存のセッション名規約は変えない",\n'
        '  "acceptance": ["起動先ドロップダウンに宣言済みリポジトリが並ぶ",\n'
        '                 "宣言が無いリポジトリは非活性で理由付きで表示される",\n'
        '                 "既存の tmux セッション名の付け方を壊していない"],\n'
        '  "size": "M", "workspace": "agent-dashboard"}]\n'
        '```\n'
    )
    return head + body + tail


def main() -> int:
    try:
        spec = json.load(sys.stdin)
    except ValueError as e:
        print(f"入力 JSON を読めません: {e}", file=sys.stderr)
        return 2
    if not isinstance(spec, dict):
        print("入力 JSON はオブジェクトである必要があります", file=sys.stderr)
        return 2
    sys.stdout.write(build_prompt(spec))
    return 0


if __name__ == "__main__":
    sys.exit(main())
