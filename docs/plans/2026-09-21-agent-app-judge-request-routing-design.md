# 依頼の振り分けを judge に委ねる: 答える・会話で実行・タスクやワークフローの流用・スキルの選択を、agent-app の入力欄 1 つから

> 作成 2026-09-21
> 対象: `tools/agent-tools/agentcore/agentcore/route.py`（新設）/ `herdcli.py`（`route` サブコマンド）/
> `herdconfig.py`（`route.*`）、`tools/agent-app/src/main/requestRouting.js`（新設）/ `ipc.js`（`runTurn`）/
> `skillSelection.js` / `renderer/index.html`（実行設定）
> 上位文書: [agent-herd 設計](../designs/agent-herd-design.md) ADR-4「権限と受入をモデルの外で判定する」、
> [judge 設計](./2026-09-19-agent-herd-system-one-judge-design.md)、
> [呼び出し先の選択の設計](./2026-09-20-agent-tools-model-selection-design.md)
> 状態: 段 0（agent-tools 側の `agent-herd route`）、段 1（agent-app 0.25.0 の配線と画面）、段 2（標本 40 件の
> 実測。既定 0.6 / 0.75 を据え置き）、段 3（流用時の入力値を依頼から写す。agent-app 0.27.0）まで 2026-09-21 に完了
> 効く柱・原則: 柱 3 / C9（判断を prefill 1 回の最小モデルへ流し、上位モデルを実行に温存）、
> 柱 2 / C3（答える・流用で済む依頼を機械で決め、人が毎回選ばない）

---

## 0. 一枚で

いま judge が決めているのは「どのエージェント・モデルに任せるか」だけ（`agent-herd select`）。
本設計はその 1 段手前、**「この依頼をどう扱うか」**を同じ judge に委ねる。

```
  会話画面の入力欄（統合の入口）
        │ 送信
        ▼
  決定的な前処理（LLM なし）: 先頭のスラッシュ行 / 定型の依頼 / 明示のスキル名 → そのまま
        │ 残り
        ▼
  agent-herd route   状態 = 依頼の先頭 + 候補（タスク・ワークフロー・スキル）
        │            問い = handling(choice) / task(choice) / flow(choice) / skill:*(boolean) / routine(boolean)
        │            段  = jev → judge。決めなければ「決めず」（終了コード 1）
        ▼
  ┌ answer   … 実行せず読み取りだけで答える（そのターンだけ読み取り専用）
  ├ converse … 会話で実行（いまの既定。select でエージェント・モデルを選ぶ）
  ├ task     … 既存タスクの流用。会話は送らず、タスクを開く導線を 1 枚出す
  ├ flow     … 既存ワークフローの流用。同上
  └ 決めず   … converse（従来どおり）。実行情報に「振り分け：決めず」
  + skill:* の yes を最大 3 件添える　　+ routine の yes は応答後に「定型化」への 1 行
```

主要な決定は 3 つ。

1. **振り分けは agent-tools 側の 1 サブコマンド `agent-herd route`** に置き、app は候補を渡して答えを検証するだけ
   （`select` と同じ分業。`modelSelection.js` の写し）。CLI は `select` と**別**にし、段の実装（jev → judge の
   試行と記録）だけを 1 つに共有する（§2.5）。`judge.py` は触らない。
2. **judge は「実行するかどうか」までは決めるが、「実行のボタン」は押さない。** `answer` は読み取り専用で送る
   （送るのは人が押した送信）。`task` / `flow` は会話を送らず、開く導線を出して止まる。新しいタスクや
   ワークフローの**作成は提案止まり**で、既存の「この作業を定型化」へ繋ぐ。
3. **決めなければ従来どおり。** judge が無い・届かない・確度不足・`other` のどれでも、いまの動き（会話で実行、
   スキルは bigram の一致）に倒す。judge 設計 §5 の 4 件の配線と同じ不変条件。

却下した主要案: クラウド CLI に「分類して JSON を返せ」と生成させる（§7）、judge に新規タスクを作らせて
そのまま実行する（§7）、送信前にプレビューを出して人に選ばせる UI（§7）。

読むべき人: agent-app の `runTurn` を触る人、`agent-herd` にサブコマンドを足す人、judge の実測をする人。

