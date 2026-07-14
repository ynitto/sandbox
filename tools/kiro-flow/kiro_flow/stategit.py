from __future__ import annotations
# stategit.py — 元 kiro-flow.py の 1498-1990 行目（機械分割・内容無改変）。
# 単体 import しない。kiro_flow/__init__.py が共有名前空間へ順に exec 合成する。
# --------------------------------------------------------------------------
# 状態の git 保存・共有（state_git）— kiro-project の同名機能と同じ流儀
# --------------------------------------------------------------------------
# ローカルバスのワーク内容（<bus>/runs/・<bus>/inbox/）を共有 git リポジトリへ保存し、
# リモートの kiro-projects-viewer（フロータブ）が run の進捗/結果を読めるようにする。
# GitBus（--git）が「バスそのものを git にして実行を分散する」のに対し、これは
# 「実行はローカルのまま、状態の鏡だけを共有する」——実行は state_git に一切依存しない。
#   ・リモート負荷を抑える: subdir だけの sparse・blob:none の管理クローンを 1 本再利用し、
#     fetch/push は state_git_interval（既定 300 秒）で律速。push は共有すべきローカル
#     コミットがあるときだけ（run の終端時は間隔を待たず押し出す）。
#   ・多重コミッタ前提: 同一リポジトリには他プログラム（kiro-project の state_git・
#     viewer 側の git-file-sync 等）もコミットする。ステージは自 subdir のみ、push 競合は
#     pull --rebase → 再 push の指数バックオフで吸収し、force push はしない。
#   ・双方向: 機械の状態（runs/）は外へ、人の投入（inbox/ の要求ドロップ）は中へ。前回同期
#     スナップショット（manifest）基準の 3-way で発生源を判定し、同時変更のみ
#     「inbox/ はリモート優先・runs/ 等の機械状態はローカル優先」で決定的に裁定する。
STATE_GIT_MARKER = "kiro-flow.stateclone"       # 自前管理クローンの目印（git config）
_STATE_LOCK_STALE_SEC = 30.0                    # これ以上古い .git ロックは残骸とみなし自己回復
_STATE_GIT_RETRIES = 4                          # ロック起因の git 失敗の再試行回数
_STATE_PUSH_RETRIES = 5                         # push 競合の再試行回数（2,4,8,16s バックオフ）


class _StateGitCorrupt(Exception):
    """state_git クローンの電源断オブジェクト破損を検知した内部シグナル（sync が捕捉して作り直す）。"""


