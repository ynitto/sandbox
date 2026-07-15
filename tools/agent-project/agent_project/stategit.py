from __future__ import annotations
# stategit.py — 元 agent-project.py の 5901-6602 行目（機械分割・内容無改変）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
# 状態の git 保存・共有（state_git）
# ---------------------------------------------------------------------------
# ワークの内容（<root> コンテナ配下の状態ファイル＝backlog/needs/decisions/journal/…）を共有 git
# リポジトリへ保存し、リモートの agent-dashboard と「結果を見せる／指示を受け取る」を往復する。
#   ・リモート負荷を抑える: 専用の管理クローン（subdir だけの sparse・blob:none）を 1 本再利用し、
#     fetch/push は state_git_interval（既定 300 秒）で律速。push は「共有すべきローカルコミットが
#     あるとき」だけ行い、idle 中は間隔ごとの pull 1 本に収まる。
#   ・多重コミッタ前提: 同一リポジトリには他プログラム（viewer 側の git-file-sync・agent-flow の
#     git バス・別ホストの agent-project 等）もコミットする。ステージは自 subdir のみ
#     （`add -A -- <subdir>`）、push 競合は pull --rebase → 再 push の指数バックオフで吸収し、
#     force push は決してしない（他者のコミットを壊さない）。
#   ・双方向: 機械の状態は外へ、人の指示（commands/ ドロップ・inbox/ 投入・needs の記入・
#     policy/charter の編集）は中へ。前回同期スナップショット（manifest）基準の 3-way で
#     「どちらが変えたか」を判定し、同時変更だけを「人の入力パスはリモート優先・機械状態は
#     ローカル優先」の決定的規則で裁定する。
STATE_GIT_MARKER = "agent-project.stateclone"   # 自前管理クローンの目印（git config）
# これ以上古い .git ロックは残骸とみなし自己回復する閾値。30 秒では「遅いだけの生きた git」
# （大きな bus/ の add・NFS・巨大リポジトリの checkout）のロックを削除して index を壊す
# 事故が起きうるため、正常な git 操作がまず超えない 5 分に置く（クラッシュ残骸の回収が
# 数分遅れる代償は許容できる。稼働中の git のロックを消す代償は index 破損）。
_STATE_LOCK_STALE_SEC = 300.0
_STATE_GIT_RETRIES = 4                          # ロック起因の git 失敗の再試行回数
_STATE_PUSH_RETRIES = 5                         # push 競合の再試行回数（2,4,8,16s バックオフ）
_STATE_WT_PREFIX = "agent-project-state-wt-"    # state コミット用 detached worktree の一時名 prefix
# コンテナ相対パスの同期除外。一時/ホスト局所の状態は共有しない:
#   flow-archive/ … viewer が bus から写し取る run のスナップショット（bus の派生・肥大しうる）
#   claims/       … 原子的クレーム（ホスト内の実行権。同期遅延越しでは排他の意味を持たない）
#   "." 始まりのセグメント … .state-git（クローン自身）や .git などの管理領域
#
# bus/ は **除外しない**。別 PC の viewer（Windows）が run の進捗を見る経路はこれしかないため
# （agent-project は WSL 側で動き、ファイルシステムを共有しない）。除外すると viewer には
# バックログしか見えず、実行中の run が一切見えない。肥大は agent-flow の gc で古い run を
# 掃除して抑える。なお claims/ は bus/runs/<id>/claims/ の形でも segment 判定に掛かるので、
# bus を対象にしても同期されない（遅延越しの排他は意味を持たないため、これは維持する）。
_STATE_EXCLUDE_DIRS = {"flow-archive", "claims"}
# 同時変更（ローカル・リモートの両方が base から変えた）の裁定。人の入力はリモート優先で
# 取りこぼさず、機械状態（backlog/journal/decisions/…）は実行側＝ローカルを正とする。
# repos.{json,yaml,yml} も人が書くレジストリ（charter ## repos の互換入力・手書きが正）なので
# charter.md / policy.md と同じくリモート優先に含める。ただし _meta.generated_from 付きの
# 自動生成 repos.json は、リモート優先で取り込んでも次の run の export_repo_registry が
# charter から再生成するため charter が正のまま保たれる（手書き＝_meta 無しだけが残る）。
_STATE_REMOTE_WINS_DIRS = {"commands", "inbox", "needs"}
# rules.md（プロジェクトルール）も人が書くのが正なのでリモート優先。システムの昇格追記
# （promote_rules）は実行側ローカルで起きるが、同時変更時は人の編集を取りこぼさない側に倒す
# （昇格は冪等なので次パスで再追記される）。
_STATE_REMOTE_WINS_FILES = {"policy.md", "charter.md", "rules.md",
                            "repos.json", "repos.yaml", "repos.yml"}


