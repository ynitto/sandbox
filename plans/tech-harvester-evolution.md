# tech-harvester 自律進化計画

> 参考: [1年間の育休に備えて「勝手に賢くなる」AI情報収集基盤を作った](https://zenn.dev/tokium_dev/articles/20260427_ai_tech_researcher)

## 背景と課題

現在の tech-harvester は以下の構造を持つ静的なスキルです。

| 要素 | 現状 |
|------|------|
| フィード一覧 | `skill-registry.json` にハードコード（19件） |
| 追加・削除 | 人手で `skill-registry.json` を直接編集 |
| キーワード | なし（タグによる粗いフィルタのみ） |
| 品質評価 | なし（全フィードを均等に扱う） |

**問題**: 技術トレンドは常に変化するが、収集先もキーワードも静止したまま。  
**目標**: 収集先・キーワードが記事の内容から自律的に改善される仕組みを作る。

---

## アーキテクチャ概要

```
┌─────────────────────────────────────────────────────────────┐
│  定期実行レイヤー（GitHub Actions / cron）                    │
│                                                             │
│  毎日              週次               月次                   │
│  fetch_feeds.py    keyword_trends.py  evolve_feeds.py       │
│  → Digest生成      → トレンド分析     → フィード進化         │
└──────────────────────────────┬──────────────────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │  harvest-state.json  │
                    │  (永続化ストア)       │
                    │  - フィード品質スコア │
                    │  - キーワードトレンド │
                    │  - 候補フィードキュー │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
       フィード品質       キーワード          フィード
       スコアリング       トレンド分析        自動発見
       (scorer.py)     (keywords.py)    (discover.py)
                               │
                    ┌──────────▼──────────┐
                    │  GitHub PR 自動作成  │
                    │  - 新フィード追加提案 │
                    │  - 低品質フィード削除 │
                    │  - タグ最適化        │
                    └─────────────────────┘
```

---

## Phase 1: フィード品質トラッキング

**目的**: どのフィードが「有益な記事」を多く出しているかを定量化する。

### 実装内容

**新ファイル**: `.github/skills/tech-harvester/scripts/scorer.py`

フィードごとに以下の指標を `harvest-state.json` に蓄積する。

```json
{
  "feeds": {
    "Hacker News": {
      "fetch_count": 42,
      "article_count": 380,
      "avg_desc_length": 210,
      "consecutive_failures": 0,
      "last_fetched": "2026-05-01T12:00:00Z",
      "relevance_score": 72.5
    }
  }
}
```

**スコアリング基準**:
- `avg_desc_length`: 説明文が短すぎるフィードはコンテンツが薄い
- `consecutive_failures`: 3回以上連続失敗 → 削除候補フラグ
- `relevance_score`: LLMが各記事の説明文を見て0〜100点で評価（週次バッチ）

**`fetch_feeds.py` への変更**:
- `--state-file FILE` オプションを追加
- フェッチ結果を `harvest-state.json` に追記するモードを追加

---

## Phase 2: キーワード・トレンド分析

**目的**: 収集した記事群から「今週浮上しているトピック」を自動抽出し、次回収集に活かす。

### 実装内容

**新ファイル**: `.github/skills/tech-harvester/scripts/keyword_trends.py`

```
入力: articles.json (fetch_feeds.py の出力)
処理: LLM に記事タイトル+説明文を渡し、出現頻度の高いキーワード/トピックを抽出
出力: harvest-state.json の keywords セクションを更新
```

**`harvest-state.json` のキーワードセクション**:
```json
{
  "keywords": {
    "2026-W19": {
      "rising": ["Claude 4", "MCP サーバー", "Rust 2026 edition"],
      "stable": ["Kubernetes", "TypeScript", "LLM評価"],
      "declining": ["GPT-3", "Docker Swarm"]
    }
  }
}
```

**キーワードの使われ方**:
1. ダイジェスト生成時に「今週の注目キーワード」セクションを自動追加
2. フィード発見フェーズで「このキーワードをよく扱うフィードを探す」検索クエリに使う
3. フィードへの `tags` 割り当てを自動補正する材料にする

**Digest 出力フォーマットへの追加**:
```markdown
## 今週の注目キーワード

`Claude 4` `MCP サーバー` `Rust 2026 edition`

---
```

---

## Phase 3: フィード自動発見

**目的**: 記事内から参照されているブログ・サイトを拾い、新しい RSS フィードを発掘する。

### 実装内容

**新ファイル**: `.github/skills/tech-harvester/scripts/discover.py`

```
入力: articles.json
処理:
  1. 各記事の本文・説明文から外部リンクを抽出（正規表現）
  2. リンク先のドメインを集計（出現頻度でランキング）
  3. 上位ドメインについて /feed, /rss, /atom.xml などを試行
  4. 有効な RSS フィードが見つかれば候補リストに追加
  5. LLMに候補フィードのサンプル記事を渡し、関連性スコアを付与
出力: harvest-state.json の candidate_feeds セクションを更新
```

**候補フィード状態**:
```json
{
  "candidate_feeds": [
    {
      "name": "Example Tech Blog",
      "url": "https://example.com/feed",
      "lang": "ja",
      "discovered_from": ["https://zenn.dev/...", "https://qiita.com/..."],
      "discovery_count": 3,
      "relevance_score": 85,
      "suggested_tags": ["japanese", "tech", "blog"],
      "status": "pending"
    }
  ]
}
```

**昇格基準** (`discovery_count >= 2` かつ `relevance_score >= 60`):
- 候補フィードが昇格基準を満たしたら GitHub PR を自動作成
- PR には「なぜこのフィードが発見されたか」の根拠を記載

---

## Phase 4: フィード自律進化（統合スクリプト）

**目的**: 上記3フェーズを統合し、`skill-registry.json` への変更提案を GitHub PR として自動生成する。

### 実装内容

**新ファイル**: `.github/skills/tech-harvester/scripts/evolve_feeds.py`

```
処理フロー:
  1. harvest-state.json を読み込む
  2. 削除候補フィードを特定
     - consecutive_failures >= 3
     - relevance_score < 30 かつ fetch_count >= 10
  3. 追加候補フィードを特定
     - candidate_feeds から昇格基準を満たすもの
  4. skill-registry.json の変更差分を生成
  5. GitHub PR を作成（mcp__github__ ツール経由）
```

**PR テンプレート**:
```markdown
## 🌱 tech-harvester フィード自律進化

### 追加提案
- **Example Tech Blog** (https://example.com/feed)
  - 発見根拠: 3件の高評価記事から参照
  - 関連性スコア: 85/100
  - 推奨タグ: japanese, tech, blog

### 削除提案
- **低品質フィード名**
  - 理由: 3回連続フェッチ失敗 / 関連性スコア 18/100

### 変更しない理由の記録
（自動生成: 変更なしの場合はスキップ）
```

---

## Phase 5: GitHub Actions による定期実行

**新ファイル**: `.github/workflows/tech-harvester-evolution.yml`

```yaml
name: Tech Harvester Evolution

on:
  schedule:
    - cron: '0 1 * * *'    # 毎日 01:00 UTC（日次ダイジェスト）
    - cron: '0 2 * * 1'    # 毎週月曜 02:00 UTC（週次トレンド分析）
    - cron: '0 3 1 * *'    # 毎月1日 03:00 UTC（月次フィード進化）
  workflow_dispatch:
    inputs:
      mode:
        description: 'Run mode'
        required: true
        default: 'digest'
        type: choice
        options: [digest, trends, evolve]

jobs:
  daily-digest:
    if: github.event_name == 'schedule' && ...
    # fetch_feeds.py → Digest Markdown を Issue/Artifact に保存

  weekly-trends:
    if: ...
    # keyword_trends.py → harvest-state.json を更新してコミット

  monthly-evolve:
    if: ...
    # evolve_feeds.py → skill-registry.json 変更 PR を作成
```

---

## ファイル構成（変更後）

```
.github/skills/tech-harvester/
├── SKILL.md                          # 更新: 進化ワークフロー追記
└── scripts/
    ├── fetch_feeds.py                # 更新: --state-file オプション追加
    ├── scorer.py                     # 新規: フィード品質スコアリング
    ├── keyword_trends.py             # 新規: キーワードトレンド分析
    ├── discover.py                   # 新規: フィード自動発見
    └── evolve_feeds.py               # 新規: 統合進化スクリプト

.github/workflows/
└── tech-harvester-evolution.yml      # 新規: 定期実行ワークフロー

.github/skill-registry.json          # 変更: 自動PRで更新される

(リポジトリルート or .github/skills/tech-harvester/)
└── harvest-state.json                # 新規: 永続化ストア（.gitignore対象外）
```

---

## 実装優先順位

| フェーズ | 優先度 | 工数感 | 効果 |
|---------|--------|--------|------|
| Phase 1: 品質トラッキング | 高 | 小 | フィード健全性の可視化 |
| Phase 2: キーワード分析 | 高 | 中 | Digest の情報密度向上 |
| Phase 3: フィード発見 | 中 | 中 | 収集源の自動拡張 |
| Phase 4: 統合進化 | 中 | 中 | PR自動生成で人手不要に |
| Phase 5: GitHub Actions | 低 | 小 | 完全自動化 |

**推奨実装順**: Phase 1 → Phase 2 → Phase 4 → Phase 3 → Phase 5

Phase 2（キーワード分析）が最もユーザー体験に直結し、かつ Phase 3（発見）の入力としても機能するため、早期に実装する価値が高い。

---

## 設計上の判断ポイント

### `harvest-state.json` の配置場所

**案A: リポジトリにコミット**（推奨）
- 履歴が残り、進化の過程が追跡可能
- PR で変更が明示的にレビューできる
- `.gitattributes` で diff を読みやすくする

**案B: GitHub Actions の Artifact**
- リポジトリを汚染しない
- 過去履歴へのアクセスが煩雑

### LLM の使い方

スキル内からの LLM 呼び出しは Claude Code のエージェント自身（`SKILL.md` のワークフロー）が担う。スクリプトは「LLM に渡すプロンプト素材を作る」役割に徹し、実際の推論はエージェントが行う。これにより API キー管理が不要になる。

例外: GitHub Actions での完全無人実行が必要な場合は Anthropic API を直接呼び出すオプションを追加する（`ANTHROPIC_API_KEY` をシークレットに設定）。

### フィード追加の承認フロー

完全自動マージはリスクが高いため、**PR 作成まで自動、マージは人手**とする。PR には根拠情報を充実させて判断コストを下げる。将来的に信頼度が上がれば auto-merge を検討する。
