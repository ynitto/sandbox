#!/usr/bin/env python3
"""backlog-verifier — 受入基準の証跡付き検証プロンプトを組み立てる。

LLM は呼ばない。**プロンプトを組み立てるだけ**で、実行・予算管理・失敗トリアージは
agent-project 側が持つ（flow-planner と違い検証はコスト管理の対象なので、実行を
スキルへ出すと 1 settle = LLM 1 回という予算の前提が崩れる）。

Usage:
    echo '<入力 JSON>' | python3 prompt.py
    → プロンプト本文を stdout に出力

入出力契約は ../SKILL.md を参照。
"""
from __future__ import annotations

import json
import sys

# 副作用の許容範囲（設定 verify_side_effects）。DB・外部サービスへの **書き込み**は
# どちらでも禁止する: 検証が失敗すると何が壊れたか分からなくなるうえ、リトライで
# 何度も走るので副作用が累積する。そこまで要る検証は人が verify: に明示的に書く。
#
# **正典は呼び出し側（agent-project の `VERIFY_SIDE_EFFECT_RULES`）**で、入力 JSON に
# `side_effects_text`（解決済みの文）があればそちらを使う。この表は、入力にその文が
# 無いとき（スキルを単体で使う・呼び出し側が古い）の受け皿として残す——同じ文言を
# 2 か所で育てると、経路によって安全制約が変わる。
_SIDE_EFFECTS = {
    "workspace": (
        "作業ツリーの中だけで完結させてください。ビルド・テスト・grep・ローカル起動は可。"
        "ネットワーク到達（HTTP 取得・外部 API）と、作業ツリー外への書き込みはしないでください。"
        "それが必要な基準は verdict=unverifiable として理由を書いてください。"
    ),
    "network": (
        "作業ツリーの中の変更と、読み取りのためのネットワーク到達（HTTP 取得・疎通確認）まで可。"
        "DB や外部サービスへの **書き込み** はしないでください"
        "（失敗時に何が壊れたか分からなくなり、リトライで副作用が累積します）。"
        "それが必要な基準は verdict=unverifiable として理由を書いてください。"
    ),
}

# 差分の常設基準（red-green の代替）。act 前ツリーで別実行する代わりに、
# 「変更が無い / 無関係な場所にしかない」を検証エージェント自身に fail と言わせる。
DIFF_CRITERION = "このタスクの差分が、上の基準の対象範囲に実在すること（変更が無い・無関係な場所にしか無いなら fail）"


def _block(title: str, body: str) -> str:
    body = (body or "").strip()
    return f"\n## {title}\n{body}\n" if body else ""


def build_prompt(spec: dict) -> str:
    task = spec.get("task") or {}
    acceptance = [str(a).strip() for a in (spec.get("acceptance") or []) if str(a).strip()]
    ws = spec.get("workspace") or {}
    side = str(spec.get("side_effects_text") or "").strip() \
        or _SIDE_EFFECTS.get(str(spec.get("side_effects") or "workspace"),
                             _SIDE_EFFECTS["workspace"])
    criteria = acceptance + [DIFF_CRITERION]
    numbered = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(criteria))

    recipes = spec.get("recipes") or []
    recipe_block = "\n".join(f"- `{r}`" for r in recipes[:10])

    head = (
        "あなたは成果物の検証エージェントです。下の受入基準それぞれについて、"
        "**実際にコマンドを実行して**充足を確かめ、証跡付きで判定してください。\n\n"
        "重要な原則:\n"
        "- 判定の根拠は **実行した結果**です。コードを読んだ印象や「妥当に見える」は根拠になりません。\n"
        "- 確かめられなかった基準は正直に fail か unverifiable にしてください。"
        "pass に倒すと、壊れた成果がそのまま done になります。\n"
        "- **成果物を直さないでください。** 直して pass にすると検証と実装の境界が消えます"
        "（作業ツリーへの変更は破棄されます）。\n"
        f"- {side}\n"
    )

    body = (
        f"\n## タスク\n"
        f"- id: {task.get('id', '')}\n"
        f"- title: {task.get('title', '')}\n"
        + (f"- why: {task['why']}\n" if task.get("why") else "")
        + (f"- 作業概要: {task['desc']}\n" if task.get("desc") else "")
        + (f"- 変更してよい範囲: {task['scope']}\n" if task.get("scope") else "")
        + (f"- やらないこと: {task['out_of_scope']}\n" if task.get("out_of_scope") else "")
        + f"\n## 検証する場所\n"
        f"- リポジトリ: {ws.get('url', '(ワークスペース)')}\n"
        f"- 成果ブランチ: {ws.get('branch', '')}（比較元: {ws.get('base', '')}）\n"
        + (f"- 対象パス: {ws['path']}\n" if ws.get("path") else "")
        + f"\n## 受入基準（この順に判定する）\n{numbered}\n"
    )

    extras = (
        _block("参考: 過去に有効だった検証コマンド（まずこれを試す。環境が違えば通らないので鵜呑みにしない）",
               recipe_block)
        + _block("前回の失敗", str(spec.get("feedback") or ""))
        + _block("リポジトリの文脈", str(spec.get("repo_context") or ""))
        + _block("プロジェクトの恒常ルール", str(spec.get("rules") or ""))
    )

    tail = (
        "\n## 出力\n"
        "まず人が読むための本文を Markdown で書いてください（基準ごとに、何を実行して何が"
        "分かったか）。**そのあと、末尾に次の形の JSON を必ず 1 つ添えてください。**\n\n"
        '```json\n'
        '{"criteria": [\n'
        '  {"id": 1, "verdict": "pass|fail|unverifiable",\n'
        '   "evidence": {"commands": ["実行したコマンド"], "output": "出力の要約",\n'
        '                "files": ["参照したファイル:行"]},\n'
        '   "note": "判定の理由（1〜2 文）"}\n'
        ']}\n'
        '```\n\n'
        f"- `criteria` は上の基準と同じ順で {len(criteria)} 件すべて含めてください。\n"
        "- `verdict=pass` には **必ず** `commands` か `files` の証跡を入れてください"
        "（証跡の無い pass は機械的に fail へ落とされます）。\n"
        "- 環境にツールが無い等で確かめられない基準は `unverifiable` にし、"
        "`note` に何が足りないかを書いてください（リトライは消費されません）。\n"
    )
    return head + body + extras + tail


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