## 1. なぜいま足せるか（前提の確認）

- `agent-herd judge` は「状態 + 型付きの問い → 確率つきの答え」を返し、`other` と `--min-confidence` で
  「決めない」を言える（judge 設計 §2）。`select` はこれを jev → judge → 決定的の 3 段で包んだ
  （選択の設計 §0）。振り分けはこの 2 つの形をそのまま借りられる。
- app 側の候補は全部手元にある。タスクは `.statemachine/*/workflow.yaml` の `name` / `description`
  （`automation/store.list`）、ワークフローは `.agents/workflows/*.json`（`flow-store.list`）、スキルは
  `skills.catalog` の `name` / `description` / `tags`。どれも一覧を作る関数が既にある。
- app のスキル自動選択（`skillSelection.select`）は bigram の一致率で決めている。LLM を 1 回も呼ばない
  ので安いが、「依頼に単語が出ていないスキル」は拾えない。judge の boolean 1 問ずつ（agent-flow の
  `filter_judge` と同じ形）はこの穴を埋める用途に向く。
- 実測の道具はある。`readout_eval.py --calibration` が method 別の Brier・ECE・しきい値掃引を出す
  （judge 設計 §6）。この mac に ollama があるので、標本を作れば当日中に測れる。

つまり足すのは「問いの組み立て」と「答えを app の動きへ写す配線」だけで、判断 AI そのものは増えない。

## 2. 契約: `agent-herd route`

抽象度: コンポーネント。

### 2.1 入口

```
agent-herd route --candidates <JSON|パス> [--min-confidence 0-1] [--hold-min-confidence 0-1]
                 [--stages jev,judge] [--json] < 依頼文
```

`--candidates` は app が組む 1 枚:

```json
{"tasks":  [{"id": "daily-report", "name": "日報", "description": "前日の commit と課題から日報を書く"}],
 "flows":  [{"id": "release-check", "name": "リリース前点検", "description": "..."}],
 "skills": [{"name": "api-designer", "description": "REST API の設計・OpenAPI 仕様生成"}],
 "context": {"repo": "sandbox", "attachments": ["api.yaml"], "readonly": false}}
```

候補は**呼び出し側が絞ってから渡す**（タスク・ワークフロー各 8 件、スキル 6 件を上限）。judge の choice は
ラベルが A〜Z の 26 個までで、候補を並べるほど prefill が伸びる。絞り方は決定的（§4.1）。

### 2.2 問い（1 基準 1 問。状態を先に置いて接頭辞キャッシュに乗せる）

| 名前 | 型 | 問い | 選択肢 |
|---|---|---|---|
| `handling` | choice | この依頼をどう扱うのが適切か | `answer` 実行せず読み取りだけで答える / `converse` 会話の中で AI に実行させる / `task` 候補タスクのどれかが同じ作業で入力を替えれば済む / `flow` 候補ワークフローのどれかが同じ作業 / `other` どれとも言えない |
| `task` | choice | 流用するならどのタスクか | 候補タスク（id）/ `other` どれでもない |
| `flow` | choice | 流用するならどのワークフローか | 候補ワークフロー / `other` |
| `skill:<name>` | boolean | このスキルを添えると依頼の質が上がるか | yes / no（候補ごとに 1 問） |
| `routine` | boolean | 日付や対象などの入力だけ替えて今後も繰り返す形か | yes / no |

`task` / `flow` は `handling` の答えに関わらず訊く。状態が同じなので 2 問目以降は prefill がキャッシュに
乗り、費用は 4 トークンずつ。app 側で「`handling` が `task` のときだけ `task` を読む」と条件を持つほうが、
judge を 2 往復させるより安い。候補が無い種類の問いは組まない（`task` が空なら `handling` の選択肢からも
`task` を外す）。

`handling` を 1 問の choice にしたのは、statemachine の `outcome`（judge 設計 §5.2）と同じ理由で、
「答えるべきでもあり実行すべきでもある」という矛盾が構造として起きないから。

### 2.3 答え

