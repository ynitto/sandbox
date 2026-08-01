# agent-dashboard — 複数 PC でのバックログ成果物の検収フロー（レビュー〜マージ）検証

> 作成 2026-07-28 ／ 実装 `tools/agent-dashboard/` `tools/agent-project/` と突き合わせ済み
> 前提とする設計: [`agent-dashboard-design.md`](../designs/agent-dashboard-design.md)（§3.1 / §6）／
> [`agent-project-design.md`](../designs/agent-project-design.md)（設計判断 1・2・4、正準ループ S4-5）

## 1. 目的とスコープ

複数 PC で 1 つのバックログを分担する構成において、**バックログ成果物の検収**
（タスクが `review` に到達してから、成果ブランチがターゲットへマージされ done が確定するまで）
の推奨・想定される操作列とリポジトリデータの流れをここに確定し、現行実装がその流れに対して
**必要**（各ステップに実装とテストがある）かつ**充分**（流れの外に余計な書き込み経路がない）
であることを検証した。結論は §6 — **ドキュメントの矛盾 1 件（本 PR で修正）を除き、必要充分**。

スコープ外: 実行前レビュー（plan-review）、blocked の往復、milestone 検収。いずれも同じ
`needs/` ＋ `commands/` の契約に乗っており、検収固有の要素（MR・検証レポート・マージ）を持たない。

## 2. 前提構成（登場ノードと持ち物）

| ノード | 持ち物 | 役割 |
|---|---|---|
| PC-E（実行ノード） | 常駐体 `agent-project serve`・状態リポジトリ clone・成果物リポジトリ・**フォージ書込トークン**（環境変数） | タスク実行・verify・MR 作成・フォージ決着の取り込み・マージ実行 |
| PC-R（検収 PC） | 常駐体 serve（host.yaml に同じプロジェクトを宣言）・状態リポジトリ clone・agent-dashboard | 検収カードの閲覧・チームコメント・判断の投函 |
| フォージ（GitLab） | MR（`ap/<task-id>` → target） | 差分レビューの正・決着シグナルの発生源 |
| 状態リポジトリ origin | `backlog/ needs/ verifications/ reviews/ assignments/ commands/ decisions/ archive/ DELIVERY.md` | 全 PC の同期路（書き手は各 PC の常駐体のみ） |
| 成果物リポジトリ origin | `ap/<task-id>` ブランチ・target ブランチ | 成果の実体（worker が push・常駐体がマージ） |

**dashboard を置く PC には常駐体が必須**である。プロジェクトの発見（`engine/status.json` の
`children[].root` 一本・設計 §3.3）と、投函したファイルを共有先へ届ける push（同期の書き手は
常駐体だけ・設計 §3.1）の両方を常駐体が担うため。常駐体を置けない PC はフォージ決着（§3 の
経路 a）だけで検収に参加する——こちらはブラウザだけで完結する。

## 3. 正準フロー（推奨・想定する操作列）

### 3-0. 検収待ちが立つまで（PC-E・自動）

1. worker（agent-flow）が成果を `ap/<task-id>` ブランチとして成果物リポジトリ origin へ push。
2. verify ゲート通過後、`_settle_review`（`mr.py:554`）が status を `review` にし、
   GitLab 設定時は **MR を冪等作成**（`ensure_task_mr`・`mr.py:129,585`）。
3. `needs/<task-id>.md` を書き出す（`needs.py:260-267`）。frontmatter に
   `mr-url` / `delivery`（複数リポジトリの構造化ペイロード）/ `verification`
   （基準×証跡の検証レポート要約）/ `risk` を載せる。
4. 常駐体が状態リポジトリへ push（間隔律速・rebase リトライ・CAS）。

**MR を作る主体は常駐体**。worker（トークンが信頼境界外へ漏れる）にも dashboard
（書かない原則・開かないと MR ができない）にも作らせない（agent-project 設計・正準ループ S4）。

### 3-1. 気づく（PC-R）

