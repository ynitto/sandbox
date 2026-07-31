# kiro-loop oneshot オプション 設計書

> 作成日: 2026-05-11  
> 対象ブランチ: `claude/kiro-loop-oneshot-design-LzV6d`  
> 関連ファイル: `kiro-loop.py`, `kiro-loop.yaml.example`, `DESIGN.md`

---

## 1. 概要

本設計書は kiro-loop に対して以下の機能追加・動作変更を行うための設計をまとめる。

### 追加機能

1. **`oneshot` オプション** — プロンプトエントリごとに指定する boolean フラグ（デフォルト `false`）
2. **ウォームアップ起動** — `oneshot: true` のエントリでは、スケジュール時刻の `warm_up_minutes` 分前に専用の tmux/kiro セッションを起動してアタッチする
3. **実行後の自動セッション終了** — プロンプト送信後、kiro の処理完了を検知したらセッションを終了してデタッチする
4. **オーバーラップ時の待機継続** — 前回のセッションが未完了のままスケジュール時刻が到来した場合、セッションを維持したまま完了を待ってプロンプトを送信する

### 動作変更

5. **全モード共通の処理完了待機** — あらゆるモード（`oneshot` の有無を問わず）で、kiro の処理が完了するまで次のプロンプトを送信せず待機する（現行の「スキップしてインターバル後に再試行」から変更）

---

## 2. 要件詳細

| # | 要件 | 現行動作 | 変更後動作 |
|---|---|---|---|
| R1 | `oneshot` フラグをプロンプトエントリに追加 | なし | `oneshot: true/false`（デフォルト `false`） |
| R2 | `oneshot: true` 時のウォームアップ | なし | `next_run_at - warm_up_minutes * 60` でセッション起動・アタッチ |
| R3 | `oneshot: true` 時の完了後セッション終了 | セッション維持 | kiro 処理完了後に kiro セッション・tmux セッションを終了してデタッチ |
| R4 | オーバーラップ時の動作 | スキップまたはキュー | 完了を待機してプロンプトを送信。セッションは終了しない |
| R5 | 全モードの完了待機 | 上限到達時スキップ | kiro 処理完了まで `next_run_at` を更新せず毎秒リトライ |
| R6 | 待機関連パラメータの設定化 | 一部ハードコード | すべて `~/kiro-loop.yml`（グローバル設定）で変更可能 |

---

## 3. 新規設定オプション

### 3.1 グローバル設定 (`~/.kiro/kiro-loop.yml`)

```yaml
# === 既存パラメータ（参考） ===
startup_timeout: 60          # kiro-cli 起動待ちのタイムアウト（秒）
response_timeout: 300        # プロンプト応答待ちのタイムアウト（秒）
slot_timeout_seconds: 7200   # スロット強制解放までの最大待機時間（秒）
cooldown_seconds: 30         # スロット解放後のクールダウン（秒）

# === 新規パラメータ ===
warm_up_minutes: 5           # oneshot セッションを事前起動するデフォルト時間（分）
                             # プロンプトエントリの warm_up_minutes で上書き可能
completion_poll_interval: 2  # 処理完了をポーリングする間隔（秒、デフォルト 2）
completion_wait_timeout: 7200 # 処理完了待機の上限時間（秒）
                              # 超過した場合は強制続行。0 = 無制限（非推奨）
```

### 3.2 プロンプトエントリ (`.kiro/kiro-loop.yml`)

```yaml
prompts:
  - name: "Daily Standup"
    prompt: "本日のタスクをレビューしてください"
    cron: "0 9 * * 1-5"

    # === 新規フィールド ===
    oneshot: true              # このエントリを oneshot モードで実行（デフォルト: false）
    warm_up_minutes: 10        # このエントリ専用のウォームアップ時間（分）
                               # 未指定時はグローバル設定の warm_up_minutes を使用
```

### 3.3 設定の優先順位（`warm_up_minutes`）

```
プロンプトエントリの warm_up_minutes
  > グローバル設定の warm_up_minutes
  > デフォルト値 5（分）
```

