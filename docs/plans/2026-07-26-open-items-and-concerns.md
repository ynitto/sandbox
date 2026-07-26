# 積み残し・懸念・不具合の総覧（2026-07-26）

- 状態: 棚卸し（07-24〜07-26 の計画・詳細設計の消化後に残っているものの単一リスト）
- 出典: [常駐一本化 実装計画 §7](2026-07-24-single-resident-controller-implementation-plan.md) /
  [agent シリーズ改良仕様 §4](2026-07-25-agent-improvement-spec.md) /
  各詳細設計（S1〜S9）の「積み残し」節 / 本棚卸しでの実装確認
- 反映先: 設計正典は `docs/designs/agent-*-design.md` に反映済み。本書は「まだやっていないこと・
  観測が要ること・直っていないこと」だけを持つ

**読み方**: §1 が次の一手（着手可能・他の前提になるもの）。§2 はその完了を待つ連鎖。
§3 は「必要が出たときに拾う」の一覧で、急ぎのものは無い。§4 は意図して残した割り切り
（やらないと決めたことの記録）。§5 は運用で観測すべき懸念。§6 は**今回の棚卸しで新たに
見つけたもの**で、§6.1 の不具合 3 件（`remote_review` が効かない・serve 起動直後の停止で
子が孤児化・ノード宛て指示が正典構成で届かない）だけは §1 と同格に扱ってよい。
全体を掴むには §1〜§2 と §6.1 で足りる。

---

## 1. 次の一手（着手可能・後続の前提）

### 1.1 実機 canary（R1）— 最優先。ここが詰まると §2 全部が動かない

フル 2 台（停止時刻をずらす）+ ワーカー 1 台（POSIX 機）で 1 週間。ランブックは
[`docs/guides/single-resident-canary.md`](../guides/single-resident-canary.md) に用意済みで、
残っているのは実施そのもの。完了条件は二重実行 0・stale done 0・状態欠損 0。

- 事前検証 V1（UNC アクセスの WSL 起動維持効果）・V3（Windows 起動ループ方式）・
  V4（systemd user unit + linger）はこの canary に内包される（単独では動かさない）
- 設計の障害回復表（常駐一本化設計 §6）のうち「常駐体のクラッシュ」「全 PC 停止」
  「WSL VM 停止」の 3 行は回復動作そのものが未検証で、canary が唯一の受け皿
- セットアップガイド（[`single-resident-setup.md`](../guides/single-resident-setup.md)）の
  受入試験を兼ねる: ガイド外の操作が要ったら全てガイドの欠陥として記録・反映する

### 1.2 R10 の grep 検査と CI（R4）— この repo には CI が無い

利用者向け文書に内部名（node / sync / resident）が現れないことの機械検査。CI 基盤自体が
存在しない（`.github/workflows` / `.gitlab-ci.yml` / Makefile いずれも無し・実装確認済み）ので、
検査スクリプトと CI の新規作成になる。同じ CI で 4 パッケージ（agent-project 1,063 /
agent-flow 571 / agent-amigos 176 / agentcore 127 — いずれも本棚卸しで全緑を実測）と
agent-dashboard の `npm test` も回すのが素直。

**検査仕様に決めが要る（今回の棚卸しで判明）**: 素朴な grep は成立しない。利用者向け文書には
`single-resident-setup.md` というガイドファイル名や `agent-node-command.schema.json` という
契約名へのリンクが正当に含まれ、これらは内部名を含む。R10 が隠すのは「製品名としての内部名」
であって契約の語彙ではない（常駐一本化設計 §3.1）ので、検査には「ファイルパス・スキーマ名・
コードブロックを除外して本文だけを見る」規則の設計が要る。

---

## 2. R1 / R2b 待ちの連鎖

