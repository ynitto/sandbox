# kiro-loop 設計資料

> 最終更新: 2026-05-04  
> 対象ファイル: `kiro-loop.py`（約 2640 行）

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
- `release(pane_id)` → スロットファイル削除 + クールダウンファイル書き込み
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

---

### 3.4 `PeriodicScheduler`

**役割**: 各プロンプトエントリの `next_run_at` を管理し、時刻が来たらペインへ送信する。

**スレッド**: `periodic-scheduler`（1 秒ごとにポーリング）

**エントリ正規化** (`_set_entries`):
- `enabled: false` のエントリは除外
- `prompt`, `interval_minutes` が空/0 のエントリは除外
- `run_immediately_on_startup: true` なら起動後 30 秒で初回送信、それ以外は `interval_minutes` 後
- UUID が未設定なら自動生成

**`_run_loop` の処理フロー**（1 秒ごと）:
```
各エントリについて:
  now >= next_run_at? → No: スキップ
  fresh_context: should_clear を決定
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
    │      失敗 → _try_enqueue_for_pane()
    │
    └─ _drain_pane_queue()  ← 次サイクル以降にキューを消化

SlotMonitor._run_loop() [別スレッド]
    ├─ ペイン: waiting_start → processing → semaphore.release()  [プロンプト復帰検知]
    └─ タイムアウト時: 強制 semaphore.release()

kiro-cli agent hook (stop)
    └─ kiro-loop slot-release → GlobalSemaphore.release($TMUX_PANE)
                                  └─ SlotMonitor.untrack(pane_id) ← hookが勝った場合
```

`slot-release` hook が正常に発火した場合は SlotMonitor の解放より先になるため、SlotMonitor は `_pending` にペインが登録されたまま次のポーリングで何もしない（既にスロットファイルが消えているため）。

---

## 10. 拡張・変更時の注意点

### 新しいサブコマンドを追加する
`main()` の `subparsers.add_parser()` でパーサーを定義し、`args.subcommand` の分岐を追加する。サブコマンドは `SessionManager` を生成しないため、tmux セッションを触る場合は `_tmux_cmd()` を直接使用する。

### 新しいプロンプトオプションを追加する
`PeriodicScheduler._set_entries()` の `normalized` 辞書にフィールドを追加し、`_run_loop()` で参照する。`kiro-loop.yaml.example` にもドキュメントを追記すること。

### 設定ファイルの読み込み先を変更する
`load_config()` がグローバル設定（`~/.kiro/`）、`_load_prompt_file_data()` がワークスペース設定（`<project>/.kiro/`）を担当する。両者の役割を混在させないこと。

### スレッド安全性
`SessionManager._lock` と `PeriodicScheduler._lock` は別物。クロスロックによるデッドロックを避けるため、両方を同時に保持するコードは書かない。

### セマフォのエラー処理方針
ファイル I/O エラーは「安全側（実行許可）に倒す」が基本方針（`GlobalSemaphore.acquire` の `except OSError`）。監視対象ペインが存在しない場合も即座に解放する。

---

## 11. デバッグ・運用

| 操作 | コマンド |
|---|---|
| デーモンログ確認 | `tail -f ~/.kiro/kiro-loop.log` |
| スロット状態確認 | `ls ~/.kiro/slots/` |
| デーモン状態確認 | `ls ~/.kiro/loop-state/` |
| セッション一覧 | `kiro-loop ls` |
| スロット強制解放 | `rm ~/.kiro/slots/pane_<ID>.json` |
| デーモン強制停止 | `kill <pid>` (状態ファイルの `pid` フィールド参照) |
| ログレベル変更 | `kiro-loop --log-level DEBUG` |