- 常駐体の pull で `needs/` が届き、dashboard が新規要対応を **OS 通知・バッジ・フラッシュ**で
  知らせる（`needsCount` の増分検知）。カードには待ち時間バッジが付き、SLA（既定 24h）超は赤。
  停滞の長い順に並び、既定選択も最も停滞したカードになる。

### 3-2. レビューする（PC-R・複数人）

1. 要対応タブの検収カード（kind=review）を開く。材料は「**差分 + 基準 + 証跡**」:
   - **検証表**（基準ごとの pass/fail/検証不能と証跡。証跡が空のまま pass の基準は警告表示）
   - **MR リンク**（MR があるあいだカード内で差分は開かない——差分レビューの正は MR 一本）
   - MR を持たないタスクに限り、この PC の host.yaml `repos[]` で解決できたローカルクローンから
     差分を表示（解決できなければ理由を表示）
2. 差分の精読・行コメントは「レビューで開く」→ gitlab-review-viewer（またはブラウザ）で MR 上で行う。
3. チームの指摘は各メンバーが**レビューコメント**（`reviews/<task-id>/*.json`・1 コメント =
   1 ファイル）に残す。別 PC からの同時投稿もファイル名が別なので同期で自然にマージされる。
4. 監視担当（`assignments.json`）がコメントを整理（編集・削除）し、決着の判断をまとめる。

コメント層と判断層は分離されている——コメントは自動では決着に影響せず、判断は次の 3-3 だけが下す。

### 3-3. 決着させる（4 経路 + 例外 2 つ）

| 経路 | 操作 | 届き方 |
|---|---|---|
| **a. フォージ決着（推奨）** | MR を**マージ**＝承認／**未マージクローズ**＝却下／**changes-requested**（ラベルまたはレビュー状態）＝差し戻し | PC-E の `poll_task_mrs`（`loop.py:377`）が決定的シグナルだけを拾う。コメントのみでは何もしない。到達不能時は決着しない（回線断で成果を却下しない） |
| **b. 承認（dashboard）** | カードの「承認して完了にする」 | `commands/` へ `{"command":"approve","complete":true}` を投函（`needs.js:1996`・`actions.js:292`）→ PC-R 常駐体が push → PC-E が ingest |
| **c. 差し戻し（dashboard）** | 修正方針を記入（必須）して確定 | `needs/<id>.md` の Decision Outcome + `[x]`（`ingest_feedback` の正規ルート）。**同一ブランチ**（`gate_branch`）に次試行が積まれる |
| **d. 却下（dashboard）** | ✕ 却下 | `commands/reject` → MR クローズ＋ブランチ削除（`needs.py:734`）＋ archive 退避＋墓標＋決定記録 |

例外: **✎ revise**（受入基準 `acceptance` の項目編集を含む修正指示。doing 中も送れる）と
**🗑 削除**（記録を残さない物理削除。実行中は拒否）。どちらも検収の決着ではなく、票を作り直す
・畳む側の操作。

複数 PC からの同時決着は git の fast-forward push を CAS とする `state_transaction` が調停し、
承認の二重取り込みは冪等（target に取り込み済みなら成功として畳む・`mr.py:250-252`）。
dashboard 側も送信済みマーカー（ファイルパス + mtime 照合）で二重送信を防ぐ。

### 3-4. マージと done 確定（PC-E・自動）

1. approve（complete=true）を ingest → `finalize_task_delivery`（`commands.py:14`・`mr.py:219`）。
2. **MR があれば API でマージ**（`should_remove_source_branch` 付き・`mr.py:212`）。
   コンフリクト・パイプライン失敗時は**マージせず** MR に差し戻しコメントを残して review を維持
   （成果未反映のまま done にしない）。差分なし MR はクローズで決着。
3. MR が無ければ origin の target に対する fast-forward push（分岐していれば一時 worktree で
   無競合マージだけを試し、競合時は理由付きで review 維持）。