---

## 4. oneshot モードの設計

### 4.1 状態遷移

oneshot エントリは以下の状態機械で管理する。`PeriodicScheduler` がエントリごとに `oneshot_state` を保持する。

```
┌─────────────────────────────────────────────────────────────────────┐
│                           oneshot_state                             │
└─────────────────────────────────────────────────────────────────────┘

  IDLE
    │
    │ now >= warm_up_at
    ▼
  WARMING_UP ──── startup_timeout 超過 ────► IDLE（次の warm_up_at を再計算）
    │
    │ kiro-cli 起動確認 (プロンプト表示)
    ▼
  READY
    │
    │ now >= next_run_at
    ▼
  SENDING
    │
    │ プロンプト送信完了
    ▼
  PROCESSING ──── completion_wait_timeout 超過 ──► COMPLETING（強制終了）
    │
    │ kiro 処理完了検知（SlotMonitor / agent hook）
    ▼
  COMPLETING
    │
    │ セッション終了・デタッチ完了
    ▼
  IDLE（next_run_at, warm_up_at を次回スケジュールへ更新）


  ── オーバーラップ例外パス ──────────────────────────────────────────

  PROCESSING（前回実行中）
    │
    │ now >= next_run_at（次回スケジュール到来）
    ▼
  OVERLAP_WAIT ── completion_wait_timeout 超過 ──► SENDING（強制続行）
    │
    │ kiro 処理完了検知
    ▼
  SENDING（同じセッション内で次のプロンプトを送信）
    │
    │ プロンプト送信完了
    ▼
  PROCESSING（次回実行として継続）
    │
    │ kiro 処理完了検知
    ▼
  COMPLETING（セッション終了へ）
```

> **注意**: `OVERLAP_WAIT` → `SENDING` 後はセッションを終了しない（次回が完了するまで継続）。  
> 3 回以上オーバーラップが発生した場合も同様に最新の 1 件分だけキューに保持し、それ以前のスケジュールは破棄する。

### 4.2 ウォームアップスケジューラ

`PeriodicScheduler` のポーリングループ（1 秒ごと）内で、既存の `next_run_at` チェックに加えて `warm_up_at` チェックを追加する。

```
各エントリについて（oneshot: true のもののみ）:
  now >= warm_up_at かつ oneshot_state == IDLE?
    → _create_oneshot_session(entry) を呼び出す
    → oneshot_state = WARMING_UP
    → warm_up_at は変更しない（次のサイクルで再チェックされないよう state で制御）
```

**`warm_up_at` の計算**:

```python
warm_up_at = next_run_at - warm_up_minutes * 60
```

- `cron` 式の場合: `CronExpression.next_run()` で `next_run_at` を求め、そこから引く
- `interval_minutes` の場合: `next_run_at - warm_up_minutes * 60`
- `warm_up_at` が `now` より過去になる場合（`warm_up_minutes > interval_minutes` など）: 即座にセッション起動

### 4.3 セッションライフサイクル管理

`SessionManager` に oneshot セッション専用の操作を追加する。

#### セッション作成 (`_create_oneshot_session`)

1. `_tmux_session_name(prompt_id, cwd)` でセッション名を生成（既存ロジックを流用）
2. `tmux new-session -d -s <name> -c <cwd>` で独立したデタッチドセッションを作成
3. kiro-cli を起動（既存の `_create_worker_pane` に相当する処理を新セッション内で実行）
4. `tmux attach-session -t <name>` で現在のターミナルにアタッチ
   - kiro-loop デーモン自体は tmux 外で動いているため、アタッチ可能なクライアントが存在する場合のみ実行
   - `TMUX` 環境変数の有無で判定し、`switch-client` または `attach-session` を使い分ける

#### セッション終了 (`_terminate_oneshot_session`)

kiro 処理完了後に呼び出す。

1. `tmux send-keys -t <pane> '/exit' Enter` — kiro-cli へ終了コマンドを送信
2. `startup_timeout` 秒（デフォルト 60 秒）待機して kiro-cli プロセスが終了するのを確認
   - 終了しない場合は `tmux kill-pane -t <pane>` で強制終了
