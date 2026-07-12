---
name: flow-worker
description: kiro-flow の executor=agent 向け実行系プロンプト強化スキル。worker（全 kind）・verify・evaluator の各 LLM 呼び出しに、gitlab-idd 由来の実行規律（解釈確定・影響範囲・スコープ厳守・自己検証・報告契約・独立検算・受け入れ評価）を織り込んだプロンプトを供給する。flow-planner と対をなし、kiro-flow が自動検出して利用する（ユーザーが直接発動するスキルではない）。
metadata:
  version: 1.0.0
  tier: experimental
  category: orchestration
  tags:
    - kiro-flow
    - dynamic-workflow
    - worker
    - verify
    - evaluator
    - prompt-engineering
---

# flow-worker — kiro-flow 向け実行系プロンプト強化

## 概要

kiro-flow の `executor: agent` は、各ノードの実行（worker/verify の各 kind）と
継続判断（evaluator）を 1 回ずつのエージェント CLI 呼び出しで行う。
本スキルはその呼び出しに渡す **プロンプトを賢くする**。planner を flow-planner
スキルへ外出ししたのと同じ作戦で、実行系の手順知識をスキル側に持たせる。

手順の中身は gitlab-idd スキル（worker-role / requester-review /
non-requester-review / project-dod）から **GitLab イシュー・MR 操作を除いて蒸留**
したもの。イシューやラベルの代わりに、kiro-flow がインターフェースで渡す情報
（goal・依存成果・ワークスペース・参照リポジトリ・中間成果物・元要求・
人フィードバック・作り直し上限）を活用する。

## アーキテクチャ

```
kiro-flow (executor=agent)
  ├─ execute_kiro(kind, goal, deps, …)      … ノード実行
  │     └─ flow-worker/scripts/prompt.py    … role=worker のプロンプト生成（決定的・LLM 無し）
  │           └─ run_kiro(prompt, purpose=kind)   … LLM 呼び出しは kiro-flow 側
  └─ continue_kiro(request, results, …)     … 継続判断（evaluator-optimizer）
        └─ flow-worker/scripts/prompt.py    … role=evaluator のプロンプト生成
              └─ run_kiro(prompt, purpose="evaluator")
```

- **prompt.py は LLM を呼ばない**。決定的なテンプレート合成のみ（高速・テスト可能）。
- LLM 呼び出し・役割別エージェント解決（設定 `agents:`）・argv スピル・タイムアウトは
  kiro-flow 側の `run_kiro` に残る。
- スキルが見つからない／生成に失敗した場合、kiro-flow は **組み込みプロンプトへ
  フォールバック** する（分散ワーカーに本スキルが未インストールでも run は止まらない）。

## 利用方法

kiro-flow が自動検出する（flow-planner と同じ検索順:
`.github/skills/flow-worker/` → git root → `~/.kiro/skills/` → skill-registry.json の
`skill_home`）。無効化する場合は kiro-flow 設定で:

```yaml
# kiro-flow.yaml
worker_skill: none        # 既定 flow-worker。none/builtin で組み込みプロンプトに戻す
```

### スクリプト直接呼び出し（デバッグ用）

```bash
echo '{"role":"worker","kind":"work","goal":"READMEに節を追加"}' \
  | python3 .github/skills/flow-worker/scripts/prompt.py
```

## 入出力契約

入力（stdin JSON）:

| role | フィールド |
|------|-----------|
| `worker` | `kind` / `goal` / `request`（run の元要求） / `deps`（`{id: {output, data}}`） / `repo_instruction`（ワークスペース＋参照の指示ブロック） / `artifact_note`（中間成果物プロトコル） / `workspace` / `references` |
| `evaluator` | `request` / `results_summary` / `human_feedback` / `patterns_catalog` / `max_retries` |

出力（stdout）: プロンプト全文。**出力契約の文言は kiro-flow のパーサと互換を保つ**:

- verify: 『verify=pass / verify=fail』＋末尾 JSON `{"ok": bool, "issues": [...]}`
- split: JSON 配列のみ / reduce: `count` は実要素数と一致 / classify: `class=<ラベル>`
- evaluator: JSON のみ `{"decision": "done"|"replan", "reason", "new_tasks": [...]}`

## 役割ごとの規律（gitlab-idd からの蒸留対応表）

| 本スキルの規律 | gitlab-idd の元手順 |
|---------------|-------------------|
| worker: 解釈の確定（推測解釈を前提として明記） | worker-role ステップ 2-7 明確性チェック（人に質問できないため、質問の代わりに前提明記へ変換） |
| worker: 影響範囲の確認 | worker-role ステップ 3-5 スカウトマップ |
| worker: スコープ厳守・範囲外は報告のみ | 行動原則「スコープ厳守」＋ requester-review「スコープ外タスクの起票」（起票判断は evaluator に委譲） |
| worker: 自己検証（テスト・リンタ実行） | worker-role ステップ 4-3〜4-4 レビューループ＋ステップ 5-0 project-dod |
| worker: 報告契約 | worker-role ステップ 5-3 サマリーコメント |
| verify: 独立検算・チェック観点・minor 区別 | non-requester-review ステップ 3 ＋ requester-review 判定基準 |
| evaluator: 受け入れ評価・差し戻し goal 具体化 | requester-review ステップ 3〜4b（リオープン時の指摘具体化） |
| evaluator: タスク膨張禁止・打ち切り | requester-review スコープ外起票の判断基準＋ kiro-flow サーキットブレーカー |

詳細は [references/execution-protocol.md](references/execution-protocol.md) を参照。

## 注意事項

- プロンプトの正文は `scripts/prompt.py` の定数が正。references/ は人間向けの解説。
- 分散実行では各ワーカーノードのローカルでスキルが検索される。未インストールの
  ノードは組み込みプロンプトで動く（混在しても run は成立する）。
- kiro-project には依存しない。kiro-flow がインターフェースに渡す情報のみを使う。
