"""Common Microsoft Graph API authentication via MSAL device code flow."""
from __future__ import annotations

import sys
from pathlib import Path

import msal

CACHE_PATH = Path.home() / ".outlook_graph_cache.json"
CLIENT_ID_PATH = Path.home() / ".outlook_graph_client_id"
AUTHORITY = "https://login.microsoftonline.com/common"


def _load_client_id() -> str:
    if CLIENT_ID_PATH.exists():
        return CLIENT_ID_PATH.read_text(encoding="utf-8").strip()
    print("Azure AD アプリの Client ID を入力してください: ", end="", file=sys.stderr)
    client_id = input().strip()
    CLIENT_ID_PATH.write_text(client_id, encoding="utf-8")
    return client_id


def get_token(scopes: list[str]) -> str:
    """Return a valid access token for the given scopes, refreshing if needed."""
    client_id = _load_client_id()

    cache = msal.SerializableTokenCache()
    if CACHE_PATH.exists():
        cache.deserialize(CACHE_PATH.read_text(encoding="utf-8"))

    app = msal.PublicClientApplication(
        client_id,
        authority=AUTHORITY,
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

    CACHE_PATH.write_text(cache.serialize(), encoding="utf-8")
    return result["access_token"]
