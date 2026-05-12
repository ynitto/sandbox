# kiro-loop イベントフック拡張 設計案

> 作成日: 2026-05-12  
> 対象ファイル: `tools/kiro-loop/kiro-loop.py`

---

## 1. 背景・目的

現在の kiro-loop はスケジュール（`interval_minutes` / `cron`）に従って固定のプロンプトを送信する。  
これを拡張し、**プロンプト単位に「タイミング・内容を制御する Python スクリプト」を設定できる**ようにしたい。

主なユースケース:
- GitLab イシューをポーリングし、変化があったときだけ送信。ラベルに応じてプロンプトを切り替える
- 外部 API や状態ファイルを参照して送信要否・送信内容を動的に決定する
- 時刻ベーススケジュールとは独立した「イベント駆動」なプロンプト発火

---

## 2. 設計方針

| 方針 | 内容 |
|---|---|
| **排他的スケジュール** | `event_hook` を設定したエントリには `interval_minutes`/`cron` を使わない。スクリプトがタイミングを完全に制御する |
| **インプロセス実行** | スクリプトは `importlib` 経由で読み込み、scheduler スレッド内で `check()` 関数を直接呼ぶ（subprocess 不使用） |
| **輻輳時のキューイング** | セマフォ上限到達時はプロンプトを破棄せず、エントリに保持して次回ポーリングで再送試行 |
| **変更範囲を最小化** | 既存のスケジュール・セマフォ・セッション管理ロジックは変更しない。`_run_loop` に event_hook 分岐を追加するだけ |
| **後方互換性** | `event_hook` が未設定のエントリは従来通り動作 |

---

## 3. フックスクリプトのインターフェース

### 呼び出し方法

`importlib.util.spec_from_file_location` でモジュールをロードし、`check()` 関数を呼ぶ。  
**scheduler スレッド内で同期実行**されるため、`check()` 内ではブロッキング処理を最小限にすること。

### 関数シグネチャ

```python
def check() -> str | None:
    """
    poll_interval_seconds ごとに scheduler スレッドから呼ばれる。

    Returns:
        str  : kiro-cli に送信するプロンプトテキスト
        None : このサイクルをスキップ（何も送らない）
    """
    ...
```

### モジュールキャッシュ

- ファイルの `mtime` を監視し、変更時のみ再ロード
- 未変更時はキャッシュ済みモジュールを再利用（毎回ロードコストなし）
- `check` 関数が存在しない場合は WARNING を出してスキップ

---

## 4. 設定スキーマ変更

### `kiro-loop.yaml` / `.kiro/kiro-loop.yml` のプロンプトエントリ

```yaml
prompts:
  - name: "GitLab Issue ワーカー"
    event_hook: ~/.kiro/hooks/gitlab-issue-hook.py
    poll_interval_seconds: 30   # check() を呼ぶ間隔（デフォルト: 60）
    # prompt / interval_minutes / cron は不要
    enabled: true
```

`event_hook` と `interval_minutes`/`cron` を同時に指定した場合は WARNING を出し、`event_hook` を優先（`interval_minutes`/`cron` 無視）。

---

## 5. コード変更範囲

### 5.1 `_set_entries()` — 変更内容

`interval_minutes`/`cron` が必須だった制約を緩和。`event_hook` がある場合は `poll_interval_seconds` を保持する。

```python
# 変更前: interval か cron がないと continue でスキップ
# 変更後: event_hook があればスケジュールなしでも受け入れる

has_hook = bool(str(entry.get("event_hook", "")).strip())

if has_hook:
    if cron_str or interval > 0:
        log.warning("[%s] event_hook と interval/cron は排他です。event_hook を優先します。", name)
    poll_interval = max(int(entry.get("poll_interval_seconds") or 60), 1)
    normalized.append({
        "id": prompt_id,
        "name": name,
        "prompt": str(entry.get("prompt", "")).strip(),  # 省略可
        "event_hook": str(entry.get("event_hook")).strip(),
        "poll_interval_seconds": poll_interval,
        "poll_next_at": now,    # 起動直後に初回ポーリング
        "_queued_prompt": None, # 輻輳時にプロンプトを保持
        "enabled": True,
        "exclude_from_concurrency": bool(entry.get("exclude_from_concurrency", False)),
        "cwd": entry_cwd,
    })
else:
    # 既存の interval/cron バリデーション（変更なし）
    ...
    normalized.append({ ... })  # 既存フィールド（変更なし）
```

