# pair-align 設計 — 実装と設計書の 2 リポジトリを交互に揃えるステートマシン

- 実装: `tools/pair-align/`（配布物は `machine/`、置き先では `.statemachine/pair_align/`）
- 使い方: [`tools/pair-align/README.md`](../../tools/pair-align/README.md)

## 1. 目的と前提

実装と設計書が別リポジトリ（別フォルダ）にあり、片方だけが直されてもう片方が置き去りになる。
pair-align は、**片方を変える前に相手を読み、その読みを前提・制約・自由に分けて意図と突き合わせる**ことで、
両者を交互に揃える。

- 同じ定義を両リポジトリに置く（対称）。違いは `pair.json` の `side` と `pair_path` だけ
- 相手のパスは手で設定する（自動発見しない）。相手のリポジトリには書き込まない（相手を直すときは依頼文を渡す）
- 相手側の検索には graphify の知識グラフを使えるときは使う（任意）

codd-gate が 1 リポジトリ内の doc↔code↔test のドリフトを受け入れ前に**止める**のに対し、
pair-align はリポジトリをまたぐ変更を**意図単位で受け渡す**。

## 2. 中心の考え方: 前提・制約・自由

意図（「こうしたい」という 1 件の変更）を実現する前に、相手の側（実装なら設計書、設計書なら実装）のうち
意図に関係する記述を読み、3 つに分ける。

| 分類 | 定義 | 意図との関係 |
|---|---|---|
| 前提 | 相手が「すでにそうなっている」と定めている事実・決定・既存の振る舞い・用語 | 崩すなら MISFIT |
| 制約 | 相手が「こうしなければならない / してはならない」と課している条件 | 破るなら MISFIT |
| 自由 | 相手が決めていない・任せている部分 | こちらで決めてよい。決めたことは後で相手へ伝える候補 |

- **合う（FIT）** → 自分のリポジトリをそのまま直す
- **合わない（MISFIT）** → 利用者に確認する。「相手を直す」なら相手の前提・制約を変える依頼を出し、
  相手が反映したら**波及**として自分を直す。「意図を直す」なら読み直す。「やめる」なら取り下げる

分類は非対称ではない。実装側から設計書を読めば「設計書が定めている前提・制約」、設計書側から実装を読めば
「実装が既に持っている前提（既存の振る舞い・データの形）と制約（互換性・性能・依存）」になる。

分類の各項目には相手のリポジトリでの根拠（相対パス）を付ける。`verify-reading` が実在しないパスを落とす——
読んでいないものを「前提」と書かせないための機械の歯止めである。

## 3. 構成

| 層 | 担うもの | 実体 |
|---|---|---|
| ステートマシン | 手順と分岐 | `workflow.yaml`（statemachine-use の定義） |
| アクション | 判断（分け方・合うか・どう直すか・どう頼むか）と、分類・確認・依頼文の執筆 | `actions/*.md`（LLM） |
| 下請けスクリプト | どこから始めるか、差分の収集、相手側の検索、書いたものの検査、コミットと合図の行、控え | `pair_align.py`（python3 + git のみ） |

statemachine-use の設計原則に合わせ、**測れることはスクリプトが測る**。分岐はすべて
`condition_rule`（スクリプトかアクションの第 1 行）か `check_ok`（検査の終了コード）で決まり、LLM の
YES/NO 評価は使わない。LLM が 1 語で決めるのは judge_fit の `FIT` / `MISFIT` と assess の `NEEDED` / `NOT_NEEDED` だけ。

### 3.1 ステート

```
begin ─┬─ INTENT / INBOUND / RIPPLE / RESUME / REVISED ─→ read_pair ─[check]→ judge_fit
       │     judge_fit ─┬─ FIT ─→ change_self ─[check]→ commit_self ─┬─ COMMITTED_USER ─→ collect …
       │                │                                            └─ LINKED / UNCHANGED ─→ applied
       │                └─ MISFIT ─→ ask_user ─[check]→ pause ─→ awaiting_decision
       ├─ DECIDED_PAIR ─→ compose_request ─[check]→ record ─(RECORDED_REQUEST)→ waiting_pair
       ├─ AWAITING_DECISION ─→ awaiting_decision
       ├─ ABORTED ─→ aborted
       ├─ WAITING_PAIR ─→ waiting_pair
       └─ PROPAGATE ─→ collect ─┬─ INBOUND_PENDING ─→ inbound_pending
                                 ├─ NO_CHANGES ─→ no_changes
                                 └─ CHANGES ─→ assess ─┬─ NOT_NEEDED ─→ record_skip ─→ skipped
                                                        └─ NEEDED ─→ locate ─→ compose ─[check]→ record ─(RECORDED)→ done
```