class StateGit:
    """ローカルバス状態 ⇔ 共有 git リポジトリの双方向同期（GitBus と同じ管理クローン流儀）。

    真実は常にファイル側（ローカルはバス・リモートは共有リポジトリ）にあり、このクラスは
    「前回同期時点のスナップショット（manifest）」を基準に差分の発生源を判定して橋渡しするだけ。
    クローンや manifest を失っても、次の同期が裁定規則で決定的に再収束させる。"""

    def __init__(self, bus_root: str, remote: str, branch: str = "main",
                 subdir: str = "kiro-flow", interval: float = 300.0,
                 clone_dir: "str | None" = None):
        self.bus_root = os.path.abspath(bus_root)
        self.remote = remote
        self.branch = branch or "main"
        self.subdir = (subdir or "").strip("/")
        self.interval = max(0.0, interval)
        self.clone = clone_dir or os.path.join(self.bus_root, ".state-git")
        self._ready = False
        self._last_remote = 0.0     # 最後にリモートへ触れた時刻（fetch/push の間隔律速）
        self._last_attempt = 0.0    # クローン準備の失敗も間隔律速（不通のリモートを連打しない）

    # --- git 低レベル（GitBus と同じ護り: ceiling / C ロケール / ロック残骸の自己回復） ---
    def _env(self) -> dict:
        env = dict(os.environ)
        parent = os.path.dirname(os.path.realpath(self.clone)) or "/"
        ceil = env.get("GIT_CEILING_DIRECTORIES")
        env["GIT_CEILING_DIRECTORIES"] = parent + (os.pathsep + ceil if ceil else "")
        env["GIT_DISCOVERY_ACROSS_FILESYSTEM"] = "0"
        env["LC_ALL"] = "C"              # ロック競合の検知は英語メッセージの文字列マッチに頼る
        env["GIT_EDITOR"] = "true"       # rebase --continue がエディタを開かないように
        return env

    _STALE_LOCKS = ("index.lock", "HEAD.lock", "config.lock", "shallow.lock", "packed-refs.lock")

    def _remove_stale_locks(self) -> int:
        removed = 0
        gitdir = os.path.join(self.clone, ".git")
        now = time.time()
        for name in self._STALE_LOCKS:
            p = os.path.join(gitdir, name)
            try:
                if os.path.isfile(p) and now - os.path.getmtime(p) >= _STATE_LOCK_STALE_SEC:
                    os.remove(p)
                    removed += 1
            except OSError:
                pass
        return removed

    @staticmethod
    def _is_lock_error(p) -> bool:
        err = p.stderr or ""
        return ".lock" in err and ("File exists" in err or "another git process" in err.lower())

    @staticmethod
    def _is_corrupt_error(p) -> bool:
        """git のオブジェクト破損（電源断でのサイズ 0 loose object 等）を示す stderr か。"""
        err = (p.stderr or "").lower()
        return any(m in err for m in _GIT_CORRUPT_MARKERS)

    def _apply_durable_writes(self, cwd: str) -> None:
        """cwd のリポジトリに durable-write 設定（core.fsync/fsyncMethod）を冪等に適用する。
        rename 前にオブジェクト内容を fsync させ、電源断でのサイズ 0 オブジェクト発生を防ぐ。"""
        for key, val in _DURABLE_GIT_CONFIG:
            try:
                cur = subprocess.run(["git", "-C", cwd, "config", "--local", "--get", key],
                                     capture_output=True, text=True, env=self._env())
                if cur.returncode == 0 and cur.stdout.strip() == val:
                    continue
                subprocess.run(["git", "-C", cwd, "config", "--local", key, val],
                               capture_output=True, text=True, env=self._env())
            except OSError:
                pass

    def _harden_remote_durability(self) -> None:
        """リモートがローカルパスの共有リポジトリなら、そちらにも durable-write を効かせる。"""
        try:
            if not self.remote or not os.path.isdir(self.remote):
                return
            probe = subprocess.run(["git", "-C", self.remote, "rev-parse", "--git-dir"],
                                   capture_output=True, text=True, env=self._env())
            if probe.returncode == 0:
                self._apply_durable_writes(self.remote)
        except OSError:
            pass

    def _probe_integrity(self) -> bool:
        """再利用クローンのオブジェクトが健全か軽量に確認する。破損なら False。"""
        try:
            p = subprocess.run(
                ["git", "-C", self.clone, "fsck", "--connectivity-only", "--no-dangling",
                 "--no-reflogs"], capture_output=True, text=True, env=self._env())
        except OSError:
            return False
        return p.returncode == 0 and not self._is_corrupt_error(p)

    def _git(self, *args: str, check: bool = False):
        p = None
        for i in range(_STATE_GIT_RETRIES):
            p = subprocess.run(["git", "-C", self.clone, *args],
                               capture_output=True, text=True, env=self._env())
            if p.returncode == 0 or not self._is_lock_error(p):
                break
            if self._remove_stale_locks() == 0 and i < _STATE_GIT_RETRIES - 1:
                time.sleep(2 ** i)
        if check and p.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} 失敗: {(p.stderr or '').strip()[:300]}")
        return p

    # --- クローンの用意（自前管理クローンのみ再利用。他人の作業ツリーは決して触らない） ---
    def _is_managed(self) -> bool:
        if not os.path.isdir(os.path.join(self.clone, ".git")):
            return False
        top = self._git("rev-parse", "--show-toplevel").stdout.strip()
        if not top or os.path.realpath(top) != os.path.realpath(self.clone):
            return False
        origin = self._git("remote", "get-url", "origin").stdout.strip()
        same_origin = origin == self.remote or (
            bool(origin) and os.path.realpath(origin) == os.path.realpath(self.remote))
        return same_origin and self._git("config", "--get", STATE_GIT_MARKER).stdout.strip() == "1"

    def _recover(self) -> None:
        """前プロセスの異常終了が残したロック残骸・中断 rebase を自己回復する。"""
        self._remove_stale_locks()
        gitdir = os.path.join(self.clone, ".git")
        if any(os.path.isdir(os.path.join(gitdir, d)) for d in ("rebase-merge", "rebase-apply")):
            self._git("rebase", "--abort")
            for d in ("rebase-merge", "rebase-apply"):
                shutil.rmtree(os.path.join(gitdir, d), ignore_errors=True)

    def _setup_worktree(self) -> None:
        if not self._git("config", "user.email").stdout.strip():
            self._git("config", "user.email", "kiro-flow@local")
            self._git("config", "user.name", "kiro-flow")
        self._apply_durable_writes(self.clone)   # 電源断でのサイズ 0 オブジェクト対策（冪等）
        if self.subdir:                  # 自分の名前空間だけを作業ツリーに展開（他者のパスを引かない）
            self._git("sparse-checkout", "init", "--cone")
            self._git("sparse-checkout", "set", self.subdir)
        if self._git("checkout", self.branch).returncode != 0:
            self._git("checkout", "-B", self.branch, check=True)   # 空リポジトリ初回など

    def _ensure_clone(self) -> None:
        self._harden_remote_durability()   # ローカルパスのリモートにも durable write を効かせる
        if self._is_managed():
            self._recover()
            # 電源断でオブジェクトが空/破損した再利用クローンは lock/rebase 回復では直らない。
            # 健全なら再利用、破損していれば捨てて作り直す（真実はローカルバスとリモート側にあり、
            # manifest を失っても次の同期が裁定規則で決定的に再収束する）。
            if self._probe_integrity():
                self._setup_worktree()
                return
            shutil.rmtree(self.clone, ignore_errors=True)
        elif os.path.isdir(self.clone) and os.listdir(self.clone):
            raise RuntimeError(
                f"state_git のクローン先 {self.clone} が管理外の非空ディレクトリです"
                "（作業ツリーを壊さないため中断。空のパスを指定してください）")
        os.makedirs(os.path.dirname(self.clone) or ".", exist_ok=True)
        # blob:none で履歴の実体を引かない（非対応サーバはフィルタ無しへフォールバック）
        for extra in (["--filter=blob:none"], []):
            r = subprocess.run(["git", "clone", "--no-checkout", *extra, self.remote, self.clone],
                               capture_output=True, text=True)
            if r.returncode == 0:
                break
            shutil.rmtree(self.clone, ignore_errors=True)
        if r.returncode != 0:
            if self._is_corrupt_error(r):
                raise RuntimeError(
                    f"state_git 共有リポジトリ {self.remote} 自体のオブジェクトが破損している"
                    f"可能性があります。健全な PC のクローンから復旧してください: "
                    f"{(r.stderr or '').strip()[:300]}")
            raise RuntimeError(f"state_git クローン失敗: {(r.stderr or '').strip()[:300]}")
        self._git("config", STATE_GIT_MARKER, "1")
        self._setup_worktree()

    # --- 3-way 同期（manifest = 前回同期時点の path→sha256 スナップショット） ---
    @property
    def _manifest_path(self) -> str:
        return os.path.join(self.clone, ".git", "kiro-flow-state.json")

    def _load_manifest(self) -> dict:
        try:
            with open(self._manifest_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_manifest(self, manifest: dict) -> None:
        tmp = self._manifest_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, self._manifest_path)

    @staticmethod
    def _excluded(parts: "tuple[str, ...]") -> bool:
        # "." 始まり（.state-git 自身・.gitkeep 等の管理領域）と書きかけの .tmp は同期しない
        return any(s.startswith(".") for s in parts) or parts[-1].endswith(".tmp")

    @staticmethod
    def _remote_wins(rel: str) -> bool:
        """同時変更の裁定: 人の投入口 inbox/（claims を除く）はリモート優先、機械状態はローカル優先。"""
        parts = tuple(rel.split("/"))
        return bool(parts) and parts[0] == "inbox" and "claims" not in parts

    @classmethod
    def _scan(cls, root: str) -> "dict[str, str]":
        """root 配下の同期対象ファイルを {相対パス: sha256} で返す（除外規則は両側で同一）。"""
        out: "dict[str, str]" = {}
        if not os.path.isdir(root):
            return out
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            rel_base = os.path.relpath(base, root)
            for name in files:
                rel = name if rel_base == "." else f"{rel_base}/{name}"
                parts = tuple(rel.replace(os.sep, "/").split("/"))
                if cls._excluded(parts):
                    continue
                p = os.path.join(base, name)
                if os.path.islink(p) or not os.path.isfile(p):
                    continue
                try:
                    with open(p, "rb") as f:
                        out["/".join(parts)] = hashlib.sha256(f.read()).hexdigest()
                except OSError:
                    pass
        return out

    def _remote_root(self) -> str:
        return os.path.join(self.clone, self.subdir) if self.subdir else self.clone

    def _three_way(self) -> "tuple[int, int]":
        """manifest 基準の 3-way でローカル⇔クローンを橋渡しする。(imported, exported) を返す。"""
        base = self._load_manifest()
        lroot, rroot = self.bus_root, self._remote_root()
        local, remote = self._scan(lroot), self._scan(rroot)
        manifest: "dict[str, str]" = {}
        imported = exported = 0
        for rel in sorted(set(base) | set(local) | set(remote)):
            lh, rh, bh = local.get(rel), remote.get(rel), base.get(rel)
            if lh == rh:                      # 一致（双方無し含む）→ そのまま
                if lh is not None:
                    manifest[rel] = lh
                continue
            if rh == bh:                      # ローカルだけが変えた（or 消した）→ export
                take_local = True
            elif lh == bh:                    # リモートだけが変えた → import
                take_local = False
            else:                             # 同時変更 → 決定的裁定
                take_local = not self._remote_wins(rel)
            src, dst, h = (lroot, rroot, lh) if take_local else (rroot, lroot, rh)
            sub = rel.replace("/", os.sep)
            try:
                if h is None:                 # 片側の削除を伝播（gc/cleanup の掃除もリモートへ届く）
                    try:
                        os.remove(os.path.join(dst, sub))
                    except FileNotFoundError:
                        pass
                else:
                    d = os.path.join(dst, sub)
                    os.makedirs(os.path.dirname(d) or ".", exist_ok=True)
                    shutil.copyfile(os.path.join(src, sub), d)
                    manifest[rel] = h
                imported, exported = (imported, exported + 1) if take_local \
                    else (imported + 1, exported)
            except OSError:
                if bh is not None:            # 反映できなかった分は次回また差分として現れるように
                    manifest[rel] = bh
        self._save_manifest(manifest)
        return imported, exported

    # --- push（多重コミッタ吸収: rebase 再試行・コンフリクトは裁定規則で決着・force しない） ---
    def _resolve_rebase(self) -> None:
        """pull --rebase が同一ファイルの同時変更で止まったら、パス種別の裁定で決着して続行する。
        rebase 中は --ours=リモート（upstream）/ --theirs=ローカルのコミット側。"""
        gitdir = os.path.join(self.clone, ".git")
        for _ in range(50):                   # 有限（1 コミットずつしか進まない）
            if not any(os.path.isdir(os.path.join(gitdir, d))
                       for d in ("rebase-merge", "rebase-apply")):
                return
            conflicted = [ln for ln in self._git(
                "diff", "--name-only", "--diff-filter=U").stdout.splitlines() if ln.strip()]
            for path in conflicted:
                rel = path[len(self.subdir) + 1:] if self.subdir and \
                    path.startswith(self.subdir + "/") else path
                side = "--ours" if self._remote_wins(rel) else "--theirs"
                if self._git("checkout", side, "--", path).returncode != 0:
                    self._git("rm", "-q", "--", path)   # add/delete 衝突: 消えた側に合わせる
                self._git("add", "--", path)
            if self._git("rebase", "--continue").returncode != 0 and \
                    self._git("rebase", "--skip").returncode != 0:
                self._git("rebase", "--abort")          # 進められない → 次回の 3-way で再収束
                return

    def _ahead(self) -> int:
        r = self._git("rev-list", "--count", f"origin/{self.branch}..HEAD")
        if r.returncode == 0:
            try:
                return int(r.stdout.strip() or 0)
            except ValueError:
                return 0
        # リモートにブランチが無い（初回）→ ローカルにコミットがあれば push が必要
        return 1 if self._git("rev-parse", "-q", "--verify", "HEAD").returncode == 0 else 0

    def _push(self) -> None:
        for i in range(_STATE_PUSH_RETRIES):
            push = self._git("push", "-u", "origin", self.branch)
            if push.returncode == 0:
                self._last_remote = time.time()
                return
            if self._is_corrupt_error(push):
                raise _StateGitCorrupt()      # 電源断でのローカル破損 → sync 側で作り直す
            self._git("pull", "--rebase", "origin", self.branch)   # 競合 → 取り込んで再試行
            self._resolve_rebase()
            self._last_remote = time.time()
            if i < _STATE_PUSH_RETRIES - 1:
                time.sleep(2 ** i if i < 4 else 16)
        raise RuntimeError(f"state_git push が {self.branch} へ反映できませんでした")

    def _rebuild(self) -> None:
        """破損したクローンを捨て、次の sync で作り直させる（真実はローカルバス＋リモート側）。"""
        log("state_git",
            f"クローン {self.clone} のオブジェクト破損を検知——次回同期で作り直します")
        shutil.rmtree(self.clone, ignore_errors=True)
        self._ready = False

    def sync(self, force: bool = False) -> "tuple[int, int]":
        """双方向同期を 1 回行い (imported, exported) を返す。リモート操作もバス走査も
        interval で律速する（daemon の毎 poll から呼ばれても負荷を一定に保つ）。
        force=True は間隔を待たず同期する（run 終端時の結果共有用）。"""
        now = time.time()
        due = force or self.interval <= 0 or (now - self._last_remote) >= self.interval
        if not due:
            return (0, 0)
        if not self._ready:
            if not force and self.interval > 0 and now - self._last_attempt < self.interval:
                return (0, 0)                 # 不通のリモートへの再クローン連打を防ぐ
            self._last_attempt = now
            self._ensure_clone()
            self._ready = True
        try:
            with _file_lock(self.clone + ".lock"):   # 同一ホストの多重プロセスを直列化
                pull = self._git("pull", "--rebase", "origin", self.branch)
                if pull.returncode != 0 and self._is_corrupt_error(pull):
                    raise _StateGitCorrupt()
                self._resolve_rebase()
                self._last_remote = now
                imported, exported = self._three_way()
                pathspec = self.subdir or "."
                self._git("add", "-A", "--", pathspec)               # 自分の名前空間だけをステージ
                # 空コミットを試みない: unborn ブランチでの失敗 commit は index を汚し以後の pull を壊す
                if self._git("status", "--porcelain", "--", pathspec).stdout.strip():
                    # 未 push の連続 state sync は --amend で 1 コミットに束ねる（push 済み履歴は
                    # 書き換えず、他コミッタのコミットが HEAD のときは通常コミットで積む）
                    amend = ["--amend"] if (self._ahead() > 0 and self._git(
                        "log", "-1", "--format=%s").stdout.strip().startswith(
                            "kiro-flow: state sync")) else []
                    self._git("commit", "-q", *amend, "-m", f"kiro-flow: state sync {now_iso()}")
                if self._ahead() > 0:
                    self._push()
        except _StateGitCorrupt:
            # 電源断でクローンのオブジェクトが壊れた → 捨てて次回作り直す（今回分は次回に持ち越し）
            self._rebuild()
            return (0, 0)
        return imported, exported