| # | 内容 | 待ち先 | 出典 |
|---|---|---|---|
| R2b | **ノード直轄実行** — プロジェクト 0 個のワーカーノードが落札して `NodeWorkerPool` で実行する経路。手動入札はこれが無くても成立する（フルノードの既存経路へ合流）が、ワーカーノードは板の仕事を請けられないまま。dashboard は `engine/status.json` の `board.intake_projects` を見てボタンを理由付き非活性にしている | R1 の後 | 実装計画 §7 R2 / S8 §10 P4-a |
| P4-b | **検証委譲の後半** — 「このノードでは確かめられない」受入基準を他ノードへ回す経路。公示を出す口は R2a で開いたが、請け負えるのはフルノードだけ | R2b | S4/S5 §7-1 / S8 §10 |
| — | **「旧バージョンノードが入札しない」の実機確認** — `agentcore.board.eligible` の `requires.contract_version` 判定として実装済み。ワーカーノードが実際に入札する状態（R2b）にならないと実機で確かめられない | R2b | 実装計画 §7 R2 |

---

## 3. 必要が出たときに拾うもの

いずれも「動作は正しいが最適でない / 入口を足すだけ / 実際に困ってから」の類。
先回りで実装しない理由も含めて出典に記録済み。

| # | 内容 | 拾う契機 | 出典 |
|---|---|---|---|
| P1-b | CLI チャット起動先のパス手入力 UI（main 側は実装済み。入口のテキスト入力を足すだけ） | 宣言済みリポジトリで足りなくなったとき | S3/S2 §6 |
| P1-c | dashboard の repos.yaml/yml 読み取り（YAML パーサを持たないアプリ。候補が減るだけで害は無い） | レジストリが yaml のプロジェクトで宣言し忘れの可視化が要るとき | S3/S2 §6 |
| P1-d | `cowork.roots` の掃除の口（project 化したフォルダの登録が残る。表示は自動で正しい） | 残骸が邪魔になったとき | S3/S2 §6 |
| P2-b | GitHub / Gitea の forge 実装（`forge` アダプタ境界だけ切って未実装。未対応リモートはフォージ無し運用へ倒れる） | 動作確認できるノードが要るようになったとき | S4/S5 §7 |
| P2-c | `diff2html` 依存の撤去（MR 無しタスクのローカル diff フォールバックが使う） | フォージ無し運用が消えたとき | S4/S5 §7 |
| P3-b | 墓標の自動失効 / 一括 revive（日付は行に持たせてある） | 古い墓標が実害を出したとき | S6/S7 §7 |
| P3-f | `ensure_repo_maps` の sha 取得キャッシュ — plan 前置の無条件化により、オフラインのノードでは plan の頭に repo 数 × 最大 60 秒が乗る | 実際に遅いという申告が出たとき（先回りすると「古い sha で再生成を見送る」別の壊れ方を作る） | S6/S7 §7 |
| P3-g | 日本語タイトルの Jaccard 照合の代替（N-gram 等）— `\w+` トークン化は分かち書き無しの日本語で「完全一致か 0 か」になる。一次防衛はプランナー入力への既存タスク注入に移してある | 重複が実際にすり抜けたとき | S6/S7 §7 |
| P3-h | dashboard から墓標を見る・解除する口（却下は画面から出せるのに解除は CLI `revive` だけの非対称） | 却下の取り消しが実際に要るとき | S6/S7 §7 |
| P3-i | draft → ready の昇格導線（`plan_review: off` で必須項目が埋まらなかったタスクの置き場） | `plan_review: off` の運用が出てきたとき | S6/S7 §7 |
| P4-c | `submitPost` / `award` の `git+` 板対応 — dashboard に手動 post の UI が無く、`board-award` 指示の契約だけ用意済み。**現状の直接書き込みは `git+` 板では誰にも届かない**（README に明記済み） | owner-picks 運用を始めたとき | S8 §10 |
| P4-d | 投機同時実行（speculation）— 契約からも削除済み（実装時に additive で戻す） | 必要が出たとき | 実装計画 W0-10 |
| P4-e | push 配信（forge webhook / long-poll）— 30 秒ポーリングで足りている | 遅いという申告が出たとき | S8 §10 |
| P4-f | `consultation` / `plan-critique` / `delivery-rationale` の対話化 — 構造化見出しの抽出に依存するため対話にすると抽出点が消える | 抽出をやめてよいと判断できたとき | S8 §10 |
| P4-g | 対話診断 tmux セッションの一括掃除（使い捨てなので状態は残らないがセッションは溜まる） | セッションが溜まって困ったとき | S8 §10 |
| — | dashboard の未実装改善（外部通知ルーティング・横断要対応キュー・条件付き自動承認・決定メモリ・メトリクス等） | — | [dashboard 設計書 §8](../designs/agent-dashboard-design.md) |