| ステート | 成果物 | 成否の測り方 |
|---|---|---|
| begin | `.pair-align/work/{intent.md,current.json}`、意図の状態 | スクリプトの第 1 行 |
| read_pair | `.pair-align/work/reading.md`（`write:`） | `check: verify-reading`（見出し 3 つ・箇条書き・実在する根拠） |
| judge_fit | 判断 | `output_validator`（`FIT` / `MISFIT`） |
| change_self | 自分のリポジトリの変更 | `check: self-check`（`pair.json` の `check` を実行。無ければ素通り） |
| commit_self | コミット（依頼・波及なら合図の行つき） | スクリプトの第 1 行 |
| ask_user | `.pair-align/work/question.md`（`write:`） | `check: verify-question` |
| compose / compose_request | `.pair-align/work/prompt.md`（`write:`） | `check: verify-prompt` |
| record / record_skip | 送り箱 `.pair-align/outbox/<ID>.md` と基準点、意図を待ちへ | スクリプトの第 1 行 |

検査つきのステートは 2 回まで再投入し、尽きたら `escalate`（statemachine-use の既定）。

### 3.2 利用者の確認は「止まって、次の実行で答える」

statemachine-use には実行の途中で人の入力を待つ仕組みが無い（人に訊く値は `inputs:` で実行前に入れる）。
そこで確認は **終端で止まり、答えを入力「確認への答え」に入れて次の実行で再開する** 形にした。
止まる前に `pause` が意図・読んだ結果・確認を `.pair-align/session/<ID>/` に控え、次の `begin` がそれを作業フォルダへ戻す。
答えの語（相手を直す / 意図を直す / やめる）は画面に出す言葉そのままで受け取る（内部の綴りも受け付ける）。

## 4. 意図の状態

`state.json` に、進行中の意図を 1 件（`active`）と、相手の反映待ちの意図を何件か（`waiting`）持つ。

| 項目 | 値 |
|---|---|
| `origin` | `user`（利用者が入れた）/ `inbound`（相手から届いた依頼。`inbound_id` を持つ）/ `ripple`（相手を直してもらったあとの波及。`outbound_id` を持つ） |
| `phase` | `working` → `confirm`（答え待ち）→ `decided_pair`（相手への依頼を作成中）→ `waiting`（相手の反映待ち） |

`begin` の優先順位（上から最初に当たったもの）:

1. 答え待ちの意図 → 答えで進める（答えが無ければ `AWAITING_DECISION`）
2. 途中で止まった意図 → 続きから（`RESUME` / `DECIDED_PAIR`）
3. 相手から届いた未処理の依頼 → それを意図にする（`INBOUND`）。入力された意図があっても先にこちら
4. 反映待ちの意図のうち、相手が受け取ったもの → 波及（`RIPPLE`）
5. 入力された意図 → `INTENT`
6. 反映待ちが残っている → `WAITING_PAIR`
7. どれでもない → コミット済みの変更を伝える（`PROPAGATE`）

3 を 4・5 より先に置くのは、両側がそれぞれ反映待ちを抱えて互いを待つ（デッドロック）のを避けるため。
待ちは `waiting` に置いたまま、届いた依頼を先に片付けられる。

## 5. 交互の規律（往復を止める仕組み）

素朴に両側で同じマシンを回すと、A の変更 → B への依頼 → B の反映 → B の変更として A への依頼 → …と往復が止まらない。

