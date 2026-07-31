# 常駐一本化 実装計画

- 日付: 2026-07-24（最終更新 2026-07-26）
- 状態: **P0〜P2 完了 / P3 進行中**。残作業は §7 に集約してある（消化済みの項目は落とした）。
- 対応設計: [`2026-07-24-single-resident-controller-design.md`](./2026-07-24-single-resident-controller-design.md)（改訂 6）。
  本書は設計 §8 の 4 フェーズを、対象ファイル・作業項目・完了条件つきの作業計画に分解する。
- 規模の目安: S = 半日〜1 日 / M = 2〜4 日 / L = 1 週間級（1 人での目安）

## 0. 進め方の原則

1. **フェーズ末ごとに全テスト緑**を回復してから次へ進む（2026-07-26 時点:
   agent-project 966 / agent-flow 561 / agent-amigos 158 / agentcore 8 / dashboard 緑）。
2. **P0 は撤退線**: ここで止めても「転送 5 実装 → 1・claim 3 実装 → 1・語彙バグ根治」の
   価値が単体で成立する。
3. **契約変更は静止点で全 PC 一斉**（語彙統一・板 result 拡張・speculation 削除・
   node_id 統一）。スキーマと実装は同一コミットで更新する。
4. **スキル互換（R9）は常設の非退行テスト**にする: `agent-flow run` と
   `agent-amigos drive` が常駐体なし・ネットワークなしで完結すること、CLI 名・引数が
   変わらないことを、P0 の時点からテストに固定して以降の全フェーズで守る。
5. P1 は段階ロールバックできない（設計 C6）。**P1 の内部も「全テスト緑を保つコミット列」で
   刻み**、切替済みバイナリを旧リビジョンに戻せばデータはそのまま動く状態を P1 完了まで保つ。

## 1. P0 — 共通ライブラリ抽出（transport / protocol）

> **状態: 完了**

目標: 転送・claim・語彙の重複を消す。語彙統一以外は**挙動不変**（既存テストが担保）。

| # | 作業 | 対象・内容 | 規模 |
|---|---|---|---|
| W0-1 | パッケージ骨格と import 経路 | 新規 `agentcore/` 通常パッケージ（`transport.py` / `protocol.py` / `vocab.py` / `heartbeat.py`）。3 ツールのエントリスクリプトに path shim を足し、`install.sh` の配置先でも import が通ることを確認（**事前検証 V2**） | S |
| W0-2 | transport 実装 | `agent_flow/gitbus.py` の護りを移植: stale lock 掃除・rebase abort・fsck プローブ・破損時の退避 → 再クローン → 復元・durable-write 設定・clone リトライ・push リトライ（force 禁止）。追加: 間隔律速（**失敗時にクロックを進めない**不変条件）・世代ディレクトリ + 原子的差し替えの再クローン・sparse 指定のパラメタ化。bare repo + 故意のロック/破損を使う新規単体テスト | L |
| W0-3 | protocol 実装 | 名前空間付き claim・`(ts, who)` 勝者決定・lease 書込/半減期延長/失効判定・心拍/鮮度・終端語彙定数（`done / failed / cancelled`） | M |
| W0-4 | BoardRepo 置換 | `agent_project/board.py` の git 操作を transport 呼び出しへ。既存テスト（TestBoardAutoWiring 12 件）緑 | S |
| W0-5 | BoardMirror 置換 | `agent_amigos/board.py` 同上。BoardParticipationTests 緑 | S |
| W0-6 | GitBus 転送委譲 | `agent_flow/gitbus.py` を Bus サブクラスのまま転送のみ transport 委譲へ。flow 全テスト緑 | M |
| W0-7 | StateGit 下回り置換 | `agent_project/stategit.py` の git 実行・回復・リトライ層を transport へ（direct / 管理クローン両モードとも、この時点では挙動不変。モード統一は P1）。CAS export・manifest 3-way・パス所有権裁定はポリシーとして残す | M |
| W0-8 | claim の共通化 | flow タスク claim・amigos ロール claim・板入札（flow / amigos / project 3 実装）を protocol 呼び出しへ置換 | M |
| W0-9 | 語彙統一（**静止点・一斉**） | `canceled` → `cancelled` を全ツール + `schemas/task.schema.json` で一斉改称。`_FLOW_TO_BOARD_STATUS` 翻訳マップと `endswith(("canceled","cancelled"))` 二重判定を削除 | M |
| W0-10 | 契約の掃除 | `board.schema.json` から未実装の speculation / `results/<who>.json` を削除。stale lock 閾値を 30s 単一定数に統一 | S |

**完了条件**: 全テスト緑 / `_recover` 系・claim 系の実装が agentcore 以外に grep で
見つからない / R9 非退行テストが緑 / 実運用 1〜2 週間で転送起因の新規バグ 0（撤退線の確認）。

## 2. P1 — 常駐体（resident）の実装と旧常駐の削除

> **状態: 完了**（W1-9 の旧経路削除のうち agent-flow 側だけ §7 へ繰り越し）

目標: 常駐を 1 本にし、旧常駐・location・instances 等を一括削除する。最大のフェーズ。

