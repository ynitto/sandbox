# 新規生成のスペック（build）

テンプレートが無いときに、JSON スペックから .pptx を作る。サンプルはスキルの `assets/spec.example.json`（`example` サブコマンドでも出る）。

```json
{
  "filename": "proposal.pptx",
  "size": "16:9",
  "style": {"font": "Meiryo", "accent": "1F4E79", "text": "222222", "light": "DDEBF7", "line": "7F7F7F"},
  "properties": {"title": "業務改善のご提案", "creator": "企画部"},
  "slides": [
    {"type": "title", "title": "業務改善のご提案", "subtitle": "2026 年 10 月"},
    {"type": "bullets", "title": "現状の課題", "bullets": ["確認に 30 分かかる", {"text": "確認者が 2 人", "level": 1}]},
    {"type": "flow", "title": "進め方", "shape": "rounded", "steps": [{"label": "調査", "note": "2 週間"}, {"label": "設計"}]},
    {"type": "cards", "title": "効果", "columns": 3, "cards": [{"title": "時間", "body": "確認の時間を半分に"}]},
    {"type": "table", "title": "費用", "columns": ["項目", "金額"], "rows": [["設計", "120 万円"]]}
  ]
}
```

| 項目 | 意味 |
|------|------|
| `filename` | 出力のパス（既定 `presentation.pptx`） |
| `size` | `16:9`（既定）か `4:3` |
| `style` | フォントと色（16 進）。`accent` は見出し・図形、`light` はカードの塗り、`line` は矢印 |
| `properties` | 文書のプロパティ（`title`・`creator`） |
| `slides[].notes` | ノート（発表者のメモ） |

## スライドの種類

| `type` | 中身 | 収まる量（超えると止まる） |
|--------|------|------------------------------|
| `title` | 表紙。`title`・`subtitle` | 表題 2 行まで |
| `bullets` | 見出しと箇条書き。`bullets` は文字列の配列、下の段は `{text, level}` か `{text, children}`。行頭の記号は段落の書式で付く（文字に混ぜない） | 本文の枠の行数（18pt） |
| `table` | 見出しと表。`columns`（見出し）と `rows`（配列の配列、または見出しをキーにしたオブジェクト） | 1 枚に入る行数。1 セル 2 行まで |
| `flow` | 手順。`steps[].label`（図形の中）と `steps[].note`（図形の下）。`shape` は `rounded`（既定）・`rect`・`ellipse`・`chevron`。図形と、つなぐ相手を持つ矢印のコネクタで描く（`chevron` は矢印なし） | 図形 1 つの幅が 3.3 cm を下回らない段数 |
| `cards` | 並列の項目。`cards[].title`・`cards[].body`。角丸の図形を `columns` 列の格子に並べる | 2 段まで |

- 図はすべて pptx の図形とコネクタで描く。画像は使わない
- 収まらない内容は、どのスライドの何を減らすかを挙げて止まる（`--allow-overflow` で警告にする）。文字の大きさは変えない
- 作った資料は、そのままテンプレートとして `analyze` に渡せる（手順は図の並び、カードは格子の並びとして拾われる）
