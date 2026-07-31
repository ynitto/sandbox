# 記憶ファイルフォーマット仕様

## 目次

- [スコープ設計](#スコープ設計)
- [ディレクトリ構造](#ディレクトリ構造)
- [ファイルフォーマット](#ファイルフォーマット)
- [フィールド定義](#フィールド定義)
- [share_score 算出ロジック](#share_score-算出ロジックv2)
- [クリーンアップ基準](#クリーンアップ基準)
- [良い summary の例](#良い-summary-の例)
- [カテゴリ命名ガイドライン](#カテゴリ命名ガイドライン)

---

## スコープ設計

記憶は2つのスコープで管理される。

```
home  →  (昇格・git)  →  shared
  ↑                           ↑
ユーザー横断               チーム共有(git)
ローカル永続               git管理
```

| スコープ | 保存先 | 用途 | git管理 |
|---------|--------|------|---------|
| `home` | `<AGENT_HOME>/memory/home/` | 全記憶のデフォルト保存先（複数プロジェクト横断） | 個人管理（ローカル） |
| `shared` | `<AGENT_HOME>/memory/shared/<repo名>/memories/` | チーム共有すべき知見 | **git管理** |

---

## ディレクトリ構造

```
memories/
├── [カテゴリ名]/          # トピック・プロジェクト・課題種別など
│   ├── [記憶名].md
│   └── [記憶名].md
└── general/               # カテゴリ不明時のデフォルト
    └── [記憶名].md
```

- **カテゴリ名**: kebab-case（例: `auth-system`, `bug-investigation`, `architecture`）
- **記憶名**: kebab-case ASCII（例: `jwt-expiry-fix`, `api-design-decision`）

---

## ファイルフォーマット

```markdown
---
id: mem-YYYYMMDD-NNN
title: "記憶タイトル（一言で内容を表す）"
created: "YYYY-MM-DD"
updated: "YYYY-MM-DD"
status: active          # active | archived | deprecated
scope: home             # home | shared
tags: [タグ1, タグ2]
related: []             # 関連する記憶ファイルのパス
access_count: 0         # 参照回数（recall時に自動加算）
last_accessed: ""       # 最終参照日（recall時に自動更新）
user_rating: 0          # ユーザー評価累計（rate_memory.py で更新）
correction_count: 0     # 修正・再指示を受けた回数（低いほど正確）
share_score: 0          # 共有価値スコア（0〜100点、自動計算）
promoted_from: ""       # 昇格元パス（昇格した場合に設定）
summary: "この記憶の要点を1〜2文で。検索時の関連性判断に使う。"
# v5.0.0 脳構造インスパイアフィールド
memory_type: semantic       # episodic | semantic | procedural（デフォルト: semantic）
importance: normal          # critical | high | normal | low（デフォルト: normal）
consolidated_from: []       # consolidate で生成された場合の元エピソードID一覧
consolidated_to: ""         # consolidate で蒸留された先の記憶ID（archived時に設定）
retention_score: 1.0        # 忘却曲線に基づく保持率（0.0-1.0、recall/cleanup時に自動更新）
---

# タイトル

## コンテキスト
どんな状況・課題に関する記憶か。プロジェクト名、背景など。

## 詳細
具体的な情報・調査結果・決定事項など。

## 学び・結論
次回に活かせる知見、注意点、推奨アクションなど。

## 次のステップ（任意）
作業を再開する場合にやること。
```

---

## フィールド定義

| フィールド | 必須 | 説明 |
|-----------|------|------|
| `id` | ✓ | `mem-YYYYMMDD-NNN` 形式。NNNは同日連番 |
| `title` | ✓ | 記憶を一言で表すタイトル |
| `created` | ✓ | 作成日（ISO 8601） |
| `updated` | ✓ | 最終更新日 |
| `status` | ✓ | `active` / `archived` / `deprecated` |
| `scope` | ✓ | `home` / `shared` |
| `tags` | - | 検索用タグ（配列） |
| `related` | - | 関連記憶ファイルのパス（配列） |
| `access_count` | ✓ | recall 時に自動加算。クリーンアップの判断基準 |
| `last_accessed` | ✓ | 最終参照日。空文字は未参照 |
| `user_rating` | ✓ | ユーザー評価の累計。`rate_memory.py --good` で +1、`--bad/--correction` で -1 |
| `correction_count` | ✓ | ユーザーによる修正・再指示の回数。高いほどスコアが下がる |
| `share_score` | ✓ | 0〜100点。save/recall/rate 時に自動計算 |
| `promoted_from` | - | 昇格元の記憶ID（`mem-YYYYMMDD-NNN` 形式） |
| `summary` | ✓ | 関連性判断用の要約（1〜2文） |
| `memory_type` | - | `episodic`（経験）/ `semantic`（知識）/ `procedural`（手順）。省略時は `semantic`。脳の記憶分類に対応 |
| `importance` | - | `critical` / `high` / `normal` / `low`。省略時は `normal`。扁桃体モデルに基づく記憶保持力制御 |
| `consolidated_from` | - | consolidate で生成された場合の元エピソードID一覧（配列） |
| `consolidated_to` | - | consolidate で蒸留された先の記憶ID（元エピソードに設定） |
| `retention_score` | - | 忘却曲線に基づく保持率（0.0-1.0）。recall/cleanup 時に自動更新 |

---

## share_score 算出ロジック（v3 — 重要度加味）

```
base_score =
  min(access_count * 8, 32)         # 参照頻度（最大32点）
+ min(tags数 * 5, 20)               # タグ豊富さ（最大20点）
+ min(本文文字数 / 100, 18)          # 情報量（最大18点）
+ (10 if status==active else 0)     # アクティブ（10点）
+ max(min(user_rating * 10, 20), -20)  # ユーザー評価（-20〜+20点）
- min(correction_count * 5, 20)     # 修正ペナルティ（最大-20点）

# v5.0.0: 重要度による調整（扁桃体モデル）
importance_multiplier = { critical: 1.5, high: 1.2, normal: 1.0, low: 0.7 }
share_score = clamp(base_score * importance_multiplier, 0, 100)
```

### ユーザー評価によるスコア変動

| 操作 | 変動 | 効果 |
|------|------|------|
| `rate_memory.py --good` | user_rating +1 | share_score +10（上限+20） |
| `rate_memory.py --bad` | user_rating -1 | share_score -10（下限-20） |
| `rate_memory.py --correction` | user_rating -1, correction_count +1 | share_score -15以上の減点 |

### 昇格閾値

| スコア | 意味 | 自動アクション |
|--------|------|---------------|
| 0〜69  | 通常記憶 | なし |
| 70〜84 | 昇格候補 | `promote_memory.py` が確認を求める |
| 85〜100 | 高価値 | `promote_memory.py --auto` で自動昇格 |

---

## クリーンアップ基準

| 条件 | 判定 | デフォルト |
|------|------|-----------|
| `access_count == 0` かつ経過日数 > N日 | 削除候補 | 30日 |
| `status == archived` かつ経過日数 > N日 | 削除候補 | 60日 |
| `status == deprecated` | 即時削除候補 | - |
| `importance == critical` | **削除対象外**（v5.0.0） | - |
| `retention_score < 0.1` かつ `importance != critical/high` | 削除候補（v5.0.0） | - |

設定は `<AGENT_HOME>/memory/config.json` で変更可能。

### v5.0.0 重要度による保持期間調整

| importance | 未アクセス削除閾値 | アーカイブ削除閾値 |
|-----------|-------------------|-------------------|
| critical | 削除対象外 | 削除対象外 |
| high | 60日（2倍） | 120日（2倍） |
| normal | 30日（デフォルト） | 60日（デフォルト） |
| low | 15日（半分） | 30日（半分） |

---

## 良い summary の例

```
# 悪い例（情報不足）
summary: "認証について調べた"

# 良い例（判断に必要な情報を含む）
summary: "JWTトークンの有効期限を15分に設定した理由と、リフレッシュトークン実装のポイント。auth-serviceに影響。"
```

---

## カテゴリ命名ガイドライン

| 用途 | カテゴリ例 |
|------|-----------|
| 機能・モジュール | `auth`, `payment`, `notifications` |
| バグ調査 | `bug-investigation`, `incident-YYYY-MM` |
| アーキテクチャ決定 | `architecture`, `adr` |
| 調査・リサーチ | `research`, `spike` |
| 汎用 | `general` |
