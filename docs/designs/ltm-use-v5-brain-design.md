# ltm-use v5 — 脳構造インスパイア設計書

> **ステータス**: Draft
> **作成日**: 2026-03-12
> **前提**: ltm-use v4.0.0（TF-IDF 類似度エンジン・ハイブリッドランキング済み）
> **参考**: Foundation Agents (arXiv:2504.01990), Rethinking Memory Mechanisms (arXiv:2602.06052), CoALA (arXiv:2309.02427)

---

## 1. 背景：人間の脳の記憶構造

### 1.1 脳の記憶システムと対応するエージェント設計

人間の脳は複数の記憶サブシステムを持ち、それぞれ異なる脳領域が担当する。
Foundation Agents 論文群では、この構造をエージェントの記憶設計に直接マッピングすることが提案されている。

```
┌─────────────────────────────────────────────────────────────────┐
│                    人間の脳の記憶システム                          │
├─────────────┬───────────────┬────────────────┬─────────────────┤
│ 感覚記憶     │ ワーキング     │  短期記憶       │   長期記憶       │
│ (感覚皮質)   │ メモリ         │  (海馬)         │  (新皮質)       │
│              │ (前頭前皮質)   │                │                 │
│ ミリ秒単位   │ 秒〜分単位     │  分〜時間       │  日〜永続        │
│ 生の入力     │ 操作・推論     │  エピソード     │  意味・手続き    │
└─────────────┴───────────────┴────────────────┴─────────────────┘
         ↕                                ↕
┌─────────────────────────────────────────────────────────────────┐
│                  エージェント記憶への対応                          │
├─────────────┬───────────────┬────────────────┬─────────────────┤
│ コンテキスト  │ セッション内   │  エピソード     │  意味・手続き    │
│ ウィンドウ    │ ワーキング     │  記憶           │  記憶           │
│ (入力処理)    │ メモリ         │  (ltm-use)     │  (ltm-use)     │
│              │ (会話履歴)     │                │                 │
│ LLM入力      │ 一時的保持     │  経験記録       │  知識・手順     │
└─────────────┴───────────────┴────────────────┴─────────────────┘
```

### 1.2 脳領域とltm-use機能のマッピング

| 脳領域 | 機能 | ltm-use での対応 | v5 での強化 |
|--------|------|-----------------|-------------|
| **海馬** | エピソード記憶の形成・一時保存 | save（経験の記録） | `memory_type: episodic` の明示化 |
| **新皮質** | 意味記憶の長期保存・スキーマ形成 | promote（昇格） | **consolidate**（エピソード→意味記憶への蒸留） |
| **前頭前皮質** | ワーキングメモリ・文脈判断・計画 | recall（文脈に応じた想起） | **context-aware recall**（作業コンテキスト連動） |
| **扁桃体** | 感情的重要度タグ付け・記憶強化 | user_rating | **importance** レベル（記憶の保持力制御） |
| **大脳基底核** | 手続き記憶・習慣・自動化 | （未対応） | `memory_type: procedural`（手順・パターンの記録） |
| **小脳** | 運動学習・微調整 | （スコープ外） | — |

### 1.3 記憶の固定化（Consolidation）プロセス

神経科学では、海馬に一時保存されたエピソード記憶が、睡眠中のリプレイ（再活性化）を経て
新皮質に転写され、意味記憶として定着する。このプロセスを「記憶の固定化」と呼ぶ。

```
[海馬] エピソード記憶（生の経験）
  │
  │ ← 睡眠中のリプレイ・再活性化
  │ ← 類似エピソードの統合・抽象化
  │ ← 不要な詳細の忘却
  ▼
[新皮質] 意味記憶（蒸留された知識）
```

ltm-use v5 では、この固定化プロセスを **`consolidate`** 操作として実装する。

---

## 2. v5 新概念

### 2.1 記憶タイプ分類（Memory Type）

人間の長期記憶は大きく3種に分類される。ltm-use v5 では `memory_type` フィールドを導入する。

| 記憶タイプ | 脳の対応 | 定義 | ltm-use での例 |
|-----------|---------|------|---------------|
| **episodic**（エピソード記憶） | 海馬 | 特定の経験・イベントの記録。いつ・どこで・何が起きたかの文脈を含む | 「3/10にJWT認証でハマった。原因はリフレッシュトークンの期限切れ未処理」 |
| **semantic**（意味記憶） | 新皮質 | 経験から蒸留された一般的な知識・事実。文脈は剥落し本質のみ残る | 「JWTリフレッシュトークンは必ず期限切れハンドリングを実装すること」 |
| **procedural**（手続き記憶） | 大脳基底核 | 手順・パターン・ワークフロー。「どうやるか」の記録 | 「デプロイ手順: 1. テスト実行 2. ビルド 3. staging確認 4. production」 |

