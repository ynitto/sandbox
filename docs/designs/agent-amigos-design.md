# agent-amigos — 役割駆動マルチエージェント協働ツール 設計書

> 作成日: 2026-07-17 ／ ステータス: **P0（MVP）・P1（GitBus 分散・away）・P2（hub・owner-picks・
> acceptance: agent・スキーマ正典化）実装済み**（残: acceptance: codd-gate = 将来拡張）
> 対象ブランチ: `claude/agent-amigos-design-u1gy34`
> 実装: `tools/agent-amigos/`（`agent-amigos.py` ＋ `agent_amigos/` パッケージ・
> `tests/test_agent_amigos.py`）。正典スキーマ: `schemas/mission.schema.json`。
>
> **実装メモ（設計との差分）**:
> - バス上の公示は正規化 **JSON**（`mission.json` / `roles/<id>.json`）で置く —
>   読み手に PyYAML を要求しないため。YAML はオーナーの入力形式（post 時に変換）。
> - amigo のカーソル・引き継ぎメモは自分名義の `status/<who>.json` に載せる
>   （所有権規律の範囲内で、後任の引き継ぎ・復帰再開がファイルだけで完結する）。
> - GitBus のステージは `add -A` — 各ノードが**自分専用クローン**を持つため、
>   ローカルの変更はすべて自プロセス由来であり、state_git の「自 subdir のみ
>   ステージ」と同じ安全性がクローン分離によって成立する（§5.1 の規律の等価実装）。
> - claim の勝者確認に使う pull は間隔律速の対象外（`sync_pull(force=True)`）。
>   鮮度がプロトコルの正しさに効くのは claim だけで、それ以外は律速してよい。
> - 静穏化（quiescence）で partial 統合した後に done へ到達した場合、integrator は
>   完全版で統合し直す（partial → done への昇格）。
> - accept は納品棚（`<home>/deliveries/<mid>/`）への搬出を伴う（§8.4・
>   正典スキーマ `schemas/delivery.schema.json`）。collect は補助コマンドへ降格。
>
> **依存する既存設計**: [`agent-flow-design.md`](./agent-flow-design.md)（バス抽象・claim プロトコル）,
> [`agent-cli-plugin-design.md`](./agent-cli-plugin-design.md)（LLM 実行 CLI の共通契約）,
> [`kiro-loop-agent-messaging-design.md`](./kiro-loop-agent-messaging-design.md)（inbox 型メッセージング）

---

## 1. 概要

agent-amigos は、**複数のエージェントに別々の役割（ロール）とミッションを与え、
相互にコミュニケーションしながら一つの成果物を作り上げる**協働基盤。

- **オーナーノード**が「design doc ＋ 役割ミッション表」を投げると**ミッション**が公示され、
  参加ノードの**アサイン受付**が始まる。公示には**収束条件と予算（実質実行時間）**を含められ、
  amigo たちはその範囲内でやり取りして成果物を出す（§3.2）。
- 参加ノードはロールを claim して **amigo**（ロールを演じるエージェント実体）になる。
  amigo は kiro / claude / copilot / codex / cursor いずれの CLI でも動く
  （[`agents/<name>.json`](../../agents/README.md) プラグイン契約を再利用）。
- amigo 同士はバス上のチャンネル / inbox で**相互に質問・回答・レビュー・決定**をやり取りし、
  各自の成果物を積み上げ、統合ロールが 1 つの成果物にまとめる。
- 結果（deliverable）は**オーナーノードに返却**され、オーナーが受入判定する。
- **複数の分散ノード**が参加できるが、**1 ノードでも完結**する（未充足ロールの自己補充）。
- 通信はファイルのみ。バスは ローカル dir ／ **専用の git リポジトリ**（オンプレ・
  ミッション別ブランチ、§5.1）／ 中央中継サーバ（オンプレ、任意）を差し替え可能。
- ノードが**毎晩シャットダウンする運用を一級の前提**とし、計画停止をクラッシュと区別して
  ロールの継続性を保つ（away プロトコル、§6.6）。

