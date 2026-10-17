## [report: 見つけたものを伝える]

次を実行し、出力をそのまま利用者へ伝えてください（自分の記憶でまとめ直さない）。

```bash
python3 .statemachine/codd/codd.py review report
```

- 何も直していないこと。直すときは、「本流に渡すやりたいこと」の行をそのまま依頼にすれば、本流（計画・確認・変更）で進めること
- 答えの検査が通っていなければ（{{check_output}}）、その旨も伝える
- 手入れのときは、`python3 .statemachine/codd/codd.py next` の一覧（立てておいた計画）も続けて伝える。進めるときは「次の計画を進めて」と頼めばよいこと

**応答:** `caveman` の full で短く（利用者が解除したら従う）。`review report` の出力と出力形式は省かず、変えない。
