# 常駐一本化 実装計画

- 日付: 2026-07-24
- 状態: 提案
- 対応設計: [`2026-07-24-single-resident-controller-design.md`](./2026-07-24-single-resident-controller-design.md)（改訂 6）。
  本書は設計 §8 の 4 フェーズを、対象ファイル・作業項目・完了条件つきの作業計画に分解する。
- 規模の目安: S = 半日〜1 日 / M = 2〜4 日 / L = 1 週間級（1 人での目安）

## 0. 進め方の原則

1. **フェーズ末ごとに全テスト緑**を回復してから次へ進む（現状: flow 528 / amigos 140 /
   project 801 件。移植に伴い数は増減する）。
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

目標: 常駐を 1 本にし、旧常駐・location・instances 等を一括削除する。最大のフェーズ。

| # | 作業 | 対象・内容 | 規模 |
|---|---|---|---|
| W1-1 | スケジューラ | `resident/scheduler`: 周期表（コード定数）・tick 種別ごとの single-flight・ステップ毎タイムアウト・例外隔離・**内蔵 self-watchdog**（心拍停止 → 自ら abort。systemd 配下では sd_notify も打つ） | M |
| W1-2 | flow の tick 抽出 | `agent_flow/daemon.py` のループ本体を cancel / orphan-adopt / auto-heal / board / heartbeat の tick 関数群へ分解（primitives は既に独立関数。ordering 制約 — cancel 受理 → 孤児回収 — をテストで固定） | M |
| W1-3 | amigos の tick 化と drive 新設 | `cycle()` から手番実行を分離（claim/心拍/away tick + 手番のワーカー投入）。**単発駆動 `agent-amigos drive`** を新設（現 `serve --cycles` の骨格から常駐化 — デーモンロック・シグナル常駐 — を除去。インライン実行のまま） | M |
| W1-4 | スーパーバイザ | 子（プロジェクトループ）の起動・心拍鮮度によるハング検知・再起動・指数バックオフ + 隔離（quarantine）・graceful 停止の一括処理（claims 解放 → controller lease 解放 → away 宣言 → 板 status away → 最終 push）。既存の自殺型停止経路（availability の自 SIGTERM・self-update execv・グローバル drain フラグ）を「親 → 子への指示」へ置換 | L |
| W1-5 | ノード直轄ワーカー | 板落札の実行をロール共通のワーカー実行へ（プロジェクト子へ渡さない）。ノード全体 `max_concurrent` セマフォ（計数は status/run ファイルから導出） | M |
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

## 3. P2 — dashboard 縮退

| # | 作業 | 対象・内容 | 規模 |
|---|---|---|---|
| W2-1 | git 書き込みの削除 | `base/main/git.js` の pull / commitPush（renderer の `gitPushAfterWrite` / `gitPushBusOp` 28 呼び出し箇所ごと）/ heal 実行 / `gitAutoPush` を削除。`diffRange` / `diagnostics` は読み取り専用モジュールへ分離して存続 | M |
| W2-2 | 本体 CLI 起動の削除 | `dashboard:start` を削除し、status 鮮度による「エンジン停止中（起動コマンド: `agent-project serve`）」案内表示へ | S |
| W2-3 | ロックプローブの削除 | `flowLockDir` 設定 UI と `flow.js` のロック鍵導出複製（`daemonLockPath` / `daemonStatus` / `stopDaemon`）を削除。稼働表示は `engine/status.json` へ一本化 | S |
| W2-4 | プロジェクト発見の切替 | ルート列挙設定を削除し、`engine/status.json` からプロジェクト（root・UNC 変換済みパス）を発見。設定は「ディストロ / ベースパス + 表示設定」へ縮退。`/mnt/c` 経路サポートも削除 | M |
| W2-5 | 表示の付け替え | 🩺 → 自動回復の状況表示 + `commands/heal` 投函。隔離マーク・behind・旧バージョンノードの表示 | S |

**完了条件**: dashboard のコードから git 書き込み・本体起動・ロック複製が消滅 /
利用者向け表示に内部名（node / sync / resident）が出ない（R10） / dashboard テスト緑。

## 4. P3 — パッケージ統合と実機 canary