```json
{"handling": {"choice": "task", "confidence": 0.82},
 "task":     {"choice": "daily-report", "confidence": 0.77},
 "flow":     null,
 "skills":   [{"name": "api-designer", "probability": 0.71}],
 "routine":  {"value": true, "probability": 0.66},
 "stage": "judge", "abstained": ["flow"]}
```

- `stage` は `jev` / `judge`。決定的な 3 段目は**持たない**。`select` の audit 段は「必ず決める」ための
  砦だが、振り分けの「決めない」は従来の動きそのものなので、砦は呼び出し側にある。
- 終了コード: 0 = `handling` を決めた、1 = 決めず（確度不足・`other`・ollama に届かない）、2 = 引数の誤り。
  `judge` / `decide` と同じ作法で、**決めていないことを黙って答えへ倒さない**。
- しきい値は 2 つ。`route.min_confidence`（既定は `select.min_confidence` と同じ 0.6）を全問に、
  `route.hold_min_confidence`（仮置き 0.75）を「会話を送らずに止める」`task` / `flow` にだけ掛ける。
  止めるほうが人の手数を増やすので、下限を高くしておく。どちらも §6 の実測で見直す。

### 2.4 実装の置き場

`agentcore/route.py`。問いの組み立てと答えの整形が本体で、段の試行は `modelselect` と共有する（§2.5）。
`herdcli.cmd_route` は `cmd_select` の写し。設定は `herdconfig` の `KNOWN_KEYS` に
`route.min_confidence` / `route.hold_min_confidence` を足す。仕様書 §5 に 1 節、README「作業別の使い方」に
1 節。`judge.py` の契約（状態 + 問い → 答え）はそのまま使い、改修しない。

### 2.5 `select` と統合するか（CLI は別、段の実装は 1 つ）

判断: サブコマンドは `select` と `route` の 2 つのまま。共有するのは「同じ状態に jev → judge の順で
訊き、どの段が決めたかを `attempts` に残す」試行の実装だけで、いまは `modelselect.select` の中に
埋まっている（`ask_jev` / `ask_judge` を順に呼ぶ 35 行ほど）。これを `modelselect._attempt(state,
questions, stages)` として切り出し、`ask_jev` の payload（`jev_payload` は問い 1 つを `candidate`
の名前で固定している）を問いの dict へ一般化する。`judge.evaluate` は元から dict を受けるので変えない。

統合すれば減るもの: WSL でのプロセス起動 1 回と、judge の prefill 1 回（状態が違うのでキャッシュは
効かない）。合わせて数秒。増えるもの: 状態が長くなる（候補のエージェント + 予算 + タスク + ワークフロー +
スキル）ので両方の prefill が伸び、初回実測の教訓「問いは状態の行を指させる」に対して無関係な節が混ざる。

分けたままにする理由は 3 つ。

| 観点 | `select` | `route` |
|---|---|---|
| 終了コードの意味 | **必ず決める**（audit 段が砦。1 は候補が全滅） | **決めなくてよい**（1 = 決めず。従来の動きへ倒すのが正） |
| 順序 | `route` の後。`answer` なら readonly になり、候補（herd の `/find`）と `--purpose plan|work` が変わる | `select` の前 |
| 呼び手 | CLI のほかに Python（`executionresolver` の selector、agent-flow の `run_agent`） | app の `runTurn` だけ |

1 つの CLI にすると、終了コードのどちらかが嘘になる（`select` に「決めず」を足すか、`route` に決定的な
選択を作るか）。順序の依存は「`handling` を見てから候補を組み直す」を 1 プロセスの中に持ち込むことで、
app が持つ `purposeOf` の写しを agent-tools に置くことになる。既存の呼び手（agent-flow・eval の
real run adapter・app の `model-selection.test.js`）は `select` の flags と出力を固定で読んでいるので、
統合はそれらの改修も伴う。

見直す条件: `route` に Python の呼び手（TUI の `/`、agent-loop の投入口）が付き、`select` と同じ
プロセスで連続して呼ぶ場面が増えたとき。そのときも先に共有する試行の実装が 1 つになっていれば、
統合は CLI の表面だけで済む。確信度は中程度（費用の差は §6 で測る）。

## 3. app 側の配線（`runTurn`）

抽象度: コンポーネント。

