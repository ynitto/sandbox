# agent-app: 参加者のトークンを持ち寄る共有実行 仕組みの設計

日付: 2026-09-11 / 対象: `tools/agent-app`、`schemas/board.schema.json`、`schemas/delegation.schema.json`、`tools/agent-board`
状態: **§8.7 の「板を持たない直結」で実装（2026-09-12）。** §2〜§8.6 は板（Syncthing）案の記録として残す。実装の契約は `docs/specs/agent-app-spec.md` §15、置き場は `tools/agent-app/src/main/share/`。未実装: 書き込みの依頼と成果の納品（§5.2）、共有の画面（付録 A は未承認）

## 0. 一文で

agent-app を入れた人の PC を「参加者」にし、各人の AI CLI の余った利用枠を **板（agent-board。各 PC の
Syncthing が LAN で同期するフォルダ 1 つ）** 越しに持ち寄る。依頼は誰でも板に投函でき、資格と空きのある参加者が
先勝ちで拾い、自分の PC で自分の CLI を動かして答え（文章、または成果ブランチ）を板へ返す。
中央のサーバも git サーバの負荷も API キーの共有も無い。PC 同士が LAN で直接複製する。順番・公平さ・利用枠の勘定は、全員が同じ
板のファイルから同じ規則で導く。

## 1. 問題と狙い

全社で「トークンが足りない」と言われているが、実態は数人が枠を使い切り、大半の人の枠は
毎日余って消えている。共有 API キーは契約と監査で成り立たず、中央のプロキシは「誰の枠か」を
壊す。要るのは、**各人の PC で各人の CLI が動くまま、仕事だけを空いている PC へ運ぶ**道。

最適化するもの（優先順）:

1. 余っている枠の利用率。空いている参加者がいるのに依頼が待つ状態を作らない。
2. 依頼者の待ち時間。投函から答えまでの上乗せを、複製の数秒に収める。
3. 公平さ。少数の大量依頼者が列を独占しない。依頼者ごとの消費を全員が見られる。
4. 参加者の安全。他人の依頼が自分の作業ツリーや CLI の履歴を汚さない。受ける量を自分で決められる。

## 2. 大枠

### 2.1 ネットワーク構成

PC 同士が LAN で直接つながる。板は各 PC の **Syncthing** が同期するフォルダ 1 つで、全員が
全ファイルを持つ（メッシュ）。git サーバ（forge）が担うのは write の成果ブランチだけになり、
巡回の pull / push は無くなる。各 CLI は各 PC の自分のアカウントで各ベンダーへ出る。

```text
        LAN（同じサブネットは UDP 21027 のブロードキャストで発見。跨ぐなら静的アドレス）
 ┌──────────────────────────────────────────────────────────────────────────────────────┐
 │  PC-A 依頼者 + 参加者          PC-B 参加者                 PC-C 参加者（Windows）        │
 │ ┌──────────────────────┐    ┌──────────────────────┐    ┌──────────────────────────┐ │
 │ │ agent-app            │    │ agent-app            │    │ agent-app                │ │
 │ │   ↕ REST / events    │    │   ↕ REST / events    │    │   ↕ REST / events        │ │
 │ │ Syncthing            │◀──▶│ Syncthing            │◀──▶│ Syncthing（Windows 版）   │ │
 │ │  板フォルダ           │TLS │  板フォルダ           │TCP │  板フォルダ               │ │
 │ │  紹介者(introducer)  │22000│                      │    │                          │ │
 │ │ claude / codex CLI   │    │ claude CLI           │    │ WSL: git, kiro CLI       │ │
 │ │ 登録 repo: app       │    │ 登録 repo: app, docs │    │ 登録 repo: app           │ │
 │ └──────────┬───────────┘    └──────────┬───────────┘    └────────────┬─────────────┘ │
 │            └──────── メッシュ（全対全。板は全員が全ファイルを持つ）───────┘             │
 └──────────────────────────────────┬───────────────────────────────────────────────────┘
                                    │ write の成果だけ（`share/<id>` の push。落札ごと 1 回）
                          ┌─────────▼─────────┐      各 CLI は各人のアカウントで
                          │ forge             │      Anthropic / OpenAI / AWS へ ──▶ インターネット
                          │  team/app.git     │
                          └───────────────────┘
```

| 経路 | ポート | 内容 | 頻度 |
|---|---|---|---|
| PC ↔ PC（Syncthing） | 22000/tcp・udp（TLS。デバイス ID で相互認証） | 板フォルダの差分 | 変化したとき（数秒） |
| PC → LAN（発見） | 21027/udp ブロードキャスト | 「私はここにいる」 | 30 秒ごと |
| agent-app → Syncthing | 127.0.0.1:8384（API キー。外へ出ない） | 設定・接続状態・events | long-poll |
| PC → forge | ssh / https | `origin/<base>` の fetch、`share/<id>` の push | 落札ごと |
| PC → ベンダー | CLI 自身 | 従来どおり | 実行ごと |

Syncthing の global discovery と relay は切る（LAN の外へ出さない）。サブネットを跨いで
ブロードキャストが届かないときは、デバイスの静的アドレス（`tcp://host:22000`）を設定するか、
小さな discovery サーバ（`stdiscosrv`）を 1 台置く。

**参加の手順**（新しい PC）:

1. Syncthing を入れる（winget / brew / apt。常駐サービス）。agent-app は `127.0.0.1:8384` と
   `config.xml` の API キーを見つける。
2. 設定 > 共有に「紹介者のデバイス ID」と「板のフォルダ ID」を入れて参加する。agent-app が REST で
   紹介者デバイス（introducer）と板フォルダ（`userData/share/board`）を作る。
3. 紹介者の PC の agent-app に「参加希望: pc-c」が出る。持ち主が受け入れると、紹介者が板フォルダに
   pc-c を足し、Syncthing の introducer 機能で他の全員に pc-c を紹介する。以後は全対全。
4. 紹介者が落ちていると新規参加だけが待つ。参加済みの同期には影響しない。紹介者は 2 台まで置ける。

### 2.2 プログラム構成（agent-app の中）

新しい塊は `src/main/share/` の 6 ファイル。git 同期は無く、`board.js` はフォルダの読み書きだけ、
`syncthing.js` が Syncthing の REST と events を扱う。既存側は `runTurn` に分岐が 1 つ増える。