3. `tmux detach-client -s <session>` — セッションにアタッチしているクライアントをデタッチ
4. `tmux kill-session -t <session>` — セッション自体を削除
5. `SessionManager` の `_panes`, `_tmux_names` などから当該エントリを除去

### 4.4 オーバーラップ検知

`PeriodicScheduler._run_loop` にて、oneshot エントリの `next_run_at` が到来したとき:

```python
if entry['oneshot']:
    if oneshot_state == PROCESSING:
        # オーバーラップ: 前回が未完了
        oneshot_state = OVERLAP_WAIT
        pending_prompt = entry['prompt']  # 次回プロンプトをキープ
        # next_run_at は更新しない（完了後に更新）
    elif oneshot_state == OVERLAP_WAIT:
        # 2回以上のオーバーラップ: 最新のプロンプトで上書き（古いものは破棄）
        pending_prompt = entry['prompt']
```

`SlotMonitor` または agent hook から処理完了を受け取ったとき:

```python
if oneshot_state == OVERLAP_WAIT:
    # 前回完了 → 続けて次のプロンプトを送信（セッションは維持）
    send_prompt(pane_id, pending_prompt)
    oneshot_state = PROCESSING
elif oneshot_state == PROCESSING:
    # 通常完了 → セッション終了
    _terminate_oneshot_session(entry)
    oneshot_state = IDLE
    next_run_at = _calc_next_run(entry)
    warm_up_at = next_run_at - effective_warm_up_minutes * 60
```

---

## 5. 全モード共通: 処理完了待機

### 5.1 現行の課題

現行の `PeriodicScheduler._run_loop` では、ペインがスロットを保持中（kiro 処理中）の場合:

- `next_run_at` を `+30 秒` に更新してスキップ（ペイン自身が busy の場合）
- `next_run_at` を `+interval` に更新してスキップ（グローバル上限到達の場合）

これにより、処理が長引いた場合でも「スケジュール通りに次のプロンプトを送ろうとしたが失敗した」という意味で `next_run_at` が進んでしまい、次のサイクルが正しい間隔で来ない。

### 5.2 変更後の挙動

kiro がプロンプトを処理中である限り、**`next_run_at` を更新しない**。毎秒のポーリングで「完了したか？」を確認し、完了次第プロンプトを送信する。

```
スケジュール時刻到来
    │
    │ ペインがアイドル状態?
    ├─ Yes → プロンプト送信 → next_run_at を更新
    └─ No  → このサイクルは何もしない（next_run_at は据え置き）
               ↑
               次の 1 秒後ポーリングで再チェック
```

**`completion_wait_timeout` 超過時の動作**:

```python
# _run_loop 内の擬似コード
if is_pane_busy(pane_id):
    wait_since = entry.get('_busy_wait_since') or now
    entry['_busy_wait_since'] = wait_since
    if (now - wait_since) > completion_wait_timeout:
        logger.warning("completion_wait_timeout exceeded, forcing send")
        entry['_busy_wait_since'] = None
        # 強制的にプロンプトを送信（slot 解放は SlotMonitor に委ねる）
    else:
        return  # skip this cycle
else:
    entry['_busy_wait_since'] = None
    # 通常送信処理へ
```

> **注意**: `exclude_from_concurrency: true` のエントリは従来通り即時送信する（この変更の影響を受けない）。

### 5.3 `max_concurrent` との関係

| 状況 | 現行 | 変更後 |
|---|---|---|
| **対象ペイン自身**が処理中 | `next_run_at += 30s` してスキップ | `next_run_at` 据え置き、毎秒リトライ |
| 対象ペインのスロットがタイムアウト超過 | 強制解放して送信 | 変更なし |
| クールダウン中 | `next_run_at` をクールダウン終了時刻に更新 | 変更なし |
| **グローバル上限**到達（他ペインが消費） | `next_run_at += interval` してスキップ | `next_run_at` 据え置き、毎秒リトライ |

