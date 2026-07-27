# canary 後の積み残しと次のステップ（2026-07-27）

- 状態: 棚卸し（07-24〜07-26 の 15 プラン文書の積み残しを、実装と突き合わせて再整理）
- 前提: **実機 canary（R1）は実施済み**。よって [総覧](2026-07-26-open-items-and-concerns.md)
  が「最優先」としていた §1.1 は消化済みとして扱い、「canary 待ち」だった判断・連鎖を
  次のステップへ繰り上げる
- 方法: `docs/plans/2026-07-24-*`〜`2026-07-26-*` の全文書から未完了項目を抽出し、
  HEAD（`fcd57c0`）のコードと突き合わせた。**コードで対応済みを確認できたものは §0 に
  カットの根拠だけ残し、以降の節には載せない**
- 反映先: 本書が総覧（07-26）の後継。総覧の §3（契機待ち）・§4（割り切り）は §4・§6 へ
  引き継ぎ、総覧に載っていなかった積み残し（今回の突き合わせで発見）は §5 に新規で持つ

**読み方**: §1 が canary の後始末（判断と記録。すぐやる）。§2 が次の実装の本丸（R2b と
同時に繋ぐもの）。§3 は R2b の静止点に相乗りさせる修正。§4 は契機待ちのまま残る一覧
（全件コードで未対応を再確認済み）。§5 は総覧から漏れていた積み残し。§6 は文書の綻び。

---

## 0. カットしたもの（コードで対応済みを確認）

07-26 棚卸しの主要修正はすべて実装済みで、本書では扱わない。

| 項目 | 確認した根拠 |
|---|---|
| P0-1〜P0-4 / P1-1〜P1-5 / P2-1〜P2-5 / P3-1〜P3-4 | 総覧 §7 の実施記録どおり。代表例: `eligible()` の workloads / max_concurrent / inflight 判定は `agentcore/board.py` の `eligible`（workloads 照合・上限超過で入札しない）に実装され、flow（`agent_flow/board.py`）・amigos（`agent_amigos/board.py` の `_node_board_declaration`）の両方へ配線済み |
| **P1-c** dashboard のレジストリ `repos.yaml` / `repos.yml` 読み取り | **総覧 §3 から削除できる**。`agent-dashboard/src/features/agent-project/main/agent.js` の `REPOS_REGISTRY_NAMES = ['repos.yaml', 'repos.yml', 'repos.json']` + `readReposRegistry()`。編集経路（`authoring.js`）とテスト（`cowork-roots.test.js` / `form-edit.test.js`）も同梱 |
| **P1-d** `cowork.roots` の掃除の口 | **総覧 §3 から削除できる**。全体設定に登録済み root の全件リスト + 「登録を解除」ボタン（`renderer/sections/orchestration.js` の `globalSettingsCoworkRootsHtml` → `dropCoworkRoot`）。残るのは「project 化済み」の印と自動掃除だけで、実害はない |
| P4-c のうち **award / cancel** | 常駐体へのノード指示（`board-award` / `board-cancel`）投函に置き換わり、`git+` 板でも届く（`delegation/main/ipc.js` → `resident_cli.py` の指示受理）。**未実装で残るのは `submitPost` と手動 post / award の UI**（§4） |
| R3（旧経路の削除・agent-flow 側） | 実装計画 §7 R3 節自身が「完了」と削除済みシンボル一覧を記録。ただし同 §7 の表と R1 本文が未更新で自己矛盾（§6-1） |
| R4 / R10（CI と利用者向け文書の内部名検査） | `.github/workflows/ci.yml`（4 パッケージ + dashboard + docs 検査）と `tools/ci/check_user_docs.py` が存在 |
| 07-27 の運用修正 3 件 | `bb091e3`（WSL 側 host.yaml 宣言を Windows の画面から読む）・`39dc117`（成果物クローンの `/mnt/<drive>` 変換）・`9d65cad`（幻のステージの自己修復 `_realign_index`・`state_transaction` の remote-HEAD 親化・`project_watch` の controller 関門）。いずれも canary 系の実害修正で、§4 の契機待ち項目（G-1 / G-2 等）は**含まない** |