```text
 renderer（既存）          preload         main（Electron 主プロセス）
 ┌──────────────────┐ turn:send ┌──────┐ ┌────────────────────────────────────────────────────┐
 │ 会話・実行設定    │ ────────▶ │window│▶│ ipc.js runTurn ─┬ tmux / headless（既存）            │
 │  起動方針「共有」 │ share:*   │ .api │ │                 └ board → share/requester           │
 │ 共有の画面（未定）│ ◀──────── │      │ │ ┌ src/main/share/ ───────────────────────────────┐ │
 └──────────────────┘ turn:*    └──────┘ │ │ requester.js   本文合成→post、答え保存、再投函、  │ │
                                          │ │                成果の取り込み                     │ │
                                          │ │ participant.js 資格→入札→10 秒待つ→実行→result、  │ │
                                          │ │                lease、残骸掃除                    │ │
                                          │ │ queue.js       並び鍵・実効優先度・資格（純関数）  │ │
                                          │ │ ledger.js      台帳・上限・quota 学習・can_accept  │ │
                                          │ │ board.js       板フォルダの読み書き・toView・勝者  │ │
                                          │ │ syncthing.js   REST（設定・接続・保留デバイス）と   │ │
                                          │ │                /rest/events の long-poll          │ │
                                          │ └───┬───────────────┬───────────────┬─────────────┘ │
                                          │  agentCli.js     worktree.js     host.js（git は     │
                                          │  (turnCmd)       (share-<id>)    project repo だけ） │
                                          │  store.js / settings.js / sessionSetup / skillSelection │
                                          └──────────────────────┬─────────────────────────────┘
                                                                 │ http://127.0.0.1:8384（API キー）
                                                     ┌───────────▼───────────┐
                                                     │ Syncthing（同じ PC）    │ ◀──▶ 他の PC の Syncthing
                                                     │  板フォルダ agent-board │
                                                     └───────────────────────┘
```

実行時の形（main プロセスの中）:

```text
 Electron main（1 プロセス）
  ├ events の long-poll（ItemFinished / FolderCompletion / DeviceConnected / PendingDevicesChanged）
  │    板のファイルが変わったら: requester.watch（自分の投函の相・答え）→ participant.pick（資格・入札）
  ├ 60 秒の timer: nodes/<me>.json の心拍と枠、lease 延長、失効した残骸の掃除
  ├ 実行中の CLI 子プロセス（inflight ≤ maxConcurrent。会話の turnGate とは別枠）
  ├ 板への書き込みは tmp に書いて rename（Syncthing は `.stignore` で `*.tmp.*` を見ない）
  └ renderer へ turn:*（会話の進捗・答え）と share:*（列・参加者・接続状態）
```

### 2.3 キューの仕組み

**列のファイルは無い。** 列は板の `delegations/*/` の「ファイルの有無」から各参加者が毎巡回
導く。同じファイル集合から同じ規則で並べるので、全員が同じ列を見る。

```text
 板の上（真実）                                       各参加者が導くもの（毎巡回・同じ規則）
 delegations/
  dg-…-01/ post.json                                   open      ┐
  dg-…-02/ post.json bids/pc-b.json status/pc-b.json   working   │  列 = open のものを並び鍵で整列
  dg-…-03/ post.json bids/pc-b.json result.json        done      │   1. 実効優先度 降順
  dg-…-04/ post.json cancelled.json                    cancelled │      = 宣言 (high2/normal1/low0)
  dg-…-05/ post.json bids/pc-c.json（lease 失効）      open      │        + 待ち 30 分ごと +1（上限 2）
  dg-…-06/ post.json                                   open      │   2. 依頼者の今日の落札数 昇順
 nodes/                                                          │      （result.json を数える）
  pc-a.json pc-b.json pc-c.json   … 参加者の宣言と枠   ┘   3. posted_at 昇順
```

拾い方（参加者 1 台。`post.json` の到着イベントか 60 秒の timer で起きる）:

```text
 列（上から）          資格（AND）                              自分の空き
 ┌ dg-06 high  suzuki ┐ CLI が交わる? repo を持つ? mode 受ける?   inflight < max?
 │ dg-01 normal nitto │ 依頼者の上限内? contract_version OK?       今日の上限内?
 │ dg-05 normal tanaka│ 自分の投函でない?                           CLI の quota OK?
 └ …                 ┘                                              ↓
   ↓ 資格を満たす最初の 1 件（空きの数だけ）に入札 → bids/<me>.json {ts, lease_until}
   ↓ 10 秒待つ（入札が全 PC に行き渡る）→ 勝者 = lease 内の入札のうち (ts, who) 最小
   ↓ 自分なら status/<me>.json {working} → CLI 起動。違えば何もしない
```

ロックは無い。**入札 = 自分名義のファイル 1 つ**で、勝敗は全員が同じ式で読む。lease（900 秒）が
切れた入札と実行は無かったことになり、依頼は列へ戻る。

### 2.4 1 件の依頼の流れ

```text
 依頼者 PC-A               Syncthing の網（LAN・全対全）        参加者 PC-B                 参加者 PC-C
   │ 「共有」で送信                                                │                           │
   │ 本文を合成                                                    │                           │
   ├ post.json を板フォルダへ書く                                  │                           │
   │   ── 複製（数秒）──────────────────────────────────────────▶ │ ────────────────────────▶ │
   │                                       events: ItemFinished post.json        同じ
   │                                       列を導き、資格 OK → bids/pc-b.json     → bids/pc-c.json
   │ ◀─────────────────────── 複製 ─────────────────────────────┤ ◀──── 複製 ─────────────▶ │
   │                                       10 秒待って勝者を読み直す: pc-b        pc-b。自分は負け
   │                                       status/pc-b.json {working}            （何もしない）
   │ ◀── 複製 ── 会話: 引受 pc-b                                   │                           │
   │                                       fetch origin/<base> → worktree share-<id>           │
   │                                       CLI ヘッドレス（≤ 900 s。60 秒ごとに lease 延長）   │
   │                                       （write: commit → push share/<id> → forge）          │
   │                                       result.json → 台帳に 1 行、worktree 掃除             │
   │ ◀── 複製 ── 会話に答えを保存                                  │                           │
   │ （write: 取り込む → fetch → merge --ff-only）                 │                           │
```