| # | 作業 | 対象・内容 | 規模 |
|---|---|---|---|
| W1-1 | スケジューラ | `resident/scheduler`: 周期表（コード定数）・tick 種別ごとの single-flight・ステップ毎タイムアウト・例外隔離・**内蔵 self-watchdog**（心拍停止 → 自ら abort。systemd 配下では sd_notify も打つ） | M |
| W1-2 | flow の tick 抽出 | `agent_flow/daemon.py` のループ本体を cancel / orphan-adopt / auto-heal / board / heartbeat の tick 関数群へ分解（primitives は既に独立関数。ordering 制約 — cancel 受理 → 孤児回収 — をテストで固定） | M |
| W1-3 | amigos の tick 化と drive 新設 | `cycle()` から手番実行を分離（claim/心拍/away tick + 手番のワーカー投入）。**単発駆動 `agent-amigos drive`** を新設（現 `serve --cycles` の骨格から常駐化 — デーモンロック・シグナル常駐 — を除去。インライン実行のまま） | M |
| W1-4 | スーパーバイザ | 子（プロジェクトループ）の起動・心拍鮮度によるハング検知・再起動・指数バックオフ + 隔離（quarantine）・graceful 停止の一括処理（claims 解放 → controller lease 解放 → away 宣言 → 板 status away → 最終 push）。既存の自殺型停止経路（availability の自 SIGTERM・self-update execv・グローバル drain フラグ）を「親 → 子への指示」へ置換 | L |
| W1-5 | ノード直轄ワーカー | 板落札の実行をロール共通のワーカー実行へ（プロジェクト子へ渡さない）。ノード全体 `max_concurrent` セマフォ（計数は**実行中を表すファイル**から導出 — 在籍状態を表すファイルは流用しない） | M |
| W1-6 | ノード契約の実装 | `nodes/<pc>.json`（能力宣言 + **契約バージョン**）・`engine/status.json`（心拍・tick 実績・同期健康・エラーリングバッファ・子状態・実行中 run）・gc tick（バス残骸・終端公示・クローン世代・tmp worktree） | M |
| W1-7 | 状態共有の一本化 | StateGit の管理クローン（`.state-git`）モードと非 git モードを削除し direct 一本へ。未初期化ルートは常駐体が git init。remote 無しはローカル縮退（同一コード） | M |
| W1-8 | coordination 常時化 | `coordination:` 設定キーを削除し「remote あり = 常時有効」へ。controller lease 配下の制御面ゲーティングを子ループに統合 | M |
| W1-9 | 旧常駐の一括削除 | flow `daemon` / `submit` / remote 委譲・`location` / `act_async`・amigos `serve` / hub / hubbus（約 450 行）・instances レジストリ・`manage_flow_daemon`・関連設定キー・dashboard 側は P2 まで現状維持。裸起動（サブコマンド無し）は案内表示化。板 `result.json` に `result_notes` / `discoveries` / `reject_guidance` を追加してから submit を消す（順序固定） | L |
| W1-10 | node_id 統一（**静止点**） | 既定を PC 名へ。`doctor` に切替前チェック（実行中の委譲・ミッション無し / 板 `status/<who>.json`・amigos `status/<node>--<role>.json` の名義残無し）を実装し、node_id 由来クローンパスの移動を含む手順書を書く | M |
| W1-11 | CLI とセットアップ | `agent-project serve / status / worker init / worker`・`agent-project.host.yaml`（プロジェクト宣言の単一ソース）・`install.sh` 拡張: 常駐起動の**選択式**セットアップ（systemd user unit / Windows タスクスケジューラ + wsl.exe 再起動ループ — **事前検証 V1・V3**）+ keep-alive + doctor 検査 | M |
| W1-12 | テスト移植と新設 | daemon 前提テストの tick 前提化（project の daemon 関連 60〜100 件が最大面。flow の実常駐必須は 6 件のみ）。新設: C14 併走テスト（スキル起動 run × 常駐体の claim 排他・孤児回収）・カオステスト（親 kill / 子 kill / ハング注入 / 電源断相当のクローン破損）・§6 回復表の各行に対応するテストまたは手動手順の対応表 | L |
| W1-13 | **セットアップガイド（ドラフト）** | 利用者向けの導入手順書を新規作成（`docs/guides/` 配下）。構成: (a) **フルノード編** — 前提（Windows + WSL・git 認証・agent CLI）→ clone + `install.sh` → 常駐起動方式の選択（systemd / Windows 起動ループ。それぞれの手順と確認コマンド）→ `agent-project.host.yaml` の書き方 → dashboard 接続 → 動作確認（status が緑になるまで）。(b) **ワーカー編** — clone + `install.sh` + `agent-project worker init` の対話例 → フォアグラウンド運用と systemd 化の選び方。(c) **トラブルシュート編** — 設計 §6 の回復表から「人の出番」がある行だけを利用者の言葉で抜粋（隔離表示・behind 表示・起動系ごと死んだ場合・旧バージョンノード）。**R10 検査対象**: ガイドに内部名（node / sync / resident）を出さない。コマンドが確定する W1-11 の後に書く | M |

**完了条件**: 1 PC + ローカル板で全機能が動く / 常駐プロセスは 1 本だけ（`ps` で確認可能） /
`agent-flow run`・`agent-amigos drive` が常駐体なしで完結（R9 緑） / 設計 §6 の回復表の
全行に検証手段がある / 旧リビジョンへのバイナリ戻しでデータがそのまま動く /
セットアップガイドのドラフトが存在し、書かれた手順どおりに新規 PC を 1 台導入できる。

### P1 実施結果

W1-1〜W1-13 完了。旧経路の削除（W1-9）の agent-flow 側は §7 R3 へ持ち越し、そこで完了した。

レビューで作業項目と実装を突き合わせて見つかった未達分も後から埋めた。**判断の根拠は
実装側の docstring に書いてある**ので、ここでは何を直したかだけ:

| 項目 | 直したこと | 根拠の在処 |
|---|---|---|
| W1-1 | systemd `Type=notify` + `WatchdogSec`（`READY=1` / `STOPPING=1` / `WATCHDOG_USEC` の半分で心拍）。復帰が 3 段構えになる | `resident/scheduler.py`・`tools/agent-tools/install.sh` |
| W1-4 | 自殺型停止経路を親 → 子の指示へ。`Supervisor.pause()` / `resume()` を新設し、計画停止を死亡回数に数えない。止めるのは `shutdown_due` に達してから（`draining` で止めると猶予設定が死ぬ） | `coordination.py`・`resident_cli._availability_tick`・`resident/supervisor.py` |
| W1-5 | `max_concurrent` の計数を**実行中を表すファイル**から導出。在籍状態（バスの `status/<who>.json`）は流用しない——終わった手番を走行中と誤読して自分の次の手番を弾く | `agent_amigos/turnmark.py`・`resident/worker.py`・`resident_cli._external_amigos_inflight` |
| W1-6 | 板の終端公示の gc。削除の主体は依頼側、**タスクが offloaded を抜けた後**に消す。孤児だけ gc tick が長期マージンで掃く | `board.py`（`drop_delegation` / `sweep_terminal_delegations`）・`loop.py` |
| W1-12 | C14 併走テストと親 kill のカオステストを新設。後者は「孤児の掃除は起動系に委ねる」前提を事実として固定する | agent-amigos `test_cli.py::DriveTests`・agent-project `test_resident.py::ResidentCliTests` |

## 3. P2 — dashboard 縮退

> **状態: 完了**

