# 委譲公示板（agent-board）設計案 — 依頼の受付・入札・成果一本化を担う分散バックエンド

- 日付: 2026-07-23
- 状態: **推奨案 A を採用・P0 実装済み**（board 中核 ＋ 4 ツールの結合点。§10 実装フェーズ参照）
- 関連契約: [`schemas/delegation.schema.json`](../../schemas/delegation.schema.json)、
  [`schemas/repos.schema.json`](../../schemas/repos.schema.json)、
  [`schemas/node-budget.schema.json`](../../schemas/node-budget.schema.json)
- 依存する既存設計: [`agent-flow-design.md`](../designs/agent-flow-design.md)（claim プロトコル・Bus 抽象）、
  [`agent-amigos-design.md`](../designs/agent-amigos-design.md)（GitBus/HubBus・node 能力宣言・納品棚）、
  [`2026-07-19-delegation-contract-design.md`](./2026-07-19-delegation-contract-design.md)（委譲封筒）、
  [`selfhost-forge-comparison.md`](../designs/selfhost-forge-comparison.md)（セルフホスト forge の選定）

## 1. 要件

1. **依頼の受付と管理** — エージェント処理の依頼を受けて公示・追跡する。登録ノードへの
   **配信（push）またはノードからのポーリング**により入札を受け付ける。
2. **先勝ち入札 ＋ 投機同時実行の許容** — 入札は先勝ちで良い。複数ノードが同時に実行して
   しまうことは許容するが、**結果整合をとり、成果はひとつ**として管理する。
3. **成果物リポジトリによるノード側の選別** — 依頼に成果物リポジトリを含められる。
   **どのリポジトリの仕事を引き受けるかをノード側が選べる**機構を設ける。
4. **agent-flow / agent-amigos の分散処理の裏側** — 両エンジンの委譲バックエンドとして機能し、
   既存スキーマ（delegation / repos / mission / task）を踏襲する。

## 2. 結論（推奨案の要約）

**新規サーバは書かない。既存の「専用 git リポジトリ＝バス ＋ 決定的 claim」を
エンジン非依存の公示板（agent-board）として一段下に敷く**。

- 公示板の実体は **専用 git リポジトリ（board リポジトリ）**。ホスティングは既存のオンプレ
  forge（Gitea / Forgejo / GitLab CE / ssh bare repo）のどれでもよい —
  「セルフホスト OSS を持ってくる」部分は **forge に集約**し、調整ロジックは既存実装の流用に留める。
- 封筒は **`delegation.schema.json` をそのまま公示形式に昇格**する（v1 は dashboard IPC 契約
  だったものを、設計書 §D6 が予約していた「ファイルドロップ＝将来拡張」の形で板の上に置く）。
- 入札は **agent-flow / amigos と同一の名前空間付き claim ＋ `(ts, who)` 決定的タイブレーク**。
  先勝ちはこのプロトコルの既定動作そのもの。
- 配信は 3 段構え: **ポーリング（GitBus・既定）／ long-poll（実装済みの amigos hub 流用）／
  forge の Webhook（push 通知）**。どれを使っても調整結果は変わらない（転送層と協調ロジックの分離）。
- 投機同時実行は **成果報告を名義分割（`results/<who>.json`）**して受け、
  **一本化は「受入 or 決定的 first-valid」**で行う。成果はひとつ（`result.json`）に確定する。
- リポジトリ選別は **ノードの能力宣言に `repos.schema.json` のレジストリを載せ、
  公示の `workspace.url` と突き合わせる**だけで実現する（新概念を作らない）。
- **agent-board は処理を持たない（リポジトリ＋契約だけ）。入札・引き渡しは請負側の既存デーモン
  （agent-flow / agent-amigos）に畳み込む** — board 専用デーモン・専用ツールは作らない。
  各デーモンが板を巡回し、`workload` が自分向きの公示に入札して、勝てば自分のエンジンへ取り込む
  （flow: 自分の inbox 投函 / amigos: オーナーとして post）。dashboard の delegation アダプタと
  同じ変換をローカルで行う。

> **実装メモ（採用構成）**: 当初 §4 の図は「board デーモン」を各ノードに描いていたが、実装では
> **板を『リポジトリ＋契約』だけにし、入札ループを agent-flow / agent-amigos の既存デーモンへ
> 畳み込んだ**（`agent_flow/board.py` / `agent_amigos/board.py`。設定 `board:` を与えると常駐
> デーモンの巡回に board 参加が 1 ステップ加わる）。新しいデーモン・サーバは増やさない。claim は
> 各エンジンの既存 claim をそのまま板の `bids/` に適用する（同じ仕様・別実装）。以下の図の
> 「board デーモン」は「agent-flow / agent-amigos デーモンの board 参加ステップ」と読み替える。

