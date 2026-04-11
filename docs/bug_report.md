# gitlab-idd Bug Report

## 概要

- 調査日時: 2026-04-11T08:48:51
- 調査対象ファイル:
  - `gl_common.py`
  - `gl.py`
  - `gl_poll_daemon.py`
  - `gl_poll_setup.py`
- 発見件数サマリー:
  - バグ (Bug): 7件
  - 潜在的問題 (Potential Issue): 14件
  - セキュリティ上の懸念 (Security): 8件

---

## 発見事項

### バグ（Bug）

#### B-01: `gl_common.py` L75 — `title_to_slug` コメント不正確
- ファイル: `gl_common.py` (L75)
- 説明: `title_to_slug` で slug が空文字になる条件のコメントが「非 ASCII のみ」と不正確。実際は「ASCII 英数字が残らない場合（日本語のみ・記号のみ等）」が正しい。
- 再現条件: `title="---"` を渡す → slug="" → MD5 フォールバック発動
- 影響: 軽微。コメントの不正確さのみで実害なし。
- 修正案: コメントを「ASCII 英数字が残らない場合（日本語のみ・記号のみ等）」に修正する。

#### B-02: `gl.py` L155 — `api_list()` caller の `page` パラメータが上書きされる
- ファイル: `gl.py` (L155)
- 説明: `api_list()` はループ内で `page` を無条件に上書きするため、呼び出し元が `params={"page": 2}` を渡しても初回ページが無視される。
- 再現条件: `api_list(host, token, path, params={"page": 2, "per_page": 50})` を呼び出す
- 影響: 不正なページネーション。呼び出し元が開始ページを指定できない。
- 修正案: `params.setdefault("per_page", 100)` のように、呼び出し元の意図を尊重する設計に変更する。

#### B-03: `gl.py` L230 — `--body` 省略時と空文字列の区別不可
- ファイル: `gl.py` (L230)
- 説明: `--body` のデフォルトが `""` のため、省略と明示的な空文字列を区別できない。イシューが常に空文字列の description で作成される。
- 再現条件: `python gl.py create-issue --title "Test"` を実行する
- 影響: description が常に `""` で設定される。将来の拡張で「未指定」検出が不可能になる。
- 修正案: `p.add_argument("--body", default=None)` に変更し、`read_body` で `None` を返すよう修正する。

#### B-04: `gl_poll_daemon.py` L337 — `seen_issues` の型不一致（str vs int）
- ファイル: `gl_poll_daemon.py` (L337)
- 説明: `run_poll_cycle()` の `seen` 構築で `int()` 変換がなく、`config.json` に文字列キーが混入すると全イシューが毎サイクル新規扱いになる。
- 再現条件: `config.json` の `seen_issues` に文字列キー `"123"` が混入した状態でポーリングを実行する
- 影響: 全イシューが毎サイクル「新規」と判定され、エージェント CLI が重複起動し続ける。GitLab API レート制限に抵触する可能性あり。
- 修正案: `seen = set(int(i) for i in config.get("seen_issues", {}).get(key, []))` に統一する。

#### B-05: `gl_poll_daemon.py` L316-317 — ワーカー起動失敗時のサイレントドロップ
- ファイル: `gl_poll_daemon.py` (L316-317)
- 説明: `mark_seen()` と `save_config()` をワーカー起動前に呼ぶため、`launch_agent_worker()` が例外を送出した場合にイシューが永遠に再処理されなくなる。
- 再現条件: `launch_agent_worker()` 内で例外が発生する状況（CLI バイナリが消えた等）
- 影響: イシューが処理されないままサイレントに消える。ユーザーへの通知なし。
- 修正案: ワーカー起動成功を確認してから `mark_seen()`/`save_config()` を呼ぶ、または例外時に `seen` からロールバックする。

#### B-06: `gl_poll_setup.py` L103 — IPv6 SSH URL のパース失敗
- ファイル: `gl_poll_setup.py` (L103)
- 説明: `get_current_repo_info()` で `split(":", 1)` を使うため、IPv6 アドレス形式の SSH URL が正しくパースされない。
- 再現条件: `git remote set-url origin git@[::1]:user/repo.git` の状態で実行する
- 影響: IPv6 環境では `RepoConfig` が返されずリポジトリが登録されない。一般的なユースケースでは影響なし。
- 修正案: 正規表現でパースするか、`gitpython` 等のライブラリを使用する。

