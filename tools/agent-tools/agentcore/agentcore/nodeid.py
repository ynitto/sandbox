"""agentcore.nodeid — node_id（PC の身元）の正規化の単一定義。

node_id は板（agent-board）とプロトコル上の「どの PC か」であり、設計 §3.1 は
`node_id = PC 名` を既定とする。板は名義でファイルを分割する
（`nodes/<node-id>.json` / `delegations/<id>/bids/<who>.json` /
`.../status/<who>.json`）ため、**同じ PC からは常に同じ綴りが出る**ことが不変条件になる。

各エンジンが独自に正規化すると、これが壊れる（実装計画 W1-10 のレビューで実際に検出）:

- agent-flow は不正文字を `_` へ、大小文字はそのまま（`Mac`）
- agent-amigos は不正文字を `-` へ、さらに小文字化（`mac`）

同一 PC が板に 2 ノードとして現れ、しかも macOS の既定ファイルシステムは大小文字を
区別しないので `Mac.json` と `mac.json` が同じファイルになって互いを上書きし、
Linux では別ノードとして残る——プラットフォームで壊れ方が変わる。

したがって正規化はここ 1 箇所に置き、flow / amigos / project（doctor）が同じ関数を使う。

**小文字化する理由**: ホスト名は DNS 上そもそも大小文字を区別せず、同じ PC が
`Mac` と `mac` の 2 通りで観測されうる。大小文字を区別しないファイルシステム上での
自己衝突を避けるため、正規形は小文字に倒す。
"""
from __future__ import annotations

# 板のファイル名にそのまま使える文字集合（flow / amigos 双方の既存 `_safe` が
# 「安全」と認める文字の共通部分。ここを通した id は各エンジンの `_safe` に
# 掛けても不変＝二重正規化で綴りが変わらない）。
_ALLOWED_EXTRA = "._-"
_FALLBACK = "node"


def normalize_node_id(name: "str | None") -> str:
    """node_id を正規形（小文字・`[a-z0-9._-]` のみ）に揃える。

    非 ASCII の英数（例: 日本語のホスト名）も `isalnum()` では真になるが、板のファイル名
    として扱うと OS・git・転送経路ごとに正規化（NFC/NFD）が割れるため ASCII に落とす。
    空になった場合は `"node"` を返す（名前が消えて空文字がファイル名になる事故を防ぐ）。"""
    out = []
    for ch in str(name or ""):
        if (ch.isascii() and ch.isalnum()) or ch in _ALLOWED_EXTRA:
            out.append(ch)
        else:
            out.append("-")
    # 先頭・末尾の区切りは落とす（`-mac-` のような綴りゆれを作らない）
    normalized = "".join(out).strip("-").lower()
    return normalized or _FALLBACK