1. **反映コミットの合図** — 依頼文は ID（`<side>-<HEAD の短縮 SHA>` か、相手を直す依頼なら `<side>-<意図の ID>`）を持つ。
   `commit` は、届いた依頼の反映に `Pair-Align: <届いた ID>`、波及の反映に `Pair-Align: <自分が出した ID>` の行を付ける。
   collect はこの行を持つコミットを「自分の変更」から外す。利用者の意図のコミットには付けない（相手へ伝える候補になる）
2. **受け取りが先** — 届いた未処理の依頼があるうちは、自分の変更を送らない（`begin` の 3、collect の `INBOUND_PENDING`）

受け取り済みの判定は「自分の git 履歴（`--all`）の合図の行」＋「`state.json` の `acked`」。反映して変更が無かった依頼
（`UNCHANGED_LINKED`）と、確認で「やめる」を選んだ依頼は `acked` に入れて閉じる。依頼を出した側はそれも「受け取られた」と
みなして波及に進む——相手が直さなかったなら、読み直した結果はまた MISFIT になり、もう一度利用者に確かめる。

## 6. 変更の範囲と基準点（伝えるとき）

- 見るのは `基準点..HEAD` のコミット。コミットしていない変更は含めない（依頼 ID がコミットに結び付かず、
  次回また同じ変更が送られるため）。含めなかったことは出力に残す
- 基準点は `record` / `record --skip` / `NO_CHANGES` で HEAD へ進む（`--since` 指定時の `NO_CHANGES` は進めない）
- 初回（基準点なし）は `HEAD~1..HEAD`。履歴全体を相手へ流さない
- `.pair-align/` と `.statemachine/pair_align/` の変更は数えない（マシン自身の設置・更新は依頼にしない）
- 差分は 60,000 文字で切る。全体は `git show` で見られる旨を残す
- 利用者の意図を FIT で直した直後は、読んだ結果（`reading.md`）が残っている。assess はその**自由**の項目でこちらが
  決めたことも、相手へ伝える候補として見る

## 7. 相手側の検索と graphify

`locate` は検索語（read_pair では意図から、伝えるときは差分から拾った関数・クラス名・見出し・`用語`・ファイル名 + LLM が
選んだ語。最大 12）ごとに次を行い、`candidates.md` にまとめる。

1. `graphify` が PATH にあり、`<pair>/graphify-out/graph.json` があれば `graphify query <語> --graph … --budget 600`。
   出力の `src=` からファイルを候補に足す。`--refresh` で先に `graphify update <pair>`（AST のみ・LLM 不要）
2. 常に `git grep -F -i`（語ごと 3 件/ファイル、20 行まで）。設計書のようにグラフに入っていない文書を拾うため

グラフは任意の加速であり、無くても同じ出力の形で動く（`graphify: not-installed | no-graph | used | off`）。
LLM は候補の前後だけを開いて確かめる——相手のリポジトリ全体を読ませないことが graphify を使う理由である。
インストーラーは `.graphifyignore` に `.statemachine/pair_align/` を足す（実測: 足さないと小さなリポジトリで
ノードの 8 割がマシン自身になった）。

## 8. 書いたものの契約

| ファイル | 検査 |
|---|---|
| `reading.md` | `## 前提` `## 制約` `## 自由` がこの順、各見出しに箇条書き（無ければ「- なし」）、各項目に相手のリポジトリに実在するパス |
| `question.md` | `## 意図` `## ぶつかっている点` `## 相手を直す場合に頼むこと` `## 波及してこちらで直すこと` がこの順で中身あり |
| `prompt.md` | 変更元 / 変更の要約 / 反映してほしいこと / 対象の候補 / 完了の合図 がこの順で中身あり、`Pair-Align-Id: <ID>` がちょうど 1 行、完了の合図に `Pair-Align: <ID>`、`{{…}}` と `<!-- TODO` が残っていない |

見出しの検査は 1 つの関数（`sections`）で共通化し、`record` も `verify-prompt` と同じ関数で検査する。

## 9. やらないこと

- 相手のリポジトリへの書き込み・コミット（相手を直すときも依頼文まで）
- 相手のパスの自動発見（`pair.json` を手で直す）
- 3 つ以上のリポジトリの組（1 対 1 のみ）
- 常駐・監視（実行は人かエージェントが起こす）
- 実行の途中で人を待つこと（確認は終端で止まり、次の実行で答える）