上乗せの時間は「複製の数秒 + 入札の 10 秒待ち + 答えの複製の数秒」で、実行時間を除けば
15 秒前後。git 板の 40 秒より短く、forge には触らない。

### 2.5 置き場の一覧

| 置き場 | 何があるか | 誰が書くか | 寿命 |
|---|---|---|---|
| 板 `nodes/<node>.json` | 参加者の宣言・心拍・枠の射影 | その参加者だけ | 参加している間 |
| 板 `delegations/<id>/post.json` `cancelled.json` | 依頼と取り下げ | 依頼者だけ | 終端から 7 日 |
| 板 `delegations/<id>/bids/<who>.json` `status/<who>.json` | 入札・実行状態 | その参加者だけ | lease |
| 板 `delegations/<id>/result.json` | 答え（文章）・成果ブランチの名前 | 落札した参加者 | 終端から 7 日 |
| 板 `delegations/<id>/attachments/` | 明示添付（1 MB まで） | 依頼者 | 依頼と同じ |
| project repo `share/<id>` | write の成果（コミット） | 落札した参加者 | 取り込み後に依頼者が消す |
| 各 PC `userData/share/board/` | 板フォルダ（Syncthing が全 PC へ複製） | Syncthing | 参加している間 |
| 参加者 `userData/share/ledger/` | 台帳（件数・秒・トークン） | 参加者 | 日次ファイル |
| 参加者 `<repo>/.worktrees/share-<id>` | 実行中の作業ツリー | 参加者 | 実行中（publish 失敗時は残す） |
| 依頼者 `sessions/<id>.json` | 会話（`share.id` と答え） | 依頼者の agent-app | 会話と同じ |

## 3. 主体と責務

### 3.1 参加者（node）

- 身元は `node_id`（`agentcore.nodeid` と同じ正規形: 小文字・`[a-z0-9._-]`。既定は `<user>.<host>`）。
  板のファイルはこの名義で分割され、同じ PC からは常に同じ綴りが出る。
- 宣言するもの（`nodes/<node-id>.json`）: 受けられる仕事の種類、提供する CLI、担当できる
  リポジトリ（登録リポジトリの `origin` URL）、同時数、心拍、利用枠の射影、今日の実績。
- 決めるもの（設定）: 参加するか、提供する CLI、書き込みの依頼を受けるか、同時数、1 日の上限
  （件数・計測できれば秒とトークン）、依頼者 1 人あたりの上限。
- やること: 巡回、入札、実行、報告、心拍、lease の延長、失効した自分の残骸の掃除。

### 3.2 依頼者

- 会話の起動方針「共有」で投函する。投函は依頼者の名義（`posted_by`）で、優先度と要求
  （CLI、モード、リポジトリ）を付ける。
- 自分の投函だけを取り下げ・優先度変更・再投函できる（`post.json` / `cancelled.json` は依頼者の書き込みパス）。
- 答えを会話に取り込む。成果ブランチがあれば作業フォルダへ取り込む。

### 3.3 板

- 実体は各 PC の Syncthing が同期するフォルダ 1 つ（`userData/share/board`）。契約は
  `board.schema.json` と `delegation.schema.json` を additive に広げたもの（§11）。
- 参加資格 = 板フォルダを共有するデバイスとして紹介者に受け入れられていること。アプリ側に
  認証は持たない（Syncthing のデバイス ID と TLS が担う）。
- 板は処理を持たない。誰かの PC が落ちても残りで動き、復帰した PC には差分が届くだけ。

## 4. 依頼のライフサイクル

```text
                 入札（先勝ち）          status: working           result.json
   open ───────────────▶ claimed ───────────────▶ working ───────────▶ done / failed
    ▲                      │                         │
    │   lease 失効（参加者が消えた）                  │  lease 失効
    └──────────────────────┴─────────────────────────┘   ＝ open に戻り、別の参加者が拾い直せる
   open / claimed / working ── cancelled.json（依頼者）──▶ cancelled
```

- **相は板のファイルの有無から導く**（`delegation_view` の規則。agent-dashboard の
  `board-adapter.js` の `toView` と同じ JS を agent-app にも置く）。
- **勝者**は lease 内の入札のうち `(ts, who)` 最小の 1 件（既存規則）。入札は `renew_lease` と同じ
  やり方で ts を温存し、残りが半分を切ったときだけ延長する。
- **lease** は入札・実行とも 900 秒。参加者が巡回のたびに延長する。参加者が消えれば失効し、
  open に戻る。実行途中の CLI は捨てられる（成果は残らない。次の参加者が最初からやる）。
- **投機実行**: 板の同期遅れで 2 台が同時に「自分が勝者」と読むことはありうる。入札のあと
  10 秒待って勝者を読み直すので通常は起きないが、起きても板の設計 §7 が許すとおり両方走ってよい。
  `result.json` が 2 つ書かれたら Syncthing の衝突解決（更新時刻が新しい方を残し、他方を
  `.sync-conflict-*` に退ける。全 PC で同じ結果になる）に任せ、残った 1 つが成果。負けた側は
  次の更新で他人の `result.json` を見て、まだ走っていれば CLI を止め、局所ログに「lost」と残す。
- **取り下げ**: 依頼者が `cancelled.json` を書く。実行中の参加者は次の巡回で見て CLI を止め、
  `status` を `cancelled` にする。`result.json` は書かない。
- **再投函**: 失敗した依頼は新しい id で再投函する（`retry_of` に元の id）。依頼者側は、失敗の
  理由が参加者側の枠切れ（`quota`）か一過性（`transient`）なら **自動で 1 回だけ再投函**する。
  それ以外（本文の問題、CLI の非 0 終了）は人に見せて止める。

## 5. 仕事の種類と実行

板の仕事の種類（`workload`）に `turn`（依頼 1 件に応答 1 件）を足す。flow / amigos の公示は
既存どおりで、agent-app の一覧はそれらも読むだけは読む。`turn` を受けるのは agent-app だけ。

### 5.1 読み取り（`turn.mode: read`）

- 答えは文章。参加者は CLI を読み取り専用（`readonly_args`）で起こす。
- `workspace` が無ければ cwd は `userData/share/scratch/<id>/`（空フォルダ。実行後に消す）。
- `workspace` があれば、参加者はその `url` を自分の登録リポジトリの `origin` と照合し、一致した
  リポジトリで **`origin/<base>` を fetch して一時 worktree `share-<id>`** を切り、そこを cwd にする。
  参加者自身の作業ツリーには触れず、依頼者が見ている基準と同じ内容を読む。終わったら worktree を消す。
