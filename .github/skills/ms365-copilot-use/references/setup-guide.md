# セットアップガイド

Playwright CLI と永続プロファイルで Microsoft 365 Copilot Chat を呼び出すための初期設定手順。

## 1. Playwright のインストール

```bash
pip install playwright
playwright install chromium
```

- `playwright` は pip でインストールされる Python パッケージ兼 CLI ツール。
- `playwright install chromium` で必要なブラウザバイナリを取得する。社内プロキシ越しの場合は `HTTPS_PROXY` 環境変数を設定する。
- 社内ポリシーで Edge しか許可されていない場合は `playwright install msedge` を追加し、スクリプトに `--channel msedge` を渡す。

## 2. 永続プロファイルの作成

スクリプトは既定で `~/.ms365_copilot_profile` を `user-data-dir` として使う。存在しなければ自動作成される。

別アカウント・別環境を切り替えたいときは `--user-data-dir /path/to/other-profile` を指定する。

## 3. 初回サインイン確認

```bash
python scripts/ask_copilot.py --login
```

- headed Chromium が立ち上がり、`https://m365.cloud.microsoft/chat` に遷移する。
- 社内 SSO（Entra ID Seamless SSO / Windows Hello / WAM）が効いていれば、自動的に Copilot Chat が表示される。
- 自動サインインが効かない場合はブラウザ画面で職場アカウントを選び、MFA を済ませる。
- 入力欄が表示されたところでスクリプトが自動終了し、セッションが `user-data-dir` に保存される。

## 4. 動作確認

```bash
python scripts/ask_copilot.py --prompt "こんにちは"
```

Markdown 形式の回答が標準出力に表示されれば成功。

## 5. CI / バッチ実行

- 永続プロファイルをジョブ間で共有したい場合は `--user-data-dir` を共有ボリュームに置く。
- 同じプロファイルを同時に複数プロセスで開かない（Chromium がロックして失敗する）。
- ヘッドレス前提で動かす場合、まず手動で 1 回 `--login` を流してセッションを作る必要がある。

## 6. プロキシ設定

社内プロキシ越しに動かすときは Playwright プロセスに環境変数を渡す:

```bash
HTTPS_PROXY=http://proxy.corp.example:8080 \
HTTP_PROXY=http://proxy.corp.example:8080 \
NO_PROXY=.corp.example,localhost,127.0.0.1 \
python scripts/ask_copilot.py --prompt "..."
```

ブラウザ自身のプロキシ設定が必要な場合は `playwright` の `proxy` 引数を `ask_copilot.py` の `launch_persistent_context` に追加する。

## 7. アンインストール / リセット

```bash
# セッションを破棄して再ログインしたい場合
rm -rf ~/.ms365_copilot_profile

# Chromium バイナリを削除
playwright uninstall chromium
```