class StateGit:
    """プロジェクト状態 ⇔ 共有 git リポジトリの双方向同期（agent-flow GitBus と同じ管理クローン流儀）。
    プロジェクトルート自体が git 作業ツリーでない場合のフォールバック（git のルートなら DirectStateGit）。

    真実は常にファイル側（ローカルはプロジェクトルート・リモートは共有リポジトリ）にあり、このクラスは
    「前回同期時点のスナップショット（manifest）」を基準に差分の発生源を判定して橋渡しするだけ。
    クローンや manifest を失っても、次の同期が裁定規則で決定的に再収束させる。"""

    def __init__(self, container: Path, remote: str, branch: str = "main",
                 subdir: str = "agent-project", interval: float = 300.0,
                 clone_dir: "Path | None" = None):
        self.container = Path(container)
        self.remote = remote
        self.branch = branch or "main"
        self.subdir = (subdir or "").strip("/")
        self.interval = max(0.0, interval)
        self.clone = Path(clone_dir) if clone_dir else (self.container / ".state-git")
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
        gitdir = self.clone / ".git"
        now = time.time()
        for name in self._STALE_LOCKS:
            p = gitdir / name
            try:
                if p.is_file() and now - p.stat().st_mtime >= _STATE_LOCK_STALE_SEC:
                    p.unlink()
                    removed += 1
            except OSError:
                pass
        return removed

    @staticmethod
    def _is_lock_error(p) -> bool:
        err = p.stderr or ""
        return ".lock" in err and ("File exists" in err or "another git process" in err.lower())

    def _git(self, *args: str, check: bool = False):
        p = None
        for i in range(_STATE_GIT_RETRIES):
            p = subprocess.run(["git", "-C", str(self.clone), *args],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", env=self._env())
            if p.returncode == 0 or not self._is_lock_error(p):
                break
            if self._remove_stale_locks() == 0 and i < _STATE_GIT_RETRIES - 1:
                time.sleep(2 ** i)
        if check and p.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} 失敗: {(p.stderr or '').strip()[:300]}")
        return p

    # --- クローンの用意（自前管理クローンのみ再利用。他人の作業ツリーは決して触らない） ---
    def _is_managed(self) -> bool:
        if not (self.clone / ".git").is_dir():
            return False
        top = self._git("rev-parse", "--show-toplevel").stdout.strip()
        if not top or os.path.realpath(top) != os.path.realpath(str(self.clone)):
            return False
        origin = self._git("remote", "get-url", "origin").stdout.strip()
        same_origin = origin == self.remote or (
            bool(origin) and os.path.realpath(origin) == os.path.realpath(self.remote))
        return same_origin and self._git("config", "--get", STATE_GIT_MARKER).stdout.strip() == "1"

    def _recover(self) -> None:
        """前プロセスの異常終了が残したロック残骸・中断 rebase を自己回復する。"""
        self._remove_stale_locks()
        gitdir = self.clone / ".git"
        if any((gitdir / d).is_dir() for d in ("rebase-merge", "rebase-apply")):
            self._git("rebase", "--abort")
            for d in ("rebase-merge", "rebase-apply"):
                shutil.rmtree(gitdir / d, ignore_errors=True)

    def _setup_worktree(self) -> None:
        if not self._git("config", "user.email").stdout.strip():
            self._git("config", "user.email", "agent-project@local")
            self._git("config", "user.name", "agent-project")
        if self.subdir:                  # 自分の名前空間だけを作業ツリーに展開（他者のパスを引かない）
            self._git("sparse-checkout", "init", "--cone")
            self._git("sparse-checkout", "set", self.subdir)
        if self._git("checkout", self.branch).returncode != 0:
            self._git("checkout", "-B", self.branch, check=True)   # 空リポジトリ初回など

    def _ensure_clone(self) -> None:
        if self._is_managed():
            self._recover()
            self._setup_worktree()
            return
        if self.clone.is_dir() and any(self.clone.iterdir()):
            raise RuntimeError(
                f"state_git のクローン先 {self.clone} が管理外の非空ディレクトリです"
                "（作業ツリーを壊さないため中断。空のパスを指定してください）")
        self.clone.parent.mkdir(parents=True, exist_ok=True)
        # blob:none で履歴の実体を引かない（非対応サーバはフィルタ無しへフォールバック）
        for extra in (["--filter=blob:none"], []):
            r = subprocess.run(["git", "clone", "--no-checkout", *extra, self.remote,
                                str(self.clone)], capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode == 0:
                break
            shutil.rmtree(self.clone, ignore_errors=True)
        if r.returncode != 0:
            raise RuntimeError(f"state_git クローン失敗: {(r.stderr or '').strip()[:300]}")
        self._git("config", STATE_GIT_MARKER, "1")
        self._setup_worktree()

    # --- 3-way 同期（manifest = 前回同期時点の path→sha256 スナップショット） ---
    @property
    def _manifest_path(self) -> Path:
        return self.clone / ".git" / "agent-project-state.json"

    def _load_manifest(self) -> dict:
        try:
            return json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_manifest(self, manifest: dict) -> None:
        tmp = self._manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self._manifest_path)

    @staticmethod
    def _excluded(rel: Path) -> bool:
        parts = rel.parts
        return any(s.startswith(".") for s in parts) or any(
            s in _STATE_EXCLUDE_DIRS for s in parts[:-1])

    @staticmethod
    def _remote_wins(rel: str) -> bool:
        parts = Path(rel).parts
        return any(s in _STATE_REMOTE_WINS_DIRS for s in parts[:-1]) or (
            parts and parts[-1] in _STATE_REMOTE_WINS_FILES)

    @staticmethod
    def _scan(root: Path) -> "dict[str, str]":
        """root 配下の同期対象ファイルを {相対パス: sha256} で返す（除外規則は両側で同一）。"""
        out: dict[str, str] = {}
        if not root.is_dir():
            return out
        for base, dirs, files in os.walk(root):
            rel_base = Path(base).relative_to(root)
            dirs[:] = [d for d in dirs
                       if not d.startswith(".") and d not in _STATE_EXCLUDE_DIRS]
            for name in files:
                rel = rel_base / name
                if StateGit._excluded(rel):
                    continue
                p = Path(base) / name
                if p.is_symlink() or not p.is_file():
                    continue
                try:
                    out[rel.as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
                except OSError:
                    pass
        return out

    def _remote_root(self) -> Path:
        return self.clone / self.subdir if self.subdir else self.clone

    def _three_way(self) -> "tuple[int, int]":
        """manifest 基準の 3-way でローカル⇔クローンを橋渡しする。(imported, exported) を返す。"""
        base = self._load_manifest()
        lroot, rroot = self.container, self._remote_root()
        local, remote = self._scan(lroot), self._scan(rroot)
        manifest: dict[str, str] = {}
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
            try:
                if h is None:                 # 片側の削除を伝播
                    (dst / rel).unlink(missing_ok=True)
                else:
                    d = dst / rel
                    d.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src / rel, d)
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
        gitdir = self.clone / ".git"
        for _ in range(50):                   # 有限（1 コミットずつしか進まない）
            if not any((gitdir / d).is_dir() for d in ("rebase-merge", "rebase-apply")):
                return
            conflicted = [ln for ln in self._git(
                "diff", "--name-only", "--diff-filter=U").stdout.splitlines() if ln.strip()]
            for path in conflicted:
                rel = path[len(self.subdir) + 1:] if self.subdir and \
                    path.startswith(self.subdir + "/") else path
                side = "--ours" if self._remote_wins(rel) else "--theirs"
                if self._git("checkout", side, "--", path).returncode != 0:
                    # checkout 失敗＝選んだ側にステージが無い add/delete 衝突とは限らない
                    # （権限・sparse 等でも失敗する）。本当に無いときだけ削除に合わせる。
                    # 無条件に rm すると backlog/needs がサイレントに消えて同期で伝播する。
                    stage = "2" if side == "--ours" else "3"
                    stages = self._git("ls-files", "-u", "--", path).stdout
                    if not re.search(rf"\s{stage}\t", stages):
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
            if self._git("push", "-u", "origin", self.branch).returncode == 0:
                self._last_remote = time.time()
                return
            self._git("pull", "--rebase", "origin", self.branch)   # 競合 → 取り込んで再試行
            self._resolve_rebase()
            self._last_remote = time.time()
            if i < _STATE_PUSH_RETRIES - 1:
                time.sleep(2 ** i if i < 4 else 16)
        raise RuntimeError(f"state_git push が {self.branch} へ反映できませんでした")

    def sync(self, force: bool = False) -> "tuple[int, int]":
        """双方向同期を 1 回行い (imported, exported) を返す。リモート操作は interval で律速し、
        force=True は「push すべきものがあれば間隔を待たず押し出す」（run 直後の結果共有用）。"""
        now = time.time()
        if not self._ready:
            if not force and self.interval > 0 and now - self._last_attempt < self.interval:
                return (0, 0)                 # 不通のリモートへの再クローン連打を防ぐ
            self._last_attempt = now
            self._ensure_clone()
            self._ready = True
            self._last_remote = 0.0           # 初回は必ず pull する（停止中の指示を取りこぼさない）
        with _file_lock(str(self.clone) + ".lock"):   # 同一ホストの多重プロセスを直列化
            due = self.interval <= 0 or (now - self._last_remote) >= self.interval
            if due:                           # 取り込み方向の fetch は間隔でのみ（負荷を一定に保つ）
                p = self._git("pull", "--rebase", "origin", self.branch)
                self._resolve_rebase()
                # pull 失敗（ネットワーク断・認証等）で間隔クロックを進めると、次の interval まで
                # リモートの指示（commands/needs）を取りこぼした上、古いリモート観のまま
                # --amend して push と争う。失敗時は進めず、次パスで即再試行する。
                # リモートにブランチがまだ無い初回（couldn't find remote ref）は正常系として進める。
                if p.returncode == 0 or "couldn't find remote ref" in (p.stderr or "").lower():
                    self._last_remote = now
            imported, exported = self._three_way()
            pathspec = self.subdir or "."
            self._git("add", "-A", "--", pathspec)               # 自分の名前空間だけをステージ
            # 空コミットを試みない: unborn ブランチでの失敗 commit は index を汚し以後の pull を壊す
            if self._git("status", "--porcelain", "--", pathspec).stdout.strip():
                # 未 push の連続 state sync は --amend で 1 コミットに束ねる（DirectStateGit と同じ）
                amend = ["--amend"] if (self._ahead() > 0 and self._git(
                    "log", "-1", "--format=%s").stdout.strip().startswith(
                        "agent-project: state sync")) else []
                self._git("commit", "-q", *amend, "-m",
                          f"agent-project: state sync {datetime.now().isoformat(timespec='seconds')}")
            if (due or force) and self._ahead() > 0:
                self._push()
        return imported, exported


