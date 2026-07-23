# agent-board — 委譲公示板（リポジトリ＋契約）

**agent-board は実行プロセスを持ちません。** 専用リポジトリ（＝板）と、その上のファイルレイアウト
契約（[`schemas/board.schema.json`](../../schemas/board.schema.json)）だけです。依頼の公示・入札・
落札・成果の一本化に必要な**処理は既存ツールが担います**（新しいデーモンやサーバは増やしません）:

| 役割 | 誰がやるか |
|------|-----------|
| 公示（post を板へ書く） | 依頼側 — `agent-project board-offload` ／ `agent-dashboard` の委譲タブ |
| 入札・落札・引き渡し | 請負側 — **`agent-flow` / `agent-amigos` の常駐デーモン**が板を巡回し、`workload` が自分向きの公示に入札して、勝てば自分のエンジンへ取り込む |
| 受入・成果の一本化 | 依頼側 — dashboard / CLI が `result.json` を書く |

真実は板の上のファイルにあり、中央（forge）は転送のみ。落札の決定・成果の一本化は各ノードが
同じファイル集合から決定的に導きます（中央が落ちても壊れない）。結合はデータ契約のみ — 各ツールは
互いのコードを import せず、板のレイアウト（この契約）を読み書きするだけです。

- 正典設計: [`docs/plans/2026-07-23-delegation-board-distributed-bidding-design.md`](../../docs/plans/2026-07-23-delegation-board-distributed-bidding-design.md)
- 契約: [`schemas/board.schema.json`](../../schemas/board.schema.json)（板のレイアウト）／
  [`schemas/delegation.schema.json`](../../schemas/delegation.schema.json)（公示封筒）／
  [`schemas/repos.schema.json`](../../schemas/repos.schema.json)（ノードの担当リポジトリ宣言）

## 板の作り方

板は「ただの git リポジトリ」です。オンプレ forge（Gitea / Forgejo / GitLab CE）や ssh bare repo に
専用リポジトリを 1 つ切るだけ。1 マシン運用ならローカルディレクトリでも構いません。

```bash
# 例: bare リポジトリを板にする
git init --bare /srv/git/agent-board.git
# 各ノードはこの URL を board として設定する（agent-flow / agent-amigos の設定 board:）
```

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

書き込み所有権をパス単位で分割するため git でもコンフリクトしません。入札は agent-flow /
agent-amigos と同一仕様の名前空間付き claim ＋ `(ts, who)` 決定的タイブレーク（同じ仕様・別実装）。
勝者は有効（lease 内）な入札のうち `(ts, who)` 最小の 1 件に決定的に定まります。

## 各ツールの結合点

- **agent-flow**（請負・入札）: 設定 `board:` を与えると、デーモンが板を巡回して `workload: flow` の
  公示に repos/tags 照合で入札し、勝てば自分の `inbox/<id>.json` へ取り込む（`agent_flow/board.py`）。
  取り込んだ run の `meta.json` には来歴 `delegation:{id, board}` が残る。
- **agent-amigos**（請負・入札）: 設定 `board:` を与えると、デーモンが板を巡回して `workload: amigos`
  の公示に repos/tags 照合で入札し、勝てば**オーナーとしてミッションを公示**する（`agent_amigos/board.py`）。
  ロール `requires.repos` は `agent-amigos.yaml` の `repos:` で選別する。
- **agent-project**（依頼・公示）: `agent-project board-offload <task> --board <repo>` で、ルーティングで
  workspace を確定したバックログタスクを板へ委譲する。
- **agent-dashboard**（依頼・公示・観測）: 委譲タブが `delegation.boardRepos` の板へ post/award/cancel を
  投函し、板の入札・落札・成果を横断一覧に揃える（`src/features/delegation/main/board-adapter.js`）。
