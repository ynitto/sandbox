# 常駐一本化設計案 — 1 PC = 1 ノードデーモン、エンジンはライフサイクル実行体に

- 日付: 2026-07-24
- 状態: **提案・改訂 2**（改訂 1 = B 案採用: 常駐単位を「PC × プロジェクト」から「PC」へ変更、
  共通転送層 agent-sync を P0 に前置。改訂 2 = 4 ツールのコード照合レビューを反映 — §0。
  初版 A 案との比較は §10）
- 動機: 構成・設定バリエーションの爆発と git 転送実装の重複がバグの温床になっている（§1）。
  エンジン常駐を必須と割り切り、**常駐プロセスを「1 PC = agent-node 1 本」に集約**する。
- 関連: [`2026-07-23-delegation-board-distributed-bidding-design.md`](./2026-07-23-delegation-board-distributed-bidding-design.md)（board 契約は不変）、
  [`2026-07-22-agent-project-multi-node-daemon-design.md`](./2026-07-22-agent-project-multi-node-daemon-design.md)（coordination / controller lease — §6 で必須既定化）、
  [`schemas/board.schema.json`](../../schemas/board.schema.json)、
  [`schemas/delegation.schema.json`](../../schemas/delegation.schema.json)

## 0. 改訂 2 の変更点 — コード照合で判明した事実と設計修正

改訂 1 を 4 ツールの実装（agent-flow / agent-project / agent-amigos / agent-dashboard）と
突き合わせた結果、**B 案の骨格（PC 単位ノードデーモン + 周期表 + agent-sync）は妥当**と
確認できた。tick 化の下地は想定より良い: amigos の `NodeDaemon.cycle()` は既に純粋な
単発 tick でテスト 40 箇所が tick 駆動（常駐ループ `run()` を使うテストは 0）、flow の
daemon ループ本体も primitives（`service_waits` / `_adopt_orphan_runs` / `_heal_failed_runs` /
`poll_board`）は独立関数としてテスト済みで、実デーモン起動が要るテストは flow 528 件中
6 件しかない。一方で以下を修正した:

1. **P0 の範囲を縮小**: agent-sync は「実装の統一」のみとし、**クローン共有
   （PC 内 1 クローン化）は P2 へ後送**。複数常駐が残る P0 時点で 1 クローンを共有すると
   プロセス間排他という新しい故障面が増える（§3.1）。
2. **「実体は `agent-project serve --all`」の表現を撤回**: serve / serve --all は現存せず、
   1 プロセス = 1 プロジェクトが構造的に強制されている（`doctor.py:873` の重複起動拒否・
   instances レジストリ）。agent-node は**新規のスーパーバイザ**であり、既存 `run_loop` を
   子として呼び出す（§3.2）。
3. **dashboard 原則の精密化**: 「git を一切使わない・CLI を一切起動しない」から
   「**書き込み側の git 同期をしない・本体の制御につながるプロセス起動をしない**」へ。
   読み取り専用 git（受入 diff 表示）と読み取り専用 AI 補助（charter / doctor /
   taskAssist の CLI スポーン）は存置する。これにより `requests/` 往復契約は v1 から
   外れ、懸念 C6 はほぼ解消する（§3.5、§4）。
4. **remote submit 廃止の等価性の穴を塞ぐ**: 消えるのは submit だけでなく
   **result 読み戻し IPC**（`read_reject_guidance` / `read_brief_discoveries` —
   gitlab の reject→retry ループが依存）も含まれる。板の `result.json` に構造化
   ペイロードを additive に載せて等価性を回復する（§3.3、C8）。
5. **daemon ロックのドメイン再設計を明記**: 現行ロックはバス単位の flock singleton。
   ノードデーモンは N バスを束ねるため、移行期の旧 daemon との相互排他を保つには
   **バス単位ロックを維持したまま親が N 個保持**する（§3.2）。dashboard 側にロック鍵
   導出の**手写し複製**（`flow.js:872-918`）があり、廃止対象に追加（§7）。
6. **周期表スケジューラの実行規約を明文化**: 長時間作業（amigos の手番実行・act）は
   tick 内でインラインに走らせない。tick は「請求・心拍・投入」だけを行い、実行は
   ワーカー（子プロセス / スレッドプール）へ移す（§3.2）。
7. **「間隔律速 fetch/pull」の所在の訂正**: これは GitBus の性質ではなく呼び出し側
   （daemon ループ / state_sync）にある。agent-sync への取り込みは「移植」ではなく
   **挙動の移動**であり、「失敗時は間隔クロックを進めない」不変条件ごと移す（§3.1）。
8. **git-file-sync の扱いを明記**: 状態 repo の第 3 の書き手になりうる常駐ツールとして
   §1.1 に挙げながら行き先が未定義だった。状態 repo・板への使用を禁止し doctor で
   検知する（§7、C11）。
9. **coordination 必須化の中身を具体化**: opt-in 分岐の反転に加え、07-22 設計の
   controller lease（制御面は 1 ノードのみ）が B 案の「全 PC の子ループが ingest する」と
   どう両立するかを明記（§6）。availability モニタの「自プロセス SIGTERM」等、
   スーパーバイザ化と非互換な自殺型停止経路の作り直しも列挙（§3.2）。