# バス単位で管理クローンを再利用する（daemon の毎 poll・run の待機ループで作り直さない）
_STATE_GITS: "dict[tuple, StateGit]" = {}


def state_git_for(args) -> "StateGit | None":
    """state_git 設定時のみ StateGit を返す。GitBus（--git）はバス自体が共有 git なので対象外。"""
    if not getattr(args, "state_git", None) or getattr(args, "git", None):
        return None
    bus_root = os.path.abspath(args.bus)
    key = (bus_root, args.state_git, args.state_git_branch, args.state_git_subdir)
    if key not in _STATE_GITS:
        _STATE_GITS[key] = StateGit(bus_root, args.state_git, args.state_git_branch,
                                    args.state_git_subdir, args.state_git_interval)
    return _STATE_GITS[key]


def daemon_status_path(bus: Bus) -> str:
    return os.path.join(bus.root, "status.json")


def _daemon_status_fresh_after_sec(args) -> float:
    """リモート viewer が『稼働中』と信じてよい経過秒数の目安。state_git/status の同期間隔
    から書き手（自分の設定を知っている側）が計算し、viewer 側は単純比較だけで済むようにする。
    kiro-project の同名関数（write_status 側）と同じ考え方。"""
    intervals = [v for v in (getattr(args, "state_git_interval", 0.0),
                             getattr(args, "status_interval", 0.0)) if v and v > 0]
    return max([2.0 * v for v in intervals] + [120.0])


