# 常駐一本化設計案 — agent-project をプロジェクトコントローラーに、エンジンはライフサイクル実行体に

- 日付: 2026-07-24
- 状態: **提案**（未実装。懸念出しまで）
- 動機: 構成・設定バリエーションの爆発がバグの温床になっている（§1）。エンジン常駐を
  必須と割り切り、**常駐プロセスを「PC × プロジェクト = agent-project 1 本」に集約**する。
- 関連: [`2026-07-23-delegation-board-distributed-bidding-design.md`](./2026-07-23-delegation-board-distributed-bidding-design.md)（board 契約は不変）、
  [`schemas/board.schema.json`](../../schemas/board.schema.json)、
  [`schemas/delegation.schema.json`](../../schemas/delegation.schema.json)

## 1. 動機 — 何がバグの温床か

現状、同じ結果を得る経路が複数あり、その直積が構成空間になっている:

| 軸 | 現在のバリエーション |
|---|---|
| act の実行モード | `location: auto / local / daemon / remote / board` × `act_async` |
| 状態の共有 | state_git 管理クローン / direct モード / 非 git（同期なし） |
| 状態を push する主体 | agent-project の state_git / **dashboard の commitPush（`gitAutoPush`）** / git-file-sync — 最大 3 者が同一リポジトリの書き手 |
| flow の実行形態 | 単発 run / ローカル daemon / GitBus 分散 daemon / hub |
| amigos の実行形態 | serve 常駐 / hub long-poll |
| board のクローン | agent-project・agent-flow・agent-amigos・dashboard が**各自別クローン** |
| dashboard → 本体の経路 | ファイルドロップ / CLI 直接実行（start・agent:*）/ git push |

直近で直した 4 件のバグ（cancelled の綴り不一致・板 result の cancelled 成功扱い・
dashboard 投函が push されない・板クローンの rebase 残骸）は、いずれも
**「同じ責務を複数の実装が別々に持つ」「書き手が複数いる」**ことに起因する。
dashboard の `git.js` に堆積した護り（隔離 worktree・autostash 事故対応・notRepo の
サイレントスキップ通知）は、この複雑さの症状そのものである。

## 2. 新原則（3 行）

1. **常駐は 1 種類**: プロジェクトを使う各 PC で `agent-project`（watch 常駐）を必ず動かす。
   flow / amigos の daemon 起動は廃止する。
2. **git を触るのは agent-project だけ**: 状態リポジトリ・flow バス・board・全ミラーの
   pull/push は各 PC のコントローラーが一手に担う。dashboard は git を一切使わない。
3. **dashboard はローカルクローンへのファイル読み書きだけ**: 指示・設定・可視化は
   すべてファイル契約経由。プロセス起動（CLI 実行）もしない。

```
┌─ PC-A ──────────────────────────────┐   ┌─ PC-B ────────────────┐
│ Windows                              │   │ （同型）               │
│  agent-dashboard（Electron）         │   │                        │
│   └ \\wsl.localhost\... のクローンへ │   │                        │
│      ファイル読み書きのみ            │   │                        │
│ WSL                                  │   │                        │
│  agent-project watch（常駐・唯一）   │◀──┼── 共有 git（状態repo／ │
│   ├ git 同期（状態/バス/board）      │   │    board／workspace）  │
│   ├ ingest（commands/inbox/needs/    │   │                        │
│   │         requests）               │   └────────────────────────┘
│   ├ act: agent-flow run（単発起動）  │
│   ├ flow tick（heal/reap/inbox）     │
│   ├ amigos tick（claim/turn/away）   │
│   ├ board tick（依頼＋請負）         │
│   └ engine/status.json（心拍）       │
└──────────────────────────────────────┘
```

## 3. コンポーネント再定義

### 3.1 agent-project = プロジェクトコントローラー（常駐・唯一）

watch ループの 1 巡を次のステップ列に固定する（各ステップは例外隔離 —
現行の board 巡回と同じ「失敗してもループは止めない」規約）:

```
sync_pull（状態repo）
→ ingest（commands/ inbox/ needs/ requests/）
→ plan / act（claims で分担。act は常に agent-flow run の単発 detach 起動）
→ flow tick（自PCの run の監督: orphan 検知→auto-heal・終端の reap・cancel 伝搬）
→ amigos tick（ロール claim・自分の手番の実行・heartbeat・away）
→ board tick（依頼側: post/reap ＋ 請負側: 入札→単発 run 起動→result 書き戻し）
→ engine/status.json 書き出し（心拍・同期健康・実行中 run）
→ sync_push（状態repo。dashboard が置いたファイルもここで必ず載る）
```

