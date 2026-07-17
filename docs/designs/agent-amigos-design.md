# agent-amigos — 役割駆動マルチエージェント協働ツール 設計書

> 作成日: 2026-07-17 ／ ステータス: **Draft（実装未着手）**
> 対象ブランチ: `claude/agent-amigos-design-u1gy34`
> 予定ファイル: `tools/agent-amigos/agent-amigos.py`, `tools/agent-amigos/README.md`,
> `schemas/amigos-mission.schema.json`
>
> **依存する既存設計**: [`agent-flow-design.md`](./agent-flow-design.md)（バス抽象・claim プロトコル）,
> [`agent-cli-plugin-design.md`](./agent-cli-plugin-design.md)（LLM 実行 CLI の共通契約）,
> [`kiro-loop-agent-messaging-design.md`](./kiro-loop-agent-messaging-design.md)（inbox 型メッセージング）

---

## 1. 概要

agent-amigos は、**複数のエージェントに別々の役割（ロール）とミッションを与え、
相互にコミュニケーションしながら一つの成果物を作り上げる**協働基盤。

- **オーナーノード**が「design doc ＋ 役割ミッション表」を投げると**ミッション**が公示され、
  参加ノードの**アサイン受付**が始まる。
- 参加ノードはロールを claim して **amigo**（ロールを演じるエージェント実体）になる。
  amigo は kiro / claude / copilot / codex / cursor いずれの CLI でも動く
  （[`agents/<name>.json`](../../agents/README.md) プラグイン契約を再利用）。
- amigo 同士はバス上のチャンネル / inbox で**相互に質問・回答・レビュー・決定**をやり取りし、
  各自の成果物を積み上げ、統合ロールが 1 つの成果物にまとめる。
- 結果（deliverable）は**オーナーノードに返却**され、オーナーが受入判定する。
- **複数の分散ノード**が参加できるが、**1 ノードでも完結**する（未充足ロールの自己補充）。
- 通信はファイルのみ。バスは ローカル dir ／ 共有 git リポジトリ（オンプレ）／
  中央中継サーバ（オンプレ、任意）を差し替え可能。

```
                 ┌───────────── 共有バス（local dir / git repo / hub server）─────────────┐
 owner: post ──▶ │ missions/<mid>/                                                        │
                 │   mission.yaml + design-doc.md + roles/   … 公示（オーナーが書く）       │
                 │   assignments/<role>/<who>.json           … アサイン claim              │
                 │   channels/ + inbox/<role>/               … エージェント間メッセージ     │
                 │   artifacts/<role>/ + deliverable/        … 成果物                      │
 owner: collect ◀│   final.json                              … 受入結果                    │
                 └──▲──────────────────▲──────────────────▲───────────────────────────────┘
          pull/push│          pull/push│          pull/push│
   ┌───────────────┴───┐  ┌────────────┴──────┐  ┌─────────┴─────────┐
   │ owner node (PC-A)  │  │ node PC-B         │  │ node PC-C         │
   │  ├ owner デーモン   │  │  └ amigo: impl-api│  │  ├ amigo: reviewer│
   │  └ amigo: architect│  │    (codex)        │  │  └ amigo: qa      │
   │    (claude)        │  │                   │  │    (kiro-cli)     │
   └────────────────────┘  └───────────────────┘  └───────────────────┘
```

---

## 2. 背景・目的

agent-flow が「**実行時に LLM がタスクグラフを生成する** Dynamic Workflow」であるのに対し、
agent-amigos は「**人が設計した役割分担のもとで、対話しながら進む**チーム型協働」を志向する。
両者は補完関係にある（§15 参照）。

満たしたい要件と実現方法:

| 要件 | 実現方法 |
|------|---------|
| 役割・ミッションを与えた複数エージェントの協働 | ミッション公示（design doc ＋ 役割ミッション表）とロール別 amigo ランナー |
| エージェント間の相互コミュニケーション | バス上の追記専用チャンネル ＋ ロール別 inbox（§7） |
| 一つの成果物への統合 | integrator ロールと `deliverable/`、オーナー受入（§8） |
| kiro/copilot/claude/codex/cursor 対応 | agent-cli プラグイン契約をそのまま利用（§9） |
| 分散ノード参加、かつ 1 ノードでも動作 | claim 型アサイン ＋ 未充足ロールの自己補充（§6.4） |
| 中央サーバなしを基本、オンプレ中央サーバも可 | Bus 抽象（Local / Git / Hub）で転送層を差し替え（§5） |

### 2.1 既存ツールとの差別化

| ツール | 構造の決め方 | 実行主体 | 相互通信 |
|--------|------------|---------|---------|
| `agent-flow` | LLM が実行時にタスクグラフを生成 | 匿名ワーカー（誰が取ってもよい） | なし（結果ファイルの受け渡しのみ） |
| `multi-agent-shogun-kiro` | 将軍/家老/足軽の固定階層 | 階層ごとの kiro-cli | 上意下達のみ |
| `council-system`（スキル） | 3 性格の合議（固定） | 単一セッション内サブエージェント | セッション内のみ |
| agent-loop メッセージング | なし（汎用 inbox） | 各 agent-loop | 汎用（ミッション概念なし） |
| **`agent-amigos`** | **人が書く役割ミッション表** | **ロールを claim した分散ノード** | **質問・回答・レビュー・決定の型付きメッセージ** |

一言でいうと: agent-flow は「仕事を**分解して配る**」、agent-amigos は「**チームを組んで作る**」。

---

## 3. コア概念

| 用語 | 意味 |
|------|------|
| **ミッション** | 1 つの成果物を作る協働の単位。`mission.yaml`（目標・方針・ポリシー）＋ `design-doc.md`（設計書）＋ 役割ミッション表で公示される。ID は `am-<UTC ts>-<rand4>` |
| **オーナーノード** | ミッションを公示し、アサインを承認（または自動承認）し、成果物を受け取り、受入判定するノード。1 ミッションに 1 つ |
| **ロール** | 役割ミッション表の 1 行。個別ミッション・成果物・席数（seats）・必須/任意を持つ |
| **amigo** | あるロールを claim して演じるエージェント実体（＝参加ノード上のランナープロセス ＋ agent CLI）。`<node-id>/<role-id>` で一意 |
| **バス** | ミッションの全状態が置かれるファイル空間。真実は常にバス上のファイルにあり、プロセスはステートレス（agent-flow と同じ原則） |
| **deliverable** | integrator が組み上げ、オーナーが受け入れる最終成果物一式 |

### 3.1 ミッションのライフサイクル

状態は agent-flow と同様に**ファイルの存在から導出**し、`mission.yaml` の書き換えは
オーナーのみが行う（§4.2 の書き込み規律）。

```
 draft ──post──▶ open（募集中）──必須ロール充足──▶ working ──全ロール完了宣言──▶ integrating
                    │                                 │                            │
                    │ staffing_timeout                │ cancel / mission_timeout   ▼
                    ▼                                 ▼                      reviewing（受入）
              自己補充 or failed                   cancelled                 │accept    │reject
                                                                            ▼          ▼
                                                                          done      working へ差し戻し
                                                                                   （フィードバック付き）
```

---

## 4. バス上のファイルレイアウトと書き込み規律

### 4.1 レイアウト

