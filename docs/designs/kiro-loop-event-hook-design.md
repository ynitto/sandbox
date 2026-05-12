# kiro-loop イベントフック拡張 設計案

> 作成日: 2026-05-12  
> 対象ファイル: `tools/kiro-loop/kiro-loop.py`

---

## 1. 背景・目的

現在の kiro-loop に以下 2 つの機能を追加する。

| 機能 | 概要 |
|---|---|
| **event_hook** | `interval_minutes`/`cron` スケジュールに乗せて、タイミング・送信内容を Python スクリプトで制御する |
| **キューイング** | セマフォ上限到達時にプロンプトを破棄せず保持し、同一ペインへの重複送信を防ぐ |

---

## 2. 設計方針

- **`interval_minutes`/`cron` と共存**: スケジュールが発火したタイミングでフックを呼ぶ。スケジュールを廃止しない
- **インプロセス実行**: `importlib` 経由でロード、`check()` を scheduler スレッド内で直接呼ぶ（subprocess 不使用）
- **同一ペインへの重複防止**: セマフォ取得失敗時はプロンプトを `_queued_prompt` に保持。次サイクルでキュー優先、スケジュール発火を抑制する
- **既存コードへの変更ゼロ**: `_run_loop` にフック呼び出しとキュー処理を **挿入するだけ**。既存メソッドは変更しない

---

## 3. event_hook フックスクリプトのインターフェース

### 呼び出しタイミング

`interval_minutes`/`cron` でスケジュールが発火し、かつ `_queued_prompt` が空のとき (`ensure_session` の直前)。

### 関数シグネチャ

```python
def check() -> str | None:
    """
    スケジュール発火のたびに scheduler スレッドから呼ばれる。

    Returns:
        str  : kiro-cli に送信するプロンプトテキスト（YAML の prompt を上書き）
        None : このサイクルをスキップ（何も送らない）
    """
    ...
```

- 引数なし。フック内の module-level 変数で状態を保持できる（同スレッドのため競合なし）
- `check` 関数が存在しない場合は WARNING を出してスキップ

### モジュールキャッシュ

`mtime` を監視し、変更時のみ再ロード。未変更時はキャッシュを再利用。

---

## 4. 設定スキーマ変更

```yaml
prompts:
  - name: "GitLab Issue ワーカー"
    prompt: |
      （省略可。check() が str を返した場合はそちらを優先）
    event_hook: ~/.kiro/hooks/gitlab-issue-hook.py   # ← 新規フィールド
    interval_minutes: 5    # スケジュールはそのまま残す
    enabled: true
```

`event_hook` は省略可。省略時は従来通り YAML の `prompt` をそのまま送信。

---

## 5. 発火フロー（変更後）

```
_run_loop (1秒ポーリング)
│
├─ _queued_prompt あり?
│   Yes → _try_drain_queued()  ← セマフォ再試行のみ（スケジュール/フック呼ばない）
│          成功: _queued_prompt クリア、next_run_at = now + interval
│          失敗: _queued_prompt 保持、何もしない
│   No  → 続行
│
├─ now < next_run_at?  Yes → スキップ
│
├─ event_hook あり?
│   Yes → _call_hook_check() を呼ぶ
│          → None:  next_run_at を更新してスキップ
│          → str:   entry["prompt"] をそのテキストで上書き
│   No  → entry["prompt"] をそのまま使用
│
├─ ensure_session()  失敗 → スキップ（再起動は session-monitor が担当）
│
├─ semaphore あり?
│   Yes → _acquire_slot()
│          成功: 続行
│          失敗: _queued_prompt = entry["prompt"] を保存  ← NEW（現在は破棄）
│               continue  （_acquire_slot が next_run_at を更新済み）
│   No  → 続行
│
├─ _dispatch_prompt()
│
└─ next_run_at を更新
```

---

## 6. コード変更範囲

### 6.1 `_set_entries()` — 2行追加

```python
normalized.append({
    # ... 既存フィールド（変更なし） ...
    "event_hook": str(entry.get("event_hook", "")).strip() or None,  # +1行
    "_queued_prompt": None,                                            # +1行
})
```

---

### 6.2 新規メソッド 3本

#### `_load_hook_module(hook_path: Path) -> Any | None` — ~20行