def write_daemon_status(args, bus: Bus, daemon_id: str, orchestrators: dict, workers: list) -> None:
    """status.json（生存信号）を書く。state_git（鏡）越しにリモートの kiro-projects-viewer が
    『daemon が今も生きているか』を判定するための最小スナップショット（bus.root 直下）。
    _scan() はバスのツリー全体を走査するため、ここに置くだけで既存の StateGit がそのまま
    同期対象に含める（GitBus 側のような sparse-checkout の追加設定は不要）。
    実イベント（run 終端・生存リース push）のタイミングで呼べば、そのイベントで既に走る
    state_sync/push に相乗りする＝これ単体で追加の push を生まない。

    GitBus（--git）モードでは書かない: GitBus の sparse-checkout は `runs/`/`inbox/`（or
    --git-subdir）しか作業ツリーに展開しないため、bus_root 直下のファイルは対象外の
    パスになり、GitBus.sync_push() の `git add -A` を壊しかねない（state_git と --git は
    元々ここでも相互排他 — state_git_for() と同じ前提）。"""
    if getattr(args, "git", None):
        return
    rec = {
        "host": socket.gethostname(), "pid": os.getpid(), "node_id": daemon_id,
        "orchestrators": len(orchestrators), "workers": len(workers),
        "updated_iso": now_iso(), "fresh_after_sec": _daemon_status_fresh_after_sec(args),
    }
    try:
        p = daemon_status_path(bus)
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def maybe_heartbeat_daemon_status(args, bus: Bus, daemon_id: str, orchestrators: dict,
                                  workers: list) -> None:
    """daemon アイドル中の任意の生存信号更新（`--status-interval`。既定 0＝無効）。
    無効時は status.json に一切触れない＝state_git の commit-if-diff で追加コミットを
    作らない（idle の git 負荷は今日と同じゼロ）。有効時も前回書き込みから
    status_interval 秒経つまでは触らず、書き込み頻度を利用者の指定した間隔に抑える。
    GitBus（--git）モードでは何もしない（write_daemon_status 側の理由と同じ）。"""
    if getattr(args, "git", None):
        return
    interval = float(getattr(args, "status_interval", 0.0) or 0.0)
    if interval <= 0:
        return
    try:
        age = time.time() - os.path.getmtime(daemon_status_path(bus))
    except OSError:
        age = float("inf")     # 未作成 → 書く
    if age >= interval:
        write_daemon_status(args, bus, daemon_id, orchestrators, workers)


