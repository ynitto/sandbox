---
name: skill-selector
description: 単一タスクに最適なプライマリスキルと補助スキルを選択・推薦するメタスキル。「どのスキルを使えばいい？」「スキルを選んで」などで発動し、skill-mentor・scrum-master からインライン実行もされる。補助スキル（self-checking・test-driven-development 等）の付加評価も担う。
metadata:
  version: 1.7.0
  tier: core
  category: orchestration
  tags:
    - skill-selection
    - orchestration
    - recommendation
---

# Skill Selector

ユーザーの**単一タスク**を分析し、利用可能なスキルの中から最適なプライマリスキルと補助スキルの組み合わせを特定・提案するメタスキル。多フェーズにまたがるプロジェクト全体のスキル選定はオーケストレーター（scrum-master 等）が担う。

---

## パス解決

このSKILL.mdが置かれているディレクトリを `SKILL_DIR`、その親を `SKILLS_DIR` とする。スクリプトは `scripts/` から実行する。スキルが複数の場所に存在する場合、ワークスペース側を優先する。

---

## 呼び出しコンテキスト

このスキルは2つの方法で利用される:

| 利用形態 | 呼び出し元 | Step 1 |
|---|---|---|
| **インライン実行** | skill-mentor (Phase 3)・scrum-master (Phase 2/5) がSKILL.mdを読んで直接実行 | タスク定義が既に渡されている場合は **省略可** |
| **直接呼び出し** | ユーザーが直接「スキルを選んで」と依頼 | **必須** |

> **インライン実行時の注意**: skill-mentor や scrum-master からタスク定義（ゴール・フェーズ・対象・制約）が渡されている場合は Step 1 をスキップして Step 2 から始める。これらのオーケストレーターとのスキル選定の**役割分担**:
> - **skill-mentor / scrum-master**: タスクの明確化・ユーザーとの対話・実行フロー管理
> - **skill-selector**: スキルの探索・評価・推薦（補助スキルの付加判定を含む）

---

## 選択プロセス

### Step 1: タスクを分析する

ユーザーのリクエストから以下を読み取る:

- **ゴール**: 何を達成したいか（作る・直す・調べる・改善する・整理する）
- **フェーズ**: タスクが開発ライフサイクルのどの段階か
- **対象**: コード・ドキュメント・設計・データ・インフラのどれか
- **複雑度**: 単一スキルで完結するか、複数スキルの連携が必要か

### Step 2: 利用可能なスキルを探索する

`discover_skills.py` を実行して、現在使えるスキルを列挙する:

```
python scripts/discover_skills.py
```

このスクリプトは `<AGENT_HOME>/skills/` と `.github/skills/` の両方を走査し、各スキルの `name`・`description`・`category`・`tags`・`tier` を出力する。**Windows・macOS 両対応**。新しいスキルが追加されても自動的に検出される。

スクリプトが実行できない環境では、利用可能なスキルディレクトリを直接確認する。

### Step 3: カテゴリとタグで動的に絞り込む

`--group-by-category` フラグで、実際のスキルメタデータに基づくカテゴリ別一覧を取得する:

```
python scripts/discover_skills.py --group-by-category
```

各スキルの `category` と `tags` を使ってタスクに関連する候補を絞り込む。タスクフェーズとカテゴリの対応例（参考）:

| タスクフェーズ | よく対応するカテゴリ |
|---|---|
| 構想・設計 | design |
| 実装 | implementation |
| テスト・検証 | implementation（testing タグ） |
| デバッグ | debug |
| レビュー・品質 | review |
| ドキュメント | research（documentation タグ） |
| オーケストレーション | orchestration |
| スキル管理 | meta |
| リサーチ | research |

**重要**: タクソノミーのカテゴリ・タグは各スキルの SKILL.md フロントマターから動的に取得する。このテーブルは参考例に過ぎず、`--group-by-category` の出力結果を正とする。新しいスキルの `category` と `tags` を読んでタスクへの適合性を判断すること。

### Step 3.5: ltm-use で過去の実績を参照する