```python
def _load_hook_module(self, hook_path: Path) -> Any | None:
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

`PeriodicScheduler.__init__` に `self._hook_cache: dict[str, tuple[float, Any]] = {}` を追加（+1行）。

---

#### `_call_hook_check(entry: dict) -> str | None` — ~20行

```python
def _call_hook_check(self, entry: dict[str, Any]) -> str | None:
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
        log.error("[%s] check() でエラーが発生しました: %s", name, exc, exc_info=True)
        return None
```

---

#### `_try_drain_queued(entry: dict, now: float) -> None` — ~30行

キュー済みプロンプトをセマフォ込みで送信試行する。`_acquire_slot` を呼ばず、インラインでスロット判定のみ行う（`next_run_at` を更新しないため）。

```python
def _try_drain_queued(self, entry: dict[str, Any], now: float) -> None:
    prompt_id = str(entry["id"])
    name = str(entry.get("name", ""))
    queued_prompt = str(entry["_queued_prompt"])
    exclude = bool(entry.get("exclude_from_concurrency", False))

    if not self._session_mgr.ensure_session(prompt_id, name):
        return  # セッション未準備、保持

    pane_id: str | None = None
    if self._semaphore is not None and not exclude:
        pane_id = self._session_mgr.get_pane_id(prompt_id)
        if pane_id:
            elapsed = self._semaphore.slot_elapsed(pane_id)
            if elapsed is not None:
                if elapsed < self._semaphore.slot_timeout:
                    return  # まだ処理中、保持
                # タイムアウト超過: 強制解放して続行
                if self._slot_monitor is not None:
                    self._slot_monitor.untrack(pane_id)
                self._semaphore.release(pane_id)
            if self._semaphore.cooldown_remaining(pane_id) > 0:
                return  # クールダウン中、保持
            if not self._semaphore.acquire(pane_id):
                return  # グローバル上限、保持

    dispatch_entry = dict(entry)
    dispatch_entry["prompt"] = queued_prompt
    self._dispatch_prompt(dispatch_entry, pane_id)
    self._update_entry(prompt_id,
                       _queued_prompt=None,
                       next_run_at=self._next_run_at_for_entry(entry))
```

---

### 6.3 `_run_loop()` — 3箇所に合計 13行挿入

既存コードは **1行も変更しない**。以下の 3 箇所に行を挿入する。

#### 挿入箇所 ①：`enabled` チェックの直後（キュー処理）

```python
for entry in entries:
    if not entry.get("enabled", True):
        continue

    # ---- ① キュー処理（+5行） ----
    if entry.get("_queued_prompt"):
        self._try_drain_queued(entry, now)
        continue
    # ---- ここまで ----

    if now < float(entry.get("next_run_at", now)):   # ← 既存行（変更なし）
        continue
```

#### 挿入箇所 ②：`ensure_session` 呼び出しの直前（フック呼び出し）

```python
    entry["_should_clear"] = should_clear   # ← 既存行（変更なし）

    # ---- ② event_hook 呼び出し（+7行） ----
    if entry.get("event_hook"):
        prompt_text = self._call_hook_check(entry)
        if prompt_text is None:
            self._update_entry(prompt_id, next_run_at=self._next_run_at_for_entry(entry))
            continue
        entry = dict(entry)
        entry["prompt"] = prompt_text
    # ---- ここまで ----

    if not self._session_mgr.ensure_session(prompt_id, name):   # ← 既存行（変更なし）
```

#### 挿入箇所 ③：`_acquire_slot` が False を返したときのキュー保存（+1行）

```python
    if pane_id and not self._acquire_slot(entry, pane_id):   # ← 既存行（変更なし）
        self._update_entry(prompt_id, _queued_prompt=entry.get("prompt"))  # +1行
        continue                                                             # ← 既存行（変更なし）
```

---

### 変更量サマリ

| ファイル | 変更種別 | 追加行数 | 変更行数 |
|---|---|---|---|
| `kiro-loop.py` | `_set_entries` に 2 行追加 | +2 | 0 |
| `kiro-loop.py` | `PeriodicScheduler.__init__` に `_hook_cache` 追加 | +1 | 0 |
| `kiro-loop.py` | `_load_hook_module` 新規メソッド | +20 | 0 |
| `kiro-loop.py` | `_call_hook_check` 新規メソッド | +20 | 0 |
| `kiro-loop.py` | `_try_drain_queued` 新規メソッド | +30 | 0 |
| `kiro-loop.py` | `_run_loop` に 13 行挿入 | +13 | 0 |
| `kiro-loop.yaml.example` | `event_hook` オプション追記 | +12 | 0 |
| `hooks/gitlab-issue-hook.py` | 新規フック例 | +70 | — |

**既存メソッドへの変更行数: 0**

---

## 7. キューイング挙動の詳細

### 重複防止の仕組み

```
T=0:   スケジュール発火 → スロット上限 → _queued_prompt = "prompt A" に保存
                                          _acquire_slot が next_run_at = T+5min に更新

