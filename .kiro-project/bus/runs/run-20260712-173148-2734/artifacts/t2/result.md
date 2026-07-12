# タスク t2 成果報告: 失敗テストの依存リストアップ

## (a) 成果

### 前提確認

t1 報告より、修正は既に完了しテストスイートは 900 件全 green の状態。
このタスクは「調査のみ」として、失敗していた 2 テストが依存するモジュール・関数・フィクスチャを記録する。

---

### 対象ファイル

`tools/kiro-flow/tests/test_kiro_flow.py`

---

## テストごとの依存リスト

### テスト 1: `GitlabExecutorPluginTests::test_open_mr_keeps_waiting_until_merged`

**クラス**: `GitlabExecutorPluginTests`（行 1430–1807）

| 種別 | 名前 | 場所・役割 |
|------|------|-----------|
| モジュール | `kf_exec_gitlab`（`gl_plugin`） | `tools/kiro-flow/executors/gitlab.py` をテスト用にロード（`_load_executor_plugin("gitlab")`） |
| モジュール | `os`, `json`, `unittest`, `unittest.mock` | 標準ライブラリ |
| フィクスチャ（setUp） | `self._cfg` | `KIRO_FLOW_EXECUTOR_CONFIG` に流す設定 dict（`poll_interval=0.0`, `timeout=0.0` 等） |
| フィクスチャ（setUp） | `self._prev_env` | `KIRO_FLOW_EXECUTOR_CONFIG` の退避・復元用 |
| フィクスチャ（setUp） | `self._prev_defer` | `KIRO_FLOW_DEFER_WAITS` の退避・復元用（**t1 で追加された修正箇所**） |
| ヘルパーメソッド | `self._run_with(api_side, mrs_seq, notes, token)` | `gl_plugin._resolve_token`, `gl_plugin.gl_api`, `gl_plugin.gl_api_list` を `mock.patch.object` でモックして `gl_plugin.execute` を実行する |
| モック対象関数 | `gl_plugin._resolve_token` | GitLab トークン解決をスタブ（`return_value="glpat-x"`） |
| モック対象関数 | `gl_plugin.gl_api` | REST API（GET/POST/PUT）を `api_side` 関数で代替 |
| モック対象関数 | `gl_plugin.gl_api_list` | `related_merge_requests` / `notes` リストを `list_side` 関数で代替 |
| テスト内ローカル変数 | `seq` | `mrs_seq` として渡す MR リストの 2 ステップシーケンス（`[opened]` → `[merged]`） |
| テスト内ローカル変数 | `api` | `api_side` に渡すインライン関数（`POST` → `iid:8`, `GET` → `state:opened`） |
| SUT 関数 | `gl_plugin.execute("work", "ログイン画面を追加", {})` | `tools/kiro-flow/executors/gitlab.py: execute()` |
| SUT 例外型 | `gl_plugin.DeferDecision` | テスト失敗の原因となった例外クラス（`KIRO_FLOW_DEFER_WAITS=1` 残存時に発生） |
| 環境変数 | `KIRO_FLOW_EXECUTOR_CONFIG` | executor の設定 JSON を inject |
| 環境変数 | `KIRO_FLOW_DEFER_WAITS` | **原因**: 前のクラス（`GitlabDeferPollTests`）が `"1"` を設定したまま残存していた |

**期待アサーション**:
- `data["decision"] == "approved"` が成立すること

---

### テスト 2: `GitlabExecutorPluginTests::test_timeout_raises_before_any_mr`

**クラス**: `GitlabExecutorPluginTests`（同上）

