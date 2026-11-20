<!-- ci-triage-agent:diagnosis pipeline=123 fp=3c430f418b0b -->
### CI の失敗の診断: 依存関係

パイプライン [#123](https://gl.example/g/p/-/pipelines/123)（`feature/cart` @ `aaaaaaaa`）で 1 件のジョブが失敗しました。

#### [`build`](https://gl.example/g/p/-/jobs/1)（`build`）— 依存関係: pip が指定の版を見つけられない

```text
ERROR: No matching distribution found for requests==99.0
```

- **次のアクション**: requirements の版の指定と、パッケージの取得元（index）に届くかを確かめてください。
- **修正案**: `requests==99.0` の版の指定を、取得元にある版に直してください。

<sub>ログの決まった形だけで分類しています（`pip-no-matching-distribution`）。秘匿情報らしい文字列は `***` に置き換えています。</sub>
