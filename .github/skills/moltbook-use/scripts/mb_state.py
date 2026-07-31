#!/usr/bin/env python3
"""Reply-mode gate and autonomy governor for moltbook-use (設計書 §8).

返信トリガーは一律に reply を試行し、抑制可否はこの単一ゲートで判定する。

- モード: skill_configs.moltbook-use.reply_mode = active(既定) / quiet
          （環境変数 MOLTBOOK_REPLY_MODE が優先）。quiet は自律返信をブロック。
- 予算/クールダウン: state.json（{agent_home}/.moltbook/state.json）で日次管理。
  揮発しても安全（上限が一時的に緩むだけ。二重投稿は GitLab マーカーで別途防止）。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from moltbook_config import get_moltbook_home, get_skill_config  # noqa: E402

_DEFAULTS = {"reply_budget": 3, "thread_depth": 2, "author_cooldown_min": 30}
_DEFAULTS_CHECK = {"auto_check_cooldown_hours": 24}


def reply_policy() -> dict:
    cfg = get_skill_config()
    mode = (os.environ.get("MOLTBOOK_REPLY_MODE") or cfg.get("reply_mode") or "active").lower()
    return {
        "mode": mode,
        "budget": int(cfg.get("reply_budget", _DEFAULTS["reply_budget"])),
        "depth": int(cfg.get("thread_depth", _DEFAULTS["thread_depth"])),
        "cooldown_min": int(cfg.get("author_cooldown_min", _DEFAULTS["author_cooldown_min"])),
    }


def _state_path() -> Path:
    return Path(get_moltbook_home()) / "state.json"


def load_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _today() -> str:
    return _dt.date.today().isoformat()


def _rolled(state: dict) -> dict:
    """日付が変わっていたら日次カウンタをリセットした state を返す。"""
    if state.get("date") != _today():
        return {"date": _today(), "replies_today": 0, "thread": {}, "cooldown": state.get("cooldown", {})}
    return state


def can_reply(iid: int, author: str, *, autonomous: bool, skip_cooldown: bool = False) -> tuple[bool, str]:
    """自律返信の可否を判定する。手動（人間指示）の返信は常に許可。

    skip_cooldown=True のときは著者クールダウンを免除する（例: ltm/wiki 保存を
    トリガーにした機会的返信）。quiet・予算・スレッド深さのゲートは維持する。
    """
    if not autonomous:
        return True, "manual"
    pol = reply_policy()
    if pol["mode"] == "quiet":
        return False, "reply_mode=quiet"
    state = _rolled(load_state())
    if state.get("replies_today", 0) >= pol["budget"]:
        return False, f"reply_budget({pol['budget']})"
    if state.get("thread", {}).get(str(iid), 0) >= pol["depth"]:
        return False, f"thread_depth({pol['depth']})"
    if not skip_cooldown:
        cd = (state.get("cooldown") or {}).get(author)
        if cd:
            try:
                last = _dt.datetime.fromisoformat(cd)
                if (_dt.datetime.now() - last).total_seconds() < pol["cooldown_min"] * 60:
                    return False, f"author_cooldown({pol['cooldown_min']}m)"
            except ValueError:
                pass
    return True, "ok"


def record_reply(iid: int, author: str, *, skip_cooldown: bool = False) -> None:
    state = _rolled(load_state())
    state["replies_today"] = state.get("replies_today", 0) + 1
    state.setdefault("thread", {})[str(iid)] = state.get("thread", {}).get(str(iid), 0) + 1
    if not skip_cooldown:
        state.setdefault("cooldown", {})[author] = _dt.datetime.now().isoformat()
    save_state(state)


def check_policy() -> dict:
    cfg = get_skill_config()
    return {
        "cooldown_hours": int(cfg.get("auto_check_cooldown_hours", _DEFAULTS_CHECK["auto_check_cooldown_hours"])),
    }


def can_auto_check() -> tuple[bool, str]:
    """定期自律チェックの可否を判定する（N時間クールダウン）。"""
    pol = check_policy()
    state = load_state()
    last = state.get("last_auto_checked_at")
    if not last:
        return True, "初回"
    try:
        last_dt = _dt.datetime.fromisoformat(last)
        elapsed_hours = (_dt.datetime.now() - last_dt).total_seconds() / 3600
        if elapsed_hours < pol["cooldown_hours"]:
            remaining = pol["cooldown_hours"] - elapsed_hours
            return False, f"cooldown({pol['cooldown_hours']}h, 残 {remaining:.1f}h)"
    except ValueError:
        pass
    return True, "ok"


def record_auto_check() -> None:
    """定期自律チェック実行時刻を state.json に記録する。"""
    state = _rolled(load_state())
    state["last_auto_checked_at"] = _dt.datetime.now().isoformat()
    save_state(state)


if __name__ == "__main__":
    pol = reply_policy()
    print(f"reply_mode : {pol['mode']}")
    print(f"budget     : {pol['budget']}/session, depth {pol['depth']}, cooldown {pol['cooldown_min']}m")
    print(f"state      : {_state_path()}")
    print(f"current    : {json.dumps(_rolled(load_state()), ensure_ascii=False)}")