**既存の `normalized.append({...})` は変更しない。**

---

### 5.2 新規メソッド 3本追加

#### `_load_hook_module(hook_path: Path)` — ~20行

```python
def _load_hook_module(self, hook_path: Path) -> Any | None:
    """importlib でフックモジュールをロード（mtime キャッシュ付き）。"""
    key = str(hook_path)
    try:
        mtime = hook_path.stat().st_mtime
    except OSError:
        log.warning("event_hook が見つかりません: %s", hook_path)
        return None

    cached = self._hook_cache.get(key)
    if cached and cached[0] == mtime:
        return cached[1]

    try:
        spec = importlib.util.spec_from_file_location("kiro_loop_hook", hook_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self._hook_cache[key] = (mtime, module)
        return module
    except Exception as exc:
        log.error("event_hook のロードに失敗しました (%s): %s", hook_path, exc)
        return None
```

`self._hook_cache: dict[str, tuple[float, Any]] = {}` を `__init__` で初期化。

---

#### `_call_hook_check(entry: dict) -> str | None` — ~20行

```python
def _call_hook_check(self, entry: dict[str, Any]) -> str | None:
    """フックの check() を呼び出し、送信するプロンプトを返す。"""
    hook_path = Path(os.path.expanduser(entry["event_hook"])).resolve()
    name = str(entry.get("name", ""))
    module = self._load_hook_module(hook_path)
    if module is None:
        return None

    check_fn = getattr(module, "check", None)
    if not callable(check_fn):
        log.warning("[%s] event_hook に check() 関数が定義されていません。", name)
        return None

    try:
        result = check_fn()
        if result is not None and not isinstance(result, str):
            log.warning("[%s] check() の戻り値が str でも None でもありません: %r", name, result)
            return None
        return result
    except Exception as exc:
        log.error("[%s] check() の実行中にエラーが発生しました: %s", name, exc, exc_info=True)
        return None
```

---

#### `_try_acquire_slot_hook(entry: dict, pane_id: str) -> bool` — ~25行

既存の `_acquire_slot` はスロット取得失敗時に `next_run_at` を更新する。  
event_hook エントリはスケジュールなしのため、失敗時は `_queued_prompt` に保持して `False` を返す専用メソッドが必要。

```python
def _try_acquire_slot_hook(self, entry: dict[str, Any], pane_id: str) -> bool:
    """event_hook 専用スロット取得。失敗時はキューイングのみ（next_run_at 更新なし）。"""
    assert self._semaphore is not None
    name = str(entry.get("name", ""))

    elapsed = self._semaphore.slot_elapsed(pane_id)
    if elapsed is not None:
        if elapsed < self._semaphore.slot_timeout:
            log.info("[%s] 前回の実行が完了待ちです。キューに保持します。", name)
            return False
        else:
            log.warning("[%s] スロットがタイムアウト超過。強制解放します。", name)
            if self._slot_monitor is not None:
                self._slot_monitor.untrack(pane_id)
            self._semaphore.release(pane_id)

    if self._semaphore.cooldown_remaining(pane_id) > 0:
        log.info("[%s] クールダウン中。キューに保持します。", name)
        return False

    if not self._semaphore.acquire(pane_id):
        log.info("[%s] 同時実行数が上限。キューに保持します。", name)
        return False

    return True
```

---

### 5.3 `_run_loop()` — event_hook 分岐を追加（~25行）

既存ループの先頭に分岐を追加。**既存コードは移動・変更しない。**