- `readonly` を `enforced` で保証しない CLI（定義の `readonly` が `enforced` でないもの）でも、
  一時 worktree の外へは出ないので、書かれても捨てられる。参加者は提供 CLI を `enforced` のものに
  絞ることもできる。

### 5.2 書き込み（`turn.mode: write`）

- 答えは文章 + **成果ブランチ**。参加者は `origin/<base>` から worktree `share-<id>`（ブランチ
  `share/<id>`）を切り、CLI を書き込み（自動承認）で起こし、終了後に変更があれば 1 コミットにして
  `origin` へ `share/<id>` を push する。`result.json` に `branch` / `commit` / `base_commit` /
  `files_changed` を載せる（agent-flow の `result.branch` と同じ置き場）。
- push に失敗したら成果を消さない。worktree とローカルブランチを残し、`result.json` は
  `status: failed` + `error_class: publish_failed` で返す。参加者の画面に「push できていない成果」
  として残し、手で push したら `result` を書き直せる（agent-flow のリモート公開復旧と同じ考え方。
  一時 worktree を復旧元にしないため、write のときだけ worktree を残す）。
- 依頼者側の前提: 会話の作業フォルダが **clean で、HEAD が `origin` 上のブランチと一致**していること。
  満たさなければ投函前に 1 行で断る（参加者は依頼者のコミットしていない変更を見られない）。
- 依頼者の取り込み: 答えの下に「取り込む」。`git fetch origin share/<id>` して、作業フォルダへ
  `merge --ff-only`。早送りできない（依頼者がその後に進めた）ときは、`share/<id>` の worktree を
  作って新しい会話で開く道へ倒す。会話の作業フォルダは作った後で変えない不変条件を守る。
- 参加者は「書き込みの依頼は受けない」を選べる（既定は受けない）。受けるのは登録リポジトリだけ。

### 5.3 会話の連続性

- 投函する本文は、ヘッドレス経路と同じ順で合成する: 共通指示 → スキル本文 → 会話履歴の再送
  （`replayPrompt`）→ 今回の依頼 → 添付の案内。相手の PC に依頼者のスキルは無い前提で、
  SKILL.md の本文を常に埋め込む（非ネイティブ CLI と同じ扱い）。開始アクション（CLI 初回の
  コマンド）は相手では実行しない。
- CLI のセッション ID は PC を跨がない。参加者は毎回新しいセッションで起こし、`no_session_args`
  を持つ CLI ではセッションを残さない（他人の会話で参加者の CLI 履歴を汚さない）。
- 答えは依頼者の会話に assistant メッセージとして保存する。`cli` は相手の CLI、実行情報に
  参加者名・CLI・所要時間を載せる。ローカル CLI のエントリは進めないので、次にローカルで送る
  ターンはこの答えを再送で見る（CLI を渡り歩く会話と同じ仕組み）。

### 5.4 添付の運び方

- 作業フォルダの中のファイル（`{rel}`）: `workspace` があれば相対パスで伝える。無ければ断る。
- 貼り付け・添付したファイル（userData の添付）: `delegations/<id>/attachments/<name>` に置く。
  合計 1 MB まで。参加者は scratch へ写して絶対パスを本文に足す（既存 `withAttachments` の形）。
  超えるものは投函前に 1 行で断る。板は git なので、大きなバイナリを流さない。

## 6. 順番と公平さ

参加者が open の中から拾う順は、全員が同じ板から同じ結果を出せる決定的な鍵で決める。

```text
並び鍵 = ( 実効優先度 降順,  依頼者の今日の落札数 昇順,  posted_at 昇順 )
実効優先度 = 宣言の優先度（high=2 / normal=1 / low=0）+ 待ち時間 30 分ごとに 1（上限 2）
```

- **優先度**は依頼者が宣言する（`priority`。既定 normal）。他人の依頼の優先度は変えられない
  （`post.json` は依頼者のパス）。
- **依頼者の今日の落札数**を鍵に入れるので、同じ優先度なら「今日まだ答えてもらっていない人」が
  先になる。少数の大量依頼者は自然に後ろへ回る。数えるのは板の `result.json`（`resolved_at` が
  今日、`posted_by` ごと）で、参加者ごとの局所台帳ではない。
- **待ち時間による繰り上げ**で low の飢餓を防ぐ。
- **参加者側の自己抑制**（資格判定。すべて AND）:
  - 仕事の種類と mode を受ける宣言をしている
  - `requires.agent_cli` と提供 CLI が交わる（空なら不問）。CLI ごとに `can_accept`
  - `workspace` があれば担当リポジトリと `url` が一致し、mode が write なら書き込みを受けている
  - `requires.contract_version` を満たす
  - 同時数に空きがある。今日の上限（件数・秒・トークン）を超えていない
  - **依頼者 1 人あたりの上限**（設定。既定 5 件/日）をその依頼者がまだ超えていない
  - 自分の投函ではない（自分の依頼は自分で拾わない。手元で走らせればよい）
- 一度に入札するのは空きの数だけ。入札を書いたら 10 秒待って勝者を再計算し、勝っていれば実行。
  負けていれば何もしない（入札は残り、勝者の lease が切れれば繰り上がりうる）。

## 7. 利用枠の勘定

- **単位は 3 つ。件数は常に、秒は常に、トークンは計測できたときだけ。**
  - 件数と秒は参加者が自分で測る。
  - トークンは CLI の申告から読む。`session_log.usage: true` の CLI（claude / codex）はセッション
    ログの usage を、agent-herd 系は stderr の `@agent-usage tokens_in= tokens_out=` を読む
    （`agentcore.agentcli.parse_usage` と同じ印）。読めなければ null（推定値は書かない。
    node-budget の「台帳には事実のみ」と同じ）。
- **台帳**は参加者の `userData/share/ledger/<YYYYMMDD>.jsonl`（追記専用・UTC 日付）。1 行 = 1 件:
  `{id, posted_by, cli, model, mode, started_at, seconds, tokens_in, tokens_out, status, error_class}`。
  agent-tools の node-budget（`$AGENT_BUDGET_DIR/ledger`）があれば同じ行を `workload: turn` で
  そこにも書き、agent-project / agent-flow の予算と同じ枠で数えられるようにする（任意）。
