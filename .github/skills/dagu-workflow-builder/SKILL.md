---
name: dagu-workflow-builder
description: この環境に入っているスキルのスクリプトをつなぎ、LLM を使わずに Dagu のワークフロー（DAG）を組み立てる。「Dagu のワークフローを作って」「スキルのスクリプトをつないで定期実行にして」「この入力からこの成果物まで自動で流す DAG を作って」などで発動する。
metadata:
  version: 0.1.0
  tier: experimental
  category: orchestration
  tags:
    - dagu
    - workflow
    - dag
    - script-chain
    - deterministic
---

# dagu-workflow-builder

インストール済みのスキルが持つスクリプトを「型」でつなぎ、Dagu v2 の DAG を書き出す。
つなぎ方は `scripts/chain.py` が決定的に探す（LLM を呼ばない）。同じスキル・同じ入力なら、
どの環境でも同じ DAG ができる。

## パス解決

この SKILL.md が置かれているディレクトリを `SKILL_DIR` とする。

```bash
CHAIN="python3 ${SKILL_DIR}/scripts/chain.py"
```

## 仕組み

- 各スキルは、スクリプトが受け取るもの・作るものを `chain.json`（スキル直下）で宣言する。
  書き方は [references/contract.md](references/contract.md)。
- `chain.py` は各エージェントのスキル置き場（`~/.copilot/skills`・`~/.claude/skills`・`~/.codex/skills`・
  `~/.kiro/skills`・`~/.agents/skills`・`~/.config/opencode/skills` の順）を見て宣言を集める。
  同じ名前のスキルは先に見つかった方を使う。`--skills-dir` か環境変数 `DAGU_CHAIN_SKILLS` で置き場を差し替えられる。
- 手元の入力の型（`--from`）から目的の型（`--to`）まで、ステップの延べ数が最小になる経路を選ぶ。
  同じ数なら「スキル名/アクション名」の順で前のものを使う。

## 手順

1. **何が作れるかを見る**

   ```bash
   $CHAIN scan
   ```

   作りたい成果物の型が「作れる型」に無ければ、そこで止めて利用者に伝える（推測で DAG を書かない）。
   スクリプトはあるのに `chain.json` が無いスキルは、利用者の了承を得て宣言を足す（手順 5）。

2. **チェーンを確かめる**

   ```bash
   $CHAIN plan --from openapi=./openapi.yaml --to openapi-report
   ```

   - 入力のパスを省く（`--from openapi`）と、実行時に渡す引数になる。
   - 複数の目的は `--to` を重ねる。独立したステップは Dagu が並列に流す。
   - 選ばれた経路が意図と違うときは `--avoid <スキル>` か `--avoid <スキル>/<アクション>` で外す。
   - 組めないときは、足りない型と、それを作れるアクションの有無が出る。その内容を利用者に伝える。

3. **DAG を書き出す**

   ```bash
   $CHAIN build --from openapi=./openapi.yaml --to openapi-report \
     -o ~/.config/dagu/dags/openapi-check.yaml
   ```

   - アクションのパラメータは `--param 名前=値`（1 つのアクションに絞るなら `--param スキル/アクション.名前=値`）。
     既定値の無いパラメータは、実行時に渡す引数になる。
   - 定期実行にするなら、書き出した YAML の先頭に `schedule:` を足す（生成物を直すのはそれだけにし、
     つなぎ方を変えたいときは作り直す）。

4. **実行して確かめる**

   ```bash
   dagu validate ~/.config/dagu/dags/openapi-check.yaml
   dagu start ~/.config/dagu/dags/openapi-check.yaml -- IN_OPENAPI=/abs/path/openapi.yaml
   ```

   成果物は実行ごとの作業フォルダ（`DAG_RUN_WORK_DIR`）にできる。どのファイルがどの型かは
   YAML の先頭のコメントに書いてある。

5. **宣言を足す（必要なときだけ）**

   スキルのスクリプトを読み、[references/contract.md](references/contract.md) の形で `chain.json` を書いて検査する。

   ```bash
   $CHAIN check <スキルのディレクトリ>
   ```

   型の名前は既存の宣言（`scan` の「作れる型」）にあるものを優先して使う。同じものに別の名前を付けると
   チェーンがつながらない。

## しないこと

- DAG のステップを手で書き足して、宣言に無いスクリプトをつながない（他の環境で同じ DAG が作れなくなる）。
- スクリプトの出力を LLM で整形して次へ渡すステップを入れない。型が合わないなら、変換するアクションを宣言する。
