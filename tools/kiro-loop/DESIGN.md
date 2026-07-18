# kiro-loop 設計資料

> 最終更新: 2026-05-04  
> 対象ファイル: `kiro-loop.py`（約 2570 行）

---

## 1. 概要

kiro-loop は **tmux + kiro-cli** を組み合わせ、設定ファイルで定義したプロンプトを定期的に自動送信するデーモンスクリプト。

主な役割:
- `kiro-cli chat` プロセスを tmux ペインとして起動・死活監視・再起動
- 定期プロンプトを指定インターバルでペインへ送信
- 複数デーモン間で kiro-cli の同時実行数を制御（ファイルベースセマフォ）
- `ls` / `send` サブコマンドによる外部操作

---

## 2. 起動フロー

```
kiro-loop (引数なし)
    │
    ├─ サブコマンドあり → ls / send / slot-release を実行して終了
    │
    ├─ 同一 cwd のデーモンが既に起動中 → スキップして終了
    │
    ├─ tmux 外で起動 → _auto_attach_tmux_if_needed()
    │      tmux new-session を exec して自身を tmux 内で再実行
    │
    ├─ 設定ロード (load_config + _load_prompt_file_data)
    │
    ├─ SessionManager 生成
    ├─ SlotMonitor 生成（max_concurrent > 0 の場合のみ）
    ├─ PeriodicScheduler 生成 → start()
    ├─ SlotMonitor.start()
    ├─ session-monitor スレッド起動
    │
    └─ command_loop() ← メインスレッドで stdin を読み続ける
```

---

## 3. クラス構成

### 3.1 `GlobalSemaphore`

**役割**: 複数の kiro-loop プロセス間で `kiro-cli` の同時実行数を制御する。

**実装**: `~/.kiro/slots/` 以下のファイルで状態を共有。

| ファイル | 意味 |
|---|---|
| `pane_<ID>.json` | 実行中スロット（`pane_id`, `pid`, `acquired_at`） |
| `cooldown_<ID>.json` | クールダウン記録（`pane_id`, `released_at`） |
| `.lock` | `fcntl.flock` によるミューテックス |

**主要メソッド**:
- `acquire(pane_id, pid)` → スロット空き確認 + ファイル書き込み（`LOCK_EX` でアトミック）
- `release(pane_id)` → スロットファイル削除 + クールダウンファイル書き込み。解放時に
  スロット保持時間（送信→完了検知）を**ノード予算の共有台帳**へ `workload: routine` で
  記帳する（契約: `schemas/node-budget.schema.json`。タイムアウト強制解放は数えない）。
  また `PeriodicScheduler._run_loop` はサイクル先頭でノード予算をチェックし、
  超過中は定期送信・webhook キューの dispatch を停止する（上限 0 = 無制限が既定）
- `slot_elapsed(pane_id)` → 取得からの経過秒（タイムアウト検知用）
- `cooldown_remaining(pane_id)` → クールダウン残り秒
- `is_busy(pane_id)` → スロットファイル参照でペインの処理中を判定（静的）

**注意点**:
- `max_concurrent <= 0` は無制限（`acquire` が常に `True` を返す）
- ファイル読み書きエラー時は安全側（実行許可）に倒す
- スロットの `pid` は `os.kill(pid, 0)` でプロセス生存確認に使用

---

### 3.2 `SlotMonitor`

**役割**: kiro-cli agent hook が発火しなかった場合のフォールバック。ペイン出力を監視してスロットを自動解放する。

**状態遷移**:
```
waiting_start
    ↓ プロンプト消失を検知（kiro-cli が処理開始）
processing
    ↓ プロンプト再出現（処理完了）OR タイムアウト
スロット解放
```

**定数**:
- `_POLL_INTERVAL = 2.0` 秒ごとにポーリング
- `_START_WAIT_TIMEOUT = 60.0` 秒 — kiro-cli が処理を開始しないままこの時間を超えたらスロット解放

**インターフェース**:
- `track(pane_id)` — 送信直後に呼び出してペインを監視対象に登録
- `untrack(pane_id)` — agent hook 発火時など、手動で監視を解除

---

### 3.3 `SessionManager`

**役割**: プロンプトごとに `kiro-cli chat` の tmux ペインを管理する。