10. **数字の更新**: テストは flow **528**（旧記載 519）/ amigos 140 / project 801。
    転送実装は「4 種」ではなく **5 実装 + git-file-sync**（§1.2）。node_id の現状は
    「`<pc>-<プロジェクト>` 規約」ではなく flow = `host-pid`（設定で固定可）・
    amigos = `hostname-乱数` 永続化・project は入札しない、という**ツール毎の不揃い**（C9）。

## 1. 動機 — 何がバグの温床か

### 1.1 構成の直積

同じ結果を得る経路が複数あり、その直積が構成空間になっている:

| 軸 | 現在のバリエーション |
|---|---|
| act の実行モード | `location: auto / local / daemon / remote / board` × `act_async` |
| 状態の共有 | state_git 管理クローン / direct モード / 非 git（同期なし） |
| 状態を push する主体 | agent-project の state_git / **dashboard の commitPush（`gitAutoPush`）** / git-file-sync — 最大 3 者が同一リポジトリの書き手 |
| flow の実行形態 | 単発 run / ローカル daemon / GitBus 分散 daemon / hub |
| amigos の実行形態 | serve 常駐 / hub long-poll |
| board のクローン | agent-project・agent-flow・agent-amigos・dashboard が**各自別クローン** |
| dashboard → 本体の経路 | ファイルドロップ / CLI 直接実行（start・agent:*）/ git push |

### 1.2 転送実装の重複

直近で直した 4 件のバグ（cancelled の綴り不一致・板 result の cancelled 成功扱い・
dashboard 投函が push されない・板クローンの rebase 残骸）の根因は、プロセス配置ではなく
**git 転送コードが 5 実装ある**ことだった: GitBus（agent-flow）・StateGit / DirectStateGit
（agent-project）・BoardRepo（agent-project）・BoardMirror（agent-amigos）・`git.js`
（dashboard）。加えて汎用ツール git-file-sync が第 6 の書き手になりうる。GitBus だけが
電源断・ロック残骸・オブジェクト破損への回復を持ち、他は独立に書かれた劣化コピーで、
同じ穴を別々に踏む（BoardMirror の `_recover` は自ら「GitBus と同じ技法・**別実装**」と
名乗っている）。stale lock 閾値さえ揃っていない（GitBus / BoardRepo / BoardMirror = 30s、
StateGit = 300s）。語彙ズレ（canceled/cancelled）も「同じ責務の複数実装」という同根の症状である。

なお「間隔律速 pull」は GitBus の機能ではなく、呼び出し側（flow daemon ループの
`next_*` タイマ・`state_sync` のクロック）が持っている。統一時はこの律速も
転送層へ移す（§3.1）。

したがって本設計は 2 段で攻める: **(P0) 転送を 1 実装に統一**し、**(P1〜) 常駐を 1 本に統一**する。

## 2. 新原則（4 行）

1. **転送は 1 実装**: git の clone / 間隔律速 pull / rebase・ロック残骸回復 / 破損時再クローン /
   push リトライを共通ライブラリ **agent-sync**（Python）に集約し、Python 3 ツールは
   これだけを使う。dashboard の `git.js` は置換ではなく**廃止**（P3 — 書き手が
   ノードデーモンに一本化されるため）。
2. **常駐は 1 PC に 1 本**: プロジェクトを使う各 PC で **agent-node**（新規スーパーバイザ。
   既存 `run_loop` / tick 関数群を束ねる）を常駐させる。flow / amigos の daemon 起動は廃止する。
3. **git の書き込み側同期はノードデーモンだけ**: 状態リポジトリ・flow バス・board・
   全ミラーの pull/push はノードデーモンが一手に担う。dashboard は git の書き込み
   （commit / push / 作業木を変える pull / heal）を一切しない。読み取り専用 git
   （受入 diff の表示等）は当面許し、C7 の views 化で漸減させる。
4. **dashboard から本体制御のプロセス起動を消す**: 指示・設定・可視化はすべて
   ファイル契約経由。`agent-project start` 等の本体 CLI 起動は廃止（OS サービスの管轄へ）。
   読み取り専用の AI 補助（charter 補完・doctor・taskAssist）の CLI スポーンは
   本体の状態を変えないため存置する。

```
┌─ PC-A ─────────────────────────────────────┐   ┌─ PC-B ──────────┐
│ Windows                                     │   │ （同型）         │
│  agent-dashboard（Electron）                │   │                  │
│   └ \\wsl.localhost\... のクローンへ        │   │                  │
│      ファイル読み書き（+読み取り専用 git）  │   │                  │
│ WSL                                         │   │                  │
│  agent-node（常駐・PC で唯一）              │◀──┼── 共有 git       │
│   ├ ノード層（親プロセス）                  │   │  （状態repo／    │
│   │  ├ agent-sync スケジューラ（全ミラー）  │   │    board／       │
│   │  ├ board tick（node 名義で入札）        │   │    workspace）   │
│   │  ├ amigos tick（claim・心拍・投入のみ） │   └──────────────────┘
│   │  ├ nodes/<pc>.json・status.json（心拍） │
│   │  └ 子プロセスの監視・再起動             │
│   ├ ワーカー（amigos 手番・依頼された run） │
│   └ プロジェクト層（子プロセス × 登録数）   │
│      ├ projX: ingest→act→flow tick→reap     │
│      └ projY: 〃                            │
└─────────────────────────────────────────────┘
```

## 3. コンポーネント再定義

### 3.1 agent-sync = 共通転送層（新規・P0）

GitBus の実証済みの護りを唯一の実装として切り出す。取り込む護りの一覧
（現 `gitbus.py` の該当箇所）:

- clone（sparse cone のパラメタ化 / `blob:none` フィルタ + フォールバック / 空リポジトリ
  フォールバック / 初回 clone の指数バックオフ）
- stale lock 掃除（閾値はリポジトリ種別パラメタ — 現状 30s と 300s が混在しており統一判断が要る）・
  ロックエラー検知 + リトライ・中断 rebase の abort
- **電源断オブジェクト破損への多層防御**: `core.fsync=all` の durable-write 設定・
  `fsck --connectivity-only` による再利用時プローブ・破損検知 → バスファイル退避
  （salvage）→ 再クローン → 復元
- `pull --rebase` → 再 push の指数バックオフ（force push 禁止）・管理クローンガード
  （ユーザの full checkout を sparse 化しない）
- **間隔律速**: 呼び出し側に散っている fetch/pull の間隔クロックを転送層へ移す。
  「**ネットワーク失敗時は間隔クロックを進めない**」という現 StateGit の不変条件
  （失敗で刻むと remote コマンド取り込みが遅延・欠落する）を仕様として明記して引き継ぐ。
- ミラーのレジストリ: 「この PC が必要とするリモート」（状態repo・板・バス・workspace
  ミラー）を宣言的に持つ。ただし**同一リモートのクローン共有（PC 内 1 クローン化）は
  P2 で解禁**する。P0 時点では常駐が複数残っており、共有クローンはプロセス間排他
  （flock）という新しい故障面を持ち込む。ノードデーモンが唯一の git 書き手になった後
  なら、プロセス内ロックだけで共有が成立する。

agent-project / agent-flow / agent-amigos の転送コード（StateGit・GitBus 転送部・
BoardRepo・BoardMirror）はこれへ置換する。**このフェーズはトポロジ変更と独立に、
ツール単位で漸進的に安全に進められる**（結果が同じで実装だけ変わる。クローン配置・
間隔・閾値は当面現状値を維持）。留意点:

- GitBus は `Bus` のサブクラスとして残し、転送メソッドだけ agent-sync 委譲にする。
  flow 固有なのは sparse 既定（`runs`/`inbox`）と `remove_run` のみで、パラメタ化で足りる。
- StateGit の direct モード（作業木に触れない CAS export・manifest 3-way・パス所有権に
  よる決定的コンフリクト裁定・journal の union merge）は**転送ではなくポリシー**であり、
  agent-sync の上に残す。切り出すのは下回りの git 実行・回復・リトライ層。
- BoardRepo / BoardMirror のクローンパスは node_id 由来（`~/.agents/amigos-board/
  <sha1(remote)>/<node_id>` 等）。node_id 切替（C9）でディスク上のクローンも移動する
  ことを移行手順に含める。

### 3.2 agent-node = ノードデーモン（常駐・PC で唯一）

**新規のスーパーバイザ**（現存する serve --all は無い。最も近い既存物は agent-project の
`ensure_flow_daemon` + `reap_orphan_flow` と flow daemon 自身の子監視で、その一般化）。
**ノード層（親）**と**プロジェクト層（子プロセス）**に分ける:

- **ノード層（親プロセス）** — 「PC」という概念に属する仕事だけを持つ:
  - agent-sync のスケジューラ（全ミラーの pull/push を周期表で駆動）
  - **board 請負 tick**: node 名義（`node_id = <pc>`）で入札。`nodes/<pc>.json` の能力宣言
    （board 契約に定義済み・**現状どのツールも未実装**）をここで初めて実装する。落札した
    公示は workspace.url → プロジェクトの対応表で該当する子へ割り振る（子が
    `agent-flow run` を単発起動）。二重入札は構造的に起きない
  - **amigos 参加 tick**: node 名義でロール claim・heartbeat・away。**手番実行は tick 内で
    走らせない**（下記の実行規約）
  - `nodes/<pc>.json`（能力宣言）と `engine/status.json`（心拍・同期健康）の書き出し
  - 子プロセスの起動・死活監視・再起動（プロジェクトの追加・削除は instances
    レジストリの変化で追従）
  - **バス単位 daemon ロックの保持**: 現行ロックはバス単位の flock singleton
    （`daemon_lock_key`）。ノードデーモンは自分が面倒を見る全バスのロックを N 個保持する。
    これにより移行期に旧 daemon が誤って起動しても同一バスを二重に所有できない
    （ロックドメインを PC 単位に作り直すのは全ツール切替完了後の P4）
- **プロジェクト層（子プロセス・登録プロジェクトごと）** — 現行 `run_loop` から
  git 同期呼び出しを抜いたもの:
  ```
  ingest（commands/ inbox/ needs/）※ controller lease 保持時のみ（§6）
  → plan / act（claims で分担。act は常に agent-flow run の単発 detach 起動）
  → flow tick（自PCの run の監督: orphan 検知→auto-heal・終端の reap・cancel 伝搬）
  → board 依頼 tick（post / result 回収）
  → status 書き出し
  ```
  1 プロジェクトの暴走・クラッシュは子プロセスに閉じ、親が再起動する。

tick は単一ループ直列ではなく**周期表で駆動**する（C3 対策の正規化）:

