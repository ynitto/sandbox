#!/usr/bin/env python3
"""汎用 webhook フック（provider 非依存の最小例）

agent-loop の inbound webhook コアが送信元に依存しないことを示す例。GitLab を一切
参照せず、受信 JSON をそのままテンプレート用の key-value として返すだけ。

同じコアが GitLab 例（gitlab-mr-webhook.py）とこの汎用例の両方を動かせることが、
コアに provider 固有が残っていないことの確認になる。

使い方（agent-loop.yaml）:
  webhook:
    enabled: true
    port: 8899
  prompts:
    - name: notify
      prompt: |
        [webhook] event={event}
        {message}
      webhook:
        hook: ~/sandbox/tools/agent-loop/hooks/generic-webhook.py

  送信例:
    curl -X POST http://127.0.0.1:8899/hooks/notify \
      -H 'Content-Type: application/json' \
      -d '{"event":"deploy","message":"本番デプロイ完了"}'
"""
from typing import Any


def handle(ctx: Any) -> dict | None:
    # payload をそのままパラメータにする（トップレベルの各キーがテンプレートで使える）。
    # payload が空/非 dict のときは何も送らない。
    if not ctx.payload:
        return None
    return dict(ctx.payload)
