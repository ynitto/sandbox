# doc-lint

Markdown の書式、日本語の文章、Mermaid と PlantUML の構文をまとめて点検する CLI です。結果を端末や CI で確認できます。図の検査には Java や Chromium は不要です。

## 入れる

```sh
node tools/doc-lint/install.js
node tools/doc-lint/install.js --check
```

インストーラーは `npm pack` で作ったパッケージを `npm install -g` で入れます。`--check` は同梱の例で `doctor` と `check` を実行します。外すときは `npm uninstall -g doc-lint` を実行します。

CLI には Node.js 22.12.0 以上が必要です。図の評価器には計画書で指定された Mermaid 12.2.0 と `@plantuml/mcp-js` 0.2.2 を使用します。

## 使う

点検したいリポジトリの直下で実行します。

```sh
doc-lint init --preset ja
doc-lint install
doc-lint doctor
doc-lint check
```

- `init` は設定と `package.json` の開発依存を用意します。既存の設定は保持し、`--force` を付けると既定の設定で上書きします。依存関係のインストールは次のコマンドで行います。
- `install` は現在のディレクトリで `npm install` を実行します。`--frozen-lockfile` を付けると既存のロックファイルを使って `npm ci` を実行します。
- `doctor` は Node.js のバージョン、評価器の解決元、評価器による短い入力の点検結果を表示します。解決元は対象リポジトリが `repo`、doc-lint の依存が `bundled`、未検出が `missing` です。
- `tools add <pkg>` と `tools remove <pkg>` は対象リポジトリで npm の依存を変更し、成功後に設定の `tools` を更新します。Phase 1 では、textlint が有効な場合に `tools` に登録された `textlint-rule-*` と `textlint-rule-preset-*` を追加で適用します。登録済みの textlint ルールやプリセットを解決できない場合は、標準エラーにパッケージ名を表示して終了コード 2 を返します。その他のパッケージは依存登録のみで、`check` に影響しません。

```sh
doc-lint check docs README.md
doc-lint check docs --fail-on warning --format json
doc-lint check docs --format sarif --output doc-lint.sarif
doc-lint check --fail-on never
```

パスを省略すると現在のディレクトリを点検します。対象は `.md`、`.mmd`、`.puml`、`.plantuml` です。`node_modules`、`.git`、`.doc-lint` は走査から除外します。

## 設定

対象パスから上位ディレクトリへ `.doc-lint/config.yaml` を探します。設定が無い場合は `doc-lint init` を案内します。複数の対象を指定する場合は、同じ設定を使うパスを指定してください。出力に記載するファイルパスと相対指定の `--output` は、設定を置いたリポジトリのルートが基準です。絶対パスも指定できます。シンボリックリンクを経由する場合も含め、リポジトリルートの外に出る `--output` は終了コード 2 で拒否します。

```yaml
version: 1
preset: ja
failOn: error
evaluators:
  textlint: true
  markdownlint: true
  mermaid: true
  plantuml: true
tools: []
```

有効な評価器は、対象リポジトリで解決できるパッケージを優先し、次に doc-lint の依存を使います。`--fail-on` は設定の `failOn` より優先します。

## 評価器と重要度

| 評価器 | 対象と重要度 |
| --- | --- |
| textlint | Markdown に日本語技術文書プリセットを適用。元の重要度が 2 ならエラー、それ以外は警告。ルール ID がない場合は `textlint/unknown` |
| markdownlint | Markdown の書式を点検。評価器に重要度の区別がないため、すべて警告 |
| Mermaid | `.mmd` と Markdown の `mermaid` フェンスを点検。構文違反はエラー |
| PlantUML | `.puml`、`.plantuml` と Markdown の `plantuml`、`puml` フェンスを点検。構文違反はエラー |

PlantUML の判定は同梱エンジンに従います。たとえば、条件分岐の閉じ忘れをエンジンが検出しない場合があります。

## 出力と終了コード

- `stylish`（既定）はファイル、行・列、重要度、ルール ID、メッセージと集計を表示します。
- `json` は形式バージョンの `version`、CLI の名前とバージョンを含む `tool`、`findings`、`summary` を返します。
- `sarif` は CI やコードスキャンに渡せる SARIF 2.1.0 を返します。

通常は結果を標準出力に書きます。`--output` を指定すると指定ファイルに書きます。評価器の診断は標準エラー出力に送ります。

| 終了コード | 意味 |
| --- | --- |
| 0 | 実行成功。失敗条件に該当する指摘なし |
| 1 | 実行成功。失敗条件に該当する指摘あり |
| 2 | 設定不備、依存不足、評価器の実行失敗など |

`--fail-on error`（既定）はエラーで 1、`warning` は警告またはエラーで 1 を返します。`never` は指摘があっても 0 を返します。実行失敗はどの設定でも 2 です。

## Phase 1 の範囲

Phase 1 は一つの文書内の文章、書式、図を検査します。AsciiDoc、文書間の横断評価、規約抽出、仕様照合、HTML 出力、`rules discover/apply` は未対応です。

文書から実装コードへの参照や、コード変更に対する文書の追随は codd-gate が扱います。文書中の設定値、コマンド、API と実装との照合は doc-drift-detector が扱います。計画上の doc-lint の参照評価は文書同士のリンク、アンカー、include が対象で、仕様照合は明示した設定やスキーマを使う範囲です。

## 開発

```sh
cd tools/doc-lint
npm install && npm test
node bin/doc-lint.js doctor
```

テストはインストール済みの依存を使用します。npm を呼ぶコマンドのテストにはスタブを使います。