| tick | 既定周期 | ブロック性 | 備考 |
|---|---|---|---|
| amigos 参加（claim・心拍・away） | 5s | 短命必須 | 手番実行はワーカーへ投入するだけ |
| board（入札・依頼） | 30–60s | 短命必須 | 現 GitBus ポーリングと同等 |
| 状態 repo / ミラー sync | state_git_interval（現行） | git 待ちあり | dashboard の投函もここで必ず載る |
| プロジェクトループ | pace（現行設定） | 長命（子プロセス側） | act の律速は従来どおり |
| amigos 手番・落札 run 実行 | （tick でなくワーカー） | 長命 | ノード全体セマフォで律速（C5） |

**スケジューラの実行規約（C3 の設計必須条件を具体化）**:

- 各 tick はワーカースレッドで実行し、種類ごとに single-flight（前回が走行中なら skip）。
- git を伴う tick はステップ毎タイムアウトを持ち、git はサブプロセスなので kill で確実に
  打ち切れる。例外は tick 内に隔離しループを殺さない（amigos が board 参加を try/except で
  包む現行流儀の一般化）。
- **周期を超えうる仕事（amigos の手番・act・落札 run）を tick 内で実行してはならない**。
  tick は「請求・心拍・キュー投入」だけを行い、実行はワーカー（サブプロセス）へ移す。
  現 serve は手番実行を cycle() 内で直列に走らせており、これを 5s tick のまま親へ持ち込むと
  ノード層全体が分単位で止まる。
- 親の再起動時は子プロセス・実行中 run を巻き込まない。**プロセス再 attach は行わず**、
  flow が実証済みの lease ベース回収（`_adopt_orphan_runs` — run-id で resume、lease 内は
  触らない）に委ねる。「再起動直後に生きている run を殺さない」は lease 猶予がそのまま
  保証する（C4 の新規状態機械を最小化）。
- 現行実装の**自殺型停止経路は作り直す**: availability モニタの `os.kill(自PID, SIGTERM)`、
  flow daemon の self-update `execv`、モジュールグローバルの `_DRAIN_REQUESTED`
  （1 プロセス 1 プロジェクト前提の単一 Event）は、いずれも親子分離後は
  「親 → 子への指示」に置き換える。

- **`location` 概念は廃止**。`daemon` / `remote` は消え、実行は常に「claim を取った PC が
  `agent-flow run` を単発起動」。PC 間の分担は (a) coordination（git-cas + claims +
  availability）と (b) board、の 2 軸だけになる。`act_async` も同時に消える
  （daemon/remote 専用のフラグで board には元々効かない）。
- flow daemon が持っていた auto-heal・max_runs 律速・inbox 受理は、自 PC の run に
  限って子ループが行う（primitives は既に独立関数 + 単体テスト済みなので移植は薄い）。
- `manage_flow_daemon` は廃止（管理対象の daemon 自体が無くなる）。
- 実装面の注意: agent-project / agent-flow / agent-amigos はいずれも exec 合成の
  単一名前空間（モジュール境界なし）であり、ノード層 / プロジェクト層の分割は
  「import の並べ替え」ではなくグローバル共有の解きほぐしになる。P1 の tick 抽出時に
  少なくとも「tick 関数は引数以外の状態に触らない」を機械的に検査できる形へ寄せる。

### 3.3 agent-flow = run ライフサイクルの実行体（常駐なし）

- **残す**: `run` / `resume` / `cancel` / `result` の CLI、バス上の run レイアウト、
  タスク claim プロトコル、run 内のノード分散（GitBus 契約）、gitlab executor。
- **廃止**: `daemon` サブコマンド。ロジックは**関数（tick）として残し**、agent-node が呼ぶ。
  注意: 現在は**サブコマンド無しの `agent-flow` が daemon を起動する既定**になっている
  （amigos の既定 serve・project の既定 run --watch も同様）。P4 で裸起動は案内表示に
  変える（黙って別の意味に変えない）。
- 「他 PC の daemon に submit」だけが消える（等価機能は board post で提供）。ただし
  **remote submit と一緒に消えるのは result 読み戻し IPC も**である
  （`read_reject_guidance` / `read_brief_discoveries` / `read_result_notes` — gitlab
  executor の reject→retry フィードバックが依存）。板の `result.json` は現状 status 程度
  しか運ばないため、**`result_notes` / `discoveries` / `reject_guidance` を additive に
  載せる**（board 契約は `additionalProperties: true` の前方互換規約なので契約変更に
  当たらない）。これが揃うまで remote submit の deprecation は完了扱いにしない。

### 3.4 agent-amigos = ミッションライフサイクルの実行体（常駐なし）

- **残す**: ミッション/ロール/メッセージのバス契約、claim・away プロトコル、納品棚。
- **廃止**: `serve` 常駐。参加ロジックはほぼ移植不要 — `NodeDaemon.cycle()` が既に
  無状態の単発 tick で（runner は「ターン間で状態を持たない」設計・テスト 40 箇所が
  cycle() 直接駆動・常駐ループ run() を使うテストは 0）、agent-node のノード層が 5s 周期で
  呼ぶ。ただし §3.2 の実行規約どおり、**手番実行（turn_once）は cycle から切り離して
  ワーカーへ**移す（現 cycle は手番をインライン実行しており、そのままではノード層を塞ぐ）。
- serve にあって cycle に無い 2 つの常駐挙動の行き先を明示する:
  **offboard（SIGTERM → away 宣言 + 最終 push）**はノード層の graceful 停止（§6）へ、
  **適応バックオフ（idle 時 interval×8 まで漸増）**は周期表の周期そのものへ吸収する。