#### B-07: `gl_poll_setup.py` L248 — systemd サービスファイルに空行が混入
- ファイル: `gl_poll_setup.py` (L248)
- 説明: `env_lines` が空の場合、f-string 展開で空行が残り、systemd Unit ファイルとして不正なフォーマットになる可能性がある。
- 再現条件: `mock_cli=False` で Linux にインストールし、生成されたサービスファイルを確認する
- 影響: systemd が警告を出す可能性あり。厳格な環境ではサービス起動失敗の可能性。
- 修正案: `env_section = f"\n{env_lines.rstrip()}" if env_lines.strip() else ""`

---

### 潜在的問題（Potential Issue）

#### P-01: `gl_common.py` L57 — `retry_on_network_error` の初期 delay が 2s スタート
- ファイル: `gl_common.py` (L57)
- 説明: `attempt=0` 失敗後の待機が `backoff` 値（デフォルト 2.0s）から始まる。一般的な指数バックオフは 1s スタートが多く、ドキュメントに明記がない。
- 再現条件: `retries=3, backoff=2.0` で呼び出し、1回目から失敗させる
- 影響: 初回リトライが想定より長くなる可能性（軽微）
- 修正案: `delay = 1.0` をデフォルトにするか、ドキュメントに「初回待機は backoff 秒」と明記する。

#### P-02: `gl_common.py` L148 — `_verify_cli` の WSL 検出で最大 10 秒のブロッキング遅延
- ファイル: `gl_common.py` (L148)
- 説明: WSL がインストールされているが `kiro-cli` がない Windows 環境で、`timeout=10` のブロッキング遅延が発生しうる。
- 再現条件: WSL がインストールされているが `kiro-cli` がない Windows 環境
- 影響: CLI 検出時に最大 10 秒のブロッキング遅延
- 修正案: timeout を 3〜5 秒に短縮するか、非同期検出に切り替える。

#### P-03: `gl_common.py` L183 — `load_config` が権限エラーを握り潰す
- ファイル: `gl_common.py` (L183)
- 説明: `OSError` を握り潰してデフォルト設定を返すため、`chmod 000` 等で読めない場合に `seen_issues={}` が返り、全イシューに重複通知が発生しうる。
- 再現条件: `config.json` を `chmod 000` にした状態でデーモンを起動する
- 影響: `seen_issues` がリセットされた状態で動作し、全イシューに重複通知が発生しうる。
- 修正案: `errno.EACCES` は例外を再 raise するか、呼び出し元に伝播させる。

#### P-04: `gl.py` L155 — ヘッダー構築の重複（`api()` と `api_list()`）
- ファイル: `gl.py` (L155)
- 説明: 認証ヘッダーの構築ロジックが `api()` と `api_list()` で重複しており、将来の変更時に片方だけ更新されるリスクがある。
- 再現条件: `api()` のヘッダーロジックをリファクタリングした場合、`api_list()` が古いロジックを使い続ける
- 影響: 認証失敗または誤ったトークンの使用（メンテナンスリスク）
- 修正案: `_make_headers(token)` ヘルパーを抽出して両関数から呼び出す。

#### P-05: `gl.py` L96 — `path` にクエリ文字列が含まれる場合の二重 `?`
- ファイル: `gl.py` (L96)
- 説明: `api()` で URL を文字列結合で構築するため、`path` に既にクエリ文字列が含まれる場合に `?` が二重になる。
- 再現条件: `api(host, token, "GET", "/user?foo=bar", params={"x": 1})` を呼び出す
- 影響: HTTP 400 またはサーバー依存の誤動作
- 修正案: `urllib.parse.urlparse` + `urlencode` + `urlunparse` で安全にマージする。

#### P-06: `gl.py` L155 — `X-Next-Page` ヘッダーの非整数値で `ValueError`
- ファイル: `gl.py` (L155)
- 説明: `int(next_page)` に `ValueError` ガードがなく、不正なヘッダー値でハンドルされない例外が発生する。
- 再現条件: サーバーが `X-Next-Page: abc` を返す
- 影響: ハンドルされない例外。ユーザーへの不明瞭なエラーメッセージ。
- 修正案: `try/except ValueError` でラップし、明確なメッセージで `sys.exit()` する。