| # | 作業 | 対象・内容 | 規模 |
|---|---|---|---|
| W2-1 | git 書き込みの削除 | `base/main/git.js` の pull / commitPush（renderer の `gitPushAfterWrite` / `gitPushBusOp` 28 呼び出し箇所ごと）/ heal 実行 / `gitAutoPush` を削除。`diffRange` / `diagnostics` は読み取り専用モジュールへ分離して存続 | M |
| W2-2 | 本体 CLI 起動の削除 | `dashboard:start` を削除し、status 鮮度による「エンジン停止中（起動コマンド: `agent-project serve`）」案内表示へ | S |
| W2-3 | ロックプローブの削除 | `flowLockDir` 設定 UI と `flow.js` のロック鍵導出複製（`daemonLockPath` / `daemonStatus` / `stopDaemon`）を削除。稼働表示は `engine/status.json` へ一本化 | S |
| W2-4 | プロジェクト発見の切替 | ルート列挙設定を削除し、`engine/status.json` からプロジェクト（root・UNC 変換済みパス）を発見。設定は「ディストロ / ベースパス + 表示設定」へ縮退。`/mnt/c` 経路サポートも削除 | M |
| W2-5 | 表示の付け替え | 🩺 → 自動回復の状況表示 + `commands/heal` 投函。隔離マーク・behind・旧バージョンノードの表示 | S |

**完了条件**: dashboard のコードから git 書き込み・本体起動・ロック複製が消滅 /
利用者向け表示に内部名（node / sync / resident）が出ない（R10） / dashboard テスト緑。

### P2 実施結果（2026-07-25）

W2-1〜W2-5 は実装・テストとも完了（agent-dashboard の全テスト緑 / agent-project 983 緑）。
非退行は `test/no-git-writes.test.js` に構造として固定した（git 書き込みサブコマンドの起動・
`git:pull`/`git:commitPush`/`git:heal`・`dashboard:start`/`runProjectCli`・
`daemonLockPath`/`flowLockDir`・列挙設定・R10 の語彙）。

着手時に判明して合わせて実装したもの:

- **`engine/status.json` に `children[].root` と `contract_version` を追加**（`resident/status.py`）。
  前者が無いと dashboard はプロジェクトを 1 件も発見できず（W2-4 の唯一の入口）、後者が無いと
  古い常駐体が「正常」に見えたまま情報を欠く（W2-5 の旧バージョン表示）。
- **`commands/heal` をエンジン側に実装**（`commands.py` の `ingest_commands`）。設計 §5 に契約は
  あったが未実装で、dashboard が投函しても `.err` へ落ちるだけだった。受理で `state_sync(force=True)`。
- **`engine/status.json` の `sync_health` / `running_runs` に書き手を実装**（`resident_cli.py` の
  `_observe_sync_health` と `DirectStateGit.observe_sync`）。型と直列化だけがあって値を設定する
  コードが無く、dashboard の `summarize()` は空配列を全分岐素通りして**常に「共有先と揃って
  います」を緑で表示**していた（W2-1 でローカル fetch を廃止したため、他に異常を知る経路が
  無い状態だった）。観測は fetch せず最後に取り込んだ `origin/<branch>` と比較する——毎 tick
  リモートを叩くと dashboard から取り除いたリモート負荷を常駐体側で復活させてしまうため。
  同期失敗は `DirectStateGit._last_sync_error` に残す（`state_sync` が握り潰すので、
  残さないとどこにも記録が無い）。

縮退に伴う挙動の変更（意図した非互換）:

- **プロジェクトの登録・登録解除の口が dashboard から消えた**。宣言の単一ソースは実行側の
  `agent-project.host.yaml` で、この画面はそれを映すだけ（`dashboard:removeProject` と
  サイドバーの × を削除）。新規プロジェクト作成は charter を書くところまでで、一覧に出すには
  host.yaml への追記が要る旨をダイアログで案内する。
- **`projects.roots` に相乗りしていた他機能（cowork / amigos / orchestration の自動発見）を
  `engine.projectRoots()` へ付け替えた**。走査深さは各機能のローカル設定（`cowork.scanDepth` /
  `amigos.scanDepth`）へ移した。
- **同期の状況表示は `engine/status.json` の `sync_health` が根拠**になり、ローカル git への
  fetch（`refreshRemote`）は廃止。`git.js` に残るのは `health` / `diagnostics` / `diffRange` /
  `bridgeRepoPath` の 4 つだけ。
- **計画停止（`children[].paused`）を隔離とは別の印で出す**。W1-4 の availability tick が
  親主導の pause を入れたことで、engine/status.json に「時間が来れば自動で戻る停止」と
  「人が直すまで戻らない切り離し」の 2 種が並ぶようになった。同じ「停止中」に見せると、
  直す必要が無いものを人が直しに行く——サイドバーは「休止中」バッジ、状況表示は
  `level: ok` の一文（稼働時間外）にした。

作業項目の記述との差分:

- **W2-1 の「読み取り専用モジュールへ分離」は、別ファイルへ切り出さず `base/main/git.js` を
  その場で読み取り専用へ縮退させた**（571 行削除）。分離の目的（書き込み経路をこの層から
  無くす）は満たしており、参照元（`src/main/git.js` の再輸出・テスト）を丸ごと張り替える
  価値が無いと判断した。`module.exports` を 4 つに固定するテストで「増えたら落ちる」側で担保する。
- **W2-4 の「`/mnt/c` 経路サポート削除」はプロジェクトのパス解決に限った**
  （`_pathKey` / `toViewerPath`）。cowork / kiro-loop / participation は Windows ドライブ上の
  リポジトリを `wsl.exe` 経由で回す機能で、`C:\…` ↔ `/mnt/c/…` の変換はその実行経路の一部。
  ここまで消すとそれらの機能自体が壊れるため残し、renderer 側の `coworkPathKey` には
  「main 側とは別物」と根拠を書いた。

## 4. P3 — パッケージ統合と実機 canary

> **状態: W3-1 / W3-2 / W3-3 とも完了**（W3-2 の R10 検査は §7 R4 で CI 化・W3-3 の実機 canary は
> 実施済みで、ランブックの記録欄の記入だけが残る）。残りは §7（R2b と R6）。

