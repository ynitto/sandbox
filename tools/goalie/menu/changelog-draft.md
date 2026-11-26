## 目的

指定期間のコミット・PR を確認し、commit-pr-writer の流儀に沿って CHANGELOG の [Unreleased] への追記案を作る。

## 入力

- `period`（対象期間。日付、Git リビジョンの範囲、または直近の経過時間で指定）＝ `{{period}}`
- `changelog`（既存 CHANGELOG のパス）＝ `{{changelog|CHANGELOG.md}}`
- `pr_source`（参照できる PR 一覧のパス）＝ `{{pr_source|指定なし}}`
- `output`（作業フォルダからの成果物パス）＝ `{{output|changelog-draft.md}}`

## 期待成果物

`output` に Markdown で [Unreleased] への追記案と、その根拠となるコミット ID・確認できた PR 番号を記す。commit-pr-writer の規約を参照し、Conventional Commits の種別や破壊的変更の有無を踏まえて、利用者に影響する変更を既存 CHANGELOG の見出し・言語に合わせてまとめる。既存記載と重複する変更を除き、参照できなかった履歴や PR は記録する。元の CHANGELOG は編集しない。

## 検収観点

- 指定パスに成果物があり、各項目の根拠が対象期間のコミットや PR にあるか。
- 既存の記載と重複せず、分類と書式が既存 CHANGELOG に合うか。
- 確認できた破壊的変更が明示され、未確認の PR や挙動を断定していないか。

確認や質問はしない。既存テストを壊さない。依頼にない大規模な変更はしない。できなかったことは理由とともに報告する。