- hub（long-poll）は転送加速のオプション契約として残すが v1 不採用（常駐が増えるため）。
  現状もクライアントは wait= を渡しておらず実質インターバルポーリングで、既定経路に
  依存は無い。

### 3.5 agent-dashboard = ファイル操作フロントエンド（Windows）

- **廃止**:
  - `base/main/git.js` の**書き込み経路**: pull / commitPush（28 呼び出し箇所の
    `gitPushAfterWrite` / `gitPushBusOp` ごと）/ heal 実行 / `gitAutoPush` 設定。
    同ファイルの**読み取り専用機能は存続**させる — `diffRange`（受入 diff 画面）と
    `diagnostics` / `health` の読み取り表示は git.js 全削除では巻き添えになるため、
    読み取り専用モジュールへ分離して残す（将来はエンジンの views 化 — C7 — で置換可）。
  - `dashboard:start` の CLI 実行（唯一残っていた本体 CLI 経路。他の approve/replan 等は
    既に commands/ ドロップへ移行済み）。
  - flow daemon ロックのプローブと **`flow.js` 内のロック鍵導出の手写し複製**
    （`daemonLockPath` / `daemonStatus` / `stopDaemon`）・`flowLockDir` 設定 UI。
    エンジン稼働表示は `engine/status.json` に一本化。
- **存置**（改訂 2 で廃止対象から除外）:
  - **AI 補助の CLI スポーン**（`agent:charter` / `agent:doctor` / `agent:taskAssist` /
    `agent:openChat`）。これらは本体の状態を変えない読み取り専用ヘルパで、現行の
    同期スピナー UX（LLM 1 呼び分の待ち）が成立している。ファイル往復化すると
    ポーリング遅延が上乗せされ、エンジン停止中は機能しなくなる — 得るものがない。
    これに伴い改訂 1 の `requests/` 往復契約は **v1 から外す**（C6 はほぼ解消。
    将来エンジン側で実行したくなったときのオプションとして §4 に予約だけ残す）。
  - gitlab-review-viewer 起動・cowork / kiro-loop / participation の tmux スポーン
    （本体制御ではない別機能）。
- **読み**: ノードデーモンが鮮度を保証するローカルクローンのファイルだけを読む
  （既存の refreshSec ポーリング。inotify に依存しない）。
- **書き**: 既存契約ファイルのみ — commands/ inbox/ needs/ reviews/ assignments/。
  受理確認は既存の `commands/processed/` レシートを使う。
- 同期の健康・エンジン稼働は `engine/status.json` の表示に一本化（🩺 の修復実行は
  エンジン側の責務になり、dashboard は `commands/heal` を投函するだけ）。
- 設定は「プロジェクトルート（UNC パス）の列挙 + 表示設定」だけに縮退。エンジン側の
  設定はノード設定（`agent-node.yaml`: node_id・tags・repos・board・amigos 参加）と
  プロジェクト設定（`agent-project.yaml`: 従来からノード的キーを除いたもの）の 2 ファイルへ
  整理する。既存の profile 機構（`PROFILE_LOCAL_KEYS` — root / node / availability を
  ローカル分離済み）が agent-node.yaml の種になる。flow / amigos の yaml から
  daemon 関連キーが消える。
- **任意の加速装置**(オプトイン・契約は変えない): 同一 PC 内に限り、dashboard →
  ノードデーモンの読み取り/対話専用 localhost TCP を許す（WSL2 は Windows から
  localhost で到達可能）。真実は常にファイルで、socket 不通でもファイル経由で成立する
  — board 設計の「webhook は加速装置」と同じ位置づけ。

## 4. ファイル契約の追加・変更

| パス | 方向 | 内容 |
|---|---|---|
| `.agents/engine/status.json` | ノードデーモン → dashboard | **新規契約**（現状は `<root>/status.json` と `<bus>/status.json` の 2 系統。エンジン側の書き出し実装が要る）。心拍（node・pid・ts）・tick 周期表の実績・同期健康（ahead/behind/エラー）・プロジェクト別の子プロセス状態・実行中 run 一覧。**書き手はノードデーモンのみ** |
| `commands/heal` | dashboard → エンジン | 🩺 の置き換え（stale lock 掃除・rebase 巻き戻し・強制同期を agent-sync が実行）。受理は `commands/processed/` レシート |
| `nodes/<pc>.json`（board） | ノードデーモン → 板 | 既存 board 契約の未実装部分をノード層が実装（能力宣言・観測用。現状はどのツールも読み書きしていない） |
| 板の `result.json` / `results/<who>.json` | 請負ノード → 板 | **additive 拡張**: `result_notes` / `discoveries` / `reject_guidance` を載せ、remote submit の result 読み戻しと等価にする（§3.3） |
| `commands/{pause,resume,stop}` | 既存 | 不変（stop したエンジンの再開だけは OS サービス管轄 — §5） |
| `requests/<id>.json` 往復 | （予約のみ） | 改訂 1 で導入予定だった対話往復は v1 から除外（AI 補助スポーン存置のため不要 — §3.5）。将来エンジン実行へ寄せる場合のオプションとして名前だけ予約 |

既存の commands / inbox / needs / reviews / assignments / board / delegation 契約は
**一切変えない**。書き込み所有権の分割（dashboard は投函ファイルのみ・エンジンは
状態ファイルのみ）も不変で、Windows↔WSL 間で flock が使えない前提（9p）と整合する
（排他はすべて「所有権分割 + tmp→rename」で成立させる。現行契約と同じ）。