---

## 6. コンポーネント変更詳細

### 6.1 `PeriodicScheduler`

#### `_set_entries` の変更

正規化辞書 `normalized` に以下を追加:

```python
normalized['oneshot'] = bool(entry.get('oneshot', False))
normalized['warm_up_minutes'] = entry.get('warm_up_minutes', None)
# None の場合は実行時にグローバル設定から解決する

# oneshot 用の実行時状態（_set_entries では初期値を設定）
normalized['_oneshot_state'] = 'IDLE'
normalized['_oneshot_pending_prompt'] = None
normalized['_oneshot_session_name'] = None
normalized['_busy_wait_since'] = None
normalized['warm_up_at'] = None  # 起動後に計算
```

#### `_run_loop` の変更

```
既存の処理フロー（変更前）:
  now >= next_run_at?
    → スロット acquire → send → next_run_at 更新

変更後のフロー:
  [oneshot エントリの場合]
  now >= warm_up_at かつ state == IDLE?
    → _create_oneshot_session()
    → state = WARMING_UP

  state == WARMING_UP?
    → kiro-cli 準備完了チェック（startup_timeout 以内）
    → 準備完了 → state = READY
    → タイムアウト → state = IDLE, warm_up_at/next_run_at を再計算

  state == READY かつ now >= next_run_at?
    → _dispatch_prompt()
    → state = SENDING → PROCESSING

  [非 oneshot エントリの場合]
  now >= next_run_at?
    → ペインが busy か?
        Yes → _busy_wait_since を記録して skip（next_run_at 据え置き）
        No  → 通常送信 → next_run_at 更新
```

#### `_on_completion_detected` コールバック追加

`SlotMonitor` および `slot-release` サブコマンドから処理完了通知を受け取る新メソッド。

```python
def _on_completion_detected(self, pane_id: str):
    entry = self._find_entry_by_pane(pane_id)
    if entry is None:
        return
    if entry['oneshot']:
        self._handle_oneshot_completion(entry)
    else:
        # 非 oneshot: _busy_wait_since をリセット（次のポーリングで即時送信）
        entry['_busy_wait_since'] = None
```

### 6.2 `SessionManager`

#### 追加メソッド

| メソッド | 引数 | 説明 |
|---|---|---|
| `create_oneshot_session(prompt_id, name, cwd)` | prompt_id, セッション名, 作業ディレクトリ | oneshot 専用セッションを新規作成 |
| `attach_to_session(session_name)` | セッション名 | クライアントをセッションにアタッチ |
| `terminate_oneshot_session(prompt_id)` | prompt_id | kiro-cli 終了 → セッション削除 → クライアントデタッチ |
| `is_session_alive(session_name)` | セッション名 | tmux セッションの生存確認 |

#### セッション名規則（oneshot）

oneshot セッションは通常のセッションと名前空間を分けるため、プレフィックスを変更する。

```
通常:    kiro-loop-{dir_name}-{sha1[:8]}-{instance_id}
oneshot: kiro-loop-oneshot-{dir_name}-{sha1[:8]}-{instance_id}
```

`instance_id` にはプロンプトの UUID を使用する（同一 `cwd` に複数の oneshot プロンプトがあっても衝突しない）。

### 6.3 `SlotMonitor`

#### 完了通知コールバックの追加

`SlotMonitor` が処理完了を検知したとき、現状は `GlobalSemaphore.release` を呼ぶだけだが、`PeriodicScheduler._on_completion_detected` も呼ぶようにする。

```python
# SlotMonitor._release_slot 内
self._semaphore.release(pane_id)
if self._completion_callback:
    self._completion_callback(pane_id)  # PeriodicScheduler が登録
```

**コールバック登録方法**:

`SlotMonitor` のコンストラクタ引数または `set_completion_callback(cb)` メソッドで登録する。

```python
slot_monitor = SlotMonitor(semaphore)
slot_monitor.set_completion_callback(scheduler._on_completion_detected)
```

