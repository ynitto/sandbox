# ClipIt（Electron 版）

クリップボード履歴、ペイント、メモ、電卓、翻訳、OCR、devkit コマンドをまとめたデスクトップアプリです。パネルはトレイまたは `Ctrl+Shift+V` で開閉できます。履歴をダブルクリックするか、履歴一覧で項目を選んで Enter を押すと貼り戻します。貼り付け先を取得できない環境ではクリップボードへのコピーになります。

```text
Electron renderer（履歴・ペイント・メモ・電卓・ツール）
        │ contextBridge / IPC
Electron main（トレイ・ホットキー・クリップボード監視・保存）
        ├─ devkit API（JavaScript モジュール）
        └─ Python ヘルパー（子プロセス、標準入出力で JSON）
                 └─ Windows の前面ウィンドウ取得・貼り戻し・OCR
```

Electron が UI とデータ保存を担当します。ツールタブでは devkit の `listCommands()` と `runCommand()` を使います。Windows のウィンドウ操作と OCR は Python ヘルパーが担当します。翻訳にはネットワーク接続が必要です。

## 開発

リポジトリのルートから実行します。

```sh
cd tools/clipit-app
npm install
npm start
npm test
```

データは Electron の `app.getPath("userData")/data` に保存されます。履歴とメモは `clips.json`、画像は `images/` に入ります。従来の Python 版は `tools/clipit` に残り、Windows 上の保存先も従来どおり `%LOCALAPPDATA%\ClipIt` です。両版はデータを個別に管理します。

### ヘルパーの探索順

1. `CLIPIT_HELPER_PATH` が設定されている場合はそのパスを使用します。`.py` なら Python で起動し、それ以外は実行ファイルとして起動します。
2. パッケージ版では `resources/helper/clipit-helper.exe` を使用します。
3. 開発時はアプリディレクトリ内の `helper/clipit_helper.py` を Python で直接起動します。

Python コマンドは `CLIPIT_HELPER_PYTHON` で指定できます。既定は Windows では `python`、その他では `python3` です。貼り付け先の追跡は Windows で行います。OCR を使う場合は Windows の日本語または英語の OCR 対応言語パックと、Python の `helper/requirements-ocr.txt` に記載された依存関係が必要です。

### devkit の探索順

1. `CLIPIT_DEVKIT_PATH` が指すディレクトリ
2. パッケージ版の `resources/devkit`
3. アプリディレクトリからの相対パス `../../devkit`、`../devkit` の順。リポジトリでは後者の `tools/devkit` が該当します。

最初に `index.js` が見つかったディレクトリを使用します。

## Windows 向けビルド

`tools/clipit-app` をカレントディレクトリにして、Windows のコマンドプロンプトで実行します。OCR を同梱する場合は、先にビルドに使う Python へ OCR の依存関係をインストールします。

```bat
npm install
python -m pip install -r helper\requirements-ocr.txt
helper\build_helper.bat
copy /Y helper\dist\clipit-helper.exe helper\clipit-helper.exe
npm run dist
```

`build_helper.bat` は PyInstaller で `helper/dist/clipit-helper.exe` を作成します。Electron のビルドは `helper/clipit-helper.exe` を同梱するため、先に上記のコピーを実行してください。`npm run dist` は `dist/` に NSIS インストーラーとポータブル版を生成します。NSIS インストーラーはウィザード形式で、ユーザー単位のインストールが既定です。

## 未検証事項

この開発環境では Windows 実機での GUI、グローバルホットキー、貼り戻し、OCR、ヘルパーと Electron アプリの exe 化は未検証です。
