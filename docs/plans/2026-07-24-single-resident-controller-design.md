# 常駐一本化設計案 — 1 PC = 1 ノードデーモン、エンジンはライフサイクル実行体に

- 日付: 2026-07-24
- 状態: **提案・改訂 1**（B 案採用: 常駐単位を「PC × プロジェクト」から「PC」へ変更、
  共通転送層 agent-sync を P0 に前置。初版 A 案との比較は §10）
- 動機: 構成・設定バリエーションの爆発と git 転送実装の重複がバグの温床になっている（§1）。
  エンジン常駐を必須と割り切り、**常駐プロセスを「1 PC = agent-node 1 本」に集約**する。
- 関連: [`2026-07-23-delegation-board-distributed-bidding-design.md`](./2026-07-23-delegation-board-distributed-bidding-design.md)（board 契約は不変）、
  [`schemas/board.schema.json`](../../schemas/board.schema.json)、
  [`schemas/delegation.schema.json`](../../schemas/delegation.schema.json)

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
**git 転送コードが 4 実装ある**ことだった。GitBus（agent-flow）だけが電源断・ロック残骸・
オブジェクト破損への回復を持ち、BoardRepo（agent-project）・BoardMirror（agent-amigos）・
`git.js`（dashboard）はそれぞれ独立に書かれた劣化コピーで、同じ穴を別々に踏む。
語彙ズレ（canceled/cancelled）も「同じ責務の複数実装」という同根の症状である。

したがって本設計は 2 段で攻める: **(P0) 転送を 1 実装に統一**し、**(P1〜) 常駐を 1 本に統一**する。

## 2. 新原則（4 行）

1. **転送は 1 実装**: git の clone / 間隔律速 pull / rebase・ロック残骸回復 / 破損時再クローン /
   push リトライを共通ライブラリ **agent-sync** に集約し、全ツールがこれだけを使う。
2. **常駐は 1 PC に 1 本**: プロジェクトを使う各 PC で **agent-node**（実体は
   `agent-project serve --all`）を常駐させる。flow / amigos の daemon 起動は廃止する。
3. **git を触るのはノードデーモンだけ**: 状態リポジトリ・flow バス・board・全ミラーの
   pull/push はノードデーモンが一手に担う。dashboard は git を一切使わない。
4. **dashboard はローカルクローンへのファイル読み書きだけ**: 指示・設定・可視化は
   すべてファイル契約経由。プロセス起動（CLI 実行）もしない。

```
┌─ PC-A ─────────────────────────────────────┐   ┌─ PC-B ──────────┐
│ Windows                                     │   │ （同型）         │
│  agent-dashboard（Electron）                │   │                  │
│   └ \\wsl.localhost\... のクローンへ        │   │                  │
│      ファイル読み書きのみ                   │   │                  │
│ WSL                                         │   │                  │
│  agent-node（常駐・PC で唯一）              │◀──┼── 共有 git       │
│   ├ ノード層（親プロセス）                  │   │  （状態repo／    │
│   │  ├ agent-sync スケジューラ（全ミラー）  │   │    board／       │
│   │  ├ board tick（node 名義で入札）        │   │    workspace）   │
│   │  ├ amigos tick（node 名義で参加）       │   └──────────────────┘
│   │  ├ nodes/<pc>.json・status.json（心拍） │
│   │  └ 子プロセスの監視・再起動             │
│   └ プロジェクト層（子プロセス × 登録数）   │
│      ├ projX: ingest→act→flow tick→reap     │
│      └ projY: 〃                            │
└─────────────────────────────────────────────┘
```

## 3. コンポーネント再定義

### 3.1 agent-sync = 共通転送層（新規・P0）

GitBus の実証済みの護りを唯一の実装として切り出す:

- clone（sparse / blob フィルタ / 空リポジトリフォールバック）・間隔律速 fetch/pull
- stale lock 掃除・中断 rebase の abort・電源断オブジェクト破損の検知と作り直し
- `pull --rebase` → 再 push の指数バックオフ（force push 禁止）
- ミラーのレジストリ: 「この PC が必要とするリモート」（状態repo・板・バス・workspace
  ミラー）を宣言的に持ち、**同一リモートのクローンを PC 内で 1 つに共有**する
  （現状の「board クローン 4 種」を 1 つに畳む）

agent-project / agent-flow / agent-amigos の転送コード（StateGit・GitBus 転送部・
BoardRepo・BoardMirror）はこれへ置換する。**このフェーズはトポロジ変更と独立に、
ツール単位で漸進的に安全に進められる**（結果が同じで実装だけ変わる）。

### 3.2 agent-node = ノードデーモン（常駐・PC で唯一）

スーパーバイザ形。**ノード層（親）**と**プロジェクト層（子プロセス）**に分ける:

- **ノード層（親プロセス）** — 「PC」という概念に属する仕事だけを持つ:
  - agent-sync のスケジューラ（全ミラーの pull/push を周期表で駆動）
  - **board 請負 tick**: node 名義（`node_id = <pc>`）で入札。落札した公示は
    workspace.url → プロジェクトの対応表で該当する子へ割り振る（子が
    `agent-flow run` を単発起動）。二重入札は構造的に起きない
  - **amigos 参加 tick**: node 名義でロール claim・手番実行・heartbeat・away
  - `nodes/<pc>.json`（能力宣言）と `engine/status.json`（心拍・同期健康）の書き出し
  - 子プロセスの起動・死活監視・再起動（プロジェクトの追加・削除は instances
    レジストリの変化で追従）
- **プロジェクト層（子プロセス・登録プロジェクトごと）** — 現行 agent-project の
  watch ループから git 同期を抜いたもの:
  ```
  ingest（commands/ inbox/ needs/ requests/）
  → plan / act（claims で分担。act は常に agent-flow run の単発 detach 起動）
  → flow tick（自PCの run の監督: orphan 検知→auto-heal・終端の reap・cancel 伝搬）
  → board 依頼 tick（post / result 回収）
  → status 書き出し
  ```
  1 プロジェクトの暴走・クラッシュは子プロセスに閉じ、親が再起動する。

tick は単一ループ直列ではなく**周期表で駆動**する（C3 対策の正規化）:

| tick | 既定周期 | 備考 |
|---|---|---|
| amigos 参加 | 5s | 役割協働の応答性を維持（現 serve と同等） |
| requests/ 監視 | 1–2s | 対話機能（charter 補完・doctor）。ローカル FS のみで安価 |
| board（入札・依頼） | 30–60s | 現 GitBus ポーリングと同等 |
| プロジェクトループ | pace（現行設定） | act の律速は従来どおり |
| 状態 repo sync | state_git_interval（現行） | dashboard の投函もここで必ず載る |

- **`location` 概念は廃止**。`daemon` / `remote` は消え、実行は常に「claim を取った PC が
  `agent-flow run` を単発起動」。PC 間の分担は (a) coordination（git-cas + claims +
  availability）と (b) board、の 2 軸だけになる。
- flow daemon が持っていた auto-heal・max_runs 律速・inbox 受理は、自 PC の run に
  限って子ループが行う（daemon テストを tick テストとして移植する）。
- `manage_flow_daemon` は廃止（管理対象の daemon 自体が無くなる）。

### 3.3 agent-flow = run ライフサイクルの実行体（常駐なし）

- **残す**: `run` / `resume` / `cancel` / `result` の CLI、バス上の run レイアウト、
  タスク claim プロトコル、run 内のノード分散（GitBus 契約）、gitlab executor。
- **廃止**: `daemon` サブコマンド。ロジックは**関数（tick）として残し**、agent-node が呼ぶ。
- 「他 PC の daemon に submit」だけが消える（等価機能は board post で提供）。

### 3.4 agent-amigos = ミッションライフサイクルの実行体（常駐なし）

