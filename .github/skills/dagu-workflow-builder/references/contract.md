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
| `run` | どちらか一方 | 実行する argv。シェルを通さない |
| `agent` | どちらか一方 | エージェント CLI に渡すプロンプト（下の「エージェント CLI を呼ぶアクション」） |
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

## エージェント CLI を呼ぶアクション

スクリプトが無く、エージェントに任せるしかない変換は `run` の代わりに `agent` を書く。

```json
{
  "id": "summarize-audit",
  "summary": "依存関係の監査結果を読み、対応の優先順位を Markdown でまとめる",
  "agent": {"prompt": "ファイル {in:audit} は依存関係の監査結果（JSON）です。対応すべき順に箇条書きでまとめ、本文だけを答えてください。"},
  "inputs": {"audit": "dependency-audit"},
  "outputs": {"summary": {"type": "audit-summary", "ext": "md", "stdout": true}},
  "timeout_sec": 900
}
```

| 項目 | 意味 |
|---|---|
| `agent.prompt` | プロンプト。`run` と同じ差し込みが使える。入力はファイルのパスに置き換わるので「ファイル … を読んで」と書く |
| `agent.cli` | 使う CLI の定義名（`agents/<名前>.json`）。省略すると build の `--agent-cli`（既定 `kiro`） |
| `agent.readonly` | 省略すると、ファイルの出力（`{out:…}`）があれば書き込み可、無ければ読み取りだけ |

- 応答は標準出力に出るので、ふつうは出力を `"stdout": true` にする。エージェントにファイルを書かせるなら
  `{out:名前}` をプロンプトに入れる（書き込み可で起動する）。
- いま使える CLI は、プロンプトを引数で受け取り、応答を標準出力に出すもの（kiro-cli）。
- 探索ではスクリプトよりずっと重く数える。同じ型をスクリプトで作れるなら、そちらが選ばれる。

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
- `run` と `agent` の両方がある、`agent.readonly` が true なのにファイルの出力がある
- 同じ型を受け取って同じ型を出す（チェーンが閉じない）