| # | 作業 | 対象・内容 | 規模 |
|---|---|---|---|
| W3-1 | 単一パッケージ統合 | 配布パッケージを 1 本へ統合（インストールは `tools/agent-tools/install.sh` の 1 本・環境チェックも 1 回）。CLI エントリは `agent-project` / `agent-flow` / `agent-amigos` の 3 本を維持（R9・R10）。**exec 断片合成の解消は見送り**（下記実施結果） | L |
| W3-2 | テスト・文書の再編 | 巨大単一テストファイル × 3 を機能別に再編。README・GUIDE 等の全面改訂・**セットアップガイドの確定版**（W1-13 のドラフトに canary での躓きを反映）・**R10 チェック**（セットアップガイド含む利用者向け文書に node / sync が現れない grep 検査を CI 化） | M |
| W3-3 | 実機 canary（1 週間） | **ランブック: [`docs/guides/single-resident-canary.md`](../guides/single-resident-canary.md)**（C1〜C10 + 日次観測 + ガイド欠陥記録）。フル 2 台（停止時刻をずらす）+ ワーカー 1 台（POSIX 機）。**セットアップは W1-13 のガイドだけを見て行い、ガイド外の操作が必要になったら全てガイドの欠陥として記録・反映する**（ガイドの受入試験を兼ねる）。チェックリスト: controller 引継ぎ / 全台停止からの復帰 / 予定 drain / 突然死と fencing 拒否 / self-watchdog 発火 / 子の隔離 / スキル起動の併走 / 板委譲の往復（result ペイロード込み）/ Windows 起動ループ方式での VM 復帰 — 各 1 回以上 | M |

**完了条件**: canary で二重実行 0・stale done 0・状態欠損 0 / 全ノードが
「git pull + install.sh」で更新でき、旧バージョンノードが入札しないことを確認。

### W3-1 実施結果（2026-07-25）

**スコープを「配布の統合」に絞った。** exec 断片合成の解消は見送り——あの方式は
「テストが `km.<name>` をモンキーパッチできる単一名前空間」を意図して選ばれたもので
（`agent_project/__init__.py` に根拠を明記）、解消するとテスト 25,704 行の参照モデルごと
張り替えになる。一方 W3-1 の目的（配布パッケージ 1 本・install.sh 1 本）は合成方式に
触らずに達成でき、そこに回帰リスクを積む理由が無い。

- **`tools/agent-tools/install.sh` を新設**し、3 エンジンの installer に散っていた環境チェック・zipapp
  ビルド・agentcore 同梱を 1 本へ集約した。環境チェックは**1 回だけ**走る（従来は同じ警告を
  3 度読ませていた）。engine 固有の付帯物は保持: `codd_gate_*.py` の zipapp 同梱と codd-gate
  同梱インストール（agent-project）・`executors/` の prefix 隣配置（agent-flow）・
  `--service` の systemd unit 生成（agent-project）。`--only <engine>` で 1 本だけも入る
  （ワーカーノードと canary の入れ直しで使う）。
- **各エンジンの `install.sh` はシムへ縮退**（19 行）。既存の手順書・`setup.sh`・自己更新の
  呼び出しパスを壊さないため残し、`tools/agent-tools/install.sh --only <engine>` へ委譲する。
- **agentcore は各 zipapp へ同梱したまま**。3 本は別実行ファイルなので、それぞれが
  自己完結していないと片方だけ動く状態になる（確認: 各 zipapp に agentcore 7 ファイル）。

**着手時に見つけて直した既存不具合（実測で確認）**:

- **自己更新が毎回失敗していた。** `update.py` の sparse-checkout は cone mode で
  `tools/agent-project` だけを取るため `tools/agentcore` が入らず、installer が
  `agentcore パッケージが見つかりません` で die する。失敗は「次回再試行」に落ちるので
  **サイレントに永久に更新されない**——完了条件の「git pull + install.sh で更新でき」が
  そもそも成立していなかった。`update_subdir` をカンマ/空白区切りの複数パス対応にし
  （`split_subdirs`）、既定を `tools/agent-project tools/agentcore` へ。統合インストーラは
  親ディレクトリのファイルなので cone mode で一緒に落ちてくる（テストで固定）。
- **ダイジェストの範囲。** 先頭 subdir だけだと agentcore だけの更新を「変更なし」と読んで
  見送り続ける（契約バージョンを共有する相手だけ古いまま回る）。逆にチェックアウト全体に
  すると、cone mode が拾うリポジトリ直下のファイルで自己増殖ループ（direct state-git 構成では
  自分の state push が update_repo の新コミットになる）が戻る。**宣言した subdir すべて**が
  正しい範囲で、両方をテストで固定した。

### W3-1 追補（2026-07-26）— 共有物を `tools/agent-tools/` へ集約

3 エンジンで共有するものを 1 か所へ寄せた。エンジン固有のものは各エンジンの
ディレクトリに残す（境界を置き場で表す）。

```
tools/agent-tools/
  install.sh      # 3 エンジンをまとめて入れる唯一のインストーラ
  agentcore/      # 共通ライブラリ（transport / protocol / vocab / heartbeat）
  README.md       # 何をここに置くか・自己更新との関係
```

- 各エンジンの `install.sh` シムは `../agent-tools/install.sh` へ委譲する。
- 各エンジンの `__init__.py`（と `resident/status.py`）の path shim は
  `../../agent-tools/agentcore` を見る。zipapp では同梱物が先に解決されるので変化なし。
- **自己更新の `update_subdir` を `tools/<engine> tools/agent-tools` へ**。共有物が
  1 ディレクトリに収まったので、ancestor-file の挙動に頼らず明示的に取れる。
- **agent-flow の自己更新にも同じ修正を入れた**。あちらは `update_subdir` が単一パスのままで、
  agent-project と同じ理由（cone mode は兄弟を含まない → agentcore が取れず installer が die）で
  **毎回サイレントに失敗していた**。`split_subdirs`・複数パスの sparse-checkout・宣言 subdir
  全体のダイジェストを移植し、テストで固定した。

### W3-1 追補（2026-07-26）— `agent-project` の裸起動を `serve` へ

サブコマンド省略時の既定を `run --watch`（cwd 1 件のプロジェクトループ）から
**`serve`（PC 単位の常駐体）**へ変えた。常駐は PC に 1 本で、持つプロジェクトの宣言は
host.yaml が単一ソース——裸起動が cwd 1 件の watch に化けると、常駐体が監督している子と
二重に回って claim を奪い合う。`run` は常駐体が子として起動する経路なので、明示したときだけ動く。