- **`location` 概念を廃止**する。`daemon` / `remote` は消え、実行は常に
  「claim を取った PC が `agent-flow run` を単発起動」。PC 間の分担は
  (a) 既存 coordination（git-cas + claims + availability）と (b) board、の 2 軸だけになる。
- **監督責任の移管**: flow daemon が持っていた auto-heal（orchestrator 死亡検知→再開）・
  max_runs 律速・inbox 受理は、自 PC の run に限って agent-project が行う。
- board の請負参加（現 `agent_flow/board.py` / `agent_amigos/board.py` の poll_board）は
  **agent-project の board tick に移す**。落札後は inbox 投函ではなく直接
  `agent-flow run` を起動する（inbox→daemon の段が消える）。
- `manage_flow_daemon` は廃止（管理対象の daemon 自体が無くなる）。

### 3.2 agent-flow = run ライフサイクルの実行体（常駐なし）

- **残す**: `run` / `resume` / `cancel` / `result` の CLI、バス上の run レイアウト、
  タスク claim プロトコル、GitBus（run 内の分散実行と観測用ミラー）、gitlab executor。
- **廃止**: `daemon` サブコマンド（inbox 監視・orchestrator 起動・auto-heal・board 巡回の常駐）。
  これらのロジックは**関数（tick）として残し**、agent-project が毎巡呼ぶ。
- run 内のノード分散（複数 PC が同一 run のタスクを claim）は GitBus 上の契約なので不変。
  「他 PC の daemon に submit」だけが消える（等価機能は board post で提供）。

### 3.3 agent-amigos = ミッションライフサイクルの実行体（常駐なし）

- **残す**: ミッション/ロール/メッセージのバス契約、claim・away プロトコル、納品棚。
- **廃止**: `serve` 常駐。参加ロジックを `tick(participate)` 関数に切り出し、
  agent-project が毎巡呼ぶ（1 巡 = 従来 daemon の 1 poll と同じ処理）。
- hub（long-poll）は転送加速のオプションとして契約だけ残すが、v1 は不採用
  （常駐が増えるため。§6 懸念 C3）。

### 3.4 agent-dashboard = ファイル操作フロントエンド（Windows）

- **廃止**:
  - `base/main/git.js` 全体（pull / commitPush / heal / health / gitAutoPush 設定）
  - `dashboard:start` の CLI 実行・`agent:charter` / `agent:doctor` 等の CLI 直接起動
  - flow daemon ロックのプローブ（`flowLockDir`）・daemon 稼働判定
- **読み**: エンジンが鮮度を保証するローカルクローンのファイルだけを読む
  （既存の refreshSec ポーリング。inotify に依存しない）。
- **書き**: 既存契約ファイルのみ — commands/ inbox/ needs/ reviews/ assignments/
  に加え、CLI 直接実行の置き換えとして `requests/`（§4）。
- 同期の健康・エンジン稼働は `engine/status.json` の表示に一本化する
  （🩺 の修復実行はエンジン側 doctor の責務になり、dashboard は
  `commands/heal` を投函するだけ）。
- 設定は「プロジェクトルート（UNC パス）の列挙 + 表示設定」だけに縮退する。
  エンジン側の設定は全て `agent-project.yaml`（1 ファイル）へ集約: board の
  依頼/請負両面（`board:` `board_repos:` `board_tags:`）・amigos 参加
  （tags/repos/roles）・coordination・availability。flow / amigos の yaml から
  daemon 関連キーが消える。

## 4. ファイル契約の追加・変更

| パス | 方向 | 内容 |
|---|---|---|
| `.agents/engine/status.json` | エンジン → dashboard | 心拍（node・pid・ts）・ループ位相・同期健康（ahead/behind/エラー）・実行中 run 一覧。**書き手はエンジンのみ** |
| `requests/<id>.json` → `requests/<id>.response.json` | dashboard → エンジン → dashboard | 旧 `agent:charter` / `agent:doctor` / 診断の往復。同一 PC のエンジンが処理（requests はローカル専用・状態 repo に同期しない） |
| `commands/heal` | dashboard → エンジン | 🩺 の置き換え（stale lock 掃除・rebase 巻き戻し・強制同期をエンジンが実行） |
| `commands/{pause,resume,stop}` | 既存 | 不変（stop したエンジンの再開だけは OS サービス管轄 — §5） |