```
<bus>/missions/<mission-id>/
  mission.yaml               # 公示本体: title/goal/deadline/各種ポリシー（オーナーのみ書く）
  design-doc.md              # 設計書（オーナーのみ書く。改訂はオーナー経由）
  roles/<role-id>.yaml       # 役割ミッション表の 1 行 = 1 ファイル（オーナーのみ書く）
  assignments/<role-id>/<who>.json   # アサイン claim（応募者が自分名義ファイルだけ書く）
  roster.json                # 確定名簿（オーナーのみ書く。§6.3）
  status/<who>.json          # amigo の自己申告状態 + ハートビート（各自が自分の分だけ）
  channels/all/<who>/<ulid>.json     # 全体チャンネル（送信者が自分の名前空間にだけ追記）
  inbox/<role-id>/<ulid>-<from>.json # ロール宛メッセージ（送信者が書く。ファイル名衝突なし）
  artifacts/<role-id>/…      # 各ロールの成果物（担当 amigo のみ書く）
  decisions.jsonl            # 決定記録（オーナーのみ追記。§7.3）
  deliverable/…              # 統合成果物（integrator のみ書く）
  final.json                 # 受入結果（オーナーのみ書く）
  events/<who>.jsonl         # 追記専用の監査ログ（各自が自分のファイルだけ）
```

### 4.2 衝突しない書き込み規律（agent-flow §4.2 の継承）

git バスでもコンフリクトしないよう、**書き込み所有権をパス単位で分割**する。

| パス | 書く人 |
|---|---|
| `mission.yaml` / `design-doc.md` / `roles/*` / `roster.json` / `decisions.jsonl` / `final.json` | オーナーのみ |
| `assignments/<role>/<who>.json` | 応募する各ノード（ファイル名＝自分なので衝突しない） |
| `status/<who>.json` / `events/<who>.jsonl` / `channels/all/<who>/*` | 各 amigo が自分名義の分だけ |
| `inbox/<role>/<ulid>-<from>.json` | 送信者（ulid＋送信者名で衝突しない） |
| `artifacts/<role>/*` | そのロールの確定 amigo のみ |
| `deliverable/*` | integrator ロールの確定 amigo のみ |

**LLM はバスに直接書かない**。amigo ランナーが LLM 出力（アクション封筒、§7.2）を検証して
代書することで、この規律をコードで強制する（パス検証・所有権チェック・`..` 拒否）。

---

## 5. 転送層（Bus 抽象）— 分散が基本、中央サーバは任意

agent-flow の `Bus` 抽象（`sync_pull()` / `sync_push(msg)`）と同じ形で 3 実装を持つ。
**協調ロジック（アサイン・状態導出）は転送層に依存せず、全実装で同一**。

| 実装 | 転送 | 想定 | 備考 |
|------|------|------|------|
| `LocalBus` | no-op（同一ディレクトリ） | 1 マシン | 最小構成。テスト・1 ノード運用 |
| `GitBus` | `git pull --rebase` / `add+commit+push` | **複数ノード分散（推奨）** | オンプレ git remote（[plan-a のローカル GitLab](./plan-a-local-gitlab-design.md)・Gitea・bare repo over ssh）をそのまま「中央」に使える。sparse checkout / subdir 間借りは agent-flow §6 の実装を踏襲 |
| `HubBus`（P2） | HTTP long-poll の薄いファイルストア | git が使えない環境・低レイテンシが欲しい環境 | §5.2 |

### 5.1 「中央サーバ」の位置づけ

要件の「既存システム（オンプレのみ）を使えるなら中央サーバがあってもよい」への回答は 2 段構え:

1. **第一候補はオンプレ git リモートを中央に据える `GitBus`**。既存の GitLab CE / Gitea /
   ssh bare repo が「中央サーバ」を兼ね、新規サーバ実装ゼロ・認証も既存の git 認証に乗る。
2. git が使えない、またはメッセージ往復のレイテンシを詰めたい場合のみ、任意コンポーネント
   **`agent-amigos hub`**（`HubBus` の対向、§5.2）をオンプレに立てる。

いずれの場合も**中央はただの転送・保管であり、調整役ではない**。アサインの勝者決定や状態遷移は
各ノードが決定的に導く（§6）ため、中央が落ちても壊れない（回復後に同期が追いつくだけ）。

### 5.2 HubBus / hub サーバ（P2・任意）