4. done 確定 → `archive/<id>.md`（納品書）・`DELIVERY.md`・`decisions/`（DR）→ 状態リポジトリ push。
5. PC-R は次の pull で done と受理レシート（`commands/processed/`）を表示し、カードが畳まれる。

### 3-5. リポジトリデータの流れ（まとめ）

```
[PC-E 実行ノード]                                  [フォージ GitLab]
 worker ── push ap/<id> ──────────▶ 成果物リポジトリ origin
 常駐体 ── MR 冪等作成 ───────────────────────────▶ MR (ap/<id> → target)
 常駐体 ── needs/<id>.md（mr-url・検証表・delivery）
 常駐体 ── state push ─────────────▶ 状態リポジトリ origin
                                          │ pull / push（各 PC の常駐体のみ・間隔律速）
[PC-R 検収 PC]                            ▼
 dashboard は読むだけ（engine/status.json → clone のファイル）
 人 ── reviews/ コメント・assignments.json 担当（ローカル書き込み → 常駐体が push）
 人 ── 決着:
   a) フォージで merge / close / changes-requested ──▶ PC-E poll_task_mrs が取り込み
   b) commands/approve{complete} ・ needs [x] ・ commands/reject（ローカル書き込み
      → PC-R 常駐体 push → PC-E ingest）
[PC-E] finalize_task_delivery ── MR マージ or ff-push ──▶ 成果物リポジトリ target
        archive / DELIVERY / decisions ──▶ 状態リポジトリ push ──▶ 全 PC へ
```

書き手の境界: **dashboard はファイルを置くだけ**（git・フォージへ書かない）。
**git を書くのは各 PC の常駐体だけ**。**フォージを書くのはトークンを持つ実行ノードの常駐体だけ**。

## 4. 必要性の検証（ステップ × 実装 × テスト）

| # | ステップ | 実装 | 固定するテスト |
|---|---|---|---|
| 1 | review 到達で MR 冪等作成・検収票書き出し | `mr.py:554-600`・`needs.py:45-67,260-267` | agent-project tests（1,174 件に含まれる） |
| 2 | カード表示（基準×証跡・MR 一本・MR 無しはローカルクローン差分） | `project.js:404-836`・`git.js diffRange` | `delivery-review.test.js`（MR があれば差分ペインを出さない:262／証跡なし pass 警告:272／`resolveDiffRoot` S4-e:232,248） |
| 3 | 票が失われても検収導線が残る（合成票） | `project.js:912-1002` | `hitl-review.test.js:129,145,164,186` |
| 4 | チームレビュー（コメント・担当） | `actions.js:157-231` | `review-comments.test.js`（状態ファイルに触れない:75／同時投稿の共存:52）・`owner-assign.test.js` |
| 5 | 気づき（通知・SLA） | `notify.js`・renderer | `needs-notify.test.js`・`needs-sla.test.js` |
| 6 | 承認の完了意図を明示（complete フラグ） | `needs.js:1991-2003`・`actions.js:292`・`commands.py:61,123-146,926` | `hitl-review.test.js`・agent-project tests |
| 7 | ingest → 自動マージ／競合時は review 維持 | `commands.py:14`・`mr.py:169-297` | agent-project tests |
| 8 | フォージ決着（決定的シグナル限定・到達不能は現状維持） | `mr.py:300-427`・`loop.py:377`・`configfile.py:204`（`remote_review: settle` 既定） | agent-project tests |
| 9 | 差し戻し＝同一ブランチ次試行 | `actions.js:54-96`・`mr.py:565`（`gate_branch`） | `hitl-review.test.js:164` |
| 10 | 却下＝MR クローズ＋ブランチ削除＋墓標 | `needs.py:734`・`mr.py:428` | agent-project tests |
| 11 | 受理レシート・二重送信防止 | `project.js:881-902`・`commands.py _write_command_receipt` | `hitl-review.test.js` ほか |
| 12 | 書き込み境界（viewer は git・フォージ・タスク状態に書かない） | `git.js`（読み取りのみ）・`gitlab.js`（読み取りのみ） | `no-git-writes.test.js`（範囲検査つき:56-167） |
| 13 | 複数 PC 調停（CAS・fencing・静的割当） | `coordination.py`・`stategit.py` | agent-project `test_state_git.py`・`test_coordination.py` |