#### 自動分類ルール

save 時に `--memory-type` を省略した場合、以下のヒューリスティクスで自動判定する:

```
if content に日付・具体的イベント・「〜したとき」「〜で起きた」を含む:
    → episodic
elif content に手順番号（1. 2. 3.）・「手順」「方法」「やり方」を含む:
    → procedural
else:
    → semantic（デフォルト）
```

### 2.2 重要度レベル（Importance — 扁桃体モデル）

扁桃体は感情的に重要な出来事に「タグ」を付け、海馬での記憶形成を強化する。
ltm-use v5 では `importance` フィールドで記憶の保持力と検索優先度を制御する。

| レベル | 定義 | 忘却耐性 | 検索ブースト | 自動設定トリガー |
|--------|------|---------|-------------|-----------------|
| **critical** | 致命的な障害・セキュリティ・データ損失の回避策 | 忘却対象外 | +0.3 | 「本番障害」「セキュリティ」「データ損失」を含む |
| **high** | 重要な設計決定・繰り返す失敗の防止策 | 2倍の保持期間 | +0.15 | 「設計決定」「重要」「再発防止」を含む |
| **normal** | 通常の知見・調査結果 | デフォルト | 0 | デフォルト |
| **low** | 一時的メモ・試行的な情報 | 半分の保持期間 | -0.1 | 「仮」「試し」「メモ」を含む |

#### share_score への影響

```
importance_multiplier = {
    "critical": 1.5,
    "high": 1.2,
    "normal": 1.0,
    "low": 0.7
}
adjusted_share_score = share_score * importance_multiplier
```

### 2.3 記憶の固定化（Consolidate — 海馬→新皮質モデル）

複数のエピソード記憶を統合・抽象化し、意味記憶またはロ手続き記憶に蒸留する操作。

#### フロー

```
consolidate_memory.py
  │
  ├─ [1] 対象のエピソード記憶群を特定
  │      - 同じカテゴリ・タグで related な記憶
  │      - TF-IDF 類似度が高いクラスタ
  │
  ├─ [2] 共通パターンを抽出（蒸留）
  │      - エピソードから文脈（いつ・誰が）を剥落
  │      - 共通する知見・ルール・手順を抽出
  │      - 矛盾する情報は最新を優先
  │
  ├─ [3] 新しい semantic/procedural 記憶を生成
  │      - consolidated_from: [元のエピソードID一覧]
  │      - importance: 元の記憶の最高レベルを継承
  │      - share_score: 元の記憶の平均 × 1.2（蒸留ボーナス）
  │
  └─ [4] 元のエピソード記憶を archived に変更
         - status: archived
         - consolidated_to: 新しい記憶のID
```

#### 自動固定化トリガー

以下の条件で `consolidate` を自動提案する:

- 同一カテゴリ内にエピソード記憶が **5件以上** 蓄積
- 類似度 0.5 以上のエピソード記憶が **3件以上** のクラスタを形成
- cleanup 実行時に固定化候補を検出

### 2.4 忘却曲線（Forgetting Curve — エビングハウスモデル）

人間の記憶は時間とともに指数関数的に減衰するが、適切なタイミングでの復習（想起）で
減衰を遅らせることができる（間隔反復効果）。

#### v4 からの改善

v4 の `freshness_decay` は線形減衰だったが、v5 ではエビングハウス忘却曲線を採用する。

```
# v4（線形）
freshness = max(0, 1 - days / 180)

# v5（指数関数的忘却 + 間隔反復）
retention = e^(-days / (half_life * repetition_factor))

where:
  half_life = 30  # 基本半減期（日数）
  repetition_factor = 1 + ln(1 + access_count)  # 参照回数で半減期が延長
  days = 最終アクセスからの経過日数
```

#### 実装

```python
import math

def compute_retention(days_since_access: int, access_count: int,
                      importance: str = "normal") -> float:
    """忘却曲線に基づく記憶保持率（0.0〜1.0）"""
    base_half_life = 30  # 日

    # 重要度による半減期調整
    importance_factor = {
        "critical": float('inf'),  # 忘却しない
        "high": 3.0,
        "normal": 1.0,
        "low": 0.5,
    }.get(importance, 1.0)

    if importance == "critical":
        return 1.0

    # 間隔反復: アクセス回数が増えるほど半減期が延長
    repetition_factor = 1 + math.log(1 + access_count)

    half_life = base_half_life * importance_factor * repetition_factor

    # 指数関数的忘却
    retention = math.exp(-0.693 * days_since_access / half_life)

    return max(0.0, min(1.0, retention))
```

#### recall スコアへの統合

