## 目次

- [ワークフロー](#ワークフロー)
- [スキーマ](#スキーマ)
- [フィルター構文](#フィルター構文)
- [プロパティ](#プロパティ)
- [フォーミュラ構文](#フォーミュラ構文)
- [ビュータイプ](#ビュータイプ)
- [サマリー](#サマリー)
- [YAMLクォートルール](#yamlクォートルール)
- [トラブルシューティング](#トラブルシューティング)
- [完全な例](#完全な例)

# Obsidian Bases

`.base` ファイルを作成・編集する。BaseはObsidianボルト内でデータベースライクなビューを提供する。

## ワークフロー

1. **ファイル作成**: ボルト内に有効なYAMLコンテンツで `.base` ファイルを作成
2. **スコープ定義**: `filters` でどのノートを表示するか選択（タグ・フォルダ・プロパティ・日付）
3. **フォーミュラ追加**（任意）: `formulas` セクションで計算プロパティを定義
4. **ビュー設定**: `table`・`cards`・`list`・`map` のいずれかでビューを追加
5. **バリデーション**: 有効なYAMLか確認。よくある問題: 特殊YAML文字を含む未クォート文字列・フォーミュラのクォート不一致・未定義の `formula.X` 参照
6. **Obsidianでテスト**: `.base` ファイルをObsidianで開いてビューが正しくレンダリングされることを確認

## スキーマ

```yaml
# グローバルフィルター（全ビューに適用）
filters:
  and: []
  or: []
  not: []

# フォーミュラプロパティの定義
formulas:
  formula_name: 'expression'

# プロパティの表示名・設定
properties:
  property_name:
    displayName: "表示名"
  formula.formula_name:
    displayName: "フォーミュラ表示名"

# カスタムサマリーフォーミュラ
summaries:
  custom_summary_name: 'values.mean().round(3)'

# ビューの定義
views:
  - type: table | cards | list | map
    name: "ビュー名"
    limit: 10
    groupBy:
      property: property_name
      direction: ASC | DESC
    filters:
      and: []
    order:
      - file.name
      - property_name
      - formula.formula_name
    summaries:
      property_name: Average
```

## フィルター構文

フィルターはグローバルまたはビューごとに適用できる。

```yaml
# 単一フィルター
filters: 'status == "done"'

# AND - すべての条件が真
filters:
  and:
    - 'status == "done"'
    - 'priority > 3'

# OR - いずれかの条件が真
filters:
  or:
    - 'file.hasTag("book")'
    - 'file.hasTag("article")'

# NOT - 一致するものを除外
filters:
  not:
    - 'file.hasTag("archived")'

# ネストしたフィルター
filters:
  or:
    - file.hasTag("tag")
    - and:
        - file.hasTag("book")
        - file.hasLink("教科書")
    - not:
        - file.hasTag("book")
        - file.inFolder("必読")
```

### フィルター演算子

| 演算子 | 説明 |
|--------|------|
| `==` | 等しい |
| `!=` | 等しくない |
| `>` | より大きい |
| `<` | より小さい |
| `>=` | 以上 |
| `<=` | 以下 |
| `&&` | 論理AND |
| `\|\|` | 論理OR |
| `!` | 論理NOT |

## プロパティ

### 3種類のプロパティ

1. **ノートプロパティ** - フロントマターから: `note.author` または `author`
2. **ファイルプロパティ** - ファイルメタデータ: `file.name`・`file.mtime` など
3. **フォーミュラプロパティ** - 計算値: `formula.my_formula`

### ファイルプロパティ一覧

| プロパティ | 型 | 説明 |
|-----------|-----|------|
| `file.name` | 文字列 | ファイル名 |
| `file.basename` | 文字列 | 拡張子なしファイル名 |
| `file.path` | 文字列 | フルパス |
| `file.folder` | 文字列 | 親フォルダパス |
| `file.ext` | 文字列 | 拡張子 |
| `file.size` | 数値 | バイト単位のサイズ |
| `file.ctime` | 日付 | 作成日時 |
| `file.mtime` | 日付 | 更新日時 |
| `file.tags` | リスト | ファイル内の全タグ |
| `file.links` | リスト | 内部リンク |
| `file.backlinks` | リスト | このファイルへのバックリンク |

### `this` キーワード

- メインコンテンツエリア: baseファイル自身を指す
- 埋め込み時: 埋め込み元のファイルを指す
- サイドバー: メインコンテンツのアクティブファイルを指す

## フォーミュラ構文

```yaml
formulas:
  # 単純な算術
  total: "price * quantity"

  # 条件ロジック
  status_icon: 'if(done, "✅", "⏳")'

  # 文字列フォーマット
  formatted_price: 'if(price, price.toFixed(2) + " 円")'

  # 日付フォーマット
  created: 'file.ctime.format("YYYY-MM-DD")'

  # 作成からの日数（Duration には .days でアクセス）
  days_old: '(now() - file.ctime).days'

  # 期限までの日数
  days_until_due: 'if(due_date, (date(due_date) - today()).days, "")'
```

### 主要関数

| 関数 | シグネチャ | 説明 |
|------|-----------|------|
| `date()` | `date(string): date` | 文字列を日付にパース |
| `now()` | `now(): date` | 現在日時 |
| `today()` | `today(): date` | 今日の日付 |
| `if()` | `if(condition, trueResult, falseResult?)` | 条件分岐 |
| `duration()` | `duration(string): duration` | 期間文字列をパース |
| `file()` | `file(path): file` | ファイルオブジェクトを取得 |
| `link()` | `link(path, display?): Link` | リンクを作成 |

### Duration 型

日付を引き算すると **Duration** 型（数値ではない）になる。

**Durationフィールド**: `duration.days`・`duration.hours`・`duration.minutes`・`duration.seconds`

**重要**: Duration に直接 `.round()`・`.floor()`・`.ceil()` は使えない。先に `.days` 等で数値にアクセスしてから適用する。

```yaml
# 正しい: 日付間の日数を計算
"(date(due_date) - today()).days"          # 日数を数値で返す
"(now() - file.ctime).days.round(0)"       # 丸めた日数

# 誤り（エラーになる）:
# "((date(due) - today()) / 86400000).round(0)"
```

## ビュータイプ

### テーブルビュー

```yaml
views:
  - type: table
    name: "マイテーブル"
    order:
      - file.name
      - status
      - due_date
    summaries:
      price: Sum
```

### カードビュー

```yaml
views:
  - type: cards
    name: "ギャラリー"
    order:
      - file.name
      - cover_image
      - description
```

### リストビュー

```yaml
views:
  - type: list
    name: "シンプルリスト"
    order:
      - file.name
      - status
```

### マップビュー

緯度・経度プロパティとMapsコミュニティプラグインが必要。

## サマリー

| 名前 | 入力型 | 説明 |
|------|--------|------|
| `Average` | 数値 | 平均 |
| `Min` | 数値 | 最小値 |
| `Max` | 数値 | 最大値 |
| `Sum` | 数値 | 合計 |
| `Median` | 数値 | 中央値 |
| `Earliest` | 日付 | 最も古い日付 |
| `Latest` | 日付 | 最も新しい日付 |
| `Checked` | 真偽値 | trueの件数 |
| `Empty` | 任意 | 空値の件数 |
| `Filled` | 任意 | 非空値の件数 |
| `Unique` | 任意 | ユニーク値の件数 |

## YAMLクォートルール

- ダブルクォートを含むフォーミュラにはシングルクォートを使う: `'if(done, "Yes", "No")'`
- 単純な文字列にはダブルクォートを使う: `"ビュー名"`
- `:` `{` `}` `[` `]` `,` `&` `*` `#` `?` `|` `-` `<` `>` `=` `!` `%` `@` `` ` `` を含む文字列はクォートが必要

## トラブルシューティング

**YAML構文エラー: 未クォートの特殊文字**

```yaml
# 誤り
displayName: Status: Active

# 正しい
displayName: "Status: Active"
```

**フォーミュラのクォート不一致**

```yaml
# 誤り
formulas:
  label: "if(done, "Yes", "No")"

# 正しい
formulas:
  label: 'if(done, "Yes", "No")'
```

**Duration演算でフィールドアクセスなし**

```yaml
# 誤り
"(now() - file.ctime).round(0)"

# 正しい
"(now() - file.ctime).days.round(0)"
```

**nullチェックなし**

```yaml
# 誤り（due_dateが空だとクラッシュ）
"(date(due_date) - today()).days"

# 正しい
'if(due_date, (date(due_date) - today()).days, "")'
```

**未定義フォーミュラの参照**

```yaml
# formulas で定義せずに order で参照するとエラー
order:
  - formula.total

# 正しい: formulas で定義する
formulas:
  total: "price * quantity"
```

## 完全な例

### タスクトラッカー

```yaml
filters:
  and:
    - file.hasTag("task")
    - 'file.ext == "md"'

formulas:
  days_until_due: 'if(due, (date(due) - today()).days, "")'
  priority_label: 'if(priority == 1, "🔴 高", if(priority == 2, "🟡 中", "🟢 低"))'

properties:
  formula.days_until_due:
    displayName: "期限まで"
  formula.priority_label:
    displayName: 優先度

views:
  - type: table
    name: "進行中タスク"
    filters:
      and:
        - 'status != "done"'
    order:
      - file.name
      - status
      - formula.priority_label
      - due
      - formula.days_until_due
    groupBy:
      property: status
      direction: ASC
```

### 読書リスト

```yaml
filters:
  or:
    - file.hasTag("book")
    - file.hasTag("article")

formulas:
  status_icon: 'if(status == "reading", "📖", if(status == "done", "✅", "📚"))'

views:
  - type: cards
    name: "ライブラリ"
    order:
      - cover
      - file.name
      - author
      - formula.status_icon
```

## Markdownファイルへの埋め込み

```markdown
![[MyBase.base]]

<!-- 特定ビュー -->
![[MyBase.base#ビュー名]]
```

## 参考リンク

- Bases構文: https://help.obsidian.md/bases/syntax
- 関数: https://help.obsidian.md/bases/functions
- ビュー: https://help.obsidian.md/bases/views