#### P-07: `gl.py` L96 — レスポンスボディの無制限メモリ読み込み
- ファイル: `gl.py` (L96)
- 説明: `resp.read()` でサイズ制限なく全ボディを読み込むため、大きなレスポンスで高メモリ使用になりうる。
- 再現条件: エンドポイントが数 MB の JSON を返す
- 影響: 高メモリ使用。制約環境での OOM の可能性。
- 修正案: `Content-Length` チェックを追加するか、`resp.read(MAX_BYTES)` でストリーミングする。

#### P-08: `gl.py` L295 — `--no-draft` 時のタイトルプレフィックス処理の意図不明瞭
- ファイル: `gl.py` (L295)
- 説明: MR タイトルに "Draft:" / "WIP:" プレフィックスがない場合のコードの意図が不明瞭で、将来のメンテナーを混乱させる可能性がある。
- 再現条件: `draft=True` だがタイトルが "Draft:" / "WIP:" で始まらない MR に `--no-draft` を適用する
- 影響: 低。機能的な動作は正しいが、コードの意図が不明瞭。
- 修正案: プレフィックスが見つからない場合はタイトルをそのまま使う旨のコメントを追加する。

#### P-09: `gl.py` L155 — `api_list()` のページ数に上限なし
- ファイル: `gl.py` (L155)
- 説明: 大量のイシュー/MR を持つプロジェクトで無制限にループし、メモリ枯渇やレート制限に抵触する可能性がある。
- 再現条件: 10,000 件以上のイシューを持つプロジェクトでイシュー一覧を取得する
- 影響: 長時間実行。高メモリ使用。ユーザーへのフィードバックなし。
- 修正案: `max_pages` パラメータを追加し、閾値超過時に警告または進捗表示を行う。

#### P-10: `gl_poll_daemon.py` L271-285 — プロンプトファイルの固定名による競合
- ファイル: `gl_poll_daemon.py` (L271-285)
- 説明: `worker-prompt-{issue_id}.md` という固定名で書き出すため、複数リポジトリで同じ `iid` が存在する場合に上書きが発生する。
- 再現条件: 2 つのリポジトリが同じ `iid=1` のイシューを持つ状態で同一サイクルにポーリングする
- 影響: ワーカーが誤ったプロンプト（別リポジトリのもの）を受け取る可能性。
- 修正案: ファイル名にリポジトリ識別子（host+project のハッシュ等）を含める。

#### P-11: `gl_poll_daemon.py` L271 — 起動中ワーカーの stdin ファイルが cleanup で削除される競合
- ファイル: `gl_poll_daemon.py` (L271)
- 説明: `_cleanup_old_worker_files()` を `launch_agent_worker()` 冒頭で毎回呼ぶため、起動中の子プロセスの stdin ファイルが削除される競合が発生しうる（特に Windows）。
- 再現条件: Windows 環境で複数イシューを同一サイクルで処理する
- 影響: Windows 環境でワーカーが stdin を読めず空プロンプトで起動する。
- 修正案: `_cleanup_old_worker_files()` をデーモン起動時の 1 回のみ呼び出す。

#### P-12: `gl_poll_daemon.py` L395-396 — CLI バイナリ消失後も古い `cli` を使い続ける
- ファイル: `gl_poll_daemon.py` (L395-396)
- 説明: `find_best_agent_cli()` が `None` を返した場合に古い `cli` オブジェクトを使い続け、毎サイクル起動失敗ログが出力され続ける。
- 再現条件: デーモン起動後に CLI バイナリを削除する
- 影響: 毎サイクル起動失敗ログが出力され続ける。イシューは処理されない。
- 修正案: `cli` が `None` の場合はそのサイクルをスキップして警告ログを出す。

