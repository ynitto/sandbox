"""GitBus — 専用バスリポジトリ ＋ ミッション別ブランチ（設計書 §5.1、P1）。

- オンプレ git remote に**専用のバスリポジトリ**（例 amigos-bus.git）を切る。
  既存リポジトリの subdir 間借りはしない。
- **ミッション（タスク）単位でブランチ分離**:
    main            … 公示インデックスのみ（index/<mid>.json、オーナーが書く）
    mission/<mid>   … そのミッションの §4 レイアウト一式（リポジトリ直下が内容ルート）
  参加ノードは main を軽く poll して募集を発見し、join したミッションの
  ブランチだけを clone する。gc はブランチ削除。
- **同期の作法は agent-project / agent-flow の state_git の規律を流用**:
  pull は間隔律速（claim の勝者確認は force で常に最新化）・push 競合は
  `pull --rebase` → 再 push の指数バックオフ・**force push はしない**。
- 各ノードは**自分専用のクローン**を持つため、ローカルの変更はすべて自プロセス
  由来 = `add -A` でステージしても他者の書き込みを巻き込まない（state_git の
  「自 subdir のみステージ」と同じ安全性が、クローン分離によって成立する）。
- 所有権分割（§4.2）により同一ファイルの双方向変更は起きないので 3-way 裁定は
  不要。万一 rebase が衝突したら abort → origin へリセットし、そのターンは
  「なかったこと」になる（ターン原子性 §6.6: バスには全部か無かしか残らない）。
"""
from __future__ import annotations

import hashlib
import os
import random
import shutil
import subprocess
import time

from .bus import Bus, MissionPaths
from .util import log, write_json_atomic

DEFAULT_PULL_INTERVAL = 15.0
PUSH_RETRIES = 5


def _pull_interval() -> float:
    try:
        return float(os.environ.get("AGENT_AMIGOS_PULL_INTERVAL", DEFAULT_PULL_INTERVAL))
    except ValueError:
        return DEFAULT_PULL_INTERVAL


