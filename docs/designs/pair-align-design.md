# pair-align 設計 — 実装と設計書の 2 リポジトリを交互に揃えるステートマシン

- 実装: `tools/pair-align/`（配布物は `machine/`、置き先では `.statemachine/pair_align/`）
- 使い方: [`tools/pair-align/README.md`](../../tools/pair-align/README.md)

## 1. 目的と前提

実装と設計書が別リポジトリ（別フォルダ）にあり、片方だけが直されてもう片方が置き去りになる。
pair-align は、**片方の変更からもう片方への依頼文（プロンプト）を作る**ことで、両者を交互に揃える。

- 同じ定義を両リポジトリに置く（対称）。違いは `pair.json` の `side` と `pair_path` だけ
- 相手のパスは手で設定する（自動発見しない）。相手のリポジトリには書き込まない
- 依頼文を**作るまで**が責務。反映は依頼を受けた側のエージェント（か人）が行う
- 相手側の検索には graphify の知識グラフを使えるときは使う（任意）

codd-gate が 1 リポジトリ内の doc↔code↔test のドリフトを受け入れ前に**止める**のに対し、
pair-align はリポジトリをまたいだドリフトを**依頼として受け渡す**。止めない・直さない。

## 2. 構成

| 層 | 担うもの | 実体 |
|---|---|---|
| ステートマシン | 手順と分岐 | `workflow.yaml`（statemachine-use の定義） |
| アクション | 判断（要るか・どこへ・どう頼むか）と依頼文の執筆 | `actions/*.md`（LLM） |
| 下請けスクリプト | 差分の収集、相手側の検索、依頼文の検査、控えと基準点 | `pair_align.py`（python3 + git のみ） |

statemachine-use の設計原則に合わせ、**測れることはスクリプトが測る**。分岐はすべて
`condition_rule`（スクリプトの第 1 行）か `check_ok`（検査の終了コード）で決まり、LLM の
YES/NO 評価は使わない。LLM が決めるのは assess の `NEEDED` / `NOT_NEEDED` と、locate・compose の中身だけ。

### 2.1 ステート

```
collect ─┬─ INBOUND_PENDING ─→ inbound_pending (terminal)
         ├─ NO_CHANGES ──────→ no_changes (terminal)
         └─ CHANGES ─→ assess ─┬─ NOT_NEEDED ─→ record_skip ─→ skipped (terminal)
                                └─ NEEDED ─→ locate ─→ compose ─[check_ok]→ record ─→ done (terminal)
```

| ステート | 成果物 | 成否の測り方 |
|---|---|---|
| collect | `.pair-align/work/{changes.md,current.json}` か `inbound.md` | スクリプトの第 1 行 |
| assess | 判断（`NEEDED` / `NOT_NEEDED` と理由） | `output_validator` |
| locate | `.pair-align/work/candidates.md`（スクリプト）と反映先の列挙（LLM） | `output_validator`（`FOUND` / `NEW`） |
| compose | `.pair-align/work/prompt.md`（`write:` で 1 ファイルに割付） | `check: pair_align.py verify-prompt`（2 回まで再投入、尽きたら escalate） |
| record / record_skip | 送り箱 `.pair-align/outbox/<ID>.md` と基準点 | スクリプトの第 1 行 |

## 3. 交互の規律（往復を止める仕組み）

素朴に両側で同じマシンを回すと、A の変更 → B への依頼 → B の反映コミット → B の変更として A への依頼 → …
と往復が止まらない。これを 2 つの規則で止める。

1. **反映コミットの合図** — 依頼文は ID（`<side>-<HEAD の短縮 SHA>`）を持ち、反映するコミットには
   `Pair-Align: <ID>` のトレーラー行を付けてもらう。collect はこの行を持つコミットを「自分の変更」から外す。
   反映と独自の変更を 1 コミットに混ぜると独自の分も外れるので、コミットは分ける（README に明記）。
2. **受け取りが先** — collect は最初に相手の送り箱（`<pair>/.pair-align/outbox/*.md`）を読み、
   自分の履歴に `Pair-Align: <ID>` が無く `ack` もしていない依頼があれば `INBOUND_PENDING` で止まる。
   両側が同時に未処理の食い違いを抱えて、依頼が交差する状態を作らない。`--ignore-inbound` は手動の逃げ道。

受け取り済みの判定は「自分の git 履歴（`--all`）のトレーラー」＋「`state.json` の `acked`」。
送り箱はファイルシステム越しに読むだけで、相手の git には触れない（コミットされていなくてよい）。

## 4. 変更の範囲と基準点

- 見るのは `基準点..HEAD` のコミット。コミットしていない変更は含めない（依頼 ID がコミットに結び付かず、
  次回また同じ変更が送られるため）。含めなかったことは出力に残す
- 基準点は `record` / `record --skip` / `NO_CHANGES` で HEAD へ進む（`--since` 指定時の `NO_CHANGES` は進めない）
- 初回（基準点なし）は `HEAD~1..HEAD`。履歴全体を相手へ流さない
- `.pair-align/` と `.statemachine/pair_align/` の変更は数えない（マシン自身の設置・更新は依頼にしない）
- 差分は 60,000 文字で切る。全体は `git show` で見られる旨を残す

## 5. 相手側の検索と graphify

`locate` は検索語（collect が差分から拾った関数・クラス名・見出し・`用語`・ファイル名 + LLM が選んだ語。
最大 12）ごとに次を行い、`candidates.md` にまとめる。

1. `graphify` が PATH にあり、`<pair>/graphify-out/graph.json` があれば `graphify query <語> --graph … --budget 600`。
   出力の `src=` からファイルを候補に足す。`--refresh` で先に `graphify update <pair>`（AST のみ・LLM 不要）
2. 常に `git grep -F -i`（語ごと 3 件/ファイル、20 行まで）。設計書のようにグラフに入っていない文書を拾うため

グラフは任意の加速であり、無くても同じ出力の形で動く（`graphify: not-installed | no-graph | used | off`）。
LLM は候補の前後だけを開いて確かめる——相手のリポジトリ全体を読ませないことが graphify を使う理由である。
インストーラーは `.graphifyignore` に `.statemachine/pair_align/` を足す（実測: 足さないと小さなリポジトリで
ノードの 8 割がマシン自身になった）。

## 6. 依頼文の契約

`templates/prompt.md` の見出し（変更元 / 変更の要約 / 反映してほしいこと / 対象の候補 / 完了の合図）が
この順ですべてあり中身が空でないこと、`Pair-Align-Id: <ID>` がちょうど 1 行、完了の合図に
`Pair-Align: <ID>`、`{{…}}` と `<!-- TODO` が残っていないこと。`verify-prompt`（compose の `check`）と
`record` が同じ関数で検査する。

## 7. やらないこと

- 相手のリポジトリへの書き込み・コミット（依頼文を作るまで）
- 相手のパスの自動発見（`pair.json` を手で直す）
- 3 つ以上のリポジトリの組（1 対 1 のみ）
- 常駐・監視（実行は人かエージェントが起こす。git hook に置くなら post-commit で `collect` だけを呼ぶ）
