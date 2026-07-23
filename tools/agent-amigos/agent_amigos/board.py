"""board — 委譲公示板（agent-board）への参加（請負・入札）。

agent-board は「リポジトリ＋契約」だけで処理を持たない（schemas/board.schema.json）。入札・
引き渡しの処理はこの請負側デーモンが担う: 板を巡回し、workload=amigos の公示に repos/tags 照合で
入札（名前空間付き claim ＋ (ts, who) タイブレーク＝flow / amigos の claim と同じ仕様・別実装）、
勝てば**オーナーとしてミッションを公示**する。結合はデータ契約のみ — agent-board のコードは
import せず、板のレイアウトを読み書きするだけ。設計:
docs/plans/2026-07-23-delegation-board-distributed-bidding-design.md
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import time

from .assign import _declared_repos, _norm_repo_url
from .commands import _do_post
from .util import log, now_iso, read_json, write_json_atomic


def _safe(s: str) -> str:
    return "".join(c if (c.isalnum() or c in "._-") else "-" for c in str(s)) or "x"


class BoardMirror:
    """板リポジトリのローカルミラー。git+<url> はノード専用クローン、他はローカル dir。
    転送層（sync_pull / sync_push）は agent-project / agent-flow の state_git と同じ規律
    （間隔なしの都度同期・pull --rebase・force push 禁止・自パスのみは所有権分割で自然に成立）。"""

    def __init__(self, spec: str, node_id: str, workdir: "str | None" = None,
                 branch: str = "main"):
        spec = str(spec or "").strip()
        self.branch = branch or "main"
        if spec.startswith("git+"):
            self.git = True
            self.remote = spec[4:]
            base = workdir or os.path.join(
                os.path.expanduser("~/.agents/amigos-board"),
                hashlib.sha1(self.remote.encode()).hexdigest()[:8])
            self.dir = os.path.join(os.path.abspath(base), _safe(node_id))
            self._ensure_clone()
        else:
            self.git = False
            self.dir = os.path.abspath(spec)
            os.makedirs(os.path.join(self.dir, "delegations"), exist_ok=True)

    def _git(self, *args, check=True):
        return subprocess.run(["git", "-C", self.dir, *args],
                              capture_output=True, text=True, check=check)

    def _ensure_clone(self) -> None:
        if os.path.isdir(os.path.join(self.dir, ".git")):
            return
        os.makedirs(os.path.dirname(self.dir) or ".", exist_ok=True)
        r = subprocess.run(["git", "clone", "--branch", self.branch, self.remote, self.dir],
                           capture_output=True, text=True)
        if r.returncode != 0:
            subprocess.run(["git", "clone", self.remote, self.dir],
                           capture_output=True, text=True, check=True)
            self._git("checkout", "-B", self.branch)
        os.makedirs(os.path.join(self.dir, "delegations"), exist_ok=True)

    def sync_pull(self) -> None:
        if self.git:
            self._git("pull", "--rebase", "origin", self.branch, check=False)

    def sync_push(self, msg: str) -> None:
        if not self.git:
            return
        self._git("add", "-A", check=False)
        if not self._git("status", "--porcelain", check=False).stdout.strip():
            return
        self._git("commit", "-m", msg or "board update", check=False)
        for i in range(5):
            if self._git("push", "origin", self.branch, check=False).returncode == 0:
                return
            self._git("pull", "--rebase", "origin", self.branch, check=False)
            time.sleep(min(2 ** i, 16))


def _list_bids(bids_dir: str) -> "dict[str, dict]":
    out = {}
    if os.path.isdir(bids_dir):
        for name in os.listdir(bids_dir):
            if name.endswith(".json"):
                info = read_json(os.path.join(bids_dir, name))
                if info:
                    out[info.get("who", name[:-5])] = info
    return out


def _winner(bids_dir: str) -> "str | None":
    now = time.time()
    live = [(info.get("ts", 0.0), who) for who, info in _list_bids(bids_dir).items()
            if info.get("lease_until", 0) >= now]
    return min(live)[1] if live else None


def _try_bid(mirror: BoardMirror, bids_dir: str, did: str, who: str, lease: float) -> bool:
    """入札して勝者になれたら True（先勝ち・(ts, who) 決定的タイブレーク）。"""
    os.makedirs(bids_dir, exist_ok=True)
    w = _winner(bids_dir)
    if w is not None and w != who:
        return False
    write_json_atomic(os.path.join(bids_dir, f"{_safe(who)}.json"), {
        "who": who, "ts": time.time(), "claimed_at": now_iso(),
        "lease_until": time.time() + lease, "workload": "amigos"})
    mirror.sync_push(f"bid {did} by {who}")
    mirror.sync_pull()
    if _winner(bids_dir) == who:
        return True
    try:
        os.remove(os.path.join(bids_dir, f"{_safe(who)}.json"))
        mirror.sync_push(f"bid withdraw {who}")
    except OSError:
        pass
    return False


def board_eligible(post: dict, node_repos, node_tags) -> bool:
    """公示に入札してよいか（成果物リポジトリ・タグでの選別）。
    workspace.url と requires.repos を担当し、requires.tags を包含していれば可。"""
    req = post.get("requires") or {}
    need_tags = set(str(t) for t in (req.get("tags") or []))
    if need_tags and not need_tags.issubset(set(node_tags or [])):
        return False
    have = _declared_repos(node_repos)
    ws = post.get("workspace") or {}
    if ws.get("url"):
        if str(ws.get("url")) not in have and _norm_repo_url(ws["url"]) not in have:
            return False
    for ref in (req.get("repos") or []):
        if str(ref) not in have and _norm_repo_url(ref) not in have:
            return False
    return True


def _synth_design(post: dict) -> str:
    return (f"# {post.get('title') or post.get('goal') or post.get('id')}\n\n"
            f"## ゴール\n\n{str(post.get('goal') or '').strip()}\n")


def _post_to_command(post: dict) -> dict:
    """委譲封筒（delegation post）→ amigos-command の post レコード。"""
    amigos = (post.get("engine") or {}).get("amigos") or {}
    rec = {
        "command": "post", "mission_id": post["id"],
        "title": post.get("title") or "", "goal": post.get("goal") or "",
        "design": (post.get("design") or "").strip() or _synth_design(post),
        "roles": amigos.get("roles") or [],
    }
    mission = dict(amigos.get("mission") or {})
    policy = post.get("policy") or {}
    if policy.get("assignment"):
        mission.setdefault("assignment_policy", policy["assignment"])
    if policy.get("staffing"):
        mission.setdefault("staffing_policy", policy["staffing"])
    if policy.get("staffing_timeout_sec") is not None:
        mission.setdefault("staffing_timeout", policy["staffing_timeout_sec"])
    if post.get("acceptance"):
        mission.setdefault("acceptance", post["acceptance"])
    if post.get("deadline"):
        mission.setdefault("deadline", post["deadline"])
    budget = post.get("budget") or {}
    if budget:
        mb = dict(mission.get("budget") or {})
        if budget.get("execution_minutes") is not None:
            mb.setdefault("execution_minutes", budget["execution_minutes"])
        if budget.get("per_unit_turns") is not None:
            mb.setdefault("per_role_turns", budget["per_unit_turns"])
        if mb:
            mission["budget"] = mb
    if mission:
        rec["mission"] = mission
    return rec


def poll_board(daemon) -> "list[str]":
    """板を 1 巡: workload=amigos の公示に入札し、勝てばオーナーとしてミッションを公示する。
    公示した委譲 id の一覧を返す。board 未設定なら no-op。"""
    spec = getattr(daemon, "board", None)
    if not spec:
        return []
    mirror = BoardMirror(spec, daemon.node_id, getattr(daemon, "board_workdir", None))
    mirror.sync_pull()
    node_repos = getattr(daemon, "repos", None) or {}
    node_tags = getattr(daemon, "tags", None) or []
    lease = float(getattr(daemon, "board_lease", None) or 900.0)
    home = daemon.commands_home or daemon.home or os.getcwd()
    deleg_root = os.path.join(mirror.dir, "delegations")
    handed = []
    if not os.path.isdir(deleg_root):
        return handed
    for did in sorted(os.listdir(deleg_root)):
        ddir = os.path.join(deleg_root, did)
        if not os.path.isdir(ddir):
            continue
        if os.path.exists(os.path.join(ddir, "result.json")) or \
           os.path.exists(os.path.join(ddir, "cancelled.json")):
            continue
        post = read_json(os.path.join(ddir, "post.json"))
        if not isinstance(post, dict) or post.get("workload") != "amigos" or post.get("op") != "post":
            continue
        # 既にこのミッションを公示済み（自分の bus にある）ならスキップ
        if daemon.bus.mission(did).exists():
            continue
        if not board_eligible(post, node_repos, node_tags):
            continue
        bids_dir = os.path.join(ddir, "bids")
        w = _winner(bids_dir)
        if w is not None and w != daemon.node_id:
            continue
        if not _try_bid(mirror, bids_dir, did, daemon.node_id, lease):
            continue
        # 落札 → オーナーとしてミッションを公示（board の落札 = ミッションオーナーの決定）
        try:
            _do_post(daemon.bus, daemon.node_id, home, _post_to_command(post))
        except (ValueError, RuntimeError, OSError, SystemExit, KeyError) as e:
            write_json_atomic(os.path.join(ddir, "status", f"{_safe(daemon.node_id)}.json"), {
                "who": daemon.node_id, "state": "failed", "error": str(e),
                "heartbeat": now_iso()})
            mirror.sync_push(f"handoff-failed {did}")
            continue
        write_json_atomic(os.path.join(ddir, "status", f"{_safe(daemon.node_id)}.json"), {
            "who": daemon.node_id, "state": "dispatched", "native_id": did,
            "heartbeat": now_iso(), "lease_until": time.time() + lease})
        mirror.sync_push(f"won+dispatch {did} by {daemon.node_id}")
        handed.append(did)
        log(daemon.node_id, f"board 落札→ミッション公示 {did}: {str(post.get('goal',''))[:50]}")
    return handed