外部ブローカー OSS（NATS / RabbitMQ / Temporal 等）を中核に据える案は §9 で比較の上、
**転送層の高速化オプション以上の採用はしない**（真実の置き場が二重になり、
「バス上のファイルが真実・中央は転送のみ」という既存原則と衝突するため）。

## 3. 位置づけ — 既存の何が足りないか

| 要件 | 既存資産 | 不足 |
|---|---|---|
| 依頼の受付・公示 | delegation 封筒（dashboard IPC）・flow `inbox/`・amigos `index/` | **エンジン非依存の公示板の実体が無い**（封筒は renderer→main の投函にしか使えず、flow/amigos それぞれのバスに直接届く） |
| 先勝ち入札 | claim プロトコル（両エンジンで同仕様・別実装） | 公示板レベルの入札が無い（エンジン内のタスク/ロール単位のみ） |
| push 配信 | HubBus の long-poll（amigos hub 実装済み） | 公示板への適用と Webhook 経路が未定義 |
| 投機同時実行＋成果一本化 | flow は「勝者のみ results を書く」＝投機を許さない設計 | **複数の成果報告を受けて一本化する機構が無い** |
| リポジトリ別のノード選別 | amigos `requires.tags`×node.yaml、repos レジストリ | **workspace.url とノード宣言の突き合わせが無い**（flow は誰でも claim できる） |
| 依頼の追跡・受入 | delegation view・amigos accept・納品棚 | 公示板単位のライフサイクル管理が無い |

つまり「エンジンの中の分散」は完成しているが、**「どのノードがこの依頼を引き受けるか」を
エンジン横断で決める層**が存在しない。agent-board はその層だけを追加する。

## 4. アーキテクチャ

```
                    ┌────────── board リポジトリ（オンプレ forge 上・専用）──────────┐
 依頼者: post ────▶ │ delegations/<id>/post.json     … 公示（delegation 封筒）        │
 (dashboard /       │ delegations/<id>/bids/<who>.json … 入札（名義分割 claim）        │
  CLI / スキル)     │ delegations/<id>/award.json      … owner-picks の落札（依頼者）  │
                    │ delegations/<id>/status/<who>.json … 実行ハートビート            │
 依頼者: accept ◀── │ delegations/<id>/results/<who>.json … 成果報告（投機なら複数）   │
                    │ delegations/<id>/result.json     … 一本化された成果（依頼者）    │
                    │ nodes/<node-id>.json             … ノード登録・能力宣言          │
                    └──▲──────────────▲──────────────▲───────────────────────────────┘
              pull/push│    pull/push │     webhook / long-poll │
     ┌─────────────────┴──┐  ┌────────┴─────────┐  ┌───────────┴────────┐
     │ node PC-A           │  │ node PC-B        │  │ node PC-C          │
     │ agent-flow デーモン  │  │ agent-amigos     │  │ flow/amigos デーモン│
     │ の board 参加ステップ │  │ デーモンの board  │  │  （repo 不一致なら   │
     │  └落札→ flow inbox  │  │ 参加ステップ       │  │    入札しない）      │
     │    へ投函（既存経路） │  │  └落札→ post     │  │                    │
     └─────────────────────┘  └──────────────────┘  └────────────────────┘
```

### 4.1 コア原則（既存からの継承）

- **真実は板の上のファイル**。状態はファイル存在から導出し、書き換え競合を作らない。
- **書き込み所有権をパス単位で分割**（§5.1 の表）。git でもコンフリクトしない。
- **中央（forge / hub）はただの転送・保管**。落札の決定・成果の一本化は各ノードが
  同じファイル集合から決定的に導く。中央が落ちても壊れず、回復後に同期が追いつくだけ。
- **毎晩シャットダウンする個人 PC を一級の前提**とする（amigos away プロトコルと同じ運用感）。

### 4.2 board リポジトリのレイアウトと所有権