- stdlib のみ（`http.server`）の薄い API: `PUT /o/<path>`（作成専用・上書き 409）、
  `GET /o/<path>`、`GET /list/<prefix>?since=<cursor>`（追加分の列挙、long-poll 可）。
- セマンティクスは「**追記専用のファイル置き場**」であり、§4 のレイアウトをそのまま写像する。
  条件付き PUT（create-only）だけで足りるのは、レイアウトが最初から追記・名義分割で
  設計されているため。
- 認証は Bearer トークン（環境変数）。TLS はリバースプロキシに委譲。オンプレ限定を前提とし、
  インターネット公開は非対応と明記する。

### 5.3 レイテンシの期待値

メッセージ 1 往復 ≒ 同期間隔 × 2（GitBus 既定 poll 30–60s）。agent-amigos の会話粒度は
「質問→回答」「レビュー依頼→指摘」であり、チャットの即時性は目標にしない。
詰めたい場合に HubBus（long-poll で秒オーダー）を選ぶ、という整理。

---

## 6. アサインプロトコル（募集 → claim → 確定 → 自己補充）

### 6.1 公示と応募

- オーナー: `agent-amigos post --design design-doc.md --roles roles.yaml` で
  `missions/<mid>/` 一式を書き、状態 open（募集中）になる。
- 参加ノード: `agent-amigos join --bus <...> [--roles r1,r2] [--agent-cli codex]` の
  デーモンが open なミッションを発見し、ロール要件（`requires.tags` / `requires.cli` 等）と
  自ノードの能力宣言（`node.yaml`: タグ・使える CLI・同時 amigo 数上限）を突き合わせて応募する。
- 応募は `assignments/<role-id>/<who>.json` に**自分名義ファイルを書くだけ**
  （`who = <node-id>`、内容は ts / 使う CLI / lease）。

### 6.2 勝者決定 — agent-flow の claim プロトコルを流用

席数 `seats: N` のロールは、有効（lease 内）な claim のうち **`(ts, who)` 昇順の先頭 N 件**が
決定的に勝者となる。全ノードが同じ集合から同じ勝者を導くため、ローカルでも git でも
二重アサインが起きない（agent-flow §5.1 と同一の理屈。push 競合は rebase リトライで吸収）。

### 6.3 確定名簿（roster）と承認ポリシー

`mission.yaml` の `assignment_policy` で選ぶ:

| ポリシー | 動作 |
|---|---|
| `first-come`（既定） | claim 勝者＝確定。オーナーは導出結果を `roster.json` に**鏡写し**するだけ（表示・監査用） |
| `owner-picks` | claim は「応募」。オーナー（人または owner デーモンの LLM 判定）が `roster.json` に書いた者だけ確定 |

roster 確定した amigo だけが `artifacts/<role>/` への書き込み権を持つ（ランナーが強制）。

### 6.4 自己補充 — 1 ノード動作の保証

`staffing_timeout`（既定 10 分）を過ぎても必須ロールが未充足の場合、`staffing_policy` に従う:

| ポリシー | 動作 |
|---|---|
| `self-staff`（既定） | **オーナーノードが未充足ロールの amigo をローカルに自動起動**して claim する。これにより参加ノードが 0 でもミッションは必ず進行する＝ 1 ノード完結 |
| `wait` | 充足まで open のまま待つ |
| `fail` | failed で終端（人へ通知） |

1 ノードに複数 amigo が同居する場合、agent-loop の `GlobalSemaphore`（`~/.kiro/slots/`）で
agent CLI の同時実行数を絞る（既存実装を再利用）。

### 6.5 離脱・死亡とロール再募集

- claim は lease 付き。amigo ランナーは `status/<who>.json` のハートビート更新で lease を延長する。
- lease 失効（ノード死亡・切断）を検知したら、そのロールは**再募集**に戻る。
  成果物・inbox・events はバスに残っているため、後任 amigo は
  「ロール定義 ＋ 前任の status / events / artifacts」を読んで**引き継ぎから再開**する。
