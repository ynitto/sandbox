### devkit: 小物 CLI 10本を統合したツールキットを追加

- tools/jwt-inspect、ip-calc、env-diff、cron-next、csv-peek、timestamp-converter、semver-tool、markdown-toc、url-inspect、git-stale-branches の10本を、Node.js 製の単一パッケージ tools/devkit に統合した（サブコマンド jwt / ip / env / cron / csv / ts / semver / toc / url / branches）。
- `npm link` で `devkit` コマンドとしてローカル登録できる。外部依存はなく、Node 標準ライブラリのみで動作する。
- コマンドは commands/ ディレクトリからの自動発見で登録され、雛形ファイルから1ファイル追加するだけで新しいコマンドを増やせる。
- 各コマンドのオプション・出力・終了コードは元の Python 実装の仕様を再現し、テストも node:test に移植した（全188件、うち Python 固有の1件のみスキップ）。