```
# v4
meta_boost = 0.3 * access + 0.3 * rating + 0.2 * freshness + 0.2 * active

# v5（retention で freshness を置換 + importance ブースト）
meta_boost = 0.25 * access + 0.25 * rating + 0.3 * retention + 0.2 * importance_boost
```

### 2.5 文脈依存想起（Context-Aware Recall — 前頭前皮質モデル）

前頭前皮質は現在の目標やタスクに基づいて、関連する記憶を選択的に活性化する。
v5 では `--context` オプションで作業コンテキストを指定し、暗黙的な関連性を考慮した検索を行う。

```bash
# v4: 明示的キーワードのみ
python recall_memory.py "JWT"

# v5: 作業コンテキストを加味
python recall_memory.py "JWT" --context "認証システムのリファクタリング"
# → JWT だけでなく、「OAuth」「セッション管理」「セキュリティ監査」も高ランク

# v5: 自動コンテキスト（git diff / 直近の会話から推定）
python recall_memory.py --auto-context
# → 現在変更中のファイルから関連する記憶を自動検索
```

#### 実装方式

```
context_vector = tfidf_vectorize(context_text)
context_boost = cosine_similarity(memory_vector, context_vector) * 0.2

final_score = α * keyword + β * tfidf_sim + γ * meta_boost + δ * context_boost
where: α=0.4, β=0.3, γ=0.15, δ=0.15
```

### 2.6 記憶のレビュー（Memory Review — 海馬リプレイモデル）

睡眠中に海馬が記憶をリプレイし、重要な記憶を強化・不要な記憶を忘却する。
v5 では定期的な記憶レビュー機能を追加する。

```bash
# 記憶の棚卸し（レビュー対象を提示）
python review_memory.py

# 出力例:
# === 記憶レビュー ===
#
# 📌 固定化候補（エピソード→意味記憶への蒸留推奨）:
#   [1] auth カテゴリに 5件のエピソード記憶 → consolidate を推奨
#
# ⚠ 忘却リスク（retention < 0.3 で価値のある記憶）:
#   [2] mem-20260201-003 "API設計のガイドライン" (retention: 0.25, share_score: 65)
#       → recall して再活性化するか、importance を high に変更を推奨
#
# 🗑 クリーンアップ候補:
#   [3] mem-20260115-001 "一時的なデバッグメモ" (retention: 0.05, importance: low)
#       → archive を推奨
```

---

## 3. フロントマター拡張

### 3.1 新規フィールド

既存フォーマットに以下を追加（全てオプショナル、後方互換）。

```yaml
---
# ... 既存フィールドはそのまま ...
memory_type: episodic       # episodic | semantic | procedural（デフォルト: semantic）
importance: normal          # critical | high | normal | low（デフォルト: normal）
consolidated_from: []       # consolidate で生成された場合の元エピソードID一覧
consolidated_to: ""         # consolidate で蒸留された先の記憶ID
retention_score: 1.0        # 忘却曲線に基づく保持率（0.0-1.0、recall/cleanup時に自動更新）
---
```

### 3.2 config.json 拡張

```json
{
  "// v4 fields": "...",

  "// v5 brain-inspired fields": "",
  "consolidation_threshold": 5,
  "consolidation_similarity": 0.5,
  "forgetting_base_half_life": 30,
  "auto_importance_enabled": true,
  "context_aware_recall": true,
  "review_interval_days": 14,
  "recall_hybrid_weights_v5": {
    "keyword": 0.4,
    "tfidf": 0.3,
    "meta": 0.15,
    "context": 0.15
  }
}
```

---

## 4. 記憶のライフサイクル（v5 拡張）

```
[生の経験] セッション内での発見・決定・失敗
  │
  │ save (memory_type: episodic, importance: auto-detect)
  ▼
[エピソード記憶] 海馬モデル — 具体的な経験の記録
  │
  │ recall で access_count 加算 → retention 上昇（間隔反復）
  │ rate で user_rating 更新 → share_score 変動
  │
  ├─── 5件以上蓄積 or 類似クラスタ形成
  │     │
  │     │ consolidate（固定化）
  │     ▼
  │   [意味/手続き記憶] 新皮質モデル — 蒸留された知識
  │     │
  │     │ promote（昇格）
  │     ▼
  │   [共有知識] workspace → home → shared
  │
  ├─── retention 低下（忘却曲線）
  │     │
  │     │ review で再活性化 or archive
  │     ▼
  │   [再活性化] access_count 加算 → retention リセット
  │   [忘却]     archive → cleanup で削除
  │
  └─── importance: critical
        │
        └── 忘却対象外（永続保持）
```

---

## 5. 実装計画

### Phase 1: 記憶タイプ + 重要度フィールド

