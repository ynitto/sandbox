# judge の回転平均と接頭辞キャッシュの実測 — Jev クローン記事から持ち込めたもの

> 作成 2026-09-22
> 対象: `tools/agent-tools/agentcore/agentcore/judge.py`（`rotations` / `orderings` / `average_orderings`）、
> `herdconfig.py`（`judge.rotations`）、`herdcli.py`（`judge --rotations`）、`eval/readout_eval.py`（`--rotations`）
> 元記事: zephel01「Jev のクローンを 7 本読んだ。再現されていたのは計算形だけで、その計算形は 2020 年からあった」
> （note、2026-09-21）
> 上位文書: [judge 設計](2026-09-19-agent-herd-system-one-judge-design.md)、
> [初回実測](2026-09-20-judge-readout-first-measurement.md)、
> [仕様](../specs/agent-herd-spec.md) §5.5 / §9.3

---

## 0. 一枚で

記事が並べた 7 本のクローンのうち、agent-herd judge に無かった部品は **1 つ**——ruling の
ordering averaging（選択肢の並びを巡回させて複数回読み、log 確率を平均する）。これを
`rotations` として足した。**既定は 1（従来どおり 1 回読み）**で、設定 `judge.rotations` か
`--rotations` で有効にする。答えには `rotations`（読んだ回数）と `agreement`（並べ替えの間で
最頻の選択肢が一致した割合）が付く。

記事の速度の柱——状態を 1 回 encode して各問いを並列の分岐で流す（本家 Jev は 4 問を 1 リクエストに
入れても 549 ms、SemIf は逐次比 8.6 倍）——は Ollama の API では組めない。代わりに、
Ollama の接頭辞キャッシュがいつ効くかを測った。

| 測ったこと | 結果 |
|---|---|
| 接頭辞の再利用 | 共有部が **約 512 トークンを超えるとき**だけ効く（2 問目以降 0.3 秒）。未満は問いごとに全量 prefill |
| 並列 slot（`OLLAMA_NUM_PARALLEL=4`） | 4 問同時で逐次比 1.5 倍。既定は 1 slot で、この mac の server も 1。**不採用** |
| 生成トークン | 既に 2（ラベル + EOS）。decode は 0.04 秒で、削るものは無い |
| 回転（r=3）の費用 | 状態が短いと呼び出しが r 倍（§6 の実測） |

つまり速度で動かせる部品は残っていない。精度側の `rotations` は費用が見える形で opt-in にした。

## 1. 記事の部品と、この judge の対応

| 記事の部品 | クローンでの姿 | agent-herd judge |
|---|---|---|
| 型付き確率を 1 パスで返す（ラベルの logit を読む。文章を生成しない） | SemIf（direct typed logits 1.0 秒 vs JSON 5.3 秒）、ruling、Kev | **2026-09-19 に実装済み**（1 トークン目の `logprobs`、`num_predict=4`、温度 0） |
| 状態を 1 回 encode し、問いを並列の suffix で流す | 本家 Jev、SemIf（777 判定 38.8 秒）、ruling（32 並列で 162 判定/秒） | Ollama では KV を共有する口が接頭辞キャッシュしか無い。「状態が先・問いが後」は既にそう。効く条件を §3 で測った |
| 位置バイアスの補正（ordering averaging） | ruling（`RULING_ROTATIONS=3`。Score は尺度の両向き） | **今回 `rotations`**（§2） |
| 温度スケーリング | Kev（T=1.47 で ECE 0.057 → 0.031） | 不採用（§5） |
| 問い同士の隔離（block-causal mask） | Kev、ruling | 問いごとに別リクエストなので隔離は満たしている（速度は払っている） |
| 較正（JevBench Calibration 軸: 本家 82.7、他は最大 72.6） | どのクローンも本家に届かない | `readout_eval --calibration` の Brier / ECE と用途別の gate（`judge.calibration`）が既にある |