```
main ブランチ（単一。会話が無く書き込み頻度が低いため、ミッション別ブランチ分離は不要）:
  nodes/<node-id>.json            # ノード登録（各ノードが自分名義のみ）
  delegations/<id>/
    post.json                     # delegation 封筒 op=post（依頼者のみ）
    bids/<who>.json               # 入札（各ノードが自分名義のみ）
    award.json                    # owner-picks の落札確定（依頼者のみ）
    status/<who>.json             # 実行状態＋ハートビート（実行ノードが自分名義のみ）
    results/<who>.json            # 成果報告（実行ノードが自分名義のみ）
    result.json                   # 確定成果（依頼者のみ。§7）
    cancelled.json                # 中止マーカー（依頼者のみ）
```

| パス | 書く人 |
|---|---|
| `post.json` / `award.json` / `result.json` / `cancelled.json` | 依頼者（公示したノード） |
| `nodes/<node-id>.json` / `bids/<who>.json` / `status/<who>.json` / `results/<who>.json` | 各ノードが自分名義の分だけ |

同期規律は amigos §5.1 と同一（間隔律速・`pull --rebase` リトライ・force push 禁止・
自パスのみステージ）。板のトラフィックはエンジン内バスより一桁少ない
（1 依頼につき数ファイル。会話・タスクグラフはエンジン側バスに残る）ため、
**ブランチ分離なしの単一 main で足りる**。将来詰まったら amigos と同じ
`delegation/<id>` ブランチ方式へ additive に移行できる。

### 4.3 公示封筒 — delegation.schema.json の踏襲と additive 拡張

`post.json` は **`delegation.schema.json` の `op: post` 封筒そのまま**。板のために足すのは
additive な 2 ブロックだけ（未知キー無視の前方互換規約に乗る）:

```jsonc
{
  "op": "post", "version": 1, "id": "dg-20260723120000-a1b2",
  "workload": "flow",                     // flow | amigos（既存語彙）
  "goal": "…", "title": "…", "design": "…",
  "workspace": {"url": "git@gitea.local:team/app.git", "base": "main"},  // repos.schema.json エントリ形
  "references": [ … ],
  "policy": {"assignment": "first-come"}, // first-come | owner-picks（既存）
  // ---- additive（板が解釈。エンジンは無視）----
  "requires": {                           // 入札資格（すべて任意・AND）
    "tags": ["python"],                   // amigos node.yaml と同じタグ語彙
    "agent_cli": ["codex", "claude"],     // ノードが使える CLI
    "repos": ["git@gitea.local:team/app.git"]  // 省略時は workspace.url が事実上の資格
  },
  "speculation": {                        // 投機同時実行（§7）。省略 = off
    "max_runners": 2,                     // 同時に走ってよいノード数（勝者含む）
    "resolve": "first-valid"              // first-valid | owner-picks
  }
}
```

- **`id` は板・flow req-id・amigos mission_id を貫く冪等キー**（delegation §D1 の決定そのまま）。
  再投函は同一公示（二重公示防止）。
- `workload: project / routine` は将来拡張（delegation §D2 と同じ additive 規約）。

## 5. ノード登録と入札

### 5.1 ノード登録 — repos レジストリを能力宣言に載せる

各ノードは `nodes/<node-id>.json` に能力を宣言する（amigos node.yaml ＋
agent-project マルチノード設計の `status/<node>.json` の合流形）:

```jsonc
{
  "node": "pc-a",
  "workloads": ["flow", "amigos"],         // 受けられるエンジン
  "tags": ["python", "frontend"],
  "agent_cli": ["claude", "codex"],
  "repos": {                               // ← repos.schema.json そのもの（担当宣言）
    "app":  {"url": "git@gitea.local:team/app.git",  "base": "main", "local": "/srv/mirror/app"},
    "docs": {"url": "git@gitea.local:team/docs.git", "readonly": true}
  },
  "availability": "09:00-21:00 Asia/Tokyo", // amigos away と同じ宣言
  "max_concurrent": 2,
  "heartbeat": "2026-07-23T03:00:00Z", "fresh_after_sec": 120
}
```

**リポジトリ選別はこの宣言と公示の突き合わせで実現する**（要件 3）:

- ノードの board デーモンは、公示の `workspace.url`（と `requires.repos`）が
  **自分の `repos` レジストリに居るときだけ入札する**。identity は repos スキーマの規約どおり
  `(url, path, base)` で照合し、`readonly: true` のエントリは書込先候補にしない。
- 逆方向の選別（依頼側がノードを縛る）は `requires.tags / agent_cli / repos` で表す。
  両方が成立した公示だけが入札対象になる。
