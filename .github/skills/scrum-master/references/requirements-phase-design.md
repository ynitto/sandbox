# 要件定義フェーズ設計案

scrum-master に要件定義フェーズを追加するための設計検討ドキュメント。

## 目次

- [現状の課題](#現状の課題)
- [設計の論点](#設計の論点)
- [設計案比較](#設計案比較)
  - [案A: scrum-master に Phase 0 を追加](#案a-scrum-master-に-phase-0-を追加)
  - [案B: 別スキル入口 + 自動連携パターン](#案b-別スキル入口--自動連携パターン)
  - [案C: scrum-master の Phase 2 を拡張（曖昧度判定ゲート）](#案c-scrum-master-の-phase-2-を拡張曖昧度判定ゲート)
- [推奨案](#推奨案)
- [アジャイル要件定義手法の統合](#アジャイル要件定義手法の統合)
  - [手法マトリクス](#手法マトリクス)
  - [requirements-definer への統合設計](#requirements-definer-への統合設計)
- [requirements.json スキーマ拡張案](#requirementsjson-スキーマ拡張案)
- [実装タスク](#実装タスク)

---

## 現状の課題

### 1. scrum-master が曖昧な指示を受け取った場合の挙動

```
ユーザー: 「ECサイト作って」
    ↓
Phase 1: スキル探索（問題なし）
    ↓
Phase 2: バックログ作成 ← ★ ここで曖昧なまま分解される
```

- Phase 2 はプロンプトから直接タスク分解するため、曖昧な入力では**不完全または的外れなバックログ**が生成される
- ユーザーの本当の意図（対象ユーザー、スコープ、優先度、制約）が不明なまま進む

### 2. requirements-definer が孤立している

- 既に `requirements-definer` スキルと `requirements.json` スキーマが存在する
- `subagent-templates.md` に呼び出しテンプレートも定義済み
- しかし scrum-master のフロー（Phase 1〜7）に**組み込まれていない**
- ユーザーが明示的に「要件定義して」と言わない限り発動しない

### 3. 要件 → バックログの変換が未定義

- `requirements.json`（要件リスト）から `plan.json`（バックログ）への変換ロジックがない
- 受け入れ条件（Given/When/Then）が done_criteria にどうマッピングされるか不明

---

## 設計の論点

| # | 論点 | 選択肢 |
|---|------|--------|
| 1 | **配置** | scrum-master 内に統合 vs 別スキルのまま |
| 2 | **発動条件** | 常に実行 vs 曖昧度に応じて条件分岐 |
| 3 | **入口** | scrum-master の中間フェーズ vs 独立入口 → scrum-master に接続 |
| 4 | **要件定義手法** | 現状の要件リストのみ vs ジャーニーマップ/ユーザーストーリー等を追加 |
| 5 | **対話の深さ** | 最小限の質問 vs 段階的に深掘り（曖昧度に比例） |

---

## 設計案比較

### 案A: scrum-master に Phase 0 を追加

Phase 1 の前に新しいフェーズを挿入し、要件定義を必ず通過させる。

```
Phase 0: 要件定義（新規）
    ↓ requirements.json
Phase 1: スキル探索
    ↓
Phase 2: バックログ作成（requirements.json → plan.json）
    ↓
Phase 3〜7: 従来通り
```

**Phase 0 の処理:**
1. requirements-definer サブエージェントを呼び出す
2. ユーザーと対話して requirements.json を生成
3. requirements.json を Phase 2 に渡す

**メリット:**
- フローが一本化される。ユーザーは「スクラムして」だけで要件定義から始まる
- Phase 2 が requirements.json を前提にできるため、バックログの質が上がる
- 既存のサブエージェントテンプレートをそのまま使える

**デメリット:**
- **明確な指示でも要件定義を通過する**ためオーバーヘッドが大きい
  - 例: 「README.mdを更新して」に対して要件定義は不要
- scrum-master の SKILL.md が肥大化する
- Phase 番号がずれる（既存の current_phase: 1〜7 との互換性）

---

### 案B: 別スキル入口 + 自動連携パターン

requirements-definer は独立スキルのまま。scrum-master は起動時に requirements.json の有無を確認し、なければ呼び出す。

```
パターン1: 順次呼び出し
  ユーザー: 「要件定義して」→ requirements-definer → requirements.json
  ユーザー: 「スクラムして」→ scrum-master が requirements.json を読み込む

パターン2: 自動検出
  ユーザー: 「スクラムして」
    → scrum-master 起動
    → requirements.json が存在する？
       Yes → Phase 2 で読み込んでバックログ化
       No  → 曖昧度を判定
              曖昧 → requirements-definer を呼び出し → requirements.json 生成 → Phase 2
              明確 → 直接 Phase 2
```

**メリット:**
- requirements-definer の独立性が保たれる（単体でも使える）
- 明確な指示のとき不要な対話をスキップできる
- Phase 番号の互換性を維持できる
- **段階的に導入可能**（まず requirements.json 読み込みだけ実装 → 後で自動検出追加）

**デメリット:**
- 曖昧度判定ロジックの実装が必要（LLM判断に依存）
- 2つのスキルの連携ポイントが暗黙的（requirements.json というファイル名の規約のみ）
- ユーザーが「スクラムして」と言ったとき要件定義が始まると混乱する可能性

---

### 案C: scrum-master の Phase 2 を拡張（曖昧度判定ゲート）

Phase 2 の冒頭に曖昧度判定を入れ、必要な場合だけ requirements-definer を呼び出す。

```
Phase 1: スキル探索
    ↓
Phase 2: バックログ作成（拡張）
    ├── Step 2a: 曖昧度判定
    │     判定基準:
    │     - ゴールが1文で明確に言えるか
    │     - 対象ユーザーが明確か
    │     - スコープが特定できるか
    │     - 具体的なアクションに分解可能か
    │
    ├── [曖昧] → Step 2b: requirements-definer 呼び出し → requirements.json 生成
    │                → Step 2c: requirements.json → バックログ変換
    │
    └── [明確] → Step 2d: 従来のバックログ作成（プロンプトから直接）
    ↓
Phase 3〜7: 従来通り
```

**曖昧度判定基準:**

| 判定項目 | 明確 | 曖昧 |
|----------|------|------|
| ゴール | 「REST APIのページネーションを実装して」 | 「ECサイト作って」 |
| 対象範囲 | 「src/api/users.tsに」 | 「Webアプリで」 |
| 完了条件 | 「テストが通ること」 | 記述なし |
| タスク数の推定 | 1〜3タスク | 5タスク以上 or 不明 |

**メリット:**
- 既存 Phase 構造を壊さない
- 曖昧なときだけ対話が入るため、UXが自然
- Phase 2 の責務として一貫している（「バックログを作る前準備」）
- requirements.json がある場合の読み込みも Phase 2 内で統一的に扱える

**デメリット:**
- Phase 2 の処理が複雑化する
- 曖昧/明確の閾値がグレーゾーンの場合の判断が安定しない
- ユーザーが意図的に曖昧にして探索的に進めたいケースへの対応が必要

---

## 推奨案

**案C（Phase 2 拡張 + 曖昧度判定ゲート）をベースに、案B の「requirements.json 自動検出」を組み合わせる。**

### 理由

1. **Phase 構造の安定性**: Phase 番号を変えないため、既存の plan.json との互換性を維持できる
2. **UX の自然さ**: 曖昧なときだけ対話が入り、明確なときはスキップされる
3. **段階的導入**: requirements.json 読み込み → 曖昧度判定 → アジャイル手法統合 の順で拡張可能
4. **独立性の維持**: requirements-definer は単体でも使えるスキルのまま残る

### 推奨する Phase 2 の改訂フロー

```
Phase 2: バックログ作成（改訂版）

  Step 2-0: requirements.json の存在チェック
    ├── [存在する] → Step 2-3 へ（requirements.json → バックログ変換）
    └── [存在しない] → Step 2-1 へ

  Step 2-1: 曖昧度判定
    以下の4項目を評価する:
    a. ゴールが1文で定義可能か
    b. 対象ユーザー/利用シーンが特定できるか
    c. スコープ（In/Out）が推定可能か
    d. 3タスク以内で分解可能か

    ├── [4項目すべて明確] → Step 2-4 へ（従来の直接バックログ作成）
    ├── [1〜3項目が不明確] → Step 2-2 へ（要件定義）
    └── [判断に迷う場合] → ユーザーに選択肢を提示:
          1. 要件を整理してから進める（推奨）
          2. このまま進める

  Step 2-2: requirements-definer 呼び出し
    - サブエージェントテンプレート「requirements-definer 呼び出し時」を使用
    - requirements.json が生成される

  Step 2-3: requirements.json → バックログ変換
    requirements.json の各要素を plan.json にマッピング:
    - goal → goal
    - functional_requirements → 各要件を1つ以上のタスクに分解
    - acceptance_criteria → done_criteria にマッピング
    - non_functional_requirements → 横断的なタスクまたはタスクの制約として反映
    - scope.out → バックログに含めない（確認用に記録）

  Step 2-4: 従来のバックログ作成
    プロンプトから直接タスク分解する（現行の Phase 2 と同じ）

  → 以降 Phase 3 へ
```

---

## アジャイル要件定義手法の統合

### 手法マトリクス

各手法の特性と、このスキルシステムへの適合度を評価する。

| 手法 | 目的 | 入力の曖昧度 | 対話回数 | スキルシステムとの相性 | 推奨 |
|------|------|-------------|---------|----------------------|------|
| **ユーザーストーリー** | 「誰が何をしたいか」の定義 | 高 | 2〜3回 | 高（→ タスク分解に直結） | ★★★ |
| **ユーザーストーリーマッピング** | ストーリーの全体像と優先順位付け | 高 | 3〜5回 | 高（→ バックログ順序に直結） | ★★★ |
| **受け入れ条件 (Given/When/Then)** | 完了条件の明確化 | 中 | 1〜2回 | 高（→ done_criteria に直結） | ★★★ |
| **カスタマージャーニーマップ** | ユーザー体験の全体設計 | 高 | 3〜5回 | 中（要変換。体験→機能→タスク） | ★★☆ |
| **ペルソナ定義** | 対象ユーザーの具体化 | 高 | 1〜2回 | 中（直接タスクにはならない） | ★★☆ |
| **MoSCoW 優先度** | スコープの優先順位付け | 中 | 1〜2回 | 高（→ priority に直結） | ★★★ |
| **イベントストーミング** | ドメインイベントの洗い出し | 高 | 5〜8回 | 低（ホワイトボード前提、テキスト対話に不向き） | ★☆☆ |
| **インパクトマッピング** | ゴール→アクター→インパクト→成果物 | 高 | 3〜5回 | 中（ゴール設定には有効） | ★★☆ |
| **プロトタイピング** | UI/UX の具体化 | 中〜高 | 可変 | 低（テキストベースの対話に不向き） | ★☆☆ |

### requirements-definer への統合設計

推奨度 ★★★ の手法を requirements-definer に段階的に組み込む。

#### Tier 1: コア手法（常に使用）

現在の requirements-definer ワークフロー（Step 1〜5）に**ユーザーストーリー形式**を標準化する。

**変更点:**
- Step 2 の機能要件を「As a [ユーザー], I want [機能], so that [価値]」形式で書く
- これにより「誰のための機能か」が常に明確になる

```
現状: F-01 | TODO作成 | タイトル・期限・優先度を指定してTODOを登録できる
変更: F-01 | TODO作成 | As a 個人ユーザー, I want タイトル・期限・優先度を指定してTODOを登録する, so that やるべきことを忘れずに管理できる
```

#### Tier 2: 構造化手法（複雑なプロジェクトで追加）

曖昧度が高く機能要件が5件以上になりそうな場合に使用する。

1. **ユーザーストーリーマッピング**
   - X軸: ユーザーの行動フロー（大きなステップ）
   - Y軸: 各ステップの詳細ストーリー（優先度順）
   - テキスト対話での表現:
   ```
   ユーザーフロー:
   [1. 商品を探す] → [2. カートに入れる] → [3. 決済する] → [4. 配送を確認する]

   ストーリー（優先度順）:
   1. 商品を探す
      Must:  キーワード検索, カテゴリ閲覧
      Should: フィルタリング, ソート
      Could:  レコメンド
   2. カートに入れる
      Must:  商品追加, 数量変更
      Should: お気に入り保存
      ...
   ```

2. **MoSCoW 優先度**
   - Step 3（スコープ確定）で In/Out の代わりに Must/Should/Could/Won't で分類
   - `priority` フィールドに直接マッピング:
     - Must → priority 1
     - Should → priority 2
     - Could → priority 3
     - Won't → scope.out

#### Tier 3: 補助手法（オプション、ユーザーが要求した場合）

1. **カスタマージャーニーマップ**
   - 特に BtoC のプロダクトで有効
   - requirements-definer の Step 1 の前に実施
   - テキスト対話での表現:
   ```
   カスタマージャーニー:
   | フェーズ | 行動 | 感情 | タッチポイント | 課題 |
   |---------|------|------|--------------|------|
   | 認知 | SNS広告を見る | 興味 | Instagram | 何ができるか不明 |
   | 検討 | LPを見る | 期待 | Web | 料金が分かりにくい |
   | 利用 | 商品を注文する | 不安 | アプリ | 配送状況が見えない |
   | 継続 | リピート購入 | 満足/不満 | メール | 通知が多すぎる |
   ```
   - 各「課題」を機能要件に変換する

2. **ペルソナ定義**
   - requirements.json に `personas` フィールドを追加（オプション）
   - ユーザーストーリーの「As a」の部分を具体化

---

## requirements.json スキーマ拡張案

現在のスキーマに以下のフィールドを追加する（すべてオプション、後方互換）。

```json
{
  "goal": "string (必須)",
  "personas": [
    {
      "id": "string P-01 形式",
      "name": "string ペルソナ名",
      "description": "string 属性・動機・課題"
    }
  ],
  "user_story_map": {
    "flow": ["string ユーザー行動フローの大ステップ"],
    "stories": {
      "<flow-step>": {
        "must": ["string ストーリー"],
        "should": ["string ストーリー"],
        "could": ["string ストーリー"]
      }
    }
  },
  "customer_journey": [
    {
      "phase": "string フェーズ名",
      "action": "string ユーザーの行動",
      "emotion": "string 感情",
      "touchpoint": "string タッチポイント",
      "pain_point": "string 課題"
    }
  ],
  "functional_requirements": [
    {
      "id": "string (必須)",
      "name": "string (必須)",
      "description": "string (必須)",
      "user_story": "string (任意) As a ..., I want ..., so that ... 形式",
      "persona": "string (任意) ペルソナID (P-01等)",
      "moscow": "string (任意) must|should|could|wont",
      "acceptance_criteria": [
        {
          "given": "string (必須)",
          "when": "string (必須)",
          "then": "string (必須)"
        }
      ]
    }
  ],
  "non_functional_requirements": ["(現状通り)"],
  "scope": {
    "in": ["string"],
    "out": [{"feature": "string", "note": "string"}]
  }
}
```

---

## 実装タスク

推奨案を実装する場合のタスク一覧（優先度順）。

### フェーズ1: 最小限の統合（MVP）

| # | タスク | 対象ファイル |
|---|--------|-------------|
| 1 | scrum-master Phase 2 に requirements.json 存在チェックを追加 | `scrum-master/SKILL.md` |
| 2 | scrum-master Phase 2 に曖昧度判定ロジックを追加 | `scrum-master/SKILL.md` |
| 3 | scrum-master Phase 2 に requirements-definer 呼び出し分岐を追加 | `scrum-master/SKILL.md` |
| 4 | requirements.json → plan.json 変換ルールを plan-schema.md に追記 | `scrum-master/references/plan-schema.md` |
| 5 | plan-schema.md に `requirements_source` フィールドを追加（トレーサビリティ用） | `scrum-master/references/plan-schema.md` |

### フェーズ2: 要件定義手法の強化

| # | タスク | 対象ファイル |
|---|--------|-------------|
| 6 | requirements-definer に Tier 1（ユーザーストーリー形式）を導入 | `requirements-definer/SKILL.md` |
| 7 | requirements-definer に Tier 2（ストーリーマッピング、MoSCoW）を条件付き導入 | `requirements-definer/SKILL.md` |
| 8 | requirements.json スキーマに拡張フィールドを追加 | `requirements-definer/references/requirements-schema.md` |

### フェーズ3: 補助手法とUX改善

| # | タスク | 対象ファイル |
|---|--------|-------------|
| 9 | requirements-definer に Tier 3（ジャーニーマップ、ペルソナ）をオプション追加 | `requirements-definer/SKILL.md` |
| 10 | scrum-master description に要件定義関連のトリガーワードを追加 | `scrum-master/SKILL.md` |
| 11 | 曖昧度判定の精度改善（実使用フィードバックを反映） | `scrum-master/SKILL.md` |
