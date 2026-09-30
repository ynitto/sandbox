# codd — 実装と設計書の一貫性を保って変えるエージェント

このリポジトリでコードや文書を変える依頼は、**必ず codd のステートマシン（`.statemachine/codd/workflow.yaml`）で進めます。**
ステートマシンの外でファイルを変えたり、検査を飛ばしたりしません。読むだけの質問（どこに何があるか、など）には、
ステートマシンを使わずに答えてかまいません。変える必要があるとわかった時点で、ステートマシンを始めます。

## 始める前に

```bash
python3 .statemachine/codd/codd.py show
```

参照先（設計書・実装）、守る決まり、使うスキルと道具、1 回で変えるファイルの上限が出ます。
スキルは、エージェントに登録されていなくても使います。挙がった `SKILL.md`（設定したものと、`.agents/skills/` などに
置かれたもの）を読み、関係するものはその手順に従ってください。

## 回し方

`statemachine-use` スキルがあれば、それで `.statemachine/codd/workflow.yaml` を実行します（依頼は入力の `request`）。
無ければ、次のとおり自分で回します。

1. `workflow.yaml` を読む。依頼を `request` に、`context:` の値を初期値にする。`initial_state` から始める
2. ステートごとに `action_file` を読み、`{{名前}}` を今の値に置き換えて、その指示どおりに実行する。
   出力の第 1 行は `output_validator` の語で始める。`output_key` があれば、出力をその名前で控える
3. `check` があれば、アクションのあとにそのコマンドを実行する。終了コード 0 なら `check_ok` は `true`。
   落ちたら、出力を踏まえて同じステートを `check_retries` 回までやり直し、使い切ったら `check_ok` を `false` にして次へ。
   `check_output` は検査の出力の最初の行
4. `transitions` のうち今のステートから出るものを `priority` の小さい順に見て、最初に合う `condition_rule` の先へ進む
   （`equals:名前:値` は一致、`startswith:名前:値` は前方一致）
5. `confirm`（計画の確認）と `stuck`（止まったときの相談）では、利用者に見せて**答えを待ちます**。推測して先へ進まない
6. `terminal: true` のステートを実行したら終わり

## 守ること

- 検査（`codd.py verify-plan` / `verify-apply`）の結果を自分で覆さない。落ちたら、指摘を直すか、`stuck` で利用者に訊く
- 終わりの報告は `codd.py report` の出力をそのまま使う。自分の記憶でまとめ直さない
- コミットしない（利用者が内容を確かめてからコミットする）
