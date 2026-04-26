# 段階的開示パターン

SKILL.md をスリムに保ちながら詳細をリファレンスに委譲するパターン集。

## パターン1: ハイレベルガイド + リファレンス

主要な操作はインラインで、高度な機能はリファレンスに委譲する。

```markdown
# PDF処理

## クイックスタート
pdfplumberでテキスト抽出:
[コード例]

## 高度な機能
- **フォーム入力**: [FORMS.md](FORMS.md) 参照
- **APIリファレンス**: [REFERENCE.md](REFERENCE.md) 参照
```

## パターン2: ドメイン別整理

機能がドメインごとに独立している場合、references/ をドメイン単位で分割する。

```
bigquery-skill/
├── SKILL.md（概要とナビゲーション）
└── references/
    ├── finance.md（収益、請求指標）
    ├── sales.md（商談、パイプライン）
    └── product.md（API利用、機能）
```

## パターン3: 条件付き詳細

ユースケースに応じて必要なリファレンスのみ読み込む。

```markdown
# DOCX処理

## ドキュメント作成
docx-jsで新規作成。[DOCX-JS.md](DOCX-JS.md) 参照。

## 編集
単純な編集はXMLを直接変更。
**変更履歴付き**: [REDLINING.md](REDLINING.md) 参照
```

## 共通ルール

- リファレンスは SKILL.md から1階層のみ。深いネストは避ける
- すべてのリファレンスは SKILL.md から直接リンクする