- agent-flow と同じく lease は liveness の信号であり progress ではない。ハングは
  agent CLI 実行のタイムアウト（プラグイン定義の `timeout`）で塞ぐ。

---

## 7. エージェント間コミュニケーション

### 7.1 チャンネルと inbox

| 経路 | 用途 | 実体 |
|---|---|---|
| `channels/all/` | 全体連絡・進捗宣言・設計判断の相談 | 送信者名義ディレクトリへの追記専用 JSON |
| `inbox/<role-id>/` | 特定ロール宛の依頼・質問・レビュー結果 | `<ulid>-<from>.json`（宛先は読むだけ） |
| `inbox/owner/` | オーナー宛（設計判断のエスカレーション等） | 同上 |

既読管理は**各 amigo が自分のカーソル**（ローカルに `last_seen` ulid）を持つだけ。
バス上に既読フラグを書かない（書き換え競合を作らない）。

### 7.2 メッセージスキーマとアクション封筒

メッセージは型付き:

```json
{
  "id": "01J8Z...",            // ulid（時系列順序が名前で決まる）
  "from": "reviewer",          // role-id（システム発は "system"）
  "to": "impl-api",            // role-id / "all" / "owner"
  "type": "question | answer | request | review | status | decision-request | info",
  "subject": "GET /faq のページング仕様",
  "body": "...",
  "refs": ["artifacts/impl-api/openapi.yaml"],
  "reply_to": "01J8Y...",      // スレッド化
  "created_at": "2026-07-17T12:00:00Z"
}
```

amigo ランナーは agent CLI の出力を**アクション封筒**（1 回の思考で実行したい操作の配列）として
受け取り、検証してからバスへ代書する:

```json
{
  "actions": [
    {"kind": "send", "to": "architect", "type": "question", "subject": "...", "body": "..."},
    {"kind": "write_artifact", "path": "openapi.yaml", "content_file": "<tmp>"},
    {"kind": "update_status", "phase": "working", "note": "エンドポイント 3/5 完了"},
    {"kind": "declare_done"}
  ]
}
```

検証内容: 自ロールの書き込み所有権（§4.2）・パス正規化（`..` 拒否）・型の妥当性。
不正アクションは棄却して events に記録し、次ターンのプロンプトで LLM に差し戻す。

### 7.3 会話の規約（プロトコルとしての最低限）

- **question には answer か「owner へのエスカレーション」で必ず応じる**。`question_timeout`
  （既定 2 ターン）を過ぎた未回答はランナーが自動で owner へ `decision-request` に昇格する。
- 設計を左右する合意は owner が `decisions.jsonl` に追記して確定する
  （`.agent-project/decisions/` と同じ思想の決定記録。amigo は次ターンから全員これを読む）。
- design doc の改訂はオーナーのみ（amigo は `request` で提案する）。
  「何が正か」を常に 1 箇所に保つ。

### 7.4 amigo ランナーのターンループ

```
loop:
  1. bus.sync_pull()
  2. 新着収集: inbox/<自ロール>/ + channels/all/ + decisions.jsonl（カーソル以降）
  3. mission 終端 or 自ロール完了済み → exit
  4. プロンプト合成:
       ロール定義（roles/<id>.yaml）+ design-doc.md + 決定記録
       + 新着メッセージ + 自分の直近 status + artifacts 一覧
  5. agent CLI 実行（agents/<name>.json プラグイン経由）→ アクション封筒
  6. 封筒を検証してバスへ適用（send / write_artifact / update_status / declare_done）
  7. status/<who>.json 更新（ハートビート・lease 延長）
  8. bus.sync_push() → 次ターンまで sleep（新着ゼロなら間隔を伸ばす adaptive interval、
     kiro-loop-adaptive-interval-design.md の方式を簡略採用）
```

新着ゼロかつ自分の TODO もない場合は **agent CLI を呼ばない**（idle ターン）。
LLM 呼び出しはメッセージ・成果物に変化があったときだけ、が既定。

---

## 8. 成果物の統合と返却