`agent-project --host-config <path>` のようにサブコマンドを省いても `serve` のフラグは
そのまま届く（`TestBareDefault` で固定）。

### W3-2 実施結果（2026-07-26・文書の改訂とテスト分割）

**利用者向け文書から「存在しないコマンド」を一掃した。** W1-9 で消えた
`agent-project instances` / `start` / `stop` / `restart`、W1-9 で消えた `agent-amigos serve` /
`hub`、W2-1 で消えた `gitAutoPush`、W2-4 で消えたルート列挙設定が、手順書にそのまま
残っていた——読んだ人が最初のコマンドで詰まる状態だった。

| 文書 | 直したもの |
|---|---|
| `tools/agent-project/README.md` | 複数プロジェクトの回し方（プロジェクト毎 daemon → host.yaml + 常駐体 1 本）・lifecycle 節・systemd 節・サブコマンド表・`--location` の選択肢 |
| `tools/agent-project/GUIDE.md` | 常駐の起動/確認・複数プロジェクト・稼働確認・トラブルシュート表 |
| `tools/agent-dashboard/README.md` | プロジェクト発見（列挙設定 + instances → `engine/status.json` 1 本・画面からの登録は無い）・稼働判定の順序・`gitAutoPush`（この画面は git へ書かない） |
| `docs/guides/multi-pc-operations.md` | PC 起動時の自動起動・障害対応表・復旧手順 |
| `docs/guides/state-repo-migration.md` | 常駐させる場合の宣言を host.yaml へ |
| `docs/guides/node-id-cutover.md` | 切替前に止めるプロセス |
| `tools/wsl-launcher/README.md` | 起動コマンドと推奨理由（cwd 非依存・PC に 1 本・systemd 案との使い分け） |
| `tools/agent-amigos/README.md`・`install.sh`・`agent-amigos.yaml.example` | 常駐節・hub 節・コマンド表・環境変数表（前段の hub 撤去で実施済み） |

日付入りの設計・計画文書（`docs/plans/2026-07-22-*` 等）は**当時の記録なので直していない**。

**巨大テストファイルを機能別へ分割した。**

| 元ファイル | 行数 | 分割後 | 最大ファイル |
|---|---|---|---|
| `test_agent_project.py` | 13,394 | `_shared.py` + 16 ファイル | `test_project_layer.py` 1,910 行 |
| `test_agent_flow.py` | 7,484 | `_shared.py` + 10 ファイル | `test_executor.py` 1,371 行 |
| `test_agent_amigos.py` | 2,572 | `_shared.py` + 9 ファイル | `test_turns.py` 530 行 |

- **`_shared.py` が共有の前置き**（環境隔離・モジュールのロード・共通ヘルパ・ヘルパ基底クラス）。
  各シャードは先頭 3 行でこれを取り込む。相対 import にしないのは、`discover -s tests` が
  `tests/` 自体を top-level にする＝パッケージとして読まれないため——`sys.path` へ自分の
  ディレクトリを入れてから素の `import` にすれば、`discover -s tests` でも
  `python -m unittest tests.test_<機能>` でも同じに解決する。
- `import *` は `_` 始まりを持ってこないので、使っている private ヘルパだけ明示 import する
  （どのシャードが何を使うかは AST で機械的に求めた）。
- **exec 断片合成には触っていない。** `km` は分割後も 1 つなので、テストの
  `km.<name> = ...` モンキーパッチは分割前と同じに効く（これが W3-1 で合成の解消を
  見送った理由でもある）。
- **分割は行スライスで機械的に行い、等価性を検証した**（`ast` の unparse は書式・コメントを
  落とすので使わない）。`<クラス>.<メソッド>` の集合を分割前後で突き合わせ、3 ファイルとも
  **欠落 0・増加 0**（agent-project 849 / agent-flow 547 / agent-amigos 158）。実行件数も
  分割前と一致（966 / 561 / 158・全て緑）。

> **注意（作業ツリーの同時編集）**: 分割の前段で `test_agent_project.py` から 372 行が
> 別経路で削除されていた（W1-9 の instances レジストリのテスト。実装側 `instances.py` からも
> 該当関数は消えており整合はしている。テスト件数 983 → 966）。分割はこの状態を起点にした。

## 5. 事前検証

| # | 検証 | 判定への影響 | 状態 |
|---|---|---|---|
| V1 | `\\wsl.localhost` への UNC アクセスがディストロを起動し続けるか | keep-alive を保険に格下げできるか（設計 §7） | **未**（実機・canary C9） |
| V2 | agentcore の import 経路（install.sh 配置先での解決方式） | W0-1 の実装方式 | 済（zipapp へ同梱。W3-1 で 1 本のインストーラに集約） |
| V3 | Windows 起動ループ方式の挙動（`wsl.exe` の終了コード伝播・VM 生存・再起動間隔） | W1-11 の選択式セットアップの実装 | **未**（実機・canary C9） |
| V4 | systemd user unit + linger が WSL 起動時に自動で常駐体を上げるか | 起動系 2 案の推奨順 | **未**（実機・canary C9） |

未検証の 3 つはいずれも実機（WSL / Windows / systemd）が要る。
[canary ランブック](../guides/single-resident-canary.md) C9 の記録欄がそのまま検証結果になる。

## 6. 順序の根拠とリスク対応

- **P0 を最初に置く**のは、トポロジ変更（P1）と独立に単体で価値が出る撤退線だから
  （設計 C10）。P0 の間、既存の常駐構成はそのまま動き続ける。
- **P1 に削除を集約する**のは、互換ラッパを作らない前提（設計 §1.3）で新旧共存の
  テスト困難を避けるため。リスクは C6 として引き受け、緩和は §0-5 のコミット列規律と
  データ後方互換（git なので戻せる）で担保する。
- **板 result のペイロード拡張 → submit 削除の順序**は固定（設計 §4.4 — 等価性が
  揃うまで削除を完了扱いにしない）。
- **node_id 統一と語彙統一は静止点イベント**として運用カレンダーに載せ、doctor の
  切替前チェックが通らない限り実施しない。

## 7. 残作業（2026-07-26 時点 / 2026-07-27 改訂）

P0〜P2 は完了。P3 は W3-1 / W3-2 / W3-3 とも完了（W3-2 の R10 検査は R4 で CI 化済み・
W3-3 の実機 canary は実施済み）。**消化済みの項目はこの節から落としてある**（実施の記録と
設計判断は各フェーズの「実施結果」節と、実装側の docstring にある）。