```
                 ┌───────────── 共有バス（local dir / git repo / hub server）─────────────┐
 owner: post ──▶ │ missions/<mid>/                                                        │
                 │   mission.yaml + design-doc.md + roles/   … 公示（オーナーが書く）       │
                 │   assignments/<role>/<who>.json           … アサイン claim              │
                 │   channels/ + inbox/<role>/               … エージェント間メッセージ     │
                 │   artifacts/<role>/ + deliverable/        … 成果物                      │
 owner: accept ◀─│   final.json                              … 受入結果 → 納品棚へ搬出     │
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
| 収束条件・予算の範囲内で自律的に収束 | mission.yaml の `convergence` / `budget` 宣言と決定的な予算会計（§3.2） |
| 中央サーバなしを基本、オンプレ中央サーバも可 | Bus 抽象（Local / Git / Hub）で転送層を差し替え（§5）。git は専用リポジトリを新規に切る |
| ノードの定期シャットダウンに耐える | away プロトコル ＋ ターン原子性 ＋ 実質実行時間ベースの予算（§6.6・§12） |

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
| **納品棚** | accept 済みの成果物をオーナーホームへ永続化した置き場（`<home>/deliveries/<mid>/`）。バスの deliverable は gc されるが、納品棚は残る（§8.4） |

### 3.1 ミッションのライフサイクル

状態は agent-flow と同様に**ファイルの存在から導出**し、`mission.yaml` の書き換えは
オーナーのみが行う（§4.2 の書き込み規律）。

```
 draft ──post──▶ open（募集中）──必須ロール充足──▶ working ──収束（§3.2）──▶ integrating
                    │                                 │                        │
                    │ staffing_timeout                │ cancel                 ▼
                    ▼                                 ▼                  reviewing（受入）
              自己補充 or failed                   cancelled             │accept    │reject
                                                                        ▼          ▼
                                                                      done      working へ差し戻し
                                                                               （フィードバック付き）
```

「収束」は §3.2 の条件（全必須ロール完了宣言・静穏化・予算枯渇 wrap-up）のいずれか
早いものが成立したとき。

### 3.2 収束条件と予算（実質実行時間）

オーナーは post 時に「**どこまでやったら終わりか**（収束条件）」と「**どれだけ使ってよいか**
（予算）」を宣言し、amigo たちはその範囲内で自律的にやり取りする。

```yaml
convergence:
  done_when: all-required-done   # all-required-done | reviewer-approved
  quiescence_turns: 3            # 全ロール新着ゼロ・未回答質問ゼロがこのターン数続いたら統合へ
  review_rounds: 2               # レビュー指摘 → 修正の往復上限
budget:
  execution_minutes: 120         # 予算 = 全 amigo の agent CLI 実実行時間の合計
  per_role_turns: 30             # ロールあたりターン数上限（空転の保険）
  soft_ratio: 0.9                # これを超えたら wrap-up モードへ
  on_exhausted: wrap-up          # wrap-up | fail
```

- **予算は wall-clock ではなく実質実行時間**（各ターンの agent CLI 実行秒の総和）。
  ノードの夜間シャットダウンや idle 待機は予算を消費しない（§6.6 の away 運用と整合し、
  「PC が落ちていた時間」で予算が溶けない）。wall-clock の締切が別に必要なら
  `deadline` を併用する（超過はオーナーへの通知であり、自動 fail にはしない）。
- **会計は決定的**: 各 amigo はターンごとに `events/<who>.jsonl` へ `cli_seconds` を追記し、
  消費合計は「バス上の全 events の総和」。誰が計算しても同じ値になるため、
  専任の集計プロセスも中央の課金台帳も要らない。
- **soft しきい値**（既定 90%）を超えたら、ランナーは次の作業ターンから **wrap-up モード**
  （「新規の論点を開かず、現状を納品可能な形に整えよ」というプロンプト前置き）に切り替え、
  最初に検知したノードが全体チャンネルへ wrap-up 宣言を流す。**hard（100%）**以降は
  integrator と受入以外の agent CLI 呼び出しを開始しない。進行中のターンは
  プラグイン timeout まで走り得るため、超過は最大〈ターン timeout × 同時実行 amigo 数〉
  に抑えられる（予算の上振れ上限が見積もれる）。
- `on_exhausted: wrap-up`（既定）は現状の artifacts のまま統合へ進み、deliverable の
  `MANIFEST.json` に `partial: true` と未達項目を記録してオーナー受入に委ねる。
  `fail` は即 failed で終端する。
- **収束条件**は次のいずれか早いもの:
  (a) `done_when` 成立（全必須ロールの `declare_done`。`reviewer-approved` なら
  加えて reviewer ロールの approve）、(b) `quiescence_turns` 連続の静穏化
  （会話が止まった＝これ以上進まないので現状で統合し、良し悪しは受入で判定）、
  (c) 予算枯渇 wrap-up。
- **予算の追加・収束条件の変更はオーナーのみ**: `agent-amigos budget add <mid> --minutes 60`
  等が mission.yaml を改訂し、`decisions.jsonl` に記録する（amigo は次ターンから読む）。

### 3.3 ノード予算 — 請負側の上限と共有台帳

ミッション予算（§3.2 = **依頼側**の宣言）とは独立に、**請負側（各ノード）も自分の上限**を
設定できる。ノードで動く LLM 仕事は amigos だけではない — 定常業務（kiro-loop /
agent-loop）・agent-project・agent-flow が同じマシンの同じ CLI 資源を食う。そこで
予算は**ノード横断の共有台帳**で管理し、**全ワークロードの合計**が上限を超えないよう
各ツールが自律的に抑制する。

**共有台帳契約**（正典: [`schemas/node-budget.schema.json`](../../schemas/node-budget.schema.json)。
ツール間はデータ契約のみで結合 — repos / task / agent-cli と同じ流儀）:

```
$AGENT_BUDGET_DIR（既定 ~/.agents/budget/）
  config.json               # 上限設定（人 / agent-dashboard / CLI が書く）
  ledger/<YYYYMMDD>.jsonl   # 記帳（UTC 日付・追記専用・O_APPEND、各ツールが 1 実行 1 行）
