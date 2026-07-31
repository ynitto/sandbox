#!/usr/bin/env python3
"""GitLab Merge Request webhook フック（agent-loop の inbound webhook として実行される）

agent-loop の WebhookServer が `POST /hooks/<name>` を受けたときに `handle(ctx)` を
呼ぶ。ここには **GitLab 固有の知識だけ**を置く（イベントヘッダ名 `X-Gitlab-Event`、
`object_attributes` の構造など）。agent-loop コアは送信元を一切知らない。

契約:
  handle(ctx) -> dict | None
    dict : プロンプトテンプレートへ注入する key-value。エントリの `prompt` に
           `{project}` `{mr_iid}` … として差し込まれる。
    None : 無視（対象外イベント/アクション）。サーバは 200 を返す。

ctx（provider 非依存のリクエストコンテキスト）:
  ctx.name    ルート名（パスの <name>）
  ctx.method  HTTP メソッド
  ctx.headers 全ヘッダ（小文字キー）
  ctx.query   クエリ文字列のパース結果
  ctx.raw     生ボディ（bytes、署名検証用）
  ctx.payload JSON パース済みボディ（dict）

WebhookServer は複数スレッドから handle() を呼び得る。モジュール状態を持たせず
ステートレスに保つこと。
"""
from typing import Any

# 反応するアクション（GitLab MR の object_attributes.action）
_ACTIONS = {"open", "reopen", "update"}


def handle(ctx: Any) -> dict | None:
    # --- provider 固有: イベント種別は hook が自分でヘッダから読む ---
    event = ctx.headers.get("x-gitlab-event", "")
    if "Merge Request" not in event:
        return None

    attrs = ctx.payload.get("object_attributes", {})
    if attrs.get("action") not in _ACTIONS:
        return None

    project = ctx.payload.get("project", {})
    # --- パースして key-value を組み立てて返すだけ（文言はテンプレート側）---
    return {
        "event": event,
        "project": project.get("path_with_namespace", "?"),
        "mr_iid": attrs.get("iid"),
        "title": attrs.get("title", ""),
        "url": attrs.get("url", ""),
        "action": attrs.get("action", ""),
        "source_branch": attrs.get("source_branch", ""),
        "target_branch": attrs.get("target_branch", ""),
        "author_id": attrs.get("author_id", ""),
    }