既存の commands / inbox / needs / reviews / assignments / board / delegation 契約は
**一切変えない**。書き込み所有権の分割（dashboard は投函ファイルのみ・エンジンは
状態ファイルのみ）も不変で、Windows↔WSL 間で flock が使えない前提（9p）と整合する
（排他はすべて「所有権分割 + tmp→rename」で成立させる。現行契約と同じ）。

## 5. Windows / WSL 配置

- **クローンは WSL ext4 に置く**。dashboard は `\\wsl.localhost\<distro>\...` 経由で
  読み書きする（9p の rename は原子的・flock は不可 → §4 の規約でカバー）。
  `/mnt/c` 側にクローンを置く構成は廃止する（inotify・perms・速度の三重苦）。
- **エンジンの常駐化**: WSL の systemd user service（`boot.systemd=true`）で
  `agent-project watch` をプロジェクトごとに登録する。
  **WSL VM は全セッション終了で自動停止する**ため、Windows 側に keep-alive が必須
  （タスクスケジューラでログオン時に `wsl.exe -d <distro> --exec sleep infinity` 等を
  常駐させる、または `.wslconfig` の `vmIdleTimeout` 延長）。→ 懸念 C2。
- dashboard の「起動」ボタンは廃止し、status.json が古いときに
  「このPCのエンジンが停止しています（起動コマンド: ...）」を表示する案内に置き換える。

## 6. マルチ PC・メンテナンス停止

- 分担は既存機構を**必須既定化**する: `coordination: git-cas`（CAS 遷移 + fencing）＋
  claims ＋ `availability`（daily_stop / drain）。全 PC が同型の常駐なので、
  どの PC が落ちても残りが ingest / act / reap を続ける。
- **graceful 停止**（systemd stop / availability の drain 窓）: claims 解放 →
  amigos away 宣言 → board の実行中 status へ `away` 書き込み（P2 契約の実装）→
  未 push 分の最終 sync_push。
- **突然死**: 他 PC がタスク claim の lease 失効で回収。board は再入札
  （二重実行は結果整合で吸収 — board 設計 §7/§8 のまま）。自 PC の中断 run は
  再起動後の flow tick が orphan 検知 → resume-run。
- **全 PC 停止**: dashboard の投函はローカルクローンに滞留し、どの PC でも復帰した
  エンジンが sync_push で押し出す（サイレント消失なし。現行と同じ性質）。

## 7. 廃止一覧（簡素化の実収支）

| 廃止 | 行き先 |
|---|---|
| agent-flow `daemon`（inbox 監視・auto-heal・board 巡回・lease 更新の常駐） | agent-project の flow tick / board tick |
| agent-flow `submit` / remote daemon への git-bus 委譲（`location: daemon/remote`） | claim 分担（同一状態 repo）または board post |
| agent-amigos `serve` / hub 常駐 | agent-project の amigos tick |
| dashboard `git.js`（pull/commitPush/heal/health）・`gitAutoPush` | エンジンの同期 + `engine/status.json` + `commands/heal` |
| dashboard の CLI 実行（`dashboard:start`・`agent:*`） | OS サービス + `requests/` 往復 |
| `manage_flow_daemon`・flow/amigos の board 設定キー・`flowLockDir` プローブ | `agent-project.yaml` に一本化 |
| board クローン 4 種（project/flow/amigos/dashboard 各自） | agent-project の 1 クローン |

## 8. 移行フェーズ

| フェーズ | 内容 |
|---|---|
| P1 | flow daemon のループ本体を tick 関数群へ抽出（daemon コマンドは薄い互換ラッパとして残す）。amigos serve も同様。board.py は既に tick 形なので呼び出し元を差し替えるだけ |
| P2 | agent-project watch へ tick 統合・`engine/status.json`・`requests/` 実装。`location` は `local` 固定化（他は deprecation 警告） |
| P3 | dashboard から git.js・CLI 経路を削除し、status 表示・requests 経由へ置換 |
| P4 | daemon/serve コマンドと旧設定キーの削除。テスト（flow 519 / amigos 140 / project 801）の daemon 前提部分を tick 前提へ書き換え |

## 9. 懸念（重要度順）

- **C1: ローカルエンジン停止中は「指示が他 PC へ届かない」**。現行は dashboard 自身が
  push できたが、新設計では同一 PC のエンジンが唯一の push 役。エンジンが落ちていると
  投函はローカル滞留する（消えはしない）。緩和: status.json による明示表示
  （「このPCのエンジン停止中 — 反映は再開後」）と、他 PC の dashboard から操作する運用。
  **これは簡素化の対価として受け入れる判断が要る**。
