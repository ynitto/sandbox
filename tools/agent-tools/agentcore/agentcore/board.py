"""agentcore.board — 委譲公示板（agent-board）の入札選別規則とノード契約バージョンの単一定義。

## なぜここに 1 実装を置くか

「このノードはこの公示に入札してよいか」を、2 者が**同じ仕様・別実装**で持っていた:

- `agent_flow/board.py:board_eligible` + `_node_repo_ids`
- `agent_amigos/board.py:board_eligible` + `_board_declared_repos`
  （docstring が自ら「agent_flow/board.py:_node_repo_ids と同じ仕様・別実装」と書いている）

`agentcore.repolocal`（URL 正規化）・`agentcore.nodeid`（ノード名）が解決したのと同型の問題で、
規則が片方だけ育つと **同じ公示が経路によって拾えたり拾えなかったり**する。ここへ集約する。

## 規則（`schemas/board.schema.json` が契約の正典）

公示（`post.json`）側の `requires`（すべて任意・AND）と、ノードの宣言を突き合わせる:

| 公示の要求 | ノードの宣言 | 不一致のとき |
|---|---|---|
| `requires.tags` | `tags` | 入札しない（包含判定） |
| `workspace.url` / `requires.repos` | 担当リポジトリ | 入札しない（URL 正規化一致） |
| `requires.agent_cli` | `agent_cli` | 入札しない（OR。**宣言が空のノードも入札しない**） |
| `requires.contract_version` | `contract_version` | 入札しない（設計 §9 C13。fail-close） |

いずれも「誤動作でなく不参加に倒す」——板は先勝ちなので、拾えるノードが他に居れば
仕事は流れる。拾えないノードが拾って失敗する方が高くつく。
"""
from __future__ import annotations

from .repolocal import normalize_repo_url

# 常駐一本化のノード契約バージョン（設計 §9 C13）。互換性の無い変更をする時だけ上げる。
# 板の上の `nodes/<node-id>.json` と公示の `requires.contract_version` が同じ語彙で話すための数。
CONTRACT_VERSION = 1


def contract_compatible(required_by_post: "int | None", *,
                        declared: "int | None" = CONTRACT_VERSION) -> bool:
    """公示の `requires.contract_version` とノードの宣言が噛み合うか。

    - 要求の無い公示（None）は不問 — True。契約バージョンを載せる前から板にある公示を
      この項目の追加だけで一斉に入札不能にしない。
    - 要求のある公示に対して未宣言のノード（declared=None）は非互換 — False
      （fail-close。更新漏れの古いノードを誤動作でなく不参加に倒す）。
    """
    if required_by_post is None:
        return True
    return declared == required_by_post


def declared_repo_ids(node_repos) -> "set[str]":
    """ノードの担当リポジトリ宣言 → 照合用の識別子集合（名前と正規化 URL）。

    受ける形は 2 つ:

    - `repos.schema.json` の mapping（`{name: {url, owns, readonly, …}}`）
      … プロジェクトのレジストリ由来。`readonly` と `owns` の無いエントリは**書込先候補に
      ならない**ので落とす（板の契約「readonly は書込先候補にしない」）。
    - host.yaml の `repos[]`（`[{url, local}, …]`）
      … **ノードの宣言**（S3）。「このノードの手元にクローンがある」という意味しか持たず、
      `owns` を持たない。ここで `owns` を要求すると host.yaml 由来の宣言が全て消えるので、
      **落とすのは `readonly` だけ**にする——手元にあるものは引き受けられる、が宣言の意図。
    """
    have: "set[str]" = set()

    def _add(name: str, entry: dict, *, require_owns: bool) -> None:
        if entry.get("readonly"):
            return
        if require_owns and not entry.get("owns"):
            return
        if name:
            have.add(str(name))
        if entry.get("url"):
            have.add(normalize_repo_url(str(entry["url"])))

    if isinstance(node_repos, dict):
        for name, entry in node_repos.items():
            if str(name).startswith("_") or not isinstance(entry, dict):
                continue
            _add(str(name), entry, require_owns=True)
    elif isinstance(node_repos, list):
        for entry in node_repos:
            if isinstance(entry, dict) and entry.get("url"):
                _add(str(entry.get("name") or ""), entry, require_owns=False)
            elif isinstance(entry, str) and entry.strip():
                have.add(normalize_repo_url(entry.strip()))
    return have


def eligible(post: dict, *, repos=None, tags=None, agent_cli=None,
             contract_version: "int | None" = CONTRACT_VERSION) -> bool:
    """このノードが公示に入札してよいか。判定材料はすべて引数で受ける（設定の読み方は
    呼び出し側の責務——agent-flow は自分の設定、常駐体は host.yaml から供給する）。"""
    if not isinstance(post, dict):
        return False
    req = post.get("requires") or {}
    if not isinstance(req, dict):
        req = {}

    need_tags = {str(t) for t in (req.get("tags") or [])}
    if need_tags and not need_tags.issubset({str(t) for t in (tags or [])}):
        return False

    need_cli = {str(c).lower() for c in (req.get("agent_cli") or [])}
    if need_cli and not (need_cli & {str(c).lower() for c in (agent_cli or [])}):
        return False

    required_version = req.get("contract_version")
    if not contract_compatible(
            int(required_version) if isinstance(required_version, (int, float)) else None,
            declared=contract_version):
        return False

    have = declared_repo_ids(repos)
    ws = post.get("workspace") or {}
    url = str(ws.get("url") or "") if isinstance(ws, dict) else ""
    if url and str(url) not in have and normalize_repo_url(url) not in have:
        return False
    for ref in (req.get("repos") or []):
        if str(ref) not in have and normalize_repo_url(str(ref)) not in have:
            return False
    return True
