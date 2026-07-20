---
name: team-building
description: agent-amigos 向けに、ミッション（ゴール）だけから最適なノード役割と各役割へ渡すプロンプトを設計し、mission.schema.json 準拠のロールミッション表を組み立てるスキル。「チームを組んで」「役割を設計して」「ミッションだけ投げてチームビルディングして」「このゴールに必要なロールを作って」「役割ミッション表を自動生成して」「誰に何をやらせるか決めて」で発動する。従来の post（役割指定）はそのまま、ロール未定のミッションから設計する。build-team コマンド（CLI / dashboard）から呼ばれる機械可読な出力契約を持つ。
metadata:
  version: 1.0.0
  tier: stable
  category: orchestration
  tags:
    - team-building
    - agent-amigos
    - role-design
    - multi-agent
    - staffing
    - orchestration
---

# team-building — ミッションから最適なチームを設計する

## 概要

達成したいこと（ミッション）だけを入力に、それを**協働で仕上げるのに最適なロール構成**と、
**各ロールへ渡すミッション文（＝そのノードのプロンプト）**を設計する。出力は
[agent-amigos](../../../tools/agent-amigos/) の**ロールミッション表**（`mission.schema.json` の
`roles` と同形）で、そのまま `agent-amigos post --roles <file>` に流せる。

agent-amigos の従来入力（design doc ＋ ロールミッション表）は変えない。本スキルは
「ロールを人が書く」代わりに「ミッションからロールを設計する」段だけを担い、以降は従来経路
（公示 → アサイン → 協働 → 統合 → 受入）に合流する。

- **人が使うとき**: このファイルの手順に従って設計し、`roles.yaml` / `roles.json` を出力する。
- **agent-amigos が呼ぶとき**: `build-team` コマンドが本スキルの手順をプロンプト化して agent CLI
  に投げ、下記「出力契約」の JSON を受け取ってロールミッション表として公示する。
  正典実装は [`agent_amigos/teambuilding.py`](../../../tools/agent-amigos/agent_amigos/teambuilding.py)。

設計正典: [`docs/designs/agent-amigos-design.md`](../../../docs/designs/agent-amigos-design.md) §10（ロールミッション表）。

---

## 適用条件

以下に**いずれも**該当するときに実行する。1つでも外れる場合は人へ確認する。

- [ ] ゴール（達成したい状態）が言語化されている
- [ ] ロール構成が未定、または既存のロール表を作り直したい
- [ ] 成果物を**複数の役割で分担・協働**して仕上げる価値がある（単発の 1 タスクなら分割しない）

既に承認済みのロールミッション表があるならそれを使う（本スキルは発動しない）。

---

## 入力（ミッションブリーフ）

| 項目 | 必須 | 説明 |
|------|:---:|------|
| `goal` | ✔ | ミッション全体の目標（完了したときの状態）。全 amigo のプロンプトに載る |
| `title` | | ミッションの短い名前 |
| `design` | | 進め方・受入基準・制約を書いた design doc 本文（あれば正典として尊重する） |
| `constraints` | | 予算・締切・技術制約・体制上の制約など |
| `capabilities` | | 使えるノードの能力（`tags` の候補）と `agent_cli` の選択肢。分かる範囲でよい |
| `agent_cli` | | ロールの既定 agent CLI（未指定なら各ロールで省略＝ノード既定に委ねる） |

`design` が無くても `goal` から設計できる。ある場合は design doc を正典として、そこに書かれた
受入基準・非機能要件・スコープ外を必ずロール設計へ反映する。

---

## プロセス

### Step 1: ゴールを成果物へ分解する

ゴールを「最終的にバスへ積まれるべき成果物（deliverables）」の集合へ写像する。
例: API を作る → `architecture.md`（設計）, `src/`（実装）, `tests/`（テスト）, レビュー指摘。
成果物が見えないゴールは、まず「完了の定義」を 1〜3 個の具体物として言語化する。

### Step 2: 必要な専門性を同定する

