---
name: tech-harvester
description: "IT系RSSフィードを取得して要約をマークダウンにまとめるスキル。「技術ニュースを取得して」「RSSをまとめて」「ITニュースのダイジェストを作って」「最新の技術情報を集めて」「テックブログをまとめて」などで発動する。フィードの追加・削除は agent home 直下の skill-registry.json で管理する。"
metadata:
  version: "1.0.1"
  category: productivity
  tags:
    - rss
    - news
    - digest
    - it
---

# Tech Harvester

IT系RSSフィードを取得し、記事タイトル・リンク・要約をマークダウン形式のダイジェストにまとめるスキル。

## ワークフロー

### ステップ1: ダイジェストを生成する

```bash
python .github/skills/tech-harvester/scripts/fetch_feeds.py
```

オプションで絞り込みが可能:

| オプション | 説明 | 例 |
|---|---|---|
| `--max-items N` | フィードあたりの最大記事数（デフォルト: 5） | `--max-items 3` |
| `--lang ja\|en` | 言語でフィードを絞り込む | `--lang ja` |
| `--tags TAG1,TAG2` | タグでフィードを絞り込む | `--tags cloud,ai` |
| `--output FILE` | 結果をファイルに書き出す | `--output digest.md` |

例: 日本語フィードのみ、各3件:

```bash
python .github/skills/tech-harvester/scripts/fetch_feeds.py --lang ja --max-items 3
```

### ステップ2: 出力を整形して返す

スクリプトが出力したマークダウンをそのままユーザーに提示する。ファイル保存を求められた場合は `--output` を使う。

## フィード管理

ユーザーからフィードの追加・削除・変更を求められた場合は `.github/skill-registry.json` の `tech-harvester.feeds` 配列を直接編集する。

使用できる `tags` の例: `general`, `cloud`, `aws`, `ai`, `github`, `japanese`, `devops`, `enterprise`, `oss`, `news`

## 出力フォーマット

```markdown
# IT Tech Digest

_Generated: YYYY-MM-DD HH:MM UTC_

## フィード名

### [記事タイトル](URL)
_公開日時_

記事の要約（最大200文字）
```

## トラブルシューティング

- **フィードの取得に失敗する**: スクリプトは `[WARN]` を stderr に出力してスキップする。
- **要約が空**: RSSフィードによっては説明文を含まない場合がある。タイトルとリンクのみ表示される。
- **Atom形式のフィード**: `fetch_feeds.py` はRSS 2.0とAtomの両方に対応している。