| # | 作業 | 対象・内容 | 規模 |
|---|---|---|---|
| W3-1 | 単一パッケージ統合 | 3 エンジンの exec 断片合成を解消し、配布パッケージ `agent-project` へ統合。CLI エントリは `agent-project` / `agent-flow` / `agent-amigos` の 3 本を維持（R9・R10）。インストールは install.sh の 1 本のまま | L |
| W3-2 | テスト・文書の再編 | 巨大単一テストファイル × 3 を機能別に再編。README・GUIDE 等の全面改訂・**セットアップガイドの確定版**（W1-13 のドラフトに canary での躓きを反映）・**R10 チェック**（セットアップガイド含む利用者向け文書に node / sync が現れない grep 検査を CI 化） | M |
| W3-3 | 実機 canary（1 週間） | フル 2 台（停止時刻をずらす）+ ワーカー 1 台（POSIX 機）。**セットアップは W1-13 のガイドだけを見て行い、ガイド外の操作が必要になったら全てガイドの欠陥として記録・反映する**（ガイドの受入試験を兼ねる）。チェックリスト: controller 引継ぎ / 全台停止からの復帰 / 予定 drain / 突然死と fencing 拒否 / self-watchdog 発火 / 子の隔離 / スキル起動の併走 / 板委譲の往復（result ペイロード込み）/ Windows 起動ループ方式での VM 復帰 — 各 1 回以上 | M |

**完了条件**: canary で二重実行 0・stale done 0・状態欠損 0 / 全ノードが
「git pull + install.sh」で更新でき、旧バージョンノードが入札しないことを確認。

## 5. 事前検証（P1 着手前に潰す）

| # | 検証 | 判定への影響 |
|---|---|---|
| V1 | `\\wsl.localhost` への UNC アクセスがディストロを起動し続けるか | keep-alive を保険に格下げできるか（設計 §7） |
| V2 | agentcore の import 経路（install.sh 配置先での解決方式） | W0-1 の実装方式 |
| V3 | Windows 起動ループ方式の挙動（`wsl.exe` の終了コード伝播・VM 生存・再起動間隔） | W1-11 の選択式セットアップの実装 |
| V4 | systemd user unit + linger が WSL 起動時に自動で常駐体を上げるか | 起動系 2 案の推奨順 |

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

## 7. P1 進捗と残作業（2026-07-25 時点）

W1-1〜W1-13 は主要部の実装・テストとも完了（agent-project 956 / agent-flow 547 /
agent-amigos 159 / agentcore 48 が全て green・fail/error ゼロ）。ただし
**W1-4・W1-5・W1-12 に未達項目が残る**（下記「未実装」の該当行。W1-4 の自殺型停止経路は
実害あり）。
以下は完了スコープの内側で見送った・未実装の項目。
利用者向けの詳細は [`docs/guides/single-resident-setup.md`](../guides/single-resident-setup.md) §6 も参照。

### 未実装（次の作業）

- **【W1-4 の未達・実害あり】自殺型停止経路が「親 → 子への指示」へ置換されていない。**
  W1-4 の作業項目に明記されているが未実施で、`availability` を設定した PC で
  **計画停止が隔離（quarantine）に化ける**。経路: 子（`run --watch`）の
  `start_availability_monitor` が `shutdown_due` で **自分に SIGTERM を送る**
  （`coordination.py` の `os.kill(os.getpid(), signal.SIGTERM)`）→ Supervisor は終了コードを
  見ずに死亡と判定（`check_health`）→ backoff 後に再起動 → 新しい子が 1 秒以内に同じ
  `shutdown_due` を検知して再び自殺 → `quarantine_after`（既定 5・window 600s）に到達して
  隔離。隔離の自動解除は無いので、夜間停止のたびに人が `serve` を上げ直すまでその
  プロジェクトが止まる。W1-4 の設計どおり「止めるかどうかは常に親が決める」形
  （`Supervisor.stop` の docstring が既にそう宣言している）へ寄せる必要がある。
  最小の直し方は、子の availability 監視を常駐体側の tick に移し、`shutdown_due` を
  親が判定して `Supervisor.stop(name)` を呼ぶこと。`update.py` の `os.execv` による
  自己再起動は PID が変わらないため Supervisor からは死に見えず、こちらは実害なし。