**内部状態** (すべて `_lock` で保護):
| フィールド | 型 | 意味 |
|---|---|---|
| `_panes` | `dict[prompt_id, pane_target]` | 管理中ペイン |
| `_prompt_names` | `dict[prompt_id, name]` | プロンプト名 |
| `_tmux_names` | `dict[prompt_id, session_name]` | アタッチ先セッション名 |
| `_prompt_cwds` | `dict[prompt_id, cwd]` | 作業ディレクトリ |
| `_restart_locks` | `dict[prompt_id, Lock]` | 再起動の二重実行防止 |

**tmux 操作の流れ**:
1. `_ensure_layout()` — 現在の tmux ウィンドウまたは専用セッションを確定
2. `_create_worker_pane()` — `split-window` でペインを追加、`remain-on-exit on` を設定
3. `send_prompt()` — `set-buffer → paste-buffer → send-keys Enter` の 3 ステップ（特殊文字対応）

**セッション名生成** (`_tmux_session_name`):
```
kiro-loop-{dir_name}-{sha1(resolved_path)[:8]}-{instance_id}
```
cwd が変わらない限り同じセッション名が生成される。

**状態ファイル**: `~/.kiro/loop-state/<pid>.json`  
`ls`/`send` サブコマンドがこれを読んでペイン情報を取得する。
`sessions[]` の各レコードには、プロンプト送信のたびに `last_sent_at`（epoch 秒）と
`last_send_ok` を記録する（agent-dashboard の構造化状態ビューが参照する）。

---

### 3.4 `PeriodicScheduler`

**役割**: 各プロンプトエントリの `next_run_at` を管理し、時刻が来たらペインへ送信する。

**スレッド**: `periodic-scheduler`（1 秒ごとにポーリング）

**エントリ正規化** (`_set_entries`):
- `enabled: false` のエントリは除外
- `interval_minutes` が空/0 のエントリは除外。ただし `webhook` ブロックを持つエントリは
  スケジュール無し（push 駆動）を許容し、`next_run_at = math.inf`（sentinel）にして
  自動発火パスから外す
- `prompt` が空のエントリは除外。ただし `event_hook` を指定している場合は許容（フックが送信内容を決めるため）
- `run_immediately_on_startup: true` なら起動後 30 秒で初回送信、それ以外は `interval_minutes` 後
- UUID が未設定なら自動生成
- `event_hook`（フックスクリプトのパス）・`event_hook_fallback`（bool）を正規化エントリに保持
- `webhook`（`{hook, secret, secret_header}` に正規化、無ければ None）を正規化エントリに保持

**event_hook**:
- スケジュール発火のたびにフックの `check() -> str | None` を呼ぶ（`importlib` でインプロセス実行、`mtime` でキャッシュ）
- `str` を返せばその文字列を `prompt` として送信、`None` ならそのサイクルはスキップ
- `event_hook_fallback: true` のとき、フック呼び出し前に環境変数 `KIRO_LOOP_EVENT_HOOK_FALLBACK=1` を設定する（false なら `0`）。フック側はこれを見て「更新が無いときでもフィルター条件に合致する対象をランダム送信する」等のフォールバックを自己判断する。`KIRO_LOOP_PROMPT_NAME` にエントリ名も渡す。環境変数は呼び出し後に元へ戻す（scheduler は単一スレッドのため安全）
- 同梱例: `hooks/gitlab-issue-hook.py` / `hooks/gitlab-mr-hook.py`

**inbound webhook**（`event_hook` のプッシュ版・provider 非依存）:
- kiro-loop 稼働中だけ `WebhookServer`（標準ライブラリ `http.server.ThreadingHTTPServer`）を
  常駐させ、`POST <path_prefix>/<name>` を受ける。グローバル `webhook:` 設定（`enabled`/`host`/
  `port`/`path_prefix`/`secret`/`secret_header`/`max_body_bytes`）で制御し、`enabled` かつ
  `port>0` のときだけ起動。bind 失敗（ポート衝突等）は WARNING を出して本体は継続
- `<name>` は毎リクエスト `scheduler.resolve_webhook_route(name)` で最新エントリへ解決
  （ルート表を持たずリロード追従）。突き合わせは `_webhook_key`（URL-safe 化 + 小文字化）
