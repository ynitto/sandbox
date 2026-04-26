---
name: tech-harvester
description: "IT系RSSフィードを取得して要約をマークダウンにまとめるスキル。「技術ニュースを取得して」「RSSをまとめて」「ITニュースのダイジェストを作って」「最新の技術情報を集めて」「テックブログをまとめて」などで発動する。フィードの追加・削除は agent home 直下の skill-registry.json で管理する。"
metadata:
  version: "1.1.0"
  category: productivity
  tags:
    - rss
    - news
    - digest
    - it
---

# Tech Harvester

IT系RSSフィードを取得し、テーマ別に整理して日本語要約付きのマークダウンダイジェストを生成するスキル。

## ワークフロー

### ステップ1: 記事を取得する

```bash
python .github/skills/tech-harvester/scripts/fetch_feeds.py
```

オプション:

| オプション | 説明 | 例 |
|---|---|---|
| `--max-items N` | フィードあたりの最大記事数（デフォルト: 10） | `--max-items 5` |
| `--lang ja\|en` | 言語でフィードを絞り込む | `--lang ja` |
| `--tags TAG1,TAG2` | タグでフィードを絞り込む | `--tags cloud,ai` |
| `--output FILE` | 結果をJSONファイルに書き出す | `--output articles.json` |

スクリプトは記事一覧をJSON形式で出力する。

### ステップ2: テーマ別に整理して日本語要約を生成する

取得したJSON（`articles` 配列）を元に以下を行う:

1. 記事のタイトルと説明文からテーマを判定し、関連する記事をグループ化する
2. 各記事の要約を日本語で1〜2文にまとめる
3. 「出力フォーマット」に従いMarkdown形式のファイルを生成する

テーマの例: `AI・機械学習`, `クラウド・インフラ`, `開発ツール・OSS`, `セキュリティ`, `フロントエンド・Web`, `キャリア・コミュニティ` など。記事の内容に応じて適切なテーマを設定する。

## フィード管理

ユーザーからフィードの追加・削除・変更を求められた場合は `.github/skill-registry.json` の `tech-harvester.feeds` 配列を直接編集する。

使用できる `tags` の例: `general`, `cloud`, `aws`, `ai`, `github`, `japanese`, `devops`, `enterprise`, `oss`, `news`

## 出力フォーマット

```markdown
# IT Tech Digest

_YYYY-MM-DD HH:MM UTC_

## テーマ名

### [記事タイトル](URL)

日本語による1〜2文の要約。

---
```

## トラブルシューティング

- **フィードの取得に失敗する**: スクリプトは `[WARN]` を stderr に出力してスキップする。
- **説明文が空の記事**: タイトルから内容を推定して日本語要約を生成する。
- **Atom形式のフィード**: `fetch_feeds.py` はRSS 2.0とAtomの両方に対応している。
