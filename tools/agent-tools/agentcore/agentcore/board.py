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
| `workload` | `workloads` | 入札しない（**宣言が空なら制限しない**） |
| （枠） | `max_concurrent` と板上の自分の非終端件数 | 入札しない（自己抑制） |

上 4 行は「誤動作でなく不参加に倒す」——板は先勝ちなので、拾えるノードが他に居れば
仕事は流れる。拾えないノードが拾って失敗する方が高くつく。

下 2 行は**向きが逆**（fail-open）。上 4 行は公示が「要る」と言った条件で、宣言の欠落は
「無い」と読む。`workloads` はノードが「これしかやらない」と言う条件で、宣言の欠落は
「制限しない」と読む——要求を無視すると拾えないノードが拾い、制限を強制すると
宣言していないノードが全部止まる。安全な倒し方が逆なので、判定の向きも逆になる（P2-3）。
"""
from __future__ import annotations

import json
import os

from . import vocab
from .protocol import safe_name
from .repolocal import normalize_repo_url

# 常駐一本化のノード契約バージョン（設計 §9 C13）。互換性の無い変更をする時だけ上げる。
# 板の上の `nodes/<node-id>.json` と公示の `requires.contract_version` が同じ語彙で話すための数。
#
# **ここが唯一の定義**（P2-1）。以前は板の判定（ここ）・板への宣言（`resident/status.py`）・
# 画面の期待値（dashboard `engine.js`）の 3 箇所に同じ数が居た。片方だけ上げると
# 「版 2 と宣言しつつ版 1 で判定する」が作れてしまい、入札選別は fail-close なので
# **誤動作ではなく無言の不参加**として出る（誰も例外を見ない）。
#
# この数は 2 つの面の版を兼ねる: 板の `nodes/<node-id>.json`（入札の語彙）と
# `.agents/engine/status.json`（画面の語彙）。分けないのは、フリート更新を静止点で
# 一斉に行う規律（C13）の下では「板だけ新しい」状態を作らないため——分けた瞬間に
# 「板は互換だが画面は非互換」という中間状態が正当になり、更新漏れの説明が 2 系統になる。
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


def _parse_contract_version(value) -> "int | None":
    """requires.contract_version を整数へ。読めなければ None（呼び出し側が fail-close）。

    bool は int の下位型なので除外する（`True` を版 1 と読まない）。文字列の `"1"` は受ける。
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value == int(value):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


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


def declared_workloads(host: dict) -> "list[str]":
    """ノード宣言（host.yaml）→ 引き受けるエンジンの一覧。空 = 制限しない（P2-3）。

    **導出しない**（`amigos_bus` の有無などから推測しない）。導出値は「板へ出す宣言」と
    しては一見もっともらしいが、判定に使うと `amigos_bus` を書かずに amigos の板参加を
    起こしている PC が**黙って入札をやめる**——P2 でいちばん起こしてはいけない壊れ方で、
    しかも入札選別は fail-close なので「なぜかこの PC だけ仕事を取らない」という
    無言の形で出る。宣言していないものは宣言しない（板へも出さない）。
    """
    raw = (host or {}).get("workloads")
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, (list, tuple)):
        return [str(w).strip() for w in raw if str(w).strip()]
    return []


def _status_state(board_root: str, did: str, node_id: str) -> "str | None":
    """`delegations/<did>/status/<node>.json` の `state`（無ければ None）。"""
    path = os.path.join(board_root, "delegations", str(did), "status",
                        f"{safe_name(node_id)}.json")
    try:
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return None
    return str(rec.get("state") or "") if isinstance(rec, dict) else None


def holds_delegation(board_root: str, did: str, node_id: str) -> bool:
    """このノードが `did` を落札・引き渡し済みで、まだ終端していないか。

    根拠は板の `status/<who>.json`（自分が引き受けた印）で、自分のバスやプロセス内
    カウンタではない——同じノードで 2 つのプロジェクトが同じ板を巡回する構成があり、
    プロセス内で数えると片方の分が見えない（板が真実という原則）。

    終端の読みは `vocab.is_terminal_read`（旧綴り `canceled` も終端として読む）。板には
    語彙統一（W0-9）より前のノードが書いた値が残りうるので、**読みは寛容・書きは正典のみ**。
    `away`（計画停止中）は終端ではないので「持っている」に数える——枠は空いていない。
    """
    state = _status_state(board_root, did, node_id)
    return bool(state) and not vocab.is_terminal_read(state)


def node_inflight(board_root: str, node_id: str) -> int:
    """板の上で、このノードが落札・引き渡し済みでまだ終端していない委譲の件数（P2-3）。

    `max_concurrent` の自己抑制の材料。**枠の真実は板にある**ので、常駐体のワーカープール
    とは二重管理しない（あちらはこの PC で走るプロセスの数、こちらは板で預かっている
    仕事の数で、数える対象が違う）。
    """
    root = os.path.join(board_root, "delegations")
    try:
        dids = sorted(os.listdir(root))
    except OSError:
        return 0
    n = 0
    for did in dids:
        d = os.path.join(root, did)
        if not os.path.isdir(d):
            continue
        # 公示そのものが終端していれば、自分の status が古いままでも枠は空いている
        if os.path.exists(os.path.join(d, "result.json")) or \
           os.path.exists(os.path.join(d, "cancelled.json")):
            continue
        if holds_delegation(board_root, did, node_id):
            n += 1
    return n


def eligible(post: dict, *, repos=None, tags=None, agent_cli=None, workloads=None,
             contract_version: "int | None" = CONTRACT_VERSION,
             max_concurrent: "int | None" = None, inflight: int = 0) -> bool:
    """このノードが公示に入札してよいか。判定材料はすべて引数で受ける（設定の読み方は
    呼び出し側の責務——agent-flow は自分の設定、常駐体は host.yaml から供給する）。

    `workloads` は空/None で「制限しない」（スキーマの「空 = 全部」）。
    `max_concurrent` は None または 0 で「無制限」（同じくスキーマの語彙）で、正の値なら
    `inflight`（板上の自分名義の非終端件数・`node_inflight`）が上限に達したときに
    入札しない——「超過時は新規入札を控える」という板の契約の実装（P2-3）。
    """
    if not isinstance(post, dict):
        return False
    # requires が壊れている公示は「制限なし」に倒さない——誤って拾う方が高くつく（fail-close）。
    if "requires" in post and post.get("requires") is not None \
            and not isinstance(post.get("requires"), dict):
        return False
    req = post.get("requires") or {}
    if not isinstance(req, dict):
        req = {}

    have_workloads = {str(w) for w in (workloads or [])}
    if have_workloads and str(post.get("workload") or "") not in have_workloads:
        return False

    try:
        cap = int(max_concurrent) if max_concurrent is not None else 0
    except (TypeError, ValueError):
        cap = 0
    if cap > 0 and int(inflight or 0) >= cap:
        return False

    need_tags = {str(t) for t in (req.get("tags") or [])}
    if need_tags and not need_tags.issubset({str(t) for t in (tags or [])}):
        return False

    need_cli = {str(c).lower() for c in (req.get("agent_cli") or [])}
    if need_cli and not (need_cli & {str(c).lower() for c in (agent_cli or [])}):
        return False

    required_version = req.get("contract_version")
    if required_version is not None:
        parsed = _parse_contract_version(required_version)
        if parsed is None or not contract_compatible(parsed, declared=contract_version):
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
