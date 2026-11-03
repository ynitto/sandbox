# chain.json の書き方

スキル直下に置く。1 つのスキルが複数のアクションを持てる。

```json
{
  "chain": 1,
  "actions": [
    {
      "id": "audit",
      "summary": "プロジェクトの依存関係を監査し、指摘を JSON で出す",
      "run": ["python", "scripts/audit_deps.py", "--path", "{in:project}",
              "--severity", "{param:severity}", "--json"],
      "inputs": {"project": "project-dir"},
      "outputs": {"report": {"type": "dependency-audit", "ext": "json", "stdout": true}},
      "params": {"severity": {"default": "moderate", "description": "報告する最低の重要度"}},
      "ok_exit": [0, 1],
      "timeout_sec": 1800
    }
  ]
}
```

## 項目

| 項目 | 必須 | 意味 |
|---|---|---|
| `id` | ○ | アクション名。英小文字・数字・ハイフン。スキルの中で一意 |
| `summary` | ○ | 何をするかの 1 行。DAG のステップの説明になる |
| `run` | ○ | 実行する argv。シェルを通さない |
| `inputs` | | `{名前: 型}`。名前は英小文字と `_` |
| `outputs` | ○ | `{名前: {type, ext, stdout}}`。`stdout: true` は標準出力をそのまま成果物にする（1 つまで） |
| `params` | | `{名前: {default, description}}`。`default` が無いと実行時に必須の引数になる |
| `ok_exit` | | 成功とみなす終了コード（既定 `[0]`）。「指摘があると 1」のような検査系は `[0, 1]` |
| `timeout_sec` | | ステップの制限時間 |

## run の差し込み

| 書き方 | 置き換わるもの |
|---|---|
| `{in:名前}` | 入力のファイル（前段の成果物か、DAG の入力の引数） |
| `{out:名前}` | このアクションが書くファイル（作業フォルダの中の相対パス） |
| `{param:名前}` | パラメータの値 |
| `{skill_dir}` | スキルのディレクトリ（絶対パス） |

- 先頭が `python` / `python3` なら DAG を書き出した Python、`node` なら見つかった node に置き換わる。
- スキルの中に実在する相対パス（`scripts/x.py` など）は絶対パスに置き換わる。
- 差し込みは 1 つの要素の中で使える（`--out={out:report}`）。

## 型の付け方

- 型は「次のスクリプトがそのまま読めるか」で分ける。拡張子が同じでも中身の約束が違えば別の型にする
  （`openapi-report` と `dependency-audit` はどちらも JSON だが別の型）。
- フォルダも型にできる（`project-dir`）。
- 既存の型名を `chain.py scan` で確かめ、同じものには同じ名前を使う。

## 検査（`chain.py check`）で落ちるもの

- `run` の差し込みが宣言に無い、宣言した入力・パラメータが `run` で使われていない
- ファイルで出す出力が `run` に無い（標準出力なら `stdout: true`）
- `run` の `scripts/…` が実在しない
- `ok_exit` に 0 が無い
- 同じ型を受け取って同じ型を出す（チェーンが閉じない）