| ファイル | 変更内容 |
|---------|---------|
| `references/memory-format.md` | `memory_type`, `importance`, `consolidated_from/to`, `retention_score` フィールド追加 |
| `scripts/save_memory.py` | `--memory-type`, `--importance` オプション追加。自動分類ロジック |
| `scripts/memory_utils.py` | 重要度定数、記憶タイプ定数、`compute_retention()` 関数追加 |

### Phase 2: 忘却曲線 + recall 強化

| ファイル | 変更内容 |
|---------|---------|
| `scripts/recall_memory.py` | `retention` を `meta_boost` に統合。`--context` オプション追加 |
| `scripts/similarity.py` | `compute_retention()` 関数追加 |
| `scripts/build_index.py` | インデックスに `memory_type`, `importance`, `retention_score` を追加 |

### Phase 3: consolidate 操作

| ファイル | 変更内容 |
|---------|---------|
| `scripts/consolidate_memory.py` | **新規**: 固定化操作（エピソード群 → 意味/手続き記憶） |
| `scripts/cleanup_memory.py` | 固定化候補の検出ロジック追加 |

### Phase 4: review 操作

| ファイル | 変更内容 |
|---------|---------|
| `scripts/review_memory.py` | **新規**: 定期レビュー（固定化候補・忘却リスク・クリーンアップ候補） |

### Phase 5: SKILL.md + ドキュメント更新

| ファイル | 変更内容 |
|---------|---------|
| `SKILL.md` | version 5.0.0、新操作・新概念の統合 |
| `references/algorithms.md` | 忘却曲線・固定化アルゴリズム追加 |
| `references/operations.md` | consolidate, review 操作追加 |
| `references/configuration.md` | 新設定項目追加 |

---

## 6. テスト戦略

### 6.1 ユニットテスト

```python
def test_compute_retention_normal():
    """normal importance, 0 access → 30日で半減"""
    r = compute_retention(days=30, access_count=0, importance="normal")
    assert 0.45 < r < 0.55

def test_compute_retention_critical():
    """critical importance → 常に 1.0"""
    r = compute_retention(days=365, access_count=0, importance="critical")
    assert r == 1.0

def test_compute_retention_repetition():
    """アクセス回数が増えると半減期が延長"""
    r0 = compute_retention(days=30, access_count=0, importance="normal")
    r5 = compute_retention(days=30, access_count=5, importance="normal")
    assert r5 > r0  # 5回アクセスした方が保持率が高い

def test_auto_memory_type_episodic():
    """日時や具体的イベントを含むテキスト → episodic"""
    mt = detect_memory_type("3月10日にJWT認証でエラーが発生した")
    assert mt == "episodic"

def test_auto_memory_type_procedural():
    """手順を含むテキスト → procedural"""
    mt = detect_memory_type("デプロイ手順: 1. テスト 2. ビルド 3. デプロイ")
    assert mt == "procedural"

def test_consolidation():
    """エピソード記憶群を意味記憶に蒸留"""
    episodes = [
        {"title": "JWT期限エラー修正", "type": "episodic"},
        {"title": "OAuthトークン更新の問題", "type": "episodic"},
        {"title": "セッション切れバグ", "type": "episodic"},
    ]
    result = consolidate(episodes)
    assert result["memory_type"] == "semantic"
    assert len(result["consolidated_from"]) == 3
```

### 6.2 統合テスト

1. エピソード記憶を5件保存 → consolidate 提案が表示されること
2. consolidate 実行 → 意味記憶が生成され、元エピソードが archived になること
3. importance: critical の記憶 → cleanup で削除対象にならないこと
4. 30日未アクセスの記憶 → retention が約0.5まで低下すること
5. review 実行 → 固定化候補・忘却リスク・クリーンアップ候補が正しく表示されること

---

## 7. 将来拡張（v6 以降の検討事項）

### 7.1 感情タグの拡張（扁桃体モデル詳細化）

現在の `importance` に加え、`emotion` フィールドで記憶の感情的文脈を記録する。
- `frustration`: 苦労して解決した問題（再発防止の価値が高い）
- `eureka`: 発見・ブレイクスルー（創造的な知見）
- `caution`: 注意・警告（リスク回避）

### 7.2 記憶グラフ（海馬の連合記憶モデル）

記憶間の関連をグラフ構造で管理し、連想検索を実現する。
`related` フィールドを自動更新し、記憶ネットワークを構築する。

### 7.3 睡眠的バッチ処理（オフライン固定化）

セッション終了時に自動的に以下を実行する「睡眠モード」:
1. エピソード記憶の固定化候補を検出
2. 忘却リスクのある記憶を通知
3. 重複記憶のマージ提案
4. retention_score の一括更新
