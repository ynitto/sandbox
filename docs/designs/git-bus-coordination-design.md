# git バス協調基盤 設計書（社内 GitLab 負荷からの脱却）

> 作成日: 2026-07-08
> 対象ブランチ: `claude/gitlab-load-alternatives-5jdlm2`
> 位置づけ: **イシュー／MR／レビューの分散協調を、社内 GitLab v4 REST から LAN 内 git バスへ移す**
> ための目標アーキテクチャと、既存スキル・ツールの**オプトイン改造**方針。
> 関連（直交レイヤ）:
> - clone コスト削減: [git-worktree-cache-pattern.md](git-worktree-cache-pattern.md)
> - アクセスの止め方・観測: [git-gitlab-circuit-breaker-pattern.md](git-gitlab-circuit-breaker-pattern.md)
> - ポーリング削減（フォールバック）: [kiro-loop-adaptive-interval-design.md](kiro-loop-adaptive-interval-design.md)
> - 同ホスト・ファイル inbox: [kiro-loop-agent-messaging-design.md](kiro-loop-agent-messaging-design.md)
> - 置き換え対象（実験停止）: [gitlab-agent-sns-design.md](gitlab-agent-sns-design.md)（Moltbook）

---

## 0. このドキュメントの使い方

「社内 GitLab に負荷をかけすぎ」というクレームの**根本原因は、pull 専用の REST API を
メッセージバスとして使っていること**（ポーリング × ノード数 × 頻度 = O(N·f) の常時負荷）。
本設計はそれを、**LAN 内の共有 git を協調バスにする**方式へ移す。

移植したい人は **§4 の不変条件**と **§5 の backend 契約**だけ守れば、§8 のチェックリストで
自分のスキル／ツールを新方式に載せられる。既定は**従来どおり（GitLab 直叩き）**で、
`GITLAB_BACKEND=gitbus` で初めて切り替わる（§4 INV-3）。

---

## 1. 背景・要件

### 現状の負荷源（測定済み）
- GitLab v4 に触れるファイル約 40、REST クライアント 12、MR 参照 26 ファイル。
- 実際に使っている v4 エンドポイントは狭い（issues / notes / merge_requests / discussions /
  repository/branches 等、実質 15〜20 種）。HTTP メソッド分布 GET 67 / POST 58 / PUT 26 / DELETE 8。
- 協調（タスク配布・レビュー引き渡し・在席ロック）を **issue の notes ポーリング**で実現しており、
  これが常時トラフィックの主因。gitlab-idd の `check-defer` / `check-review-defer` /
  `check-assigned-defer` はすべて **notes を全件取得して node-id タグと時刻で判定**している。

### 本設計が満たす要件（ユーザ確定事項）
1. **LAN 内の各 PC が、共有フォルダ or 同一 LAN 内特定 PC 上の git を共有バスとして使う。**
   イシュー・コード変更・レビューのやりとりをそこで行う。
   実データは git で運び、**イベント（更新通知）は別メッセージングでよい**。
2. **イシュー／MR 情報のマスターは作成者が管理**し、**完了次第 git を介して社内 GitLab へ上げる**。
3. **社内 GitLab を前提にしていたスキル・ツールは、新方式に対応できるオプトイン改造**を施す
   （既定は現状維持、切替は明示フラグ）。

### 非目標
- 社内 GitLab の完全廃止（コードの最終正本は社内 GitLab のまま）。
- CI の移設（CI は対象外。必要なら各ノードローカルで実行）。
- リアルタイム厳密同期（結果整合で十分。§4 INV-2）。

---

## 2. 中心アイデア

> **「実データ＝git」「イベント＝軽量通知」「1 オブジェクト 1 ライター」**

- **協調バス**: LAN 内に **bare git リポジトリを 1 つ**置く（特定 PC 上 or NAS 共有フォルダ）。
  これがイシュー／MR／レビュー／メッセージの substrate。各ノードは fetch/push でやりとりする。
  社内 GitLab には触れない → 負荷はゼロ。
- **データは git ファイル**: issue/MR/note/review を git 管理下の JSON ファイルとして表現。
  Moltbook の「hot=GitLab issue / cold=md」を、**すべて git ファイル**に一本化する。