記事の結語は「llama.cpp や Ollama の上に自前で組んだ公開事例は見つけられなかった」。この judge は
その形そのもので、部品は揃っていた。足りなかったのは位置バイアスの扱いだけで、これは
[2026-09-21 の select の調整](../experiments/2026-09-21-selection/tuning-report.md)で
「候補順を反転すると最終結果が変わる」「yes/no ラベル自体の偏りは未解決」と観測していた穴と同じ。

## 2. 回転平均の設計

```
  問い（宣言順）  ──▶  読む（1 回目。従来と同じ）
     │                     分布を読めた ─┐            読めない ─▶ 票 / 本文（従来どおり 1 回）
     │                                    ▼
     └─ 並べ替え 2..r  ──▶  読む ──▶ 宣言順に戻す ──▶ 対数空間で平均 ──▶ 正規化
                                                          │
                                                          └─ agreement = 最頻が一致した並べ替えの割合
```

- **並べ方。** `choice` / `boolean` は巡回シフト（r 回、選択肢の数まで。2 択は正順と逆順の 2 回）。
  `score` は尺度の向きを保つため正順と逆順の 2 回だけ——巡回すると low / high の間に medium が
  来なくなり、順序つきの選択肢という前提が壊れる。ruling が Score を「両向き」にしているのと同じ。
- **平均は対数空間。** 位置バイアスを「位置ごとに logit へ足される定数」と見れば、全位置を
  一巡した log 確率の平均でその定数は消える。算術平均では消えない。top_logprobs（20 個）の外に
  落ちた質量 0 は `LOG_FLOOR`（1e-9）で受ける。
- **`agreement` を確度と別に出す。** 並べ替えで最頻が割れた問いは、確度が高くても位置に
  引かれていた疑いがある。tuning-report の「順序を変えると判定が割れる場合を検知し、その
  不確実性を残す」がこの 1 項目。しきい値はまだ置かない（消費者の gate に足すなら実測してから）。
- **縮退は回転しない。** 1 回目で分布を読めなければ、従来どおり票（`--samples`）か本文へ倒す。
  途中の並べ替えが読めなければその回だけ捨て、読めた分で平均する（`rotations` に実際の回数が残る）。
- **既定は 1。** 呼び出しが r 倍になり、状態が短いと全量 prefill が r 回になる（§3）。select の
  独立適合評価は 4 候補で 5 問い合わせ・上限 75 秒なので、黙って 3 倍にすると上限に当たる。
  効果と費用を §6 で測り、有効にする値は設定（`judge.rotations`）で人が決める。

## 3. 接頭辞キャッシュが効く条件（gemma4:e4b、Ollama 0.34.1、この mac）

Ollama 0.34 は gemma4 を llama-server（`-np 1 -b 512 -ub 512`）で回し、ログに
`restored context checkpoint (pos_min=…, pos_max=…)` を出す——sliding window attention の
KV は途中の位置へ巻き戻せないので、checkpoint から復元して差分を prefill する。

| 状態の長さ（トークン） | 1 問目（cold） | 選択肢を回した 2 問目 | 別の問い |
|---|---|---|---|
| 381 | 1.64 s | 1.47 s | 1.37 s |
| 409 | 5.96 s（GPU 競合あり） | 1.62 s | — |
| 754 | 2.89 s | 0.29 s | — |
| 1099 | 4.17 s | 0.29 s | — |
| 2504 | 9.35 s | 0.30 s | 0.41 s |
| 2984 | 11.45 s | 0.31 s | — |

同じプロンプトの再送は長さに関わらず 0.08〜0.15 秒（完全一致のキャッシュ）。qwen3.5:9b（SWA なし）
でも 2507 トークンで 4.7 秒 → 部分再利用が効いていないので、モデルと server の版に依る。

読み方: **共有する接頭辞が約 512 トークン（`-b 512` の 1 バッチ）を超えると、2 問目以降が
0.3 秒で済む。** それより短い状態では、問いも回転も 1 回ごとに全量の prefill を払う。判断の
状態を 1200 文字に切っている select は、日本語だと 400〜700 トークンで境目に乗る。

## 4. 並列 slot（不採用）