**2026-07-27 改訂**: 初版のこの表と R1 本文は「R3 は残・R1 完了まで進めない」のままだったが、
R3 節自身は完了（削除済みシンボル一覧つき）・R4 も CI 実装済みで、表と本文が自己矛盾していた。
実態（完了側）へ揃える（[積み残し棚卸し](2026-07-27-post-canary-backlog.md) §6-1）。
**2026-07-27（同日・追記）**: R2b も実装した。残っているのは **R6 の各行（契機待ち）だけ**で、
そのうち R2b 待ちだった 2 行（P4-a / P4-b）は解けた。

| # | 残作業 | 由来 | 規模 | 状態 |
|---|---|---|---|---|
| R1 | 実機 canary の実施 | W3-3 | M | ✅ 実施済み（**ランブックの記録欄の記入だけが残り**。棚卸し §1.1） |
| R2 | 板の請負 tick — R2a / R2b とも実装済み | 設計 §4.2 | M | ✅ 完了（R2b は 2026-07-27。下記 R2 節） |
| R3 | 旧経路の削除（agent-flow 側） | W1-9 残 | L | ✅ 完了（下記 R3 節に削除済みシンボル一覧） |
| R4 | R10 の grep 検査と CI | W3-2 残 | S | ✅ 完了（`.github/workflows/ci.yml` + `tools/ci/check_user_docs.py`） |
| R5 | 事前検証 V1 / V3 / V4 | §5 | S | R1 に内包＝実施済み（記録は R1 と同じくランブックへの記入待ち） |
| R6 | 板まわりの積み残し（R2a の実装後に残ったもの） | [S8/S9-4 詳細設計](2026-07-26-s8-s9-4-board-ui-and-doctor-chat-detailed-design.md) §10 | S〜M | 各行の「待ち先」 |

### R1. 実機 canary の実施（W3-3）— 実施済み（記録の記入が残り）

**ランブックは用意済み**: [`docs/guides/single-resident-canary.md`](../guides/single-resident-canary.md)。
C1〜C10 を再現操作・確認コマンド・期待値・記録欄の形に落としてある。実施そのもの
——フル 2 台（停止時刻をずらす）+ ワーカー 1 台（POSIX 機）——は完了した。

**残っているのは実施時のメモからランブックの記録欄（C1〜C10 / §5 欠陥記録 / §6 終了判定）を
埋めること**。結果が実施者の手元にだけある状態は
[コンセプト正典](../designs/agent-tools-concept.md) C1（成果と判断はチームのもの）への違反状態なので、
R2b の実装より先に解消する（棚卸し §1.1）。記入が済むまで、完了条件
（二重実行 0・stale done 0・状態欠損 0）は「実機で確認した」とリポジトリ上は名乗れない。

canary は**セットアップガイドの受入試験も兼ねる**。ガイド外の操作が要ったら全て
ランブック §5 に記録し、[`single-resident-setup.md`](../guides/single-resident-setup.md) へ反映する。

### R2. 板の請負 tick（設計 §4.2）

node 名義での `nodes/<pc>.json` 能力宣言・workload=flow/amigos への入札・落札した仕事の
ノード直轄ワーカー実行。設計 §4.2 が「現状未実装、ここで初めて実装する」と明記した機能。

**設計が固まっていないので意図的に手を付けていない。** 既存の flow/amigos の板参加
（`poll_board`）はいずれも「委譲側の bus へ取り込む」形で、ノード直轄の契約側実行
（bus を持たないノードが落札 → `NodeWorkerPool` で実行 → board へ結果報告）は別物。
中途半端に実装すると二重落札・二重実行になる。

依存: これが入るまで**「旧バージョンノードが入札しない」（設計 §6 最終行）を実機で
確認できない**。現状で確かめられるのは `contract_compatible` の判定と既存の板参加までで、
canary ランブック C10 にその旨を明記してある。

**この R2 待ちで止まっている他計画の作業**（agent シリーズ改良の積み残し。一覧は
[`2026-07-25-agent-improvement-spec.md`](2026-07-25-agent-improvement-spec.md) §4）:

| # | 内容 |
|---|---|
| P1-a | S3-5: 板の `nodes/<node-id>.json` への `repos[].local` 転記 — **その JSON を書く実装が R2 に含まれる**ため、書き手ができるまで転記先が無い |
| P2-a | S5: 「検証不能」（このノードでは確かめられない受入基準）の板への検証委譲 — 判定結果に理由コードは残してあるが、請負実行が無いので接続先が無い |

R2 を実装するときは、この 2 つを同時に繋ぐと board 側の契約を 1 度で固められる。

**設計は固まった（2026-07-26）**: [`2026-07-26-s8-s9-4-board-ui-and-doctor-chat-detailed-design.md`](2026-07-26-s8-s9-4-board-ui-and-doctor-chat-detailed-design.md)（S8 / S9-4 詳細設計）で R2 を 2 つに割った:

| | 内容 | 実施 |
|---|---|---|
| **R2a** | 常駐体の board tick（30s）: 板の同期・`nodes/<pc>.json` の書き出し（能力宣言・心拍）・ノード宛て指示の取り込み（入札 / キャンセル）・入札選別規則の agentcore 一本化 | ✅ **実装済み**（agent シリーズ改良の Phase 4 と同時。`resident_cli.py` の `tick_board` / `agentcore.board` / `agentcore.commands`） |
| **R2b** | ノード直轄実行: プロジェクト 0 個のワーカーノードが落札して `NodeWorkerPool` で実行する経路 | ✅ **実装済み（2026-07-27）**。`resident_cli.py` の `_node_direct_flow_tick`（`~/.agents/flow-node/bus` を唯一の取り込み先に `agent-flow participate` を 1 巡 → 受理した run をプールへ）。同時に繋いだもの: P4-b（検証委譲の後半）・graceful 停止の away 宣言・`board.node_direct`（dashboard の入札ボタンの根拠）。詳細は[棚卸し](2026-07-27-post-canary-backlog.md) §0c |