board の入札者は「ノード」になるため、`node_id` は PC 名そのもの（`pc-a`）で足りる。
現行実装の「落札→自分の flow inbox へ投函」は「落札→担当プロジェクトの子ループへ
割り振り→単発 run 起動」に変わるが、板の上のファイル（bids / status / results / result）
の形は不変。

## 5. Windows / WSL 配置

- **クローンは WSL ext4 に置く**。dashboard は `\\wsl.localhost\<distro>\...` 経由で
  読み書きする（9p の rename は原子的・flock は不可 → §4 の規約でカバー）。
  `/mnt/c` 側にクローンを置く構成は廃止する（inotify・perms・速度の三重苦）。
- **常駐化は PC あたり systemd user unit 1 個**（`agent-node.service`。
  `Restart=always` + `boot.systemd=true`）。プロジェクトの増減で unit は増えない
  （instances レジストリで子が追従する）。`Restart=always` は C1（エンジン停止中は
  投函が届かない）の実質的な緩和でもある — 停止窓を「クラッシュ〜再起動の数秒」まで縮める。
- **WSL VM は全セッション終了で自動停止する**ため、Windows 側に keep-alive が必須
  （タスクスケジューラでログオン時に `wsl.exe -d <distro> --exec sleep infinity` 等を常駐、
  または `.wslconfig` の `vmIdleTimeout` 延長）。keep-alive も PC あたり 1 個で固定。
  セットアップは install.py に組み込み、欠落は status 表示と doctor で検知する。
- dashboard の「起動」ボタンは廃止し、status.json が古いときに「このPCのエンジンが
  停止しています（起動コマンド: ...）」の案内表示に置き換える。**dashboard から
  死んだエンジンを起こす手段は無くなる**（`dashboard:start` が唯一のコールドスタート
  経路だった）。ファイル契約はエンジンが読まなければ届かない以上これは原理的で、
  再起動は OS サービス（systemd `Restart=always` + keep-alive）に一任する、が本設計の
  受け入れ事項（C1）。

## 6. マルチ PC・メンテナンス停止

- 分担は既存機構を**必須既定化**する: `coordination: git-cas`（CAS 遷移 + fencing）＋
  claims ＋ `availability`（daily_stop / drain）。全 PC が同型の常駐なので、どの PC が
  落ちても残りが ingest / act / reap / 入札を続ける。
- **制御面は controller lease で 1 ノードに絞る**（07-22 設計の踏襲を明記）: §3.2 の
  子ループ図で「全 PC が ingest」と書いたのは lease 調停込みの意味であり、charter
  plan/evaluate・commands/inbox 取り込み・triage・未割当タスクの配分は lease 保持
  ノードの子だけが行う。worker 側は claim/act/settle のみ。coordination はコード上は
  実装済みだが**完全オプトイン**（既定 off・多数の分岐が短絡）なので、「必須既定化」は
  既定値の反転 + 空 node_id の禁止 + remote 必須（fail-close は現行仕様どおり）を含む。
- **graceful 停止**（systemd stop / drain 窓）はノード層が一括で行う: 全子の claims 解放 →
  controller lease の明示解放 → amigos away 宣言（現 offboard の移設）→ board の実行中
  status へ `away` 書き込み（P2 契約の実装）→ 未 push 分の最終 sync_push。**PC 単位の
  常駐なので、メンテ時のフックが 1 箇所で済む**（A 案ではプロジェクト数ぶんの停止順序を
  気にする必要があった）。
- **突然死**: 他 PC がタスク claim の lease 失効で回収。board は再入札（二重実行は
  結果整合で吸収 — board 設計 §7/§8 のまま）。自 PC の中断 run は再起動後の
  flow tick が orphan 検知 → resume-run（lease ベース回収なので、再起動直後に生きている
  run を誤って回収しない — §3.2 実行規約）。
- **全 PC 停止**: dashboard の投函はローカルクローンに滞留し、復帰したノードデーモンが
  sync_push で押し出す（サイレント消失なし。現行と同じ性質）。

## 7. 廃止一覧（簡素化の実収支）

| 廃止 | 行き先 |
|---|---|
| 転送実装 5 種（GitBus 転送部・StateGit 下回り・BoardRepo・BoardMirror・dashboard git.js 書き込み経路） | **agent-sync 1 実装**（git.js のみ置換でなく廃止） |
| git-file-sync の状態 repo / 板への使用 | **禁止**（doctor で検知）。無関係な汎用フォルダ同期用途は本設計のスコープ外として存続 |
| agent-flow `daemon`（inbox 監視・auto-heal・board 巡回・lease 更新の常駐）と裸起動既定 | agent-node の flow tick / board tick |
| agent-flow `submit` / remote daemon への git-bus 委譲（`location: daemon/remote`）と result 読み戻し IPC | claim 分担（同一状態 repo）または board post + 板 result の additive ペイロード（§3.3） |
| `act_async`・`offloaded` 状態の daemon/remote 分岐 | board 経路のみに縮退 |
| agent-amigos `serve` / hub 常駐 | agent-node のノード層 tick（5s 周期）+ ワーカー実行 |
| dashboard `git.js` 書き込み経路・`gitAutoPush`・自動 pull・🩺 実行 | agent-sync + `engine/status.json` + `commands/heal` |
| dashboard の本体 CLI 実行（`dashboard:start`） | OS サービス + status 案内表示（AI 補助スポーンは存置 — §3.5） |
| `manage_flow_daemon`・flow/amigos の board 設定キー・`flowLockDir` プローブ + `flow.js` のロック鍵手写し複製 | `agent-node.yaml` / `agent-project.yaml` の 2 ファイルに整理 |
| board クローン 4 種（project/flow/amigos/dashboard 各自） | agent-sync 管理の PC 内 1 クローン（**P2 で**。P0 では実装統一のみ — §3.1） |
| systemd unit × プロジェクト数（A 案） | `agent-node.service` 1 個（`Restart=always`） |

