## [start: 点検し、見る項目を決める]

**何を確かめるか:** {{target}}

この回は**どのファイルも変えないでください**（書くのは `.codd/` だけ。コミットもしない）。

上の「何を確かめるか」を、次のどれかの形にして 1 回だけ実行します。

```bash
python3 .statemachine/codd/codd.py review start                          # 空なら、直近 30 日に変わったファイル
python3 .statemachine/codd/codd.py review start --path src/orders        # ファイル・フォルダを挙げた（繰り返し可。参照先は 名前:パス）
python3 .statemachine/codd/codd.py review start --since "7 days ago"     # 期間を挙げた
```

テストを動かさないよう頼まれたときだけ `--no-test` を付けます。

**出力形式:** コマンドの出力の第 1 行（`OK …` か `NONE …`）をそのまま第 1 行に。