- `local:` ミラーを持つノードは worktree 切り出しが速い。**入札の自己抑制**（ミラーが無い
  リポジトリは重い clone が要るので入札しない/遅らせる）はノード側ポリシーとして自由に足せる —
  勝者決定は先勝ちなので、「速いノードほど先に bid を書ける」ことが自然な負荷分散になる。
- node-budget 台帳（既存契約）が枯渇しているノードは入札を開始しない
  （amigos の paused と同じ扱い。板から見ると単に入札が来ないだけ）。

### 5.2 入札プロトコル — claim の流用（先勝ち）

要件 2 前段の「先勝ち」は、**flow / amigos と同一仕様の claim をそのまま使う**:

```
try_bid(id, who):
  1. sync_pull()
  2. result.json / cancelled.json があれば False（終端済み）
  3. 有効（lease 内）な bids の勝者が居て自分でなければ False
  4. bids/<who>.json を書く（ts, lease_until, agent_cli, 予定エンジン）
  5. sync_push() → sync_pull()
  6. 勝者 = 有効 bid のうち (ts, who) 最小。自分なら落札
```

- `assignment: first-come`（既定）は claim 勝者＝落札。`owner-picks` は bid ＝応募で、
  依頼者が `award.json` を書いた者だけが落札（amigos の apply→confirm と同型）。
- bid は lease 付き。落札ノードは `status/<who>.json` のハートビートで延長し、
  失効（クラッシュ）や `state: away`（計画停止）の扱いは amigos §6.5/§6.6 の規則を流用する。
- 全ノードが同じファイル集合から同じ勝者を導くため、**push が遅延しても最終的に全員が
  同じ結論に収束する**（要件 2 の「結果整合」の基盤）。

### 5.3 配信 — ポーリング／long-poll／Webhook の 3 段構え

| 経路 | 実体 | レイテンシ | 追加要素 |
|---|---|---|---|
| **ポーリング（既定）** | GitBus の間隔律速 fetch（30–60s） | 分オーダー | なし（最小構成） |
| **long-poll** | 実装済みの **amigos hub** を board のデータディレクトリに向けて流用（`GET /list?since=<rev>&wait=`） | 秒オーダー | hub プロセス 1 つ |
| **Webhook push** | forge（Gitea/Forgejo/GitLab）の **push webhook → ノードの board デーモンを即時 1 サイクル起こす** | 秒オーダー | forge の webhook 設定のみ |

重要なのは **3 経路とも「起きるタイミング」を変えるだけ**で、起きた後にやることは同一
（sync_pull → 突き合わせ → 入札判断）という点。Webhook が落ちてもポーリングが
フォールバックとして生きるので、push 配信は可用性要件を持たない「加速装置」に留まる。
NAT 裏で webhook を受けられないノードは long-poll かポーリングを使う。

## 6. エンジンへの引き渡し — 「裏側」としての動作（要件 4）

**落札が決めるのは「どのノードがこの依頼をホストするか」だけ**。以降はエンジンの既存機構が
そのまま動く。v1 でエンジンのコードは無変更:

| workload | 落札ノードのデーモンがやること | 以降 |
|---|---|---|
| `flow` | 封筒を `submit_request` 形式へ変換し、**自ノードの flow バスの `inbox/<id>.json` へ投函**（dashboard の flow-adapter と同じ変換） | 自ノードの flow daemon が orchestrate。flow が複数ノードで組まれていればタスク単位の claim 分散も従来どおり働く |
| `amigos` | 封筒＋ `engine.amigos.roles` から **自分をオーナーノードとして `post`**（dashboard の amigos-adapter / commands 契約と同じ変換） | ロール募集・協働・統合・受入は amigos の全機構が従来どおり。板の落札＝「ミッションオーナーの決定」 |

- 変換ロジックは agent-dashboard の `delegation/main/*-adapter.js` に実装済みの写像と
  同一仕様。board デーモン（Python）へは**同じ契約の別実装**として移植する
  （delegation 設計 §7 の「共通 claim 仕様書」と同様、契約で縛って実装は独立）。
- 進捗の観測: board デーモンはエンジン側の状態（flow `meta.json` / amigos `derive_phase`）を
  **delegation view（既存の読み取り契約）に正規化して `status/<who>.json` へ転記**する。
  依頼者・dashboard は板だけ見れば横断状況が分かり、詳細はエンジンのバスへ降りる。
- dashboard の委譲タブは、投函先を IPC 直接からboard リポジトリへ切り替えるだけで
  そのまま横断管理面になる（封筒・ビューの形は不変）。