- **板の請負 tick**（node 名義での `nodes/<pc>.json` 能力宣言・workload=flow/amigos への
  入札・落札した仕事のノード直轄ワーカー実行）。設計 §4.2 で「現状未実装、ここで初めて
  実装する」と明記された機能。既存の flow/amigos の板参加（`poll_board`）はいずれも
  「委譲側の bus へ取り込む」形で実装されており、ノード直轄の契約側実行（bus を持たない
  ノードが落札 → NodeWorkerPool で実行 → board へ結果報告）はまだ設計が固まっていない。
  中途半端に実装すると二重落札・二重実行のリスクがあるため意図的に手を付けていない。
- **旧経路の削除**（設計 P1 行に明記済み）: `agent-flow` の `daemon`/`submit`/`location`/
  `act_async`、`agent-amigos` の `serve`/`hub`、`agent-project` の
  `instances`/`start`/`stop`/`restart`。resident（`serve`/`status`/`worker` + amigos 参加
  tick + gc tick）が実地で安定してから、3 パッケージのテスト資産（daemon 前提テストが
  project 側だけで数十件規模）ごと計画的に削るべき規模の変更のため、今回のスコープでは
  見送った。W1-2 で tick 関数へ抽出した `_tick_cancel` 等（flow daemon 内蔵）は、
  daemon 削除後に呼び手が無くなるため同時に整理する。
- **板の終端公示の gc**（設計 §4.2 gc tick の対象に「終端した公示」が明記されているが未実装）。
  `board/delegations/<id>/` は `result.json`/`cancelled.json` が付いても誰も削除しない
  ため無限に積み上がる。現状の gc tick（今回追加）はプロジェクトの flow バス掃除
  （`agent-flow cleanup`/`gc`）のみで、板そのものの掃除は範囲外。板の請負 tick と同様、
  誰が・いつ・どの安全マージンで消すか（他ノードが遅延同期中に消すと結果を取りこぼす）が
  未検討のため見送った。
- **systemd `Type=notify` + `WatchdogSec`**（sd_notify によるハング監視の外部二重化）。
  常駐体内蔵の self-watchdog による自己 abort が主経路のため、無くても設計 §7 の
  「(a) 起動時に上がる (b) 死んだら上げ直される」は満たされる。
- **§5 事前検証 V1・V3・V4 が未検証のまま**（実機 WSL/Windows/systemd 環境が要る）。
  `install.sh --service` は書いたが `loginctl enable-linger` 込みで実機通しの確認はして
  いない（V4）。Windows タスクスケジューラの `wsl.exe` 終了コード伝播・再起動間隔（V3）、
  UNC アクセスがディストロを起動し続けるか（V1）も未検証——ガイドにはその旨明記済み
  （`docs/guides/single-resident-setup.md` §4b の「要検証」）。W3-3 の実機 canary で
  初めて検証される。
- **【W1-12 の未達】C14 併走テストとカオステストが未新設。** W1-12 の作業項目は
  「C14 併走テスト（スキル起動 run × 常駐体の claim 排他・孤児回収）」と
  「カオステスト（親 kill / 子 kill / ハング注入 / 電源断相当のクローン破損）」の新設を
  挙げているが、実際に足したのは §6 回復表の対応付け（既存資産の棚卸し）まで。
  現状の充足は部分的で、子 kill は `test_resident_supervisor.py`、ハング注入は同
  `test_hang_detected_via_is_healthy_and_restarted`、クローン破損は
  `test_transport.py::test_corrupted_object_triggers_rebuild` が押さえるが、
  **親 kill（常駐体を落として子が孤児化しないか）と C14 併走は対応テストが無い**。
  併走は `agent-amigos drive`（W1-3 で新設）と常駐体の amigos 参加 tick が同じ bus を
  同時に触る経路で、claim の排他が効くかを検証していない。
- **【W1-5 の設計差異】NodeWorkerPool の同時実行計数がプロセス内カウンタ。**
  W1-5 は「計数は status/run ファイルから導出」と規定するが、実装はプロセス内の
  in-flight dict（`resident/worker.py` の docstring に明記済み）。常駐体が唯一の実行主体で
  ある限り正確だが、**スキル起動の単発実行（`drive` / `run --once` を人が直接叩く）は
  この計数に入らない**ため、C14 の併走時にノード全体の `max_concurrent` を超えうる。
  上の C14 併走テストと同じ経路の話なので、併せて検討する。

