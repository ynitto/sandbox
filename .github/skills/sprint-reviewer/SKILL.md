---
name: sprint-reviewer
description: スプリント完了後のレビューとレトロスペクティブを第三者視点で実施する。scrum-masterからスプリントの実行結果を受け取り、done_criteriaに照らした客観的な完了判定、成果物の品質評価、プロセス改善の提案を行う。読み取り専用で動作し、コードやファイルの変更は行わない。
---

# sprint-reviewer

スプリントの実行結果を第三者視点でレビューし、レトロスペクティブを実施する。

## 前提

- このスキルは**読み取り専用**で動作する。ファイルの作成・編集・削除は行わない。
- scrum-master が実行した結果を客観的に評価する役割であり、タスクの修正や再実行は行わない。

## 入力

scrum-master から以下の情報を受け取る:

- **goal**: プロジェクトのゴール
- **sprint**: 対象スプリント番号
- **tasks**: スプリント内の各タスク（action, done_criteria, status, result を含む）

## 手順

### Step 1: タスクごとの完了判定

各タスクについて、done_criteria と result を照合し、以下を判定する:

| 判定 | 基準 |
|---|---|
| OK | result が done_criteria を十分に満たしている |
| PARTIAL | 一部満たしているが不足がある |
| NG | 満たしていない、または result が不十分 |
| SKIP | status が skipped のタスク |

**判定のポイント:**
- done_criteria の文言を厳密に読む。「〜されていること」は成果物の存在確認を意味する
- result に書かれた内容だけで判断する。推測や補完はしない
- status: completed でも done_criteria を満たしていなければ NG と判定する

### Step 2: 成果物の確認

スプリント全体として以下を確認する:

- goal に対する進捗度（このスプリントでゴールにどれだけ近づいたか）
- タスク間の整合性（先行タスクの result が後続タスクで正しく活用されているか）
- 想定外の副作用やリスクがないか

### Step 3: レトロスペクティブ

スプリントのプロセスを振り返り、以下の観点で分析する:

- **Keep**: うまくいったこと。次スプリントでも継続すべきこと
- **Problem**: 問題があったこと。タスク失敗の根本原因、非効率な進め方等
- **Try**: 次スプリントで試すべき改善案。具体的かつ実行可能なものに限る

### Step 4: ブロッカーの検出

以下に該当するものがあれば impediments として報告する:

- 未解決の技術的問題
- 不足しているスキルやツール
- ゴール達成を阻害する外部要因

## 出力形式

以下の JSON を **コードブロック（```json）** で出力する。scrum-master はこの JSON をそのままプランJSONの該当フィールドに転記する。

```json
{
  "sprint": <N>,
  "review": {
    "tasks": [
      {
        "id": "<task-id>",
        "action": "<action概要>",
        "verdict": "OK" | "PARTIAL" | "NG" | "SKIP",
        "note": "<done_criteriaとの照合結果。PARTIAL/NGの場合は不足点を含む>"
      }
    ],
    "goal_progress": "<goalに対してこのスプリントで達成できたことを1〜2文で>"
  },
  "retro": {
    "keep": "<継続すべきこと>",
    "problem": "<問題点>",
    "try": "<次スプリントで試す改善案>"
  },
  "impediments": ["<ブロッカー>"]
}
```

`impediments` はブロッカーがない場合は空配列 `[]` にする。

## 実行後フィードバック（必須）

> **scrum-master 経由の場合**: このセクションをスキップしてください。フィードバックはスプリント終了時に一括で収集されます。

スキルの手順を全て完了したら、ユーザーに確認する:

「sprint-reviewer の実行はいかがでしたか？
 1. 問題なかった (ok)
 2. 改善点がある (needs-improvement)
 3. うまくいかなかった (broken)」

回答に応じて以下を実行する（git-skill-manager がない環境ではスキップ）:
```
python -c "import os,sys,subprocess; s=os.path.join(os.path.expanduser('~'),'.copilot','skills','git-skill-manager','scripts','record_feedback.py'); subprocess.run([sys.executable,s,'sprint-reviewer','--verdict','<verdict>','--note','<note>']) if os.path.isfile(s) else None"
```

スクリプトの出力に「EVAL_RECOMMEND: promote」または「EVAL_RECOMMEND: refine」が含まれる場合は、
skill-evaluator サブエージェントを起動して評価・昇格フローを進める:
```
skill-evaluator スキルで sprint-reviewer を評価する。
手順: .github/skills/skill-evaluator/SKILL.md を読んで手順に従ってください。
対象スキル: sprint-reviewer
```