- **1 オブジェクト 1 ライター（衝突しない書き込み）**: 各ノードは**自分の領域のパスにしか書かない**。
  同じファイルを 2 ノードが触らない → **git のマージ衝突が構造的に起きない**。
  「現在の状態」は全ノードのイベントファイルを**決定的に畳み込んで**得る（event sourcing）。
  これは ActivityPub の「各 Actor が自分の outbox を所有」を git で実現したもの。
- **イベントは push 通知**: git は pull 型なのでそのままだとポーリングが再燃する。
  そこでバスリポジトリの **`post-receive` フックで「更新が来た」通知だけを別チャネルで飛ばす**。
  ノードは通知を受けてから fetch する → **バスのポーリングもほぼゼロ**。
- **作成者がマスター**: issue/MR の正典（meta）はそれを作ったノードだけが書く。
  完了したら**コードだけ社内 GitLab へ push**（メタデータはローカル完結、必要なら後でエクスポート）。

---

## 3. アーキテクチャ

```
        LAN
  ┌───────────────────────── 共有 git バス（bare, 特定PC or 共有フォルダ）──────────────────────┐
  │  objects/issues/<id>/meta.json          … 作成者ノードだけが書く（単一ライター）           │
  │  objects/issues/<id>/events/<node>/*.ndjson … 各ノードは自分の <node>/ にのみ追記         │
  │  objects/mrs/<id>/{meta.json, events/<node>/...}                                          │
  │  refs/…（コード共有: feature ブランチをここで push/fetch し合う）                         │
  │  hooks/post-receive → 影響オブジェクトと宛先ノードを判定し「更新通知」を発火               │
  └──────────────────────────────────────────────────────────────────────────────────────────┘
      ▲ fetch (通知を受けてから)    │ push (自分の領域のみ)         │ 通知(別チャネル)
      │                            ▼                               ▼
  ┌────────────┐            ┌────────────┐               各ノードの inbox / pub-sub
  │  ノード A   │            │  ノード B   │      (a) 共有フォルダ inbox + InboxWatcher(既存)
  │ gl.py       │            │ gl.py       │      (b) NATS/Redis を LAN に1台（任意）
  │  backend=   │            │  backend=   │      (c) フォールバック: 適応間隔 fetch（既存設計）
  │  gitbus     │            │  gitbus     │
  └────┬───────┘            └─────┬──────┘
       │ 完了時のみ                │ 完了時のみ
       └──────────► 社内 GitLab ◄──┘   （コードの最終正本 / メタは任意で後日エクスポート）
```

### 3-1. データモデル（衝突しないレイアウト）

```
objects/
  issues/
    GK-7F3KQ9/                      # packet_id（既存 gl.py gen-packet-id を流用）
      meta.json                     # ← 作成者ノードのみが書く: title/author/creator_node_id/created_at
      events/
        <node_id_A>/0001.ndjson     # ← ノード A だけが append: note / label-op / claim / state-req / review
        <node_id_B>/0001.ndjson     # ← ノード B だけが append
  mrs/
    GK-9QER22/
      meta.json                     # source_branch / target_branch / author / created_at
      events/<node_id>/*.ndjson     # note / discussion / resolve / review-verdict / merge-req
```

- **パスの所有者は 1 ノードに固定**。`meta.json` は作成者、`events/<node>/` はその node のみ。
  → 2 ノードが同一パスを書かない → **push が衝突しない**（fast-forward し合うだけ）。
- **イベント 1 行 = 1 操作**（NDJSON, 追記専用）。例:
  ```json
  {"ts":"2026-07-08T09:00:00Z","node":"a1b2c3","op":"note","body":"…"}
  {"ts":"2026-07-08T09:01:00Z","node":"a1b2c3","op":"label","add":["status::doing"],"remove":["status::todo"]}
  {"ts":"2026-07-08T09:01:00Z","node":"a1b2c3","op":"claim","role":"worker"}
  ```
- **現在状態 = 畳み込み**: 全 `events/*/*.ndjson` を `(ts, node)` で安定ソートし、決定的に reduce。
  - labels: add/remove 集合演算（gl.py の update-issue が既に持つセマンティクスと一致）。
  - state: last-writer-wins（close/reopen）。ただし meta の author を尊重（§4 INV-1）。
  - 時刻は wall-clock。厳密順序が要る箇所は Lamport カウンタを併記可（v2 拡張）。

### 3-2. 排他取得（claim / defer）— 既存ロジックの写像