### 6.4 `load_config` の変更

新規グローバルパラメータを読み込んでデフォルト値とともに返す。

```python
config = {
    # 既存
    'startup_timeout': raw.get('startup_timeout', 60),
    'response_timeout': raw.get('response_timeout', 300),
    'slot_timeout_seconds': raw.get('slot_timeout_seconds', 7200),
    'cooldown_seconds': raw.get('cooldown_seconds', 30),
    # 新規
    'warm_up_minutes': raw.get('warm_up_minutes', 5),
    'completion_poll_interval': raw.get('completion_poll_interval', 2),
    'completion_wait_timeout': raw.get('completion_wait_timeout', 7200),
}
```

---

## 7. スレッド構成

既存スレッド構成に変更はない。`oneshot` に関する処理はすべて既存の `periodic-scheduler` スレッドと `slot-monitor` スレッド内に収める。

| スレッド名 | 担当 | 変更内容 |
|---|---|---|
| メインスレッド | `command_loop()` | 変更なし |
| `periodic-scheduler` | 定期プロンプト送信 | warm_up チェック、oneshot 状態管理、完了待機ロジック追加 |
| `slot-monitor` | 処理完了検知 + スロット解放 | 完了コールバック追加 |
| `session-monitor` | 死亡ペイン再起動 | oneshot セッションは再起動しない（セッション終了後は `IDLE` に戻す） |

### `session-monitor` の oneshot 除外ロジック

oneshot エントリの `_oneshot_state != IDLE` のペインを `session-monitor` が死亡検知して再起動しないよう除外リストを設ける。

```python
# session-monitor スレッド内
if session_manager.is_oneshot_session(pane_id):
    # 再起動せず PeriodicScheduler に通知のみ
    scheduler._on_session_died(pane_id)
    continue
```

---

## 8. 設定ファイルスキーマ（変更後）

### `~/.kiro/kiro-loop.yml`（グローバル設定）

```yaml
kiro_options:
  trust_all_tools: true
  resume: false

# タイムアウト・待機時間（既存）
startup_timeout: 60
response_timeout: 300
slot_timeout_seconds: 7200
cooldown_seconds: 30

# 同時実行制御（既存）
max_concurrent: 3

# oneshot / 完了待機（新規）
warm_up_minutes: 5           # oneshot セッションをスケジュール前に起動する時間（分）
completion_poll_interval: 2  # 完了チェックのポーリング間隔（秒）
completion_wait_timeout: 7200 # 完了待機の最大時間（秒）。0 = 無制限
```

### `.kiro/kiro-loop.yml`（プロジェクト設定・プロンプトエントリ）

```yaml
prompts:
  - name: "日次コードレビュー"
    prompt: "今日の変更をレビューしてください"
    cron: "0 9 * * 1-5"
    oneshot: true             # スケジュール実行後にセッションを終了する
    warm_up_minutes: 10       # このエントリ専用のウォームアップ時間（省略時はグローバル値）

  - name: "30分インターバルタスク"
    prompt: "進捗を確認してください"
    interval_minutes: 30
    oneshot: false            # デフォルト動作（セッション維持）
    # warm_up_minutes は oneshot: false では無視される
```

---

## 9. 後方互換性

| 項目 | 互換性 |
|---|---|
| `oneshot` 未指定エントリ | 変更なし（デフォルト `false`） |
| 既存グローバル設定パラメータ | 変更なし（新パラメータは追加のみ） |
| `slot-release` サブコマンド | 変更なし |
| `ls` / `send` サブコマンド | 変更なし |
| `max_concurrent: 0`（無制限モード） | 完了待機ロジックは `SlotMonitor` ベースの検知に依存するため、`max_concurrent: 0` 時は `SlotMonitor` を起動する必要がある（現行は `max_concurrent > 0` 時のみ起動）。条件を変更する |

### `SlotMonitor` 起動条件の変更

```python
# 変更前
if max_concurrent > 0:
    slot_monitor = SlotMonitor(semaphore)
    slot_monitor.start()

# 変更後（常に起動）
slot_monitor = SlotMonitor(semaphore)
slot_monitor.start()
```