---

## 1. canary の後始末（最初にやる。実装より判断と記録）

### 1.1 ランブックへの結果記入

`docs/guides/single-resident-canary.md` は **C1〜C10 の記録欄・§5 欠陥記録・§6 終了判定の
チェックボックスがすべて空欄のまま**（リポジトリ上、canary 完了の痕跡が無い）。canary は
セットアップガイドの受入試験と V1 / V3 / V4 の検証結果を兼ねる設計（実装計画 §5・§7 R1）
なので、記録が無いと「障害回復表の 3 行（常駐体クラッシュ / 全 PC 停止 / WSL VM 停止）は
検証済みか」「ガイド外の操作は要らなかったか」が後から確かめられない。**実施時のメモから
記録欄を埋めるのが最初の一手**。

### 1.2 canary の観測結果で決める判断（総覧 §5 で予約済みのもの）

| # | 判断 | 内容 |
|---|---|---|
| 1 | **host.yaml 検査 W5/W6/W7 の E 昇格** | 現状は警告どまり（`resident_cli.py` の `host_config_findings` に「E への昇格は canary 明けの判断に回す」と明記）。canary で出た警告の件数・内容・スカラ救済の発動回数から、E（起動を止める）へ上げるかを決める。**E6（`projects[].config`）も同件**——現状 doctor だけが critical を出し、起動時は誘導文の警告のみ。S1 §3.3 の宣言（エラー）へ戻すかをここで決める |
| 2 | **`max_concurrent` の意味変更の影響** | `0` = 無制限へ変えた（P2-3）。枠の自己抑制で入札を控えた回数と、無制限宣言 PC の負荷を見る。多いなら上限が実態に対して小さい |
| 3 | **`workloads` を誰も宣言しない状態が続くか** | 宣言したくなる場面が出なかったなら、契約に残すだけで運用の口（doctor の info 等）は増やさない |
| 4 | **flow-planner 粒度ゲートの再評価**（総覧に無かった観測点） | [粒度設計](2026-07-25-flow-planner-granularity-design.md)の Decision Record: work ノードの成功率・手戻りが改善しない／スコープ逸脱が多発するなら、案 B（分解批評 Phase 3.5）または内側 verify 契約を再検討 |
| 5 | **板の `local` publish 廃止後の速度**（総覧に無かった観測点） | P2 詳細設計 §8-7。板経由の仕事が遅くなったという申告が出たら、請負ノードの実際の解決経路を確かめる |
| 6 | **graceful 停止の板残留時間**（総覧に無かった観測点） | P0 詳細設計 §7-F2。`cmd_serve` の `graceful_shutdown` は設計 §4.2 の 4 ステップ（板への away 宣言・最終 push 等）を注入していない。「板に『応答なし』で残る時間」の実測が R2b 設計（§2）の入力になる |

---

## 2. R2b（ノード直轄実行）— 次の実装の本丸

**現状（コードで確認）**: 未実装。`resident_cli.py` 冒頭に「**落札した仕事のノード直轄実行
（R2b）はまだ無い**」と明記され、`_board_participate_tick` は宣言・心拍・指示取り込みまで、
`_flow_participate_tick` は `host.projects` でしか回らない。`NodeWorkerPool` 自体は完成して
いるが投入元はプロジェクト / amigos バス経由の 2 つだけ。dashboard は `board.intake_projects`
が空のノードで手動入札ボタンを理由付き非活性にしている（`participation/model.js`）——この
ガードが R2b の完成で外れる。

