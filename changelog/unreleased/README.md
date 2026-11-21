# CHANGELOG フラグメントの書き方

`CHANGELOG.md` は直接編集せず、変更1件につきこのディレクトリにファイルを1つ追加します。ファイル名は `<対象>-<短いスラッグ>.md` にします（例 `change-watch-iac-risk-check.md`）。

内容は従来の CHANGELOG エントリと同じ形式で、`### 見出し` の行に続けて変更内容を箇条書きにします。

```markdown
### change-watch: IaC変更のリスク検査を追加

- MR 差分の IaC 変更を検査し、公開範囲の拡大などを検出したときに警告する。
```

リリース時または定期的に、リポジトリのルートで次のコマンドを実行して集約します。`--dry-run` を付けると、取り込み予定を事前に確認できます。

```sh
python3 tools/changelog-collect/changelog_collect.py
```