```
runTurn
  ├ executionSpec（従来）
  ├ 共有の依頼（SHARED_POLICY）→ 振り分けしない（従来）
  ├ [新] 決定的な前処理: 先頭のスラッシュ行 / 定型の依頼 / p.skillMode=manual → route を呼ばない
  ├ [新] requestRouting.route({ text, candidates, capture })   ← selectionLimits / ratings と並行
  │     └ answer   → requested.readonly = true（このターンだけ）
  │       task/flow（hold 下限以上）→ 会話を送らず、案内の 1 枚を appendMessage して return
  │       converse / 決めず → 何もしない
  ├ modelSelection.select（従来。readonly が変わっていれば purposeOf も変わる）
  ├ [変更] skillSelection.select に judged: [{name, probability}] を渡す
  │     └ 明示のスキル名 → judge の yes（確率順）→ 従来の bigram 1 位 → self-checking の順で最大 3 件
  └ 実行情報に「振り分け：答えるだけ / 会話で実行 / 決めず」と「選択方法：ローカル判定 / Jev」
```

`requestRouting.js` は `modelSelection.js` と同じ形（候補を組む・`agent-herd route` を `capture` で
起こす・返った id が候補にあるか検証する・ENOENT や旧版なら「決めず」）。90 秒ではなく 30 秒で切る。
judge は prefill 律速で、候補 20 件でも数秒のはず（実測で確かめる）。

`routine` が yes のときは、応答が終わってから実行情報に 1 行「繰り返せる依頼です。••• → この作業を定型化」
を足す。定型化そのものは既存の `session-routine`（会話から AI が スキル / タスク / ワークフローを提案する）
に任せ、新しい生成経路は作らない。

`task` / `flow` の案内は**会話を消費しない**。送信した本文は入力欄に残し（「入力欄に戻す」と同じ）、
案内の 1 枚に「タスクを開く」と「そのまま会話で実行」の 2 つを置く。後者は振り分けを切って再送する。
タスクを開いたときは本文を実行条件の自由入力へ写すだけで、**入力値の抽出はしない**（§7、段 3）。

## 4. 決定的に済ませる部分（LLM の前後）

抽象度: 実装。

### 4.1 前（候補の絞り込み。`prefilter`）

- 先頭のスラッシュ行（`/sm name` など）があれば route を呼ばない。起動形は `slashroute` が決める。
- 定型の依頼ボタン（`quickRequests`）から入った本文と、`skillMode: manual` のターンも呼ばない。
- 依頼文に名前がそのまま出ているスキルは「依頼で明示」として確定し、judge の候補から外す（従来どおり）。
- タスク・ワークフロー・スキルの候補は `skillSelection.relevance`（bigram）の上位だけ渡す。タスクと
  ワークフローの一覧は `{name, description}` の形がスキルと同じなので、同じ関数がそのまま使える。
  一致率 0 の候補は渡さない。候補が 0 なら judge は `handling`（answer / converse）と `routine` だけ。
- 権限が読み取り専用のターンは `handling` を訊かない（answer と converse の差が無い）。

### 4.2 後（答えの検証）

- 返った id が候補に無ければ「決めず」。`stage` が `jev` / `judge` 以外も「決めず」。
- `task` / `flow` は `handling` がそれを指し、かつ両方が hold 下限以上のときだけ止める。片方でも足りなければ
  converse。**最頻を黙って採らない**（judge 設計 §5.3 と同じ）。
- スキルは yes の確率が `route.min_confidence` 以上のものを確率順に。上限 3 件は `MAX_SELECTED` のまま。

## 5. 画面（承認が要る）

抽象度: 概要。借りる形: 会話画面の実行設定ポップオーバー（スキルの行と同じ `<label><select>`）と、
会話の実行情報（`parts.information` の status 行）、受信箱の要対応の行のボタン（`.row` に `.small`）。

実行設定に行を 1 つ足す。既定は「自動」（agent-herd が使えるとき）。

```
┌ 実行設定 ───────────────────────────────── 利用状況を見る ┐
│ 今回の配分   [通常設定に従う      ▾]                        │
│ 起動方針     [標準                ▾]                        │
│ スキル       [自動                ▾]                        │
│ 依頼の扱い   [自動で振り分ける    ▾]   ← 追加（自動 / 会話で実行）│
│ 権限         [確認して実行        ▾]                        │
│ 作業フォルダ [main                ▾]  管理                  │
└─────────────────────────────────────────────────────────────┘
```