**スコープ**（S8 §2 / 実装計画 §7 R2 / 常駐一本化設計 §4.2〜§4.3）:
プロジェクト 0 個のワーカーノードが「板 tick で入札 → 落札 → `NodeWorkerPool` で実行 →
板へ結果報告」まで通る経路。ロール分岐は「プロジェクト子を起動しない・coordination に
触れない」の 2 点だけに保ち、フォークを作らない（設計 §9 C12）。導入は
`clone + install.sh + agent-project worker init`（対話で yaml 生成）。ワーカーの死は
lease 失効 → 再入札が吸収する正常事象として扱う。

**R2b と同時に繋ぐもの**:

| # | 内容 | 出典 |
|---|---|---|
| P4-b | **検証委譲の後半** — `unverifiable` は今も人検収へ直行して終端（`mr.py` の `_block`。板への公示分岐なし）。公示を出す口は R2a で開通済み・理由コードも残してあるので、請負側（板の実行）が R2b で開く | S4/S5 §7-1 / S8 §10 |
| — | **graceful 停止の 4 ステップ注入**（§1.2-6）。「落札した仕事を持つワーカーノードが落ちる」経路が現実になるため、P0 詳細設計 §8 が「R2b で一緒に設計する」と予約済み | P0 §7-F2・§8 |
| — | **`poll_board` の取り込み済み判定を板の `status/<who>.json` 基準へ**。現行は自分のバスしか見ず、同一ノードの 2 プロジェクトが同じ板を巡回すると同じ公示を二重に取り込む（S8 §6.5 が「既に壊れている」と記録） | S8 §6.5 |
| — | **「旧バージョンノードが入札しない」の実機確認**。`eligible` の `requires.contract_version` として実装済み。ワーカーノードが実際に入札する状態になって初めて確かめられる（canary ランブック C10 の「現状では確かめられない」注記を外せる） | 実装計画 §7 R2・R6 |
| — | dashboard の手動入札ボタン有効化（`intake_projects` ガードの解除）と、`worker` ノードの参加タブ表示の確認 | S8 §6.6・§9-1 |

---

## 3. R2b の静止点（フリート更新）に相乗りさせる修正

契約・名義・全 PC のブランチに触る修正は静止点でしか入れられない。R2b のフリート更新が
次の静止点なので、そこへ相乗りさせる候補を先に決めておく。

| # | 内容 | 備考 |
|---|---|---|
| 1 | **明示指定 node_id の正規化** — flow / amigos の `--node-id`・設定値・`$AGENT_AMIGOS_NODE`・`node.json` は今も素通し（`agent_flow/daemon.py` / `agent_amigos/cli.py`・`daemon.py` で確認）。agent-project 側だけは正規化済み | **着手前に方針の矛盾の決着が要る（§6-2）**: 総覧 §3 / P2 §8-1 は「次の静止点でやる」、P0 詳細設計 §8 は「明示値は正規化しない——cutover ガイドが『そのまま使う』と約束しており、この非対称は意図的」。やる側に倒すならガイドの約束の改訂と `doctor --node-id-cutover` への検査追加（非正規形の明示設定の検出）がセット |
| 2 | **`flow-archive/` の追跡外し** — 所有者（dashboard の git 書き込み）は削除済みなのに、`stategit.py` の `_EXCLUDE_PATTERNS` / `_untrack_excluded` に `flow-archive` が無く、コメントも「viewer が所有」と旧理由のまま。追跡から外す＝全 PC のブランチからファイルが消えるコミットになるため静止点案件（総覧 §7.6 の判断どおり） | `_STATE_EXCLUDE_DIRS` 側には入っており非対称が残っている |
| 3 | （speculation を実装する場合のみ）**枠の数え方の見直し** — P2-3 の自己抑制は「1 委譲 = 1 枠」前提 | P2 §8-6 |

---

## 4. 契機待ちのまま残るもの（全件、コードで未対応を再確認済み）

総覧 §3・§7.7 の一覧から、§0 でカットした P1-c / P1-d と、§1〜§3 へ繰り上げたものを除いた
残り。**いずれも契機が来るまで着手しない**（先回りしない理由は総覧の各行にある）。