```python
def _run_loop(self) -> None:
    while not self._stop_event.wait(1):
        now = time.time()
        with self._lock:
            entries = [e.copy() for e in self._entries]

        for entry in entries:
            if not entry.get("enabled", True):
                continue

            # ---- event_hook 分岐（新規） ----
            if entry.get("event_hook"):
                self._run_hook_entry(entry, now)   # 後述のヘルパーに委譲
                continue                            # 既存スケジュール処理には入らない
            # ---- ここまで新規 ----

            # 既存スケジュール処理（変更なし）
            if now < float(entry.get("next_run_at", now)):
                continue
            ...
```

`_run_hook_entry` を別メソッドに切り出すことで `_run_loop` の肥大化を防ぐ。

---

#### `_run_hook_entry(entry, now)` — ~30行（新規メソッド）

```python
def _run_hook_entry(self, entry: dict[str, Any], now: float) -> None:
    """event_hook エントリの1サイクル処理。"""
    prompt_id = str(entry.get("id", ""))
    name = str(entry.get("name", ""))
    poll_interval = int(entry.get("poll_interval_seconds", 60))
    exclude = bool(entry.get("exclude_from_concurrency", False))

    # キュー済みプロンプトがあれば再送試行、なければポーリング
    queued = entry.get("_queued_prompt")
    if queued:
        prompt_to_send = queued
    elif now >= float(entry.get("poll_next_at", 0)):
        prompt_to_send = self._call_hook_check(entry)
        self._update_entry(prompt_id, poll_next_at=now + poll_interval)
        if prompt_to_send is None:
            return  # スキップ
    else:
        return  # ポーリング時刻前

    if not self._session_mgr.ensure_session(prompt_id, name):
        log.warning("[%s] セッション確保失敗。キューに保持します。", name)
        self._update_entry(prompt_id, _queued_prompt=prompt_to_send)
        return

    pane_id: str | None = None
    if self._semaphore is not None and not exclude:
        pane_id = self._session_mgr.get_pane_id(prompt_id)
        if pane_id and not self._try_acquire_slot_hook(entry, pane_id):
            self._update_entry(prompt_id, _queued_prompt=prompt_to_send)
            return

    dispatch_entry = dict(entry)
    dispatch_entry["prompt"] = prompt_to_send
    self._dispatch_prompt(dispatch_entry, pane_id)
    self._update_entry(prompt_id, _queued_prompt=None)  # キュークリア
```

---

### 変更量サマリ

| ファイル | 変更種別 | 追加行数 | 変更行数 |
|---|---|---|---|
| `kiro-loop.py` | `_set_entries` に event_hook 分岐を追加 | +20 | 0 |
| `kiro-loop.py` | `__init__` に `_hook_cache = {}` 追加 | +1 | 0 |
| `kiro-loop.py` | `_load_hook_module` 新規メソッド | +20 | 0 |
| `kiro-loop.py` | `_call_hook_check` 新規メソッド | +20 | 0 |
| `kiro-loop.py` | `_try_acquire_slot_hook` 新規メソッド | +25 | 0 |
| `kiro-loop.py` | `_run_loop` に event_hook 分岐追加（3行） | +3 | 0 |
| `kiro-loop.py` | `_run_hook_entry` 新規メソッド | +30 | 0 |
| `kiro-loop.yaml.example` | `event_hook` オプション追記 | +15 | 0 |
| `hooks/gitlab-issue-hook.py` | 新規フック例 | +70 | — |

**既存メソッドへの変更行数: 0**（`_set_entries` への追加は新しい `if` ブロックとして挿入）

---

## 6. スレッド安全性の注意点

`check()` は `periodic-scheduler` スレッドで実行されるため:

| 注意点 | 対処 |
|---|---|
| ブロッキング処理 | `check()` が長時間ブロックすると他エントリのスケジュールが遅延する。ネットワーク呼び出しは短いタイムアウトを設定すること |
| モジュールレベル変数 | フックスクリプト内のグローバル変数はサイクル間で状態を保持できる（同一スレッドのため競合なし） |
| スレッド間共有 | `check()` から kiro-loop の内部オブジェクト（`SessionManager` 等）にアクセスしないこと |

---

## 7. GitLab イシューフック実装例

> ファイル: `tools/kiro-loop/hooks/gitlab-issue-hook.py`

### 動作概要