各成果物を仕上げるのに必要な**専門性の軸**（設計・実装・データ・フロント・レビュー・文書 …）を
挙げる。**軸が重なるものは 1 ロールに束ねる**。人数を増やすほど調整コスト（質問往復）が増えるため、
「最小の人数で成果物を過不足なく覆う」ことを目標にする（→ [設計原則](#設計原則)）。

### Step 3: ロールを設計する（責務を直交させる）

同定した専門性を**責務の重ならないロール**へ落とす。各ロールに:

- `id`: 短い識別子（`architect` / `impl-api` / `reviewer` …。`all` / `owner` は予約語で不可、`/` 不可）
- `title`: 人が読む役割名
- `deliverables`: そのロールが書く成果物（artifacts 内の相対パス／ディレクトリ）
- `required`: そのロールが欠けると収束できないなら `true`（必須の最小化 — [原則](#設計原則)）
- `requires.tags`: そのロールに要るノード能力（例 `{tags: [python]}`）。`capabilities` と整合させる
- `agent_cli`: 指定があれば載せる（未指定はノード既定）
- `approver`: レビュー承認者なら `true`（`done_when: reviewer-approved` の承認ゲート）
- `collaborates_with`: 主に会話する相手ロールの id（順序の強制ではなく会話ヒント）

**integrator は書かなくてよい**（省略時はオーナーノードが組み込みロールとして自己補充する）。
明示したい場合のみ `{id: integrator, builtin: integrator}` を置く。

### Step 4: 各ロールのミッション文（プロンプト）を書く

`mission` フィールドが**そのノードへ渡るプロンプト**になる。次を満たすように書く:

- **何を作り、何を根拠にするか**（design doc / 他ロールの成果物）を明示する
- **完了条件**（このロールがいつ `declare_done` してよいか）を書く
- **誰と何を会話するか**（質問の投げ先・レビュー依頼先）を促す
- 命令口調で簡潔に。amigo は受け取ったミッションと design doc と新着メッセージから自律的に動く
- 迷う設計判断は owner へ `decision-request` を上げるよう促す（勝手に決めさせない）

各ロールのミッションは独立して読めること（他ロールの文脈が無くても着手できる粒度）。

### Step 5: 収束条件と予算を見積もる（任意・保守的に）

必要なら `mission` ブロックに収束条件・予算を提案する（未指定は agent-amigos の既定に委ねる）:

- `convergence.done_when`: レビュー承認で締めるなら `reviewer-approved`（`approver` ロールが要る）
- `budget.execution_minutes`: 規模から控えめに見積もる（0 = 無制限。過大より過小＋追加を推奨）

**予算・収束を確信できないときは省略する**（既定が安全側に働く）。勝手に厳しい締切を課さない。

### Step 6: 自己検証する

出力する前に次を確認する（[出力契約](#出力契約)の機械検証は agent-amigos 側の `normalize_mission`
が行うが、意味の妥当性は本スキルの責任）:

- [ ] すべての deliverables が、いずれかのロールに割り当たっている（取りこぼしなし）
- [ ] ロールの責務が重なっていない（同じ成果物を 2 ロールが書かない）
- [ ] `required: true` は本当に欠かせないロールだけ（過剰必須は staffing を詰まらせる）
- [ ] `collaborates_with` の相手が実在するロール id を指している
- [ ] `requires.tags` が入力の `capabilities` と矛盾しない（存在しない能力を要求しない）
- [ ] `done_when: reviewer-approved` を使うなら `approver: true` のロールが 1 つ以上ある
- [ ] 各 `mission` 文だけを読んで担当が着手できる

---

## 出力契約

**agent-amigos の `build-team` はこの JSON だけをパースする。前後に説明文を付けない。**

```json
{
  "mission": {
    "title": "（任意）ミッション名",
    "goal": "（任意）ゴールの再掲・明確化",
    "convergence": { "done_when": "reviewer-approved" },
    "budget": { "execution_minutes": 120 }
  },
  "roles": [
    {
      "id": "architect",
      "title": "アーキテクト",
      "mission": "design doc を正として構成を確定し、他ロールの設計質問に回答する。迷う判断は owner へ decision-request を上げる。",
      "deliverables": ["architecture.md"],
      "required": true,
      "agent_cli": "claude"
    },
    {
      "id": "impl-api",
      "title": "API 実装",
      "mission": "architecture.md に従い API を実装し、単体テストを通す。設計の疑問は architect へ question を送る。",
      "deliverables": ["src/", "tests/"],
      "required": true,
      "requires": { "tags": ["python"] },
      "collaborates_with": ["architect"]
    },
    {
      "id": "reviewer",
      "title": "レビュアー",
      "mission": "全ロールの成果物を design doc と突き合わせてレビューし、指摘を返す。基準を満たしたら approve する。",
      "required": true,
      "approver": true
    }
  ]
}
```

- `roles` は**1 つ以上必須**。`mission` ブロックは任意（省略時は agent-amigos の既定）。
- キー・値の意味は `mission.schema.json` に従う。未知キーは無視される（前方互換）。
- `mission.title` / `mission.goal` は入力ブリーフの値を上書きしたいときだけ載せる。

正典スキーマ: [`schemas/mission.schema.json`](../../../schemas/mission.schema.json)。
ロールミッション表の雛形: [`tools/agent-amigos/roles.yaml.example`](../../../tools/agent-amigos/roles.yaml.example)。

---

## 設計原則

- **最小人数**: ロールは少ないほど調整コストが小さい。1 ロールで覆えるなら分けない。
- **責務の直交**: 2 ロールが同じ成果物・同じ判断を持たない。境界を明確にする。
- **必須の最小化**: `required: true` は「欠けると収束不能」なロールだけ。あれば嬉しい程度は
  `required: false`（self-staff / staffing のボトルネックを作らない）。
- **能力整合**: `requires.tags` は入力 `capabilities` の範囲で。存在しない能力を要求して
  未充足で詰ませない。
- **承認ゲートは 1 本**: レビュー承認で締めるなら `approver` を明確に 1 ロールへ寄せる。
- **プロンプトは自律の起点**: `mission` 文は「指示の全部」ではなく「自律判断の起点」。
  細かな手順の列挙より、ゴール・根拠・完了条件・会話相手を書く。
- **保守的な予算**: 迷ったら予算・締切は省略して既定に委ねる。過小に見積もり、足りなければ
  `agent-amigos budget add` で足す運用を前提にする。

詳細な設計ヒューリスティクスと例: [`references/design-heuristics.md`](references/design-heuristics.md)。
