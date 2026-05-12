# kiro-loop イベントフック拡張 設計案

> 作成日: 2026-05-12  
> 対象ファイル: `tools/kiro-loop/kiro-loop.py`

---

## 1. 背景・目的

現在の kiro-loop はスケジュール（`interval_minutes` / `cron`）に従って固定のプロンプトを送信する。  
これを拡張し、**プロンプト単位に「発動を制御する Python スクリプト」を設定できる**ようにしたい。

主なユースケース:
- GitLab イシューをポーリングし、変化があったときだけ送信。さらにラベルに応じてプロンプトを切り替える
- 外部 API や状態ファイルを参照して送信要否・送信内容を動的に決定する
- 時刻ベーススケジュールに「追加条件」を重ねる（例: 「5分ごとに確認するが、CI が通っているときのみ」）

---

## 2. 設計方針

- **変更範囲を最小化する**: 既存のスケジューリング・セマフォ・セッション管理ロジックは一切触らない
- **後付け拡張**: `event_hook` が未設定のエントリは従来通り動作（完全な後方互換性）
- **プロセス分離**: フックは `subprocess.run` で起動し、クラッシュしても kiro-loop 本体に影響しない
- **シンプルな I/F**: exit コード + stdout だけでフックの意思を伝える

---

## 3. フックスクリプトのインターフェース

### 呼び出しタイミング

スケジューラが「次回実行時刻に到達」した直後、かつ `ensure_session` / スロット取得 の**前**に呼び出す。  
フックがスキップを指示した場合はセッション生成もスロット消費も発生しない。

### 呼び出し方法

```
python /path/to/hook.py
```

フックスクリプトは任意の Python スクリプトとして実行される（`sys.executable` 経由）。

### 環境変数（フックへの入力）

| 変数名 | 内容 |
|---|---|
| `KIRO_LOOP_PROMPT_NAME` | エントリの `name` フィールド |
| `KIRO_LOOP_PROMPT_TEXT` | YAML に設定されたデフォルトプロンプト |

### 終了コード・標準出力（フックからの出力）

| exit コード | stdout | 動作 |
|---|---|---|
| `0` | 文字列あり | **stdout の内容**をプロンプトとして送信 |
| `0` | 空 | **デフォルトプロンプト**（`KIRO_LOOP_PROMPT_TEXT`）を送信 |
| 非 `0` | 任意 | このサイクルをスキップ（プロンプト送信なし）。次の `next_run_at` まで待機 |

---

## 4. 設定スキーマ変更

### `kiro-loop.yaml` / `.kiro/kiro-loop.yml` のプロンプトエントリ

```yaml
prompts:
  - name: "GitLab Issue ワーカー"
    prompt: |
      自分にアサインされたイシューを1件取得して対応してください。
    event_hook: ~/.kiro/hooks/gitlab-issue-hook.py   # ← 新規フィールド
    interval_minutes: 5
    enabled: true
```

`event_hook` は省略可。`~` 展開に対応。

---

## 5. コード変更範囲

### 5.1 `_set_entries()` — 1行追加

`normalized.append({...})` の辞書に `event_hook` フィールドを追加するだけ。

```python
# 変更前（抜粋）
normalized.append({
    "id": prompt_id,
    "name": name,
    "prompt": prompt,
    # ...
})

# 変更後
normalized.append({
    "id": prompt_id,
    "name": name,
    "prompt": prompt,
    # ...
    "event_hook": str(entry.get("event_hook", "")).strip() or None,  # ← +1行
})
```

### 5.2 `_call_event_hook()` — 新規メソッド追加（約35行）

`PeriodicScheduler` クラスに追加。既存メソッドへの変更なし。

```python
def _call_event_hook(self, entry: dict[str, Any]) -> str | None:
    """event_hook スクリプトを実行し、送信するプロンプトテキストを返す。

    Returns:
        str  : 送信するプロンプト（元プロンプトまたはフック出力）
        None : このサイクルをスキップ
    """
    hook_raw = entry.get("event_hook")
    if not hook_raw:
        return entry.get("prompt", "")

    hook_path = Path(os.path.expanduser(str(hook_raw))).resolve()
    name = str(entry.get("name", ""))

    if not hook_path.exists():
        log.warning("[%s] event_hook が見つかりません: %s", name, hook_path)
        return None

    env = {
        **os.environ,
        "KIRO_LOOP_PROMPT_NAME": name,
        "KIRO_LOOP_PROMPT_TEXT": str(entry.get("prompt", "")),
    }

    try:
        result = subprocess.run(
            [sys.executable, str(hook_path)],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
    except subprocess.TimeoutExpired:
        log.warning("[%s] event_hook がタイムアウトしました (30s)。スキップします。", name)
        return None
    except Exception as exc:
        log.error("[%s] event_hook 実行エラー: %s", name, exc)
        return None

    if result.returncode != 0:
        log.debug("[%s] event_hook がスキップを指示しました (exit %d)。", name, result.returncode)
        return None

    output = result.stdout.strip()
    return output if output else str(entry.get("prompt", ""))
```