def state_git_status_line(args) -> str:
    """起動時に「state_git が有効か・どこへ鏡写しするか」を一行で示す。無効時は理由も出す
    （silent な設定ミス＝バスが見えない原因の切り分けを容易にする）。"""
    if getattr(args, "git", None):
        return "state-git: 無効（--git バス使用時はバス自体が共有 git のため不要）"
    if not getattr(args, "state_git", None):
        return ("state-git: 無効（未設定）。リモート viewer にバスを見せるには kiro-flow.yaml に "
                "state_git を設定し、この daemon がその設定を読めていること（--config か "
                "起動 cwd の .kiro/kiro-flow.yaml）を確認")
    return (f"state-git: 有効 → {args.state_git} subdir={args.state_git_subdir} "
            f"interval={args.state_git_interval}s（バス {os.path.abspath(args.bus)} をリモートへ鏡写し）")


def state_sync(args, force: bool = False) -> None:
    """状態の git 同期（best-effort）。ネットワーク断・リポジトリ不通でもループは殺さず
    ログに残して続行する（run の実行・終端は state_git に一切依存しない）。"""
    sg = state_git_for(args)
    if sg is None:
        return
    try:
        imported, exported = sg.sync(force=force)
        if imported or exported:
            log("state-git", f"同期: import={imported} export={exported}")
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        log("state-git", f"同期失敗（続行）: {e}")

