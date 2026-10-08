# 新規生成のスペック（build）

テンプレートが無いときに、JSON スペックから .docx を作る。サンプルはスキルの `assets/spec.example.json`（`example` サブコマンドでも出る）。

```json
{
  "filename": "report.docx",
  "page": {"size": "A4", "orientation": "portrait", "margins_cm": [2.5, 2.5, 2.5, 2.5]},
  "style": {"font": "游明朝", "heading_font": "游ゴシック", "size": 10.5, "accent": "1F4E79", "text": "222222"},
  "numbered_headings": true,
  "properties": {"title": "業務改善の報告", "creator": "企画部"},
  "blocks": [
    {"type": "title", "text": "業務改善の報告", "subtitle": "確認の時間を半分にする", "date": "2026 年 10 月 8 日"},
    {"type": "heading", "text": "概要", "level": 1},
    {"type": "paragraph", "text": "1 行め\n2 行め（同じ段落の中の改行）\n\n次の段落"},
    {"type": "bullets", "items": ["確認者の偏り", {"text": "差し戻し", "children": ["月 40 件"]}]},
    {"type": "numbered", "items": ["調べる", "決める", "試す"]},
    {"type": "table", "columns": ["項目", "金額"], "rows": [["設計", "120 万円"]]},
    {"type": "page_break"}
  ]
}
```

| 項目 | 意味 |
|------|------|
| `filename` | 出力のパス（既定 `document.docx`） |
| `page` | 用紙（`A4`（既定）・`A3`・`B5`・`Letter`）、向き（`portrait`・`landscape`）、余白（上・下・左・右の cm） |
| `style` | 本文のフォント・見出しのフォント・本文の文字の大きさ（pt）・色（16 進）。`accent` は見出し・表の見出しの塗り |
| `numbered_headings` | `true`（既定）なら、見出し 1〜3 に章の番号（1.・1.1・1.1.1）をスタイルの番号で振る。文字には番号を書かない |
| `properties` | 文書のプロパティ（`title`・`creator`） |

## ブロックの種類

| `type` | 中身 |
|--------|------|
| `title` | 表題（Title のスタイル）。`subtitle`・`date`・`author` は続く段落（日付・作成者は右寄せ） |
| `heading` | 見出し。`level` は 1〜3（Heading 1〜3 のスタイル） |
| `paragraph` | 本文。`\n` は段落の中の改行、空行（`\n\n`）は段落の区切り |
| `bullets` | 記号のリスト。`items` は文字列の配列、下の段は `{text, level}` か `{text, children}`。3 段まで |
| `numbered` | 番号のリスト（1. → (1) → 1)）。ブロックごとに 1 から振る |
| `table` | 表。`columns`（見出し）と `rows`（配列の配列、または見出しをキーにしたオブジェクト）。見出しの行は塗り、ページをまたぐと繰り返す |
| `page_break` | 改ページ |

- スタイルは Word の組み込みのスタイル（Normal・Heading 1〜3・Title・List Paragraph・Table Grid）に書く。Word で開いてスタイルを直せば、文書全体に効く
- 作った文書は、そのままテンプレートとして `analyze` に渡せる（見出しで部分に分かれ、リスト・表・合計の行が拾われる）
