# GitLab インラインレビューコメント

obsidian-gitlab-issues プラグインに、イシュー/MR ノードの本文へ「箇所を選んでコメント」を
繰り返し付け、それらをまとめて 1 件のイシューコメントとして投稿し、必要ならラベルを
付け替えて再作業をトリガーする機能を追加した。

参考: [HTML 設計ドキュメントにインラインコメントを付けて再作業させるワークフロー](https://zenn.dev/rehabforjapan/articles/html-design-doc-workflow-claude-code-202605)。
HTML である必要はなく、Obsidian ネイティブの脚注で「人間にとって見やすい・入力しやすい」
マークダウン表現を採用した。

## マークダウン表現（脚注スタイル）

選択箇所をハイライトで囲み、脚注参照を付ける。コメント本文はノート末尾の管理ブロックに置く。

```markdown
... この ==処理は冪等でない==[^gli-1] ため、リトライ時に二重実行される ...

<!-- gitlab-review-comments:start -->
[^gli-1]: 💬 リトライ時に二重実行される。冪等化が必要。
<!-- gitlab-review-comments:end -->
```

- 脚注ラベルは `gli-<n>` で名前空間化し、ユーザー自身の脚注と衝突しない。
- リーディングビューでは脚注が連番（クリック/ホバーで本文表示）として描画され読みやすい。
- 定義は `<!-- gitlab-review-comments:start/end -->` で囲んだ管理領域に集約し、収集・消去を確実に行える。
- 選択なしでコメントした場合は本文末尾に裸の参照を置き、アンカーなしコメントとして扱う。

## ワークフロー

1. **コメントを付ける** — イシューノートでテキストを選択し、
   コマンド「Add inline review comment from selection」、または
   サイドパネル「Inline review」の「＋ From selection」を実行 → モーダルでコメントを入力。
   これを必要なだけ繰り返す。
2. **集約する** — パネルの「Compose ↑」で全インラインコメントを上部のコメント欄に集約
   （見出し + 引用 + コメントの箇条書き）。必要なら手で編集できる。
3. **投稿して再作業を依頼** — 「Post review → request re-work」で
   - 集約コメントをイシューコメントとして投稿
   - 下の Labels 欄に入力があれば付け替え（例: `status::reviewing` を外し `status::todo` を付与）
   - 「Clear annotations after posting」が有効ならノートの注釈を消去してクリーンな状態に戻す
4. ラベルが付け替わることでワーカーノードがプロンプトトリガーで再作業を拾う（gitlab-idd 連携）。

## 実装

- `src/IssueActions/inline-comments.ts` — マークダウン表現の純関数
  （`addInlineComment` / `parseInlineComments` / `composeAggregateComment` /
  `clearInlineComments`）とエディタ連携ヘルパー。脚注の付与・収集・消去はテキストレベルで
  決定的に動く（27 ケースの自己検証で確認）。
- `src/IssueActions/modals.ts` — `InlineCommentModal`（選択箇所を引用表示しつつコメント入力）。
- `src/IssueActions/form.ts` — パネル/モーダル内の「Inline review」セクションと投稿フロー。
- `src/main.ts` — コマンド「Add inline review comment from selection」とモーダル連携フック。

ラベル付け替えは既存の `applyLabelChanges`（ワイルドカード除去対応）を再利用しているため、
`status::*` のような一括除去 → 付与もそのまま使える。