- **上限**は参加者の設定: 1 日の件数（既定 20）、秒（既定 0 = 無制限）、トークン（既定 0）、
  依頼者 1 人あたりの件数（既定 5）。0 は無制限。日付は UTC で切り替える（node-budget と同じ）。
- **枠切れの学習**: CLI が定義の `errors` で `class: quota` に当たる出力を返したら、
  `quota_kind: exhausted` はその CLI をその日の残り受けない、`rate_limit` は 10 分受けない。
  板の `nodes/<me>.json` に CLI ごとの `can_accept: false` と `reason_codes` を出すので、依頼者にも
  「今は claude が空いていない」と見える。
- **射影**: `nodes/<me>.json` の `budget` は既存の `node-budget-summary` 契約のまま
  （`capacity` は計測できなければ null。判断は `can_accept` と `reason_codes`）。CLI ごとの内訳は
  additive の `clis` に置く（§11）。他の参加者は `can_accept` を再計算しない。

## 8. 転送と同期

### 8.1 板の種類

| 種類 | 設定値 | 同期 | 位置づけ |
|---|---|---|---|
| Syncthing フォルダ | フォルダ ID + 紹介者のデバイス ID | Syncthing が LAN で全対全に複製 | **既定**（§2.1） |
| 共有フォルダ（SMB / NFS） | 絶対パス | 無し。ファイルを直接読み書き | 1 拠点・試用 |
| git リポジトリ | `git+ssh://forge/team/agent-board.git` | agent-app が clone し、巡回で pull / push（§8.6 の旧案） | LAN で結べない拠点を forge 経由で結ぶ代替 |

### 8.2 書き込みの規律（Syncthing 板）

1. 書くのは自分名義のパスだけ。他人のパスは読むだけ。
2. tmp に書いて rename する。`.stignore` に `*.tmp.*` を置き、書きかけを他の PC へ流さない。
3. 削除も自分名義だけ。Syncthing は削除を伝える。
4. 衝突は `result.json` の投機実行でしか起きず、Syncthing の解決（新しい方を残す）に任せる。
   `.sync-conflict-*` を見つけた PC はそれを消す（中身は局所ログに残す）。
5. 板フォルダの Syncthing 設定: `fsWatcherDelayS: 1`（既定 10 秒を縮める）、versioning 無し、
   `ignorePerms: true`。global discovery と relay は無効。

### 8.3 待ち時間の見積もり

投函 → 複製（数秒）→ 入札 → 10 秒待ち → 実行 → `result` の複製（数秒）→ 依頼者が見る。
実行時間を除く上乗せは 15 秒前後。ローカルで自分の CLI を起こす数秒とは違う経路だと
利用者に分かるよう、会話の進捗に「列に並べた（前に n 件）」「引受 pc-b」を出す。

### 8.4 Windows

Syncthing は Windows 版を Windows 側で動かし、板フォルダは Windows のファイルシステムに置く
（agent-app は Node の fs で直接読み書きする）。git と CLI は従来どおり WSL 側で、worktree
`share-<id>` と scratch は WSL 側のホームに置く。板と作業ツリーは別のファイルシステムでよい
（板には作業ツリーのパスを書かない）。

### 8.5 板の掃除

- 依頼者は自分の終端した依頼を 7 日で消す（`delegations/<id>/` は依頼者のパス）。
- 参加者は自分の失効した入札と、終端した依頼の自分名義 `status` を消す。
- Syncthing は履歴を持たない（versioning 無し）ので、消せば全 PC から消える。会話も成果も板には残さない。

### 8.6 LAN の P2P で板を運ぶ候補の比較（2026-09-11。Syncthing を採用）

板の契約（自分名義のファイルだけ書く・状態はファイルの有無から導く・lease で失効）は転送に
依存しない。git を外すなら、差し替えるのは `board.js` の同期部分だけで、`queue.js` /
`participant.js` / `requester.js` は変わらない。forge に残るのは write の成果ブランチの push だけ
（件数は落札ごとに 1 回で、巡回の pull / push とは桁が違う）。

| 候補 | 担うもの | LAN での発見 | 同期モデル | agent-app との接続 | 各 PC に入れるもの | 適合 |
|---|---|---|---|---|---|---|
| **Syncthing** | フォルダ同期（板 = 同期フォルダ） | UDP ブロードキャスト。サブネットを跨ぐなら静的アドレスか小さな discovery サーバ | 全員が全ファイルを複製。所有パス分割なので衝突しない | REST（設定・フォルダ・デバイス）と `/rest/events` の long-poll をサイドカーとして叩く | Syncthing 本体（winget / brew / apt） | ◎ 板の設計を変えない。「共有フォルダ板」のまま LAN 越しになる |
| **Hypercore + Hyperswarm**（Holepunch） | ノードごとの追記ログ + 接続 | DHT。公開 bootstrap を使わないなら LAN に bootstrap ノード 1 台（`hyperdht` の bootstrapper）。現行版に mDNS は無い | 各ノードの core を全員が複製（= 名義分割そのもの） | 純 Node。Electron main に同居 | npm だけ（+ bootstrap 1 台） | ○ 板を「1 ノード 1 core」に写す変換が要る。サイドカー無し |
| **iroh**（`@number0/iroh`） | 接続（QUIC）+ blobs + gossip | Rust 側に mDNS。JS の露出は要確認。既定は relay + 公開 discovery | docs（多書き手 KV）は JS 未提供 → 同期層を自前で書く | napi（Node 20+、Windows/mac/Linux のビルド済み） | npm だけ | △ 1.0（2026-06）で土台は堅いが、同期層が自前になる |
| **js-libp2p（+ OrbitDB）** | 接続 + pubsub（+ Merkle-CRDT ログ DB） | `@libp2p/mdns` ◎ | OrbitDB のイベントログを全員が複製 | 純 Node。Helia（IPFS）まで載るので重い | npm だけ | ○ 発見は最も素直。依存が太い |
| **Zenoh** | pub/sub/query + storage、peer モード | multicast scouting ◎（224.0.0.224:7446） | storage プラグイン | `zenoh-ts` はルータ経由なので `zenohd` をサイドカーに | zenohd | △ ロボット系で実績。JS からは一段挟む |
| **Radicle**（heartwood） | git 自体を P2P に | seed / peer をアドレスで指定 | git リポジトリを各ノードが seeding | git remote を自分の radicle-node に向けるだけ。既存の git 板コードが生きる | radicle-node（Linux / macOS。Windows は WSL） | ○ 契約も実装も最小差分。ただしノードと鍵の運用が増える |
| **Secure Scuttlebutt** | 身元ごとの追記フィード + LAN 発見 | UDP ブロードキャスト ◎ | 全フィード複製 | `ssb-db2`（Node） | npm だけ | △ モデルの参照として最良。生態系は静か |