Step 3 の絞り込みと並行して、`recall_memory.py` で類似タスクの過去実績を検索する:

```bash
python ${LTM}/recall_memory.py \
  --query "[タスクゴール・タスクフェーズのキーワード]" \
  --category "skill-selection-result" \
  --limit 5
```

取得できた場合は以下の情報を抽出する:
- **成功した組み合わせ**: 以前使われて良い結果を出したスキルセット
- **失敗・非推奨の組み合わせ**: 試みたが効果が低かったパターン
- **タスク種別との対応**: 類似ゴールにどのスキルが有効だったか

> **注意**: 実績は参考情報に留め、最終判断はスキルの `description` を読んで行う。
> 記憶が存在しない場合はスキップして Step 4 へ進む。

### Step 4: 組み合わせを評価する

> **⚠️ 除外ルール**: `category: orchestration` のスキル（scrum-master・skill-mentor・gitlab-idd 等）は推薦対象から除外する。これらは skill-selector を呼び出す側であり、推薦に含めると相互依存が生じる。オーケストレーターの選択はユーザーまたは呼び出し元が行う。

**最初に複合タスクの可能性を検討し、その上で単一スキルを選ぶかどうかを判断する**:

- **シーケンシャル**: フェーズが複数にまたがる（設計→実装→テストなど）→ 複数スキルを順番に使う
- **並列**: 独立した側面を同時対応（例: フロントエンド実装と API 設計）→ 複数スキルを並行して使う
- **単一スキル**: タスクが1つのカテゴリに明確に収まり、他フェーズへの波及がない場合のみ

**複合タスクと判断する基準**:
- ゴールが2フェーズ以上にまたがる（「設計して実装する」「実装してレビューする」など）
- タスクに複数の専門観点が必要（機能実装＋セキュリティチェックなど）
- スキルの `description` に「後続スキルに委譲」「別スキルと組み合わせ」などの記述がある

**⚠️ アンチパターン**: 複合可能性を検討せずにいきなり単一スキルを選ぶのは誤り。必ず複合タスクの可能性を評価してから、単一スキルで本当に十分かを確認する。単一スキルだけで進めた場合、設計フェーズのスキルを省いてテストが不十分になったり、レビュー系スキルを省いてコード品質の問題が残ったりと、後続フェーズで手戻りが発生しやすい。

組み合わせパターンの詳細は [references/combinations.md](references/combinations.md) を参照。

#### Step 4.5: 補助スキルを付加する

プライマリスキルが決まったら、補助スキルは **2種類** に分けて判断する。最大件数は **2件まで** とし、内訳は以下に固定する。

- **原則付加するもの**: `self-checking` を 0 または 1件
- **条件が合えば付加するもの**: `test-driven-development` / `contract-driven-development` / `risk-driven-development` / `failure-driven-development` の中から 0 または 1件

`agent-reviewer` は skill-selector の推薦対象に含めない。レビューの起動判断と実行は呼び出し元のオーケストレーターが担う。

**補助スキルの種類と付加基準**:

| 種類 | 補助スキル | 付加するタイミング | 付加しない場合 |
|---|---|---|---|
| 原則付加 | `self-checking` | コード・ドキュメント・設計の成果物が生まれる実装系・作成系タスク全般 | 調査・リサーチのみで成果物がないタスク |
| 条件付きで1つ選択 | `test-driven-development` | 下記「TDD が有効なケース」参照 | 下記「TDD を省略するケース」参照 |
| 条件付きで1つ選択 | `contract-driven-development` | 下記「契約駆動が有効なケース」参照 | 下記「契約駆動を省略するケース」参照 |
| 条件付きで1つ選択 | `risk-driven-development` | 下記「リスク駆動が有効なケース」参照 | 下記「リスク駆動を省略するケース」参照 |
| 条件付きで1つ選択 | `failure-driven-development` | 下記「失敗駆動が有効なケース」参照 | 下記「失敗駆動を省略するケース」参照 |

**判断フロー**:

