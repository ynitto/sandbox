## [start: 点検して、見る項目を決める]

**どう回すか:** {{mode}}
**何を確かめるか:** {{target}}

この回は**コードも文書も変えないでください**（書くのは `.codd/` と `.plans/next/` だけ。コミットもしない）。

1. **手入れのときだけ、先にグラフを用意する。** `python3 .statemachine/codd/codd.py graph` を実行します。
   `PENDING` と出たら、示されたとおりに束ごとに文書を抜き出し（サブエージェント `codd-graph` に渡すと示されたら、
   自分では文書を読まない）、`python3 .statemachine/codd/codd.py graph --merge` を実行します。`OK` が出るまで繰り返します
   （`FAILED` と出た束だけをやり直す）。graphify が無ければ、その旨が出るので先へ進みます。チェックのときはしません。
2. 上の「何を確かめるか」を、次のどれかの形にして 1 回だけ実行します。

   ```bash
   python3 .statemachine/codd/codd.py review start                          # 空でチェック: 直近 30 日に変わったファイル
   python3 .statemachine/codd/codd.py review start --since "8 days ago"     # 空で手入れ（週 1 回の定期実行と少し重ねる）
   python3 .statemachine/codd/codd.py review start --path src/orders        # ファイル・フォルダを挙げた（繰り返し可。参照先は 名前:パス）
   python3 .statemachine/codd/codd.py review start --since "7 days ago"     # 期間を挙げた
   ```

   機能や名前だけを挙げられたとき（「注文まわり」「`hello`」など）は、先に `codd.py impact --term 名前`（自分）と
   `codd.py explore --term 名前`（参照先）で関係するファイルを探し、そのファイルを `--path` に並べます（多くても 20）。
   テストを動かさないよう頼まれたときだけ `--no-test` を付けます。

**出力形式:** `review start` の出力の第 1 行（`OK …` か `NONE …`）をそのまま第 1 行に。