読み方:

- **板の設計を守って新規コードを最小にするなら Syncthing。** agent-app は「同期フォルダを
  読み書きし、events で変化を知る」だけになり、初案の git 同期の規律（pull --rebase・衝突時の捨て直し）は丸ごと消える。
  待ちも Syncthing の fs 監視（既定 10 秒）で巡回間隔より短い。
- **サイドカーを置かず agent-app の中で閉じたいなら Hypercore + Hyperswarm。** 板の
  「名義分割」と Hypercore の「1 書き手 1 ログ」が同型なので、写像は素直。LAN に bootstrap 1 台
  （常時稼働の小さなプロセス）が要る点だけが「中央」に見える。
- 発見（誰がいるか）と複製（何を持つか）は別の関心。libp2p / iroh は発見が強く複製は自前、
  Syncthing / Hypercore / SSB は複製が本体。板に要るのは複製の方。
- 会社のネットワークはサブネットや無線の client isolation で multicast / broadcast が届かない
  ことがある。どの候補でも「静的な peer 一覧」を設定に持てるようにしておく。

### 8.7 LAN 限定ならもう一段簡単にできる: 板を持たない直結（2026-09-11 追記。**2026-09-12 に採用・実装**）

同一サブネットに限定してよいなら、板（複製されるフォルダ）そのものを無くせる。鍵は
**依頼の持ち主（依頼者の agent-app）が、その依頼の調停役になる**こと。依頼ごとに調停役が
1 つに決まるので、分散 claim・lease の同期・衝突解決・Syncthing の導入と紹介者が要らない。

```text
発見（どれか 1 つ通れば全員につながる。UDP が塞がれていても TCP だけで動く）
  1. 静的な仲間   設定の「仲間の PC」へ 30 秒ごとに POST /hello（TCP）
  2. ゴシップ     /hello の返事に「相手が知っている仲間」が載る
  3. UDP 47800    ブロードキャストの HELLO / NEW（任意）
HTTP（各 agent-app が LAN 向けに 1 ポート（既定 47801）。Node 標準 http。共有の合言葉をヘッダで照合）
  POST /hello                    POST /notify（NEW）
  GET  /node                     GET /requests（自分が投函した open / working）
  POST /requests/<id>/claim      依頼者が先着 1 件だけ 200、以後 409（勝者は必ず 1 人）
  POST /requests/<id>/heartbeat  30 秒ごと。90 秒途絶で依頼者が open に戻す
  POST /requests/<id>/result     答え。依頼者が会話に保存
  GET  /requests/<id>/attachments/<name>    POST /requests/<id>/cancel
```

- 参加者は HELLO で知った全員の `/requests` を集めて §6 の鍵で並べ、上から claim を打ち、最初に
  200 が返った 1 件を実行する。依頼者の「今日の落札数」は依頼者が自分で数えて添える。
- 消えるもの: Syncthing・板のファイル契約・lease の同期・`.sync-conflict`・全員が全ファイルを持つ複製。
  残るもの: 資格判定・上限・quota 学習・台帳（自分の分）・本文合成・答えの保存・共有画面
  （全員の `/node` と `/requests` を集めて描く）。
- 引き換え: 依頼者が落ちるとその依頼は消える（受け取る相手がいないので実害なし）。答えは執行者が
  依頼者へ直送し、不在なら手元に持って HELLO が戻るまで再送（24 時間）。横断の履歴は無い。
  サブネットを跨がない。Windows は初回の待ち受けでファイアウォールの許可が出る。
- forge も外せる: write の成果を `git bundle` にして同じ HTTP で依頼者へ直送し、`git fetch <bundle>`
  で取り込む。参加者が基準コミットを持たないときは逆向きに依頼者が bundle を出す。
- 新しい依存は無い（`dgram` と `http`）。`src/main/share/` は `lan.js`（HELLO / NEW と peer 表）、
  `server.js`（HTTP）、`requester.js`（自分の依頼の列と claim の調停、心拍の監視）、
  `participant.js`、`queue.js`、`ledger.js` になり、`board.js` / `syncthing.js` は無くなる。

## 9. 障害と回復

| 起きること | 検知 | 回復 |
|---|---|---|
| 参加者が実行中に落ちる | `status` / `bids` の lease 失効（900 秒） | open に戻り他の参加者が拾う。落ちた参加者は再起動時に自分名義の `status: working` を見て、lease が切れていれば残骸（worktree `share-<id>`・scratch）を消す。切れていなければ最初からやり直す |
| 依頼者が落ちる | 何も要らない | 答えは板に残る。再起動時に `share.id` を持ち答えの無い会話を拾って監視へ戻す |
| 自分の Syncthing が止まっている | REST に応答が無い | 参加を止め、画面に「Syncthing に接続できない」。投函は断る（届かない依頼を作らない） |
| 誰ともつながっていない（孤立） | `/rest/system/connections` が 0 台 | 板は書けるが届かない。投函は「今は誰にも届かない」と 1 行出して受け付け、つながったら複製される |
| 紹介者が落ちている | 保留デバイスが受理されない | 新規参加だけが待つ。参加済みの PC には影響しない |
| CLI が失敗（非 0・空応答・認証切れ） | 参加者の実行終了 | `result: failed` + `error_class`。quota は参加者が学習（§7）。依頼者は quota / transient なら自動で 1 回再投函、他は人に見せる |
| 2 台が同時に走る | `result.json` の push 衝突 | 先に push した側が成果。負けた側は CLI を止めて捨てる（§4） |
| 参加者の時計がずれる | 無し | `ts` のずれは勝敗にしか効かない。lease は 900 秒あり数分のずれを吸収する。NTP を前提に置く |
| 書き込み成果の push 失敗 | 参加者の push 失敗 | 成果を残し `publish_failed`。参加者の画面から手で push して `result` を書き直せる（§5.2） |
| 依頼者の作業フォルダが進んで早送りできない | `merge --ff-only` の失敗 | 別 worktree で開く道へ倒す（§5.2） |