### 実装済み（W1-11 残作業として今回追加）

amigos 参加/手番分離（`agent-amigos participate` + 既存 `run --once` へのワーカー投入）・
`agent-flow cleanup`/`agent-project gc`（新設 CLI・resident gc tick から利用）・doctor の
常駐化構成検査・`install.sh --service`（systemd user unit 生成）・セットアップガイド。
詳細は各コミットの docstring・`docs/guides/single-resident-setup.md` を参照。

### §6 障害と回復表 ×既存テストの対応（W1-12）

設計 §6 の各行に対応する既存テストを列挙する（新規追加ではなく、既存資産の棚卸し）。
一部は正確なテスト名確認までで、内容までは今回精査していない。

| 事象 | テスト |
|---|---|
| 常駐体のクラッシュ | 無し（起動系＝OS 責務。doctor 構成検査のみ: `test_agent_project.py::test_residency_findings_flags_missing_unit`） |
| 常駐体のハング（self-watchdog） | `test_resident_scheduler.py::test_self_watchdog_aborts_on_stall` |
| プロジェクト子のクラッシュ | `test_resident_supervisor.py::test_start_and_crash_is_restarted` |
| プロジェクト子のハング | `test_resident_supervisor.py::test_hang_detected_via_is_healthy_and_restarted` |
| 子の連続クラッシュ→隔離 | `test_resident_supervisor.py::test_quarantine_after_repeated_deaths` |
| 実行中 run の孤児化 | `tools/agent-flow/tests/test_agent_flow.py::OrphanRecoveryTests.test_orphan_inbox_run_is_resumed_not_failed`（関連多数） |
| git ロック残骸・中断 rebase | `tools/agentcore/agentcore/tests/test_transport.py::TestSelfHealing.test_stale_lock_is_removed_and_recovered` / `.test_interrupted_rebase_is_aborted_on_reuse` |
| クローン破損 | `test_transport.py::TestSelfHealing.test_corrupted_object_triggers_rebuild` |
| push 競合 | `test_transport.py::TestCloneAndSync.test_concurrent_push_resolves_via_rebase_no_force` |
| リモート不通（fail-close） | `test_agent_project.py::TestAtomicClaim.test_peer_present_with_unreachable_origin_fails_closed` |
| PC の計画停止（drain/away） | `test_agent_project.py::TestDirectStateGit.test_draining_node_releases_controller_for_another_node` / `test_resident_supervisor.py::test_graceful_shutdown_sequences_all_steps_after_stopping_children` |
| PC の突然死（lease 失効・fencing） | `test_agent_project.py::TestDirectStateGit.test_controller_lease_moves_after_expiry` / `.test_distributed_claim_has_one_winner_and_persists_fence` / `.test_stale_claim_token_cannot_settle` |
| 全 PC 停止 | 専用テスト無し（ローカル滞留は各所の意図しない push 抑止テストで間接カバー） |
| forge 停止 | `test_agent_project.py::TestDirectStateGit.test_unreachable_remote_is_unknown_not_lost` / `.test_settle_with_unreachable_remote_preserves_work_for_human` |
| WSL VM 停止 | 無し（実機 canary 待ち。上記 V1 未検証と同根） |
| ディスク肥大→gc | `test_agent_project.py::ResidentCliTests.test_gc_tick_isolates_project_sweeper_failure` / `test_resident_status.py::test_run_gc_aggregates_and_isolates_failures` |
| 時計ずれ | `test_agent_project.py::TestDirectStateGit.test_controller_lease_tolerates_clock_skew_before_reclaiming` |
| 更新漏れの古いノード（契約バージョン） | `test_resident_status.py::test_contract_compatible` |

**回復そのものを検証するテストが無い行**: 「常駐体のクラッシュ」「全 PC 停止」「WSL VM 停止」
の 3 行。実機/OS 領域で単体テスト化が原理的に困難（W3-3 の canary が受け皿）。うち
「常駐体のクラッシュ」だけは**構成の事前検査**（起動系が上げ直す設定になっているか）を
`test_residency_findings_flags_missing_unit` が押さえており、回復動作そのものが未検証。

「時計ずれ」は穴を確認後にテストを追加済み（許容幅の内側では横取りしないことを固定——
従来は許容幅を過ぎた後の横取りしか検証していなかった）。
