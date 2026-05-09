---
name: tech-harvester
description: "RSSフィードを取得して要約をMarkdownにまとめるスキル。「ニュースを取得して」「テックブログをまとめて」「ダイジェストを作って」などで発動。フィード・キーワードの自律進化も対応：「フィードを最適化して」「トレンドを分析して」「新しいフィードを探して」なども発動トリガー。"
metadata:
  version: "2.0.0"
  category: productivity
  tags:
    - rss
    - news
    - digest
    - it
---

# Tech Harvester

RSSフィードを取得し、テーマ別に整理して日本語要約付きのMarkdownダイジェストを生成するスキル。
収集先・キーワードが使うたびに自律的に改善される進化機能も備える。

## ワークフロー A: ダイジェスト生成（通常利用）

### ステップ1: 記事を取得する

```bash
python .github/skills/tech-harvester/scripts/fetch_feeds.py --output articles.json
```

`--update-stats` を付けると取得後に自動で `feed_stats` を更新する（推奨）:

```bash
python .github/skills/tech-harvester/scripts/fetch_feeds.py --output articles.json --update-stats
```

オプション:

| オプション | 説明 | 例 |
|---|---|---|
| `--max-items N` | フィードあたりの最大記事数（デフォルト: 10） | `--max-items 5` |
| `--lang ja\|en` | 言語でフィードを絞り込む | `--lang ja` |
| `--tags TAG1,TAG2` | タグでフィードを絞り込む | `--tags cloud,ai` |
| `--output FILE` | 結果をJSONファイルに書き出す | `--output articles.json` |
| `--update-stats` | フェッチ後に feed_stats を更新する | — |

スクリプトは記事一覧をJSON形式で出力する。

### ステップ2: テーマ別に整理して日本語要約を生成する

取得したJSON（`articles` 配列）を元に以下を行う:

1. 記事のタイトルと説明文からテーマを判定し、関連する記事をグループ化する
2. 各記事の要約を日本語で1〜2文にまとめる
3. 「出力フォーマット」に従いMarkdown形式のファイルを生成する

テーマの例: `AI・機械学習`, `クラウド・インフラ`, `開発ツール・OSS`, `セキュリティ`, `フロントエンド・Web`, `キャリア・コミュニティ` など。記事の内容に応じて適切なテーマを設定する。

キーワードトレンドが蓄積されている場合は「今週の注目キーワード」セクションを先頭に追加する:

```markdown
## 今週の注目キーワード

`キーワード1` `キーワード2` `キーワード3`
```

---

## ワークフロー B: キーワードトレンド分析

「トレンドを分析して」「今週のキーワードをまとめて」などで発動する。

```bash
python .github/skills/tech-harvester/scripts/keyword_trends.py --articles articles.json
```

- articles.json がなければ先にステップ1を実行する
- 実行後、`skill-registry.json` の `keyword_trends` セクションに今週のデータが書き込まれる
- ltm-use が利用可能であれば、トレンドサマリーが episodic 記憶として自動保存される

オプション:

| オプション | 説明 | 例 |
|---|---|---|
| `--week YYYY-WNN` | 対象週（省略時: 今週） | `--week 2026-W19` |
| `--keep-weeks N` | 保持する週数（デフォルト: 8） | `--keep-weeks 4` |
| `--no-ltm` | ltm-use への保存をスキップ | — |

保存済みトレンドを確認する:

```bash
python .github/skills/tech-harvester/scripts/keyword_trends.py show
```

---

## ワークフロー C: フィード自動発見

「新しいフィードを探して」「収集先を増やして」などで発動する。

```bash
python .github/skills/tech-harvester/scripts/discover.py --articles articles.json
```

記事の説明文・リンクから外部ドメインを抽出し、RSS/Atom フィードを試行する。
発見した候補は `skill-registry.json` の `candidate_feeds` に追記される。

オプション:

| オプション | 説明 | 例 |
|---|---|---|
| `--top-domains N` | 調査するドメイン上位数（デフォルト: 15） | `--top-domains 20` |
| `--timeout SEC` | フィード確認のタイムアウト秒数（デフォルト: 8） | `--timeout 5` |

候補一覧を確認する:

