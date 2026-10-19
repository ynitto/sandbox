この回の codd の実行を KPT で振り返り、wiki に残す。

1. `python3 .statemachine/codd/codd.py guide kpt` で kpt スキルを読み込み、その手順で振り返る。出力先は wiki-use。
   - 振り返るセッションは、この会話（依頼から終わりの報告まで）。材料に挙がった計画の記録・止まった理由・検査の指摘も読む。
   - 前回の Try の検証は、wiki-use の query で `KPT codd` を探し、最新の 1 件を使う。
   - Problem は、検査で止まった回数・やり直した段・利用者に訊き直したことなど、この回に起きた事実から拾い、
     codd の手順・設定（codd.json）・決まりのファイル・スキルのどれで直せるかまでたどる。
   - 承認の手順（kpt の 5.）は飛ばす。Try は反映せず、状態を「試す」、反映を「未反映（利用者の承認待ち）」として記録する。
2. `python3 .statemachine/codd/codd.py guide wiki-use` で wiki-use スキルを読み込み、記録の全文を ingest する。
   - 題は `KPT <YYYY-MM-DD> codd <計画の英語の名前>`、タグは `kpt`・`retro`・`codd`。
   - 同じ Problem が前の KPT のページにあれば、そのページへのリンクを添える（繰り返している問題として目立たせる）。
3. 利用者には何も訊かない。wiki-use が使えない（入っていない・wiki が無い）ときは、ファイルにも書かず、
   記録を 10 行以内に縮めて応答に書いて終える。
