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
  （スキル実行時 / 手動トリガー）

  fetch_feeds.py    keyword_trends.py  evolve_feeds.py
  → Digest生成      → トレンド分析     → フィード進化
         │                 │                 │
         │          ┌──────▼──────┐          │
         │          │  ltm-use    │◄─────────┤
         │          │  - 過去トレンド参照     │
         │          │  - キーワード蓄積       │
         │          └──────┬──────┘          │
         │                 │                 │
         └─────────────────┴─────────────────┘
                           │
              ┌────────────▼────────────┐
              │    skill-registry.json   │
              │  skill_configs.tech-     │
              │  harvester 領域（ローカル）│
              │  - feeds（既存）         │
              │  - feed_stats            │
              │  - keyword_trends        │
              │  - candidate_feeds       │
              └─────────────────────────┘
              （リポジトリへのプッシュは行わない。
                必要に応じてユーザーがバックアップ）
```

---

## データ構造: `skill-registry.json` の拡張

状態データはすべて `skill-registry.json` の `skill_configs.tech-harvester` 領域に統合する。既存の `feeds` 配列は維持し、以下のセクションを追加する。

```json
{
  "skill_configs": {
    "tech-harvester": {
      "feeds": [ ... ],

      "feed_stats": {
        "Hacker News": {
          "fetch_count": 42,
          "article_count": 380,
          "avg_desc_length": 210,
          "consecutive_failures": 0,
          "last_fetched": "2026-05-01T12:00:00Z",
          "relevance_score": 72.5
        }
      },

      "keyword_trends": {
        "2026-W19": {
          "rising": ["Claude 4", "MCP サーバー", "Rust 2026 edition"],
          "stable": ["Kubernetes", "TypeScript", "LLM評価"],
          "declining": ["GPT-3", "Docker Swarm"]
        }
      },

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
  }
}
```

---

## Phase 1: フィード品質トラッキング

**目的**: どのフィードが「有益な記事」を多く出しているかを定量化する。

### 実装内容

**新ファイル**: `.github/skills/tech-harvester/scripts/scorer.py`

フィードごとの指標を `skill-registry.json` の `feed_stats` セクションに蓄積する。

**スコアリング基準**:
- `avg_desc_length`: 説明文が短すぎるフィードはコンテンツが薄い
- `consecutive_failures`: 3回以上連続失敗 → 削除候補フラグ
- `relevance_score`: LLMが各記事の説明文を見て0〜100点で評価

**`fetch_feeds.py` への変更**:
- フェッチ結果を `skill-registry.json` の `feed_stats` に追記するモードを追加（`--update-stats` フラグ）

---

## Phase 2: キーワード・トレンド分析

**目的**: 収集した記事群から「今週浮上しているトピック」を自動抽出し、次回収集に活かす。

### 実装内容

**新ファイル**: `.github/skills/tech-harvester/scripts/keyword_trends.py`

```
入力: articles.json (fetch_feeds.py の出力)
処理: 記事タイトル+説明文から出現頻度の高いキーワード/トピックを抽出
出力: skill-registry.json の keyword_trends セクションを更新
      + ltm-use にトレンドサマリーを記録
```

**ltm-use との連携**:

キーワード分析の前に ltm-use の直近の記憶を参照し、過去のトレンドと照合する。

- 過去に `rising` だったキーワードが今週も上位なら `stable` に格上げ
- ltm-use に記録された「ユーザーが関心を示した話題」はキーワード候補の優先度を上げる
- 分析後、今週のトレンドサマリーを ltm-use に episodic 記憶として保存し、次回以降の参照に備える

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
出力: skill-registry.json の candidate_feeds セクションを更新
```

**昇格基準** (`discovery_count >= 2` かつ `relevance_score >= 60`):
- 昇格基準を満たした候補フィードはエージェントが `feeds` 配列に直接追記する
- 追記時にエージェントは根拠（どの記事から何回参照されたか）をチャットで報告する

---

## Phase 4: フィード自律進化（統合スクリプト）

**目的**: 上記3フェーズを統合し、`skill-registry.json` への変更提案を GitHub PR として自動生成する。

### 実装内容

**新ファイル**: `.github/skills/tech-harvester/scripts/evolve_feeds.py`

```
処理フロー:
  1. skill-registry.json の feed_stats / candidate_feeds を読み込む
  2. 削除候補フィードを特定
     - consecutive_failures >= 3
     - relevance_score < 30 かつ fetch_count >= 10
  3. 追加候補フィードを特定
     - candidate_feeds から昇格基準を満たすもの
  4. feeds 配列をローカルで直接更新（プッシュは行わない）
  5. 変更サマリーを ltm-use に記録し、チャットで報告
```

**チャット報告例**:
```
## 🌱 tech-harvester フィード進化レポート

### 追加
- **Example Tech Blog** (https://example.com/feed)
  - 発見根拠: 3件の高評価記事から参照
  - 関連性スコア: 85/100
  - 付与タグ: japanese, tech, blog

### 削除
- **低品質フィード名**
  - 理由: 3回連続フェッチ失敗 / 関連性スコア 18/100

skill-registry.json をローカルで更新しました。
```

---

## ファイル構成（変更後）

```
.github/skills/tech-harvester/
├── SKILL.md                          # 更新: 進化ワークフロー追記
└── scripts/
    ├── fetch_feeds.py                # 更新: --update-stats オプション追加
    ├── scorer.py                     # 新規: フィード品質スコアリング
    ├── keyword_trends.py             # 新規: キーワードトレンド分析
    ├── discover.py                   # 新規: フィード自動発見
    └── evolve_feeds.py               # 新規: 統合進化スクリプト

.github/skill-registry.json          # 変更: feeds + 進化データを一元管理（ローカルのみ）
```

---

## 実装優先順位

| フェーズ | 優先度 | 工数感 | 効果 |
|---------|--------|--------|------|
| Phase 1: 品質トラッキング | 高 | 小 | フィード健全性の可視化 |
| Phase 2: キーワード分析 | 高 | 中 | Digest の情報密度向上 |
| Phase 3: フィード発見 | 中 | 中 | 収集源の自動拡張 |
| Phase 4: 統合進化 | 中 | 中 | ローカル自動更新で人手不要に |

**推奨実装順**: Phase 1 → Phase 2 → Phase 4 → Phase 3

Phase 2（キーワード分析）が最もユーザー体験に直結し、かつ Phase 3（発見）の入力としても機能するため、早期に実装する価値が高い。

---

## 設計上の判断ポイント

### 状態データの配置

状態データ（`feed_stats`・`keyword_trends`・`candidate_feeds`）は `skill-registry.json` の `skill_configs.tech-harvester` 領域に統合する。

- フィード設定と状態を1ファイルで管理でき、参照・更新のパスが統一される
- 設定ファイルはローカルで育っていく。リポジトリへのプッシュは行わず、バックアップが必要な場合はユーザーが任意で取る

`keyword_trends` は直近 N 週分のみ保持し、古いエントリは自動削除してファイルサイズを抑える。

### ltm-use の活用

ltm-use をキーワード・フィード進化の長期記憶として使う。

- **キーワード蓄積**: トレンド分析のたびに今週のキーワードサマリーを episodic 記憶として保存する。次回の分析時にこれらを参照し、継続して浮上しているキーワードを `stable` へ格上げしたり、ユーザーの関心と照合して優先度を調整したりする
- **フィード進化ログ**: フィードの追加・削除が発生したとき、その根拠と日時を ltm-use に記録する。将来「なぜこのフィードを追加したか」を振り返れるようにする
- **ユーザーの関心との連携**: 他のスキルが ltm-use に残した記憶（調査内容、気になったトピックなど）をキーワード候補の入力として活用し、ユーザーの文脈に合った収集先へ自然に育てる
