## [confirm: 計画を利用者に確認する]

次を実行し、出力（計画の要約）をそのまま利用者に見せて、この計画で進めてよいかを訊いてください。
全文は貼らず、`docs/.plan/current.md` を開けば読めることを添えます（全文を貼ると、応答の長さの上限で止まることがあります）。
利用者が見出しを指して訊いたときは、その見出しだけを見せます。

```bash
python3 .statemachine/codd/codd.py summary
```

ずれがあるときは、参照先も変えることと、ハーネスが測った影響範囲（`.codd/impact.md` の「候補のファイル」）を添えます。
計画の「守る決まり」に、`codd.py show` の「決まりの候補」から挙げたものがあれば、次からも決まりとして設定に書くかを
あわせて訊きます。書いてよいと言われたら `python3 .statemachine/codd/codd.py rules --write --only 名前:パス` で書きます。

**利用者が答えるまで待ってください。答えを推測したり、先へ進んだりしないでください。**

答えを受けたら、判断の記録に残すため控えます（指摘は利用者の言葉のまま）。

```bash
python3 .statemachine/codd/codd.py decide OK
python3 .statemachine/codd/codd.py decide NG --note "利用者の指摘"
python3 .statemachine/codd/codd.py decide STOP --note "利用者の答え"
```

- 進めてよい → `OK`
- 直してほしい → `NG`。利用者の指摘をそのまま続けて書く（次の計画で踏まえる）
- やめたい → `STOP`（まだ何も変えていないので、そのままやめる）

**出力形式:** 第 1 行に `OK`・`NG`・`STOP` の一語だけ。NG なら第 2 行以降に利用者の指摘。