class GitBus(Bus):
    kind = "git"

    def __init__(self, url: str, workdir: "str | None" = None,
                 pull_interval: "float | None" = None):
        self.url = url
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
        self.root = os.path.abspath(os.path.expanduser(
            workdir or os.path.join("~", ".agent", "amigos", "bus", digest)))
        self.pull_interval = pull_interval if pull_interval is not None else _pull_interval()
        self._last_pull: "dict[str, float]" = {}
        os.makedirs(self.root, exist_ok=True)

    # --- git 低レベル -------------------------------------------------------
    def _git(self, cwd: "str | None", *args: str, check: bool = True):
        cmd = ["git"] + (["-C", cwd] if cwd else []) + list(args)
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace")
        if check and proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} 失敗 (rc={proc.returncode}): "
                               f"{(proc.stderr or proc.stdout).strip()[-500:]}")
        return proc

    def _remote_has(self, branch: str) -> bool:
        proc = self._git(None, "ls-remote", self.url, f"refs/heads/{branch}", check=False)
        return proc.returncode == 0 and bool(proc.stdout.strip())

    def _clone_dir(self, branch: str) -> str:
        safe = branch.replace("/", "__")
        return os.path.join(self.root, safe)

    def _ensure_clone(self, branch: str, create: bool = False) -> "str | None":
        """ブランチの自分専用クローンを用意する。remote にも無く create でも
        なければ None。"""
        d = self._clone_dir(branch)
        if os.path.isdir(os.path.join(d, ".git")):
            return d
        if self._remote_has(branch):
            self._git(None, "clone", "--quiet", "--single-branch", "--branch", branch,
                      self.url, d)
        elif create:
            os.makedirs(d, exist_ok=True)
            self._git(d, "init", "--quiet")
            self._git(d, "checkout", "--quiet", "-b", branch)
            self._git(d, "remote", "add", "origin", self.url)
        else:
            return None
        self._git(d, "config", "user.name", "agent-amigos")
        self._git(d, "config", "user.email", "agent-amigos@local")
        return d

    def _branch_of(self, clone_dir: str) -> str:
        # symbolic-ref は unborn branch（init 直後・コミットなし）でも解決できる
        return self._git(clone_dir, "symbolic-ref", "--short", "HEAD").stdout.strip()

    def _has_head(self, clone_dir: str) -> bool:
        return self._git(clone_dir, "rev-parse", "--verify", "-q", "HEAD",
                         check=False).returncode == 0

    def _pull_clone(self, d: str) -> None:
        branch = self._branch_of(d)
        if not self._remote_has(branch):
            return                       # まだ誰も push していない新規ブランチ
        if not self._has_head(d):
            # unborn（init 直後）だが remote には実体がある → そのまま取り込む
            self._git(d, "fetch", "--quiet", "origin", branch, check=False)
            self._git(d, "reset", "--hard", f"origin/{branch}", "--quiet", check=False)
            return
        proc = self._git(d, "pull", "--rebase", "--quiet", "origin", branch, check=False)
        if proc.returncode != 0:
            # 所有権分割により本来起きない。起きたら abort → origin に合わせる
            # （ローカルの未 push ターンは失われるが、やり直すだけで整合は壊れない §6.6）
            self._git(d, "rebase", "--abort", check=False)
            self._git(d, "fetch", "--quiet", "origin", branch, check=False)
            self._git(d, "reset", "--hard", f"origin/{branch}", "--quiet", check=False)
            log("gitbus", f"{branch}: rebase 衝突のため origin へリセットしました"
                          "（ローカル未 push 分は破棄）")

    def _push_clone(self, d: str, msg: str) -> None:
        branch = self._branch_of(d)
        if self._git(d, "status", "--porcelain", check=False).stdout.strip():
            self._git(d, "add", "-A")
            self._git(d, "commit", "--quiet", "-m", msg or "amigos sync", check=False)
        if not self._has_head(d):
            return                       # unborn（コミットなし）→ 押すものがない
        ahead = True
        if self._remote_has(branch):
            proc = self._git(d, "rev-list", "--count", f"origin/{branch}..HEAD", check=False)
            ahead = proc.returncode != 0 or proc.stdout.strip() != "0"
        if not ahead:
            return
        for attempt in range(PUSH_RETRIES):
            proc = self._git(d, "push", "--quiet", "-u", "origin", branch, check=False)
            if proc.returncode == 0:
                return
            # 競合 → pull --rebase → 再 push（指数バックオフ・force はしない）
            time.sleep(0.1 * (2 ** attempt) + random.uniform(0, 0.05))
            self._pull_clone(d)
        log("gitbus", f"{branch}: push が {PUSH_RETRIES} 回失敗しました（次の同期で再試行）")

    def _each_clone(self):
        try:
            names = sorted(os.listdir(self.root))
        except FileNotFoundError:
            return
        for name in names:
            d = os.path.join(self.root, name)
            if os.path.isdir(os.path.join(d, ".git")):
                yield d

    # --- Bus フック ---------------------------------------------------------
    def sync_pull(self, force: bool = False) -> None:
        now = time.time()
        for d in self._each_clone():
            if not force and now - self._last_pull.get(d, 0) < self.pull_interval:
                continue
            self._pull_clone(d)
            self._last_pull[d] = now

    def sync_push(self, msg: str = "") -> None:
        for d in self._each_clone():
            self._push_clone(d, msg)

    def prepare_mission(self, mission_id: str) -> None:
        self._ensure_clone(f"mission/{mission_id}", create=True)
        self._ensure_clone("main", create=True)

    def register_mission(self, mission_id: str, meta: dict) -> None:
        d = self._ensure_clone("main", create=True)
        write_json_atomic(os.path.join(d, "index", f"{mission_id}.json"),
                          {"id": mission_id, "title": meta.get("title"),
                           "branch": f"mission/{mission_id}",
                           "owner_node": meta.get("owner_node"),
                           "posted_at": meta.get("posted_at")})

    def remove_mission(self, mission_id: str) -> None:
        branch = f"mission/{mission_id}"
        if self._remote_has(branch):
            self._git(None, "push", "--quiet", self.url, "--delete", branch, check=False)
        d = self._ensure_clone("main")
        if d:
            entry = os.path.join(d, "index", f"{mission_id}.json")
            if os.path.isfile(entry):
                os.remove(entry)
                self._push_clone(d, f"gc {mission_id}")
        shutil.rmtree(self._clone_dir(branch), ignore_errors=True)

    def mission(self, mission_id: str) -> MissionPaths:
        d = self._ensure_clone(f"mission/{mission_id}")
        if d is None:
            # 未 clone・remote にも無い → 存在しないミッションとして空パスを返す
            # （mission.json が無いので load_mission が「見つからない」と言う）
            d = self._clone_dir(f"mission/{mission_id}")
        return MissionPaths(d, mission_id)

    def list_missions(self) -> list:
        d = self._ensure_clone("main")
        if d is None:
            return []
        now = time.time()
        if now - self._last_pull.get(d, 0) >= self.pull_interval:
            self._pull_clone(d)
            self._last_pull[d] = now
        index = os.path.join(d, "index")
        try:
            return sorted(n[:-5] for n in os.listdir(index)
                          if n.endswith(".json") and ".tmp." not in n)
        except FileNotFoundError:
            return []
