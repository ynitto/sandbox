# -*- coding: utf-8 -*-
"""開発木から `python3 -m gitlab_repo_sync` で動かすための入口。

配布物（zipapp）の入口は install.sh が生成するルートの __main__.py。こちらは
tools/gitlab-repo-sync/ で直接動かすとき用で、両者は独立している。
"""
from . import main

if __name__ == "__main__":
    raise SystemExit(main())