```

- `config.json`: 合計上限 `execution_minutes`（**0 = 無制限**、既定）・適用期間
  `period: day|month|total`（既定 day）・ワークロード別の内訳上限 `workloads:
  {routine, project, flow, amigos}`（0/省略 = 無制限。合計上限と AND で効く）。
- 記帳: `{ts, workload, tool, seconds, ref, node}`。amigos は `workload: amigos`、
  `ref: <mission-id>/<role>` で記帳する（バスの events はミッション予算の会計、
  台帳はノード予算の会計 — 二重帳簿だが対象が違う）。
- **超過時の挙動（amigos）**: そのノードの amigo は CLI ターンを開始せず **paused**
  （`[node-budget]` タグ・遷移時に一度だけ owner へ通知）。**ミッションは殺さない** —
  他ノードの amigo は進行を続け、依頼側は通知を見て別ノードへの再アサインや待ちを
  判断できる。上限を上げる（または期間が更新される）と自動で復帰する。
- **管理は依頼側・請負側どちらも**: 依頼側はミッション予算（バス上・budget add）、
  請負側はノード予算（ローカル・`agent-amigos budget node --limit-minutes N`）。
  **agent-dashboard は両方の管理面**になる — ノード予算は config.json を書き
  ledger を読むだけなので、dashboard 側はこの契約を読むタブを足せばよい。
  **実装済み**: dashboard の Amigos タブ（`tools/agent-dashboard/src/features/amigos/`、
  制御面分離の feature として独立）が、ミッションの読み取りビュー（phase 近似・名簿・
  ミッション予算・未回答質問）と、ノード予算のワークロード別消費表示・上限編集を持つ。
- **全ワークロード実装済み**: agent-flow / agent-project は LLM 単一チョークポイントで
  実行前チェック（超過は `[agent-error:quota] [node-budget]` として既存の環境要因フローに
  乗る）＋成功実行の実測秒を記帳。kiro-loop（定常業務）はスケジューラのサイクル先頭で
  抑制し、セマフォスロットの保持時間で実行秒を近似記帳する（詳細は
  [`schemas/README.md`](../../schemas/README.md) の node-budget 節）。
- チェックはロックなしの読み合計なので、上振れは「進行中ターン × 同時実行数」に
  有界（§3.2 と同じ性質）。

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
| `GitBus` | `git pull --rebase` / `add+commit+push` | **複数ノード分散（推奨）** | オンプレ git remote（[plan-a のローカル GitLab](./plan-a-local-gitlab-design.md)・Gitea・bare repo over ssh）に**専用のバスリポジトリを新規に切る**。ミッション別ブランチで分離（§5.1）。既存リポジトリへの subdir 間借りは採らない |
| `HubBus`（P2） | HTTP long-poll の薄いファイルストア | git が使えない環境・低レイテンシが欲しい環境 | §5.2 |

### 5.1 「中央サーバ」の位置づけ — 専用バスリポジトリ ＋ ミッション別ブランチ

要件の「既存システム（オンプレのみ）を使えるなら中央サーバがあってもよい」への回答は 2 段構え:

1. **第一候補はオンプレ git リモートに専用のバスリポジトリを新規に切る `GitBus`**
   （例: GitLab CE / Gitea / ssh bare repo 上の `amigos-bus.git`）。既存インフラが
   「中央サーバ」を兼ね、新規サーバ実装ゼロ・認証も既存の git 認証に乗る。
   agent-flow が持つ「既存リポジトリの subdir 間借り」方式は**採らない** —
   ミッションのメッセージ・状態はコミット頻度が高く、成果物リポジトリの履歴を汚すため。
   バス（調整・会話）と成果物（コードは `workspace.repo`、§8.3）を最初からリポジトリで分ける。
2. git が使えない、またはメッセージ往復のレイテンシを詰めたい場合のみ、任意コンポーネント
   **`agent-amigos hub`**（`HubBus` の対向、§5.2）をオンプレに立てる。

**バスリポジトリ内はミッション（タスク）単位でブランチ分離する**:

| ブランチ | 内容 | 書く人 |
|---|---|---|
| `main` | 公示インデックスのみ: `index/<mid>.json`（title / 状態 / ブランチ名 / 締切） | 各ミッションのオーナー |
| `mission/<mid>` | そのミッションの §4 レイアウト一式 | 参加者（§4.2 の所有権規律どおり） |

- 参加ノードは `main` だけを軽く poll して募集を発見し、**join したミッションのブランチだけ**
  fetch / sparse checkout する。ミッション間で履歴・コンフリクト・同期コストが交差しない。
- gc はブランチ削除 ＋ index の状態更新で完了する（バスの肥大化がミッション単位で回収できる）。

**同期の作法は agent-project / agent-flow の state_git と同じ規律を流用する**
（agent-flow 設計書 §6.1 参照）:

- fetch / push は**間隔律速**（既定 30–60s。ミッションの状態遷移・終端時は間隔を待たず押し出す）。
- push 競合は `pull --rebase` → 再 push の**指数バックオフ**。**force push はしない**。
- ステージは**自分の書き込み所有パスのみ**（`git add -A` をしない）。`*.tmp`（書きかけ）と
  `.` 始まりは同期しない。
- ノード生存の可視化は state_git の `status.json` 方式を踏襲: `status/<who>.json` に
  `fresh_after_sec` を埋め、読む側（オーナー / dashboard）は単純な経過時間比較で
  生死を判定できる。
- state_git と違い **3-way 裁定は不要**: state_git は「同一ファイルを複数ツールが書く」前提の
  鏡だが、agent-amigos はミッション＝ブランチで分離したうえ §4.2 の所有権分割で
  同一ファイルの同時変更が起きないため、rebase だけで足りる。

いずれの場合も**中央はただの転送・保管であり、調整役ではない**。アサインの勝者決定や状態遷移は
各ノードが決定的に導く（§6）ため、中央が落ちても壊れない（回復後に同期が追いつくだけ）。

### 5.2 HubBus / hub サーバ（P2・任意）— 実装済み

- stdlib のみ（`http.server`）の薄い API:
  `PUT /o/<path>`（**所有者上書き** — 所有権分割 §4.2 により 1 パス 1 書き手なので、
  hub は最後の書き込みを保持するだけで裁定しない）、`GET /o/<path>`、
  `GET /list?prefix=&since=<rev>[&wait=<sec>]`（単調増加リビジョンによる差分列挙・
  long-poll 可）、`DELETE /tree?prefix=`（gc）。
- セマンティクスは「**ファイル置き場**」であり、§4 のレイアウトをそのまま写像する。
  **hub のデータディレクトリはミッションレイアウト（`missions/<mid>/…`）そのもの**で、
  hub ホスト上の agent-dashboard は busDirs にそこを指すだけで読める。
- クライアント（HubBus）は GitBus と同じくローカルミラー上で動き、`since=<rev>` の
  差分 pull（間隔律速・claim の勝者確認は force）と、前回 push 時とハッシュが変わった
  自分の書き込み分だけの push を行う。協調ロジックは他バスと完全に同一。
- 認証は Bearer トークン（`AGENT_AMIGOS_HUB_TOKEN`）。TLS はリバースプロキシに委譲。
  オンプレ限定を前提とし、インターネット公開は非対応。クライアントは環境のプロキシ設定を
  常に迂回して hub へ直接接続する（LAN 前提）。
- 起動: `agent-amigos hub --data <dir> [--host] [--port] [--token]`。

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

各ロールは、有効（lease 内）な claim のうち **`(ts, who)` 昇順の先頭 1 件**が決定的に勝者となる。
全ノードが同じ集合から同じ勝者を導くため、ローカルでも git でも二重アサインが起きない
（agent-flow §5.1 と同一の理屈。push 競合は rebase リトライで吸収）。

席数 `seats: N`（N>1）のロールは、公示（正規化）時に `<role>#0..#N-1` の **N 個の具体席ロール**へ
展開される（`_expand_seats`、G1）。各席は通常の 1 席ロールなので claim / roster / 収束 / 統合の
機構をそのまま使い、上記の「1 ロール＝勝者 1 名」が席ごとに成立する。1 ノード運用でも self-staff が
全席を充足する。席の成果は integrator が `aggregate`（majority / consensus / gather、G2）で決定的に
集約する（§8.1）。

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