class DirectStateGit:
    """プロジェクトルート自体が git 作業ツリー（トップレベル）のときの直接同期（direct モード）。

    管理クローン（StateGit）を介さず、ルートのリポジトリのブランチへ state コミットを積む。
    ただし **ルートのチェックアウト（index・作業ツリー・stash）には触れない**:

    - export: コミットは detached worktree（専用 index）で組み立て、ルートのブランチは
      update-ref の CAS（compare-and-swap）で進める。人が同時にコミットしていたら
      今回の export は見送る（次パスで再試行）＝ index.lock 競合・人のステージの
      巻き込み・コミットの衝突が起きない。
    - import: fetch → 分岐していなければ fast-forward、分岐していれば **一時 index 上の
      plumbing 3-way マージ**で決定的に合流する（rebase は使わない）。コンフリクトは
      パス所有権（人の入力=リモート優先 / 機械状態=ローカル優先）で必ず決着するため
      **統合は失敗しない**。作業ツリーの汚れにも依存しない（rebase 前提の旧実装は
      「tracked だが同期除外」のファイルが 1 つ残るだけで永久に統合できなくなり、
      push が non-fast-forward のまま状態共有が復旧不能になった）。取り込んだ変更は
      ローカルの未コミット変更を上書きしない形で作業ツリー・index に反映する。
    - push: HEAD:branch。reject は fetch + 上記 integrate の再試行で合流（force push しない）。
    - 自己修復: sync のたびに中断 rebase の残骸・古い index.lock・「追跡されてしまった
      同期除外パス」を除去する（前プロセスの強制終了・旧実装・他コミッタの後始末）。

    同期対象・除外規則は StateGit と同一（claims/ とドット始まりは同期しない。bus/ は同期する）。
    リモート（origin）が無ければコミットのみ行う。"""

    def __init__(self, root: Path, interval: float = 300.0):
        self.root = Path(root)
        self.interval = max(0.0, interval)
        self._last_remote = 0.0

    def _env(self) -> dict:
        env = dict(os.environ)
        env["LC_ALL"] = "C"
        env["GIT_EDITOR"] = "true"
        return env

    def _git(self, *args: str):
        return subprocess.run(["git", "-C", str(self.root), *args],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", env=self._env())

    def _branch(self) -> str:
        # symbolic-ref は unborn ブランチ（空リポジトリの clone 直後）でも現在ブランチ名を返す
        name = self._git("symbolic-ref", "--short", "-q", "HEAD").stdout.strip()
        return name or self._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "HEAD"

    def _has_remote(self) -> bool:
        return bool(self._git("remote", "get-url", "origin").stdout.strip())

    def _sync_lock_path(self) -> str:
        """sync 直列化ロックの置き場所。リポジトリの .git（共通ディレクトリ）内に置く——
        以前はホスト局所の tempdir だったため、同じ共有ツリーを Windows と WSL（あるいは
        複数ホストのマウント）から書く構成では互いに一切排他されず、コミット中の worktree
        掃除・CAS 競合・push 詰まりの温床になっていた。gitdir 内なら物理的に同じファイルを
        全書き手がロックする。gitdir が取れないときだけ従来の tempdir に落ちる。"""
        gd = self._git("rev-parse", "--git-common-dir").stdout.strip()
        if gd:
            p = Path(gd) if os.path.isabs(gd) else (self.root / gd)
            try:
                if p.is_dir():
                    return str(p / "agent-project-sync.lock")
            except OSError:
                pass
        return os.path.join(tempfile.gettempdir(),
                            f"agent-project-sync-{hashlib.sha1(str(self.root).encode()).hexdigest()[:12]}.lock")

    def _ensure_identity(self) -> None:
        if not self._git("config", "user.email").stdout.strip():
            self._git("config", "user.email", "agent-project@local")
            self._git("config", "user.name", "agent-project")

    _MERGE_ATTRS = ("journal.md merge=union",)

    def _ensure_merge_attrs(self) -> None:
        """journal.md（追記専用ログ）の EOF 追記同士が _integrate の rebase/merge で
        衝突しないよう、リポジトリローカルの .git/info/attributes に union マージを
        宣言する（冪等）。versioned な .gitattributes はユーザーの領分なので触れない。"""
        gp = self._git("rev-parse", "--git-path", "info/attributes").stdout.strip()
        if not gp:
            return
        attrs = Path(gp) if os.path.isabs(gp) else (self.root / gp)
        try:
            cur = attrs.read_text(encoding="utf-8") if attrs.is_file() else ""
            missing = [ln for ln in self._MERGE_ATTRS if ln not in cur.splitlines()]
            if not missing:
                return
            attrs.parent.mkdir(parents=True, exist_ok=True)
            with attrs.open("a", encoding="utf-8") as f:
                if cur and not cur.endswith("\n"):
                    f.write("\n")
                f.write("\n".join(missing) + "\n")
        except OSError:
            pass

    def _changed_targets(self) -> "list[str]":
        """ルート配下の未コミット変更のうち同期対象（StateGit と同じ除外規則）の相対パス。"""
        top = self._git("rev-parse", "--show-toplevel").stdout.strip()
        out: list[str] = []
        for line in self._git("status", "--porcelain", "--", ".").stdout.splitlines():
            path = line[3:].split(" -> ")[-1].strip().strip('"')
            if not path:
                continue
            try:                          # porcelain はリポジトリトップ相対 → ルート相対へ
                rel = (Path(top) / path).resolve().relative_to(self.root.resolve())
            except ValueError:
                continue
            # 未追跡ディレクトリは `bus/` のようにディレクトリ 1 行で出るため、
            # 全要素を除外規則（ドット始まり・bus/claims）にかける（StateGit と同じ集合）。
            parts = rel.parts
            if any(s.startswith(".") for s in parts) or any(
                    s in _STATE_EXCLUDE_DIRS for s in parts):
                continue
            out.append(str(rel))
        return out

    def _amendable(self, remote: bool) -> bool:
        """HEAD が「未 push の state sync コミット」なら True（--amend で 1 つに束ねる）。
        push 済み履歴は書き換えず、人・他コミッタのコミットが HEAD のときは通常コミットで積む。"""
        head = self._git("log", "-1", "--format=%s").stdout.strip()
        if not head.startswith("agent-project: state sync"):
            return False
        if not remote:
            return True
        r = self._git("rev-list", "--count", f"origin/{self._branch()}..HEAD")
        if r.returncode != 0:
            return True                       # リモートに追跡ブランチが無い＝未 push
        try:
            return int(r.stdout.strip() or 0) > 0
        except ValueError:
            return False

    def _commit_msg(self) -> str:
        return f"agent-project: state sync {datetime.now().isoformat(timespec='seconds')}"

    def _cas_branch(self, branch: str, new: str, old: str) -> bool:
        """ルートのブランチを CAS（update-ref <new> <old>）で進める。old 不一致
        （人の並行コミット）なら失敗＝今回の export は見送り。作業ツリーには触れない。"""
        zero = "0" * 40
        r = self._git("update-ref", f"refs/heads/{branch}", new, old or zero)
        return r.returncode == 0

    def _refresh_index(self, targets: "list[str]") -> None:
        """CAS で進めた新 HEAD に、対象パスの index エントリだけを追随させる
        （作業ツリー内容＝コミット内容なので status が clean に戻る）。他パスのステージは触らない。
        未追跡ディレクトリは porcelain が 1 行（`dir/`）で返すため、ここでファイルへ展開する。"""
        existing: list[str] = []
        gone: list[str] = []
        for t in targets:
            p = self.root / t
            if p.is_dir():
                for base, _dirs, files in os.walk(p):
                    for name in files:
                        existing.append(str((Path(base) / name).relative_to(self.root)))
            elif p.exists():
                existing.append(t)
            else:
                gone.append(t)
        if existing:
            self._git("update-index", "--add", "--", *existing)
        if gone:
            self._git("update-index", "--remove", "--", *gone)

    def _initial_commit(self, targets: "list[str]", branch: str) -> "str | None":
        """unborn ブランチ（コミット 0 件）への最初の state コミット。worktree は作れないため
        一時 index（GIT_INDEX_FILE）で組み立てる。ルートの index には触れない。"""
        fd, tmpidx = tempfile.mkstemp(prefix="agent-project-state-idx-")
        os.close(fd)
        os.remove(tmpidx)                    # git が新規作成する
        env = {**self._env(), "GIT_INDEX_FILE": tmpidx}
        try:
            existing = [t for t in targets if (self.root / t).exists()]
            if not existing:
                return None
            r = subprocess.run(["git", "-C", str(self.root), "add", "--", *existing],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
            if r.returncode != 0:
                return None
            tree = subprocess.run(["git", "-C", str(self.root), "write-tree"],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace", env=env).stdout.strip()
            if not tree:
                return None
            r = subprocess.run(["git", "-C", str(self.root), "commit-tree", tree,
                                "-m", self._commit_msg()],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
            new = r.stdout.strip()
            if r.returncode != 0 or not new:
                return None
            if not self._cas_branch(branch, new, ""):
                return None
            self._refresh_index(targets)
            return new
        finally:
            with contextlib.suppress(OSError):
                os.remove(tmpidx)

    def _subdir(self) -> str:
        """リポジトリトップから見た root の相対パス（root がトップ自身なら ""）。

        状態 worktree では root=<top>/.agent-project というサブディレクトリになる。これを噛ませないと
        detached worktree 側で <wt>/journal.md（**トップ直下**）を書いてしまい、状態ファイルを
        丸ごと別のパスへコミットし続ける: 本来の .agent-project/* は一度もコミットされず永久に
        dirty のまま残り（→ rebase が必ず失敗 → push が永久に non-fast-forward）、代わりに
        トップ直下の journal.md / status.json が毎パス上書きされてコミットが積み上がる。"""
        top = self._git("rev-parse", "--show-toplevel").stdout.strip()
        if not top:
            return ""
        rel = os.path.relpath(os.path.realpath(str(self.root)), os.path.realpath(top))
        return "" if rel == "." else rel

    def _prune_stale_state_worktrees(self) -> None:
        """前プロセスの強制終了が残した専用 worktree（/tmp/agent-project-state-wt-*）を
        unlock → remove → prune で掃除する（冪等・失敗しても本業を止めない）。

        _worktree_commit の finally が走らずに死ぬ（kill -9・タイムアウト・クラッシュ）と
        .git/worktrees/ に登録だけが残る。/tmp の実体が消えていれば prune で片付くが、
        **ロック済み worktree は prune が飛ばす**ため二度と消えず溜まり続け、.git/worktrees/
        が膨れて worktree 操作を鈍らせる。そこで新規作成の前に、自分の prefix に一致する
        登録だけを unlock してから remove/prune する。sync() が _file_lock で同一ホストの
        プロセスを直列化しているので、この時点で生きている自分の worktree は無い＝prefix
        一致は全て残骸と断定でき、人・他ツールの worktree には一切触れない。"""
        r = self._git("worktree", "list", "--porcelain")
        if r.returncode != 0:
            self._git("worktree", "prune")   # list が引けなくても最低限の掃除は試みる
            return
        for block in r.stdout.split("\n\n"):
            path = ""
            for line in block.splitlines():
                if line.startswith("worktree "):
                    path = line[len("worktree "):].strip()
                    break
            if not path or not os.path.basename(path).startswith(_STATE_WT_PREFIX):
                continue                     # メイン作業ツリー・人/他ツールの worktree は対象外
            self._git("worktree", "unlock", path)            # prune/remove を阻むロックを外す
            self._git("worktree", "remove", "--force", path)  # 実体が残っていても登録ごと外す
            shutil.rmtree(path, ignore_errors=True)          # /tmp 側の実体も掃除
        self._git("worktree", "prune")                       # 実体が既に消えた登録を最後に一掃

    def _worktree_commit(self, targets: "list[str]", branch: str,
                         amend: bool) -> "str | None":
        """state コミットを detached worktree（専用 index）で組み立てる。
        ルートの index・作業ツリーに触れず、ブランチ更新は CAS（_cas_branch）のみ。
        amend=True なら HEAD（未 push の state sync コミット）へ束ねる（--amend）。
        返り値は新コミット SHA（差分なし・競合検知・失敗は None）。"""
        self._prune_stale_state_worktrees()  # 前プロセスの残骸 worktree を先に自己回復
        old = self._git("rev-parse", "HEAD").stdout.strip()
        if not old:
            return None
        sub = self._subdir()
        wt = tempfile.mkdtemp(prefix=_STATE_WT_PREFIX)
        os.rmdir(wt)                         # worktree add は空でも既存ディレクトリを嫌う
        try:
            if self._git("worktree", "add", "--detach", "--force", wt, old).returncode != 0:
                return None
            base = Path(wt) / sub if sub else Path(wt)   # worktree 内の「自分の名前空間」
            base.mkdir(parents=True, exist_ok=True)

            def _wgit(*args: str):           # pathspec が root 相対で解決されるよう base を cwd にする
                return subprocess.run(["git", "-C", str(base), *args],
                                      capture_output=True, text=True, encoding="utf-8", errors="replace", env=self._env())

            for rel in targets:              # 現在のルートの内容を worktree へ写す（削除も反映）
                src = self.root / rel
                dst = base / rel
                if src.is_dir():             # 未追跡ディレクトリ（porcelain は dir/ 1 行で返す）
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                elif src.is_file():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                elif dst.exists():
                    _wgit("rm", "-rq", "--ignore-unmatch", "--", rel)
            _wgit("add", "-A", "--", *targets)
            if _wgit("diff", "--cached", "--quiet").returncode == 0:
                return None                  # 差分なし
            # 連続する state sync は未 push の間 --amend で 1 コミットに束ねる
            # （同期のたびに 1 行差分のコミットが積もり、履歴を埋め尽くすのを防ぐ）
            args = (["commit", "-q", "--amend"] if amend else ["commit", "-q"])
            if _wgit(*args, "-m", self._commit_msg()).returncode != 0:
                return None
            new = _wgit("rev-parse", "HEAD").stdout.strip()
            if not new or not self._cas_branch(branch, new, old):
                return None                  # 人の並行コミットに競り負け → 次パスで再試行
            self._refresh_index(targets)
            return new
        finally:
            self._git("worktree", "remove", "--force", wt)
            shutil.rmtree(wt, ignore_errors=True)
            self._git("worktree", "prune")

    def _foreign_dirty(self, top: str) -> "list[str]":
        """同期名前空間（self.root 配下）の**外**に残った未コミット変更（top 相対パス）。
        root == top（プロジェクトルート自体がリポジトリ）のときは「外」が存在しないので空。"""
        root = os.path.realpath(str(self.root))
        if os.path.realpath(top) == root:
            return []
        rel = os.path.relpath(root, top)
        r = subprocess.run(["git", "-C", top, "status", "--porcelain", "--untracked-files=no"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=self._env())
        out: list[str] = []
        for line in r.stdout.splitlines():
            path = line[3:].split(" -> ")[-1].strip().strip('"')
            if path and path != rel and not path.startswith(rel + "/"):
                out.append(path)
        return out

    def _reset_foreign(self, top: str) -> None:
        """状態 worktree の「自分の名前空間の外」に残った未コミット変更を HEAD へ戻す。

        この worktree は agent-project 専用で、root（<worktree>/.agent-project）の外を書くのは
        自分だけ（旧レイアウトの残骸・中断した rebase の残留）。人の作業は存在しない。
        放置すると _integrate の rebase/merge が「作業ツリーが汚れている」で必ず失敗し、
        push は non-fast-forward のまま二度と通らない＝**同期が永久に停止する**（実際そうなった）。
        root == top（人のリポジトリ直下で動かす direct モード）では何もしない。"""
        paths = self._foreign_dirty(top)
        if not paths:
            return
        for args in (("reset", "-q", "HEAD", "--"), ("checkout", "-q", "--")):
            subprocess.run(["git", "-C", top, *args, *paths],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=self._env())

    def _rebasing(self) -> bool:
        """rebase が進行中か。worktree では .git が **ファイル** なので <root>/.git/rebase-merge を
        直に見ても永遠に一致しない（一度これで誤判定した）。必ず rev-parse --git-path で解決する。"""
        for d in ("rebase-merge", "rebase-apply"):
            p = self._git("rev-parse", "--git-path", d).stdout.strip()
            if p and (Path(p) if os.path.isabs(p) else (self.root / p)).is_dir():
                return True
        return False

    def _top(self) -> str:
        """リポジトリのトップレベル（root がサブディレクトリでも index 操作の基準はここ）。
        ls-files は cwd 相対・update-index --cacheinfo はトップ相対、と git のパス規約が
        コマンドごとに違うため、index を触る操作は必ずトップを cwd にしてパスを
        トップ相対に統一する（cwd=root のまま混ぜたのが「状態がトップ直下へ化けて
        コミットされ続ける」バグの温床だった。_subdir の教訓と同根）。"""
        return self._git("rev-parse", "--show-toplevel").stdout.strip() or str(self.root)

    def _top_git(self, *args: str, env: "dict | None" = None):
        return subprocess.run(["git", "-C", self._top(), *args],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", env=env or self._env())

    def _merge_union(self, path: str,
                     stages: "dict[int, tuple[str, str]]") -> "str | None":
        """merge=union 宣言のあるパス（journal.md）の追記同士を無衝突で合流させる。
        3 stage が揃っているときだけ。成功したらマージ後 blob の SHA を返す。"""
        if set(stages) != {1, 2, 3}:
            return None
        attr = self._top_git("check-attr", "merge", "--", path).stdout
        if not attr.strip().endswith("union"):
            return None
        try:
            tmpdir = tempfile.mkdtemp(prefix="agent-project-union-")
        except OSError:
            return None
        try:
            names = {}
            for stage in (1, 2, 3):
                blob = self._cat_blob(stages[stage][1])
                if blob is None:
                    return None
                p = os.path.join(tmpdir, str(stage))
                with open(p, "wb") as f:
                    f.write(blob)
                names[stage] = p
            # merge-file は ours（第1引数）へ結果を書き込む
            r = self._top_git("merge-file", "--union", names[2], names[1], names[3])
            if r.returncode < 0:
                return None
            h = self._top_git("hash-object", "-w", "--", names[2]).stdout.strip()
            return h or None
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _merge_commit(self, old: str, remote_sha: str) -> "str | None":
        """old（ローカル HEAD）と remote_sha を一時 index 上の 3-way で決定的にマージした
        コミット（親 = old, remote_sha）を作る。作業ツリー・実 index には一切触れない。

        両側が同じパスを変えていたら、まず追記ログ（merge=union 宣言）を union で合流させ、
        それ以外はパス所有権（人の入力=リモート優先 / 機械状態=ローカル優先）で必ず決着する
        ＝コンフリクトで止まらない。rebase と違い履歴を書き換えないので、未 push コミットの
        amend 判定や push 済み履歴と干渉しない。返り値はマージコミット SHA
        （git 自体の障害時のみ None）。"""
        base = self._git("merge-base", old, remote_sha).stdout.strip()
        if not base:                          # 無関係な履歴（正本の付け替え等）→ 空ツリー基準
            base = self._git("hash-object", "-t", "tree", os.devnull).stdout.strip()
            if not base:
                return None
        sub = self._subdir()
        fd, tmpidx = tempfile.mkstemp(prefix="agent-project-merge-idx-")
        os.close(fd)
        os.remove(tmpidx)                     # git に新規作成させる
        env = {**self._env(), "GIT_INDEX_FILE": tmpidx}

        def _igit(*args: str):
            return self._top_git(*args, env=env)

        try:
            if _igit("read-tree", "-m", base, old, remote_sha).returncode != 0:
                return None
            # 未解決（両側が同じパスを変えた）を決着する。
            # ls-files -u: "<mode> <sha> <stage>\t<path>"（cwd=トップ → トップ相対）。
            # stage 1=base / 2=ローカル / 3=リモート。
            unmerged: "dict[str, dict[int, tuple[str, str]]]" = {}
            for line in _igit("ls-files", "-u").stdout.splitlines():
                try:
                    meta, path = line.split("\t", 1)
                    mode, sha, stage = meta.split()
                    unmerged.setdefault(path, {})[int(stage)] = (mode, sha)
                except ValueError:
                    continue
            for path, stages in unmerged.items():
                rel = path[len(sub) + 1:] if sub and path.startswith(sub + "/") else path
                union_sha = self._merge_union(path, stages)
                if _igit("update-index", "--force-remove", "--", path).returncode != 0:
                    return None
                if union_sha:
                    mode = stages[2][0]
                    if _igit("update-index", "--add", "--cacheinfo",
                             f"{mode},{union_sha},{path}").returncode != 0:
                        return None
                    continue
                want = 3 if StateGit._remote_wins(rel) else 2
                if want in stages:            # 選んだ側が「削除」なら消えたままにする
                    mode, sha = stages[want]
                    if _igit("update-index", "--add", "--cacheinfo",
                             f"{mode},{sha},{path}").returncode != 0:
                        return None
            tree = _igit("write-tree").stdout.strip()
            if not tree:
                return None
            r = _igit("commit-tree", tree, "-p", old, "-p", remote_sha,
                      "-m", "agent-project: state merge（決定的裁定）")
            return r.stdout.strip() or None
        finally:
            with contextlib.suppress(OSError):
                os.remove(tmpidx)

    def _cat_blob(self, sha: str) -> "bytes | None":
        try:
            r = subprocess.run(["git", "-C", str(self.root), "cat-file", "blob", sha],
                               capture_output=True, env=self._env())
        except OSError:
            return None
        return r.stdout if r.returncode == 0 else None

    def _skip_worktree_paths(self) -> "set[str]":
        """sparse checkout が作業ツリーへ出していない（skip-worktree な）パス（トップ相対）。
        そこへはファイルを書かず index だけ更新する（sparse の見え方を壊さない）。"""
        out: "set[str]" = set()
        for line in self._top_git("ls-files", "-t").stdout.splitlines():
            if line.startswith("S "):
                out.add(line[2:])
        return out

    def _materialize(self, old: str, new: str, top: str) -> int:
        """ブランチが old→new へ進んだ後、変わったパスを作業ツリーと index に反映する。

        ローカルの未コミット変更（内容が old と異なるファイル）は上書きしない——人の入力パス
        （commands/needs/charter 等）を除く。上書きしない場合も index は new のエントリへ進める
        （そのファイルは「ローカルで編集中」として次のコミットで拾われる）。取り込んだ
        （＝実際にファイルを書き換えた/消した）数を返す。"""
        raw = self._top_git("diff-tree", "-r", "--raw", "--no-renames", "-z", old, new).stdout
        # -z 形式: ":oldmode newmode oldsha newsha status\0path\0" の繰り返し
        fields = raw.split("\0")
        entries: "list[tuple[str, str, str, str, str]]" = []
        i = 0
        while i + 1 < len(fields):
            head, path = fields[i], fields[i + 1]
            i += 2
            if not head.startswith(":"):
                continue
            parts = head[1:].split()
            if len(parts) >= 5 and path:
                entries.append((parts[1], parts[2], parts[3], parts[4], path))
        if not entries:
            return 0
        sub = self._subdir()
        skip = self._skip_worktree_paths()
        zero = set("0")
        imported = 0
        for newmode, oldsha, newsha, status, path in entries:
            rel = path[len(sub) + 1:] if sub and path.startswith(sub + "/") else path
            f = Path(top) / path
            # ローカル未コミット変更の検出: 実ファイルの内容が old 版と異なるか
            if set(oldsha) == zero:            # old に無い（リモートの新規追加）
                dirty = f.exists()
                if dirty:
                    h = self._top_git("hash-object", "--", str(f)).stdout.strip()
                    dirty = h != newsha        # 同一内容ならそのまま採用（dirty ではない）
            elif not f.exists():
                # sparse で出していないだけなら未編集。実在すべきものが無いなら「手元で削除中」
                dirty = path not in skip
            else:
                h = self._top_git("hash-object", "--", str(f)).stdout.strip()
                dirty = h != oldsha
            overwrite = StateGit._remote_wins(rel) or not dirty
            if status == "D":
                if overwrite and path not in skip:
                    with contextlib.suppress(OSError):
                        f.unlink()
                    imported += 1
                self._top_git("update-index", "--force-remove", "--", path)
                continue
            if overwrite and path not in skip:
                blob = self._cat_blob(newsha)
                if blob is None:
                    continue
                try:
                    f.parent.mkdir(parents=True, exist_ok=True)
                    f.write_bytes(blob)
                    if newmode == "100755":
                        os.chmod(f, os.stat(f).st_mode | 0o111)
                except OSError:
                    continue
                self._top_git("update-index", "--add", "--", path)
                imported += 1
            else:                              # ローカルの編集を残し、index だけ new へ進める
                self._top_git("update-index", "--add", "--cacheinfo",
                              f"{newmode},{newsha},{path}")
        return imported

    def _integrate(self, branch: str) -> int:
        """origin/<branch> をローカルへ取り込む。分岐していなければ fast-forward、分岐して
        いれば plumbing 3-way マージ（_merge_commit）。どちらも CAS でブランチを進めてから
        作業ツリーへ反映する＝作業ツリーの汚れで統合が止まることはない。
        取り込んだファイル数を返す（リモート未到達・CAS 競り負けは 0）。"""
        if self._git("rev-parse", "-q", "--verify",
                     f"refs/remotes/origin/{branch}").returncode != 0:
            return 0
        top = self._git("rev-parse", "--show-toplevel").stdout.strip()
        if top:
            self._reset_foreign(top)      # 専用 worktree の名前空間外の残骸を掃除（本体 root では no-op）
        old = self._git("rev-parse", "HEAD").stdout.strip()
        if not old:
            return 0
        remote_sha = self._git("rev-parse", f"refs/remotes/origin/{branch}").stdout.strip()
        if not remote_sha or remote_sha == old:
            return 0
        r = self._git("rev-list", "--count", f"HEAD..origin/{branch}")
        try:
            behind = int(r.stdout.strip() or 0) if r.returncode == 0 else 0
        except ValueError:
            behind = 0
        if behind == 0:
            return 0
        r = self._git("rev-list", "--count", f"origin/{branch}..HEAD")
        try:
            local_only = int(r.stdout.strip() or 0) if r.returncode == 0 else 0
        except ValueError:
            local_only = 0
        new = remote_sha if local_only == 0 else self._merge_commit(old, remote_sha)
        if not new:
            return 0
        if not self._cas_branch(branch, new, old):
            return 0                      # 並行更新に競り負け → 次パスで再試行
        return self._materialize(old, new, top or str(self.root))

    _EXCLUDE_PATTERNS = ("claims/", ".state-git*")

    def _ensure_exclude_patterns(self) -> None:
        """同期除外パスをリポジトリローカルの .git/info/exclude に宣言する（冪等）。
        `add -A` 系の他コミッタ（旧 commit_state・viewer）が除外パスを再追跡しないための防壁。
        versioned な .gitignore はユーザーの領分なので触れない。"""
        gp = self._git("rev-parse", "--git-path", "info/exclude").stdout.strip()
        if not gp:
            return
        excl = Path(gp) if os.path.isabs(gp) else (self.root / gp)
        try:
            cur = excl.read_text(encoding="utf-8") if excl.is_file() else ""
            missing = [p for p in self._EXCLUDE_PATTERNS if p not in cur.splitlines()]
            if not missing:
                return
            excl.parent.mkdir(parents=True, exist_ok=True)
            with excl.open("a", encoding="utf-8") as f:
                if cur and not cur.endswith("\n"):
                    f.write("\n")
                f.write("\n".join(missing) + "\n")
        except OSError:
            pass

    def _untrack_excluded(self, branch: str) -> int:
        """「追跡されてしまった同期除外パス」（claims/・ドット始まり）を追跡から外す（自己修復）。

        旧実装・他コミッタ（viewer / agent-flow の管理クローン残骸）がこれらを一度コミットすると、
        以後こちらは絶対にコミットしないため **「tracked だが commit されない変更」が永久に残り**、
        作業ツリー依存の統合（旧 rebase 実装）を二度と通らなくした。plumbing 統合は影響を
        受けないが、status を汚し人の診断を誤らせるので追跡から外す。ブランチへの反映は
        plumbing（一時 index → commit-tree → CAS）で行い、作業ツリーのファイル自体は消さない。
        外した数を返す。"""
        sub = self._subdir()
        scope = sub if sub else "."
        tracked: "list[str]" = []
        for path in self._top_git("ls-files", "-z", "--", scope).stdout.split("\0"):
            if not path:
                continue
            rel = path[len(sub) + 1:] if sub and path.startswith(sub + "/") else path
            parts = Path(rel).parts
            # claims/（ホスト局所の実行権）とドット始まり（.state-git 残骸等）だけを外す。
            # flow-archive/ は viewer が所有・コミットする名前空間なので追跡のまま残す。
            if any(s.startswith(".") for s in parts) or any(
                    s == "claims" for s in parts[:-1]):
                tracked.append(path)
        self._ensure_exclude_patterns()
        if not tracked:
            return 0
        old = self._git("rev-parse", "HEAD").stdout.strip()
        if not old:
            return 0
        fd, tmpidx = tempfile.mkstemp(prefix="agent-project-untrack-idx-")
        os.close(fd)
        os.remove(tmpidx)
        env = {**self._env(), "GIT_INDEX_FILE": tmpidx}

        def _igit(*args: str):
            return self._top_git(*args, env=env)

        try:
            if _igit("read-tree", old).returncode != 0:
                return 0
            for i in range(0, len(tracked), 100):
                if _igit("update-index", "--force-remove", "--",
                         *tracked[i:i + 100]).returncode != 0:
                    return 0
            tree = _igit("write-tree").stdout.strip()
            if not tree or tree == self._git("rev-parse", f"{old}^{{tree}}").stdout.strip():
                return 0
            new = _igit("commit-tree", tree, "-p", old, "-m",
                        "agent-project: 同期除外パスを追跡から外す（自己修復）").stdout.strip()
            if not new or not self._cas_branch(branch, new, old):
                return 0
        finally:
            with contextlib.suppress(OSError):
                os.remove(tmpidx)
        # 実 index も追随させる（ファイルは残す＝untracked へ。exclude 宣言済みなので再追跡されない）
        for i in range(0, len(tracked), 100):
            self._top_git("update-index", "--force-remove", "--", *tracked[i:i + 100])
        return len(tracked)

    def _self_heal(self, branch: str) -> None:
        """前プロセス・旧実装が残した詰まりの残骸を除去する（冪等・失敗しても本業を止めない）。
        中断 rebase / 古い index.lock / 追跡されてしまった同期除外パス。"""
        if self._rebasing():
            self._git("rebase", "--abort")
        p = self._git("rev-parse", "--git-path", "index.lock").stdout.strip()
        if p:
            lock = Path(p) if os.path.isabs(p) else (self.root / p)
            with contextlib.suppress(OSError):
                if lock.is_file() and time.time() - lock.stat().st_mtime > _STATE_LOCK_STALE_SEC:
                    lock.unlink()
        try:
            self._untrack_excluded(branch)
        except OSError:
            pass

    def _wedge_reason(self, branch: str) -> str:
        """push が通らない理由を一行で述べる（ahead/behind と、rebase を阻む未コミット変更）。"""
        def _n(*rev: str) -> str:
            r = self._git("rev-list", "--count", *rev)
            return (r.stdout.strip() or "?") if r.returncode == 0 else "?"
        parts = [f"origin/{branch} より {_n(f'origin/{branch}..HEAD')} 件先行・"
                 f"{_n(f'HEAD..origin/{branch}')} 件遅れ"]
        top = self._git("rev-parse", "--show-toplevel").stdout.strip()
        if top:
            dirty = self._foreign_dirty(top)
            if dirty:
                parts.append("同期対象外の未コミット変更が rebase を阻んでいる: "
                             + ", ".join(dirty[:5]))
        return "; ".join(parts)

    def sync(self, force: bool = False) -> "tuple[int, int]":
        """双方向同期を 1 回行い (imported, exported) を返す。リモート操作は interval で律速し、
        force=True は「push すべきものがあれば間隔を待たず押し出す」（run 直後の結果共有用）。"""
        now = time.time()
        lock = self._sync_lock_path()
        with _file_lock(lock):            # 同一リポジトリへの多重プロセス書き込みを直列化
            self._ensure_identity()
            self._ensure_merge_attrs()    # journal の追記同士を union で無衝突マージ
            remote = self._has_remote()
            branch = self._branch()
            self._self_heal(branch)       # 強制終了・旧実装・他コミッタの残骸を先に除去
            due = self.interval <= 0 or (now - self._last_remote) >= self.interval
            imported = 0
            if remote and due:
                f = self._git("fetch", "-q", "origin", branch)   # 取り込みは _integrate が行う
                # fetch 失敗で間隔クロックを進めると、次の interval までリモートの指示
                # （commands/needs）を取りこぼす。失敗時は進めず次パスで即再試行する。
                # リモートにブランチがまだ無い初回は正常系として進める。
                if f.returncode == 0 or "couldn't find remote ref" in (f.stderr or "").lower():
                    self._last_remote = now
            targets = self._changed_targets()
            exported = 0
            if targets:
                if self._git("rev-parse", "-q", "--verify", "HEAD").returncode != 0:
                    new = self._initial_commit(targets, branch)
                else:
                    new = self._worktree_commit(targets, branch,
                                                amend=self._amendable(remote))
                if new:
                    exported = len(targets)
            if remote and (due or force):
                imported += self._integrate(branch)
                r = self._git("rev-list", "--count", f"origin/{branch}..HEAD")
                if r.returncode == 0:
                    ahead = (r.stdout.strip() or "0") != "0"
                else:                     # リモートにブランチが無い（初回）→ コミットがあれば push
                    ahead = self._git("rev-parse", "-q", "--verify", "HEAD").returncode == 0
                if ahead:
                    for i in range(_STATE_PUSH_RETRIES):
                        r = self._git("push", "-u", "origin", f"HEAD:{branch}")
                        if r.returncode == 0:
                            self._last_remote = time.time()
                            break
                        self._git("fetch", "-q", "origin", branch)
                        imported += self._integrate(branch)
                        if i < _STATE_PUSH_RETRIES - 1:
                            time.sleep(2 ** i if i < 4 else 16)
                    else:
                        # 「反映できませんでした」だけでは何が詰まっているのか分からず、毎パス
                        # 同じ一行が journal に出続ける。詰まりの正体（取り込めていない／作業ツリーが
                        # 汚れている）を出して、人が最初の一手を打てるようにする。
                        raise RuntimeError(
                            f"state_git push が {branch} へ反映できませんでした: "
                            f"{self._wedge_reason(branch)}")
        return imported, exported


# プロジェクトルート単位で同期器を再利用する（watch 常駐で毎パス作り直さない）
_STATE_GITS: "dict[tuple, object]" = {}


def _git_toplevel(root: Path) -> bool:
    """root 自体が git 作業ツリーのトップレベルか（direct モードの発動条件）。
    リポジトリ内の深いサブディレクトリでは発動させない（無関係リポジトリへの自動コミットを防ぐ）。"""
    if not (root / ".git").exists():
        return False
    r = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode == 0 and os.path.realpath(r.stdout.strip()) == os.path.realpath(str(root))


def _direct_state_git_ok(cfg: "Config") -> bool:
    """direct モード（root のリポジトリへ直接同期）を使ってよいか。

    root 自体が git のトップレベルか、状態 worktree へ逃がしている場合。後者では root は
    worktree 内のサブディレクトリ（<repo>-agent-state/.agent-project）になるため _git_toplevel は
    False を返す。それだけを条件にすると **状態 worktree を使った瞬間に分散同期が丸ごと無効化
    される**: state_git_for も project_flow_remote も None になり、origin へ何も push されず、
    別 PC の viewer が状態と run を読む唯一の経路が消える（実際そうなっていた。journal に
    「state-git: 無効（未設定・ルートも git リポジトリでない）」と出続ける）。

    この worktree は agent-project 専用なので、そこへ自動コミット・push しても
    「無関係なリポジトリを勝手に触らない」という _git_toplevel の防御意図には反しない。"""
    return cfg.state_top is not None or _git_toplevel(cfg.backlog.parent)


def state_git_status_line(cfg: "Config") -> str:
    """起動時に「state_git が有効か・何を鏡写しするか」を一行で示す（silent な設定ミスの切り分け用）。
    注意: これはプロジェクト状態（backlog/needs/…）の鏡写し。agent-flow のバス（フロータブの run 表示）
    は別途 agent-flow 側の state_git が担う（本ツールはバスを同期しない）。"""
    root = cfg.backlog.parent
    if _direct_state_git_ok(cfg):
        return (f"state-git: direct モード → {root} 自体の git リポジトリへ直接コミット/push "
                f"interval={cfg.state_git_interval}s")
    if not getattr(cfg, "state_git", None):
        return "state-git: 無効（未設定・ルートも git リポジトリでない）"
    return (f"state-git: 有効 → {cfg.state_git} subdir={cfg.state_git_subdir} "
            f"interval={cfg.state_git_interval}s（プロジェクト状態を鏡写し。agent-flow のバスは "
            f"agent-flow 側 state_git が別途担当）")


def state_git_for(cfg: "Config") -> "StateGit | DirectStateGit | None":
    root = cfg.backlog.parent
    # direct モード（既定）: ルート自体が git クローン、または状態 worktree の中なら、そのリポジトリへ
    # 直接コミット・push する（_direct_state_git_ok 参照）。
    if _direct_state_git_ok(cfg):
        key = ("direct", str(root))
        if key not in _STATE_GITS:
            _STATE_GITS[key] = DirectStateGit(root, cfg.state_git_interval)
        return _STATE_GITS[key]
    # フォールバック: ルートが git でないときは管理クローン（.state-git）で共有リポジトリへ鏡写しする。
    if not getattr(cfg, "state_git", None):
        return None
    key = (str(root), cfg.state_git, cfg.state_git_branch, cfg.state_git_subdir)
    if key not in _STATE_GITS:
        _STATE_GITS[key] = StateGit(root, cfg.state_git, cfg.state_git_branch,
                                    cfg.state_git_subdir, cfg.state_git_interval,
                                    clone_dir=root / ".state-git")
    return _STATE_GITS[key]


# 実行層 agent-flow を「プロジェクト単位で agent-project が起動・監視」する。
# agent-flow は project の概念を持たず素の単一バス daemon のまま。プロジェクトとリポジトリの対応
# （＝どのバスがどこへ鏡写しするか）は制御層 agent-project が握り、daemon 起動時に CLI で注入する:
#   agent-flow --bus <project>/bus --state-git <repo> --state-git-subdir agent-flow ... daemon ...
# 起動はバスロックで冪等（既に稼働なら二重起動しない）。agent-project 停止時も detached で残すため、
# in-flight run（gitlab 長期委譲・夜間停止からの孤児再開）は daemon 側でそのまま継続する。
FLOW_STATE_SUBDIR = "agent-flow"   # プロジェクト固有リポジトリ内の agent-flow 名前空間（viewer は <clone>/agent-flow）