- **残す**: ミッション/ロール/メッセージのバス契約、claim・away プロトコル、納品棚。
- **廃止**: `serve` 常駐。参加ロジックを `tick(participate)` に切り出し、agent-node の
  ノード層が 5s 周期で呼ぶ（応答性は現 serve と同等 — 単一ループ案の弱点を周期表で解消）。
- hub（long-poll）は転送加速のオプション契約として残すが v1 不採用（常駐が増えるため）。

### 3.5 agent-dashboard = ファイル操作フロントエンド（Windows）

- **廃止**:
  - `base/main/git.js` 全体（pull / commitPush / heal / health / gitAutoPush 設定）
  - `dashboard:start` の CLI 実行・`agent:charter` / `agent:doctor` 等の CLI 直接起動
  - flow daemon ロックのプローブ（`flowLockDir`）・daemon 稼働判定
- **読み**: ノードデーモンが鮮度を保証するローカルクローンのファイルだけを読む
  （既存の refreshSec ポーリング。inotify に依存しない）。
- **書き**: 既存契約ファイルのみ — commands/ inbox/ needs/ reviews/ assignments/ に加え、
  CLI 直接実行の置き換えとして `requests/`（§4）。
- 同期の健康・エンジン稼働は `engine/status.json` の表示に一本化（🩺 の修復実行は
  エンジン側の責務になり、dashboard は `commands/heal` を投函するだけ）。
- 設定は「プロジェクトルート（UNC パス）の列挙 + 表示設定」だけに縮退。エンジン側の
  設定はノード設定（`agent-node.yaml`: node_id・tags・repos・board・amigos 参加）と
  プロジェクト設定（`agent-project.yaml`: 従来からノード的キーを除いたもの）の 2 ファイルへ
  整理する。flow / amigos の yaml から daemon 関連キーが消える。
- **任意の加速装置**（オプトイン・契約は変えない）: 同一 PC 内に限り、dashboard →
  ノードデーモンの読み取り/対話専用 localhost TCP を許す（WSL2 は Windows から
  localhost で到達可能）。真実は常にファイルで、socket 不通でもファイル経由で成立する
  — board 設計の「webhook は加速装置」と同じ位置づけ。

## 4. ファイル契約の追加・変更

| パス | 方向 | 内容 |
|---|---|---|
| `.agents/engine/status.json` | ノードデーモン → dashboard | 心拍（node・pid・ts）・tick 周期表の実績・同期健康（ahead/behind/エラー）・プロジェクト別の子プロセス状態・実行中 run 一覧。**書き手はノードデーモンのみ** |
| `requests/<id>.json` → `requests/<id>.response.json` | dashboard → エンジン → dashboard | 旧 `agent:charter` / `agent:doctor` / 診断の往復。同一 PC のエンジンが処理（requests はローカル専用・状態 repo に同期しない） |
| `commands/heal` | dashboard → エンジン | 🩺 の置き換え（stale lock 掃除・rebase 巻き戻し・強制同期を agent-sync が実行） |
| `nodes/<pc>.json`（board） | ノードデーモン → 板 | 既存 board 契約の未実装部分をノード層が実装（能力宣言・観測用） |
| `commands/{pause,resume,stop}` | 既存 | 不変（stop したエンジンの再開だけは OS サービス管轄 — §5） |

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
  `boot.systemd=true`）。プロジェクトの増減で unit は増えない（instances レジストリで
  子が追従する）。
- **WSL VM は全セッション終了で自動停止する**ため、Windows 側に keep-alive が必須
  （タスクスケジューラでログオン時に `wsl.exe -d <distro> --exec sleep infinity` 等を常駐、
  または `.wslconfig` の `vmIdleTimeout` 延長）。keep-alive も PC あたり 1 個で固定。
  セットアップは install.py に組み込み、欠落は status 表示と doctor で検知する。
- dashboard の「起動」ボタンは廃止し、status.json が古いときに「このPCのエンジンが
  停止しています（起動コマンド: ...）」の案内表示に置き換える。

## 6. マルチ PC・メンテナンス停止

