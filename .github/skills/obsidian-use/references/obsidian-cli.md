## 目次

- [構文](#構文)
- [ファイルのターゲット指定](#ファイルのターゲット指定)
- [ボルトのターゲット指定](#ボルトのターゲット指定)
- [よく使うコマンド](#よく使うコマンド)
- [プラグイン開発](#プラグイン開発)

# Obsidian CLI

`obsidian` CLIでObsidianの実行中インスタンスを操作する。Obsidianが開いている必要がある。

## 構文

**パラメーター**は `=` で値を指定。スペースを含む値はクォートで囲む:

```bash
obsidian create name="マイノート" content="Hello world"
```

**フラグ**は値なしのブール型スイッチ:

```bash
obsidian create name="マイノート" silent overwrite
```

複数行コンテンツには `\n`（改行）と `\t`（タブ）を使用する。

## ファイルのターゲット指定

多くのコマンドでは `file` または `path` でファイルを指定する。どちらも指定しない場合はアクティブなファイルが対象になる。

- `file=<名前>` — ウィキリンクと同様に解決（パスや拡張子不要）
- `path=<パス>` — ボルトルートからの正確なパス（例: `folder/note.md`）

## ボルトのターゲット指定

デフォルトでは最後にフォーカスしたボルトを対象にする。特定のボルトを対象にするには最初のパラメーターに `vault=<名前>` を指定:

```bash
obsidian vault="マイボルト" search query="検索語"
```

## よく使うコマンド

```bash
obsidian read file="マイノート"
obsidian create name="新規ノート" content="# Hello" template="テンプレート" silent
obsidian append file="マイノート" content="新しい行"
obsidian search query="検索語" limit=10
obsidian daily:read
obsidian daily:append content="- [ ] 新しいタスク"
obsidian property:set name="status" value="done" file="マイノート"
obsidian tasks daily todo
obsidian tags sort=count counts
obsidian backlinks file="マイノート"
```

- `--copy`: 任意のコマンドに追加するとクリップボードにコピー
- `silent`: ファイルを開かずに操作
- `total`: リストコマンドで件数を取得

## プラグイン開発

### 開発・テストサイクル

コードを変更したら次の手順でテストする:

1. **リロード**: プラグインの変更を反映
   ```bash
   obsidian plugin:reload id=my-plugin
   ```
2. **エラー確認**: エラーがあれば修正してステップ1に戻る
   ```bash
   obsidian dev:errors
   ```
3. **ビジュアル確認**: スクリーンショットまたはDOM検査
   ```bash
   obsidian dev:screenshot path=screenshot.png
   obsidian dev:dom selector=".workspace-leaf" text
   ```
4. **コンソール確認**: 警告や予期しないログを確認
   ```bash
   obsidian dev:console level=error
   ```

### 追加の開発コマンド

```bash
# アプリコンテキストでJavaScriptを実行
obsidian eval code="app.vault.getFiles().length"

# CSS値を検査
obsidian dev:css selector=".workspace-leaf" prop=background-color

# モバイルエミュレーションの切り替え
obsidian dev:mobile on
```

全コマンドの最新情報は `obsidian help` で確認する。完全ドキュメント: https://help.obsidian.md/cli
