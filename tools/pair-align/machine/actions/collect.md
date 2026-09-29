## [collect: 受け取った依頼の残りと自分の変更を集める]

リポジトリのルートで次のコマンドを実行してください（`{{since}}` が空でなければ `--since {{since}}` を付ける）。

```bash
python3 .statemachine/pair_align/pair_align.py collect
```

コマンドが `ERROR` で終わった場合は、その内容を第 1 行を `ERROR` にして返し、先へ進まないでください
（相手のパスの誤りなど、利用者が `.statemachine/pair_align/pair.json` を直す必要があります）。

**出力形式:** コマンドの標準出力をそのまま返してください。第 1 行は `INBOUND_PENDING` / `NO_CHANGES` / `CHANGES` のいずれかで始まります。

この指示に従ってタスクを実行してください。
完了後、指定された形式で出力のみを返してください。次のステップは別途指示されます。