| # | 内容 | 現状の確認結果 | 拾う契機 |
|---|---|---|---|
| P1-b | CLI チャット起動先のパス手入力 UI | main 側は任意パスを受けられる（`agent.js` のコメントに「その場限りの手入力パス」と意図が残る）が、入口は `<select>` のみで `<input>` が無い | 宣言済みリポジトリで足りなくなったとき |
| P2-b | GitHub / Gitea の forge 実装 | `mr.py` は判別（`_forge_kind`）だけ実装し、gitlab 以外は警告してフォージ無し運用へ。意図的（「動作確認できる環境が無いまま書いた API クライアントを増やさない」） | 動作確認できるノードが要るとき |
| P2-c | `diff2html` 撤去 | `package.json` に残存。`needs.js` のローカル diff フォールバックが使用中 | フォージ無し運用が消えたとき |
| P3-b | 墓標の自動失効 / 一括 revive | `load_tombstones` は `date` を読むが期限判定に未使用。`cmd_revive --all` は「指紋一致を charter 横断で消す」であり一括 revive ではない | 古い墓標が実害を出したとき |
| P3-f / P3-g | `ensure_repo_maps` の sha キャッシュ / 日本語タイトルの Jaccard 代替 | 未実装（変更なし） | 遅い申告 / 重複すり抜けが出たとき |
| P3-h | dashboard から墓標を見る・解除する口 | dashboard に `revive` は 0 件。dashboard の「墓標」は agent-flow のリトライ世代交代の run 墓標で別概念 | 却下の取り消しが実際に要るとき |
| P3-i | draft → ready の昇格導線 | `plan_review: off` で `draft` に落ちたタスクは、`REVISE_FIELDS` に `status` が無く `cmd_approve` も扱わないため、md 直接編集以外に上げる手段が無い | `plan_review: off` の運用が出たとき |
| P4-c 残 | `submitPost` の `git+` 板対応と手動 post / award の UI | `submitPost` は板の作業ディレクトリへ直書きのまま（`board-post` 指示は無い）。renderer から post / award を呼ぶ箇所も無い | owner-picks 運用を始めたとき |
| P4-d/e/f/g | speculation / push 配信 / 対話化 / tmux 掃除 | 未実装（変更なし） | 総覧 §3 のとおり |
| — | **プロジェクト側 `commands/*.err` の期限掃除の配線** | ノードスコープ（`~/.agents/commands`）は `_sweep_node_commands` で `prune_rejected` 配線済み。**プロジェクト側（状態リポジトリ配下）は `prune_receipts` のみで、`.err` は全 PC へ配られたまま溜まる** | `.err` の残骸が邪魔になったとき（dashboard の失敗バナー規約との突き合わせが先） |
| — | **doctor のまとめ足し** — ①`host_config_findings()` の doctor 配線（**総覧は P3-3 完了と記すが未配線**。doctor は `layer_findings` しか呼ばず、「doctor は緑なのに起動時は警告」が現に残っている・§6-3）②設定値検査（`argv_limit ≤ 0` 等。flow の doctor にはあり project には無い）③`workloads` 未宣言の info ④flow / amigos の非正規形 node_id 検出（§3-1 とセット） | ①〜④とも未実装を確認 | doctor へ検査をまとめて足す回（①は P3-3 の取りこぼしなので早めに拾ってよい） |
| — | **スキーマ検証を CI へ** | `jsonschema` 依存なし・CI にスキーマ関連ステップなし。スキーマを読むテストは手書き期待値の突き合わせのみ | 契約の食い違いをもう 1 度踏んだとき（まず 1 回流して違反量を見る） |
| — | CI 強化（eslint / 新しい python 版 / concurrency / キャッシュ） | 4 点とも無し（`ci.yml` は 3.11 固定・`npm test` のみ） | 総覧 §7.7 C-1〜C-3 のとおり |
| — | R10 検査の対象拡張（C-4） | `docs/guides/` + 主要 README のみ | 広げるならまず 1 回流して違反量を見る |
| — | dashboard の README にテスト手順（`npm install --omit=dev` → `npm test`）が無い | 記載は CI と CHANGELOG のみ | 次に手順を触るとき |
| G-1 | `listArchivedRuns` の突合 | bus の live run としか突合せず、他 PC で消した run が `archived` バッジ付きで残る（上限 100 件で自然消滅） | 実害が邪魔になったとき |
| G-2 | 判断待ち needs の復活ループ | `_take_local_on_conflict` / `ensure_needs` に DR（`decisions/<id>`）参照なし。**07-27 の `9d65cad` はこの件を含まない**（`_realign_index` と `state_transaction` のみ） | 復活を実際に観測したとき（canary で観測したなら今） |
| G-3 | 死活判定の閾値 | 未確認のまま（読み手の既定は 120 秒に揃っている） | 誤表示を実際に見たら |
| — | `gitbus._safe` の置換文字 / `canceled` 識別子 | どちらも**方針どおり**未変更（`_safe` は衝突していた板レイアウト 1 箇所だけ `protocol.safe_name` へ寄せ済み。`mark_canceled` 等の識別子は「触るファイルの修正時に限る」） | 方針の再決定があるまで対象外 |