| 種別 | 名前 | 場所・役割 |
|------|------|-----------|
| モジュール | `kf_exec_gitlab`（`gl_plugin`） | `tools/kiro-flow/executors/gitlab.py` |
| モジュール | `os`, `json`, `unittest`, `unittest.mock` | 標準ライブラリ |
| フィクスチャ（setUp） | `self._cfg` | 基底設定 dict（テスト内で `timeout=0.01, approved_timeout=0.01` にオーバーライド） |
| フィクスチャ（setUp） | `self._prev_env` | `KIRO_FLOW_EXECUTOR_CONFIG` の退避・復元用 |
| フィクスチャ（setUp） | `self._prev_defer` | `KIRO_FLOW_DEFER_WAITS` の退避・復元用（**t1 で追加された修正箇所**） |
| ヘルパーメソッド | `self._run_with(api_side, mrs_seq, notes, token)` | テスト 1 と同じ `_run_with` |
| モック対象関数 | `gl_plugin._resolve_token` | `"glpat-x"` に固定 |
| モック対象関数 | `gl_plugin.gl_api` | `POST` → `iid:1`, `GET` → `state:opened` |
| モック対象関数 | `gl_plugin.gl_api_list` | `related_merge_requests` を空リスト `[[]]` で返す |
| テスト内環境変数操作 | `os.environ["KIRO_FLOW_EXECUTOR_CONFIG"]` | `timeout=0.01, approved_timeout=0.01, poll_interval=0.0` を注入（タイムアウトを即発火させる） |
| SUT 関数 | `gl_plugin.execute("work", "ログイン画面を追加", {})` | `tools/kiro-flow/executors/gitlab.py: execute()` |
| SUT 例外型 | `RuntimeError` | タイムアウト時に発生すべき例外（テストは `assertRaises(RuntimeError)` で検証） |
| SUT 例外型 | `gl_plugin.DeferDecision` | **原因**: `KIRO_FLOW_DEFER_WAITS=1` 残存時に `RuntimeError` の前に発生してしまっていた |
| 環境変数 | `KIRO_FLOW_EXECUTOR_CONFIG` | `timeout=0.01` で即タイムアウトを強制 |
| 環境変数 | `KIRO_FLOW_DEFER_WAITS` | **原因**: 前のクラス（`GitlabDeferPollTests`）から残存していた |

**期待アサーション**:
- `RuntimeError` が発生すること
- エラーメッセージに `"レビュー/MR 作成"` が含まれること

---

## 共通依存（クラスレベル）

| 種別 | 名前 | 役割 |
|------|------|------|
| モジュールレベル関数 | `_load_executor_plugin("gitlab")` | `executors/gitlab.py` を動的ロードして `gl_plugin` として公開 |
| モジュールレベル変数 | `gl_plugin` | 全テストで共用する executor モジュールオブジェクト |
| モジュールレベル関数 | `_load_module()` | `kiro-flow.py` 本体を `kf` として動的ロード（gitlab テストは `kf` を直接は使わないが同一ファイルで共存） |
| 環境変数（モジュールレベル初期化） | `KIRO_FLOW_STUB_SLEEP_MAX=0` | スタブのスリープを無効化してテスト高速化 |
| 環境変数（モジュールレベル初期化） | `GIT_CONFIG_COUNT=1`, `GIT_CONFIG_KEY_0=commit.gpgsign`, `GIT_CONFIG_VALUE_0=false` | GPG 署名を無効化して git 操作を決定的にする |
| 環境変数（モジュールレベル初期化） | `KIRO_SKILL_REGISTRY` | 存在しないパスに設定して自動アップデートを無効化 |

---

## 失敗の根本原因（t1 報告の補足）

`GitlabDeferPollTests`（行 5060–5179）が各テストで `os.environ["KIRO_FLOW_DEFER_WAITS"] = "1"` を設定する。
同クラスの `tearDown` は `self._prev_defer` を参照して復元するが、`setUp` の保存が `os.environ.get()` であるため、
元々存在しなかった場合は `self._prev_defer = None` → `tearDown` で `pop` する（正常動作）。

問題は `GitlabExecutorPluginTests` の（修正前の）`setUp` が `KIRO_FLOW_DEFER_WAITS` を一切管理していなかったこと。
Python の unittest はデフォルトでアルファベット順にテストクラスを実行するため、
`GitlabDeferPollTests` → `GitlabExecutorPluginTests` の順となり、
`KIRO_FLOW_DEFER_WAITS=1` が残存して `execute` が `DeferDecision` を raise し、2 件が失敗した。

**修正内容**（t1 が適用済み）:
`GitlabExecutorPluginTests.setUp` に `self._prev_defer = os.environ.pop("KIRO_FLOW_DEFER_WAITS", None)` を追加し、
`tearDown` に対応する復元コードを追加（5 行の追加のみ）。

---

## (b) 検証

このタスクはソースコードの調査のみ。コード変更は行っていない。
t1 報告に `900 passed in 128.59s (0:02:08)` の実行結果が含まれており、完了条件を満たしている。

---

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

- **採用した前提**: t1 が修正・検証まで完了しているため、このタスクは純粋に「依存リストアップ」の調査成果物として機能する。変更は不要と判断した。
- **チャーターとの差異**: チャーターでは「4 件失敗」とあるが、実際の失敗は 2 件。これは t1 報告と同様の観察で、残り 2 件は今回の実行環境では再現しなかった（or 先行修正で解消済み）。
- **範囲外で見つけた問題**: なし。