---

## 4. 意図して残した割り切り（やらないと決めたことの記録）

| 内容 | 理由 | 出典 |
|---|---|---|
| **charter acceptance の LLM 一発合成が残る（P2-d）** — タスク検証は証跡ベースへ置き換えたが、`resolve_charter_acceptance`（マイルストーン収束判定）は今も自然文 → コマンド合成。S5 と同じ問題（合成コマンドの良し悪しを人が判断できない）を抱える設計上の非対称で、**ここが残るあいだ `synth_verify` と静的スクリーニング群も消せない** | 検証対象（タスク単位・成果ブランチ上ではない）も出口（milestone）も違い、別設計が要る。S6 で「基準を書くのはエージェント、直すのは人」の経路ができたので下地は揃った | S4/S5 §6・§7 / S6/S7 §7 |
| red-green（`verify_validate`）を fast path 専用に残す | 差分の常設基準が効くのは verifier 経路だけ。`verify_template` 由来の機械生成コマンドが done の唯一の根拠になる経路には、実行で弁別を確かめる価値が残る | S4/S5 §6 |
| exec 断片合成の解消はしない | あの方式は「テストが `km.<name>` をモンキーパッチできる単一名前空間」を意図した選択で、解消はテスト 2.5 万行の参照モデルごと張り替えになる。配布統合（W3-1）は合成に触らず達成できた | 実装計画 §7 |
| `size` は表示のみ（自動再分解しない） | 自動で計画を作り直す経路を増やすと、人が直した計画が動く理由が増える | S6/S7 §7 |
| md 直接編集の検出はしない | 内容署名の維持コストに見合わない。実害は `planned_title` で塞いだ | S6/S7 §7 |
| `readonly` の強制はしない（宣言 `enforced / best-effort` と画面警告のみ） | レイヤの責務は argv の組み立て。防御を持つと CLI ごとの実装が散らばりとして再発する | S9 §6 |
| 参加ノード表に他 PC の `local`（絶対パス）を出さない | 読み手に意味が無い。アダプタ段階で落とす | S8 §10 P4-h |
| `_source_repo` の共有 bare ミラーは blobless のまま | フォージ無し運用の自動マージだけが blob 遅延取得のネットワークを要する。確実にしたいノードは host.yaml `repos[].local` にフルクローンを宣言する。S4 でレビューが MR/PR へ寄り出番は縮んだ | S1 §7 / 改良仕様 P1-e |
| `participate` 受理直後のリース窓は縮めない | 受理〜run 作成の間に呼び出し側が死ぬと inbox claim のリース失効まで拾い直されないが、リースを短くすると起動待ち run の二重取りが起きる | [agent-flow 設計書](../designs/agent-flow-design.md) |
| 状態リポジトリ無しの完全ローカル運用は非推奨どまり（git init 縮退は動く） | 公式サポート範囲を広げない | S1 §7 |
| host.yaml を複数ノードへ共有配布する場合の `projects[].root` パス差異 | 「共有配布するなら root をノード間で同一パスに揃える」を運用規約とする | S1 §7 |

---

## 5. 運用で観測が要る懸念

- **notes の同時編集**（P3-e）: `notes/` は状態リポジトリ配下で全 PC へ同期される。衝突解決は
  state 同期の既存規則に委ねており、メモの粒度で衝突が問題になったらファイル名にノード id を
  入れる。
- **1 プロセス集中（常駐一本化設計 C3）**: single-flight・タイムアウト・例外隔離・
  ワーカー分離・self-watchdog で緩和してあるが、実負荷での挙動は canary（§1.1）で観測する。
