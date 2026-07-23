# agent-board — 委譲公示板（依頼の公示・入札・成果一本化の分散バックエンド）

専用リポジトリ（またはローカル dir）を **委譲公示板** にして、エージェント処理の依頼を公示し、
**登録ノードの入札（先勝ち claim）** で引き受け先を決める、エンジン非依存の一段下の層です。
[agent-flow](../agent-flow/) / [agent-amigos](../agent-amigos/) の分散処理の裏側として機能します
（両エンジンのコードは import せず、各エンジンの入力契約＝flow inbox / amigos-command を
ファイルとして書いて引き渡す — 結合はデータ契約のみ）。

- 正典設計: [`docs/plans/2026-07-23-delegation-board-distributed-bidding-design.md`](../../docs/plans/2026-07-23-delegation-board-distributed-bidding-design.md)
- 契約: [`schemas/board.schema.json`](../../schemas/board.schema.json)（板のレイアウト）／
  [`schemas/delegation.schema.json`](../../schemas/delegation.schema.json)（公示封筒）／
  [`schemas/repos.schema.json`](../../schemas/repos.schema.json)（ノードの担当リポジトリ宣言）

## これは何を解決するか

| 要件 | 実現 |
|------|------|
| 依頼の受付・管理、配信/ポーリングでの入札 | 専用 git リポジトリ（板）＋ delegation 封筒。配信はポーリング（GitBus）が既定、long-poll / forge webhook は加速オプション |
| 先勝ち入札・投機同時実行の許容・成果はひとつ | agent-flow / agent-amigos と同一の名前空間付き claim ＋ `(ts, who)` 決定的タイブレーク。成果は `result.json` 1 つに一本化 |
| 成果物リポジトリによるノード側の入札選別 | ノード登録 `nodes/<node-id>.json` に repos レジストリ（repos.schema.json）を載せ、公示の `workspace.url` / `requires.repos` と `(url, path, base)` identity で照合 |
| flow / amigos の裏側・スキーマ踏襲 | 落札＝「どのノードがホストするか」だけを決める。落札ノードが flow inbox 投函 / amigos post（オーナーとして公示）へ引き渡す |

## 板のレイアウト（`schemas/board.schema.json`）

```
nodes/<node-id>.json                  # 参加ノードの能力宣言（各ノードが自分名義のみ）
delegations/<id>/
  post.json                           # 公示（delegation 封筒 op=post・依頼者のみ）
  bids/<who>.json                     # 入札（名前空間付き claim・各ノードが自分名義のみ）
  award.json                          # owner-picks の落札確定（依頼者のみ）
  status/<who>.json                   # 実行ハートビート（実行ノードが自分名義のみ）
  results/<who>.json                  # 成果報告（投機なら複数・各自名義）
  result.json                         # 一本化された確定成果（依頼者のみ・成果はこれ 1 つ）
  cancelled.json                      # 中止マーカー（依頼者のみ）
```

書き込み所有権をパス単位で分割するため git でもコンフリクトしません。真実は板の上のファイルに
あり、中央（forge）は転送のみ。落札の決定・成果の一本化は各ノードが同じファイル集合から
決定的に導きます（中央が落ちても壊れない）。

## 使い方

```bash
# ノードを登録し、入札デーモンを回す（cwd に agent-board.yaml を置く。省略時 = serve）
agent-board register
agent-board                       # = serve（板を巡回 → 入札 → 落札 → ローカルエンジンへ引き渡し）

# 依頼を公示する（依頼者側）
agent-board post --workload flow --goal "X を実装" --workspace git@h:team/app.git
agent-board post --file delegation.json          # delegation 封筒をそのまま

# 一覧・落札（owner-picks）・中止・掃除
agent-board status
agent-board award <id> <node>
agent-board cancel <id>
agent-board gc
```

設定は [`agent-board.yaml.example`](agent-board.yaml.example) を参照。`board:` に
`git+<url>` を指定すると専用リポジトリを板にします（ローカル dir なら 1 マシン運用）。
`repos:` にこのノードが担当するリポジトリを宣言すると、そのリポジトリの公示にだけ入札します。

## テスト

```bash
python3 -m unittest discover -s tools/agent-board/tests
```

LLM 不要（stub のみ）。決定的タイブレークで勝者は 1 人・lease 失効で再入札・repos 照合による
入札選別・flow inbox / amigos-command への引き渡し・投機の first-valid 一本化・owner-picks の
応募/落札を検証します。

## インストール

```bash
bash install.sh            # ~/.local/bin/agent-board へ zipapp で配置
```

標準ライブラリのみ（pip 依存なし）。git は分散モード（`board: git+<url>`）で必要です。

## 各ツールとの結合

- **agent-flow**: 落札ノードが `<flow_bus>/inbox/<id>.json` を書く（`submit_request` 契約）。
  flow は `delegation:{id, board}` を run の `meta.json` へ引き回し、来歴を表示できる。
- **agent-amigos**: 落札ノードが `<amigos_home>/.agents/agent-amigos/commands/` へ post を投函する
  （オーナーとして公示）。amigos のノードは `agent-amigos.yaml` の `repos:` でロール
  `requires.repos` を選別できる（板と同語彙）。
- **agent-project**: `agent-project board-offload <task> --board <repo>` で、ルーティングで
  workspace を確定したバックログタスクを板へ委譲できる。
- **agent-dashboard**: 委譲タブが `delegation.boardRepos` の板へ post/award/cancel を投函し、
  板の入札・落札・成果を横断一覧に揃える（`src/features/delegation/main/board-adapter.js`）。