### 6.6 計画的シャットダウンへの耐性（away プロトコル）

ノードが毎晩落ちる運用（社内 PC の定時シャットダウン・省電力ポリシー等）を
**一級の前提**とする。クラッシュ（§6.5）と計画停止を区別し、**計画停止ではロールを奪わない**。

- **可用性ウィンドウの宣言**: `node.yaml` に `availability: "09:00-21:00 Asia/Tokyo"` を
  宣言できる。応募（assignments）に含まれて `owner-picks` の判断材料になるほか、
  全ノードが「このロールの担当はいま不在時間帯」を決定的に導出できる。
- **graceful offboard**: ランナーは SIGTERM / シャットダウンフックを受けると
  (1) 進行中ターンを破棄（後述の原子性によりバスには何も残らない）、
  (2) `status/<who>.json` を `state: away`（`resume_at` 付き）へ更新、
  (3) **引き継ぎメモ**（ここまでのあらすじ・次にやること・保留中の質問）を status に書き、
  最後の sync_push をして終了する。引き継ぎメモは毎ターン更新しておく（フックが走らない
  強制電源断でも、前ターン時点のメモがバスに残っている）。
- **away 中はロールを保持する**: lease が切れても `state: away` かつ `resume_at` 内なら
  再募集しない。会話の文脈を持つ本人が翌朝戻って続けるほうが、引き継ぎより安い。
  ただし (a) `away_grace`（既定: `resume_at` + 2 時間）を超過、(b) オーナーが deadline や
  予算残から待てないと判断して roster から外した、のどちらかで通常の再募集（§6.5）に戻る。
