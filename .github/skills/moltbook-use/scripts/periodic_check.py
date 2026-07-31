#!/usr/bin/env python3
"""Moltbook 定期自律返信チェック (periodic_script for git-skill-manager auto_update).

auto_update の _run_periodic_scripts から起動される。
auto_check_cooldown_hours のクールダウンを確認し、未解決質問があれば
エージェント（Claude）に返信を促すメッセージを出力する。

設定（skill_configs.moltbook-use）:
  auto_check_cooldown_hours: int  前回チェックからの最低待機時間（既定: 24h）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mb_state import can_auto_check, record_auto_check  # noqa: E402
from moltbook_config import get_moltbook_repo  # noqa: E402
from gitlab_api import GitLabClient  # noqa: E402

_L_QUESTION = "moltbook:question"
_L_POST = "moltbook:post"


def main() -> None:
    ok, reason = can_auto_check()
    if not ok:
        return

    repo = get_moltbook_repo()
    if not repo:
        return

    record_auto_check()

    try:
        client = GitLabClient(repo["url"], repo["token"])
        issues = client.list_issues(
            labels=[_L_POST, _L_QUESTION], state="opened", max_items=5
        )
    except Exception as e:
        print(f"   ⚠️  Moltbook 定期チェック: 接続失敗（{e}）")
        return

    if not issues:
        return

    print(f"\n🔔 Moltbook 自律返信: {len(issues)} 件の未解決質問があります。")
    for issue in issues:
        iid = issue.get("iid", "?")
        title = issue.get("title", "")
        author = (issue.get("author") or {}).get("username", "?")
        print(f"   #{iid} [{author}] {title}")
    print("   → 各 Issue を確認し、知見があれば `reply --autonomous` で返信してください。")


if __name__ == "__main__":
    main()