gitlab-idd の defer 群は「notes に埋めた node-id と時刻」で判定している。
git バスでは **claim イベント**がその notes を置き換える。ロジックはほぼ 1:1 で移植できる:

- worker は取得時に自分の `events/<node>/` に `{"op":"claim","role":"worker"}` を append→push。
- 競合（同時 claim）: fetch 後に両者が両方の claim を見る → **`(ts, node_id)` で決定的アービトレーション**
  （最小が勝ち、敗者はバックオフ）。これは楽観ロック＋決定的解決。
- 既存の lock 窓（`assigned_lock_minutes`=24h 等）が「誰かが作業中」の TTL としてそのまま機能する。
- `check-defer`（自作 issue の冷却）/ `check-review-defer`（自己レビュー抑止）/
  `check-non-requester-review-defer` は**同じ node-id + 時刻の判定**なので、入力を notes から
  claim/review イベントに差し替えるだけ。

### 3-3. イベント通知（ポーリング撲滅）

- バス bare repo の **`post-receive` フック**（＝バス PC 上で push 受信時に走る）が:
  1. 受信 ref の差分から**影響オブジェクトと宛先ノード**を判定（例: assignee / follower / MR reviewer）。
  2. **更新通知だけ**を別チャネルへ発火（本文は運ばない。「GK-7F3KQ9 が更新された、fetch せよ」）。
- 通知トランスポートは**プラガブル**（要件どおり「イベントは別メッセージングでよい」）:
  - (a) **共有フォルダ inbox**: 各ノードの inbox に 1 ファイル書く →
    既存 [kiro-loop-messaging](kiro-loop-agent-messaging-design.md) の `InboxWatcher` が拾う（同ホスト前提を
    LAN 共有フォルダに広げるだけ。追加ミドルウェア不要で最小）。
  - (b) **NATS / Redis を LAN に 1 台**: subject/channel = ノード or ラベル。push 配送で完全非ポーリング。
  - (c) **フォールバック**: 通知基盤が無い環境は、既存の
    [適応間隔 fetch](kiro-loop-adaptive-interval-design.md)（AIMD）でバスを間欠 fetch。
    社内 GitLab ではなく**LAN 内バス**を叩くので、負荷は社外に出ない。
- 結果: 平常時は **push→フック→通知→対象ノードだけが fetch** の一直線。空回りポーリングが消える。

### 3-4. コード共有と社内 GitLab への同期

- **開発中**: feature ブランチはバス（追加 remote `bus`）へ push/fetch し合う。LAN 内なので高速・無負荷。
- **完了時**: 作成者（またはマージ担当）ノードが**コードだけ社内 GitLab へ push**（`origin`）。
  → 社内 GitLab は**コードの最終正本**として維持。負荷は「完了時の 1 回」に圧縮される。
- **メタデータ（issue/MR/review）**: 基本ローカル完結。必要なら後日エクスポート（§6）。

---

## 4. 不変条件（移植時に必ず守る）

### INV-1: 1 オブジェクト 1 ライター（衝突しない書き込み）
- 各ノードは **`meta.json`（自作分のみ）と `events/<自ノード>/` にしか書かない**。
- 他ノードのイベントや他者の meta を**上書きしない**。状態変更は**自分のイベント追記**で表現し、
  読み手が畳み込む。これが成立する限り git は衝突せず、単なる fast-forward マージになる。
- state/label のような「共有可変」項目も、**イベント（label-op / state-req）として自領域に書く**。
  最終値は決定的 reduce（LWW / 集合演算）で得る。作成者の meta 権限を reduce が尊重する。

### INV-2: 結果整合・best-effort・本処理を止めない
- 時刻は wall-clock（プロセス／マシンを跨ぐため monotonic 不可）。厳密順序は Lamport 併記で補強（v2）。
- 通知の取りこぼし・重複は前提（at-least-once）。**通知はヒントに過ぎず**、真実は常にバスの git 内容。
  取りこぼしても次の fetch で収束する。イベントは**冪等**に設計（同 op 二重適用が無害）。
- 通知チャネル障害・フック失敗はフォールバック（適応間隔 fetch）へ縮退し、**協調は止まらない**。

### INV-3: 既定は現状維持・オプトイン切替（gitguard と同じ思想）
- **既定 `GITLAB_BACKEND=gitlab`** = 従来どおり社内 GitLab 直叩き。挙動を一切変えない。
- **`GITLAB_BACKEND=gitbus`** で初めてバス方式に切替。段階採用: まず 1〜2 スキルで実運用 →
  問題なければ既定化を検討。