`OLLAMA_NUM_PARALLEL=4` の別 server（port 11435）を立て、同じ状態への 4 つの並べ替えを
逐次と 4 並列で流した（405 トークン、接頭辞の再利用は効かない長さ）。

| | 4 呼び出しの合計 |
|---|---|
| 逐次 | 8.42 s / 9.22 s |
| 4 並列 | 9.04 s / 6.16 s |

良くて 1.5 倍。既定の server は 1 slot（この mac も `OLLAMA_NUM_PARALLEL:1`）で、slot を
増やすと slot ごとに KV を持つので §3 の再利用が割れる。クライアント側の並列化は入れない。

## 5. 採らなかった案

- **温度スケーリング（Kev の T=1.47）。** 確度の順位は変えず、しきい値の位置だけ動く。用途別の
  しきい値（`judge.calibration`）が既にその役を果たしている。[初回実測](2026-09-20-judge-readout-first-measurement.md)
  の誤り（E3〜E5、確度 0.95〜0.99）は較正で直る形ではなく、問いの立て方で直った。
- **content-free の事前分布で割る（Calibrate Before Use, 2021）。** 「N/A」のような空の状態で
  ラベルの分布を 1 回読み、その偏りで割る。1 モデル 1 回で済み回転より安いが、消えるのは
  ラベル自体の偏りだけで、内容と位置の相互作用は残る。回転が高すぎる場面の次の候補。
- **確度が高ければ回転を省く。** 位置に引かれた答えも確度は高く出るので、省く根拠が無い。
- **`num_predict` を 1 にする。** 生成は既に 2 トークン（0.04 秒）。読む位置の余裕を捨てる
  価値が無い。
- **既定を 3 にする。** 呼び出しが増える先は select / route / 遷移条件 / filter / assess の
  全部で、費用の見えないまま切り替わる。実測（§6）を見て設定で決める。

## 6. 回転の実測（gemma4:e4b、2026-09-22）

### 6.1 select の独立適合評価（modelfit）、r=1 と r=3

依頼 12 件（[holdout 6 件](../experiments/2026-09-21-selection/independent-holdout-input.json) +
tune.py の開発 6 件）× 候補 4（cursor / codex / claude / ollama/gemma4:e4b）。本番の
`modelfit.evaluate` を直接呼び、CLI は起動しない。要求水準の問い（3 択 → 3 回）と候補ごとの
適合（boolean → 2 回）で、r=3 は 5 呼び出しが 11 呼び出しになる。

| 依頼 | 期待 | 選択（r=1 → r=3） | 適合 yes 確率で大きく動いたもの（r=1 → r=3） | agreement 最小 |
|---|---|---|---|---|
| h1 挨拶 | local | ollama → ollama | cursor 0.97 → 0.80 | 0.5 |
| h2 翻訳 | local | ollama → ollama | cursor 0.91 → 0.46 | 0.5 |
| h3 抽出 | local | claude → claude | cursor 0.22 → 0.02 | 1.0 |
| h4 改善提案 | cloud | 保留 → 保留 | claude 0.40 → 0.05、codex 0.23 → 0.02 | 1.0 |
| h5 重複請求の修理 | cloud | claude → claude | cursor 0.97 → 0.82、ollama 0.97 → 0.86 | 0.5 |
| h6 認可の移行 | cloud | claude → claude | cursor 0.69 → 0.07、ollama 0.95 → 0.73 | 0.5 |
| greeting | local | ollama → ollama | cursor 0.99 → 0.91 | 1.0 |
| translation | local | ollama → ollama | cursor 0.83 → 0.14 | 0.5 |
| fact | local | ollama → ollama | cursor 0.96 → 0.39 | 0.5 |
| proposal | cloud | claude → claude | codex 0.81 → 0.15、claude 0.95 → 0.65、ollama 0.90 → 0.47 | 0.5 |
| debug | cloud | claude → claude | cursor 0.97 → 0.87 | 1.0 |
| architecture | cloud | claude → claude | cursor 0.44 → 0.02、ollama 0.97 → 0.88 | 1.0 |

- **最終の選択は 12 件とも変わらない**（期待との一致 10/12、保留 1 は両方同じ）。この標本で
  「選択が正しくなった」とは言えない。