- **不在ロール宛のメッセージ**: inbox は永続なので溜まるだけで失われない。
  `question_timeout` は宛先が away の間は停止し、代わりに送信側ランナーが
  「宛先は `resume_at` まで不在」と system メッセージで即応する（無駄な待ちと
  owner エスカレーションの濫発を防ぐ）。
- **ターンの原子性（all-or-nothing）**: 1 ターンの成果（アクション封筒の適用 ＋ events 追記
  ＋ status 更新）は**単一コミットにまとめてから push** する。途中で電源が落ちても、
  バスには「ターン全部」か「何もなし」しか残らない。ローカルに未 push コミットが残った場合は
  再起動時に rebase して push し、それも失われた場合はそのターンをやり直すだけで
  整合は壊れない（真実は常にバス、プロセスはステートレス）。
- **予算との整合**: 予算は実質実行時間（§3.2）なので、不在時間は予算を消費しない。
- **オーナーノードの停止**: 受入・裁定・roster 更新が止まるだけで、amigo の作業と会話は
  継続する。owner 宛エスカレーションは inbox に滞留し、復帰後に順に処理される（§12）。

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
  4. 予算チェック（§3.2）: hard 超過 → 作業ターンを開始しない（integrator/受入を除く）。
     soft 超過 → wrap-up モードのプロンプト前置きに切替
  5. プロンプト合成:
       ロール定義（roles/<id>.yaml）+ design-doc.md + 決定記録
       + 新着メッセージ + 自分の直近 status（引き継ぎメモ含む）+ artifacts 一覧
  6. agent CLI 実行（agents/<name>.json プラグイン経由）→ アクション封筒
  7. 封筒を検証して適用 + events へ cli_seconds 追記 + status 更新（ハートビート・
     引き継ぎメモ）— **ここまでを単一コミットに**まとめる（ターン原子性、§6.6）
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

- integrator の完了で reviewing へ。
- 受入判定は `acceptance` ポリシーで選ぶ:
  `manual`（人が確認して `accept`/`reject` サブコマンド）／
  `agent`（オーナーの agent CLI が design doc と突き合わせて判定）／
  `codd-gate`（[一貫性ゲート](./codd-gate-design.md)を通す。将来拡張）。
- `reject` はフィードバックを `inbox/all` 相当（全体宛 review メッセージ）と
  `decisions.jsonl` に残して working へ差し戻す。**done を作るのはオーナーの accept のみ**。
- **accept はオーナーホームの納品棚へ搬出する**（push 型納品、§8.4）。
  `collect <mid> --out <dir>` は残るが、納品棚とは別の場所へ改めてコピーするための補助。

### 8.3 コード成果物の扱い

deliverable がリポジトリ変更の場合、artifacts にパッチを置くのではなく、
`mission.yaml` の `workspace.repo` に対象リポジトリを宣言し、amigo は
`amigos/<mission-id>/<role-id>` ブランチで作業して push、integrator が統合ブランチ
`amigos/<mission-id>/integration` へマージして deliverable の `MANIFEST.json` から参照する。
バスに巨大ファイルを積まない（バスは調整とメッセージ、コードは git、の分離）。

### 8.4 納品棚 — accept 後の永続先（実装済み）

バスの `deliverable/` は受け渡しの場であり gc の対象なので、accept し忘れ・collect し忘れが
成果物の喪失に直結する。accept という明示の意思表示があった時点で、owner デーモンが
`<home>/deliveries/<mission-id>/` へ搬出し、納品書 `delivery.json`
（正典: [`schemas/delivery.schema.json`](../../schemas/delivery.schema.json)）と
受領一覧 `<home>/DELIVERY.md` を書く。agent-project の archive + DELIVERY.md と同じ二段構え。