送った結果、`task` に振り分けたとき。会話の吹き出しは作らず、実行情報と同じ status 行に操作を添える。

```
  あなた   前月分の日報をまとめて
  ─────────────────────────────────────────────────────────────
  ⓘ 振り分け：タスク「日報」を流用できます（ローカル判定 0.82）
     [タスクを開く]  [そのまま会話で実行]
  ─────────────────────────────────────────────────────────────
  ▸ 入力欄には本文が残っている
```

`answer` / `converse` / 決めず のときは、既存の実行情報に 1 行増えるだけ。

```
  ⓘ 自動選択：claude / sonnet-5   選択方法：ローカル判定
  ⓘ 振り分け：答えるだけ（実行しない）
  ⓘ スキル：api-designer（判定で選択 0.71）
```

`routine` が yes のとき、応答の末尾に 1 行。

```
  ⓘ 繰り返せる依頼です。••• → この作業を定型化
```

実装後は `xvfb-run -a node --test test/electron-smoke.test.js` で撮って見てから出す（CLAUDE.md 6）。
`ui-consistency.test.js` に「実行設定の `<select>` は `<label>` 直下」の既存規則がそのまま効く。

## 6. 測ってから決めること

judge の初回実測（09-20）は「問いの形を決めてからでないと確度の使いどころは測れない」で終わった。振り分けも
同じ順で行く。

1. **標本**: 自分の会話記録（`store.listSessions`）から依頼 40 件を抜き、handling を手で付ける
   （answer / converse / task / flow）。task / flow は実在する `.statemachine` と `.agents/workflows`
   のあるリポジトリで。`readout_eval.py` のセルとして `RT1`（handling）`RT2`（task 選択）`RT3`（skill boolean）
   を足し、fake / replay も同じ schema で残す。
2. **見るもの**: `coverage` の分布（0.8 未満なら状態の並べ方を直す）、`handling` の信頼度図、
   hold 下限 0.7 / 0.75 / 0.85 で「止めた件数」と「止めて正しかった率」。止めるほうの誤りは人の手数に
   直結するので、こちらを厳しく取る。
3. **費用**: 候補 0 件 / 8 件 / 20 件で壁時計を測る。3 秒を超えるなら候補の上限を下げる。

既定値（0.6 / 0.75）は §6.2 の実測で確かめた。標本 40 件なので「据え置く根拠が出た」までで、
確定ではない。

### 6.2 段 2 の実測（2026-09-21、この mac の gemma4:e4b、標本 40 件 × 4 族 = 160 セル）

標本は `tools/agent-tools/eval/data/route/corpus.json`（正解は手で付けた。候補はタスク 8 /
ワークフロー 3 / スキル 6 の固定集合で、app が絞る前の候補が多い側）。run は
`tools/agent-tools/eval/results/archive/20260921-route-calibration-real/`。160 セル全部が
logprobs で読め、coverage は p10 0.999（下限 0.8 を割る問いは 0）。360 問の Brier 0.11、ECE 0.017。

| 族 | 正答 | 確度 0.6 以上 | 0.75 以上 | 0.9 以上 |
|---|---|---|---|---|
| RT1 handling | 32/40 | 29/36 | 24/29 | 20/22 |
| RT2 task（流用先） | 35/40 | 33/38 | 31/34 | 31/33 |
| RT3 skill（boolean 240 問） | 39/40 セル | yes 9 件中 8 が正解、見落とし 0（p_yes の下限は 0.6〜0.95 で結果が動かない） | | |
| RT4 routine | 31/40 | 26/31 | 15/18 | 7/8 |

hold の掃引（RT1 と RT2 を依頼ごとに突き合わせ。正解が task の依頼は 10 件）:

| hold 下限 | 止めた | 正しい | 誤って止めた | 止め損ね |
|---|---|---|---|---|
| 0.6 | 10 | 9 | 1 | 1 |
| 0.7 | 9 | 8 | 1 | 2 |
| **0.75** | 8 | 8 | 0 | 2 |
| 0.9 | 8 | 8 | 0 | 2 |