検証方法: 上記の実装箇所とテスト題を直接読み、設計書（agent-dashboard §3.1/§6、agent-project
設計判断 1/2/4・正準ループ）の記述と突き合わせた。`npm test`（63 ファイル）は本 PR の変更が
ドキュメントのみのため実行対象に変化なし。

## 5. 充分性の検証（余計な経路が無いこと）

- 決着経路はフォージの決定的シグナルと dashboard のボタンの **2 系統だけ**で、どちらも同じ
  approve / reject / revise 契約へ合流する。コメント本文のキーワード推定は検収の決着に使わない。
- dashboard の書き込みは needs 記入・inbox・commands・viewer サイドカー（assignments /
  reviews）・notes・上位入力（charter / policy / repos）・ゴミ箱移動に閉じ、いずれも
  タスク状態（`backlog/*.md` の status・`archive/`・`project.json`）に触れない。
  `no-git-writes.test.js` が feature 追加時も自動で範囲に入る形で固定している。
- 過去に存在した危険経路——viewer の pull/push、worker の `/tmp` worktree 前提の diff、
  CLI で本体を起こす経路、板への直接書き込み——はいずれも撤去済みで、テストが再導入を塞ぐ。

## 6. 発見事項

**F1（修正済み・本 PR）: README が「エンジンの居ない閲覧専用 PC は clone を登録する」と
案内していたが、その経路は存在しない。** 発見は `engine/status.json` 一本（画面からの登録は
撤去済み・`discover-engine.test.js`）であり、また常駐体が居なければ投函を push する書き手も
居ないため、案内どおりにしても「一覧に出ない・押しても届かない」構成になる。README を
「検収に参加する PC にも常駐体を置く／置けない PC はフォージ決着で参加する」に改めた
（役割 `viewer` の説明も同様に補正）。

**F2（既知の運用制約・実装は意図どおり）: 「同期のみ（検収専用）ノード」の一級の語彙が無い。**
host.yaml にプロジェクトを宣言した PC は静的割当（`allocate_distributed_tasks`）の対象になり、
`concurrency` は 0 に倒せない（`configfile.py:859` で `max(1, …)`）。検収 PC に実行が
割り当てられるのを避けたい場合の現実的な運用は (1) フォージ決着を主経路にする、
(2) タスクの `- node:` を手動指定する、(3) `availability` で稼働時間帯を絞る、のいずれか。
一級の宣言（例: `projects[].execute: false`）は将来の改善候補として残す（本検証では
「推奨経路 a で回避できる」ため必要条件とはしない）。

**F3（設計判断の再確認）: フォージ決着は GitLab のみ。** GitHub 等の未対応リモートはボタン
決着だけの従来運用に倒れる（`_forge_kind`）。複数 PC 検収の推奨経路 a を使うには状態・成果物
リポジトリのフォージが GitLab であることが前提になる——構成選定時の注意点として明記する。

**F4（意図どおりであることの確認）: `reviews/` のコメントは決着に自動注入されない。**
コメント層と判断層の分離は設計どおりで、監視担当が整理して差し戻し文面（needs 記入）へ
載せるのが想定運用。フォージ側の未解決コメントだけは changes-requested 決着時に feedback へ
自動注入される——「フォージでレビューするならフォージだけで完結する」を保つ非対称であり、妥当。

## 7. 結論

レビューからマージまでの推奨操作列（§3）に対し、実装は各ステップに実体とテストを持ち
（§4）、流れの外の書き込み経路を持たない（§5）。**必要充分と判定する**。ただし利用手順の
文書に実装と矛盾する案内が 1 件あり（F1）、本 PR で修正した。F2・F3 は構成選定・運用の
注意点として本書に残す。