## 8. 移行フェーズ

| フェーズ | 内容 |
|---|---|
| **P0** | **agent-sync 抽出**（実装統一のみ・クローン配置と間隔は現状維持）。GitBus の護りを共通ライブラリ化し、BoardRepo → BoardMirror → StateGit 下回りの順に置換（ツール単位・挙動不変・既存テストで担保。stale 閾値等の差異は per-repo パラメタとして温存し、統一は別判断）。dashboard git.js はこの時点では触らない |
| P1 | flow daemon / amigos serve のループ本体を tick 関数群へ抽出（daemon / serve コマンドは薄い互換ラッパとして残す）。flow は primitives が既に独立関数なのでループ骨格の分解が主作業、amigos は cycle() から手番実行を切り出してワーカー投入形へ（§3.4）。board.py は既に tick 形なので呼び出し規約だけ揃える。板 result の additive ペイロード（§3.3）もここで実装し、remote submit の等価性を先に確保する |
| P2 | agent-node 実装（スーパーバイザ + 周期表スケジューラ + ノード層 tick + バス単位ロック N 個保持 + `nodes/<pc>.json` 実装）・`engine/status.json`・クローン共有レジストリの解禁。`location` は `local` 固定化（他は deprecation 警告）。node_id の既定を PC 名へ（切替手順は C9 — クローンパス移動と 2 バスの status 名義を含む静止点切替） |
| P3 | dashboard から git.js 書き込み経路・`dashboard:start`・flow ロックプローブを削除し、status 表示へ置換。diffRange / diagnostics は読み取り専用モジュールとして分離存続 |
| P4 | daemon/serve コマンド・裸起動既定・旧設定キーの削除。ロックドメインの整理。テスト（flow 528 / amigos 140 / project 801）の daemon 前提部分を tick 前提へ書き換え — 実際に常駐プロセスを要するテストは少ない（flow の実デーモン E2E 6 件・amigos 0 件が実測。project の daemon 関連 60〜100 件が最大の書き換え面） |
| P5 | **実機 canary**: 2 台・停止時刻をずらした 1 週間運用で、ノード引継ぎ・全台停止復帰・drain・突然死を各 1 回以上通す（07-22 設計 Phase 6 の踏襲。自動テストは WSL 終了・電源断・9p の差を代替しない） |

移行期間の混在（旧 daemon と新 tick が同じバスを触る）はテスト困難なため、**PC 単位で
一斉切替**する（データ契約が不変なので、切替済み PC と未切替 PC の共存は問題ない —
板と同じ「契約は不変・プロセス配置だけ変える」性質）。バス単位ロックの維持（§3.2）が、
切替漏れの旧 daemon と新ノードの同一バス二重所有を機械的に防ぐ。

## 9. 懸念（重要度順）

- **C1: ローカルエンジン停止中は「指示が他 PC へ届かない」+ コールドスタート不能**。
  現行は dashboard 自身が commitPush で投函を共有リモートへ届けられ（エンジンが他 PC に
  しか居なくても機能した）、`dashboard:start` で死んだエンジンを起こせた。新設計では
  同一 PC のノードデーモンが唯一の push 役で、dashboard は起動手段も持たない。エンジンが
  落ちていると投函はローカル滞留する（消えはしない）。緩和: systemd `Restart=always` +
  keep-alive で停止窓自体を縮める・status.json による明示表示・他 PC の dashboard から
  操作する運用。**これは「dashboard に git をさせない」と決めた瞬間に、どの案でも払う
  対価** — 受け入れの判断が要る。
- **C2: WSL VM の自動停止**。「daemon 必須」は Windows 構成では「WSL keep-alive 必須」を
  意味する。ここが死ぬと C1 が全機能で同時に起きる。keep-alive は PC あたり 1 個に
  固定されたが、必須要件であることは変わらない — install.py への組込みと status / doctor
  での検知を必須にする。
- **C3: 1 プロセスへの責務集中**。緩和は §3.2 の実行規約に具体化した（tick の
  single-flight + タイムアウト + 例外隔離、長時間作業のワーカー分離、自殺型停止経路の
  作り直し）。プロジェクト層は子プロセスで隔離済み。親の再起動が子と run を巻き込まない
  保証は lease 回収の猶予に還元した（→ C4）。
- **C4: 長時間 run の監督の再実装リスク**。auto-heal・orphan 回収・max_runs は flow daemon で
  実証済みのロジックで、primitives は独立関数 + 単体テスト済み — 移植面は小さい。
  「ノードデーモン自身の再起動との合成」はプロセス再 attach を諦めて lease ベース回収に
  一本化することで新規状態機械を最小化する（§3.2）。残るリスクは cancel 受理 →
  orphan 回収の順序・heartbeat 周期と lease 窓の整合など、現ループが暗黙に持つ順序制約の
  移し漏れ。P1 で daemon テストをそのまま tick テストへ移せる形で抽出するのが安全。