壁時計（依頼 1 件、warm。cold は初回だけ +3〜5 秒）:

| 候補 | 問い | tokens_in | 壁時計 |
|---|---|---|---|
| 0 件 | 2 | 515 | 3.3 秒 |
| 8 件 | 6 | 3.8k | 4.0 秒 |
| 20 件 | 15 | 11k | 7.2 秒 |

決めたこと。

- `route.hold_min_confidence` は **0.75 のまま**。0.7 以下で「誤って止める」が 1 件出て、0.75 で消える。
  止め損ねの 2 件は会話で実行されるだけで害が小さい。
- `route.min_confidence` は **0.6 のまま**（select と同じ）。上げても handling の正答率は 80% → 83%
  （0.75）→ 91%（0.9）と緩やかで、答えない件数のほうが速く増える。
- app の候補上限（8 / 8 / 6）は据え置き。上限いっぱいでも 7 秒台で、30 秒の打ち切りに余裕がある。

残っている癖。handling の誤り 8 件のうち、**確度が高いのに違う** のが 3 件ある: n13
「OpenAPI を設計して実装まで進めて」→ answer 0.83、n38「3 観点でこの PR を確認して」→ answer
0.94（正解 flow）、n24「PR の説明文を書いて gh で PR を作って」→ task 0.93。answer への誤りは
そのターンが読み取り専用で走るので、人が「そのまま会話で実行」で送り直す手数になる（40 件中 2 件）。
下限では除けないので、次に手を入れるなら問いの文（「実行や変更を頼んでいれば converse」を明示）
か、answer だけ別の下限を持つかの 2 択。routine は 31/40 で弱いが、出すのは案内 1 行なので
下限 0.6 のまま様子を見る。標本は 1 人が書いた 40 件で、実際の会話記録から抜いた標本で引き直す
のが次。

### 6.1 段 0 の煙試験（2026-09-21、この mac の gemma4:e4b、候補はタスク 2・ワークフロー 1・スキル 2）

標本 3 件だけ。しきい値を決める材料ではなく、形が動くことと、較正前の癖を見るためのもの。

| 依頼 | handling | 備考 |
|---|---|---|
| 前月分の日報をまとめて | 決めず（converse 0.57、task は下位） | 候補タスク「日報」を指さなかった。状態の並べ方（タスクの説明が薄い）か、問いの文かは未切り分け |
| この関数は何をしている？説明だけでいい | answer 0.89 | 期待どおり |
| ユーザー API の OpenAPI を設計して実装まで進めて | answer 0.73 | converse が正しい。skills は api-designer 0.9998 / self-checking 0.81 で期待どおり |

6 問で tokens_in 約 2.8k、壁時計 13 秒（1 問あたり 2 秒強。接頭辞キャッシュは効いている）。
`handling` は answer に寄る癖があり、`route.min_confidence` 0.6 では 3 件中 1 件が決めずに
倒れた。標本 40 件を作ってから問いの文と下限を動かす（上の 1〜3）。app 側（段 1）は
この癖を前提に「決めなければ従来」で組んであるので、先に配線しても壊れない。

## 7. 採らなかった案

- **クラウド CLI に「分類して JSON で返せ」と生成させる**: 判断 1 回に数百トークン、JSON の壊れ、確度が
  無い。judge 設計 §1 の表そのもの。振り分けは選択肢が先に決まっている判断なので、分布を読む形が合う。
- **judge に新しいタスクやワークフローを作らせて、そのまま実行する**: 定義を書くのは生成で、judge の
  仕事ではない。作成は費用と副作用があり、ADR-4「実行のボタンはモデルの外」に反する。既存の
  「この作業を定型化」への 1 行に留める。
- **skill-selector スキル（カタログ全文を LLM に読ませる）を毎ターン**: 数千トークン。judge の boolean を
  候補 6 件に絞って訊けば、prefill 1 回と 24 トークンで済む。
- **app が `agent-herd judge --questions` を直接叩く**: agent-tools の改修が 0 になるが、jev 段が
  `modelselect` の中にあって使えず、問いの組み立てが JS に写って eval が同じプロンプトを測れない。
  「判断は agent-tools、候補と検証は app」の分業（選択の設計）を崩さない。
