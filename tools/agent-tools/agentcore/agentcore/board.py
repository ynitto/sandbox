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
import math
import os
from datetime import datetime, timezone

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
CONTRACT_VERSION = 2

# 書込先が複数（workset）ある公示を安全に配れる契約版。要素ごとに commit/push し
# `deliveries[]` を返せる請負側でなければ、`workspaces` を未知キーとして無視して **primary
# だけに書く**（静かな部分実行）ため、版でしか止められない。`contract_compatible` は完全一致
# なので、この版へ上げるにはフリート全体を静止点で一斉に更新する必要がある（C13）。
# P4 でその一斉更新を行い、`CONTRACT_VERSION` をこの版へ上げた——版 1 のままのノードは
# workset の公示に入札しない（fail-close）。
# 設計: docs/plans/2026-09-05-agent-flow-multi-workspace-design.md §5.7・§7 の P4。
WORKSET_CONTRACT_VERSION = 2


def workset_posts_supported(declared: "int | None" = None) -> bool:
    """このフリートが書込先の集合を持つ公示を扱えるか（P4 で真になった）。"""
    version = CONTRACT_VERSION if declared is None else declared
    return version is not None and version >= WORKSET_CONTRACT_VERSION


# node-budget-summary 射影の契約版（schemas/node-budget-summary.schema.json）。
# status/<node>.json と板 nodes/<id>.json の `budget` が同じ語彙。版不一致は非適格（fail-close）。
BUDGET_SUMMARY_CONTRACT_VERSION = 1


def status_budget_gate(record: dict, *, at: "datetime | None" = None,
                       enforce_default: bool = False) -> dict:
    """status/<node>.json（または板上 nodes を同形に畳んだ record）の生存＋利用枠ゲート。

    allocate/claim（agent-project.coordination）と `eligible` の**単一実装**。

    戻り値:
      alive     … node あり・availability=active・鮮度内
      kind      … "ok" | "exhausted" | "unknown"
                  （不明≠枯渇。stale / 版不一致 / source=unavailable / 射影欠落 → unknown）
      eligible  … alive かつ（enforce でないか kind==ok）
      reason_codes … 射影の固定語彙（欠落時は []）
    enforce は射影の budget.enforce、無ければ enforce_default（既定 false）。
    """
    now = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    node = ""
    active = False
    fresh = False
    reason_codes: list[str] = []
    can_accept: "bool | None" = None
    enforce = bool(enforce_default)
    kind = "unknown"
    try:
        node = str(record.get("node", "") or "").strip()
        active = str(record.get("availability", "active")) == "active"
        updated = datetime.fromisoformat(str(record["updated_iso"]).replace("Z", "+00:00"))
        fresh_sec = float(record.get("fresh_after_sec", 120.0) or 120.0)
        fresh = (now - updated.astimezone(timezone.utc)).total_seconds() <= fresh_sec
    except (KeyError, TypeError, ValueError):
        return {"node": node, "active": False, "fresh": False, "alive": False,
                "enforce": enforce, "can_accept": None, "reason_codes": [],
                "kind": "unknown", "eligible": False}
    alive = bool(node) and active and fresh
    budget = record.get("budget")
    if isinstance(budget, dict):
        if "enforce" in budget:
            enforce = bool(budget.get("enforce"))
        reason_codes = [str(c) for c in list(budget.get("reason_codes") or [])]
        if "can_accept" in budget:
            can_accept = bool(budget.get("can_accept"))
        ver = budget.get("contract_version")
        source = str(budget.get("source", "") or "")
        if not fresh:
            kind = "unknown"
        elif ver != BUDGET_SUMMARY_CONTRACT_VERSION:
            kind = "unknown"
        elif source == "unavailable":
            kind = "unknown"
        elif can_accept is False:
            kind = "exhausted"
        elif can_accept is True:
            kind = "ok"
        else:
            kind = "unknown"
    else:
        # 射影無し: enforce 中は不明（fail-close）。非 enforce では従来どおり alive のみ。
        kind = "unknown"
    eligible = alive and (not enforce or kind == "ok")
    return {"node": node, "active": active, "fresh": fresh, "alive": alive,
            "enforce": enforce, "can_accept": can_accept, "reason_codes": reason_codes,
            "kind": kind, "eligible": eligible}