`max_concurrent == 0` 時は `GlobalSemaphore.acquire` が常に `True` を返し、`SlotMonitor` は完了検知のみに使われる。既存の完了検知ロジックはスロットファイルではなくペイン出力のポーリングに基づくため、スロットなしでも動作する。

---

## 10. エラーハンドリング方針

| シナリオ | 対処 |
|---|---|
| ウォームアップ中に kiro-cli が起動しない（`startup_timeout` 超過） | `WARMING_UP → IDLE` に戻し、次の `warm_up_at` まで待機。ログに WARNING を記録 |
| oneshot セッション起動中に tmux エラー | ログに ERROR を記録し、`IDLE` に戻す。`next_run_at` は変更せず次回を試みる |
| kiro-cli の `/exit` コマンドが効かない（`startup_timeout` 秒以内に終了しない） | `kill-pane` で強制終了。ログに WARNING を記録 |
| `completion_wait_timeout` 超過 | 処理中でもプロンプト送信を強制続行。ログに WARNING を記録。oneshot の場合はセッション終了処理へ進む |
| セッション終了後の `session-monitor` による再起動検知 | oneshot セッションをホワイトリストで除外し再起動しない |
| アタッチ中クライアントがいない場合の `detach-client` | `tmux detach-client` エラーを無視して `kill-session` に進む |
| 3 回以上オーバーラップ | 古いスケジュールを破棄し最新 1 件のみ `pending_prompt` に保持。ログに WARNING を記録 |

---

## 11. 実装上の注意点

### tmux 操作の冪等性

`create_oneshot_session` を呼ぶ前に `is_session_alive` で既存セッションの存在確認を行う。既存セッションが残っている場合はアタッチのみ実行し、新規作成しない。

### アタッチ先クライアントの判定

kiro-loop デーモンは常に tmux セッション内で動作する（`_auto_attach_tmux_if_needed` により）。oneshot セッションへのアタッチは `tmux switch-client -t <session>` を使う（`attach-session` は新しいターミナルを必要とするため）。

### `warm_up_at` の再計算タイミング

`next_run_at` が更新されるたびに `warm_up_at` も再計算する。`PeriodicScheduler._update_next_run` に両者の更新をまとめる。

### `_oneshot_state` のスレッド安全性

`PeriodicScheduler._lock` の保護下で操作する。`SlotMonitor` からのコールバックは `_lock` を取得してから `_oneshot_state` を変更する。デッドロックを避けるため、コールバック内で `SessionManager._lock` を同時に取得しない（`SessionManager` 操作は非同期タスクとして切り出す）。

### `warm_up_minutes` と `interval_minutes` の矛盾

`warm_up_minutes >= interval_minutes` となる設定はウォームアップが意味をなさないため、`_set_entries` でチェックして WARNING を出す。ただし動作は妨げない（即時起動する）。

---

## 12. テスト観点

| テストケース | 確認内容 |
|---|---|
| `oneshot: true`、正常フロー | warm_up → READY → PROCESSING → セッション終了 の状態遷移 |
| `oneshot: true`、オーバーラップ | PROCESSING 中に next_run_at 到来 → OVERLAP_WAIT → セッション維持で次プロンプト送信 → 終了 |
| `oneshot: false`、完了待機 | kiro 処理中に next_run_at 到来 → `next_run_at` 据え置きで毎秒リトライ → 完了後に送信 |
| `warm_up_minutes` の優先順位 | エントリ値 > グローバル値 > デフォルト(5) の順で解決 |
| `completion_wait_timeout` 超過 | タイムアウト後に強制送信・ログ出力 |
| `startup_timeout` 超過（warm-up） | `IDLE` に戻り次回スケジュールを試みる |
| `max_concurrent: 0` での完了待機 | `SlotMonitor` が起動してペイン出力ベースで完了検知 |
| 後方互換性 | `oneshot` 未指定エントリが従来通り動作する |
