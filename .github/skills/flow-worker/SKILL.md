---
name: flow-worker
description: agent-flow の executor=agent 向け実行系プロンプト強化スキル。worker（全 kind）・verify・evaluator の各 LLM 呼び出しへ、flow-worker の実行規律 —「三つの約束」（前提を書く・範囲を守る・検証してから渡す）・再導出検証・受け入れ評価 — と、git 操作を worktree に限定する git 利用規約を織り込んだプロンプトを供給する。flow-planner と対をなし、agent-flow が自動検出して利用する（ユーザーが直接発動するスキルではない）。
metadata:
  version: 2.0.0
  tier: experimental
  category: orchestration
  tags:
    - agent-flow
    - dynamic-workflow
    - worker
    - verify
    - evaluator
    - git-worktree
---

# flow-worker — agent-flow 向け実行系プロンプト強化

## 概要

agent-flow の `executor: agent` は、各ノードの実行（worker/verify の各 kind）と
継続判断（evaluator）を 1 回ずつのエージェント CLI 呼び出しで行う。
本スキルはその呼び出しに渡す **プロンプトを賢くする**。planner を flow-planner
スキルへ外出ししたのと同じ作戦で、実行系の手順知識をスキル側に持たせる。

規律の核は 3 つだけ:

| 役割 | 規律 | 一言で |
|------|------|--------|
| worker（work/generate/map） | **三つの約束** | 前提を書く・範囲を守る・検証してから渡す |
| verify | **再導出検証** | 結論をなぞらず自分で導き直して突き合わせる |
| evaluator | **受け入れ・具体化・打ち切り** | 完了条件と突き合わせ、差し戻しは具体的に、膨張させない |

集約・選別系 kind（classify/synthesize/filter/judge/reduce/split）は
出力契約の厳守＋「入力を鵜呑みにしない・根拠を添える」の軽量規律のみ。

## アーキテクチャ

```
agent-flow (executor=agent)
  ├─ execute_kiro(kind, goal, deps, …)      … ノード実行
  │     └─ flow-worker/scripts/prompt.py    … role=worker のプロンプト生成（決定的・LLM 無し）
  │           └─ run_kiro(prompt, purpose=kind)   … LLM 呼び出しは agent-flow 側
  └─ continue_kiro(request, results, …)     … 継続判断（evaluator-optimizer）
        └─ flow-worker/scripts/prompt.py    … role=evaluator のプロンプト生成
              └─ run_kiro(prompt, purpose="evaluator")
```

- **prompt.py は LLM を呼ばない**。決定的なテンプレート合成のみ（高速・テスト可能）。
- LLM 呼び出し・役割別エージェント解決（設定 `agents:`）・argv スピル・タイムアウトは
  agent-flow 側の `run_kiro` に残る。
- スキルが見つからない／生成に失敗した場合、agent-flow は **組み込みプロンプトへ
  フォールバック** する（分散ワーカーに本スキルが未インストールでも run は止まらない）。

## git 利用規約 — worktree 必須（scripts/git_worktree.py）

エージェントが git を触るときの唯一の入口として、共有キャッシュ + worktree の
CLI（[scripts/git_worktree.py](scripts/git_worktree.py)）を同梱する。実装系・検証役の
プロンプトには常にこの規約が注入され、エージェントの自発的な clone / checkout /
共有チェックアウトへの commit を機械的に封じる。

```bash
# 読み取り: 別リポジトリ・別ブランチの内容が必要なとき
WT=$(python3 scripts/git_worktree.py provision <URL|パス> --ref <ブランチ|SHA>)
# …… $WT 内で参照・作業 ……
python3 scripts/git_worktree.py release "$WT"

# 書き込み: 専用 worktree で commit → detached のまま HEAD:refs/heads/<branch> へ push
python3 scripts/git_worktree.py push "$WT" --branch <専用ブランチ> -m "<メッセージ>"
```

- キャッシュ root は agent-flow / agent-project と共有
  （`KIRO_GIT_CACHE_DIR` / 既定 `$TMPDIR/kiro-git-cache`）。同じ URL のミラーは
  ホスト内で 1 本になり、worktree の生成にネットワーク通信は発生しない。
- push は detached worktree から `HEAD:refs/heads/<branch>` で送るため、
  共有チェックアウトのブランチを動かさない。reject は fetch + rebase で自動リトライし、
  並行コミットと衝突しない。
- パターンの正典は
  [docs/designs/git-worktree-cache-pattern.md](../../../docs/designs/git-worktree-cache-pattern.md)
  （INV-1 鮮度 / INV-2 直列化・自己修復 / INV-3 direct clone フォールバック）。

## 利用方法

agent-flow が自動検出する（flow-planner と同じ検索順:
`.github/skills/flow-worker/` → git root → `~/.kiro/skills/` → skill-registry.json の
`skill_home`）。無効化する場合は agent-flow 設定で:

```yaml
# agent-flow.yaml
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

出力（stdout）: プロンプト全文。**出力契約の文言は agent-flow のパーサと互換を保つ**:

- verify: 『verify=pass / verify=fail』＋末尾 JSON `{"ok": bool, "issues": [...]}`
- split: JSON 配列のみ / reduce: `count` は実要素数と一致 / classify: `class=<ラベル>`
- evaluator: JSON のみ `{"decision": "done"|"replan", "reason", "new_tasks": [...]}`

## 注意事項

- プロンプトの正文は `scripts/prompt.py` の定数が正。
- 分散実行では各ワーカーノードのローカルでスキルが検索される。未インストールの
  ノードは組み込みプロンプトで動く（混在しても run は成立する）。
- agent-project には依存しない。agent-flow がインターフェースに渡す情報のみを使う。