- コアは provider 非依存。認証は**汎用共有シークレット照合のみ**（照合ヘッダ名は `secret_header`
  で可変、`hmac.compare_digest`）。イベント種別フィルタ・署名検証・payload 構造の解釈は
  すべて hook 側（`handle(ctx) -> dict | None`）の責務
- hook が返した dict は基本キー `name` を補完しつつエントリの `prompt` テンプレートへ
  `str.format_map(_SafeDict(...))` で注入（未定義キーは `{key}` のまま残す）。HTTP スレッドは
  完成プロンプトを `scheduler.enqueue_external(name, text)` で name 別の bounded deque
  （`_external_queues`、上限 `_WEBHOOK_QUEUE_MAX`）へ積んで即 `202` を返す
- 実 dispatch は `_run_loop` の `_drain_external_one()`（1 サイクル 1 件）が担当。session 準備・
  セマフォ判定を通し、未準備/上限時は `appendleft` で積み直す（再起動でキューは消える＝at-most-once）
- hook のロードは event_hook と共通の `_load_hook_module`（HTTP/scheduler の複数スレッドから
  呼ばれるため `_hook_cache_lock` で保護）
- 同梱例: `hooks/gitlab-mr-webhook.py`（GitLab MR）/ `hooks/generic-webhook.py`（非 GitLab 最小例）
- 詳細設計: `docs/designs/kiro-loop-gitlab-webhook-design.md`

**`_run_loop` の処理フロー**（1 秒ごと）:
```
各エントリについて:
  webhook あり かつ 外部キューに要素あり?
    → _drain_external_one()（1 件送信 or 保留で積み直し）してこのエントリは終了
  now >= next_run_at? → No: スキップ
  fresh_context: should_clear を決定
  event_hook あり? → check() を呼ぶ
    None → next_run_at を更新してスキップ
    str  → entry["prompt"] を上書き
  ensure_session() でペイン確保
  max_concurrent > 0 かつ exclude_from_concurrency でない場合:
    _acquire_slot() でセマフォ取得
    取得失敗 → _try_enqueue_for_pane() でキューへ、next_run_at を +interval
  _dispatch_prompt() でプロンプト送信
  next_run_at = now + interval

_drain_pane_queue() でキュー済みプロンプトを送信試行
```

**fresh_context 機能**:
- `fresh_context: true` → 毎回送信前に `/clear` を送信
- `fresh_context_interval_minutes` を指定すると、その間隔でのみ `/clear` を実行（通常送信は毎回）

**pane queue**:  
同時実行数が上限に達した場合、プロンプトをキューに積んで次サイクルで再試行する。ペイン単位で管理し、同じペインに複数エントリが競合した場合は `scheduled_at` が早いものを残す。

---

## 4. スレッド構成

| スレッド名 | 担当 | 間隔 |
|---|---|---|
| メインスレッド | `command_loop()` — stdin コマンド受付 | ブロッキング |
| `periodic-scheduler` | 定期プロンプト送信 | 1 秒 |
| `slot-monitor` | ペイン処理完了検知 + スロット解放 | 2 秒 |
| `session-monitor` | 死亡ペインの再起動 + 状態ファイル更新 | 10 秒 |

---

## 5. ファイルシステムレイアウト

```
~/.kiro/
├── kiro-loop.yaml            グローバル設定ファイル（load_config が参照）
├── kiro-loop.log             ローテートログ（7世代保持）
├── agents/
│   └── kiro-loop-concurrency.json   install.sh が自動生成する agent 設定
├── slots/
│   ├── .lock                 fcntl ミューテックス
│   ├── pane_<ID>.json        実行中スロット
│   └── cooldown_<ID>.json    クールダウン記録
└── loop-state/
    └── <pid>.json            デーモン状態（ls/send が参照）

<project>/
└── .kiro/
    ├── kiro-loop.yaml        ワークスペース固有設定（prompts / kiro_options）
    └── kiro-loop.yml         同上（どちらか一方）

<project>/
└── .vscode/
    └── settings.json         agentExecutor.periodicPrompts を読み込み
```

---

## 6. 設定の優先順位