---

## 5. 総覧から漏れていた積み残し（今回の突き合わせで発見）

07-26 総覧の §3・§4 に転記されていなかったもの。緊急のものは無いが、次に総覧級の
棚卸しをするとき落ちないようここへ持つ。

| # | 内容 | 出典 |
|---|---|---|
| 1 | **flow-planner 粒度設計の非目標・将来フックが総覧に丸ごと不在** — 分解批評 Phase 3.5・失敗時細分化（ADaPT 型）・`--learnings` 粒度フィードバック・goal 構造化ブロックの正式フィールド化・`plan_strategy_agent` フォールバックがゲート対象外のまま、など。観測点（§1.2-4）だけは本書へ繰り上げた | [粒度設計](2026-07-25-flow-planner-granularity-design.md) 非目標・将来フック |
| 2 | **`state_repo` / `state_repo_branch` がどの層からも `Config` へ届かない**（実害なし）。フィールドを消すかの判断が「P1 以降」のまま宙に浮いている | P0 §7-I |
| 3 | **60 文字超の FQDN ホスト名は大文字が無くても名義が変わる**（`_auto_node_name` の `[:60]` が `normalize_node_id` に無い）。長い名義は node-id 切替の対象に含める、の一文がガイドに無い | P0 §7-C |
| 4 | **`Config` を `slots=True` にするか**（動的属性の棚卸しが要る）。P0 §8 → P1 §8-5 と持ち越されたまま | P0 §8 / P1 §8-5 |
| 5 | **`engine/status.json` のスキーマファイル化** — 「P3-1 の CI でゴールデンテストを入れる際に決める」とされたが、P3-1 実施済みの今も決着の記録が無い（`board` ブロックは dict のまま育っている） | P0 §8 |
| 6 | **`schema_version` を誰も読んでいない** — 例示ファイルにあるが値の検査もバージョン分岐も無い。「P2 以降」とされたが P2 詳細設計で扱われた形跡なし | P1 §7-K |
| 7 | **`side_effects` の実効性** — 副作用制約はプロンプトで頼むだけで CLI の権限フラグとしては強制していない。`readonly` 非強制（総覧 §4）と同型の割り切りだが総覧に記録が無い | P1 §8-6 |
| 8 | **ノード宛て指示の debounce 3 秒が定数**（プロジェクト側は設定可・ノードスコープは不可） | P1 §8-7 |
| 9 | **定義ファイルの `spill.instruction` が Python から使われていない** — P2-5 は `spill_instruction(what, then)` の枠だけ統一。「定義ファイルが正典」（S9）とのずれが残る | P1 §8-4 / P2 §9 |
| 10 | **`agents:`（処理毎上書き）と SHARED 群の合成順は保留のまま**（「agents が処理単位で最終勝ち」を維持） | S1 §7 |
| 11 | **S9 由来の P1'-b / P1'-c が総覧に不在** — ①dashboard の CLI 定義 YAML 読み取り（見送り理由「YAML パーサを持たない」は P1-c と同じで、**前提は既に消えている**——dashboard は `yaml` を実行時依存に持つ）②定義の `errors[]` を dashboard の失敗分類に使う（現状は kiro の月間上限だけ特別扱い） | S9 §8-2・§8-3 / 改良仕様 §4 Phase 1' |
| 12 | **常駐一本化設計 §9 C7 の緩和策（`views/*.json` 正規化）が未実装** — UNC 越しのバス走査の読み性能。§4.6 の「受入 diff は将来 views 化で置換可」も同根 | 設計 §9 C7・§4.6 |
| 13 | **`flow.js` のキーワード推定と `executors/gitlab.py` の「一致させる約束」に保証機構が無い** — CONTRACT_VERSION / DIFF_CRITERION と同型の「2 実装が黙ってずれる」候補 | S4/S5 §6 |
| 14 | **テスト再編（巨大単一ファイル × 3 → 機能別）の flow / amigos 側の完了記録が無い**（project 側は分割済み） | 設計 §4.7 |

