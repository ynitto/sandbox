from __future__ import annotations
# stategit.py — 元 kiro-project.py の 5901-6602 行目（機械分割・内容無改変）。
# 単体 import しない。kiro_project/__init__.py が共有名前空間へ順に exec 合成する。
# 状態の git 保存・共有（state_git）
# ---------------------------------------------------------------------------
# ワークの内容（<root> コンテナ配下の状態ファイル＝backlog/needs/decisions/journal/…）を共有 git
# リポジトリへ保存し、リモートの kiro-projects-viewer と「結果を見せる／指示を受け取る」を往復する。
#   ・リモート負荷を抑える: 専用の管理クローン（subdir だけの sparse・blob:none）を 1 本再利用し、
#     fetch/push は state_git_interval（既定 300 秒）で律速。push は「共有すべきローカルコミットが
#     あるとき」だけ行い、idle 中は間隔ごとの pull 1 本に収まる。
#   ・多重コミッタ前提: 同一リポジトリには他プログラム（viewer 側の git-file-sync・kiro-flow の
#     git バス・別ホストの kiro-project 等）もコミットする。ステージは自 subdir のみ
#     （`add -A -- <subdir>`）、push 競合は pull --rebase → 再 push の指数バックオフで吸収し、
#     force push は決してしない（他者のコミットを壊さない）。
#   ・双方向: 機械の状態は外へ、人の指示（commands/ ドロップ・inbox/ 投入・needs の記入・
#     policy/charter の編集）は中へ。前回同期スナップショット（manifest）基準の 3-way で
#     「どちらが変えたか」を判定し、同時変更だけを「人の入力パスはリモート優先・機械状態は
#     ローカル優先」の決定的規則で裁定する。
STATE_GIT_MARKER = "kiro-project.stateclone"   # 自前管理クローンの目印（git config）
_STATE_LOCK_STALE_SEC = 30.0                    # これ以上古い .git ロックは残骸とみなし自己回復
_STATE_GIT_RETRIES = 4                          # ロック起因の git 失敗の再試行回数
_STATE_PUSH_RETRIES = 5                         # push 競合の再試行回数（2,4,8,16s バックオフ）
# コンテナ相対パスの同期除外。一時/ホスト局所の状態は共有しない:
#   flow-archive/ … viewer が bus から写し取る run のスナップショット（bus の派生・肥大しうる）
#   claims/       … 原子的クレーム（ホスト内の実行権。同期遅延越しでは排他の意味を持たない）
#   "." 始まりのセグメント … .state-git（クローン自身）や .git などの管理領域
#
# bus/ は **除外しない**。別 PC の viewer（Windows）が run の進捗を見る経路はこれしかないため
# （kiro-project は WSL 側で動き、ファイルシステムを共有しない）。除外すると viewer には
# バックログしか見えず、実行中の run が一切見えない。肥大は kiro-flow の gc で古い run を
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
    """プロジェクト状態 ⇔ 共有 git リポジトリの双方向同期（kiro-flow GitBus と同じ管理クローン流儀）。
    プロジェクトルート自体が git 作業ツリーでない場合のフォールバック（git のルートなら DirectStateGit）。

    真実は常にファイル側（ローカルはプロジェクトルート・リモートは共有リポジトリ）にあり、このクラスは
    「前回同期時点のスナップショット（manifest）」を基準に差分の発生源を判定して橋渡しするだけ。
    クローンや manifest を失っても、次の同期が裁定規則で決定的に再収束させる。"""

    def __init__(self, container: Path, remote: str, branch: str = "main",
                 subdir: str = "kiro-project", interval: float = 300.0,
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
            self._git("config", "user.email", "kiro-project@local")
            self._git("config", "user.name", "kiro-project")
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
                                str(self.clone)], capture_output=True, text=True)
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
        return self.clone / ".git" / "kiro-project-state.json"

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
                self._git("pull", "--rebase", "origin", self.branch)
                self._resolve_rebase()
                self._last_remote = now
            imported, exported = self._three_way()
            pathspec = self.subdir or "."
            self._git("add", "-A", "--", pathspec)               # 自分の名前空間だけをステージ
            # 空コミットを試みない: unborn ブランチでの失敗 commit は index を汚し以後の pull を壊す
            if self._git("status", "--porcelain", "--", pathspec).stdout.strip():
                # 未 push の連続 state sync は --amend で 1 コミットに束ねる（DirectStateGit と同じ）
                amend = ["--amend"] if (self._ahead() > 0 and self._git(
                    "log", "-1", "--format=%s").stdout.strip().startswith(
                        "kiro-project: state sync")) else []
                self._git("commit", "-q", *amend, "-m",
                          f"kiro-project: state sync {datetime.now().isoformat(timespec='seconds')}")
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
    - import: fetch → ff-only を優先し、分岐時のみ rebase する。--autostash は使わない
      （未コミット変更と衝突するなら取り込みを見送る＝人の作業を stash で壊さない）。
    - push: HEAD:branch。reject は fetch + 上記 integrate の再試行で合流（force push しない）。

    同期対象・除外規則は StateGit と同一（bus/ claims/ とドット始まりは同期しない）。
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
                              capture_output=True, text=True, env=self._env())

    def _branch(self) -> str:
        # symbolic-ref は unborn ブランチ（空リポジトリの clone 直後）でも現在ブランチ名を返す
        name = self._git("symbolic-ref", "--short", "-q", "HEAD").stdout.strip()
        return name or self._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "HEAD"

    def _has_remote(self) -> bool:
        return bool(self._git("remote", "get-url", "origin").stdout.strip())

    def _ensure_identity(self) -> None:
        if not self._git("config", "user.email").stdout.strip():
            self._git("config", "user.email", "kiro-project@local")
            self._git("config", "user.name", "kiro-project")

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
        if not head.startswith("kiro-project: state sync"):
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
        return f"kiro-project: state sync {datetime.now().isoformat(timespec='seconds')}"

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
        fd, tmpidx = tempfile.mkstemp(prefix="kiro-project-state-idx-")
        os.close(fd)
        os.remove(tmpidx)                    # git が新規作成する
        env = {**self._env(), "GIT_INDEX_FILE": tmpidx}
        try:
            existing = [t for t in targets if (self.root / t).exists()]
            if not existing:
                return None
            r = subprocess.run(["git", "-C", str(self.root), "add", "--", *existing],
                               capture_output=True, text=True, env=env)
            if r.returncode != 0:
                return None
            tree = subprocess.run(["git", "-C", str(self.root), "write-tree"],
                                  capture_output=True, text=True, env=env).stdout.strip()
            if not tree:
                return None
            r = subprocess.run(["git", "-C", str(self.root), "commit-tree", tree,
                                "-m", self._commit_msg()],
                               capture_output=True, text=True, env=env)
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

        状態 worktree では root=<top>/.kiro-project というサブディレクトリになる。これを噛ませないと
        detached worktree 側で <wt>/journal.md（**トップ直下**）を書いてしまい、状態ファイルを
        丸ごと別のパスへコミットし続ける: 本来の .kiro-project/* は一度もコミットされず永久に
        dirty のまま残り（→ rebase が必ず失敗 → push が永久に non-fast-forward）、代わりに
        トップ直下の journal.md / status.json が毎パス上書きされてコミットが積み上がる。"""
        top = self._git("rev-parse", "--show-toplevel").stdout.strip()
        if not top:
            return ""
        rel = os.path.relpath(os.path.realpath(str(self.root)), os.path.realpath(top))
        return "" if rel == "." else rel

    def _worktree_commit(self, targets: "list[str]", branch: str,
                         amend: bool) -> "str | None":
        """state コミットを detached worktree（専用 index）で組み立てる。
        ルートの index・作業ツリーに触れず、ブランチ更新は CAS（_cas_branch）のみ。
        amend=True なら HEAD（未 push の state sync コミット）へ束ねる（--amend）。
        返り値は新コミット SHA（差分なし・競合検知・失敗は None）。"""
        old = self._git("rev-parse", "HEAD").stdout.strip()
        if not old:
            return None
        sub = self._subdir()
        wt = tempfile.mkdtemp(prefix="kiro-project-state-wt-")
        os.rmdir(wt)                         # worktree add は空でも既存ディレクトリを嫌う
        try:
            if self._git("worktree", "add", "--detach", "--force", wt, old).returncode != 0:
                return None
            base = Path(wt) / sub if sub else Path(wt)   # worktree 内の「自分の名前空間」
            base.mkdir(parents=True, exist_ok=True)

            def _wgit(*args: str):           # pathspec が root 相対で解決されるよう base を cwd にする
                return subprocess.run(["git", "-C", str(base), *args],
                                      capture_output=True, text=True, env=self._env())

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
                           capture_output=True, text=True, env=self._env())
        out: list[str] = []
        for line in r.stdout.splitlines():
            path = line[3:].split(" -> ")[-1].strip().strip('"')
            if path and path != rel and not path.startswith(rel + "/"):
                out.append(path)
        return out

    def _reset_foreign(self, top: str) -> None:
        """状態 worktree の「自分の名前空間の外」に残った未コミット変更を HEAD へ戻す。

        この worktree は kiro-project 専用で、root（<worktree>/.kiro-project）の外を書くのは
        自分だけ（旧レイアウトの残骸・中断した rebase の残留）。人の作業は存在しない。
        放置すると _integrate の rebase/merge が「作業ツリーが汚れている」で必ず失敗し、
        push は non-fast-forward のまま二度と通らない＝**同期が永久に停止する**（実際そうなった）。
        root == top（人のリポジトリ直下で動かす direct モード）では何もしない。"""
        paths = self._foreign_dirty(top)
        if not paths:
            return
        for args in (("reset", "-q", "HEAD", "--"), ("checkout", "-q", "--")):
            subprocess.run(["git", "-C", top, *args, *paths],
                           capture_output=True, text=True, env=self._env())

    def _rebasing(self) -> bool:
        """rebase が進行中か。worktree では .git が **ファイル** なので <root>/.git/rebase-merge を
        直に見ても永遠に一致しない（一度これで誤判定した）。必ず rev-parse --git-path で解決する。"""
        for d in ("rebase-merge", "rebase-apply"):
            p = self._git("rev-parse", "--git-path", d).stdout.strip()
            if p and (Path(p) if os.path.isabs(p) else (self.root / p)).is_dir():
                return True
        return False

    def _resolve_rebase(self, sub: str) -> bool:
        """rebase 中のコンフリクトをパス種別の裁定で決着させて続行する（StateGit と同じ規則:
        人の入力＝リモート優先 / 機械状態＝ローカル優先）。決着できたら True。

        これが無いと 1 ファイル競合した瞬間に abort し、以後 push は永久に non-fast-forward の
        まま＝分散同期が二度と回復しない。rebase 中は --ours=リモート / --theirs=ローカル。
        パスは `:/<path>` （リポジトリルート相対）で渡す: root はサブディレクトリなので
        cwd 相対で渡すと解決に失敗する。"""
        for _ in range(200):                   # 有限（1 コミットずつしか進まない）
            if not self._rebasing():
                return True
            for path in [ln for ln in self._git(
                    "diff", "--name-only", "--diff-filter=U").stdout.splitlines() if ln.strip()]:
                rel = path[len(sub) + 1:] if sub and path.startswith(sub + "/") else path
                side = "--ours" if StateGit._remote_wins(rel) else "--theirs"
                if self._git("checkout", side, "--", f":/{path}").returncode != 0:
                    self._git("rm", "-q", "--", f":/{path}")   # add/delete 衝突: 消えた側に合わせる
                self._git("add", "--", f":/{path}")
            if self._git("rebase", "--continue").returncode != 0 and \
                    self._git("rebase", "--skip").returncode != 0:
                break
        self._git("rebase", "--abort")          # 進められない → 中途半端な rebase を残さない
        return False

    def _integrate(self, branch: str) -> int:
        """origin/<branch> をローカルへ取り込む。ff-only を優先し、分岐時のみ rebase。
        --autostash は使わない: 未コミット変更と衝突するときは取り込みを見送る（壊さない）。
        取り込んだファイル数を返す（見送り・コンフリクト abort は 0）。"""
        if self._git("rev-parse", "-q", "--verify",
                     f"refs/remotes/origin/{branch}").returncode != 0:
            return 0
        top = self._git("rev-parse", "--show-toplevel").stdout.strip()
        if top:
            self._reset_foreign(top)      # 名前空間外の残骸で rebase が詰むのを防ぐ
        before = self._git("rev-parse", "HEAD").stdout.strip()
        if not before:
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
        if local_only == 0:
            ok = self._git("merge", "--ff-only", f"origin/{branch}").returncode == 0
        else:
            ok = self._git("rebase", f"origin/{branch}").returncode == 0
            if not ok:                        # 競合 → 裁定で決着させて続行（abort して諦めない）
                ok = self._resolve_rebase(self._subdir())
        if not ok:
            return 0
        after = self._git("rev-parse", "HEAD").stdout.strip()
        if before == after:
            return 0
        diff = self._git("diff", "--name-only", before, after, "--", ".").stdout
        return len([ln for ln in diff.splitlines() if ln.strip()])

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
        lock = os.path.join(tempfile.gettempdir(),
                            f"kiro-project-sync-{hashlib.sha1(str(self.root).encode()).hexdigest()[:12]}.lock")
        with _file_lock(lock):            # 同一ホストの多重プロセスを直列化
            self._ensure_identity()
            self._ensure_merge_attrs()    # journal の追記同士を union で無衝突マージ
            remote = self._has_remote()
            branch = self._branch()
            due = self.interval <= 0 or (now - self._last_remote) >= self.interval
            imported = 0
            if remote and due:
                self._git("fetch", "-q", "origin", branch)   # 取り込みは _integrate が行う
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
                       capture_output=True, text=True)
    return r.returncode == 0 and os.path.realpath(r.stdout.strip()) == os.path.realpath(str(root))


def _direct_state_git_ok(cfg: "Config") -> bool:
    """direct モード（root のリポジトリへ直接同期）を使ってよいか。

    root 自体が git のトップレベルか、状態 worktree へ逃がしている場合。後者では root は
    worktree 内のサブディレクトリ（<repo>-kiro-state/.kiro-project）になるため _git_toplevel は
    False を返す。それだけを条件にすると **状態 worktree を使った瞬間に分散同期が丸ごと無効化
    される**: state_git_for も project_flow_remote も None になり、origin へ何も push されず、
    別 PC の viewer が状態と run を読む唯一の経路が消える（実際そうなっていた。journal に
    「state-git: 無効（未設定・ルートも git リポジトリでない）」と出続ける）。

    この worktree は kiro-project 専用なので、そこへ自動コミット・push しても
    「無関係なリポジトリを勝手に触らない」という _git_toplevel の防御意図には反しない。"""
    return cfg.state_top is not None or _git_toplevel(cfg.backlog.parent)


def state_git_status_line(cfg: "Config") -> str:
    """起動時に「state_git が有効か・何を鏡写しするか」を一行で示す（silent な設定ミスの切り分け用）。
    注意: これはプロジェクト状態（backlog/needs/…）の鏡写し。kiro-flow のバス（フロータブの run 表示）
    は別途 kiro-flow 側の state_git が担う（本ツールはバスを同期しない）。"""
    root = cfg.backlog.parent
    if _direct_state_git_ok(cfg):
        return (f"state-git: direct モード → {root} 自体の git リポジトリへ直接コミット/push "
                f"interval={cfg.state_git_interval}s")
    if not getattr(cfg, "state_git", None):
        return "state-git: 無効（未設定・ルートも git リポジトリでない）"
    return (f"state-git: 有効 → {cfg.state_git} subdir={cfg.state_git_subdir} "
            f"interval={cfg.state_git_interval}s（プロジェクト状態を鏡写し。kiro-flow のバスは "
            f"kiro-flow 側 state_git が別途担当）")


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


# 実行層 kiro-flow を「プロジェクト単位で kiro-project が起動・監視」する。
# kiro-flow は project の概念を持たず素の単一バス daemon のまま。プロジェクトとリポジトリの対応
# （＝どのバスがどこへ鏡写しするか）は制御層 kiro-project が握り、daemon 起動時に CLI で注入する:
#   kiro-flow --bus <project>/bus --state-git <repo> --state-git-subdir kiro-flow ... daemon ...
# 起動はバスロックで冪等（既に稼働なら二重起動しない）。kiro-project 停止時も detached で残すため、
# in-flight run（gitlab 長期委譲・夜間停止からの孤児再開）は daemon 側でそのまま継続する。
FLOW_STATE_SUBDIR = "kiro-flow"   # プロジェクト固有リポジトリ内の kiro-flow 名前空間（viewer は <clone>/kiro-flow）