- **正本の置き場は種別で分ける**: 文書・調査結果・小さい画像は本体を納品棚へ、コードは
  `workspace.repo` の統合ブランチが正本で納品棚には参照だけ（§8.3）、10MB 超のファイルも
  搬出せず参照だけ残す（納品書の `exported: false`）。
- 納品棚は gc の既定では消さない（`gc --deliveries-keep-days N` の明示時のみ）。
  バスと違い、受け取った成果物の唯一の置き場になるため。
- **agent-dashboard は提示面**: reviewing のミッションで `deliverable/` を有界に読んで
  プレビューし（markdown は本文・画像はインライン）、accept / reject は commands 投函で
  owner デーモンへ委ねる。納品棚は読むだけ。dashboard がバスへ書かない規律（§4.2）は不変。

詳細な設計判断（却下案含む）は
[`2026-07-19-agent-amigos-deliverable-delivery-design.md`](../plans/2026-07-19-agent-amigos-deliverable-delivery-design.md)。

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

`roles.yaml`（post 時にオーナーが渡す。正典スキーマは `schemas/mission.schema.json` に置く）:

```yaml
mission:
  title: 社内 FAQ ボットの MVP
  goal: design-doc.md の受入基準をすべて満たす FAQ ボットを納品する
  deadline: 2026-07-24T09:00:00Z          # 任意。超過で owner へ通知
  assignment_policy: first-come            # first-come | owner-picks
  staffing_policy: self-staff              # self-staff | wait | fail
  staffing_timeout: 600
  acceptance: manual                       # manual | agent | codd-gate
  convergence:                             # 収束条件（§3.2）
    done_when: reviewer-approved
    quiescence_turns: 3
    review_rounds: 2
  budget:                                  # 予算 = 実質実行時間（§3.2）
    execution_minutes: 120
    per_role_turns: 30
    on_exhausted: wrap-up
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

### 10.1 チームビルディング — ミッションから役割表を設計する

役割ミッション表を人が書く従来経路（`post`）はそのままに、**ミッション（ゴール／design doc）
だけ**から上記の役割表を設計する入口を追加する（`build-team`）。設計手順自体は
`.github/skills/team-builder/` に **team-builder スキル**として切り出し、agent-amigos は
それを呼び出して実現する（手順の単一ソース化。人＝Claude Code / Copilot からも同じスキルで
設計できる）。

- **実装**: `agent_amigos/teambuilding.py`。スキル本文を探索（インストール済みスキルホーム →
  リポジトリ内 `.github/skills` → 組み込みフォールバック）してプロンプト化し、
  `agentcli.run_agent`（全 LLM 呼び出しの単一チョークポイント、§9）で 1 回実行する。
- **出力契約**: `{"mission": {…任意…}, "roles": [ … ]}`（`mission.schema.json` の roles と同形）。
  返ってきた設計は `normalize_mission` で検証してから、そのまま `post` 経路へ合流する
  （以降のアサイン → 協働 → 統合 → 受入は従来と一切変わらない）。
- **入口**: CLI `build-team`（既定はドライラン、`--out` 保存 / `--post` 公示）と、commands
  ドロップ `{"command":"build-team", …}`（dashboard のミッション画面が「チームビルディング」
  モードで投函する）。design doc が無ければゴールから最小 design doc を自動生成する。
- 設計には実際の agent CLI が要る（`stub` / 未指定は不可）。予算・締切は不確かなら省略し既定に委ねる。

これは agent-amigos に「入力の前段（役割設計）を自動化する」もう 1 つの入口を足すだけで、
コアのプロトコル（状態のファイル導出・決定的 claim・収束会計）には手を入れない。

---

## 11. CLI コマンド体系

```
agent-amigos           # サブコマンド省略 = serve（常駐起動）。cwd がホーム
agent-amigos serve     [--hub/--no-hub] [--manual-claim] [--cycles N]
agent-amigos init-bus  (--dir <path> | --git <url> [--subdir amigos] | --hub <url>)
agent-amigos post      --design design-doc.md --roles roles.yaml     # オーナー: 公示（役割指定）
agent-amigos build-team --goal "..." [--design d.md] --agent-cli claude [--out f | --post]
                                                                     # オーナー: ミッションから役割設計（§10.1）
agent-amigos join      [--roles r1,r2] [--agent-cli codex] [--tags python,frontend]
                                                                     # 参加ノード: 常駐デーモン
