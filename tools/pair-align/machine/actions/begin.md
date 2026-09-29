## [begin: 今回の実行が何から始まるかを決める]

**やりたいこと:** {{intent}}
**確認への答え:** {{decision}}

1. 「やりたいこと」が空でなければ、その文をそのまま `.pair-align/work/intent_input.md` に書き出してください（言い換えない）。
2. リポジトリのルートで次を実行してください。
   - 1 で書き出したときだけ `--intent-file .pair-align/work/intent_input.md` を付ける
   - 「確認への答え」が空でなければ `--decision {{decision}}` を付ける

   ```bash
   python3 .statemachine/pair_align/pair_align.py begin
   ```

コマンドが `ERROR` で終わった場合は、その内容を第 1 行を `ERROR` にして返し、先へ進まないでください。

**出力形式:** コマンドの標準出力をそのまま返してください。

この指示に従ってタスクを実行してください。
完了後、指定された形式で出力のみを返してください。次のステップは別途指示されます。
