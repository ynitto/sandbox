### clipit: Electron 版 ClipIt（ハイブリッド構成）と devkit のプログラム化 API を追加

- tools/devkit に `index.js` を追加し、`listCommands()` / `runCommand()` / `run()` でコマンドをプログラムから実行できるようにした（出力と終了コードを値で受け取る。CLI の挙動は不変）。`csv` の同期出力も API で取得できるよう io に `outSync` を追加した。
- tools/clipit-app に Electron 版 ClipIt を新規追加した。UI と devkit プラグインは Node（Electron）、Windows ネイティブ機能（前面ウィンドウ取得・貼り戻し・WinRT OCR）だけを Python ヘルパー（helper/clipit_helper.py、JSON 入出力）に切り出したハイブリッド構成。
- 履歴（テキスト/画像、最大200件、タグ・検索・並び替え・ピン留め）/ ペイント / メモ / 電卓 / ツール（devkit コマンドを自動発見して実行し、結果をクリップに保存できる）の5タブを持ち、Ctrl+Shift+V とトレイで開閉する。
- Windows 向けビルド設定（electron-builder、NSIS / portable）とヘルパーの exe 化 bat を同梱。Windows 実機での GUI・ホットキー・貼り戻し・OCR・exe 化は未検証。