1. `scripts/gl.py list-issues` でオープンイシューを取得
2. 前回実行時のイシュー ID セット（`~/.kiro/hooks/gitlab-issue-state.json`）と比較
3. 新規イシューがなければ `None` を返す（スキップ）
4. 新規イシューのラベルを確認し、対応するプロンプトを返す

### ラベル → プロンプトマッピング例

| ラベル | 送信するプロンプト |
|---|---|
| `priority:critical` | 緊急対応を促すプロンプト |
| `type:bug` | バグ修正を指示するプロンプト |
| `review:needed` | レビュー対応を指示するプロンプト |
| （その他）| 汎用の対応依頼プロンプト |

### コード

```python
#!/usr/bin/env python3
"""GitLab イシューポーリングフック

新規イシューが割り当てられたときのみ発火し、ラベルに応じてプロンプトを切り替える。
このスクリプトは kiro-loop の scheduler スレッド内で直接実行されるため、
ブロッキング処理は最小限にすること。
"""
import json
import subprocess
from pathlib import Path

_STATE_FILE = Path.home() / ".kiro" / "hooks" / "gitlab-issue-state.json"

_LABEL_PROMPTS: dict[str, str] = {
    "priority:critical": """\
緊急イシューが割り当てられました。最優先で対応してください。
イシュー詳細:
{issue_json}
""",
    "type:bug": """\
バグイシューが割り当てられました。再現手順を確認して修正してください。
イシュー詳細:
{issue_json}
""",
    "review:needed": """\
レビュー依頼イシューがあります。コードを確認してフィードバックしてください。
イシュー詳細:
{issue_json}
""",
}

_DEFAULT_PROMPT = """\
新しいイシューが割り当てられました。内容を確認して対応してください。
イシュー詳細:
{issue_json}
"""


def _get_issues() -> list[dict] | None:
    result = subprocess.run(
        ["python", "scripts/gl.py", "list-issues", "--state", "opened", "--json"],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _load_state() -> set[str]:
    if not _STATE_FILE.exists():
        return set()
    try:
        return set(json.loads(_STATE_FILE.read_text(encoding="utf-8")).get("issue_ids", []))
    except Exception:
        return set()


def _save_state(issue_ids: set[str]) -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(
        json.dumps({"issue_ids": list(issue_ids)}, ensure_ascii=False),
        encoding="utf-8",
    )


def _choose_prompt(issue: dict) -> str:
    labels: list[str] = issue.get("labels", [])
    issue_json = json.dumps(issue, ensure_ascii=False, indent=2)
    for label in labels:
        if label in _LABEL_PROMPTS:
            return _LABEL_PROMPTS[label].format(issue_json=issue_json)
    return _DEFAULT_PROMPT.format(issue_json=issue_json)


def check() -> str | None:
    """新規イシューがあればプロンプトを返す。なければ None を返してスキップ。"""
    issues = _get_issues()
    if issues is None:
        return None

    prev_ids = _load_state()
    curr_ids = {str(i["iid"]) for i in issues}
    new_issues = [i for i in issues if str(i["iid"]) not in prev_ids]

    _save_state(curr_ids)

    if not new_issues:
        return None

    return _choose_prompt(new_issues[0])
```

### 設定例

```yaml
# .kiro/kiro-loop.yml
prompts:
  - name: "GitLab Issue ワーカー"
    event_hook: ~/sandbox/tools/kiro-loop/hooks/gitlab-issue-hook.py
    poll_interval_seconds: 30
    enabled: true
```

---

## 8. 実装時の注意点

### `importlib` の副作用
`spec.loader.exec_module(module)` はモジュールのトップレベルコードを実行する。フックスクリプトのトップレベルには副作用のある処理を書かないこと（`check()` 内に閉じること）。

### `_queued_prompt` の永続性
現時点では `_queued_prompt` はインメモリのみ。kiro-loop 再起動時にキューは消える。永続化が必要な場合はフック側で状態ファイルに書き出すこと。

### `DESIGN.md` の更新
実装後は `tools/kiro-loop/DESIGN.md` の「新しいプロンプトオプションを追加する」セクションに `event_hook` / `poll_interval_seconds` を追記すること。