```
プライマリスキルが実装系・作成系？
  YES → self-checking を原則付加する
         ただしスキルが存在しない場合は
         「完了後に成果物を自己評価し、問題があれば修正してから次工程へ進む」と自然文で補う
  NO  → self-checking は付加しない

条件付き補助スキルは 1つだけ選ぶ。判定順は以下:

1. プライマリスキルがコード新規実装で、受け入れ条件を先にテスト化できる？
   YES → test-driven-development を選ぶ

2. プライマリスキルが API・モジュール境界・I/O 契約を含み、先に境界を固定した方が安全？
  YES → contract-driven-development を選ぶ

3. プライマリスキルが失敗時の振る舞い・回復手段・劣化運転を含み、異常系を先に固めた方が安全？
  YES → failure-driven-development を選ぶ

4. プライマリスキルが変更系で、順序設計・最小検証・停止条件の明示が重要？
  YES → risk-driven-development を選ぶ

5. 上記に当てはまらない、または該当スキルが存在しない
   → 条件付き補助スキルはなし
```

**test-driven-development の付加判断**:

*TDD が有効なケース（付加を推奨）*:
- **ドメインロジック・ビジネスルール**の新規実装（純粋関数・計算ロジック・バリデーション）
- **API / ライブラリ / モジュール**の新規作成（インターフェースが先に決まっている）
- **仕様が明確**で受け入れ条件を先にテストとして書ける
- **リグレッション防止が重要**（既存機能に影響するコアロジックの追加）
- **CI/CD のカバレッジゲート**が設定されている（一定カバレッジを維持しなければならない）
- ユーザーが TDD・テストファーストを **明示的に要求**している

*TDD を省略するケース（付加しない）*:
- **UI コンポーネント中心**の実装（視覚的検証が主で自動テストが困難）
- **既存コードのリファクタリング**（テストが既に存在する）
- **プロトタイプ・探索的コーディング**（仕様が未確定で頻繁に変わる）
- **インフラ・設定ファイル**（スクリプト・CI 設定・Dockerfile 等）
- **テスト環境が未整備**（テストランナー未設定 / カバレッジツール未導入）
- タスクのスコープが小さく TDD のオーバーヘッドがコストに見合わない

**contract-driven-development の付加判断**:

*契約駆動が有効なケース（付加を推奨）*:
- **API / SDK / ライブラリ / モジュール境界**を持つ実装
- **外部連携や複数コンポーネント連携**があり、I/O を先に固定したい
- **request / response / payload / 関数シグネチャ**を先に決めると手戻りが減る
- **breaking / non-breaking / deprecation** を意識した方がよい変更
- **契約テストや schema validation** に落とし込める変更
- ユーザーが **契約から決めたい / API を先に固めたい / I/O を固定して進めたい** と明示している

*契約駆動を省略するケース（付加しない）*:
- **内部実装のみ**で明確な境界がない
- **探索的実装**で、先に契約を固定するとむしろ邪魔になる
- **変更順序や安全停止**が主論点で `risk-driven-development` の方が適切
- **文書更新のみ**で契約対象が存在しない
- **異常系・回復戦略**が主論点で `failure-driven-development` 相当の方が適切

**risk-driven-development の付加判断**:

*リスク駆動が有効なケース（付加を推奨）*:
- **変更や実装はあるが、TDD の前に順序設計が必要**（UI、設定、CI、インフラ、限定リファクタ等）
- **影響範囲が読みづらい変更**（複数ファイル・複数レイヤー・既存依存が絡む）
- **仕様不確実性がある**ため、最初に最小検証を置いた方が安全
- **ロールバックや停止条件を先に決めたい**変更
- **手動確認・静的チェック・限定実行**で段階的に確かめられる変更
- ユーザーが **安全に進めたい / 段階導入したい / まず危ないところから潰したい** と明示している

*リスク駆動を省略するケース（付加しない）*:
- **純粋な調査のみ**で変更が発生しない
- **文書更新のみ**で実行順序や停止条件が不要
- **影響範囲が極小**で、順序設計を追加するほどではない
- **異常系・回復戦略の設計が主論点**で `failure-driven-development` 相当の方が適切