**R2a で解けた依存**: P1-a（`repos[].local` の板への転記）は完全に解けた——常駐体が
`nodes/<pc>.json` の書き手になった。P2-a（検証委譲）は公示を出す口までで、請け負えるのは
フルノードだけなので残りは R2b。「旧バージョンノードが入札しない」（設計 §6 最終行）は
`agentcore.board.eligible` の `requires.contract_version` 判定として実装済みで、
実機での確認だけが R1 待ちになった。

「中途半端に実装すると二重落札・二重実行になる」への答えは同設計 §6.5——**取り込み済みの判定を
自分のバスから板の `status/<who>.json` へ移す**。現行 `poll_board` は自分のバスしか見ないため、
同一ノードで 2 プロジェクトが同じ板を巡回すると同じ公示を二重に取り込む（既に壊れている）。

### R3. 旧経路の削除（W1-9）— 完了

削除済み: agent-amigos（`serve` / `hub` / `hubbus`）、agent-project（`instances` / `start` /
`stop` / `restart` / `location{daemon,remote}` / `act_async` / `manage_flow_daemon` /
`flow_max_workers` / `lock_dir` / `ensure_flow_daemon`）、agent-flow（`submit` / `daemon` /
daemon singleton ロック / `write_daemon_status` / 裸起動の既定）。

#### 調査で覆った前提

**1. agent-project は agent-flow を import していない。** `resolve_agent_flow` で外部コマンド
として起動する（別 venv・別バージョンでも動く）。flow の駆動ループを常駐体やプロジェクト子の
**プロセス内へ移すことはできない**——「常駐体がバス毎の実行コンテキストを持つ」案も「子の中に
駆動スレッドを立てる」案も、この境界で成立しない。

**2. daemon の 5 tick のうち 3 つは、既に別の場所に実装がある。**

| tick | 実体のある場所 |
|---|---|
| heartbeat | `orchestrate.py` の `heartbeat()` — orchestrator が自分でリースを張る |
| park 監視 | `run.py` の `cmd_run` が `service_waits(only_runs=[run_id])` を回す |
| 停滞 run 回収 | `cmd_run` の入口が `run_is_orphaned` → `retry_failed()` |

`local`（＝`agent-flow run` 単発）は**自己完結**であって daemon を必要としない。daemon は
暖機ワーカー再利用の最適化だった。

#### 実測（`run --watch` の 1 パス長）

`sandbox-agent-state/.agent-project/journal.md` の `project 開始`→`project 停止` 56 件:

```
min=2s  median=9s  p90=4451s  max=12141s   （>120s が 12/56 = 21%）
```

完全な二峰性（11 秒以下 43 件 / 120 秒以上 12 件 / 中間 1 件）。`_run_lease_window` は
`max(poll*10, 120.0)` ＝ **最低 120 秒**なので、watch ループ本体に tick を差し込むと長い
パスの実行中に必ずリースが切れ、他ノードが孤児と誤判定して run を奪う（二重実行）。
**駆動と待機を同じ制御フローに置けない**。

#### 採った形

受理と実行を別プロセスに分け、周期駆動だけを常駐体が持つ（amigos 参加 tick と同型）:

```
常駐体 flow tick（5s）
  → agent-project flow-participate --root R --running <ids>
      → agent-flow participate --json     … cancel 受理 / park 再確認 / 孤児・auto-heal の
                                            引き継ぎ判断 / 板巡回 / inbox 受理（run は実行しない）
  → 受理された run-id を NodeWorkerPool へ投入
      → agent-project flow-run --root R --run-id X
          → agent-flow --run-id X run --from-inbox   … 完了まで走る（自分でリースと park を持つ）
```

- `--running` に「走っている＋起動待ち」の run-id を渡す（`NodeWorkerPool.busy_ids`）。
  渡さないと、枠が空くのを待っている run を毎周『駆動者が居ない孤児』と読んで
  `max_resumes` を焼き切り failed に確定する。
- `--from-inbox` は要求文・書込先ワークスペース・参照リポジトリ・引き継ぎ元を inbox 要求から
  読む。argv へ転記させると、項目が増えるたびに転記漏れが静かな機能欠落になる。
- `flow-participate` / `flow-run` は常駐体の内部配線なので `help` から隠す（利用者向けの
  語彙を増やさない）。プロジェクト設定の解決を agent-project 本体に閉じるための薄い層。
- `bus/inbox` は agent-dashboard の委譲アダプタが公式契約として書き込む先でもある。この
  配線が入ったことで消費者が復活した（daemon 削除の前提条件だった）。

### R4. R10 の grep 検査と CI（W3-2 の残り）— 完了

利用者向け文書に内部名（node / sync / resident）が現れないことを機械で検査する。

**実装済み**: `.github/workflows/ci.yml` が 4 パッケージのテスト（agent-project / agent-flow /
agent-amigos / agentcore）+ agent-dashboard の `npm test` + 利用者向け文書の検査を回し、
検査本体は `tools/ci/check_user_docs.py`（自身のテストは `tools/ci/tests/`）が持つ。
検査対象は `docs/guides/` と主要 README——セットアップガイドと canary ランブックを含む。

（初版の「この repo には CI 設定が存在しない」という記述は当時の事実。CI 強化の残り
——eslint / 新しい python 版 / concurrency / キャッシュ、検査対象の拡張——は
[棚卸し](2026-07-27-post-canary-backlog.md) §4 の契機待ちに移した。）

### R5. 事前検証 V1 / V3 / V4（§5）

R1 に内包される（canary ランブック C9 の記録欄がそのまま検証結果になる）。単独では動かさない。
canary は実施済みなので、**残っているのは R1 と同じく記録欄の記入だけ**。

### R6. 板まわりの積み残し（R2a の実装後に残ったもの）

R2a（板 tick・ノード宛て指示・入札選別の一本化）と S8 / S9-4 の UI を実装したあとに残った分。
出典は [S8/S9-4 詳細設計 §10](2026-07-26-s8-s9-4-board-ui-and-doctor-chat-detailed-design.md)で、
**この計画に効くのは R2b を待つ 2 行**（P4-a / P4-b）。残りは「動作は正しいが最適でない」類なので、
必要が出たときに拾う。