### 8.1 integrator ロール

- 役割ミッション表に `builtin: integrator` のロールを 1 つ置く（省略時はオーナーノードが
  自動で self-staff する）。
- 全必須ロールが `declare_done` すると integrating へ進み、integrator が
  `artifacts/*` を検証・統合して `deliverable/` に**単一の成果物一式**
  （成果物本体 ＋ `MANIFEST.json`: 由来ロール・ハッシュ・組み立て手順）を書く。

### 8.2 受入（オーナーへの返却）

- integrator の完了で reviewing へ。オーナーノードは `agent-amigos collect <mid> --out <dir>`
  で deliverable を取り出す。
- 受入判定は `acceptance` ポリシーで選ぶ:
  `manual`（人が確認して `accept`/`reject` サブコマンド）／
  `agent`（オーナーの agent CLI が design doc と突き合わせて判定）／
  `codd-gate`（[一貫性ゲート](./codd-gate-design.md)を通す。将来拡張）。
- `reject` はフィードバックを `inbox/all` 相当（全体宛 review メッセージ）と
  `decisions.jsonl` に残して working へ差し戻す。**done を作るのはオーナーの accept のみ**。

### 8.3 コード成果物の扱い

deliverable がリポジトリ変更の場合、artifacts にパッチを置くのではなく、
`mission.yaml` の `workspace.repo` に対象リポジトリを宣言し、amigo は
`amigos/<mission-id>/<role-id>` ブランチで作業して push、integrator が統合ブランチ
`amigos/<mission-id>/integration` へマージして deliverable の `MANIFEST.json` から参照する。
バスに巨大ファイルを積まない（バスは調整とメッセージ、コードは git、の分離）。

---

## 9. agent CLI 抽象 — 既存プラグイン契約の再利用

- LLM 実行は [`schemas/agent-cli.schema.json`](../../schemas/agent-cli.schema.json) の
  プラグイン契約（`agents/<name>.json`）を**そのまま**使う。kiro / claude / copilot / codex は
  組み込み、cursor / ollama は同梱定義済み。agent-amigos 側に CLI 分岐コードを書かない。
- ロールごとに CLI を選べる: `roles/<id>.yaml` の `agent_cli:`（未指定はノード既定）。
  例: reviewer は claude、実装は codex、QA は kiro-cli、と混成チームを組める。
- 失敗は [`agent-cli-plugin-design.md`](./agent-cli-plugin-design.md) の
  **決定的トリアージ**（`[agent-error:quota|auth|env|transient]`）を読む:
  - `transient` → そのターンをリトライ。
  - `quota`/`auth`/`env` → その **amigo を paused** にして status へタグ付き理由を書き、
    owner へ通知。ロールは lease を保持したまま待機（環境を直せば続きから）。
    ミッション全体は殺さない（他ロールは進行継続）。

---

## 10. データスキーマ（役割ミッション表）

`roles.yaml`（post 時にオーナーが渡す。正典スキーマは `schemas/amigos-mission.schema.json` に置く）:

```yaml
mission:
  title: 社内 FAQ ボットの MVP
  goal: design-doc.md の受入基準をすべて満たす FAQ ボットを納品する
  deadline: 2026-07-24T09:00:00Z          # 任意。超過で owner へ通知
  assignment_policy: first-come            # first-come | owner-picks
  staffing_policy: self-staff              # self-staff | wait | fail
  staffing_timeout: 600
  acceptance: manual                       # manual | agent | codd-gate
  workspace:
    repo: ssh://git@gitlab.local/team/faq-bot.git   # コード成果物用（任意）

roles:
  - id: architect
    title: アーキテクト
    mission: |
      design-doc.md を正として構成を確定し、他ロールからの設計質問に回答する。
      判断に迷うものは owner へ decision-request でエスカレーションする。
    deliverables: [architecture.md]
    required: true
    seats: 1
    agent_cli: claude
  - id: impl-api
    title: API 実装
    mission: architecture.md に従い API を実装し、単体テストを通す。
    deliverables: [src/, tests/]
    required: true
    seats: 1
    requires: { tags: [python] }           # ノード能力とのマッチング条件
    collaborates_with: [architect, reviewer]  # 会話相手のヒント（プロンプトに載る）
  - id: reviewer
    title: レビュアー
    mission: 全ロールの成果物を design-doc.md と突き合わせてレビューし、指摘を返す。
    required: true
    seats: 1
  - id: integrator
    builtin: integrator
    mission: 全成果物を統合し deliverable/ を組み立てる。
    required: true
```

