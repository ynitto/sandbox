# lint — Wiki の整合性をチェックする

```bash
python scripts/wiki_lint.py
```

チェック内容:
- **孤立ページ**: `index.md` に未登録のページ
- **リンク切れ**: `[[ページ名]]` 形式のリンクが存在しないページを参照している
- **未取り込みソース**: `sources/` にコピー済みだが `log.md` に未記録のファイル
- **空ページ**: 本文が極端に短いページ（100文字未満）

出力例:
```
[WARN] 孤立ページ: wiki/concepts/foo.md (index.mdに未登録)
[WARN] リンク切れ: wiki/topics/bar.md → [[baz]] (baz.mdが存在しない)
[INFO] 孤立ソース: sources/2026-01-01-some-paper.pdf (log.mdに未記録)
[OK] 空ページなし
```
