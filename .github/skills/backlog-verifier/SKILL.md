---
name: backlog-verifier
description: agent-project のタスクを「受入基準チェックリスト × 証跡」で検証する検証エージェント向けスキル。1 行のシェルコマンドの exit 0 を done の唯一の根拠にする方式をやめ、基準ごとに実行時にコマンドを試行錯誤して充足を確かめ、証跡付きの判定レポートを返す。agent-project の settle（verifier）から呼ばれる。
metadata:
  version: 1.0.0
  tier: experimental
  category: verification
  tags:
    - agent-project
    - verification
    - acceptance-criteria
    - evidence
---

# backlog-verifier — 受入基準の証跡付き検証

## 概要

`backlog-planner` が書き人が直したタスクの **受入基準チェックリスト（`acceptance:`）** に対して、
成果ブランチのワークスペース上で充足を確かめ、**基準ごとの判定 + 証跡**を返す。

**なぜコマンドではなく基準と証跡なのか**: 従来は自然文の完了条件から決定的シェルコマンドを
1 回の LLM 呼び出しで合成し、その exit 0 を done の唯一の根拠にしていた。環境差で大半が失敗
して人へ倒れるうえ、昇格したコマンドが「たまたま通る劣化した検証」でも、**人にはそれを
見抜く材料が無い**。人がレビューできるのは基準と証跡であって、コマンドの良し悪しではない。

## 入出力契約

`scripts/prompt.py` は **プロンプトを組み立てるだけ**（LLM は呼ばない）。実行・予算管理・
失敗トリアージは agent-project 側が持つ。

```
echo '<入力 JSON>' | python3 scripts/prompt.py
→ プロンプト本文を stdout に出力
```

入力 JSON:

| キー | 内容 |
|---|---|
| `task` | `{id, title, why, desc, scope, out_of_scope}` |
| `acceptance` | 受入基準の配列（自然文。`backlog-planner` が生成し人が修正したもの） |
| `workspace` | `{url, branch, base, path}`（成果ブランチの所在） |
| `repo_context` | `context/<repo>.md`（repo-map）の抜粋。無ければ空 |
| `rules` | `rules.md`（プロジェクト恒常ルール）の抜粋 |
| `recipes` | 過去に有効だった検証コマンド列（**参考**。決定的ゲートではない） |
| `feedback` | 前回失敗した基準と理由 |
| `side_effects` | `workspace`（既定・作業ツリー内のみ） / `network`（HTTP 到達も許す） |
| `side_effects_text` | 上を解決済みの制約文（任意）。**あればこれを使う**——文言の正典は呼び出し側で、スキル側の表は入力に無いときの受け皿（同じ文言を 2 か所で育てると経路によって安全制約が変わる） |
| `diff_criterion` | 差分の常設基準の文（任意）。**あればこれを使う**——`side_effects_text` と同じ理由。呼び出し側は検証レポートの基準列を同じ文字列から組むので、ここがずれるとレポートの基準文とエージェントが見た基準文が黙って食い違う（判定は番号で突き合わせるため機械は気付かない） |

出力（検証エージェントが返すもの）: Markdown 本文 + 末尾に JSON。

```json
{"criteria": [
  {"id": 1, "verdict": "pass",
   "evidence": {"commands": ["npm test -- needs"], "output": "24 passing", "files": ["src/a.js:12"]},
   "note": ""}
]}
```

`verdict` は `pass` / `fail` / `unverifiable`。

## 不変条件（agent-project 側が機械的に強制する）

1. **フェイルクローズ** — 明示の `pass` が無い基準は `fail`
2. **証跡必須** — `pass` なのに実行コマンドも参照ファイルも無い基準は `fail` に落とす
   （「確認しました」だけで pass にできる穴を塞ぐ）
3. **差分の常設基準** — 「差分が基準の対象範囲に実在すること」が必ず 1 項目入る
   （何も変えずに全 pass を返す道を塞ぐ。red-green の代替）
4. **`unverifiable` はリトライを焼かない** — 環境にツールが無い等は人へ回す
5. **成果物を直さない** — 検証が成果物を「直して」pass にすると、検証と実装の境界が消える

## カスタマイズ

上位のスキル置き場（プロジェクトの `.github/skills/backlog-verifier/`）に同名スキルを置けば
全面的に差し替えられる。プロジェクト固有の検証手順・禁止事項をここに書く。
設定 `verifier_skill` でスキル名自体も変えられる。