## 10. 安全と秘匿

- **信頼境界は板の参加資格**（forge の push 権限）。同じ板 = 同じ組織の同僚、と置く。アプリ側で
  依頼を審査しない。
- **依頼文と答えは板の参加者全員に見える。** 隠すと仕事が渡らない。設定画面と初回の投函で
  1 行で言う。秘密（鍵・個人情報）を依頼に書かないのは依頼者の責任。
- **参加者の PC を守るもの**: 読み取りは `readonly_args` + 一時 worktree（外へ出ない）。書き込みは
  受けるかを参加者が選び、受けても登録リポジトリの `share/<id>` worktree の中だけ。CLI のセッションは
  残さない。参加者の共通指示・スキルは他人の依頼に混ぜない。
- **板に載せないもの**: 資格情報、参加者の絶対パス（答えに混ざった参加者のホームパスは書き出し前に
  `~` へ置換）、CLI のセッションログ、依頼者の userData の添付（1 MB までの明示添付を除く）。
- **書き込み依頼の CLI は自動承認で動く。** 依頼文がそのまま実行指示になるので、参加者は
  「書き込みを受ける」を自分で ON にする。既定 OFF。

## 11. データ契約（既存契約への additive 追加）

### 11.1 公示 `delegations/<id>/post.json`（`delegation.schema.json` の `op: post`）

```jsonc
{
  "op": "post", "version": 1, "id": "dg-20260911093000-7f3a",
  "workload": "turn",                                   // 語彙に turn を追加
  "title": "依頼の 1 行目（60 字まで）",
  "goal": "合成した本文（§5.3）",
  "requires": { "agent_cli": ["claude"], "contract_version": 1 },   // agent_cli 空 = どれでも
  "workspace": { "url": "git@forge:team/app.git", "base": "main", "base_commit": "abc123" },  // 任意
  "priority": "normal",                                 // high | normal | low
  "turn": { "mode": "read", "model": "" },              // mode: read | write
  "attachments": ["spec.md"],                           // delegations/<id>/attachments/ の名前
  "retry_of": "",                                       // 再投函なら元の id
  "posted_by": "nitto.mbp", "posted_at": "2026-09-11T00:30:00Z"
}
```

### 11.2 参加者 `nodes/<node-id>.json`（`board.schema.json` の `node`）

```jsonc
{
  "node": "nitto.mbp", "workloads": ["turn"], "modes": ["read"],   // modes: 受ける mode
  "agent_cli": ["claude", "codex"],
  "repos": [{ "url": "git@forge:team/app.git" }],
  "max_concurrent": 1, "heartbeat": "…", "fresh_after_sec": 90, "contract_version": 1,
  "budget": { "contract_version": 1, "observed_at": "…", "source": "local-ledger",
              "capacity": { "limit": null, "used": null, "reserved": null },
              "can_accept": true, "reason_codes": ["ok"] },
  "clis": { "claude": { "can_accept": true,  "reason_codes": ["ok"],       "today": 3 },
            "codex":  { "can_accept": false, "reason_codes": ["exceeded"], "today": 20 } },
  "turns": { "today": 23, "cap": 40, "inflight": 0, "per_requester_cap": 5 }
}
```

### 11.3 入札・状態・成果

`bids/<who>.json` / `status/<who>.json` は既存のまま（`workload: turn`、lease 900 秒）。`result.json`:

```jsonc
{ "winner": "pc-b", "resolved_by": "pc-b", "resolved_at": "…", "status": "done",
  "answer": "答えの本文（200 KB まで。超えたら末尾を切り error に記す）",
  "agent_cli": "claude", "model": "", "elapsed_ms": 42000,
  "usage": { "tokens_in": 12000, "tokens_out": 800 },   // 計測できたときだけ
  "branch": "share/dg-…", "commit": "def456", "base_commit": "abc123", "files_changed": 3,  // write のとき
  "error": "", "error_class": "" }                      // quota | transient | publish_failed | cli | ''
```

### 11.4 agent-app 側

- 設定 `config.json` の `share`: `transport`（`syncthing` | `dir` | `git`）、`syncthing: { apiUrl, folderId,
  introducer }`（API キーは Syncthing の `config.xml` から読む）、`node`、`participate`、`clis`、
  `acceptWrite`、`maxConcurrent`、`dailyCap`、`dailySeconds`、`dailyTokens`、`perRequesterDailyCap`。
- 会話 `sessions/<id>.json`: 利用者メッセージに `transport: 'board'` と `share: { id, board }`。
  答えのメッセージに `share: { id, node, cli, elapsedMs, branch }`。
- 台帳 `userData/share/ledger/<YYYYMMDD>.jsonl`（§7）。

## 12. 実装の置き場（範囲の段階分けは別途）

| 部品 | 責務 | 再利用するもの |
|---|---|---|
| `src/main/share/board.js` | 板フォルダの読み書き（tmp → rename）、`toView`、勝者判定、`.sync-conflict-*` の掃除 | agent-dashboard `board-adapter.js` の読み側をそのまま写す（同じ仕様・別実装。fixture を共有して同じ出力を保証） |
| `src/main/share/syncthing.js` | REST（デバイス・フォルダの設定、接続状態、保留デバイスの受理）と `/rest/events` の long-poll。API キーの自動検出 | Syncthing の REST 契約。テストは疑似サーバ |
| `src/main/share/queue.js` | 並び鍵・実効優先度・資格判定・依頼者の今日の件数（純関数） | なし。テストはここに集める |
| `src/main/share/participant.js` | 巡回、入札、実行、報告、lease、残骸の掃除 | `agentCli.turnCmd`、`runHeadless` から起動と収集を切り出した `runPrompt`、`worktree.create/remove` |
| `src/main/share/requester.js` | 本文の合成、投函、監視、答えの保存、自動再投函、取り込み | `runTurn` の本文合成、`store.appendMessage`、`worktree` |
| `src/main/share/ledger.js` | 台帳、上限、quota の学習、`can_accept` | `agentCli.classifyError`、定義の `session_log` |
| `settings.js` / `store.js` | `share` の正規化、会話の `share` キー | 既存の正規化の形 |
| `ipc.js` / `preload.js` | `share:*` チャネル、`runTurn` の `transport: 'board'` 分岐 | 既存 envelope |
| 契約 | `workload: turn`、`priority`、`turn`、`modes`、`clis`、`turns`、`answer` 群 | `schemas/board.schema.json`、`schemas/delegation.schema.json`、`tools/agent-board/README.md` |

