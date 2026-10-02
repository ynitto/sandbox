## [stopped: やめたときの結果を伝える]

利用者の判断で、ここでやめます。利用者の答え:

{{choice}}

次を実行し、出力をそのまま利用者へ伝えてください（終了コードが 0 でなくてもかまいません）。

```bash
python3 .statemachine/codd/codd.py report
python3 .statemachine/codd/codd.py record
```

`record` は、やめたことも含めて計画を `docs/.plan/日付-名前.md` に判断の記録として残します。出てきたパスも伝えます。

- 変えた分を残したか、戻したか（`rollback` を実行したか）をはっきり書く
- 止まった理由は `.codd/advice.md` にある。続きをやるなら、次の回のやりたいことを 1 行で示す
- どちらもコミットしていないこと