- backend が未実装コマンドに当たったら**明示エラー**で落とす（暗黙に GitLab へ漏れない）。
  緊急避難は `GITLAB_BACKEND=gitlab` に戻すだけ。

### INV-4: 出力互換（消費側を触らない）
- `gitbus` backend は **gl.py 各コマンドと同じ JSON 形状**を返す（issue は `iid`/`title`/`labels`/
  `author.username`/`created_at`/`description`、note は `body`/`created_at` 等）。
- kiro-flow executors・SKILL.md・viewer 群は**パースを変えずに動く**ことをゴールとする
  （契約テストで担保、§7）。

---

## 5. backend 契約（最小インターフェース）

差し込み点は **gl.py の CLI 境界**（既に全消費側がここを叩いている）。
`api()` / `api_list()` の下に **backend 層**を置き、コマンドディスパッチを分岐する。

```python
# gl.py（擬似コード）
BACKEND = os.environ.get("GITLAB_BACKEND", "gitlab")   # 既定は現状維持（INV-3）

def dispatch(command, args, ctx):
    if BACKEND == "gitbus":
        return gitbus_backend.handle(command, args, ctx)   # 新: 共有 git バス
    return gitlab_backend.handle(command, args, ctx)       # 既存: api()/api_list()（無改変）
```

`gitbus_backend` が実装すべき最小コマンド（＝現行 gl.py の実使用面）:

| 種別 | コマンド | gitbus 実装の要点 |
|---|---|---|
| issue 読取 | `list-issues` / `get-issue` / `get-comments` | バスを fetch → objects を reduce → GitLab 互換 JSON |
| issue 書込 | `create-issue`（meta 作成）/ `update-issue`（label/state/desc イベント）/ `add-comment`（note イベント） | 自領域へ append → push |
| MR | `list-mrs` / `create-mr` / `update-mr` / `merge-mr` / `get-mr-changes` / `add-mr-comment` / `get-mr-discussions` / `resolve-mr-discussion` | meta + events。changes は `git diff` から算出（no clone 経路は worktree-cache 併用） |
| ブランチ | `make-branch-name` / `delete-branch` | 既存ロジック流用（branch はバス上で操作） |
| 協調 | `check-defer` / `check-review-defer` / `check-assigned-defer` / `check-non-requester-review-defer` | notes 判定を **claim/review イベント判定**へ差替（§3-2） |
| メタ | `project-info` / `get-default-branch` / `current-user` / `get-node-id` | current-user はローカル identity（node_id ベース）を返す |
| CI | `get-mr-pipeline` | CI 非対象 → 常に `{"status":"none"}` を返す（互換維持） |
| オフライン | `gen-packet-id` / `normalize-packet-id` | backend 非依存（現状のまま） |

- **gitguard 併用**: バスへの git アクセスも `gitguard.git()` 経由にできる（best-effort import）。
  LAN バス断・共有フォルダロック競合を fail-fast + 観測できる。
- **worktree-cache 併用**: `get-mr-changes` 等でバスを clone する箇所は共有ミラー＋worktree で圧縮。

---

## 6. 社内 GitLab との同期／後日エクスポート

- **コード（push mirror）**: 完了ブランチを `bus` → `origin`(社内 GitLab) へ push。
  最終正本は社内 GitLab。これだけが社外に出るトラフィック。
- **メタデータ（任意・後日）**: 対称性を利用したエクスポータを用意。
  バスの reduce 済み状態を、**`GITLAB_BACKEND=gitlab` の同じ gl.py コマンド**で GitLab に流し込む
  （create-issue → add-comment → update-issue …）。「協調は gitbus、書き戻しは gitlab」と backend を
  切り替えるだけで実装が再利用でき、二重メンテを避けられる。

---

## 7. 段階導入計画

1. **バス構築**: LAN の 1 PC に bare repo（`git init --bare`）または共有フォルダに配置。
   各ノードに remote `bus` を追加。まずは**通知なし（適応間隔 fetch, INV フォールバック）**で疎通確認。
2. **データ層 + reduce**: objects レイアウトと畳み込みを実装。契約テスト（GitLab 実レスポンス JSON を
   ゴールデンにして gitbus 出力と突合）で **INV-4 出力互換**を固定。