```
1. ~/.kiro/kiro-loop.yaml  ← グローバル設定（kiro_options, タイムアウト, max_concurrent）
2. <project>/.kiro/kiro-loop.yml  ← ワークスペース固有プロンプト・kiro_options
3. <project>/.vscode/settings.json agentExecutor.periodicPrompts
   （~/.kiro/ に設定ファイルがない場合のみ有効）
```

`kiro_options` は `~/.kiro/kiro-loop.yaml` を優先し、ない場合に `.kiro/kiro-loop.yml` を使用。  
`prompts` は `.kiro/kiro-loop.yml` が正とし、起動後はこのファイルへ書き込む。

---

## 7. サブコマンド

### `ls`
1. `_read_all_states()` で生きているデーモンの状態ファイルを全列挙
2. デーモンがなければ tmux から `kiro` プレフィックスのセッションを直接取得して表示

### `send [--session/-s] [--dir/-d] PROMPT`
1. `--session` 未指定時 → 状態ファイルから alive なペインを自動解決（複数時はエラー）
2. `PROMPT` の解決順序:
   - ファイルとして存在する → 内容を読んで「実行してください」と送信
   - `.kiro/kiro-loop.yml` のプロンプト名と一致 → そのテキストを使用
   - そのまま自然文として送信
3. ペインが処理中（スロットあり or プロンプト非表示）なら送信拒否
4. `max_concurrent > 0` のデーモン管理下ペインにはスロットを取得してから送信

### `slot-release`
kiro-cli agent hook（`stop` イベント）から呼び出されるコマンド。`$TMUX_PANE` のスロットを解放する。

---

## 8. tmux 自動アタッチ

tmux 外で起動された場合（`$TMUX` 未設定）、`_auto_attach_tmux_if_needed()` が:
1. セッションが未作成 → `os.execvp(tmux, ["new-session", ..., controller_cmd])` で自身を置き換え
2. セッションが既存 → 新ウィンドウを追加してから `attach-session` を exec

`--controller-mode` フラグ付きで再実行されることで、同じ cwd の二重デーモン起動を防ぐ。

---

## 9. 同時実行制御の全体像

```
PeriodicScheduler._run_loop()
    ├─ GlobalSemaphore.acquire(pane_id)
    │      成功 → _dispatch_prompt()
    │              ├─ send_prompt() 成功 → SlotMonitor.track(pane_id)
    │              └─ send_prompt() 失敗 → semaphore.release(pane_id)
    │      失敗 → ログ出力してスキップ（next_run_at を +interval に更新）
    │
    └─ [次のインターバルで再試行]

SlotMonitor._run_loop() [別スレッド]
    ├─ ペイン: waiting_start → processing → semaphore.release()  [プロンプト復帰検知]
    └─ タイムアウト時: 強制 semaphore.release()

kiro-cli agent hook (stop)
    └─ kiro-loop slot-release → GlobalSemaphore.release($TMUX_PANE)
                                  └─ SlotMonitor.untrack(pane_id) ← hookが勝った場合
```

`slot-release` hook が正常に発火した場合は SlotMonitor の解放より先になるため、SlotMonitor は `_pending` にペインが登録されたまま次のポーリングで何もしない（既にスロットファイルが消えているため）。

---

## 10. コマンド・操作別の同時実行上限到達時の挙動

`max_concurrent > 0` が設定されている場合、以下の通り各操作で挙動が異なる。

### `PeriodicScheduler`（定期スケジューラ）

| 条件 | 挙動 |
|---|---|
| 対象ペイン自身がスロット保持中（前回実行が未完了） | `next_run_at` を +30 秒に更新してスキップ。30 秒後に再試行 |
| 対象ペインのスロットがタイムアウト超過 | スロットを強制解放して今回の送信を続行 |
| クールダウン中 | `next_run_at` をクールダウン終了時刻に更新してスキップ |
| グローバル上限到達（他ペインがスロットを消費） | ログ + stderr メッセージを出してスキップ。`next_run_at` を +interval に更新し次のサイクルで再試行 |

スキップされた回は **失われる**（キューには積まない）。次のインターバルで通常通り再試行される。

### `send` サブコマンド