T=1s:  _queued_prompt あり → _try_drain_queued → まだ上限 → 保持（スケジュール触らない）

T=5min: next_run_at 到達するが _queued_prompt あり → スケジュール発火せずキュードレインを試みる
        スロット空き → "prompt A" を送信、_queued_prompt = None
        next_run_at = T+5min + 5min に更新

T=10min: 通常スケジュール発火
```

### キューは 1 エントリあたり最大 1 件

新しいスケジュール発火時に `_queued_prompt` が存在する場合、キュードレインを優先し新しいプロンプトは生成しない。  
これにより同一ペインへの多重送信を防ぐ。

### インメモリのみ

`_queued_prompt` はプロセスメモリ内のみ。kiro-loop 再起動時にキューは消える。再起動後は次のスケジュール発火から再開する。

---

## 8. GitLab イシューフック実装例

> ファイル: `tools/kiro-loop/hooks/gitlab-issue-hook.py`

新規イシューが割り当てられたときのみ発火し、ラベルに応じてプロンプトを切り替える。

```python
#!/usr/bin/env python3
"""GitLab イシューポーリングフック（kiro-loop scheduler スレッド内で実行される）"""
import json
import subprocess
from pathlib import Path

_STATE_FILE = Path.home() / ".kiro" / "hooks" / "gitlab-issue-state.json"

_LABEL_PROMPTS: dict[str, str] = {
    "priority:critical": "緊急イシューが割り当てられました。最優先で対応してください。\n\n{issue_json}",
    "type:bug":          "バグイシューが割り当てられました。再現手順を確認して修正してください。\n\n{issue_json}",
    "review:needed":     "レビュー依頼イシューがあります。コードを確認してフィードバックしてください。\n\n{issue_json}",
}
_DEFAULT_PROMPT = "新しいイシューが割り当てられました。内容を確認して対応してください。\n\n{issue_json}"


def _get_issues() -> list[dict] | None:
    r = subprocess.run(
        ["python", "scripts/gl.py", "list-issues", "--state", "opened", "--json"],
        capture_output=True, text=True, timeout=15,
    )
    try:
        return json.loads(r.stdout) if r.returncode == 0 else None
    except json.JSONDecodeError:
        return None


def _load_state() -> set[str]:
    try:
        return set(json.loads(_STATE_FILE.read_text(encoding="utf-8")).get("issue_ids", []))
    except Exception:
        return set()


def _save_state(ids: set[str]) -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps({"issue_ids": list(ids)}, ensure_ascii=False), encoding="utf-8")


def check() -> str | None:
    issues = _get_issues()
    if issues is None:
        return None

    prev_ids = _load_state()
    curr_ids = {str(i["iid"]) for i in issues}
    new_issues = [i for i in issues if str(i["iid"]) not in prev_ids]
    _save_state(curr_ids)

    if not new_issues:
        return None

    issue = new_issues[0]
    issue_json = json.dumps(issue, ensure_ascii=False, indent=2)
    for label in issue.get("labels", []):
        if label in _LABEL_PROMPTS:
            return _LABEL_PROMPTS[label].format(issue_json=issue_json)
    return _DEFAULT_PROMPT.format(issue_json=issue_json)
```

### 設定例

```yaml
# .kiro/kiro-loop.yml
prompts:
  - name: "GitLab Issue ワーカー"
    event_hook: ~/sandbox/tools/kiro-loop/hooks/gitlab-issue-hook.py
    interval_minutes: 5
    enabled: true
```

---

## 9. 実装時の注意点

**`check()` のブロッキング**  
scheduler スレッドで実行されるため、長時間ブロックすると他エントリのスケジュールが遅延する。ネットワーク呼び出しには短い timeout を設定すること（上記例では 15 秒）。

**`importlib` のトップレベル副作用**  
`spec.loader.exec_module` はモジュールのトップレベルコードを実行する。副作用のある処理は `check()` 内に閉じること。

**`DESIGN.md` の更新**  
実装後は `tools/kiro-loop/DESIGN.md` の「新しいプロンプトオプションを追加する」セクションに `event_hook` と `_queued_prompt` を追記すること。
