# ltm-use アルゴリズム詳細

## 目次

- [v4.0.0 ハイブリッドランキング](#v400-ハイブリッドランキング)
- [v4.0.0 重複検出ワークフロー](#v400-重複検出ワークフロー)
- [v4.0.0 品質スコア計算](#v400-品質スコア計算)
- [自動タグ抽出](#自動タグ抽出)
- [v5.0.0 忘却曲線（エビングハウスモデル）](#v500-忘却曲線エビングハウスモデル)
- [v5.0.0 記憶の固定化（Consolidation）](#v500-記憶の固定化consolidation)
- [v5.0.0 記憶タイプ自動分類](#v500-記憶タイプ自動分類)
- [v5.0.0 重要度に基づくスコア調整](#v500-重要度に基づくスコア調整)
- [v5.0.0 文脈依存想起](#v500-文脈依存想起)

---

## v4.0.0 ハイブリッドランキング

recall 時のスコアリング方式。キーワード一致 + TF-IDF 意味的類似度 + メタデータブーストの3軸で評価する。

> **v5.0.0 で拡張**: 4軸（+文脈ブースト）に拡張。[v5.0.0 文脈依存想起](#v500-文脈依存想起) を参照。

### ランキング式

```
# v4（3軸）
final_score = 0.5 * keyword_score_normalized + 0.35 * tfidf_cosine + 0.15 * meta_boost

# v5（4軸 — 文脈ブースト追加）
final_score = 0.4 * keyword + 0.3 * tfidf + 0.15 * meta_boost + 0.15 * context_boost

meta_boost = 0.25 * (access_count / 20 上限)
           + 0.25 * (user_rating / 3 上限)
           + 0.3 * retention          # ← v5: freshness_decay を忘却曲線に置換
           + 0.2 * active_status
```

### スコア要素

- **keyword_score_normalized**: タイトル・サマリー・タグでのキーワード一致（0-1正規化）
  - タイトル一致: 10点
  - サマリー一致: 5点
  - タグ一致: 3点
  
- **tfidf_cosine**: クエリと記憶内容の TF-IDF コサイン類似度（0-1）
  - 検索クエリと記憶本文を TF-IDF ベクトル化
  - コサイン類似度を計算
  - 意味的に関連する記憶を検出（例: 「認証」→「JWT」「OAuth」）

- **meta_boost**: アクセス回数・評価・新鮮さ・ステータスによるブースト（0-1）
  - `access_count`: アクセス回数が多いほど高スコア（20回で上限）
  - `user_rating`: ユーザー評価が高いほど高スコア（+3で上限）
  - `freshness_decay`: 新しいほど高スコア（0日=1.0, 30日=0.5, 180日以上=0）
  - `active_status`: active=1.0, archived=0.3, deprecated=0

### 検索の仕組み（2段階）

1. **インデックス検索**: `.memory-index.json` で title/summary/tags を高速スコアリング
   - ファイル読み込みなし
   - 全記憶を高速評価
   
2. **精密スコアリング**: 上位候補のみ実ファイルを読み込み
   - body も含めた TF-IDF 計算
   - 最終スコアで再ランキング

---

## v4.0.0 重複検出ワークフロー

save 時に類似記憶を自動検出し、統合・更新を促す。

### 検出方式

- **閾値**: デフォルト 0.65（`--dedup-threshold` で調整可能）
- **対象フィールド**: title, summary, content を結合して TF-IDF ベクトル化
- **類似度計算**: コサイン類似度

### インタラクティブプロンプト

類似記憶が見つかった場合の選択肢:

| 選択 | 動作 |
|------|------|
| `s` (skip) | 既存記憶をスキップして新規保存 |
| `u` (update) | 既存記憶を更新（内容マージ・タグ統合・updated 更新） |
| `m` (manual) | 手動統合（既存記憶を読み込んでユーザーが編集） |
| `q` (quit) | 保存をキャンセル |

### 統合ロジック（update 選択時）

- **タグ**: 既存 + 新規（重複除去）
- **summary**: 新規で上書き（より正確な情報と判断）
- **content**: 既存の末尾に新規を追記（履歴保持）
- **updated**: 現在日時に更新
- **created**: 維持（初回作成日を保持）

---

## v4.0.0 品質スコア計算

cleanup 時の品質判定に使用。低品質記憶を自動検出する。

### 計算式

```
quality_score = share_score * 0.6 + freshness * 0.2 + uniqueness * 0.2
```

### スコア要素

- **share_score**: 記憶の共有価値スコア（0-100）
  - recall での access_count 加算
  - rate での user_rating 反映
  - 自動計算される総合品質指標

- **freshness**: 新鮮さスコア（0-100）
  - 0日: 100
  - 30日: 50
  - 180日以上: 0
  - 線形補間

- **uniqueness**: 独自性スコア（0 / 50 / 100）
  - body >= 300文字 && tags >= 3: 100
  - body >= 150文字 && tags >= 2: 50
  - それ以下: 0

### 削除判定（cleanup）

- **quality_threshold モード**: `quality_score < 閾値`（デフォルト30）を削除候補に
- **duplicates-only モード**: 類似度 >= 0.85 のペアの低品質側を削除候補に
- 最終的にはユーザー確認または `--yes` で自動削除

---

## 自動タグ抽出

save 時に記憶内容から TF-IDF ベースでタグを自動提案する。

### 抽出ロジック

1. **テキスト結合**: title + summary + content
2. **トークナイゼーション**: 日本語・英語混在対応
3. **TF-IDF 計算**: 
   - コーパス: 既存記憶全体
   - 上位10語を候補に抽出
4. **フィルタリング**:
   - 2文字未満の語を除外
   - 一般的すぎる語を除外（the, is, が, を 等）
5. **インタラクティブ選択**: ユーザーが追加・スキップを判断

### 無効化

`--no-auto-tags` で自動抽出をスキップ可能。

---

## v5.0.0 忘却曲線（エビングハウスモデル）

> **脳科学的背景**: 人間の記憶はエビングハウスの忘却曲線に従い指数関数的に減衰する。
> ただし、適切なタイミングでの復習（想起）により半減期が延長される（間隔反復効果）。
> 海馬での記憶リプレイがこの効果の神経基盤と考えられている。

### 計算式

```
retention = e^(-0.693 * days_since_access / half_life)

where:
  half_life = base_half_life * importance_factor * repetition_factor

  base_half_life = 30  # 日（config で変更可能）

  importance_factor = {
      critical: ∞（忘却しない → retention = 1.0）,
      high:     3.0,
      normal:   1.0,
      low:      0.5
  }

  repetition_factor = 1 + ln(1 + access_count)
  # access_count=0 → 1.0（補正なし）
  # access_count=1 → 1.69
  # access_count=5 → 2.79
  # access_count=10 → 3.40
```

### retention の具体例

| access_count | importance | 30日後 | 90日後 | 180日後 |
|-------------|-----------|--------|--------|---------|
| 0 | normal | 0.50 | 0.13 | 0.02 |
| 0 | high | 0.79 | 0.50 | 0.25 |
| 3 | normal | 0.72 | 0.37 | 0.14 |
| 10 | normal | 0.81 | 0.53 | 0.28 |
| any | critical | 1.00 | 1.00 | 1.00 |

### recall スコアへの統合

`meta_boost` 内の `freshness_decay`（v4: 線形減衰）を `retention`（v5: 指数関数的忘却）に置換。

```
# v4
meta_boost の freshness 成分 = 0.2 * max(0, 1 - days/180)

# v5
meta_boost の retention 成分 = 0.3 * retention
```

### retention の自動更新タイミング

- **recall 時**: access_count 加算と同時に retention_score を再計算して保存
- **cleanup 時**: 全記憶の retention_score を一括再計算
- **review 時**: 全記憶の retention_score を一括再計算

---

## v5.0.0 記憶の固定化（Consolidation）

> **脳科学的背景**: 海馬に一時保存されたエピソード記憶は、睡眠中のリプレイ（再活性化）を経て
> 新皮質に転写され、意味記憶として定着する。この過程で具体的な文脈情報が剥落し、
> 一般化された知識として再構成される。

### 固定化の目的

複数のエピソード記憶を統合・抽象化し、意味記憶（知識）または手続き記憶（手順）に蒸留する。

### 固定化候補の検出

以下の条件で consolidate を自動提案する:

```
条件1: 同一カテゴリ内のエピソード記憶 >= consolidation_threshold（デフォルト: 5）
条件2: TF-IDF 類似度 >= consolidation_similarity（デフォルト: 0.5）のクラスタが 3件以上
```

### 固定化アルゴリズム

```
consolidate(episode_ids: list[str]) → Memory:
  1. 対象エピソード記憶を全文読み込み
  2. 共通トピックを TF-IDF 上位語から抽出
  3. 蒸留ルール:
     - 日時・具体的状況の記述を除去（文脈の剥落）
     - 共通する知見・ルール・パターンを抽出
     - 矛盾する情報は最新のエピソードを優先
     - 手順的な内容が主なら procedural、それ以外は semantic
  4. 新しい記憶を生成:
     - memory_type: semantic or procedural
     - consolidated_from: [元のエピソードID一覧]
     - importance: 元の記憶の最高レベルを継承
     - share_score: 元の記憶の平均 × 1.2（蒸留ボーナス）
     - tags: 全エピソードのタグを統合（重複除去）
  5. 元のエピソード記憶:
     - status: archived
     - consolidated_to: 新しい記憶のID
```

### 固定化後のスコア計算

```
consolidated_share_score = mean(source_share_scores) * 1.2
→ clamp [0, 100]
```

---

## v5.0.0 記憶タイプ自動分類

> **脳科学的背景**: 人間の長期記憶は宣言的記憶（エピソード・意味）と非宣言的記憶（手続き）に
> 大別される。海馬がエピソード記憶を、新皮質が意味記憶を、大脳基底核が手続き記憶を担う。

### 分類ルール

save 時に `--memory-type` を省略した場合、以下のヒューリスティクスで自動判定する。

```
パターンマッチ（優先度順）:

1. procedural（手続き記憶）:
   - content に手順番号パターン（"1." "2." "3." または "手順" "ステップ"）
   - content に「方法」「やり方」「手順書」「ワークフロー」を含む
   → 大脳基底核に対応

2. episodic（エピソード記憶）:
   - content に具体的日付（YYYY-MM-DD, MM/DD, 〇月〇日）
   - content に「〜したとき」「〜で起きた」「〜が発生した」「〜を発見した」
   - content に「今日」「昨日」「さっき」「先ほど」
   → 海馬に対応

3. semantic（意味記憶 — デフォルト）:
   - 上記に該当しない一般的な知識・事実・ルール
   → 新皮質に対応
```

### 分類の影響

| memory_type | 忘却曲線 | 固定化対象 | 昇格優先度 |
|------------|---------|-----------|-----------|
| episodic | 通常の忘却 | ✅ consolidate 対象 | 低（蒸留後に昇格推奨） |
| semantic | 忘却が遅い（×1.5） | ❌ すでに蒸留済み | 高 |
| procedural | 忘却が遅い（×2.0） | ❌ すでに蒸留済み | 高 |

---

## v5.0.0 重要度に基づくスコア調整

> **脳科学的背景**: 扁桃体は感情的に重要な出来事に「タグ」を付け、海馬での記憶形成を強化する。
> 恐怖・驚き・達成感などの感情が伴う記憶は、中性的な記憶より長期に保持される。

### 重要度の自動検出

save 時に `--importance` を省略した場合、以下のキーワードで自動判定する。

```
critical（扁桃体: 強い感情反応）:
  - 「本番障害」「セキュリティ」「データ損失」「脆弱性」「インシデント」
  - 「絶対に」「致命的」「重大な」

high（扁桃体: 中程度の感情反応）:
  - 「設計決定」「アーキテクチャ」「重要」「再発防止」「根本原因」
  - 「ベストプラクティス」「パフォーマンス」

normal（デフォルト）:
  - 上記に該当しない通常の知見

low（低い感情反応）:
  - 「仮」「試し」「メモ」「とりあえず」「一時的」「WIP」
```

### share_score への影響

```
importance_multiplier = { critical: 1.5, high: 1.2, normal: 1.0, low: 0.7 }
adjusted_share_score = clamp(base_share_score * importance_multiplier, 0, 100)
```

### recall ランキングへの影響

```
importance_boost = { critical: 0.3, high: 0.15, normal: 0, low: -0.1 }
# meta_boost 内の active_status 成分（0.2）に加算
```

---

## v5.0.0 文脈依存想起

> **脳科学的背景**: 前頭前皮質は現在の目標やタスクに基づいて、海馬から関連する記憶を
> 選択的に活性化する（文脈依存想起）。同じ手がかりでも、文脈が異なれば異なる記憶が想起される。

### コンテキストブースト計算

```
context_boost = cosine_similarity(memory_tfidf_vector, context_tfidf_vector)

where:
  context_text = --context で指定されたテキスト
               or --auto-context で自動収集されたコンテキスト（git diff, 直近の変更ファイル名）
  context_tfidf_vector = tfidf_vectorize(tokenize(context_text))
```

### v5 ランキング式（4軸）

```
final_score = 0.4 * keyword + 0.3 * tfidf_sim + 0.15 * meta_boost + 0.15 * context_boost

# context 未指定時は v4 互換（3軸）にフォールバック:
final_score = 0.5 * keyword + 0.35 * tfidf_sim + 0.15 * meta_boost
```

### auto-context の情報源

1. `git diff --stat HEAD` の変更ファイル名
2. 現在のディレクトリ名（プロジェクト/モジュール推定）
3. 直近に recall したキーワード（セッション内キャッシュ）
