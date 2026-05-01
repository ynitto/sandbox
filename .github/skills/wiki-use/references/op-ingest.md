# ingest — ソースを取り込む

ingest はこのスキルの中核操作。**エージェントが** ソースを読み込み、Wiki ページを生成・更新する。

---

## ケース A: 単一ファイル / URL の場合

```bash
python scripts/wiki_ingest.py copy --source <ファイルパス|URL> [--published YYYY-MM-DD]
```

コピー後、以下の「全ステップ」を 1 回実行する。

---

## ケース B: フォルダ指定（複数ファイル）の場合

> ⛔ **STOP — 以下の手順を厳守すること。**
> - `next-batch` が返したファイルリスト以外は処理しない
> - 「件数が多いから自動化する」「まとめて処理する」は禁止
> - バッチ総数を見ても「全件把握した」とみなしてはならない

### B-1. バッチ状態を初期化する

```bash
python scripts/wiki_ingest.py init-batches --source <フォルダパス> [--batch-size 5]
```

出力例:
```
[OK] バッチ状態を初期化しました: /path/to/wiki/.wiki-batch-state.json
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

/path/to/file01.md
/path/to/file02.md
...

処理完了後: python scripts/wiki_ingest.py complete-batch --pages-created <N> --pages-updated <M>
```

**このコマンドが出力したファイルリストだけを対象として「全ステップ」を実行する。**

---

## 全ステップ（1 バッチ = N ファイルに対して実行）

### ステップ 1: ソースをコピー

バッチ内の各ファイルを 1 件ずつ `copy` する。  
同一内容が既にある場合は `[SKIP]` と表示されスキップされる。

```bash
# バッチ内のファイルを 1 件ずつ実行
python scripts/wiki_ingest.py copy --source <ファイルパス> [--published YYYY-MM-DD]
```

### ステップ 2: 現在の Wiki 状態を確認

```bash
python scripts/wiki_query.py list-pages
```

既存ページの一覧と概要を確認し、重複・関連ページを把握する。  
（バッチの最初の 1 回のみ実行すればよい）

### ステップ 3: ソースを読み込み、Wiki ページを生成・更新（エージェントが実行）

**3-1. ソースを精読する**  
バッチ内の各ファイルを read_file で読み込む。  
フロントマターに `published` フィールドがある場合はその値を控える。

**3-2. 概念を抽出する**  
ソースから以下を列挙する:
- 新しい概念・用語（`wiki/concepts/` に作成）
- 人物・プロダクト・組織（`wiki/entities/` に作成）
- テーマ・まとめ（`wiki/topics/` に作成）

**3-3. 既存ページとの照合**  
各概念について既存ページの有無を確認する（`wiki_query.py search` を使う）。
- 既存ページがある → そのページに情報を**追記・更新**する
- 新しい概念 → 新規ページを**作成**する

**3-4. ページを作成・更新する**  
各ページは「ページフォーマット」（[`page-conventions.md`](page-conventions.md)）に従う。  
1 バッチから 5〜15 ページを作成・更新するのが目安。  
発行日が分かっている場合は、そのソースから書き込んだ情報ブロックの末尾にインライン注記する:

```
*発行: YYYY-MM-DD / [[ソーススラッグ]]*
```

発行日不明の場合は注記不要（ユーザーに確認しない）。

**3-5. クロスリファレンスを追加する**
- 新規ページを参照するページがあれば `[[ページ名]]` リンクを追記する
- 関連概念ページ同士を `## 関連` セクションでつなぐ

### ステップ 4: index.md・log.md・hot.md を更新する

```bash
# index.md に新規ページを登録
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

### B-3. バッチ完了を記録する（**省略禁止**）

ステップ 4 が完了したら、**必ず**以下を実行する:

```bash
python scripts/wiki_ingest.py complete-batch --pages-created <N> --pages-updated <M>
```

出力例（途中）:
```
[OK] バッチ 1/63 完了（作成: 7 ページ・更新: 2 ページ）
     残り 62 バッチ。

next-batch を実行して次のバッチを取得してください。
```

- 「残り N バッチ」と表示されたら → **B-2 に戻る**（`next-batch` を実行）
- 「全 N バッチ完了！」と表示されたら → **B-4 へ進む**

> ⚠️ `--pages-created` と `--pages-updated` の両方が 0 の場合は `[WARN]` が出る。
> ソースの精読とページ生成が実施されたか確認すること。

### B-4. 取り込み完了の検証（**省略禁止**）

```bash
python scripts/wiki_ingest.py verify-completion --source <フォルダパス>
```

- 出力が `[OK] 全ファイルの取り込みが完了しています` → 最終報告に進む
- 出力が `[WARN] 未完了: N 件のファイルが残っています` → 未処理ファイルに対して B-1 から再実行する

**「全バッチ完了: 計 X ページ作成・Y ページ更新」の最終報告は、`verify-completion` が `[OK]` を返した後のみ行う。**

---

## ページ作成・更新の判断基準

| 状況 | 判断 |
|------|------|
| ソースに固有の新しい概念がある | 新規ページを作成する |
| 既存ページの概念にソースが新情報を提供 | 既存ページに追記・更新する |
| 既存ページとほぼ同じ内容 | 既存ページに出典として追記するだけ |
| 非常に小さな補足情報 | 関連ページの「関連」セクションに1行追記 |
| 固有名詞・人名・組織名 | `entities/` に作成（重複は統合する） |
| 複数概念を横断するテーマ | `topics/` に作成 |
