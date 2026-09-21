# assessed 独立検証（2026-09-21）

> 不採用方式の検証。現行実装の評価ではない。 [実装結果](implemented-independent.md)・[記録一覧](README.md)。

候補: cursor, codex, claude, ollama/gemma4:e4b。通常順と全反転を比較。ローカル judge の実行であり、本家 Jev や選択候補での依頼実行ではない。

要求の routine/demanding 判定を別呼び出しで実施し、その確率分布を候補選択へ渡す。候補から site を外し、費用・quota を economics に分離。偽の能力値・キーワードによる候補除外は使用しない。

最終結果は modelselect._pick(min_confidence=0.6) と、未採用時の modelselect.audit_order に従って算出。期待 local/cloud はテスト設計上の区分であり、実際の作業成功を測った正解ではない。

| 条件 | 件数 | 期待との最終一致 | judge採用 | 総時間 |
|---|---:|---:|---:|---:|
| ホールドアウト通常順 | 6 | 6 | 5 | 25.13秒 |
| ホールドアウト反転順 | 6 | 4 | 4 | 25.46秒 |
| 開発例反転順 | 6 | 3 | 5 | 26.05秒 |

時間には要求判定と候補判定の両方を含む。各行jsonlのsecondsは候補判定のみなので区別する。

## 限界と観測

- 順序による差が残る。ホールドアウトh4/h5は通常順でcloud、反転で最終local。開発例反転では複雑3件すべて最終local。
- 反転h5は候補選択の最大値自体はClaudeだが0.5533で閾値未達になり、auditの費用順位によりlocalとなる。
- 単純翻訳h2の要求判定はdemanding 0.6994と誤分類。通常順ではClaude 0.5742で未採用になり、auditによって最終localになった。最終一致だけ見るとこの誤りが隠れる。
- h3要求判定routine 0.5682は0.6未満だが、実験assessedは要求分布をそのまま材料として使用。要求判定の閾値を設けた本番コードとは同条件でない可能性がある。
- 候補名や既定モデル名は残るため、自己モデル優遇・既存ブランド知識・位置バイアスを分離して測ったものではない。
- 各条件は1回ずつ、12種類の依頼。確率は成功率ではない。これだけで偏り解消やClaudeの優位性を断定できない。

再現スクリプト: validate_assessed.py（入力は independent-holdout-input.json）。生データと最終採用段階は assessed-validation-*.jsonl と assessed-validation-summary.json。