| # | 内容 | 待ち先 | 規模 |
|---|---|---|---|
| P4-a | ~~**R2b: ノード直轄実行**~~ → ✅ **実装済み（2026-07-27）**。ワーカーノードは `~/.agents/flow-node/bus` を取り込み先に落札 → 実行 → 報告まで通る。dashboard の可否判断は `board.intake_projects` に `board.node_direct` を足して両方を見る | 済 | M |
| P4-b | ~~**検証委譲の後半**~~ → ✅ **実装済み（2026-07-27・R2b と同時）**。`unverifiable` は人検収へ直行せず、まず板へ**検証だけ**を公示する（成果の変更は依頼しない）。返ってきた判定は成果コミット（rev）ごとの受理点 `verifications/<task-id>/<rev>.external.json` に置かれ、次の settle が同じ rev の検証として受理する。板が無い・決着しない場合だけ従来どおり人へ | 済 | S |
| P4-c | `submitPost` / `award` の `git+` 板対応 — dashboard に手動 post の UI が無いので今回触っていない。`board-award` 指示の契約だけ用意済み | owner-picks を使い始めたとき | S |
| P4-d | 投機同時実行（speculation）— 契約からは W0-10 で削除済み。板設計の P2 のまま | 必要が出たとき | M |
| P4-e | push 配信（forge webhook / hub long-poll）— 30 秒ポーリングで足りている。板設計 §5.3 の「加速装置」 | 遅いという申告が出たとき | M |
| P4-f | 対話診断を `consultation` / `plan-critique` / `delivery-rationale` へも広げる — いずれも構造化見出しの抽出に依存するので、対話にすると抽出点が消える | 抽出をやめてよいと判断できたとき | S |
| P4-g | 対話診断セッションの掃除 — 使い捨て（`no_session_args`）なので状態は残らないが tmux セッションは溜まる。名前での一括 kill は付けていない | セッションが溜まって困ったとき | S |

**R1（実機 canary）との関係**: 「旧バージョンノードが入札しない」（設計 §6 最終行）は
`agentcore.board.eligible` の `requires.contract_version` 判定として**実装済み**になった。
canary ランブック C10 の「現状では確かめられない」という注記は、R2b が入って
ワーカーノードが実際に入札するようになった時点で外せる。

### 着手しないと決めたもの

- **exec 断片合成の解消**（W3-1 の原文）。あの方式は「テストが `km.<name>` を
  モンキーパッチできる単一名前空間」を意図して選ばれたもので、解消するとテスト資産の
  参照モデルごと張り替えになる。W3-1 の目的（配布 1 本）は合成に触らず達成できたので、
  そこに回帰リスクを積む理由が無い（判断の詳細は §4 W3-1 実施結果）。

## 8. 設計 §6 障害と回復表 ×既存テストの対応（W1-12）

設計 §6 の各行に対応する既存テストを列挙する（新規追加ではなく、既存資産の棚卸し）。
一部は正確なテスト名確認までで、内容までは今回精査していない。

| 事象 | テスト |
|---|---|
| 常駐体のクラッシュ | 回復は起動系＝OS 責務。doctor 構成検査 `test_residency_findings_flags_missing_unit` と、孤児化の事実を固定する `ResidentCliTests.test_parent_kill_leaves_child_orphaned_and_next_start_recovers` |
| 常駐体のハング（self-watchdog） | `test_resident_scheduler.py::test_self_watchdog_aborts_on_stall` |
| プロジェクト子のクラッシュ | `test_resident_supervisor.py::test_start_and_crash_is_restarted` |
| プロジェクト子のハング | `test_resident_supervisor.py::test_hang_detected_via_is_healthy_and_restarted` |
| 子の連続クラッシュ→隔離 | `test_resident_supervisor.py::test_quarantine_after_repeated_deaths` |
| 実行中 run の孤児化 | `test_daemon.py::OrphanRecoveryTests.test_orphan_inbox_run_is_resumed_not_failed`（関連多数） |
| git ロック残骸・中断 rebase | `tools/agentcore/agentcore/tests/test_transport.py::TestSelfHealing.test_stale_lock_is_removed_and_recovered` / `.test_interrupted_rebase_is_aborted_on_reuse` |
| クローン破損 | `test_transport.py::TestSelfHealing.test_corrupted_object_triggers_rebuild` |
| push 競合 | `test_transport.py::TestCloneAndSync.test_concurrent_push_resolves_via_rebase_no_force` |
| リモート不通（fail-close） | `test_coordination.py::TestAtomicClaim.test_peer_present_with_unreachable_origin_fails_closed` |
| PC の計画停止（drain/away） | `test_state_git.py::TestDirectStateGit.test_draining_node_releases_controller_for_another_node` / `test_resident_supervisor.py::test_graceful_shutdown_sequences_all_steps_after_stopping_children` / `ResidentCliTests.test_planned_stop_pauses_child_instead_of_counting_deaths`（親が止める・隔離に化けない） |
| PC の突然死（lease 失効・fencing） | `test_state_git.py::TestDirectStateGit.test_controller_lease_moves_after_expiry` / `.test_distributed_claim_has_one_winner_and_persists_fence` / `.test_stale_claim_token_cannot_settle` |
| 全 PC 停止 | 専用テスト無し（ローカル滞留は各所の意図しない push 抑止テストで間接カバー） |
| forge 停止 | `test_state_git.py::TestDirectStateGit.test_unreachable_remote_is_unknown_not_lost` / `.test_settle_with_unreachable_remote_preserves_work_for_human` |
| WSL VM 停止 | 無し（実機 canary 待ち。上記 V1 未検証と同根） |
| ディスク肥大→gc | `test_resident.py::ResidentCliTests.test_gc_tick_isolates_project_sweeper_failure` / `test_resident_status.py::test_run_gc_aggregates_and_isolates_failures` |
| 時計ずれ | `test_state_git.py::TestDirectStateGit.test_controller_lease_tolerates_clock_skew_before_reclaiming` |
| 更新漏れの古いノード（契約バージョン） | `test_resident_status.py::test_contract_compatible` |

**回復そのものを検証するテストが無い行**: 「常駐体のクラッシュ」「全 PC 停止」「WSL VM 停止」
の 3 行。実機/OS 領域で単体テスト化が原理的に困難（W3-3 の canary が受け皿）。うち
「常駐体のクラッシュ」だけは**構成の事前検査**（起動系が上げ直す設定になっているか）を
`test_residency_findings_flags_missing_unit` が押さえており、回復動作そのものが未検証。

「時計ずれ」は穴を確認後にテストを追加済み（許容幅の内側では横取りしないことを固定——
従来は許容幅を過ぎた後の横取りしか検証していなかった）。