`collaborates_with` は依存グラフではなく**会話のヒント**に留める（実行順序の強制はしない。
順序が本質の仕事はタスクグラフ＝agent-flow の領分、§15）。

---

## 11. CLI コマンド体系

```
agent-amigos init-bus  (--dir <path> | --git <url> [--subdir amigos] | --hub <url>)
agent-amigos post      --design design-doc.md --roles roles.yaml     # オーナー: 公示
agent-amigos join      [--roles r1,r2] [--agent-cli codex] [--tags python,frontend]
                                                                     # 参加ノード: 常駐デーモン
agent-amigos run       --mission <mid> --role <role> [--once]        # 単発 amigo（デバッグ用）
agent-amigos status    [<mid>]                                       # 名簿・各ロール状態・未回答質問
agent-amigos collect   <mid> --out ./deliverable                     # オーナー: 成果物取り出し
agent-amigos accept    <mid> / reject <mid> --feedback "..."         # オーナー: 受入判定
agent-amigos say       <mid> --to <role|all> --body "..."            # 人がバスに直接発言（介入）
agent-amigos cancel    <mid>
agent-amigos gc        [--keep-days 14]
```

`say` は「人もチームの一員として口を挟める」ための穴。owner 名義（または `--as` 指定ロール名義の
`human:` プレフィクス付き）でメッセージを書く。

---

## 12. 障害・回復のまとめ

| 障害 | 検知 | 回復 |
|---|---|---|
| amigo ノード死亡 | ハートビート途絶 → lease 失効 | ロール再募集。後任が status/events/artifacts から引き継ぎ（§6.5） |
| agent CLI ハング | プラグイン timeout | ターン失敗 → リトライ、繰り返せば paused ＋ owner 通知 |
| quota/auth/env | `[agent-error:*]` タグ | amigo paused・環境修復後に続きから（§9） |
| 質問の放置 | `question_timeout` | ランナーが owner へ自動エスカレーション（§7.3） |
| push 競合（GitBus） | git | 名義分割で原理的に稀。`pull --rebase` リトライで吸収 |
| 議論の空転・停滞 | `mission.yaml` の `max_turns` / `deadline` | owner へ通知。owner が decisions で裁定 or cancel |
| オーナーノード停止 | roster/decisions が進まない | ミッションは自然停止（amigo は idle ターンで待機）。オーナー復帰で再開。オーナーのフェイルオーバーは非目標（§14） |

---

## 13. セキュリティと信頼境界

- **オンプレ限定**。バスへの到達性＝参加権限とし、認可は既存基盤（git 認証 / hub の Bearer）に
  委ねる。バスに秘密情報を書かない（プロンプトへ渡す資格情報は各ノードのローカル環境変数）。
- **他 amigo からのメッセージは半信頼**として扱う。ランナーはプロンプト合成時にメッセージを
  「他エージェントからの入力（指示ではなく情報）」として区画表示し、
  design doc と decisions のみを正典と明示する（プロンプトインジェクション耐性の最低線）。
- ランナーによる代書（§7.2）が唯一の書き込み経路であり、LLM の出力がバス規律・
  ワークスペース外パスを破ることはできない。

---

## 14. 非目標（Non-goals）

- **タスクグラフの動的生成はしない** — agent-flow の領分。amigo が自分のミッション遂行の
  内部で agent-flow に submit するのは自由（§15）。