- **C2: WSL VM の自動停止**。「daemon 必須」は Windows 構成では「WSL keep-alive 必須」を
  意味する。タスクスケジューラ/vmIdleTimeout の運用手順が新たな必須要件になり、
  ここが死ぬと C1 が全 PC で同時に起きる。セットアップスクリプト（install.py への組込み）と
  status 表示での検知を必須にすべき。
- **C3: amigos の応答性劣化**。役割協働（メッセージ往復・ロール募集）は serve の
  秒オーダー poll が前提だった。tick が agent-project の pace に律速されると、
  ミッション進行が分オーダーに落ちうる。緩和: amigos tick だけ内部で短周期
  サブループを回す（＝常駐の複雑さが一部戻る）か、pace をミッション活動中だけ
  短縮する適応間隔。**どこまで許容するか要判断**。
- **C4: 責務集中とループ肥大**。1 常駐に git 同期・監督・全 tick が乗る。1 ステップの
  ハング（git のネットワーク待ち・エージェント CLI の無応答）が全機能を止めないよう、
  act の detach 起動・ステップ毎のタイムアウト・例外隔離が**設計必須条件**になる。
  現 flow daemon の「板の巡回失敗は daemon を止めない」規約を全ステップへ一般化する。
- **C5: per-project 常駐と node 単位プロトコルの食い違い**。board 入札・amigos 参加は
  「ノード（PC）」の概念だが、常駐はプロジェクト単位。同一 PC に複数プロジェクトが
  あると、(a) 同一 repo を複数プロジェクトが宣言すれば二重入札、(b) node_id の一意性が
  崩れる。規約が要る: `node_id = <pc>-<project>`（別入札者として claim が解決）＋
  「1 repo を担当宣言できるのは PC 内で 1 プロジェクト」の lint。
- **C6: 長時間 run の監督の再実装リスク**。auto-heal・orphan 回収・max_runs は flow daemon で
  実証済みのロジック。移植では「agent-project 自身の再起動」との合成（再起動直後に
  実行中 run を殺さない・二重 heal しない）が新たな状態機械になる。P1 で tick 化する際、
  daemon テストをそのまま tick テストへ移せる形で抽出するのが安全。
- **C7: 対話機能のファイル往復化**。charter AI 補完・doctor は現在 dashboard から CLI 同期
  実行（数秒）。requests/ 往復では最悪「エンジンの 1 巡」待ちになる。緩和:
  requests/ だけ短周期（1–2 秒）の監視をエンジン側に置く（ローカル FS ポーリングは安価）。
  それでもエンジン停止中は機能しない（C1 と同根）。
- **C8: 9p 越しの読み性能**。bus の run ディレクトリや flow-archive は数百ファイルになり、
  UNC 越しの走査は遅い。緩和: エンジンが正規化ビュー（views/*.json — flow-archive 方式の
  一般化）を少数ファイルに materialize し、dashboard はそれだけ読む。これは
  「可視化もファイル経由」という新原則の自然な帰結だが、**ビュー生成という新責務**が
  エンジンに増える点は認識しておく。
- **C9: remote submit 廃止の等価性**。「別 PC の daemon へ単発依頼」は board post
  （workload=flow）で等価にできるが、board 必須になる＝最小構成でも board リポジトリが
  1 つ要る。1 PC 構成ではローカル dir 板で足りるとはいえ、セットアップ手順は 1 段増える。
- **C10: 移行コスト**。3 ツールのテスト（801/140/519）の daemon 前提部分・既存運用中の
  設定ファイル・ドキュメントの全面改訂。P1〜P4 の互換ラッパ期間を挟んでも、
  「旧構成と新構成が混在する期間」の相互作用（旧 daemon と新 tick が同じバスを触る）は
  テスト困難。移行期間は短く切る（板と同じ「契約は不変・プロセス配置だけ変える」性質を
  活かし、PC 単位で一斉切替）方針を推奨。

## 10. 非目標

- バス・板・封筒などの**データ契約の変更はしない**（プロセス配置の再編のみ）。
- インターネット越しの分散・認可機構の追加はしない（従来どおりオンプレ + git 認証）。
- dashboard の機能削減はしない（起動ボタンと 🩺 の実行主体が変わるだけで、
  できることは維持する）。