- **スキル起動の単発実行はノードのセマフォ外**（同 C5）: 呼び出し元の判断だが、同じ
  agent CLI の座席を消費する。status 表示で見えるようにしてある。
- **フリート更新の規律**（同 C13）: 契約変更は静止点で全ノード一斉・スキーマと実装は
  同一コミット。更新漏れノードは「入札しない」に倒す実装は済んでいるが、実機確認は
  R2b 待ち（§2）。

## 6. 今回の棚卸しで新たに見つけたもの

07-26 時点の実装を横断調査した結果。既知の積み残し（§1〜§4）に無いものだけを載せる。
テストは実測済み（agent-project 1,063 件中 §6.1-2 の 1 件だけが間欠失敗。他は全緑）。

### 6.1 不具合（直すべきもの・重要度順）

1. **`remote_review` が Config へ配線されておらず、`observe` が効かない**（高）。
   `CONFIG_DEFAULTS` にキーはあり層検査も通るのに、`Config` dataclass にフィールドが無く
   `build_config` も渡していない。読み出し（`mr.py:397`）は `getattr(cfg, "remote_review",
   "settle")` なので、**プロジェクト yaml に `observe` と書いても常に `settle`** になり、
   S4 の移行用スイッチ（フォージ決着を表示だけに留める）が機能しない。observe 分岐は
   到達不能の死んだコード。S5 の verifier キーで踏んだ「CONFIG_DEFAULTS にあるだけで
   届いていない」欠落（S6/S7 詳細設計 §6 で自省済み）と同型の再発で、機械的に差分を
   取ると Config へ届いていないのはこのキーだけ。設定キー追加時に
   「CONFIG_DEFAULTS ⊆ Config フィールド」を CI で固定する再発防止まで含めて直したい。
2. **`serve` の SIGTERM ハンドラ設置が子プロセスの起動より後**（高）。
   `resident_cli.py` の `cmd_serve` は `_build_resident`（この中で子を start 済み）→
   `write_status()`（git 観測を含み数秒かかりうる）→ シグナルハンドラ設置、の順。
   この窓で SIGTERM が届くと既定ハンドラで即死して `graceful_shutdown` が走らず、
   **子だけが監督者不在で生き残る**（次回起動の子と同一プロジェクトでループが 2 本並ぶ——
   コード自身のコメントが警告している事故そのもの）。
   `tests/test_resident.py::test_serve_exits_cleanly_on_sigterm` が実際に間欠失敗する
   （3 回連続実行で 1 回、`-15 != 0`）。`systemctl restart` の連打や起動直後の
   シャットダウンで踏む。修正はハンドラ設置を `_build_resident` の前へ動かすだけ。
3. **ノード宛て指示ドロップの置き場が、書き手（dashboard）と読み手（常駐体）で別**（高）。
   常駐体は `$AGENT_COMMANDS_DIR` → WSL 側 `~/.agents/commands` を読むが、dashboard の
   `node-commands.js` は Windows 側 `os.homedir()` 基準で書く（同じ dashboard の
   `engine.js` は `wslpath` で WSL 側 home を解決して `engine/status.json` を読んで
   いるのに、この経路だけ通っていない）。正典構成（Windows で dashboard + WSL で
   常駐体）では投函先と取り込み先が別ファイルシステムになり、**手動入札・委任中止・
   落札が押しても何も起きない**（`.err` も出ず pending のまま）——S8-2 が直したはずの
   「押しても効かないボタン」が置き場所を変えて残った形。逃げ道の設定
   `delegation.nodeCommandsDir` は config.js の既定に載っておらず、画面から辿れない。
   旧 `~/.agent` フォールバックの有無も両者で食い違う。
4. **`Config.node` だけが `normalize_node_id` を通らない**（中〜高）。
   `node_id` 未宣言・環境変数無し（host.yaml を複数 PC へ共有配布する場合の推奨構成）の
   とき、`Config.node` は大文字を保持し、常駐体・板・agent-flow は小文字化する。
   ホスト名に大文字が入る（Windows/WSL では普通）PC は `status/DESKTOP-X.json` と
   `nodes/desktop-x.json` の 2 名義になる。`- node:` の完全一致で判定する
   `task_runnable_here` は、人が板の端末一覧（小文字）を見て書いた `- node:` を
   **どのノードも拾わないまま ready で固める**。`doctor --node-id-cutover` も status 側の
   残骸を見つけられない。`agentcore.nodeid` を 1 実装にした動機の取りこぼし。