```bash
python .github/skills/tech-harvester/scripts/discover.py show
```

候補の `relevance_score` を評価・設定する（0〜100点）:

```bash
python .github/skills/tech-harvester/scripts/scorer.py set-relevance "フィード名" 80.0
```

---

## ワークフロー D: フィード自律進化（統合）

「フィードを最適化して」「不要なフィードを整理して」などで発動する。

まず変更内容をプレビューする:

```bash
python .github/skills/tech-harvester/scripts/evolve_feeds.py --dry-run
```

問題なければ適用する:

```bash
python .github/skills/tech-harvester/scripts/evolve_feeds.py
```

**削除基準**:
- 3回以上連続フェッチ失敗
- 関連性スコアが 30 未満かつ取得回数が 10 以上

**追加基準（`candidate_feeds` からの昇格）**:
- 発見数 2 回以上かつ関連性スコアが 60 以上

すべてローカルで更新する。リポジトリへのプッシュは行わない。
変更内容は ltm-use に進化ログとして記録される。

オプション:

| オプション | 説明 |
|---|---|
| `--dry-run` | プレビューのみ（適用しない） |
| `--only-remove` | 削除のみ実行 |
| `--only-add` | 追加のみ実行 |
| `--min-discovery N` | 昇格に必要な発見回数（デフォルト: 2） |
| `--min-relevance SCORE` | 昇格に必要な関連性スコア（デフォルト: 60） |
| `--no-ltm` | ltm-use へのログ保存をスキップ |

---

## フィード管理（手動）

ユーザーからフィードの追加・削除・変更を求められた場合は `.github/skill-registry.json` の `tech-harvester.feeds` 配列を直接編集する。

使用できる `tags` の例: `general`, `cloud`, `aws`, `ai`, `github`, `japanese`, `devops`, `enterprise`, `oss`, `news`

フィード品質の統計を確認する:

```bash
python .github/skills/tech-harvester/scripts/scorer.py show
```

---

## `skill-registry.json` の構造

```json
{
  "skill_configs": {
    "tech-harvester": {
      "feeds": [...],
      "feed_stats": {
        "フィード名": {
          "fetch_count": 42,
          "article_count": 380,
          "avg_desc_length": 210.5,
          "consecutive_failures": 0,
          "last_fetched": "2026-05-09T12:00:00Z",
          "relevance_score": 75.0
        }
      },
      "keyword_trends": {
        "2026-W19": {
          "rising": ["Claude 4", "MCP"],
          "stable": ["Kubernetes", "TypeScript"],
          "declining": ["GPT-3"]
        }
      },
      "candidate_feeds": [
        {
          "name": "Example Tech Blog",
          "url": "https://example.com/feed",
          "lang": "ja",
          "discovered_from": ["https://..."],
          "discovery_count": 3,
          "relevance_score": 0.0,
          "suggested_tags": ["japanese", "tech", "blog"],
          "status": "pending"
        }
      ]
    }
  }
}
```

`feed_stats`・`keyword_trends`・`candidate_feeds` はローカルで育っていく。
リポジトリへのプッシュは行わず、バックアップが必要な場合はユーザーが任意で行う。

---

## 出力フォーマット

```markdown
# Tech Digest

_YYYY-MM-DD HH:MM UTC_

## 今週の注目キーワード

`キーワード1` `キーワード2` `キーワード3`

---

## テーマ名

### [記事タイトル](URL)

日本語による1〜2文の要約。

---
```

## トラブルシューティング

- **フィードの取得に失敗する**: スクリプトは `[WARN]` を stderr に出力してスキップする。`--update-stats` を使っていれば `consecutive_failures` が自動加算される。
- **説明文が空の記事**: タイトルから内容を推定して日本語要約を生成する。
- **Atom形式のフィード**: `fetch_feeds.py` はRSS 2.0とAtomの両方に対応している。
- **ltm-use が見つからない**: ltm-use スキルがインストールされていない場合は ltm 連携をスキップしてスクリプトは正常終了する。
- **フィード発見で何も見つからない**: RSS を公開していないサイトや説明文にリンクを含まないフィードでは候補が出ない。手動でフィードを追加する。
