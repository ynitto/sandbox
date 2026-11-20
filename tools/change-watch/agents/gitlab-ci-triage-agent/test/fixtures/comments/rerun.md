<!-- ci-triage-agent:diagnosis pipeline=123 fp=4ca3458d68c5 -->
### CI の失敗の診断（再実行後）: インフラ・クォータ

パイプライン [#123](https://gl.example/g/p/-/pipelines/123)（`feature/cart` @ `aaaaaaaa`）で 1 件のジョブが失敗しました。
この MR には、このパイプラインより新しいコミットがあります。

#### [`build`](https://gl.example/g/p/-/jobs/1)（`build`）— インフラ・クォータ: ディスクの空き容量が不足

```text
fatal: No space left on device
```

- **次のアクション**: ランナーの状態と利用できるリソース・枠を確かめてください。

<sub>ログの決まった形だけで分類しています（`disk-full`）。秘匿情報らしい文字列は `***` に置き換えています。</sub>