- 変わったのは確率の形。yes/no を入れ替えて読むと、r=1 で 0.8〜0.97 だった弱い候補の yes が
  0.1〜0.5 へ落ちる（48 問の |Δ| は中央値 0.04、最大 0.69）。つまり r=1 の高い yes の一部は
  「yes が A に置かれていた」ことで出ていた——tuning-report が未解決としていた yes/no ラベルの
  偏りがこれ。上位候補（0.98〜1.00）はほとんど動かないので選択は保たれた。
- 8/12 件で少なくとも 1 つの適合問いが並べ替えで割れた（agreement 0.5）。割れた問いは
  弱い候補の側に集まる。要求水準の問い（3 択）は 12 件とも割れなかった。
- 費用: 状態が短い h1 / h2 は 23 s → 63 s、31 s → 83 s（2.7 倍。**modelfit の上限 75 s を超えた**）。
  残りは r=1 と同程度に見えるが、これは直前の r=1 で送った宣言順のプロンプトが server 側の
  prompt cache に残っていたため（完全一致のヒット）で、独立に測った費用ではない。素の費用は
  §3 のとおり、状態が 512 トークン未満なら呼び出し 1 回ごとに全量 prefill。

結論: select に r=3 を既定で入れる根拠は無い（選択は変わらず、上限に当たる）。効くのは
確率を信用する場面——`agreement` で割れた問いを保留に回す、`min_confidence` の較正——で、
それは用途別に測ってから。

### 6.2 較正セル（`readout_eval --calibration`、CELLS 12 セル × 1 回）

初回実測と同じ 12 セル（F1 / J2 / CL1 / E1〜E6 / RO1〜RO3、問い 17）を r=1 と r=3 で別 run にした。

| | r=1 | r=3 |
|---|---|---|
| セルの合否 | 9/12（E3 / E4 / E5 が不正解） | 9/12（同じ 3 セル） |
| 問いの正答 | 14/17 | 14/17 |
| Brier / ECE（logprobs） | 0.342 / 0.160 | 0.320 / 0.140 |
| 呼び出し / 入力トークン | 17 / 4,830 | 39 / 10,962 |
| 壁時計（合計 / 中央値） | 112 s / 7.6 s | 148 s / 6.4 s |
| agreement < 1 の問い | — | 1/17（RO3 の `other`、0.67） |

- 正誤は 1 問も動かない。E3〜E5 は 2 回とも `done` で agreement 1.0——位置に引かれた誤りでは
  なく、初回実測どおり問いの立て方の問題（status しか読んでいない）。回転で直る種類ではない。
- Brier / ECE はわずかに良くなる。動いたのは F1 の boolean で、c1 の確度 0.63 → 0.92 など、
  yes/no を入れ替えても同じ答えが出る問いは平均で確度が上がり、逆に位置に寄っていた確度は下がる
  （F1:c6 0.97 → 0.89）。
- 費用は入力トークン 2.3 倍に対して壁時計 1.3 倍。状態が長いセルでは接頭辞キャッシュ（§3）が
  効いて、回転 1 回が 0.3 秒で済んでいる。短い状態の select（§6.1）とは逆の側。

結論をまとめると: 回転は**位置に寄った確率を直す**が、**答えを変えるほどの位置バイアスはこの
標本には無かった**。有効にする価値があるのは確率そのものを使う場面（`score` の確率加重、
`min_confidence` の較正、`agreement` による保留）で、状態が 512 トークンを超える呼び出しなら
費用はほぼ増えない。既定は 1 のまま、用途別に `judge.rotations` で入れる。

## 7. 使い方

```bash
agent-herd judge --questions q.json --rotations 3 < state.json     # 1 回だけ試す
agent-herd config set judge.rotations 3                            # 組み込みの判定すべてに効かせる
agent-herd config unset judge.rotations                            # 既定（1 回読み）へ戻す
python3 tools/agent-tools/eval/readout_eval.py --calibration --rotations 3 --output-dir …   # 較正の台帳に残す
```