---

## 6. 文書の綻び（実装不要。次に該当文書を触るときに直す）

1. **実装計画 §7 が自己矛盾** — 冒頭の表と R1 本文は「R3 は残・R1 完了まで進めない」の
   ままだが、R3 節の見出しは「完了」で削除済みシンボル一覧まである。R4（CI）も総覧側の
   「実施済み」注記が実装計画には無い。表・本文を完了側へ揃える。
2. **明示 node_id 正規化の方針が文書間で正面から逆**（§3-1） — P0 詳細設計 §8
   「明示値は正規化しない（意図的な非対称。cutover ガイドの約束）」対 総覧 §3 / P2 §8-1
   「次の静止点でやる」。どちらかへ決着させ、負けた側へ訂正注記を入れる。
3. **総覧 §7.4 P3-3 の「doctor は `host_config_findings()` を呼ぶだけ」は実装と不一致**
   （§4 の doctor 行）。実際に配線されたのは `layer_findings` のみ。総覧側に訂正注記が要る。
4. **S3/S2 詳細設計の 2 箇所が古い** — §5「YAML パーサを持たないので `repos:` だけ読む
   小さなパーサ」（実際は `base/main/yaml.js` の本物のパーサへ移設済み）、§6-1「`nodes/<pc>.json`
   の書き手がまだ無い」（R2a で常駐体が書き手になった）。
5. **S4/S5 §7 の待ち先表記が旧世代**（「W1-11 待ち」→ 正しくは R2b 待ち）。
6. **canary ランブックの記録欄が空**（§1.1。文書修正ではなく記入）。

---

## 7. 進め方のまとめ

```
§1 canary の後始末（記録の記入 + 6 つの判断）      ← いま。実装を伴わない
   └ 判断 1（W→E 昇格）と G-2（needs 復活）は canary の観測結果次第で即実装に化ける
§2 R2b（ノード直轄実行 + 検証委譲後半 + graceful 4 ステップ + 二重取り込み修正）
   └ 着手前に §6-2（明示 node_id の方針矛盾）を決着させ、§3 の相乗り分を確定する
§3 R2b の静止点で一斉適用（node_id 正規化 or 見送りの明文化・flow-archive 追跡外し）
§4 契機待ち（着手しない。契機が来た行だけ拾う）
§5〜§6 次に該当文書・該当ファイルを触るときに反映
```