**failure-driven-development の付加判断**:

*失敗駆動が有効なケース（付加を推奨）*:
- **外部依存・非同期処理・可用性要件**があり、失敗時の振る舞いが重要
- **再試行、タイムアウト、fallback、degraded mode** を先に定めた方が安全
- **正常系より異常系の詰め不足が致命的**になりやすい変更
- **利用者影響や運用影響**を事前に設計へ織り込みたい
- **エラーレスポンス、異常系 UI、監視・検知**を先に決めたい
- ユーザーが **異常系から考えたい / 回復手段を先に決めたい / 障害時の振る舞いを先に固めたい** と明示している

*失敗駆動を省略するケース（付加しない）*:
- **内部ロジックのみ**で異常系が自明
- **すでに起きた障害の原因調査**が主眼で `systematic-debugging` の方が適切
- **変更順序や停止条件**が主論点で `risk-driven-development` の方が適切
- **文書更新のみ**で失敗時挙動の設計が不要

> **注意**: 補助スキルの構成は「self-checking 系 0または1件 + 条件付き 0または1件」に固定する。呼び出し元のオーケストレーター（skill-mentor・scrum-master・gitlab-idd）は、時間・コスト制約に応じて省略を最終判断してよい。

### Step 4.5（必須）: 実行戦略の確信度を評価し council_hint を出力する

skill-selector は推薦を確定する役割を持つ。council-system による実行戦略の合議は**呼び出し元（skill-mentor / scrum-master 等）の責務**である。このステップでは skill-selector が実行戦略の複雑度・不確実性を評価し、呼び出し元が council-system を起動すべきかを判断できる**構造化シグナル（`council_hint`）**を `notes` に出力する。

以下の条件のいずれかに該当する場合、`notes` に `council_hint` を追加する:

| 条件 | 追加する理由 |
|---|---|
| `execution_plan.groups` が 2 つ以上 | 並列・依存関係に実行順序リスクがある |
| `supporting_skills.conditional.mode == skill` | 補助スキルの省略可否判断が必要 |
| `primary_skills` が 2 件以上（複合タスク） | 複数スキルの協調実行に競合リスクがある |
| `past_examples.warnings` に失敗パターンがある | 過去の失敗から学ぶ追加判断が必要 |

`council_hint` の書式:

```
"council_hint: [理由]。呼び出し元（skill-mentor/scrum-master 等）で council-system による実行戦略の合議を推奨。"
```

条件に該当しない場合は `council_hint` を notes に追加しない。

### Step 5: 推薦を提示する

呼び出し元が安定して扱えるよう、**必ず以下の出力契約で返す**。説明文を自由記述で散らさず、キー名を固定する。

#### 出力契約

```yaml
selection_status: success | failure
goal: "タスク要約"
primary_skills:
  - name: "skill-name"
    role: "このスキルが担う役割"
supporting_skills:
  principle:
    mode: skill | fallback | none
    name: "skill-name または null"
    instruction: "自然文フォールバックまたは null"
    timing: "before-primary | after-primary | null"
    reason: "推薦理由または null"
  conditional:
    mode: skill | fallback | none
    name: "skill-name または null"
    instruction: "補助指示または null"
    timing: "before-primary | after-primary | null"
    reason: "推薦理由または null"
execution_plan:
  groups:
    - id: "A"
      after: []
      skills: ["skill-A", "skill-B"]
notes:
  - "重複・競合・注意点"
past_examples:
  success:
    - "類似タスクで成功した組み合わせ"
  warnings:
    - "以前効果が低かったパターン"
```

#### 出力ルール

