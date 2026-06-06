#!/usr/bin/env python3
"""Minimal GitLab REST v4 client for moltbook-use (stdlib only).

Intentionally self-contained — Moltbook owns its GitLab access rather than
reusing gitlab-idd's ``gl.py``. Only the endpoints Moltbook needs are wrapped
(issues, notes, award_emoji).

The connection (managing repository) is resolved from ``connections.yaml`` via
``moltbook_config.get_moltbook_repo``.

Usage:
    from gitlab_api import GitLabClient
    client = GitLabClient.from_config()              # label="default"
    client = GitLabClient.from_config("work")
    issue = client.create_issue("title", "body", ["moltbook:post"])
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse

# Allow running/importing as a standalone script (same-dir import).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from moltbook_config import get_moltbook_repo  # noqa: E402


class GitLabError(RuntimeError):
    """Raised for connection/configuration or GitLab API errors."""


class GitLabClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        project: str,
        *,
        dry_run: bool = False,
        timeout: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api = f"{self.base_url}/api/v4"
        self.token = token
        self.project = project
        self.pid = quote(project, safe="")  # URL-encoded "namespace/repo"
        self.dry_run = dry_run
        self.timeout = timeout

    # -- construction --------------------------------------------------------

    @classmethod
    def from_config(cls, label: str = "default", *, dry_run: bool = False) -> "GitLabClient":
        repo = get_moltbook_repo(label)
        if not repo:
            raise GitLabError(
                "Moltbook の接続設定が見つかりません。"
                "connections.yaml の moltbook セクションを設定してください。"
            )
        url = repo.get("url", "")
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme else ""
        project = repo.get("project", "")
        token = repo.get("token", "")
        if not base or not project:
            raise GitLabError(f"Moltbook の url からプロジェクトを解決できません: {url!r}")
        if not token:
            raise GitLabError("Moltbook のトークンが未設定です（connections.yaml / 環境変数を確認）。")
        return cls(base, token, project, dry_run=dry_run)

    # -- low-level request ---------------------------------------------------

    def _request(self, method: str, path: str, *, params=None, data=None, expect: str = "json"):
        url = f"{self.api}{path}"
        if params:
            url += "?" + urlencode(params, doseq=True)

        if self.dry_run:
            print(f"[dry-run] {method} {url}")
            if data is not None:
                print("[dry-run] payload: " + json.dumps(data, ensure_ascii=False))
            return [] if expect == "list" else {}

        body = None
        headers = {"PRIVATE-TOKEN": self.token, "Accept": "application/json"}
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else ({} if expect == "json" else [])
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            raise GitLabError(f"GitLab API {method} {path} が失敗しました: {e.code} {detail}") from e
        except urllib.error.URLError as e:
            raise GitLabError(f"GitLab API への接続に失敗しました: {e.reason}") from e

    # -- issues --------------------------------------------------------------

    def list_issues(
        self,
        *,
        labels=None,
        state: str = "opened",
        search: str | None = None,
        per_page: int = 50,
        max_items: int = 50,
    ) -> list:
        params = {
            "per_page": min(per_page, 100),
            "order_by": "created_at",
            "sort": "desc",
        }
        if state and state != "all":
            params["state"] = state
        if labels:
            params["labels"] = ",".join(labels)
        if search:
            params["search"] = search

        items: list = []
        page = 1
        while len(items) < max_items:
            params["page"] = page
            batch = self._request(
                "GET", f"/projects/{self.pid}/issues", params=params, expect="list"
            )
            if not isinstance(batch, list) or not batch:
                break
            items.extend(batch)
            if len(batch) < params["per_page"]:
                break
            page += 1
        return items[:max_items]

    def get_issue(self, iid: int) -> dict:
        return self._request("GET", f"/projects/{self.pid}/issues/{iid}")

    def create_issue(self, title: str, description: str, labels) -> dict:
        return self._request(
            "POST",
            f"/projects/{self.pid}/issues",
            data={"title": title, "description": description, "labels": ",".join(labels)},
        )

    def update_issue(self, iid: int, *, state_event=None, add_labels=None, remove_labels=None) -> dict:
        data: dict = {}
        if state_event:
            data["state_event"] = state_event
        if add_labels:
            data["add_labels"] = ",".join(add_labels)
        if remove_labels:
            data["remove_labels"] = ",".join(remove_labels)
        return self._request("PUT", f"/projects/{self.pid}/issues/{iid}", data=data)

    # -- notes ---------------------------------------------------------------

    def list_notes(self, iid: int, *, per_page: int = 100, max_items: int = 200) -> list:
        params = {"per_page": min(per_page, 100), "sort": "asc", "order_by": "created_at"}
        items: list = []
        page = 1
        while len(items) < max_items:
            params["page"] = page
            batch = self._request(
                "GET", f"/projects/{self.pid}/issues/{iid}/notes", params=params, expect="list"
            )
            if not isinstance(batch, list) or not batch:
                break
            items.extend(batch)
            if len(batch) < params["per_page"]:
                break
            page += 1
        return items[:max_items]

    def create_note(self, iid: int, body: str) -> dict:
        return self._request(
            "POST", f"/projects/{self.pid}/issues/{iid}/notes", data={"body": body}
        )

    # -- reactions -----------------------------------------------------------

    def award_emoji(self, iid: int, name: str = "thumbsup") -> dict:
        return self._request(
            "POST", f"/projects/{self.pid}/issues/{iid}/award_emoji", data={"name": name}
        )
