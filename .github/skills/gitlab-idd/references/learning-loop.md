# 学習ループの記録と評価

## 目次

- [学習イベントのマーカー](#学習イベントのマーカー)
- [差し戻し原因](#差し戻し原因)
- [サイクルと結果の定義](#サイクルと結果の定義)
- [役割分担](#役割分担)
- [誤発火対策](#誤発火対策)
- [評価の判定表](#評価の判定表)
- [取り消し手順](#取り消し手順)
- [取り消しの限界](#取り消しの限界)

## 学習イベントのマーカー

学習ループの記録は、人が読む表と機械が読むマーカーを 1 つのコメントに入れる。マーカーは gl.py だけが生成し、手書きしない。

```
<!-- gitlab-idd:ltm:v1 {JSON} -->
```

- JSON は `json.dumps(obj, ensure_ascii=True, separators=(",", ":"))` の後、`--` を `\u002d\u002d` に、`>` を `\u003e` に置換する（HTML コメントを壊さず、JSON としては等価）。
- 解析の正規表現: `<!--\s*gitlab-idd:ltm:v1\s+(\{.*?\})\s*-->`（DOTALL）。JSON として読めないものは数えるだけで落とさない（トレイルの `malformed`）。
- 共通フィールド: `v`（1）、`event`、`node`（`get_node_id()`）、`cycle`、`at`（UTC ISO8601）。

| event | 誰が | 追加フィールド |
|---|---|---|
| `recall` | ワーカー（`ltm-recall`） | `trigger`: `start`/`rework`、`queries`: [str]、`failed_queries`: [str]（失敗時のみ）、`status`: `ok`/`empty`/`unavailable`/`error`、`memories`: [{`id`,`title`,`score`、任意の `summary`}]、`wiki_status`: 同じ 4 値、`wiki`: [{`id`,`title`,`score`}] |
| `usage` | ワーカー（`ltm-log`） | `memories`: [{`id`,`use`: `applied`/`referenced`/`unused`,`how`}] |
| `save` | ワーカー（`ltm-save --kind execution`） | `file`（保存先のファイル名） |
| `retro` | ワーカー／リクエスター（`ltm-save --kind retro`） | `trigger`: `rework`/`clarification`/`non-convergence`、`causes`: [slug]、`target_cycle`、`learning`（結論 1 文）、`file` |
| `rate` | ワーカー（`ltm-rate`） | `outcome`: `approved`/`rework`、`target_cycle`、`applied`: [{`id`,`rating`: `good`/`correction`,`note`,`result`: `ok`/`not-found`/`error`}]、`held`: [{`id`,`reason`}]、`skipped`: [{`id`,`reason`}] |
| `failure` | 誰でも（各コマンドが自動、または `ltm-log`） | `step`: `recall`/`save`/`retro`/`rate`/`sprint-review`、`exit_code`、`attempts`、`stderr_tail`（末尾 500 字）、`lost`（保存失敗時のみ。保存できなかった学びの `title` と `conclusion`） |

## 差し戻し原因

リクエスターがマーカーを手で書く。

既存マーカーと同じ単純な形にする。

```
<!-- gitlab-idd:rework-cause:spec-misread,quality -->
```

正規表現: `<!--\s*gitlab-idd:rework-cause:([a-z\-,]+)\s*-->`

| slug | 意味 | 記憶への correction |
|---|---|---|
| `ac-unmet` | 受け入れ条件の実装漏れ | 不可 |
| `spec-misread` | 仕様・受け入れ条件の読み違い | 可 |
| `wrong-approach` | 方式・設計の誤り | 可 |
| `quality` | テスト・品質・セキュリティの不足 | 可 |
| `env` | 環境・CI・ツール起因 | 可 |
| `scope` | スコープ逸脱 | 不可 |
| `design-doc` | 設計記録の不足 | 不可 |
| `issue-defect` | イシュー記述側の不備（リクエスター起因） | 不可 |

## サイクルと結果の定義

- **差し戻しコメント**: `rework-cause` マーカーを含むコメント、または本文が `## ❌ 差し戻し` で始まるコメント（マーカー導入前のものを拾うため）。
- **cycle**: そのイベントより前にある差し戻しコメントの数 + 1。gl.py が数え、ワーカーは渡さない。
- **cycle N の結果**:
  - `rework`: N 番目の差し戻しコメントがある。
  - `approved`: cycle N が最終サイクルで、同じノードの最後の `usage` イベントより後に `gitlab-idd:requester-approved:` マーカーがある。
  - `closed-unknown`: 承認マーカーなしでクローズ済み。rate の対象にしない。
  - `pending`: 上のどれでもない。

`closed-unknown` を承認扱いにしないのは、requester-review のステップ 0 が「MR がマージされずに閉じられた」場合もイシューを閉じるため。

原因マーカーがない旧形式では、コメント本文の先頭が `## ❌ 差し戻し` で始まる場合だけ差し戻しとして数える。見出しの前に空行や別の文がある場合や、コメントなしで直接リオープンした場合は検出できない。

## 役割分担

| 失敗イベント | リクエスター側 | ワーカー側 |
|---|---|---|
| 差し戻し／リオープン（4b） | 原因分類マーカーを付ける。`issue-defect` なら自分の学びを保存 | 再着手時のステップ 2-6a で原因を分類し、学びを保存し、評価を返す |
| 人による直接リオープン（旧形式の差し戻しコメントあり） | — | 2-6a を原因 `unknown` で実行。学びは保存、評価は全件保留 |
| `needs-clarification` | C-5 で学びを保存 | その場では何もしない。完了時に 5-5b の起動条件になる |
| 5 回上限の非収束（4-4） | — | その場で学びを保存。評価は返さない |

## 誤発火対策

`ltm-rate` は評価計画を検証し、入力規則に違反した場合は終了コード 2 を返す。上限を超えた correction は理由付きで保留にする。

| # | 規則 | 防ぐ誤発火 |
|---|---|---|
| 1 | **対象の限定**: 評価できるのは、そのイシュー・そのサイクルで自ノードが recall し、`usage` で `applied` と記録した記憶だけ。計画では対象の全件を扱う | 検索に出ただけの記憶を巻き込む |
| 2 | **自動で使う評価は `good` と `correction` だけ**。`--bad` は自動では使わない。読んだだけ・関係なかった記憶には何も返さない | 「役に立たなかった」の乱発 |
| 3 | **correction の 2 つの鍵**: (a) 差し戻しコメントの原因分類に `spec-misread` / `wrong-approach` / `quality` / `env` のどれかがある、(b) 計画の項目に `evidence`（「…」・『…』・半角二重引用符で囲んだ、差し戻しコメント本文に存在する引用）と `note`（正しい内容）が両方ある。原因マーカーが無い差し戻しは (a) を満たさない | 実装漏れ・スコープ逸脱・イシュー側の不備で記憶を減点する |
| 4 | **上限**: 1 回の差し戻しにつき correction は 1 件まで。同じ記憶への correction は 1 イシューにつき 1 回まで。超えた分は保留 | 1 回の失敗で複数の記憶を連鎖的に減点する |
| 5 | **冪等**: 同じ（記憶・サイクル・ノード）への評価は 1 回だけ。`ok` と `not-found` を評価済みとする。`error` は再試行できる。評価済みの項目を再処理する計画では `skipped` に `reason: already-rated` を記録する | 再実行や精算の重複による二重加点・二重減点 |
| 6 | **参照回数は採用した記憶だけに加算**: `ltm-recall` は `--no-track`、`ltm-log --event usage` が `applied` の ID だけを追跡ありで読み直す | 必須化した検索のたびに、当たっただけの記憶の共有スコアが上がる |
| 7 | **承認の `good` は承認マーカーがあるときだけ**。マーカーなしのクローズは評価しない | マージされずに閉じた MR を成功として加点する |
| 8 | **初回の評価返しはコメントに残す**: 返した評価、保留、理由、実行ノード、取り消し方 | 人が追えない・戻せない |

## 評価の判定表

ワーカーは [ステップ 2-6a](worker-role.md#ステップ-2-6a-差し戻しの振り返り差し戻し再着手時のみ) の手順 3 でこの表を使う。

| 状況 | 評価 |
|---|---|
| 承認された。記憶を採用していた | `good`（`--auto`） |
| 差し戻された。指摘箇所が、記憶を採用した判断（`how` に書いた場所）ではない | なし（`none`） |
| 差し戻された。指摘箇所は採用した判断だが、記憶の内容は正しく、当てはめ方を誤った | なし（`none`）。誤りは振り返りの学びとして保存する |
| 差し戻された。指摘箇所は採用した判断で、**記憶の記述どおりに実装すると指摘された結果になる** | `correction`（規則 3 を満たす場合） |
| 上のどれか決めきれない | 保留（`hold`）。コメントに理由を書く |

correction の判定に使う問いは 1 つに絞る: 「この記憶を正しく読んで従った別のワーカーも、同じ差し戻しを受けるか」。Yes と言い切れて、差し戻しコメントの引用で示せるときだけ correction。

## 取り消し手順

1. 評価コメントに、誤判定だと判断した理由を返信する。
2. コメントに記載された評価実行ノードで、対象 ID の取り消しコマンドを実行する。

```sh
python "{ltm-use}/scripts/rate_memory.py" --id mem-20260303-001 --good --note "取り消し: #42 の評価は誤判定"
```

## 取り消しの限界

`rate_memory.py` に取り消しは無い。correction を `--good` で打ち消すと `user_rating` は戻るが、`correction_count` は 1 のまま残る（共有スコアで −5 点）。1 回の correction の影響は −15 点（評価 −10、修正 −5）で、うち 10 点分だけが戻せる。