agent-amigos run       --mission <mid> --role <role> [--once]        # 単発 amigo（デバッグ用）
agent-amigos status    [<mid>]                                       # 名簿・各ロール状態・未回答質問
agent-amigos collect   <mid> --out ./deliverable                     # オーナー: 成果物取り出し
agent-amigos accept    <mid> / reject <mid> --feedback "..."         # オーナー: 受入判定
agent-amigos budget    add <mid> --minutes 60                        # オーナー: 予算追加（§3.2）
agent-amigos say       <mid> --to <role|all> --body "..."            # 人がバスに直接発言（介入）
agent-amigos cancel    <mid>
agent-amigos gc        [--keep-days 14]
```

`say` は「人もチームの一員として口を挟める」ための穴。owner 名義（または `--as` 指定ロール名義の
`human:` プレフィクス付き）でメッセージを書く。

### 11.1 常駐運用（ホーム）— agent-project と同じ実施方法

**サブコマンド省略 = 常駐起動（serve）**を既定にする（agent-project の `run --watch` 既定と
同じ流儀 — PC 起動時に立ち上げっぱなしにして cwd を面倒見る daemon 用途が一級市民）。

- **ホーム**: cwd。設定探索は agent-project と同じ
  `<cwd>/agent-amigos.*` → `<cwd>/.agents/agent-amigos.*` → `~/.agents/agent-amigos.*`
  （優先順位 CLI > 設定 > 既定・雛形は `tools/agent-amigos/agent-amigos.yaml.example`）。
  プロジェクトローカルの設定があるディレクトリがホーム。グローバル設定時のホームは cwd。
  設定ファイルは agent-dashboard の **自動発見マーカー**を兼ねる。
- **cwd = バス = hub**: 既定でホーム自身がローカルバス（`missions/` がホームに生える）。
  設定 `hub.serve: true` で同じバスを hub として公開し、他ノードは
  `--bus hub+http://<host>:<port>` で参加できる。ローカル直接書き込みと hub 公開の
  共存は hub 側の**再走査**（PUT を経ないファイル変更・削除を索引へ反映。/list 時と
  long-poll 中に間隔律速で走る）が担保する。
- **指示のファイル取り込み**: `<home>/.agents/agent-amigos/commands/*.json` を毎サイクル
  取り込む（agent-project の `commands/` と同じ「プロセス間 API を持たない・結合は
  データ×一方向」方式）。コマンドは `post`（タスク依頼 — design 本文と役割ミッション表を
  受けて公示。design はホームの `designs/` へ永続化）/ `claim`（**手動引き受け** —
  ポリシーに従い claim / 応募。owner-picks でオーナー自身なら応募＋即時確定）/
  `assign` / `accept` / `reject` / `cancel` / `say`。処理済みは削除・失敗は
  `.rejected` へ改名（壊れた指示を無限に噛み続けない）。
- **manual_claim**: 自動応募を止め、手動引き受け（commands / dashboard）だけで回すモード。
  引き受け済みロールのターン実行・オーナー職務（self-staff はミッション側のポリシー）は
  従来どおり動く。
- **agent-dashboard**: Amigos タブがホームを自動発見し、**タスク依頼**（post フォーム）と
  **手動引き受け**（募集中ロールの「引き受け」ボタン）を commands 投函で行う。
  dashboard がバスへ直接書くことは引き続き無い（書くのはホームの commands ドロップのみ —
  バスの書き込み所有権 §4.2 は破らない）。

---

## 12. 障害・回復のまとめ