## 7. 投機同時実行と成果の一本化（要件 2 後段）

既定（`speculation` 省略）は勝者 1 ノードのみが実行する。`speculation.max_runners: N` の公示では:

1. **bid 順位 1..N のノードが並行実行してよい**（順位は同じ決定的タイブレークの昇順）。
   各自の成果は名義分割された `results/<who>.json` に報告する — 衝突しない。
2. コード成果物は workspace リポジトリの **`board/<id>/<who>` ブランチ**に push する
   （amigos §8.3 の `amigos/<mid>/<role>` 方式の一般化）。板や成果リポジトリの正史を汚さない。
3. **一本化（resolve）**:
   - `first-valid`（既定）: `results/<who>.json` のうち **`verify` PASS（封筒の task 語彙を流用した
     受入コマンド）を満たす報告の `(completed_ts, who)` 最小**を勝者とし、依頼者ノードの
     デーモンが `result.json` を書いて確定する。判定材料が全てファイルなので、
     依頼者が落ちていても再起動後に同じ結論になる（決定的）。
   - `owner-picks`: 依頼者（人 or agent 受入）が候補を見て `result.json` を書く。
4. **成果はひとつ**: `result.json` が唯一の確定点（amigos「done を作るのはオーナーの accept のみ」
   と同じ不変条件）。敗者の成果は `results/` に監査として残し、敗者ブランチは gc が削除する。
   納品の永続化は amigos の納品棚（`delivery.schema.json`）へ同じ形で搬出する。
5. **会計**: 敗者の実行時間も node-budget 台帳には記帳される（実際に使った資源だから）。
   ミッション予算に載せるかは封筒の `budget` を解釈するエンジン側の既存規則に従う。

投機は「速さ・成功率を金で買う」明示のオプトインであり、既定はオフ。
flow の「勝者のみが results を書く」原則は**エンジン内では不変**で、
投機は板のレイヤ（独立した run を N 本走らせ、採用を 1 つ選ぶ）に閉じる。

## 8. 障害・整合性

| 事象 | 検知 | 回復 |
|---|---|---|
| 落札ノードのクラッシュ | `status/<who>.json` のハートビート途絶 → bid lease 失効 | 公示が再び入札可能に。次点ノードが落札し、エンジンのリトライ機構（flow の inherit_from 等）で引き継ぐ |
| 計画停止（毎晩シャットダウン） | `state: away` ＋ `resume_at`（amigos 流用） | 猶予内はロール保持。超過で再募集 |
| 依頼者ノード停止 | award / result が進まないだけ | 実行は継続。復帰後に受入。first-valid なら誰が計算しても同じ勝者なので確定が遅れるだけ |
| push 競合 | git | 名義分割で原理的に稀。rebase リトライで吸収 |
| forge 停止 | fetch/push 失敗 | 各ノードはローカルミラーで継続、復帰後に同期。調整は板のファイルから決定的に再導出 |
| 二重公示 | `id` 冪等キー | 同一 id は同一公示として無視 |
| 板の肥大化 | — | `gc`: 終端した `delegations/<id>/` の削除＋敗者ブランチ削除（納品棚は残る） |

## 9. 代替案の比較 — セルフホスト OSS を中核に据える場合

「全て実装ではなく既存 OSS でも良い」への正面回答。**中核（調整・台帳）を OSS に任せる案**と
推奨案を並べる:

| 観点 | ★A: board リポジトリ＋claim（推奨） | B: forge の issue を板に（gitlab-idd 方式） | C: NATS JetStream | D: RabbitMQ | E: Temporal / Hatchet / Windmill |
|---|---|---|---|---|---|
| 先勝ち入札の決定性 | ◎ 実証済みの決定的 claim | ○ assignee 取り合い（API の CAS 依存） | △ queue group の配達 = ブローカー任せ（監査困難） | △ 同左 | △ タスクキュー配達（bid 概念なし） |
| owner-picks（応募→選定） | ◎ 既存機構 | ○ ラベル運用で可能 | ✕ 作り込み | ✕ 作り込み | ✕ 作り込み |
| 投機実行＋成果一本化 | ◎ 名義分割 results＋決定的 resolve | △ issue コメント運用 | ✕ 自前実装 | ✕ 自前実装 | △ 子 WF を N 本＋select は書けるが台帳が Temporal 内 |
| リポジトリ別ノード選別 | ◎ repos レジストリ照合 | ○ ラベル `repo:*` | ○ subject 分割 `jobs.<repo>` | ○ ルーティングキー | ○ キュー分割 |
| push 配信 | ○ webhook / hub long-poll | ○ webhook | ◎ ネイティブ | ◎ ネイティブ | ◎ ネイティブ |
| 既存スキーマ踏襲 | ◎ 封筒そのまま | ○ 封筒を issue 本文に埋める | △ 二重表現 | △ 二重表現 | ✕ WF 定義へ翻訳 |
| 真実の一元性 | ◎ 板のファイルが唯一 | ○ forge DB | ✕ ブローカー状態と板の二重台帳 | ✕ 同左 | ✕ サーバ DB が正 |
| 夜間シャットダウン耐性 | ◎ 設計前提 | ◎ | ○ durable consumer | ○ | ○ |
| 追加運用（個人 PC） | なし（forge は既存前提） | なし | ブローカー常駐＋監視 | 同左 | サーバ＋DB（重量級） |
| 新規実装量 | 小（board デーモン＋アダプタ移植） | 小〜中（executor 資産流用） | 中 | 中 | 大 |