3. **gl.py backend 分岐**: `GITLAB_BACKEND` 導入。読取コマンドから載せ、書込→協調→MR の順に拡張。
4. **claim/defer 移植**: notes 判定を claim/review イベント判定に差替。決定的アービトレーション検証。
5. **通知**: post-receive フック + トランスポート (a) 共有フォルダ inbox（既存 InboxWatcher 再利用）。
   → 空回りポーリング消滅を確認。必要なら (b) NATS/Redis へ。
6. **1 スキルで実運用**（gitlab-idd 推奨）→ 問題なければ横展開。**既定切替は実データを見てから**。
7. **エクスポータ**（§6）を整備し、社内 GitLab への完了同期／後日メタ書き戻しを自動化。

---

## 8. 採用チェックリスト（他スキル／ツールへの移植手順）

1. [ ] そのツールの GitLab アクセスは **gl.py 経由か直叩きか**を確認。直叩きなら gl.py（or 同等の
       backend 層）に寄せてから載せる。
2. [ ] 使っている**コマンド／エンドポイントを列挙**し、§5 表の未カバーが無いか確認。
3. [ ] **INV-4**: 依存している JSON フィールドを洗い出し、gitbus 出力が同形状か契約テストで固定。
4. [ ] **INV-3**: `GITLAB_BACKEND` 未設定時に**従来と 1 bit も変わらない**ことを確認（回帰なし）。
5. [ ] 協調（ロック・在席・レビュー引き渡し）を notes 依存でやっていないか。していれば
       **claim/review イベント**へ写像（§3-2）。
6. [ ] 通知が要るか。要るなら post-receive フック対象に宛先算出を追加。要らなければ適応間隔で可。
7. [ ] gitguard / worktree-cache を**併用**（バス git アクセスの止め方・clone コスト）。
8. [ ] 段階採用: **監視・小規模実運用 → 実データでしきい値調整 → 既定化検討**。

---

## 9. 代替案との比較（なぜこの設計か）

| 案 | 社内GitLab負荷 | 既存コード改変 | 衝突/整合 | 追加インフラ | 採否 |
|----|------|------|------|------|------|
| **git バス + イベント通知（本案）** | ほぼ 0（完了時のみ）| gl.py に backend 1 枚（オプトイン）| 単一ライターで衝突なし | bare repo 1 + 通知(任意) | **採用** |
| ローカル GitLab CE を立てる | 0 | 0（v4 互換）| GitLab 任せ | 重い（Postgres/Redis/Sidekiq…）| 不採用（過剰）|
| Gitea/Forgejo をローカル | 0 | 大（v1≠v4, 全書換）| Gitea 任せ | 中 | 不採用（書換コスト）|
| ブローカー（NATS/Redis Streams）中心 | 0 | 中〜大（協調を全面移行）| ブローカーの ack/lease | 中（1台）| 併用候補（claim を厳密化したい時のみ）|
| 現状維持（issue notes ポーリング）| 高（クレーム源）| 0 | — | 0 | 不採用 |

**要点**: 消費側は既に **gl.py CLI 契約**に依存しているので、そこに backend を差すのが**最小改変で
最大効果**。実データは git、協調は単一ライター＋イベント畳み込み、通知だけ別チャネル、という
分割が「GitLab を bus として酷使する」構造そのものを解消する。厳密 exactly-once claim が硬要件に
なった場合のみ、claim 部分を NATS JetStream 等のブローカーに委ねる二層構成へ拡張できる（後方互換）。

---

## 10. 既知の制約・非目標

- **厳密 exactly-once はプロトコル外**: claim は楽観ロック＋決定的解決（結果整合）。
  「絶対に二重着手不可」が硬要件なら §9 のブローカー二層へ。
- **共有フォルダ bare repo の同時 push**: SMB/NFS のロック挙動に依存。堅牢性が要るなら
  **特定 PC 上の git over ssh / git-daemon** を推奨（単一ライター設計なので競合はそもそも稀）。
- **通知は跨ぎに弱いトランスポートを選ばない**: 共有フォルダ inbox / NATS は LAN 前提。
  拠点跨ぎが要るなら NATS leaf / HTTP push を検討（本設計の主眼は同一 LAN）。
- **CI は対象外**: `get-mr-pipeline` は `none` を返す互換スタブ。CI が要るノードはローカル実行。
- 認証情報は持たない（git credential / トークンは呼び出し側に従う。gitguard と同方針）。
```