- `primary_skills`: 推薦するプライマリスキルを 1件以上入れる。1件だけなら配列長 1。
- `supporting_skills.principle`: 原則付加枠。`self-checking` を返す場合は `mode: skill`、スキル不在で自然文指示を返す場合は `mode: fallback`、不要なら `mode: none`。
- `supporting_skills.conditional`: 条件付き枠。呼び出し元は候補名を解釈せず、このオブジェクトをそのまま扱う。
- `execution_plan.groups`: 実行順序と並列グループ。呼び出し元はこの順序を壊さずに利用する。
- `notes`: なければ空配列を返す。Step 4.5 の評価で `council_hint` が生成された場合はその文字列を配列要素として含める。
- `past_examples`: なければ空配列を返す。

#### 呼び出し元への契約

- skill-mentor / scrum-master / gitlab-idd は、`primary_skills` から実行対象を作り、`supporting_skills` は**キー構造を保ったまま**保持・伝播する。
- `notes` に `council_hint:` で始まる要素が含まれる場合、呼び出し元は実行戦略の確定前に `council-system` を使って合議することを推奨する（強制ではなく、コスト・時間制約に応じて判断してよい）。
- レビューは skill-selector の返却値ではなく、オーケストレーターが `agent-reviewer` を直接呼び出して実施する。
- 呼び出し元は `self-checking` や `test-driven-development` などの具体名を前提に独自分岐しない。必要な分岐は `mode` と `timing`、および返却された `name` に対する実行だけで行う。

以下の形式でユーザーに提示する:

```yaml
selection_status: success
goal: "[ユーザーのタスク要約]"
primary_skills:
  - name: "skill-name"
    role: "このスキルが担う役割"
supporting_skills:
  principle:
    mode: skill
    name: "self-checking"
    instruction: null
    timing: "after-primary"
    reason: "実装成果物の自己評価が必要"
  conditional:
    mode: skill
    name: "contract-driven-development"
    instruction: null
    timing: "before-primary"
    reason: "境界条件と I/O 契約を先に固定した方が安全"
execution_plan:
  groups:
    - id: "A"
      after: []
      skills: ["contract-driven-development"]
    - id: "B"
      after: ["A"]
      skills: ["skill-name", "self-checking"]
notes:
  - "council_hint: execution_plan に2グループ以上の並列実行あり。呼び出し元（skill-mentor/scrum-master 等）で council-system による実行戦略の合議を推奨。"
  - "[スキルの重複・競合がある場合はここに記載]"
past_examples:
  success:
    - "[類似タスクで成功した組み合わせ]"
  warnings:
    - "[以前効果が低かったパターン]"
```

#### 推薦後: 実績を ltm-use に保存する

推薦を提示したら、条件に関わらず **skill-selector 自身が直ちに** 実績を保存する:

```bash
python ${SKILLS_DIR}/ltm-use/scripts/save_memory.py \
  --non-interactive --no-dedup \
  --category "skill-selection-result" \
  --title "[タスク種別]のスキル組み合わせ実績" \
  --summary "[使用スキル一覧]: 選択完了" \
  --content "[タスクゴール]\n使用スキル: [A, B, C]\n補助スキル: [X]\n備考: [選定理由・注意点]" \
  --conclusion "[このタスク種別に最適なスキル組み合わせの知見]" \
  --tags skill-selection,[タスク種別],[スキル名]
```

`${SKILLS_DIR}` は `SKILL_DIR`（このファイルの場所）の親ディレクトリ。`ltm-use/scripts/save_memory.py` が存在しない場合はスキップしてよい。

---

## ギャップへの対応

適切なスキルが見つからない場合:

1. **description を再読する** — 一見無関係に見えるスキルが対応している場合がある
2. **skill-creator を外部取得する** — 外部リポジトリからスキルを取得・インストールできる
3. **skill-creator を新規作成する** — 新しいスキルをゼロから作成する
4. **エージェント標準機能で対応する** — スキルなしで進め、必要に応じて記憶に残す（ltm-use）

---

## アンチパターン

- **過剰選択**: 「念のため」で多くのスキルを選ばない。タスクに最小限のスキルを選ぶ
- **静的マッピング依存**: 新しいスキルは description を読んで判断する。古い固定マッピングに頼らない
- **スキル強制**: 既存スキルで対応できない場合はエージェント標準機能を使う