テストの軸: (a) 板の fixture で相と勝者が dashboard と同じ、(b) 並び鍵と資格判定、(c) ローカル
フォルダの板で 2 ノードを回して投函 → 入札 → 実行（疑似 CLI）→ 答えが会話に載る、(d) 衝突時に
捨てて書き直す、(e) write の worktree → push → ff 取り込み（ローカル bare リポジトリ）。

## 13. 却下した案

| 案 | 却下の理由 |
|---|---|
| 共有 API キー / 中央プロキシ | 契約と監査で成り立たない。「誰の枠か」が消える |
| 独自のキューサーバ（NATS / Redis / 常駐 HTTP） | 真実の置き場が二重になる。板の原則（ファイルが真実・中央は転送）と衝突。2026-07-23 設計 §9 と同じ結論 |
| agent-loop / agent-flow のデーモンに `turn` を足す | Python 一式の導入が前提になり、agent-app が単体で閉じる方針（ADR-11）に反する |
| `workload: flow` で会話を受ける | 分解と計画が挟まり、1 往復に重い。agent-flow が必要 |
| `agent-loop msg` で点対点に投げる | 宛先を人が選ぶ。キューと公平さが無い |
| 中央で割り当てる（スケジューラ） | 板の原則と衝突。落札は各参加者が同じ規則で導く |
| git リポジトリを板にして巡回で pull / push する（初案） | 参加者数 × 巡回のたびに forge を叩く。LAN の外の拠点を結ぶ代替としてだけ残す（§8.1） |
| 依頼者がノードを指名する（owner-picks） | 「空いている人が拾う」に要らない。契約は残るので後から足せる |
| 参加者ごとの台帳で公平さを決める | 参加者ごとに違う答えになる。板の `result.json` から数えれば全員同じ |
| トークンを推定して上限に使う | 推定値を台帳に書かない（node-budget と同じ）。件数と秒は常に測れる |

## 14. 未決事項（後で決める）

- 範囲の段階分け（read だけ先か、write まで一度にか）。
- 紹介者を誰にするか（常時稼働の PC か、参加者の誰か 2 台か）。サブネットを跨ぐ拠点の結び方（静的アドレスか `stdiscosrv`）。
- 各上限の既定値（件数 20・依頼者あたり 5・lease 900 秒は仮）。
- トークン計測の対応範囲（claude / codex のセッションログ、herd の stderr 印。copilot / kiro は件数のみ）。
- node-budget 台帳への併記を既定にするか。
- agent-herd（ollama）を持つ参加者が費用 0 の CLI として `herd` を提供する形（節約の依頼を先に
  ローカル LLM へ回す道。提供 CLI の 1 つとして自然に載るが、品質の期待値を依頼者に伝える手段が要る）。
- 画面（付録 A は旧案。仕組みが固まってから借りる形を決め直す）。

## 15. 関連

- [委譲公示板（agent-board）設計](./2026-07-23-delegation-board-distributed-bidding-design.md)
- [分散クレジット協調とプロジェクト知識循環 実装計画](./2026-07-29-agent-tools-distributed-credit-knowledge-plan.md)（`node-budget-summary`）
- [agent-flow リモート公開保証と緊急復旧](./2026-08-15-agent-flow-remote-publication-recovery-design.md)（`publish_failed` の扱い）
- [agent-app: agent-tools 無しで動く本体](./2026-09-09-agent-app-standalone-and-herd-benefits-design.md)（ADR-11）
- [`docs/designs/agent-app-design.md`](../designs/agent-app-design.md) / [`docs/specs/agent-app-spec.md`](../specs/agent-app-spec.md)

---

## 付録 A. 画面の初案（未承認・仕組みの確定後に見直す）

共有画面（借りる形: タスク概要の `.execution-card` の並び）:

```text
┌ サイドバー ─────────┐┌ 共有 ───────────────────────────────────────────────────────────┐
│  会話 / タスク /    ││ 共有                                                              │
│  ワークフロー       ││ 参加者の AI に依頼を回します                                      │
│ ▶共有               ││ ┌ このPCの提供 ──────────────────────────────────────────────┐ │
│                     ││ │ nitto.mbp                                            受付中 │ │
│ リポジトリ [sandbox]││ │ claude, codex · 今日 3/20 件 · 同時 1 · 書き込み 受けない    │ │
│                     ││ │                              [参加を止める] [設定]        │ │
│ 依頼            (＋)││ └─────────────────────────────────────────────────────────────┘ │
│ ● 実行中            ││ ┌ 参加者 ───────────────────────────────────────────────────┐ │
│   ログ設計をレビュー ││ │ pc-b     claude          受付中   今日 5 件  実行中 1        │ │
│   suzuki · pc-b     ││ │ pc-c     codex, kiro     上限     今日 20 件 実行中 0        │ │
│ ○ 順番待ち          ││ │ pc-d     claude          不在     最終 09:12               │ │
│   移行手順の要約     ││ └─────────────────────────────────────────────────────────────┘ │
│   nitto · 高        ││ ┌ ログ設計をレビュー ────────────────────────────────────────┐ │
│ ✓ 完了              ││ │ suzuki.win · 09:31 に投函 · 優先度 通常              実行中 │ │
│   テスト方針の相談   ││ │ 引受 pc-b · claude · 1 分 20 秒                              │ │
│   nitto · pc-c       ││ │ ▸ 依頼の本文                                                 │ │
│ 設定                ││ │                                     [優先度 ▾] [取り下げ]   │ │
└─────────────────────┘└──────────────────────────────────────────────────────────────────┘
```

会話の実行設定に起動方針「共有」を 1 択足し、共有のときだけ「エージェント: どれでも / claude …」と
「モード: 読み取り / 書き込み」を出す。待機中は送信ボタンの位置が「取り下げ」になる。
設定に「共有」タブ（板の場所・参加者名・参加する・提供 AI・書き込みを受ける・同時数・1 日の上限・
依頼者あたりの上限）。