- 分担は既存機構を**必須既定化**する: `coordination: git-cas`（CAS 遷移 + fencing）＋
  claims ＋ `availability`（daily_stop / drain）。全 PC が同型の常駐なので、どの PC が
  落ちても残りが ingest / act / reap / 入札を続ける。
- **graceful 停止**（systemd stop / drain 窓）はノード層が一括で行う: 全子の claims 解放 →
  amigos away 宣言 → board の実行中 status へ `away` 書き込み（P2 契約の実装）→
  未 push 分の最終 sync_push。**PC 単位の常駐なので、メンテ時のフックが 1 箇所で済む**
  （A 案ではプロジェクト数ぶんの停止順序を気にする必要があった）。
- **突然死**: 他 PC がタスク claim の lease 失効で回収。board は再入札（二重実行は
  結果整合で吸収 — board 設計 §7/§8 のまま）。自 PC の中断 run は再起動後の
  flow tick が orphan 検知 → resume-run。
- **全 PC 停止**: dashboard の投函はローカルクローンに滞留し、復帰したノードデーモンが
  sync_push で押し出す（サイレント消失なし。現行と同じ性質）。

## 7. 廃止一覧（簡素化の実収支）

| 廃止 | 行き先 |
|---|---|
| 転送実装 4 種（GitBus 転送部・StateGit・BoardRepo・BoardMirror・dashboard git.js） | **agent-sync 1 実装** |
| agent-flow `daemon`（inbox 監視・auto-heal・board 巡回・lease 更新の常駐） | agent-node の flow tick / board tick |
| agent-flow `submit` / remote daemon への git-bus 委譲（`location: daemon/remote`） | claim 分担（同一状態 repo）または board post |
| agent-amigos `serve` / hub 常駐 | agent-node のノード層 tick（5s 周期） |
| dashboard `git.js`・`gitAutoPush` | agent-sync + `engine/status.json` + `commands/heal` |
| dashboard の CLI 実行（`dashboard:start`・`agent:*`） | OS サービス + `requests/` 往復 |
| `manage_flow_daemon`・flow/amigos の board 設定キー・`flowLockDir` プローブ | `agent-node.yaml` / `agent-project.yaml` の 2 ファイルに整理 |
| board クローン 4 種（project/flow/amigos/dashboard 各自） | agent-sync 管理の PC 内 1 クローン |
| systemd unit × プロジェクト数（A 案） | `agent-node.service` 1 個 |

## 8. 移行フェーズ

| フェーズ | 内容 |
|---|---|
| **P0** | **agent-sync 抽出**。GitBus の護りを共通ライブラリ化し、BoardRepo → BoardMirror → StateGit の順に置換（ツール単位・挙動不変・既存テストで担保）。dashboard git.js はこの時点では触らない |
| P1 | flow daemon / amigos serve のループ本体を tick 関数群へ抽出（daemon / serve コマンドは薄い互換ラッパとして残す）。board.py は既に tick 形なので呼び出し規約だけ揃える |
| P2 | agent-node 実装（スーパーバイザ + 周期表スケジューラ + ノード層 tick）・`engine/status.json`・`requests/`。`location` は `local` 固定化（他は deprecation 警告）。node_id の既定を PC 名へ |
| P3 | dashboard から git.js・CLI 経路を削除し、status 表示・requests 経由へ置換 |
| P4 | daemon/serve コマンドと旧設定キーの削除。テスト（flow 519 / amigos 140 / project 801）の daemon 前提部分を tick 前提へ書き換え |

移行期間の混在（旧 daemon と新 tick が同じバスを触る）はテスト困難なため、**PC 単位で
一斉切替**する（データ契約が不変なので、切替済み PC と未切替 PC の共存は問題ない —
板と同じ「契約は不変・プロセス配置だけ変える」性質）。

## 9. 懸念（重要度順）