- **送信前にプレビューを出して人に選ばせる**: 毎回クリックが 1 つ増える。judge が止めるのは hold 下限を
  超えた `task` / `flow` だけで、そのときだけ「そのまま会話で実行」で戻れれば足りる。
- **`handling` と `skill` と `routine` を 1 問にまとめる**: 多基準を 1 問で訊かない（judge 設計 §5）。
- **`select` を拡張して振り分けも返す**: §2.5。終了コードの意味と呼ぶ順序が違う。段の実装だけ共有する。
- **`judge` CLI に jev 段を足して `route` の代わりにする**: `judge` は「状態 + 問い → 答え」の素の口で、
  jev への振り替えや確度下限の設定を持たせると `select` と同じ段の実装が 2 か所になる。`judge` は
  そのまま、段は `modelselect` 側の 1 実装に寄せる。

## 8. 進め方

| 段 | 何を | 受入 |
|---|---|---|
| 0 | **済（2026-09-21）。** `modelselect` の段の試行を `ask_stages` に切り出し（`select` の出力と終了コードは不変。既存 916 件で確認）、`agentcore/route.py` + `agent-herd route` + `herdconfig` の 2 鍵。テストは `judge.evaluate` の `request` 差し替えで ollama 無し（`tests/test_route.py`）。仕様書 §5.8 と README。候補 1 件の流用先は judge の choice が 2 択以上を要るので boolean で訊く（実装で判明） | `python -m unittest` が通り、`agent-herd route --candidates x.json < 依頼` が JSON と終了コードを返す |
| 1 | **済（2026-09-21）。** app: `requestRouting.js`、`runTurn` の配線、`skillSelection.select` の `judged` 引数、実行設定の行、実行情報、案内は役割 `routing` の記録（履歴の再送・要約・未読には入らない）。`package.json` を 0.25.0、CHANGELOG。候補はファイルで渡す（説明文の引用符を argv に通さない）。案内の操作は他の応答と同じ右端の `.message-action` に置いた（§5 の図では ⓘ の下に描いていたが、既存の形を借りる） | `request-routing.test.js`（ENOENT・旧版・確度不足で従来に倒れる）、electron-smoke で案内の 2 操作と「依頼の扱い」の行を確認、スクリーンショットで目視 |
| 2 | **済（2026-09-21）。** §6.2 の実測。標本 `eval/data/route/corpus.json`、セル `RT1〜RT4`（`route_cells.py`、readout_eval に族名で名指し）、hold の掃引は `route_cells.py --hold-sweep`。既定 0.6 / 0.75 を据え置き、README の「置き値」を「標本 40 件で確認」に改めた | `RT1〜RT4` の `--calibration` 出力が eval README と archive に載る |
| 3 | **済（2026-09-21、agent-app 0.27.0）。** 流用時の入力値: 日付の語（前月 / 今月 / 昨日 / 今日）は決定的に `@date:*` へ、残りのキーだけ `agent-herd --purpose extract`（ollama の json profile。`decide` は候補の選別の契約で、キー → 値の転記には合わないので使わない）に「依頼文の言葉をそのまま」写させ、宣言にあるキーの文字列だけ機械が受ける。案内に「入力：…」の 1 行、「タスクを開く」で概要の入力欄へ（今回の値として前回の値より優先）。ワークフローの入力は対象外。タスク画面の会話は §9 のとおり対象外のまま | `request-routing.test.js` に抽出 3 件、electron-smoke で案内の行 |

非目標: 共有の依頼（SHARED_POLICY）の振り分け、agent-flow の工程内でのスキル選択、agent-project の
バックログ投入の振り分け、タスクの自動実行。

## 9. 前提と読み替え

- 「統合問い合わせ画面」は会話画面の入力欄（メッセージ / 端末操作 / 共有に依頼を切り替える 1 つの入口）と
  読んだ。別の画面を指すなら、§3 の配線先が `runTurn` から変わるだけで §2 は同じ。
- タスク画面の中の会話（作成・編集）は対象外。あそこは `/sm` の作成モードで、振り分ける先が無い。
