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