5. **組み込み検証プロンプトが `verify_side_effects` を無視する**（中）。
   スキル（backlog-verifier）が見つからない／実行失敗時のフォールバックプロンプトは
   `acceptance` とタイトルしか使わず、副作用制約（作業ツリー外に書かない・外部へ
   書き込まない）が載らない。**安全設定がスキル未導入ノードで黙って落ち**、検証は失敗時に
   リトライで何度も走るので副作用が累積しうる。`rules` / `repo_context` / `recipes` /
   `feedback` も同じ経路で落ちるが、そちらは品質劣化に留まる。
6. **agent-project にだけ argv 長制限の退避（spill）が無い**（中）。
   agent-flow / agent-amigos は `prompt_via: argv` の CLI でプロンプトが上限を超えると
   一時ファイルへ退避するが、agent-project の `_agent_cmd` は無防備。S5/S6 で
   プロンプトは明確に肥大した（verifier 入力 = repo 文脈 + rules + レシピ + feedback、
   planner 入力 = charter 全文 + 既存タスク + 墓標）。既定 CLI の kiro は argv 渡しなので、
   超過すると verifier は全基準 unverifiable、plan は空振りで人へ倒れる。スキーマの
   「argv 長制限を超えると自動で退避に切り替わる」という説明とも矛盾する。
7. **`revive` が charter スコープを無視して墓標を消す**（低）。墓標の追記は
   `(指紋, charter)` 単位なのに、削除は指紋一致行を charter 無関係に全部消す。
   複数 charter 運用で意図しない解除が起きる。

### 6.2 実装と契約のずれ・二重実装（懸念）

- **`CONTRACT_VERSION` が 3 箇所に重複定義**: `agentcore/board.py`（入札判定が使う）・
  `resident/status.py`（板へ宣言する値。`contract_compatible` も docstring ごと重複）・
  dashboard `engine.js`。片方だけ上げると「版 2 と宣言しつつ版 1 で判定」になり、
  fail-close の設計ゆえ**誤動作ではなく無言の不参加**に倒れて誰も気付かない。
  「規則が片方だけ育つ」ことを潰すために agentcore へ集約した、その定数が割れている。
- **板の `nodes/<id>.json` に deprecated の `local`（他 PC の絶対パス）を publish している**:
  `repos.schema.json` は `local` を「ホスト固有なので共有レジストリに置けない」と
  deprecated 宣言しているのに、常駐体は host.yaml の `repos[]` を `local` ごと共有 git の
  板へ書く（S8 §6.2 の「速度最適化のヒント」として意図的）。表示では落としているが
  データは配られており、S3 の動機と正面から矛盾する。意図を維持するならスキーマ側の
  文言の決着が要る。あわせて `$defs.node.repos` の宣言（レジストリ形）と実装
  （host.yaml 配列形）も食い違い、スキーマ検証すると落ちる。
- **`nodes/<id>.json` の `workloads` / `max_concurrent` を入札判定が読まない**: スキーマは
  「workload 不一致・上限超過時は入札しない」と宣言するが、`eligible()` は tags /
  agent_cli / contract_version / repos しか見ない。忙しいノードが板の仕事を掴んだまま
  枠待ちで塞ぎ、空きノードが拾えない。`max_concurrent: 0` の意味もスキーマ（無制限）と
  実装（既定 4）で真逆。
- **host.yaml のトップレベルキーは無検査**: `PROJECT_ONLY_KEYS` は定義とテストにしか
  使われず、`_validate_layers` は `defaults` / `overrides` しか見ない。host.yaml の
  トップレベルに `plan_review: false` を書いても、`node_id` を `nodeid` と綴り間違えても、
  警告ゼロで黙って無視される。S1 の E2 契約（defaults/overrides の検査）自体は満たすが、
  「設定したのに効かないことに気付けない」という S1 の設計動機が host.yaml 側だけ抜けている。
