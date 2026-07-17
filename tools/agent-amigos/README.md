# agent-amigos — 役割駆動マルチエージェント協働ツール

複数のエージェントに**別々の役割（ロール）とミッション**を与え、相互にコミュニケーション
しながら**一つの成果物**を作り上げる協働基盤。

- オーナーノードが「design doc ＋ 役割ミッション表」で**ミッションを公示**すると、
  参加ノードの**アサイン受付**が始まる。
- 参加ノードはロールを claim して **amigo** になり、型付きメッセージ
  （質問・回答・レビュー・決定）でやり取りしながら成果物を積み上げる。
- 統合ロール（integrator）が 1 つの deliverable にまとめ、**オーナーに返却**・受入判定。
- オーナーは**収束条件と予算（実質実行時間）**を指示でき、amigo はその範囲内で自律的に収束する。
- **1 ノードでも完結**（未充足ロールの自己補充 self-staff）。
- LLM 実行は agent CLI プラグイン契約（`agents/<name>.json`）を利用:
  kiro / claude / copilot / codex は組み込み、cursor / ollama 等は定義ファイルで追加。
  `stub` は LLM なしのプロトコル検証用。

設計正典: [`docs/designs/agent-amigos-design.md`](../../docs/designs/agent-amigos-design.md)
（本実装は **P0（MVP）＋ P1（GitBus 分散・away プロトコル）**。hub / dashboard 連携は P2）。

## クイックスタート（1 ノード・stub）

```bash
cd tools/agent-amigos
python3 agent-amigos.py init-bus --bus /tmp/amigos-bus

# 公示してそのままオーナーノードとして常駐（staffing_timeout 後に自己補充）
python3 agent-amigos.py post --bus /tmp/amigos-bus \
  --design design-doc.md --roles roles.yaml.example --serve --agent-cli stub

# 別端末から状態確認・受入
python3 agent-amigos.py status --bus /tmp/amigos-bus
python3 agent-amigos.py collect <mission-id> --bus /tmp/amigos-bus --out ./deliverable
python3 agent-amigos.py accept <mission-id> --bus /tmp/amigos-bus
```

実運用では `--agent-cli claude` などを指定する（ロール別の `agent_cli` が優先）。

## 参加ノード

```bash
# 能力タグと使う CLI を宣言してデーモン参加（合うロールへ first-come で応募）
python3 agent-amigos.py join --bus <bus> --tags python,frontend --agent-cli codex

# 特定ロールだけに絞る
python3 agent-amigos.py join --bus <bus> --roles impl-api --agent-cli codex
```

## 複数 PC 分散（GitBus）

オンプレ git remote に**専用のバスリポジトリ**を切り、`--bus git+<url>` で参加する。
ミッションは `mission/<mid>` ブランチに分離され（`main` は公示インデックスのみ）、
参加したミッションのブランチだけが clone される。gc はブランチ削除。

```bash
# 中央（オンプレ GitLab / Gitea / bare repo）にバスリポジトリを用意
git init --bare /srv/git/amigos-bus.git

# オーナーノード（PC-A）
python3 agent-amigos.py post --bus git+ssh://git@gitlab.local/team/amigos-bus.git \
  --design design-doc.md --roles roles.yaml --serve --agent-cli claude

# 参加ノード（PC-B）
python3 agent-amigos.py join --bus git+ssh://git@gitlab.local/team/amigos-bus.git \
  --tags python --agent-cli codex
```

同期の作法は state_git（agent-project / agent-flow）の規律を流用:
pull は間隔律速（claim の勝者確認だけは常に最新化）・push 競合は `pull --rebase` →
再 push の指数バックオフ・**force push しない**・1 ターン = 1 コミット（原子性）。

## 定期シャットダウン耐性（away プロトコル）

ノードのデーモンは SIGTERM / Ctrl-C で **graceful offboard** する: 自分の全 amigo を
`state: away`（`resume_at` 付き）にして最後の push をしてから終了。away 中は
lease が切れても **`resume_at` + grace（既定 2 時間）までロールを保持**し、翌朝
デーモンを再起動すれば同じ担当が続きから再開する。grace 超過・away 宣言なしの
クラッシュは通常の再募集に戻る（後任は status の引き継ぎメモ・artifacts から再開）。
予算は実質実行時間ベースなので、不在時間は予算を消費しない。

## コマンド一覧

| コマンド | 説明 |
|---|---|
| `init-bus --bus <dir>` | バスを初期化 |
| `post --design <md> --roles <yaml> [--serve]` | ミッション公示（オーナー）。`--serve` で常駐 |
| `join [--roles ...] [--tags ...] [--agent-cli ...]` | 参加ノードのデーモン |
| `run --mission <mid> --role <role> [--once]` | 単発 amigo（デバッグ用） |
| `status [<mid>]` | 名簿・状態・予算消費・未回答質問 |
| `collect <mid> --out <dir>` | deliverable の取り出し（オーナー） |
| `accept <mid>` / `reject <mid> --feedback "..."` | 受入 / 差し戻し（オーナー） |
| `budget add <mid> --minutes N` | 予算追加（オーナー） |
| `say <mid> --to <role\|all\|owner> --body "..."` | 人の介入発言 |
| `cancel <mid>` / `gc [--keep-days N]` | 中止 / 終了済みの掃除 |

## 仕組みの要点

- **状態はファイルの存在から導出**（open / working / integrating / reviewing / done …）。
  書き換え競合を作らない（agent-flow と同じ原則）。
- **アサインは決定的**: `assignments/<role>/<node>.json` の名前空間付き claim ＋
  `(ts, node)` タイブレーク。lease 失効で自動再募集。
- **LLM はバスに直接書かない**: ランナーがアクション封筒
  （`send / write_artifact / update_status / declare_done`）を検証して代書する。
  パス逸脱・不正宛先・越権 approve は棄却され events に記録される。
- **予算会計は決定的**: 各ターンの `cli_seconds` を `events/<who>.jsonl` へ追記し、
  総和が消費。soft で wrap-up モード、hard で partial 統合（`on_exhausted: fail` なら終端）。
- **収束**: 全必須ロール完了（＋approver 承認）／静穏化（quiescence）／予算枯渇 wrap-up の
  いずれか早いもの。差し戻し（reject）はラウンドを上げて再作業。

## テスト

```bash
python3 -m unittest discover -s tools/agent-amigos/tests
```

LLM 不要（stub のみ・stdlib unittest）。claim の決定的タイブレーク、E2E
（質問/回答 → 成果物 → 承認 → 統合 → 受入）、差し戻しラウンド、予算 wrap-up / fail、
静穏化収束、封筒検証、owner エスカレーションを検証する。

## 環境変数

| 変数 | 意味 |
|---|---|
| `AGENT_AMIGOS_BUS` | `--bus` の既定値 |
| `AGENT_AMIGOS_NODE` | ノード ID（既定: `~/.agent/amigos/node.json` に自動採番） |
| `AGENT_AMIGOS_LEASE` | claim lease 秒（既定 600） |
| `AGENT_AMIGOS_AWAY_GRACE` | away の resume_at からの猶予秒（既定 7200） |
| `AGENT_AMIGOS_PULL_INTERVAL` | GitBus の pull 間隔律速秒（既定 15） |
| `AGENT_AMIGOS_STUB_COST` | stub の 1 ターン消費秒（予算テスト用、既定 0.01） |
| `KIRO_AGENTS_DIR` | agent CLI プラグイン定義の探索先（agent-flow と共通） |
