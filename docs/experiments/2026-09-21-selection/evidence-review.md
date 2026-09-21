# 独立適合判定へ渡す証拠のレビュー

> 修正前のコードレビュー。記載した問題の対応状況は実装結果を参照。 [実装結果](implemented-independent.md)・[記録一覧](README.md)。

2026-09-21。コードと既存契約を読んだ改善案。ここに示す数値例はテスト用合成値で、モデル能力の実測ではない。本レビューでは modelselect.py を変更していない。

## 確認できた問題

- `agentcore/modelselect.py::_rating_for` は model または CLI 名が一致する行を集め、用途一致がなければ先頭行を返す。用途別実績が別用途の能力に流用される。`tests/test_modelselect.py::test_ratings_rows_attach_by_purpose_then_model` は planner に review の0.9を使う現状を期待しており、修正時にこの期待値を変更する必要がある。
- CLI名とモデル名を同じ集合へ入れるため、特定モデルの実績よりCLI全体の行が先に選ばれる可能性がある。CLI列がある新しい入力でも、現状は別CLIの同名モデルを排除しない。
- `_criterion_line` は pass_rate と平均トークンだけを表示し、用途、標本数、出典、制約を落としている。`build_state` も候補のモデルが未指定なのか定義既定なのかを区別しない。
- `describe_candidate` の直接指定 rating は無検証でコピーされる。標本0・欠落でも pass_rate があれば `_audit_pick` が実績として順位付けする。

## 既存証拠の意味と境界

`tools/agent-audit/agent_audit/stats.py::aggregate_ratings` は現状 `(purpose, model)` ごとに集計し、CLI列を出していない。`model` 欠落時だけ agent_cli が文字列として使われる。`outcome_runs` は result.status が done/failed の件数で、`pass_rate` は done の割合。独立verificationによる品質合格率ではない。`average_tokens` は別の usage_runs を分母とし、時間換算推定を含み得る。結果件数と消費計測件数を同一視しない。

`schemas/agent-candidate-qualifications.schema.json` は識別子を agent_cli+model と定め、処理種別・制約・標本・出典を保持する。また生 qualifications は管理面専用でエンジンは読まない。`executioncontract._by_purpose_errors` は用途ごとに候補が空なら別用途候補へ流さず park する契約。この変更でも生 qualifications を直接読む経路を追加しない。既存 ratings 引数か、Compiler等の許可された境界で渡された情報だけを判断材料にする。

## 推奨パッチの構造

1. `_rating_for` は先に用途を一致させる。要求purposeが空なら `""` と `(なし)` の行だけを対象にする。要求purposeがある場合、別用途や用途不明へフォールバックしない。
2. 同用途の中で `(agent_cli, model)` 完全一致を最優先にする。明示agent_cliが異なる行は拒否する。次点の旧形式モデル一致行は `identity_match=model-only; agent_cli=unknown` と明示して、CLIとの完全一致を名乗らない。明示モデルがある候補にCLI名だけの格付けを付けない。
3. 重複する同粒度の行が矛盾する場合、先頭を偶然選ばず曖昧な証拠として扱う。出典・制約・観測期間の違いを無条件に合算しない。可能なら複数行を別証拠として保持し、最初の対応では曖昧として実績順位付けから外す。
4. ratingの正規化を直接指定とratings行の両方に適用する。整数の `outcome_runs`（直接指定は `runs`）が1以上かつpass_rateが有限の0..1の場合だけ measured。標本0/欠落/負数/bool/不正値は unmeasured、pass_rateを実績順位付けに使わない。未測定は性能不足を意味しない。
5. `describe_candidate` に `model_source=explicit|definition-default|unspecified` と `model_known` を付ける。未指定なら「実行時CLI既定、具体モデル未特定」とし、有名モデル名や推測能力を補わない。定義default_modelがある場合だけそのモデルを使い出典を明示する。
6. evidence欄に purpose, runs, source, identity_match, constraints, metric, limitationsを保持する。legacy audit は `source=agent-audit ratings (caller supplied)`、`metric=reported node completion rate`、`constraints=not provided` とする。出典のパスや日時は実際に渡された場合だけ記載する。
7. judgement文では「未測定は不足とも十分とも断定しない」「CLI tool-loopは作業機構であり推論能力の実測ではない」「少数件・別用途の成功を一般化しない」を明示する。cost/siteを適合判定の証拠と混ぜず、十分と判断した候補間でだけ費用比較する。
8. `build_state` のallowlistとcandidate独立質問の両方へ同じ正規化済み証拠を渡す。`_criterion_line` だけ改善してstate側へ古い値を残さない。audit fallbackも有効な measured ratingだけを参照する。

## 具体的な回帰テスト案

| ケース | 期待 |
|---|---|
| workerモデル完全一致行、review同モデル行 | workerのみ採用 |
| planner要求、reviewしかない | rating無し/未測定、review値を流用しない |
| purpose空、reviewと `(なし)` | `(なし)` のみ採用 |
| model同名、別CLIの明示行 | 不採用 |
| 旧形式model-only行が先、完全CLI+model一致行が後 | 完全一致優先。順序反転も同じ |
| モデル明示候補にCLI-only行しかない | モデル能力の実績にしない |
| legacy model-onlyが唯一 | 限定的な証拠としてCLI不明を明示 |
| runs=0/missing/-1/True、rate=1 | 未測定。auditが高実績として優先しない |
| runs=4、rate=0 | measuredな観測失敗。未測定と区別 |
| NaN/Inf/範囲外rate | 実績不採用、JSON/質問生成が壊れない |
| 同じ格付け直接ratingとratings経由 | 同じ正規化と未測定判定 |
| 定義defaultあり・明示モデル・両方無し | model_sourceが正しく、未指定で名称を創作しない |
| source/constraints付き入力 | 独立適合質問とstateに出典・制約が残る |
| source/constraints無し旧入力 | unknown/not provided明示、実測を捏造しない |
| qualification_refsだけ付く入力 | 生qualification読込なし、参照を能力値に変換しない |

既存のaudit順位テストとCLI `--ratings` テストは用途・件数が揃っているので維持可能。`tools/agent-tools/eval/test_model_selection_eval.py` のratings fixtureもoutcome_runs=4があり、正常系互換性の確認対象。`tools/agent-audit/tests/test_usage.py` のaggregate_ratings契約は今回変更せず、model-onlyの限界を受け側で明示する。
