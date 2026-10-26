#!/usr/bin/env python3
"""GitLab の検索が日本語の投稿に使えるかを、実際のインスタンスで確かめる（読み取りのみ）。

  set GITLAB_URL=https://gitlab.example.com
  set GITLAB_TOKEN=glpat-...
  python gitlab_search_probe.py --project agents/moltbook --phrase "日本語が文字化け"

--phrase には、Issue の本文と knowledge/*.md の両方に実在する日本語の文を渡す。
全文・前半・後半・2 文字・3 文字・助詞を挟んだ変形で、Issue・ファイル本文・コメントを
検索してヒット件数を表にする。何も書き込まない。

検索 API には利用者ごとの回数制限（既定で毎分 30 回）がある。HTTP 429 が返ったら
Retry-After の秒数だけ待って続ける。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def get(url: str, token: str, path: str, params: dict | None = None):
    q = ("?" + urllib.parse.urlencode(params)) if params else ""
    req = urllib.request.Request(f"{url}/api/v4{path}{q}", headers={"PRIVATE-TOKEN": token})
    for _ in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(int(e.headers.get("Retry-After") or 60))
                continue
            return e.code, e.read().decode("utf-8", "replace")[:120]
    return 429, "rate limited"


def probes(phrase: str) -> list[tuple[str, str]]:
    n = len(phrase)
    out = [("全文", phrase), ("前半", phrase[: n // 2]), ("後半", phrase[n // 2:]),
           ("先頭2文字", phrase[:2]), ("先頭3文字", phrase[:3])]
    if n >= 4:
        out.append(("語間に「の」", phrase[:2] + "の" + phrase[2:]))
    out.append(("空白区切り", phrase[:3] + " " + phrase[-3:]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="namespace/repo")
    ap.add_argument("--phrase", required=True)
    a = ap.parse_args()
    url, token = os.environ.get("GITLAB_URL", "").rstrip("/"), os.environ.get("GITLAB_TOKEN", "")
    if not url or not token:
        print("GITLAB_URL と GITLAB_TOKEN を設定してください", file=sys.stderr)
        return 2

    _, ver = get(url, token, "/version")
    _, meta = get(url, token, "/metadata")
    print(f"version : {ver.get('version') if isinstance(ver, dict) else ver}")
    print(f"edition : {'EE' if isinstance(meta, dict) and meta.get('enterprise') else 'CE または不明'}")
    pid = urllib.parse.quote(a.project, safe="")

    print(f"\n{'probe':<12} {'query':<20} {'issues':>8} {'blobs':>8} {'notes':>8}")
    for label, q in probes(a.phrase):
        cells = []
        for scope in ("issues", "blobs", "notes"):
            st, body = get(url, token, f"/projects/{pid}/search", {"scope": scope, "search": q, "per_page": 20})
            cells.append(str(len(body)) if st == 200 and isinstance(body, list) else f"HTTP{st}")
        print(f"{label:<12} {q:<20} " + " ".join(f"{c:>8}" for c in cells))
    print("\n読み方: 先頭2文字の issues が 0 なら、Issue 検索は 3 字未満の語を無視している。"
          "\n語間に「の」の行が 0 なら、部分一致だけで語の区切りは見ていない（キーワード行が要る）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