- **`BoardRepo` の請負側書き込みだけ排他を通らない**（要確認）: `write_bid` /
  `write_cancelled` / `write_award`（S8 で追加）だけ flock と `_ensure()` を通らず直接
  書く。transport の破損時再クローン（`rmtree`）や `pull --rebase` と競合すると
  入札・中止が消えうる。
- **ノード宛て指示に debounce と `.err` 掃除が無い**: プロジェクト側 `ingest_commands` に
  ある「書きかけ猶予」と「成功時の古い `.err` 掃除」が、ノードスコープ側に無い。
  スキーマは書き手として人を認めているのに手置きは即 `.err` 行きで、`.err` は無限に
  溜まり gc tick も見ない。
- **`DIFF_CRITERION` の文字列が本体とスキルの 2 箇所**: 片方だけ直すと、検証レポートに
  出る基準文とエージェントが見た基準文が黙ってずれる（判定は番号で突き合わせるため）。
- **`repolocal` の Python / JS で吸収規則がずれる**（低・要確認）: symlink 解決の有無が
  違い、JS 側は `repos:` の行末コメントで 0 件に読める。どちらも「読めなければミラー
  取得へ落ちる」だけだが、1 実装へ集約した動機に照らすと再発の芽。
- **識別子レベルの `canceled`（米式）残存**（低）: 語彙統一（W0-9）の対象はデータ値で、
  `mark_canceled` 等の関数名・コメントには米式が残る。書き込む値は `cancelled` で正しく
  実害は無いが、grep のノイズになる。`NodeCapability.write` のパス導出が
  `_safe_node` を通らない件（現経路では実害なし）も同類。

### 6.3 文書の綻び

- **`docs/guides/multi-pc-operations.md` が常駐一本化前の記述と混在**: 廃止済みの
  `start` / `stop`、通らない `status --root`、旧モデルの「各 PC 1 daemon」が 10 箇所超
  残る一方、一部だけ `serve` へ更新済み。W3-2 の実施結果は本ガイドを「直した」と
  記録しているが、直り切っていない。複数 PC 分担は今回の改修の中心で、最初に読まれる
  ガイドなので優先度は高め。
- **S1 詳細設計が現存しないシンボルを参照**: `_validate_layers` の引数形が実装と違い、
  `_STATE_SIGNIFICANT` は存在しない（同期は除外方式）。結論は正しいが、設計書を根拠に
  読むと存在しない機構を探すことになる。
- **doctor に S1 設計 §3.6 の新検査が入っていない**: E1〜E7 相当の起動前チェック再掲と
  host.yaml `projects[]` の root 存在・origin 一致検査が未実装（設計書の実績節は実装済みと
  記す）。root の綴り間違いが「子の起動失敗 → 隔離」という最も遠い症状でしか見えない。
- **R10 検査は素朴な grep では成立しない**（§1.2 に詳述）: ガイドのファイル名・スキーマ名が
  内部名を正当に含むため、検査規則に除外の設計が要る。
- **agentcore のテストルートが 2 つ**: `agentcore/tests`（74 件）と `tests/`（53 件）。
  片方だけ `discover` すると残りが黙ってスキップされる。R4 の CI では両方を明示するか
  1 ディレクトリへ寄せる。

### 6.4 確認して問題なしだったもの（記録）

- `tombstones.md` / `notes/` / `verifications/` / `verify-recipes/` / `context/` は
  すべて状態同期の対象（同期は除外方式で、これらは除外されない）。複数 PC で墓標や
  検証レポートが共有されない懸念は当たらない。
- `task.schema.json` の status enum・`acceptance` の配列 ↔ 複数行往復は実装と整合。
- `agent-cli.schema.json` の `interactive` 継承規則は Python / JS で一致。
- 旧キー（`state_worktree_dir` / `--profile` 等）のガイド残存は移行対照表としての
  意図的な記載のみ。`gitAutoPush` / `location: daemon|remote` は残存ゼロ。