### 5.3 `_run_loop()` — 8行追加

`ensure_session` 呼び出しの直前に挿入。既存の行を移動・削除しない。

```python
# ---- 変更前の該当部分 ----
if not self._session_mgr.ensure_session(prompt_id, name):
    log.warning(...)
else:
    pane_id = ...
    self._dispatch_prompt(entry, pane_id)

self._update_entry(str(entry.get("id", "")), next_run_at=...)

# ---- 変更後 ----
# event_hook 呼び出し（+8行）
prompt_text = self._call_event_hook(entry)
if prompt_text is None:
    self._update_entry(str(entry.get("id", "")), next_run_at=self._next_run_at_for_entry(entry))
    continue
if prompt_text != entry.get("prompt", ""):
    entry = dict(entry)          # コピーして元エントリを汚さない
    entry["prompt"] = prompt_text

if not self._session_mgr.ensure_session(prompt_id, name):   # ← 既存行（移動なし）
    log.warning(...)
else:
    pane_id = ...
    self._dispatch_prompt(entry, pane_id)

self._update_entry(str(entry.get("id", "")), next_run_at=...)
```

### 変更量サマリ

| ファイル | 変更種別 | 追加行数 | 変更行数 |
|---|---|---|---|
| `tools/kiro-loop/kiro-loop.py` | `_set_entries` に1行追加 | +1 | 0 |
| `tools/kiro-loop/kiro-loop.py` | `_call_event_hook` メソッド新規追加 | +35 | 0 |
| `tools/kiro-loop/kiro-loop.py` | `_run_loop` に8行挿入 | +8 | 0 |
| `tools/kiro-loop/kiro-loop.yaml.example` | `event_hook` オプション追記 | +15 | 0 |
| `tools/kiro-loop/hooks/gitlab-issue-hook.py` | 新規フック例 | +80 | — |

**既存メソッドへの変更行数: 0**

---

## 6. GitLab イシューフック実装例

> ファイル: `tools/kiro-loop/hooks/gitlab-issue-hook.py`

### 動作概要

1. `scripts/gl.py list-issues` でオープンイシューを取得
2. 前回実行時のイシュー ID セット（`~/.kiro/hooks/gitlab-issue-state.json`）と比較
3. 新規イシューがなければ `exit 1`（スキップ）
4. 新規イシューのラベルを確認し、対応するプロンプトを stdout に出力して `exit 0`

### ラベル → プロンプトマッピング例

| ラベル | 送信するプロンプト |
|---|---|
| `priority:critical` | 緊急対応を促すプロンプト |
| `type:bug` | バグ修正を指示するプロンプト |
| `review:needed` | レビュー対応を指示するプロンプト |
| （その他）| デフォルトプロンプト（YAML 設定値） |

### コード

```python
#!/usr/bin/env python3
"""GitLab イシューポーリングフック

新規イシューが割り当てられたときのみ発火し、ラベルに応じてプロンプトを切り替える。
"""
import json
import os
import subprocess
import sys
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
        capture_output=True, text=True, timeout=30,
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


def main() -> None:
    issues = _get_issues()
    if issues is None:
        print("イシュー取得失敗", file=sys.stderr)
        sys.exit(1)

    prev_ids = _load_state()
    curr_ids = {str(i["iid"]) for i in issues}
    new_issues = [i for i in issues if str(i["iid"]) not in prev_ids]

    _save_state(curr_ids)

    if not new_issues:
        sys.exit(1)  # 変化なし → スキップ

    print(_choose_prompt(new_issues[0]))  # 最初の新規イシューのプロンプトを出力


if __name__ == "__main__":
    main()
```

### 設定例

```yaml
# .kiro/kiro-loop.yml
prompts:
  - name: "GitLab Issue ワーカー"
    prompt: |
      自分にアサインされたイシューを1件取得して対応してください。
    event_hook: ~/sandbox/tools/kiro-loop/hooks/gitlab-issue-hook.py
    interval_minutes: 5
    enabled: true
```

---

## 7. 実装時の注意点

### スレッド安全性
`_call_event_hook` は `_run_loop` スレッドから呼ばれる。`entry` は既にコピー済みのため、他スレッドとの共有はない。

### タイムアウト
フックのデフォルトタイムアウトは 30 秒。外部 API を呼ぶフックでは適切な timeout を設定すること。`interval_minutes: 5` のエントリに 30 秒フックを付けても問題ない（スケジューラのポーリング間隔は 1 秒）。

### エラー時の挙動
フックが例外・タイムアウト・`exit 非0` のいずれの場合も **スキップ扱い**（プロンプトを送らない）。フックが壊れていても kiro-loop 本体は停止しない。

### フック不在
`event_hook` に指定したファイルが存在しない場合は `WARNING` を出してスキップ。設定ミスを見逃さないようにログに残す。

### `DESIGN.md` の更新
実装後は `tools/kiro-loop/DESIGN.md` の「新しいプロンプトオプションを追加する」セクションに `event_hook` を追記すること。