def budget_kwargs_from_node(node: "dict | None") -> dict:
    """板上 `nodes/<id>.json` → `eligible(..., **kwargs)` 用の budget / 鮮度 kwargs。

    budget が無い（ミラー前・旧ノード）ときは空 dict ——呼び出し側は従来どおり適格判定のみ。
    板の `availability`（稼働窓文字列）は status の availability 語彙と違うので渡さない。
    """
    if not isinstance(node, dict):
        return {}
    budget = node.get("budget")
    if not isinstance(budget, dict):
        return {}
    out: dict = {"budget": budget, "node": str(node.get("node") or "") or None}
    hb = node.get("heartbeat") or node.get("updated_iso")
    if hb is not None:
        out["heartbeat"] = hb
    if node.get("fresh_after_sec") is not None:
        out["fresh_after_sec"] = node.get("fresh_after_sec")
    return {k: v for k, v in out.items() if v is not None}


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

    NaN / Inf は `int()` が ValueError / OverflowError を投げる。`json` は既定で `NaN` /
    `Infinity` リテラルを受理するので、そういう公示が板に 1 件あるだけで `eligible()` が
    例外になり、そのノードの入札巡回ごと止まってしまう——fail-close は「拾わない」であって
    「落ちる」ではないので、読めない値として None に倒す。
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or value != int(value):
            return None
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
             max_concurrent: "int | None" = None, inflight: int = 0,
             budget=None, heartbeat=None, updated_iso=None, fresh_after_sec=None,
             node: "str | None" = None, enforce_default: bool = False,
             at: "datetime | None" = None) -> bool:
    """このノードが公示に入札してよいか。判定材料はすべて引数で受ける（設定の読み方は
    呼び出し側の責務——agent-flow は自分の設定、常駐体は host.yaml から供給する）。

    `workloads` は空/None で「制限しない」（スキーマの「空 = 全部」）。
    `max_concurrent` は None または 0 で「無制限」（同じくスキーマの語彙）で、正の値なら
    `inflight`（板上の自分名義の非終端件数・`node_inflight`）が上限に達したときに
    入札しない——「超過時は新規入札を控える」という板の契約の実装（P2-3）。

    Phase1: 任意の `budget`（node-budget-summary）と鮮度（`heartbeat`/`updated_iso` +
    `fresh_after_sec`）を渡すと、プロジェクト内割当と同じ `status_budget_gate` で
    can_accept＋鮮度を見る。`budget` 省略時は従来どおり（board なし／ミラー前経路を壊さない）。
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

    # 利用枠・鮮度（allocate/claim と単一実装）。budget 無しは Phase0 互換でスキップ。
    if isinstance(budget, dict):
        clock = updated_iso or heartbeat
        if clock is None:
            # 自ノードがライブで予算だけ見る経路——時計欠落を stale に倒さない。
            clock = datetime.now(timezone.utc).isoformat()
        gate = status_budget_gate(
            {"node": str(node or "self"),
             # 板 nodes の availability は稼働窓文字列であり status の active 語彙ではない。
             # 入札選別の時点でプロセスが動いている＝生存として畳む。
             "availability": "active",
             "updated_iso": clock,
             "fresh_after_sec": (120.0 if fresh_after_sec is None else fresh_after_sec),
             "budget": budget},
            at=at, enforce_default=enforce_default)
        # enforce=false は観測のみ（§6: 従来の落札を変えない）。enforce 時のみ kind==ok を
        # 要求し、stale・版不一致・unavailable は unknown ＝非適格（fail-close）。
        if bool(gate.get("enforce")) and gate.get("kind") != "ok":
            return False
    return True
