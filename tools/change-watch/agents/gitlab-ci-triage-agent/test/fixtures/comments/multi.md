<!-- ci-triage-agent:diagnosis pipeline=123 fp=2444fe0bc035 -->
### CI の失敗の診断: 依存関係

パイプライン [#123](https://gl.example/g/p/-/pipelines/123)（`feature/cart` @ `aaaaaaaa`）で 4 件のジョブが失敗しました（依存関係 2・テスト失敗 1）。

#### [`build`](https://gl.example/g/p/-/jobs/1)（`build`）— 依存関係: pip が指定の版を見つけられない

```text
ERROR: No matching distribution found for requests==99.0
```

- **次のアクション**: requirements の版の指定と、パッケージの取得元（index）に届くかを確かめてください。
- **修正案**: `requests==99.0` の版の指定を、取得元にある版に直してください。
- 同じ原因のジョブ: `build-arm64`

#### [`unit`](https://gl.example/g/p/-/jobs/3)（`test`）— テスト失敗: pytest のテストが失敗

```text
FAILED tests/test_cart.py::test_empty_cart
```

- **次のアクション**: 失敗したテストの出力を読み、実装と期待値を確かめてください。

ほかに失敗したジョブ: `deploy`

<sub>ログの決まった形だけで分類しています（`pip-no-matching-distribution`・`pytest-failed`）。秘匿情報らしい文字列は `***` に置き換えています。</sub>