- **C5: マルチプロジェクトの資源競合**。1 PC の全プロジェクトが同じノードデーモン配下に
  乗るため、あるプロジェクトの重い act がエージェント CLI・CPU を占有しうる。
  緩和: ノード層に PC 全体の `max_concurrent`（board 契約の宣言と同じ語彙）を置き、
  子ループの act 起動・amigos 手番・落札 run をノード層のセマフォで律速する。act は
  detach 起動のため、セマフォの計数はプロセス手持ちでなく status/run ファイルから導出する。
- **C6: 対話機能** — 改訂 2 でほぼ解消。charter 補完・doctor は dashboard の読み取り専用
  スポーンとして存置し（§3.5）、ファイル往復化しない。残るのは「エンジン設定と dashboard
  設定で AI CLI の解決が二重にある」という既存の小さな重複のみ。
- **C7: 9p 越しの読み性能**。bus の run ディレクトリや flow-archive は数百ファイルになり、
  UNC 越しの走査は遅い。緩和: エンジンが正規化ビュー（views/*.json — flow-archive 方式の
  一般化）を少数ファイルに materialize し、dashboard はそれだけ読む。ビュー生成という
  新責務がエンジンに増える点は認識しておく。views は将来、dashboard に残した読み取り専用
  git（diffRange）の置換先でもある。
- **C8: remote submit 廃止の等価性**。submit は board post（workload=flow）で等価にできるが、
  (a) **result 読み戻しの等価性は板 result の additive ペイロードが前提**（§3.3。これ無しで
  deprecation を完了させない）、(b) board 必須になる＝最小構成でも board リポジトリが
  1 つ要る（1 PC 構成ではローカル dir 板で可）。セットアップ手順は 1 段増える。
- **C9: node_id の移行**。現状はツール毎に不揃い（flow: `host-pid` 既定を設定で固定可・
  amigos: `hostname-乱数` を node.json に永続化・project: 入札せず）で、「PC 名」への統一は
  規約変更というより初の統一。切替は自分名義ファイルを 2 つのバスで見失う
  （板の `status/<who>.json` と amigos の `status/<node>--<role>.json` — 後者は roster・
  claim・events の名義でもある）ため、**実行中の委譲・ミッションが無い静止点で PC 単位に
  一斉切替**し、node_id 由来のクローンパス（`~/.agents/amigos-board/<sha>/<node_id>` 等）の
  移動も手順に含める。切替前チェックを doctor に実装する。
- **C10: 移行コスト**。3 ツールのテストの daemon 前提部分・既存運用中の設定・ドキュメントの
  全面改訂。ただし実測では tick 化済みの下地が厚く（§0）、最大の書き換え面は
  agent-project の単一 12k 行テストファイル。P0（転送統一）が先に単体で価値を出すため、
  **P0 だけで止めても現状構成の堅牢化として成立する**という撤退線を持てるのが本改訂の利点。
- **C11: exec 合成モジュールの分割リスク**（新規）。3 ツールとも `__init__.py` が全 .py を
  exec で単一名前空間に合成しており、ノード層 / プロジェクト層 / agent-sync の境界は
  既存コードの暗黙のグローバル共有を断ち切る作業を伴う。agent-sync（P0）は新規
  ライブラリなので通常の import 境界で書き、既存側からの参照だけを段階的に差し替える。

## 10. 初版（A 案: PC × プロジェクト常駐）との比較

| 観点 | A 案（PC × プロジェクト常駐・単一ループ） | ★B 案（ノードデーモン + 周期表） |
|---|---|---|
| systemd / keep-alive | プロジェクト数ぶん | PC あたり 1 個 |
| amigos 応答性 | 単一 pace に律速（緩和策が場当たり） | tick 別周期で構造的に解決 |
| node と project のズレ | `node_id = <pc>-<project>` 規約 + 二重入札 lint が必要 | 構造的に消滅（入札者 = PC） |
| 障害隔離 | プロセス = プロジェクトで自然に隔離 | スーパーバイザ + 子プロセスで同等 |
| メンテ停止フック | プロジェクト数ぶん | ノード層で一括 |
| 転送の重複 | 触れず（別課題のまま） | P0 で 1 実装に統一 |
| 実装の複雑さ | ループ 1 本で単純 | スケジューラ + 監視の分だけ増える |

B 案の追加コストは「スーパーバイザと周期表」だが、これは flow daemon が既に持っていた
poll ループ + 子（orchestrator）監視の一般化であり、新奇な機構ではない。

## 11. 非目標

- バス・板・封筒などの**データ契約の変更はしない**（プロセス配置の再編のみ。板 result への
  additive ペイロード追加は前方互換規約の範囲内 — §3.3）。
- インターネット越しの分散・認可機構の追加はしない（従来どおりオンプレ + git 認証）。
- dashboard の機能削減はしない（起動ボタンと 🩺 の実行主体が変わるだけで、
  できることは維持する。AI 補助・受入 diff・外部ビューア起動は現行のまま）。
- amigos hub・localhost socket は「加速装置」の位置づけを超えない（真実は常にファイル）。
- git-file-sync の汎用フォルダ同期用途には手を入れない（状態 repo・板への使用禁止のみ — §7）。
- stale lock 閾値などツール間で異なる転送パラメタの**値の統一はしない**（agent-sync の
  パラメタとして温存し、変えるなら別判断にする — P0 を「挙動不変」に保つため）。
