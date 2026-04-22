"""Common Microsoft Graph API authentication.

Priority:
  1. Azure CLI session (az account get-access-token) — no setup required
  2. MSAL device code flow with Microsoft's public client ID — browser auth required once
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import msal

# Microsoft Graph PowerShell SDK の公開 Client ID（アプリ登録不要）
_PUBLIC_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
_AUTHORITY = "https://login.microsoftonline.com/common"
_CACHE_PATH = Path.home() / ".teams_graph_cache.json"


def _try_azure_cli() -> str | None:
    """Return a Graph API token via Azure CLI, or None if unavailable/not logged in."""
    try:
        result = subprocess.run(
            [
                "az", "account", "get-access-token",
                "--resource", "https://graph.microsoft.com",
                "--query", "accessToken",
                "--output", "tsv",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            token = result.stdout.strip()
            if token:
                print("Azure CLI セッションで認証しました。", file=sys.stderr)
                return token
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _msal_device_flow(scopes: list[str]) -> str:
    """Return a token via MSAL device code flow using Microsoft's public client ID."""
    cache = msal.SerializableTokenCache()
    if _CACHE_PATH.exists():
        cache.deserialize(_CACHE_PATH.read_text(encoding="utf-8"))

    app = msal.PublicClientApplication(
        _PUBLIC_CLIENT_ID,
        authority=_AUTHORITY,
        token_cache=cache,
    )

    result = None
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(scopes, account=accounts[0])

    if not result:
        flow = app.initiate_device_flow(scopes=scopes)
        if "user_code" not in flow:
            raise RuntimeError(f"認証フロー開始失敗: {flow}")
        print(flow["message"], file=sys.stderr)
        result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        err = result.get("error_description") or result.get("error", "不明なエラー")
        raise RuntimeError(f"トークン取得失敗: {err}")

    _CACHE_PATH.write_text(cache.serialize(), encoding="utf-8")
    return result["access_token"]


def get_token(scopes: list[str]) -> str:
    """Return a valid Microsoft Graph access token.

    Tries Azure CLI first; falls back to MSAL device code flow with
    Microsoft's public client ID (no custom app registration required).
    """
    token = _try_azure_cli()
    if token:
        return token
    print("Azure CLI が利用できません。MSAL デバイスコードフローを使用します。", file=sys.stderr)
    return _msal_device_flow(scopes)
