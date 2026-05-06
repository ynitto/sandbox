# ingest — ソースを取り込む

ingest はこのスキルの中核操作。**エージェントが** ソースを読み込み、Wiki ページを生成・更新する。
ソースデータは取り込み前に保全済みであることを前提とする（コピーは行わない）。

パス規約:
- 生成・更新する Wiki 側のローカルパスは `wiki_root` 起点の相対パスで記述する
- 絶対パスは使わない

---

## ケース A: 単一ファイル / URL の場合

以下の「全ステップ」を 1 回実行する。

---

## ケース B: フォルダ指定（複数ファイル）の場合

### B-1. バッチ状態を初期化する

```bash
python scripts/wiki_ingest.py init-batches --source <フォルダパス> [--batch-size 5]
```

出力例:
```
[OK] バッチ状態を初期化しました: .wiki-batch-state.json
     合計 311 ファイル・63 バッチ（バッチサイズ: 5）

next-batch を実行して最初のバッチを取得してください。
```

### B-2. 次のバッチを取得する（**1バッチずつ。先読み禁止**）

```bash
python scripts/wiki_ingest.py next-batch
```

出力例:
```
=== BATCH 1/63 ===
完了済み: 0 バッチ / 残り: 63 バッチ（このバッチを含む）

docs/source/file01.md
docs/source/file02.md
...

処理完了後: python scripts/wiki_ingest.py complete-batch --pages-created <N> --pages-updated <M>
```

**このコマンドが出力したファイルリストだけを対象として「全ステップ」を実行する。**

---

## 全ステップ（1 バッチ = N ファイルに対して実行）

### ステップ 1: 現在の Wiki 状態を確認

```bash
python scripts/wiki_query.py list-pages
```

既存ページの一覧と概要を確認し、重複・関連ページを把握する。  
（バッチの最初の 1 回のみ実行すればよい）

### ステップ 2: ソースを読み込み、Wiki ページを生成・更新（エージェントが実行）

**2-1. ソースを精読する**  
バッチ内の各ファイルを read_file で読み込む。  
フロントマターに `published` フィールドがある場合はその値を控える。

**2-2. トピックを抽出する**  
ソースから以下を列挙する:

- **atoms** (`wiki/atoms/`): 「〜とは何か・誰か」と1文で答えられる個別トピック  
  例: 概念・用語・人物・製品・組織など。フロントマターの `type:` で分類する  
  `type` の値: `concept` | `term` | `person` | `organization` | `product`
- **topics** (`wiki/topics/`): 複数のatomsを横断するまとめ・比較・分析  
  例: ある技術の歴史、複数手法の比較、領域全体の概観など

**2-3. 既存ページとの照合**  
各概念について既存ページの有無を確認する（`wiki_query.py search` を使う）。
- 既存ページがある → そのページに情報を**追記・更新**する
- 新しい概念 → 新規ページを**作成**する

**2-4. ページを作成・更新する**  
各ページは「ページフォーマット」（[`page-conventions.md`](page-conventions.md)）に従う。  
1 バッチから 5〜15 ページを作成・更新するのが目安。  
発行日が分かっている場合は、そのソースから書き込んだ情報ブロックの末尾にインライン注記する:

```
*発行: YYYY-MM-DD / [[ソーススラッグ]]*
```

発行日不明の場合は注記不要。

**2-5. クロスリファレンスを追加する**
- 新規ページを参照するページがあれば `[[ページ名]]` リンクを追記する
- 関連概念ページ同士を `## 関連` セクションでつなぐ

### ステップ 3: index.md・log.md・hot.md を更新する

```bash
# index.md に新規ページを登録（atoms/ または topics/ のパスを渡す）
python scripts/wiki_ingest.py update-index --pages <作成したページのパスリスト>

# log.md に操作を記録（バッチ内の各ソースごとに実行）
python scripts/wiki_ingest.py log \
  --source <ソースパス> \
  --pages-created <作成数> \
  --pages-updated <更新数> \
  [--published YYYY-MM-DD]

# hot.md（最近のコンテキスト）を更新（直近 20 件を維持）
python scripts/wiki_ingest.py update-hot --pages <作成・更新したページのパスリスト>
```

### ステップ 4: lint を実行する（**省略禁止**）

```bash
python scripts/wiki_lint.py
```

孤立ページ・リンク切れ・短小ページを確認し、警告があれば修正する。  
修正可能な孤立ページは `--fix` で自動登録できる。

```bash
python scripts/wiki_lint.py --fix
```

---

### B-3. バッチ完了を記録する（**省略禁止**）

ステップ 4 が完了したら、**必ず**以下を実行する:

```bash
python scripts/wiki_ingest.py complete-batch --pages-created <N> --pages-updated <M>
```

- 「残り N バッチ」と表示されたら → **B-2 に戻る**（`next-batch` を実行）
- 「全 N バッチ完了！」と表示されたら → **B-4 へ進む**

### B-4. 取り込み完了の検証（**省略禁止**）

```bash
python scripts/wiki_ingest.py verify-completion --source <フォルダパス>
```

- 出力が `[OK] 全ファイルの取り込みが完了しています` → 最終報告に進む
- 出力が `[WARN] 未完了: N バッチが残っています` → 未処理バッチに対して B-2 から再実行する

**「全バッチ完了: 計 X ページ作成・Y ページ更新」の最終報告は、`verify-completion` が `[OK]` を返した後のみ行う。**

---

## ページ作成・更新の判断基準

| 状況 | 判断 |
|------|------|
| ソースに固有の新しい概念・用語がある | `atoms/` に新規ページを作成する |
| 人物・製品・組織名が登場する | `atoms/` に新規ページを作成する（type: person / product / organization） |
| 既存ページの概念にソースが新情報を提供 | 既存ページに追記・更新する |
| 既存ページとほぼ同じ内容 | 既存ページに出典として追記するだけ |
| 非常に小さな補足情報 | 関連ページの「関連」セクションに1行追記 |
| 複数atomsを横断するまとめ・比較 | `topics/` に作成 |

## ページの長さの目安

肥大化を防ぐため、以下を目安にする。超えた場合は次のingest時に圧縮する。

| セクション | 目安 |
|-----------|------|
| 概要（定義） | 1〜3文・100語以内 |
| 詳細 | 通算400語以内 |
| 特徴・性質 | 箇条書き10項目以内 |
| 関連 | 10リンク以内 |