| 障害 | 検知 | 回復 |
|---|---|---|
| 計画的ノード停止（定時シャットダウン） | SIGTERM フック → `state: away`（`resume_at` 付き） | ロール保持のまま翌朝続きから。`away_grace` 超過やオーナー判断で再募集へ（§6.6） |
| ターン途中の電源断 | —（検知不要） | ターン原子性（単一コミット）によりバスは「全部か無か」。そのターンのやり直しのみで不整合なし（§6.6） |
| amigo ノード死亡（クラッシュ） | ハートビート途絶 → lease 失効（away 宣言なし） | ロール再募集。後任が status（引き継ぎメモ）/events/artifacts から引き継ぎ（§6.5） |
| 予算枯渇（ミッション） | events の `cli_seconds` 総和（決定的会計、§3.2） | soft で wrap-up モード → hard で現状統合・`partial: true` 納品。オーナーは budget add で追加可 |
| 予算枯渇（ノード） | 共有台帳の合計（§3.3） | そのノードの amigo だけ paused（`[node-budget]`・owner へ一度通知）。ミッションは継続。上限引き上げ/期間更新で自動復帰 |
| 会話の空転（誰も進まない） | `quiescence_turns` の静穏化検知（§3.2） | 現状で統合へ進め、良し悪しは受入判定に委ねる |
| agent CLI ハング | プラグイン timeout | ターン失敗 → リトライ、繰り返せば paused ＋ owner 通知 |
| quota/auth/env | `[agent-error:*]` タグ | amigo paused・環境修復後に続きから（§9） |
| 質問の放置 | `question_timeout` | ランナーが owner へ自動エスカレーション（§7.3） |
| push 競合（GitBus） | git | 名義分割で原理的に稀。`pull --rebase` リトライで吸収 |
| deadline（wall-clock）超過 | `mission.yaml` の `deadline` | owner へ通知（自動 fail にしない）。owner が予算・収束条件を見直すか cancel |
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
| **P0（MVP）** | LocalBus / post・join・run・status・collect / claim 型アサイン＋self-staff / inbox＋all チャンネル / アクション封筒ランナー（ターン原子性込み） / **収束条件・予算会計（`cli_seconds` 集計・wrap-up・quiescence）** / integrator＋manual 受入 / agent-cli プラグイン | 1 マシン上で 3 ロール（architect・impl・reviewer）が相互に質問・レビューしながら成果物を 1 つ納品し、`collect` で取り出せる。予算枯渇で partial 納品に収束する。stub CLI（LLM なし）でプロトコルのユニットテストが通る |
| **P1（分散）** | GitBus（**専用バスリポジトリ＋ミッション別ブランチ**、state_git の同期規律を移植）/ lease・ハートビート・ロール再募集 / **away プロトコル（graceful offboard・引き継ぎメモ・away_grace）** / エラートリアージ連携 / adaptive interval / budget add・say・cancel・gc | 2 ノード（別 PC）でロール分担して P0 と同じ納品ができる。ノードを 1 つ kill してもロール再募集で完走し、**定時シャットダウン→翌朝再起動をまたいでも同じ担当が続きから完走する** |
| **P2（拡張）** | HubBus＋hub サーバ / owner-picks（応募 → `assign` で確定・自己補充両対応） / acceptance: agent（自動判定・review_rounds 超で人へエスカレーション） / agent-dashboard の Amigos タブ / `schemas/mission.schema.json` 正典化 | ✅ 完了（hub 経由の 2 ノード E2E・claim 競合・認証・gc をテストで検証。dashboard はローカルバス / GitBus workdir / hub データディレクトリを読める）。**残**: acceptance: codd-gate（将来拡張・§8.2） |
| **拡張（team-builder パターン起点）** | seats>1（G1）／ 決定的集約 aggregate: majority/consensus/weighted-vote/approval-count/gather（G2）／ done_when: consensus ／ 同期討論 rounds:N ＋ 通信トポロジ topology（G3）／ 実行中の動的編成 restaff add/prune（G5）。オーケストレーションパターンのカタログ（`.github/skills/team-builder/patterns/`）と自動選択 | ✅ 実装済み（`agent-amigos-teambuilder-patterns.md`）。**残**: 探索木（G4・agent-flow 委譲）／ pairwise-rank（ranker ロールで対応）／ 自律的な自己組織化ループ（restaff を叩く上位ワークフロー） |

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
6. **予算は wall-clock でなく実質実行時間** — 定期シャットダウン運用で「PC が落ちていた
   時間」に予算が溶けるのを防ぎ、消費＝LLM を実際に回した時間に一致させる。会計は
   バス上の追記ログ（events の `cli_seconds`）の総和で、どのノードが計算しても同じ値になる
   （中央の台帳・集計プロセス不要）。
7. **バスは専用リポジトリ＋ミッション別ブランチ** — 成果物リポジトリの履歴を汚さない・
   ミッション間で同期コストとコンフリクトが交差しない・gc がブランチ削除で済む。
   同期の運用規律（間隔律速・rebase リトライ・force push 禁止・自パスのみステージ）は
   agent-project / agent-flow の state_git から流用し、3-way 裁定は所有権分割で不要化した。
8. **計画停止はクラッシュと区別する（away プロトコル）** — 会話の文脈を持つ本人の復帰を
   既定とし、引き継ぎコストを払うのは grace 超過かオーナー判断のときだけ。ターンを
   単一コミットの all-or-nothing にすることで、任意のタイミングの電源断でも
   バスに壊れた中間状態が残らない。
9. **予算は二層（ミッション = 依頼側 / ノード = 請負側）で、ノード側はツール横断の
   共有台帳** — ノードの CLI 資源は amigos 専有ではないため、上限は「そのミッションで
   いくら」ではなく「このマシンで合計いくら」も必要になる。台帳をデータ契約
   （schemas/node-budget）にすることで、定常業務・project・flow が同じ台帳に記帳でき、
   agent-dashboard は契約を読むだけで依頼側・請負側どちらの管理面にもなれる。
   ノード予算超過はノードの都合なので amigo を paused に留め、ミッションを殺さない。