| 条件 | 挙動 |
|---|---|
| 対象ペインがスロット保持中（`_pane_is_busy`） | エラーメッセージを出して `exit 1` |
| 対象ペインのプロンプトが非表示（処理中） | エラーメッセージを出して `exit 1` |
| グローバル上限到達（他ペインがスロットを消費） | エラーメッセージを出して `exit 1` |

手動 `send` はリトライせず即時終了する。ユーザーが完了を待って再実行すること。

### `command_loop` の `send` コマンド（デーモン内インタラクティブ）

デーモン内 `send` コマンドはセマフォを介さず直接 `_send_to_pane()` を呼ぶため、同時実行制御の影響を受けない。管理対象ペインへのアドホック送信用途のため意図的な設計。

### `slot-release`（agent hook）

セマフォ解放のみを行うコマンドであり、同時実行上限の影響を受けない。

---

## 11. 拡張・変更時の注意点

### 新しいサブコマンドを追加する
`main()` の `subparsers.add_parser()` でパーサーを定義し、`args.subcommand` の分岐を追加する。サブコマンドは `SessionManager` を生成しないため、tmux セッションを触る場合は `_tmux_cmd()` を直接使用する。

### 新しいプロンプトオプションを追加する
`PeriodicScheduler._set_entries()` の `normalized` 辞書にフィールドを追加し、`_run_loop()` で参照する。`kiro-loop.yaml.example` にもドキュメントを追記すること。

### event_hook を追加・変更する
- フックは `check() -> str | None` を実装する。`check()` は scheduler スレッド内で同期実行されるため、ネットワーク呼び出しには短い timeout を設定しブロックを避けること。
- フックのロードは `_load_hook_module()`（`mtime` キャッシュ付き）、呼び出しは `_call_hook_check()`。`importlib.util.exec_module` はトップレベルコードを実行するため、副作用は `check()` 内に閉じること。
- フォールバック有無は YAML の `event_hook_fallback` で制御し、環境変数 `KIRO_LOOP_EVENT_HOOK_FALLBACK`（`1`/`0`）でフックへ渡す。新しいフックでもこの規約に従う。

### webhook フックを追加・変更する
- フックは `handle(ctx) -> dict | None` を実装する。`ctx`（`name`/`method`/`headers`/`query`/`raw`/`payload`）から **provider 固有の判定（イベント種別・署名検証）を自分で行い**、対象外は `None` を返す。返す dict は `prompt` テンプレートの `{key}` に注入される。
- `handle()` は `WebhookServer` の複数スレッドから同時に呼ばれ得る。モジュール状態を持たせずステートレスに保つこと（持つ場合は自前でロック）。
- コアに provider 固有を足さないこと。認証は汎用共有シークレット照合のみで、HMAC 署名方式や `X-Gitlab-Event` 等のヘッダ解釈はフック側に閉じる。
- 送信先は既存の名前付きセッション（`prompts` エントリ）。webhook 専用エントリはスケジュール不要（`next_run_at = math.inf`）で、`_drain_external_one()` 経由でのみ送信される。

### 設定ファイルの読み込み先を変更する
`load_config()` がグローバル設定（`~/.kiro/`）、`_load_prompt_file_data()` がワークスペース設定（`<project>/.kiro/`）を担当する。両者の役割を混在させないこと。

### スレッド安全性
`SessionManager._lock` と `PeriodicScheduler._lock` は別物。クロスロックによるデッドロックを避けるため、両方を同時に保持するコードは書かない。

### セマフォのエラー処理方針
ファイル I/O エラーは「安全側（実行許可）に倒す」が基本方針（`GlobalSemaphore.acquire` の `except OSError`）。監視対象ペインが存在しない場合も即座に解放する。

---

## 12. デバッグ・運用

| 操作 | コマンド |
|---|---|
| デーモンログ確認 | `tail -f ~/.kiro/kiro-loop.log` |
| スロット状態確認 | `ls ~/.kiro/slots/` |
| デーモン状態確認 | `ls ~/.kiro/loop-state/` |
| セッション一覧 | `kiro-loop ls` |
| スロット強制解放 | `rm ~/.kiro/slots/pane_<ID>.json` |
| デーモン強制停止 | `kill <pid>` (状態ファイルの `pid` フィールド参照) |
| ログレベル変更 | `kiro-loop --log-level DEBUG` |