#### P-13: `gl_poll_setup.py` L195 — crontab の部分一致による誤検出
- ファイル: `gl_poll_setup.py` (L195)
- 説明: `daemon_path in existing` でフルパスの存在を確認するが、別エントリのパスの一部として含まれる場合に誤って「登録済み」と判断する可能性がある。
- 再現条件: 別のエントリに `gl_poll_daemon.py` を含むパスが存在する場合
- 影響: crontab への登録がスキップされ、デーモンが起動しない。
- 修正案: 行単位の正規表現チェックに変更する。

#### P-14: `gl_poll_setup.py` L406 — モック CLI の空 binary フィールド
- ファイル: `gl_poll_setup.py` (L406)
- 説明: `AgentCLI("mock", "", [])` の空 binary が将来 `install_service()` 内で参照された場合にクラッシュする可能性がある。
- 再現条件: `--allow-mock-cli` でインストールし、`install_service` 内で `cli.binary` を参照する変更を加えた場合
- 影響: 現状は影響なし。将来的なリグレッションリスク。
- 修正案: `MOCK_CLI = AgentCLI("mock", "mock", [])` のような定数を定義する。

---

### セキュリティ上の懸念（Security）

#### S-01: `gl_common.py` L196 — tmp ファイルの rename 前 chmod 漏れ（トークン漏洩リスク）
- ファイル: `gl_common.py` (L196)
- 説明: `save_config()` で tmp ファイルを `rename` 後に `chmod 0o600` を適用するため、rename 直後から chmod 完了までの短い窓でトークンが world-readable になりうる。
- 再現条件: `umask=0o022` の環境で `save_config()` を呼び出し、rename 直後に別プロセスが `config.json` を stat する
- 影響: `RepoConfig.token`（GitLab アクセストークン）が一瞬 world-readable になる可能性。マルチユーザー環境では情報漏洩リスク。
- 修正案: tmp ファイルを `open` する前に `os.chmod(tmp, 0o600)` を適用する（rename 前に権限を設定）。

#### S-02: `gl.py` L44 — git remote URL の無条件信頼によるトークン漏洩リスク
- ファイル: `gl.py` (L44)
- 説明: `get_project_info()` が `.git/config` の remote URL を無条件に API ホストとして使用するため、悪意ある `.git/config` によりトークンが任意のホストに送信される可能性がある。
- 再現条件: `.git/config` の origin を `https://evil.example.com/ns/repo.git` に設定して任意の `gl.py` コマンドを実行する
- 影響: トークン漏洩。CI 環境では高リスク。
- 修正案: 抽出したホストを設定済みの `GITLAB_HOST` 環境変数と照合するか、既知の GitLab インスタンスと異なる場合に警告する。

#### S-03: `gl_poll_daemon.py` L44-55 — テンプレートファイルへの書き込みによるプロンプトインジェクション
- ファイル: `gl_poll_daemon.py` (L44-55)
- 説明: `templates/` ディレクトリへの書き込み権限があれば、`worker-prompt.md` に任意のプレースホルダーを埋め込んでエージェント CLI に任意の指示を注入できる。
- 再現条件: 攻撃者が `templates/` ディレクトリに書き込み権限を持つ場合
- 影響: テンプレートが信頼できないソースから読み込まれる場合、エージェント CLI に任意の指示を注入できる。
- 修正案: テンプレートファイルのパーミッションを `0o600` に制限する。テンプレートディレクトリの整合性チェック（ハッシュ検証等）を検討する。

#### S-04: `gl_poll_daemon.py` L196-200 — tmp ファイルの rename 前 chmod 漏れ（再掲）
- ファイル: `gl_common.py` (L196-200)
- 説明: S-01 と同一の問題。`gl_poll_daemon.py` からも `save_config()` が呼ばれるため影響範囲に含まれる。
- 再現条件: S-01 と同様
- 影響: S-01 と同様
- 修正案: S-01 と同様。`tmp.touch(mode=0o600)` を `open` 前に呼ぶ。

#### S-05: `gl_poll_daemon.py` L75-76 — トークンの平文保存（OS キーチェーン統合なし）
- ファイル: `gl_common.py` (L75-76)
- 説明: `config.json` にトークンを平文保存しており、ファイルパーミッション（`0o600`）のみが保護手段。macOS Keychain や Linux Secret Service への統合は提供されていない。
- 再現条件: `config.json` を読み取れる別ユーザーまたはプロセスが存在する場合
- 影響: GitLab トークンの漏洩リスク。
- 修正案: OS キーチェーン統合（`keyring` ライブラリ等）を検討する。短期的には環境変数経由のトークン提供を推奨し、`config.json` へのトークン保存を非推奨とする。

