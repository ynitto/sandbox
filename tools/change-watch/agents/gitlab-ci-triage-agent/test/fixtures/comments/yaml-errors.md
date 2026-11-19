<!-- ci-triage-agent:diagnosis pipeline=123 fp=2a726b3492ef -->
### CI の失敗の診断: 設定ミス

パイプライン [#123](https://gl.example/g/p/-/pipelines/123)（`feature/cart` @ `aaaaaaaa`）で CI の設定エラーが発生しました。

#### `.gitlab-ci.yml`— 設定ミス: GitLab CI の YAML が無効

```text
jobs:build:script config should be a string
```

- **次のアクション**: CI の設定・変数や、ジョブの実行環境を確かめてください。

<sub>ログの決まった形だけで分類しています（`gitlab-ci-yaml-invalid`）。秘匿情報らしい文字列は `***` に置き換えています。</sub>