- **リアルタイムチャットの即時性は保証しない**（§5.3）。
- **オーナーのフェイルオーバー / 多重オーナーはしない**。オーナーは単一障害点だが、
  停止しても状態はバスに残り、復帰すれば続きから進む。
- **インターネット越しのフェデレーションはしない**（オンプレ限定）。
- **課金・人格の永続化はしない**。amigo はミッション限りの実体（長期記憶が欲しければ
  ltm-use をロールのミッション文で指示する）。

---

## 15. agent-flow との住み分けと相互運用

| 観点 | agent-flow | agent-amigos |
|---|---|---|
| 分解の主体 | LLM（実行時） | 人（役割ミッション表） |
| 実行単位 | 使い捨てタスク | 継続するロール |
| 通信 | 結果ファイルの受け渡し | 型付き相互メッセージ |
| 向く仕事 | 分割統治できる一括処理 | 設計判断・レビューの往復が要る成果物づくり |

相互運用: amigo は自ミッションの中で `agent-flow submit` を呼び、大量の並列作業を
外注できる（例: impl-api が 30 エンドポイントの実装を map-reduce で agent-flow に投げる）。
逆方向（agent-flow のタスクから amigos を起動）は複雑化するため当面やらない。

---

## 16. 実装フェーズ

| フェーズ | 内容 | 完了条件 |
|---|---|---|
| **P0（MVP）** | LocalBus / post・join・run・status・collect / claim 型アサイン＋self-staff / inbox＋all チャンネル / アクション封筒ランナー / integrator＋manual 受入 / agent-cli プラグイン | 1 マシン上で 3 ロール（architect・impl・reviewer）が相互に質問・レビューしながら成果物を 1 つ納品し、`collect` で取り出せる。stub CLI（LLM なし）でプロトコルのユニットテストが通る |
| **P1（分散）** | GitBus（agent-flow の転送実装を移植）/ lease・ハートビート・ロール再募集 / エラートリアージ連携 / adaptive interval / say・cancel・gc | 2 ノード（別 PC）でロール分担して P0 と同じ納品ができる。ノードを 1 つ kill してもロール再募集で完走する |
| **P2（拡張）** | HubBus＋hub サーバ / owner-picks / acceptance: agent・codd-gate / state_git 方式の鏡による agent-dashboard 表示 / `schemas/amigos-mission.schema.json` 正典化 | hub 経由で P1 と同じ動作。dashboard でミッション名簿・会話・状態が読める |

テスト方針は agent-flow と同じく、**stub エージェント（決め打ち応答）でプロトコル層を
LLM なしに決定的に検証**する。claim の二重アサインなし・書き込み規律違反の棄却・
question_timeout のエスカレーション・lease 失効→再募集、が P0/P1 のコアテスト。

---

## 17. 主要な設計判断（ADR 抜粋）

1. **バスはファイル、調整は決定的アルゴリズム、中央は転送のみ** —
   agent-flow で実証済みの形。中央サーバを「調整役」にしないことで、
   1 ノード〜多ノード〜hub ありの全構成が同一コードパスになる。
2. **役割分担は人が書く（LLM に組ませない）** — 本ツールの価値は「意図した役割分担で
   対話させる」こと。動的分解が欲しいケースは agent-flow が既にある。二重発明を避ける。
3. **LLM はバスに直接書かず、ランナーが代書する** — 書き込み規律・パス安全・
   メッセージ型の強制をプロンプト頼みにしない。壊れ方を「不正アクションの棄却」という
   観測可能なイベントに閉じ込める。
4. **既読・進捗フラグをバスに書かない** — 状態はファイル存在＋各自カーソルから導出。
   git バスでの書き換え競合を設計段階で排除する（agent-flow §4.3 の継承)。
5. **done を作れるのはオーナーの accept のみ** — agent-project の不変条件
   「勝手に done を作らない」に揃え、受入の最終権限を人（オーナー）側に固定する。