#### S-06: `gl_poll_setup.py` L68 — 設定ディレクトリのパーミッション未設定
- ファイル: `gl_poll_setup.py` (L68)
- 説明: `get_config_dir()` 作成時にディレクトリのパーミッションが設定されておらず、`config.json` の `0o600` 設定が無意味になる可能性がある（ディレクトリ自体が world-readable の場合）。
- 再現条件: インストール後に `ls -la ~/.config/gitlab-idd/` を確認する
- 影響: 設定ディレクトリが他ユーザーから参照可能な場合、`config.json` のパーミッション設定が無意味になる。
- 修正案: `get_config_dir()` 作成後に `os.chmod(config_dir, 0o700)` を明示的に呼ぶ。

#### S-07: `gl_poll_setup.py` L155 — plist への XML 特殊文字エスケープ不足
- ファイル: `gl_poll_setup.py` (L155)
- 説明: `install_service_macos()` で `python_exe` と `daemon_path` を plist の `<string>` タグに直接埋め込むため、XML 特殊文字（`&`, `<`, `>` 等）を含むパスで不正な plist が生成される。
- 再現条件: ユーザー名やパスに `&` や `<` を含む環境（稀だが存在する）
- 影響: `launchctl` が plist の読み込みに失敗し、デーモンが起動しない。
- 修正案: `xml.sax.saxutils.escape()` でエスケープする。

#### S-08: `gl_poll_setup.py` L340 — `settings.json` tmp ファイルのパーミッション未設定
- ファイル: `gl_poll_setup.py` (L340)
- 説明: `configure_session_hook()` で `settings.json` を tmp ファイル経由でアトミックに書き込む際、tmp ファイルのパーミッションがデフォルト（umask 依存）のまま。
- 再現条件: マルチユーザー環境でインストールを実行中に別ユーザーが tmp ファイルを読む
- 影響: 低リスク。フックコマンドの改ざんによるコード実行リスクがある。
- 修正案: tmp ファイル書き込み後に `os.chmod(tmp, 0o600)` を設定してから `tmp.replace(settings_path)` する。

---

## 総評

### 全体的な品質評価

gitlab-idd は全体的に堅牢な設計で実装されており、stdlib のみの依存、atomic write パターン、リトライ機構、OS 別サービス管理など良い設計が随所に見られる。重大なデータ破壊やクラッシュバグは通常の使用パスでは発見されなかった。

### 優先修正事項

**最優先（実害が出やすい）:**

1. **S-01 / S-04** (`gl_common.py` L196): tmp ファイルの rename 前 chmod 漏れ。GitLab トークンが一時的に world-readable になる。1行追加で修正可能。
2. **B-04** (`gl_poll_daemon.py` L337): `seen_issues` の型不一致。全イシューが毎サイクル新規扱いになり、重複起動とレート制限抵触を引き起こす。
3. **S-02** (`gl.py` L44): git remote URL の無条件信頼。CI 環境でのトークン漏洩リスクが高い。

**高優先（ユーザー体験への影響）:**

4. **B-05** (`gl_poll_daemon.py` L316-317): ワーカー起動失敗時のサイレントドロップ。イシューが通知なく消える。
5. **P-03** (`gl_common.py` L183): `load_config` の権限エラー握り潰し。重複通知の原因になりうる。
6. **B-07** (`gl_poll_setup.py` L248): systemd サービスファイルの空行。厳格な環境でサービス起動失敗の可能性。

**中優先（将来のメンテナンスリスク）:**

7. **P-04** (`gl.py` L155): ヘッダー構築の重複。将来の変更時に片方だけ更新されるリスク。
8. **S-06** (`gl_poll_setup.py` L68): 設定ディレクトリのパーミッション未設定。
9. **P-10 / P-11** (`gl_poll_daemon.py` L271): プロンプトファイルの競合。マルチリポジトリ環境で誤動作の可能性。
