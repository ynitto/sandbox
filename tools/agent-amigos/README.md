# agent-amigos — 役割駆動マルチエージェント協働ツール

複数のエージェントに**別々の役割（ロール）とミッション**を与え、相互にコミュニケーション
しながら**一つの成果物**を作り上げる協働基盤。

- オーナーノードが「design doc ＋ 役割ミッション表」で**ミッションを公示**すると、
  参加ノードの**アサイン受付**が始まる。
- 役割表を書かず**ミッション（ゴール）だけを投げる「チームビルディング」**も選べる
  （従来の入力契約はそのまま並存）。[team-builder スキル](../../.github/skills/team-builder/)が
  最適な役割構成と各役割へ渡すプロンプトを設計し、そのまま公示経路へ合流する
  （`build-team` コマンド。詳細は[下記](#チームビルディングミッションだけ投げる)）。
- 参加ノードはロールを claim して **amigo** になり、型付きメッセージ
  （質問・回答・レビュー・決定）でやり取りしながら成果物を積み上げる。
- 統合ロール（integrator）が 1 つの deliverable にまとめ、**オーナーに返却**・受入判定。
- オーナーは**収束条件と予算（実質実行時間）**を指示でき、amigo はその範囲内で自律的に収束する。
- **1 ノードでも完結**（未充足ロールの自己補充 self-staff）。
- LLM 実行は agent CLI プラグイン契約（`agents/<name>.json`）を利用:
  kiro / claude / copilot / codex は組み込み、cursor / ollama 等は定義ファイルで追加。
  `stub` は LLM なしのプロトコル検証用。

設計正典: [`docs/designs/agent-amigos-design.md`](../../docs/designs/agent-amigos-design.md)
（本実装は **P0（MVP）＋ P1（GitBus 分散・away プロトコル）＋ P2（hub・owner-picks・
acceptance: agent）**。agent-dashboard 連携は `tools/agent-dashboard/src/features/amigos/`）。

## インストール

agent-project / agent-flow と同じく、標準ライブラリのみ（pip 依存なし）で、実体の
`agent_amigos/` パッケージを **zipapp 単一実行ファイル**にまとめて配置する。

```bash
bash tools/agent-amigos/install.sh              # ~/.local/bin/agent-amigos へ
bash tools/agent-amigos/install.sh --prefix /usr/local/bin
```

インストール後は `agent-amigos <サブコマンド>` で使える（以下の例はインストール後の形）。
リポジトリから直接動かす場合は `python3 tools/agent-amigos/agent-amigos.py <...>` でも同じ。

## 常駐運用（推奨 — agent-project と同じ実施方法）

**サブコマンドを省略すると常駐起動（`serve`）**になる。PC 起動時に立ち上げっぱなしにして
cwd の「ホーム」を面倒見る daemon 用途が一級市民（agent-project の `run --watch` 既定と同じ）。

```bash
mkdir team-amigos && cd team-amigos
mkdir -p .agents
cp <repo>/tools/agent-amigos/agent-amigos.yaml.example .agents/agent-amigos.yaml   # 設定（任意）
agent-amigos          # 常駐開始。cwd がホーム = 既定のローカルバス（missions/ がここに生える）
```

- **設定は `.agents/agent-amigos.yaml`**（cwd。`.yml` / `.json` 可・無くても動く）。
  探索順は `./agent-amigos.*` → `./.agents/agent-amigos.*` → `~/.agents/agent-amigos.*`。
  優先順位は CLI > 設定 > 既定。雛形: [`agent-amigos.yaml.example`](agent-amigos.yaml.example)。
- **cwd は hub として利用可能**: 設定 `hub.serve: true`（または `serve --hub`）で
  同じバスを hub として公開し、他ノードは `--bus hub+http://<host>:<port>` で参加できる
  （ローカル直接書き込みと hub 公開は共存する — hub が再走査で索引へ反映）。
- **指示のファイル取り込み**: `<home>/.agents/agent-amigos/commands/*.json` に JSON を
  1 ファイル置くと常駐デーモンが次サイクルで取り込む（agent-project の commands/ と同じ
  結合方式）。コマンド: `post`（タスク依頼）/ `build-team`（チームビルディング依頼）/
  `claim`（手動引き受け）/ `assign` / `restaff`（実行中の編成変更）/ `accept` / `reject` /
  `cancel` / `say`。処理済みは削除・失敗は `.rejected` へ改名。
- **agent-dashboard 連携**: `.agents/agent-amigos.yaml` が自動発見マーカー。dashboard の
  Amigos タブから**タスク依頼**（ミッション画面。「チームビルディング（役割を自動設計）」/
  「役割を自分で指定」のどちらのモードでも投函できる）と**手動引き受け**（募集中ロールの
  「引き受け」ボタン）を commands 投函で行える。`manual_claim: true` にすると自動応募を
  止めて手動引き受けだけで回せる。

```bash
# 手動引き受けの例（dashboard を使わない場合）
cat > .agents/agent-amigos/commands/claim.json <<'EOF'
{"command": "claim", "mission": "am-…", "role": "impl-api"}
EOF

# チームビルディング依頼の例（ロール未指定。team-builder スキルが役割を設計して公示）
cat > .agents/agent-amigos/commands/build-team.json <<'EOF'
{"command": "build-team", "title": "FAQ ボット", "goal": "社内 FAQ ボットの MVP を納品する",
 "capabilities": ["python"], "agent_cli": "claude"}
EOF
```

## クイックスタート（1 ノード・stub）

```bash
agent-amigos init-bus --bus /tmp/amigos-bus

# 公示してそのままオーナーノードとして常駐（staffing_timeout 後に自己補充）
agent-amigos post --bus /tmp/amigos-bus \
  --design design-doc.md --roles roles.yaml.example --serve --agent-cli stub

# 別端末から状態確認・受入（accept でホームの納品棚へ搬出される）
agent-amigos status --bus /tmp/amigos-bus
agent-amigos accept <mission-id> --bus /tmp/amigos-bus
agent-amigos deliveries -v
```

実運用では `--agent-cli claude` などを指定する（ロール別の `agent_cli` が優先）。

## チームビルディング（ミッションだけ投げる）

役割ミッション表を人が書く代わりに、**ミッション（ゴール／design doc）だけ**を渡すと、
[team-builder スキル](../../.github/skills/team-builder/)が最適な役割構成と各役割へ渡す
プロンプトを設計する。設計結果は従来と同じロールミッション表なので、そのまま公示経路
（アサイン → 協働 → 統合 → 受入）へ合流する。**従来の `post`（役割指定）はそのまま使える** —
チームビルディングはその前段を自動化するもう 1 つの入口。

```bash
# 1) ドライラン: 設計だけ見る（既定。公示しない）
agent-amigos build-team --goal "社内 FAQ ボットの MVP を納品する" \
  --capabilities python --agent-cli claude

# 2) 保存して調整してから従来経路で公示
agent-amigos build-team --goal "..." --agent-cli claude --out roles.yaml
#   （roles.yaml を確認・編集して）
agent-amigos post --design design-doc.md --roles roles.yaml --serve

# 3) 設計してそのまま公示（design 省略時はゴールから最小 design doc を自動生成）
agent-amigos build-team --goal "..." --title "FAQ ボット" --agent-cli claude --post --serve
```

- 設計には実際の agent CLI が要る（`--agent-cli claude/codex/…`。`stub` / 未指定は不可）。
- `--design <md>` を渡すと、それを正典として設計へ反映し公示にも使う。無ければゴールから
  最小の design doc を自動生成する。
- `--capabilities python,frontend` は使えるノード能力のヒント（`requires.tags` の候補）。
- dashboard / 常駐デーモン経由では `build-team` コマンドを投函する（下記「指示のファイル取り込み」）。

### オーケストレーションパターン

team-builder は、論文由来のマルチエージェント・オーケストレーションパターン（Self-Refine /
MetaGPT SOP / 討論 / Mixture-of-Agents 等）を agent-amigos のロール構成へ写した**設計テンプレ**を
[`.github/skills/team-builder/patterns/`](../../.github/skills/team-builder/patterns/) に持つ。
`build-team` はミッションの性質に応じて**高価値パターンから自動選択**し、そのロール骨格・収束
条件を出発点にチームを設計する（採用パターンは設計結果に記録される）。

```bash
agent-amigos build-team --list-patterns                 # 利用可能なパターン一覧
agent-amigos build-team --goal "..." --agent-cli claude --pattern metagpt-sop   # 明示指定
```

- **high**（8 種）は自動選択の対象。**medium**（25 種）は `--pattern <id>` / commands の
  `"pattern"` で明示指定したときだけ使う。
- 現実装（seats>1・投票・同期ラウンド・探索木・動的編成が無い）では写せないパターンと拡張提案は
  [`docs/designs/agent-amigos-teambuilder-patterns.md`](../../docs/designs/agent-amigos-teambuilder-patterns.md)。

## 参加ノード

```bash
# 能力タグと使う CLI を宣言してデーモン参加（合うロールへ first-come で応募）
agent-amigos join --bus <bus> --tags python,frontend --agent-cli codex

# 特定ロールだけに絞る
agent-amigos join --bus <bus> --roles impl-api --agent-cli codex
```

## 複数 PC 分散（GitBus）

オンプレ git remote に**専用のバスリポジトリ**を切り、`--bus git+<url>` で参加する。
ミッションは `mission/<mid>` ブランチに分離され（`main` は公示インデックスのみ）、
参加したミッションのブランチだけが clone される。gc はブランチ削除。

```bash
# 中央（オンプレ GitLab / Gitea / bare repo）にバスリポジトリを用意
git init --bare /srv/git/amigos-bus.git

# オーナーノード（PC-A）
agent-amigos post --bus git+ssh://git@gitlab.local/team/amigos-bus.git \
  --design design-doc.md --roles roles.yaml --serve --agent-cli claude

# 参加ノード（PC-B）
agent-amigos join --bus git+ssh://git@gitlab.local/team/amigos-bus.git \
  --tags python --agent-cli codex
```

同期の作法は state_git（agent-project / agent-flow）の規律を流用:
pull は間隔律速（claim の勝者確認だけは常に最新化）・push 競合は `pull --rebase` →
再 push の指数バックオフ・**force push しない**・1 ターン = 1 コミット（原子性）。

## hub サーバ（git が使えない環境・低レイテンシ向け、任意）

```bash
# オンプレに hub を立てる（データディレクトリはミッションレイアウトそのまま）
AGENT_AMIGOS_HUB_TOKEN=secret agent-amigos hub --data /srv/amigos --port 8765

# 各ノードは hub+<url> で参加（トークンは同じ環境変数）
AGENT_AMIGOS_HUB_TOKEN=secret agent-amigos join --bus hub+http://hub.local:8765 --agent-cli codex
```

hub は「所有者上書きのファイル置き場」で調整はしない（中央が落ちても壊れず、復帰後に
同期が追いつく）。差分はリビジョン付き list（long-poll 可）で取る。hub ホストの
agent-dashboard は `amigos.busDirs` にデータディレクトリを指すだけで全ミッションを読める。
オンプレ限定（TLS はリバースプロキシに委譲・クライアントはプロキシ設定を迂回して直接接続）。

## owner-picks（オーナーがアサインを確定する募集方式）

`assignment_policy: owner-picks` にすると claim は「応募」になり、確定はオーナーの
明示アサインだけが行う（自己補充は従来どおり動く）:

```bash
agent-amigos status <mid>                 # 未充足ロールへの応募者が並ぶ
agent-amigos assign <mid> impl-api        # 応募者一覧を表示
agent-amigos assign <mid> impl-api node-b # node-b に確定
```

## 納品棚 — 受け取った成果物の置き場

バスの `deliverable/` は受け渡しの場で gc の対象なので、accept が成立した時点で
オーナーノードが手元へ搬出する（push 型納品。collect し忘れで成果物を失わない）。

```
<home>/deliveries/<mission-id>/
  <role>/…            # 成果物の本体（文書・調査結果・画像）
  delivery.json       # 納品書（受入日時・受入者・partial・消費実行時間・ファイル一覧）
<home>/DELIVERY.md    # 受領一覧（1 ミッション 1 行）
```

正本の置き場は種別で分ける。コードは `workspace.repo` の統合ブランチが正本で、納品棚には
参照（repo / branch）だけを書く。10MB を超えるファイルも搬出せず参照だけ残す
（納品書の `exported: false`）。契約の正典は
[`schemas/delivery.schema.json`](../../schemas/delivery.schema.json)。

納品棚は gc の既定では消さない（受け取った成果物の唯一の置き場になるため）。
掃除したいときだけ `gc --deliveries-keep-days N` を明示する。
`accept` / `deliveries` / `gc` のホームは設定ファイルの位置で決まり、`--home` で上書きできる。

## 並列同一シート（seats）と集約（aggregate）

sampling/voting/ensembling 型のために、同じロールを**複数席**に増やして独立に走らせ、
成果を**決定的に集約**できる。

```yaml
roles:
  - id: solver
    mission: 問題を独立に解き、最終回答を ANSWER.md に書く（他席は見ない）。
    deliverables: [ANSWER.md]
    seats: 5             # solver#0..#4 の独立席へ展開（各席が同じロールを実行）
    aggregate: majority  # integrator が決定的に集約: majority | consensus | gather
```

- **seats: N（G1）** は公示（正規化）時に `solver#0..#N-1` の具体席ロールへ展開される。各席は
  通常の 1 席ロールなので、claim・roster・収束・統合・納品の既存機構をそのまま使う（1 ノードでも
  self-staff が全席を充足）。
- **aggregate（G2）** は integrator が席の回答（既定 `ANSWER.md`）を決定的に集約する方式:
  `majority`（多数決）/ `consensus`（全席一致の判定つき最頻値）/ `weighted-vote`（席の重み `SCORE`
  を回答ごとに合計）/ `approval-count`（`SCORE` 最大の候補席を選抜）/ `gather`（全席を集約収集）。
  `weighted-vote` / `approval-count` の席は数値を `SCORE`（`aggregate_score` で変更可）に書く。
  結果は `deliverable/<id>/AGGREGATE.{md,json}` と `MANIFEST.json` の `aggregates` に残る。
- **収束を早める**: `convergence.done_when: consensus`（＋ `consensus_ratio` / `consensus_min`）で、
  席グループが合意に達したら全席の完了を待たず収束する。
- **同期討論（G3）**: 席グループに `rounds: N` を付けると、各席が `round-<k>.md` を 1 ラウンドずつ書き、
  **全席が前ラウンドを出し切るまで次へ進めない**（ラウンドバリア）。最終ラウンドの主張が ANSWER.md
  になる。discuss → judge/aggregate の討論パターンに使う。`done_when: consensus` で合意時に早期終了。
- **通信トポロジ（G3 拡張）**: 討論席に `topology`（`complete`/`ring`/`star`/`tree`）を付けると、
  各席が毎ラウンド読む相手を制限できる（exchange-of-thought）。
- **動的編成（G5）**: `agent-amigos restaff <mid> --add <roles.yaml> --prune <id,...>` で、実行中に
  ロールを追加・停止できる（オーナー）。追加ロールは通常どおり募集・充足され、剪定ロールは収束・
  募集・実行から外れる。
- **自律コンダクタ**: `mission.conductor.enabled: true` にすると、オーナーノードが実行中に
  team-builder 的な判断で restaff を自動で回す（`acceptance: agent` と同じオーナー CLI ターン）。
  AgentVerse（再編成）・DyLAN（`SCORE` 評価 → 剪定）・meta-prompting（専門家招集）が agent-amigos 内で
  自律的に回る。ラウンド律速・`max_total_ops`・ガードレール（integrator/唯一の承認者/最後の必須
  ワーカーは守る）付き。既定は off。
- **探索木・動的分解（G4）**: Tree/Graph-of-Thoughts・LATS のような探索は役割協働の領分ではなく
  **agent-flow へ委譲**する。team-builder は探索が本質のミッションを見分けて `target: agent-flow` の
  委譲封筒（`delegation.schema.json` の workload=flow）を出力する（`build-team` が表示・保存し
  `agent-flow submit` を提示）。詳細:
  [`docs/designs/agent-amigos-teambuilder-patterns.md`](../../docs/designs/agent-amigos-teambuilder-patterns.md)。

## acceptance: agent（受入の自動判定）

`acceptance: agent` にすると、reviewing になった時点で**オーナーノードの agent CLI** が
design doc と deliverable を突き合わせて accept / reject を自動判定する。差し戻しは
通常のラウンドとして働き、`convergence.review_rounds` 回で止まって owner へ
decision-request をエスカレーションする（**無限ループを作らない・final を書けるのは
オーナーノードだけ**という不変条件は維持）。

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
| `serve`（省略時の既定） | 常駐起動: ノードデーモン + commands/ 取り込み + hub 公開（設定 hub.serve） |
| `init-bus --bus <dir>` | バスを初期化 |
| `post --design <md> --roles <yaml> [--serve]` | ミッション公示（オーナー）。`--serve` で常駐 |
| `build-team --goal "..." [--design <md>] --agent-cli <cli> [--pattern <id>] [--out <f>\|--post]` | チームビルディング: ミッションから最適な役割表を設計（パターン自動選択。`--list-patterns` で一覧、`--pattern` で明示指定。既定はドライラン、`--out` 保存 / `--post` 公示） |
| `join [--roles ...] [--tags ...] [--agent-cli ...]` | 参加ノードのデーモン |
| `run --mission <mid> --role <role> [--once]` | 単発 amigo（デバッグ用） |
| `status [<mid>]` | 名簿・状態・予算消費・未回答質問 |
| `collect <mid> --out <dir>` | deliverable を任意の場所へコピー（オーナー。納品棚とは別に取り出したいとき） |
| `accept <mid>` / `reject <mid> --feedback "..."` | 受入 / 差し戻し（オーナー）。accept は納品棚へ自動搬出する |
| `deliveries [-v]` | 納品棚（受領済みの成果物）の一覧 |
| `assign <mid> <role> [<node>]` | owner-picks の確定（省略時は応募者一覧。オーナー） |
| `restaff <mid> [--add <roles>] [--prune <id,...>]` | 実行中のチーム編成変更（G5・オーナー）: ロール追加 / 停止 |
| `hub --data <dir> [--port N] [--token T]` | 中継サーバの起動（オンプレ・任意） |
| `budget add <mid> --minutes N` | ミッション予算の追加（オーナー） |
| `budget node [--limit-minutes N] [--period day\|month\|total]` | このノードの上限の表示・設定（請負側。0 = 無制限） |
| `say <mid> --to <role\|all\|owner> --body "..."` | 人の介入発言 |
| `cancel <mid>` / `gc [--keep-days N]` | 中止 / 終了済みバスの掃除（納品棚は `--deliveries-keep-days` 明示時のみ） |

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
- **予算は二層**: ミッション予算（依頼側がバスに宣言）に加えて、**ノード予算（請負側）**を
  各ノードが設定できる。ノード予算はツール横断の共有台帳
  （`$AGENT_BUDGET_DIR`＝既定 `~/.agents/budget/`、契約は
  [`schemas/node-budget.schema.json`](../../schemas/node-budget.schema.json)）で、
  定常業務・agent-project・agent-flow・amigos の**合計**に上限を掛ける（**0 = 無制限**）。
  超過中はこのノードの amigo だけが paused（owner へ通知）になり、ミッションは
  他ノードで継続。上限を上げるか期間（day/month）が更新されると自動復帰する。
  agent-dashboard の **Amigos タブ**（`tools/agent-dashboard/src/features/amigos/`）が
  この契約（config を書く・ledger を読む）でミッション一覧（読み取り専用）と
  ノード予算の表示・上限編集を提供する — 依頼側・請負側どちらのノードでも同じ画面。
  記帳・抑制は amigos に加えて kiro-loop（routine）/ agent-project（project）/
  agent-flow（flow）にも組み込み済み（詳細は `schemas/README.md` の node-budget 節）。

  ```bash
  agent-amigos budget node                        # 現在の上限と消費内訳を表示
  agent-amigos budget node --limit-minutes 240    # このノードの上限を 240 分/日に
  agent-amigos budget node --limit-minutes 0      # 0 = 無制限
  agent-amigos budget node --amigos-minutes 60    # amigos ワークロードだけ内訳上限
  ```
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
| `AGENT_AMIGOS_BUS` | `--bus` の既定値（解決順: CLI > 環境変数 > `.agents/agent-amigos.yaml` の bus） |
| `AGENT_AMIGOS_NODE` | ノード ID（既定: `~/.agents/amigos/node.json` に自動採番） |
| `AGENT_AMIGOS_LEASE` | claim lease 秒（既定 600） |
| `AGENT_AMIGOS_AWAY_GRACE` | away の resume_at からの猶予秒（既定 7200） |
| `AGENT_AMIGOS_PULL_INTERVAL` | GitBus / HubBus の pull 間隔律速秒（既定 15 / 5） |
| `AGENT_AMIGOS_HUB_TOKEN` | hub の Bearer トークン（サーバ・クライアント共通） |
| `AGENT_BUDGET_DIR` | ノード予算の設定・台帳の置き場所（既定 `~/.agents/budget`。ツール横断契約） |
| `AGENT_AMIGOS_STUB_COST` | stub の 1 ターン消費秒（予算テスト用、既定 0.01） |
| `KIRO_AGENTS_DIR` | agent CLI プラグイン定義の探索先（agent-flow と共通） |