- **C1: ローカルエンジン停止中は「指示が他 PC へ届かない」**。現行は dashboard 自身が
  push できたが、新設計では同一 PC のノードデーモンが唯一の push 役。エンジンが落ちて
  いると投函はローカル滞留する（消えはしない）。緩和: status.json による明示表示と、
  他 PC の dashboard から操作する運用。**これは「dashboard に git をさせない」と決めた
  瞬間に、どの案でも払う対価** — 受け入れの判断が要る。
- **C2: WSL VM の自動停止**。「daemon 必須」は Windows 構成では「WSL keep-alive 必須」を
  意味する。ここが死ぬと C1 が全機能で同時に起きる。keep-alive は PC あたり 1 個に
  固定されたが、必須要件であることは変わらない — install.py への組込みと status / doctor
  での検知を必須にする。
- **C3: 1 プロセスへの責務集中**。ノード層のハング（git のネットワーク待ち等）が全 tick を
  止めないよう、周期表スケジューラのステップ毎タイムアウト・例外隔離が**設計必須条件**。
  プロジェクト層は子プロセスで隔離済みだが、親の再起動時に子を巻き込まない
  （子は生かして再 attach する）設計が要る。
- **C4: 長時間 run の監督の再実装リスク**。auto-heal・orphan 回収・max_runs は flow daemon で
  実証済みのロジック。移植では「ノードデーモン自身の再起動」との合成（再起動直後に
  実行中 run を殺さない・二重 heal しない）が新たな状態機械になる。P1 で daemon テストを
  そのまま tick テストへ移せる形で抽出するのが安全。
- **C5: マルチプロジェクトの資源競合**。1 PC の全プロジェクトが同じノードデーモン配下に
  乗るため、あるプロジェクトの重い act がエージェント CLI・CPU を占有しうる。
  緩和: ノード層に PC 全体の `max_concurrent`（board 契約の宣言と同じ語彙）を置き、
  子ループの act 起動をノード層のセマフォで律速する。
- **C6: 対話機能のファイル往復化**。charter AI 補完・doctor は現在 dashboard から CLI 同期
  実行（数秒）。requests/ 往復は 1–2s 監視で緩和するが、エンジン停止中は機能しない
  （C1 と同根）。§3.5 の localhost socket（オプトイン）でさらに縮められる。
- **C7: 9p 越しの読み性能**。bus の run ディレクトリや flow-archive は数百ファイルになり、
  UNC 越しの走査は遅い。緩和: エンジンが正規化ビュー（views/*.json — flow-archive 方式の
  一般化）を少数ファイルに materialize し、dashboard はそれだけ読む。ビュー生成という
  新責務がエンジンに増える点は認識しておく。
- **C8: remote submit 廃止の等価性**。「別 PC の daemon へ単発依頼」は board post
  （workload=flow）で等価にできるが、board 必須になる＝最小構成でも board リポジトリが
  1 つ要る（1 PC 構成ではローカル dir 板で可）。セットアップ手順は 1 段増える。
- **C9: node_id の移行**。板・amigos の名義が「プロジェクト付きノード」から「PC」へ変わる。
  実行中の委譲・ミッションを跨いで切り替えると自分名義のファイル（status/<who>.json 等）を
  見失うため、切替は「実行中の委譲が無い状態」で行う（PC 単位一斉切替の手順に含める）。
- **C10: 移行コスト**。3 ツールのテストの daemon 前提部分・既存運用中の設定・ドキュメントの
  全面改訂。P0（転送統一）が先に単体で価値を出すため、**P0 だけで止めても現状構成の
  堅牢化として成立する**という撤退線を持てるのが本改訂の利点。

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

- バス・板・封筒などの**データ契約の変更はしない**（プロセス配置の再編のみ）。
- インターネット越しの分散・認可機構の追加はしない（従来どおりオンプレ + git 認証）。
- dashboard の機能削減はしない（起動ボタンと 🩺 の実行主体が変わるだけで、
  できることは維持する）。
- amigos hub・localhost socket は「加速装置」の位置づけを超えない（真実は常にファイル）。