- **案 B は現実的な次点**。agent-flow の gitlab executor（イシュー起票→claim→MR→自動マージ）が
  実証済みで、「板 = forge の issue、入札 = assignee、成果 = MR」の写像は自然。
  ただし forge API への結合が強く（selfhost-forge-comparison §2 の教訓 — GitLab v4 縛りの再生産）、
  オフライン耐性・決定的再導出は git バスに劣る。**forge を GitLab に固定してよい運用**なら
  A の代わりに選べる。
- **案 C（NATS）は「push 配信の加速装置」としてのみ将来検討**。§5.3 の webhook で
  当面足りるため v1 では不採用。中核に据えると「配達された者が勝者」となり、
  板ファイルからの決定的再導出（誰が計算しても同じ勝者）が失われる。
- **案 E は要件過剰**。ワークフロー実行そのものは agent-flow が既に担っており、
  Temporal を入れると「タスクグラフの真実」が二箇所になる。

## 10. 実装フェーズ（推奨案 A を採る場合）

| フェーズ | 内容 | 状態 |
|---|---|---|
| **P0（板と入札）** | board リポジトリ規約・`nodes/` 登録・封筒ドロップ（post/cancel）・claim 流用の先勝ち入札・repos 照合による入札選別・board デーモン（ポーリング）・flow / amigos への引き渡し | ✅ 実装済み（`tools/agent-board/`・`tests/test_agent_board.py` 26 件）。2 ノードで同一公示に同時入札しても落札は決定的に 1 ノード・workspace.url を担当しないノードは入札しない・owner-picks は award 後に引き渡し・max_concurrent 上限を stub で検証 |
| **P0'（4 ツールの結合点）** | flow: inbox 引き渡し＋`delegation` 来歴を meta へ／amigos: `repos` 能力宣言と `requires.repos` 選別／project: `board-offload`（タスク→委譲）／dashboard: board アダプタ・IPC・`boardRepos` 一覧 | ✅ 実装済み（agent-flow / agent-amigos / agent-project / agent-dashboard 各テスト緑。契約は board の `validate_post` と横断一致） |
| **P1（受入・観測の拡充）** | delegation view の `status/<who>.json` へのエンジン状態転記（flow meta / amigos derive_phase の正規化）・dashboard の renderer UI（board ターゲット選択）・納品棚連携 | 未（board 側は status を dispatched で書くところまで。エンジン状態の逐次転記は追加実装） |
| **P2（push 配信・投機）** | forge webhook／hub long-poll 経路・`speculation`（max_runners・first-valid / owner-picks 一本化・敗者 gc・敗者ブランチ回収）・away 対応 | 未（契約は additive で用意済み — `board.schema.json` の speculation / result_report、`resolve_first_valid` は実装＋テスト済み。配信経路と実行ノード側の投機は追加実装） |

## 11. 非目標

- **エンジン内部の claim / バスの統一はしない**（delegation 設計の決定を維持。板は上に載るだけ）。
- **インターネット越しのフェデレーションはしない**（オンプレ限定。認可は forge の git 認証に委ねる）。
- **板でのタスク分解・進行管理はしない** — 分解は flow、協働は amigos の領分。板は
  「依頼を誰が引き受け、成果がどれか」だけを管理する。
- **課金・評価（レピュテーション）による入札調停はしない** — 先勝ち＋ノード側の自己抑制で足りる。
  必要になったら bid にスコアを載せる additive 拡張で対応できる形にしておく。
