from __future__ import annotations
# gitbus.py — 元 agent-flow.py の 1085-1496 行目（機械分割・内容無改変）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
# --------------------------------------------------------------------------
# GitBus — git 共有リポジトリをバスにする（複数 PC 分散）
# --------------------------------------------------------------------------
# 初回クローンの最大試行回数（push/pull と同じ指数バックオフでリトライ）。
CLONE_RETRIES = 5
# .git 直下のロック（index.lock 等）を「異常終了の残骸」と断定する最小経過秒。
# バスの git 操作は数 KB の JSON の add/commit で数秒あれば終わるため、これ以上
# 更新の無いロックは SIGKILL・電源断・daemon の terminate が残した残骸とみなせる。
# 新しいロックは（同一クローンを共有する）稼働中の git が保持している可能性があるので残す。
GIT_LOCK_STALE_SEC = 30.0
# ロック起因で git コマンドが失敗したときの再試行回数（合間に 1,2,4s バックオフ）。
GIT_LOCK_RETRIES = 4

# --- 電源断によるオブジェクト破損への耐性（durable write / 自己修復） -----------------
# git は既定で loose object を「一時ファイル→rename」で書くが *中身の fsync をしない*。
# PC の定期シャットダウン/電源断が書き込み途中に起きると、rename のメタデータだけがジャーナル
# で残り中身（データブロック）は未フラッシュ——再起動後に **サイズ 0 のオブジェクトファイル**
# が残る（症状: `error: object file .git/objects/xx/yy… is empty` → 以後 add/commit/push/
# checkout が全滅し、バスが同期不能になる）。
#   対策 A（予防）: 管理クローンとローカルパスのリモートに core.fsync=all / fsyncMethod=batch
#     を設定し、rename 前に中身を durable 化する（batch により tiny JSON の書き込みでも安価）。
#   対策 B（自己修復）: それでも壊れたクローンは検知して捨て、リモート（真実）から作り直す。
#     クローンは使い捨て設計（未 push の作業は孤児 reclaim で続きから再実行される）なので安全。
_DURABLE_GIT_CONFIG = (("core.fsync", "all"), ("core.fsyncMethod", "batch"))
# git がオブジェクト破損時に stderr へ出す代表的シグネチャ（LC_ALL=C 固定なので英語で判定できる）。
# 一過性のネットワーク/権限エラー（"unable to access" 等）とは重ならない、破損に固有の語だけに絞る
# （誤検知しても捨てて作り直すだけで情報は失われないが、無駄な再クローンは避けたい）。
_GIT_CORRUPT_MARKERS = (
    "object file", "loose object", "corrupt", "did not match content",
    "bad object", "sha1 mismatch", "unable to unpack", "invalid object",
    "unable to read tree", "unable to read sha1",
)


class GitBus(Bus):
    """共有 git リポジトリをメッセージバスにする転送実装。

    各ノードは自分専用のクローン（root）で作業し、push/pull で同期する。
    書き込みはノードごとに名前空間化されている（claims/<node>/<who>.json、
    results/<node>.json は勝者のみ、meta/graph/tasks は orchestrator のみ）ため、
    rebase はほぼ disjoint なファイルの取り込みで済みコンフリクトしない。
    push 競合は pull --rebase → 再 push のリトライで吸収する。"""

    def __init__(self, clone_dir: str, run_id: str, remote: str, branch: str = "main",
                 subdir: str = ""):
        # git の作業ツリーは clone_dir。バスのルートはその中の subdir（指定時）。
        self.workdir = clone_dir
        self.subdir = (subdir or "").strip("/")
        bus_root = os.path.join(clone_dir, self.subdir) if self.subdir else clone_dir
        super().__init__(bus_root, run_id)
        self.remote = remote
        self.branch = branch
        self._ensure_clone()

    # sparse checkout で作業ツリーに展開するパス（cone モード）
    def _sparse_paths(self):
        return [self.subdir] if self.subdir else ["runs", "inbox"]

    # 自前管理のバスクローンに付ける目印（git config）。ユーザーのフルチェックアウトを
    # 誤って sparse-checkout で間引かないため、再利用は「この目印を持つ／既に sparse 済みの
    # 自前バスクローン」に限定する。
    MANAGED_FLAG = "agent-flow.busclone"

    def _git_env(self) -> dict:
        """`git -C workdir` が workdir の親ディレクトリへ遡ってリポジトリを探さないようにする環境。
        GIT_CEILING_DIRECTORIES に workdir の親を指定し、workdir 直下に .git が無い場合でも
        親リポジトリを掴んで sparse-checkout 等を波及させる事故を物理的に防ぐ（多重防御）。"""
        env = dict(os.environ)
        parent = os.path.dirname(os.path.realpath(self.workdir)) or "/"
        ceil = env.get("GIT_CEILING_DIRECTORIES")
        env["GIT_CEILING_DIRECTORIES"] = parent + (os.pathsep + ceil if ceil else "")
        env["GIT_DISCOVERY_ACROSS_FILESYSTEM"] = "0"
        # ロック競合の検知はエラーメッセージの文字列マッチに頼るため、翻訳されない C ロケールに固定する
        env["LC_ALL"] = "C"
        return env

    # 異常終了した git が .git 直下に残すロックの残骸。これがあると以後の add/commit/
    # checkout/pull が「File exists」で失敗し続け、orchestrator の run 作成（sync_push）が
    # 恒久的に失敗する（→ daemon が同じ要求を再 claim し続ける）原因になる。
    _STALE_GIT_LOCKS = ("index.lock", "HEAD.lock", "config.lock", "shallow.lock",
                        "packed-refs.lock")

    def _remove_stale_git_locks(self, min_age_sec: float) -> int:
        """min_age_sec 以上更新の無いロック残骸を削除して削除数を返す。
        新しいロックは稼働中の git が保持している可能性があるため残す。"""
        removed = 0
        gitdir = os.path.join(self.workdir, ".git")
        now = time.time()
        for name in self._STALE_GIT_LOCKS:
            path = os.path.join(gitdir, name)
            try:
                if os.path.isfile(path) and now - os.path.getmtime(path) >= min_age_sec:
                    os.remove(path)
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
        """git のオブジェクト破損（空/壊れた loose object 等）を示す stderr かを判定する。
        電源断で生じるサイズ 0 のオブジェクトは `error: object file … is empty` 等で表面化する。"""
        err = (p.stderr or "").lower()
        return any(m in err for m in _GIT_CORRUPT_MARKERS)

    def _apply_durable_writes(self, cwd: str) -> None:
        """cwd のリポジトリに durable-write 設定（core.fsync/fsyncMethod）を冪等に適用する。
        rename 前にオブジェクト内容を fsync させ、電源断でのサイズ 0 オブジェクト発生を防ぐ。
        古い git が値を知らなくても無害（未知の core.fsync トークンは無視される）。設定 lock 競合等の
        一過性失敗は無視する（次回起動で再適用される。予防設定が一度失敗しても致命ではない）。"""
        for key, val in _DURABLE_GIT_CONFIG:
            try:
                cur = subprocess.run(["git", "-C", cwd, "config", "--local", "--get", key],
                                     capture_output=True, text=True, env=self._git_env())
                if cur.returncode == 0 and cur.stdout.strip() == val:
                    continue  # 既に設定済み（冪等・書き込み lock を無駄に取らない）
                subprocess.run(["git", "-C", cwd, "config", "--local", key, val],
                               capture_output=True, text=True, env=self._git_env())
            except OSError:
                pass

    def _harden_remote_durability(self) -> None:
        """リモートがローカルパスの共有リポジトリなら、そちらにも durable-write 設定を適用する。
        ローカルパスのリモートへ push すると receive-pack がリモート側にオブジェクトを書くため、
        リモート自身が電源断で壊れる経路を塞ぐ。URL（http/ssh 等）のリモートは触れないので黙って skip。"""
        try:
            if not self.remote or not os.path.isdir(self.remote):
                return
            probe = subprocess.run(["git", "-C", self.remote, "rev-parse", "--git-dir"],
                                   capture_output=True, text=True, env=self._git_env())
            if probe.returncode == 0:
                self._apply_durable_writes(self.remote)
        except OSError:
            pass

    def _probe_integrity(self) -> bool:
        """再利用クローンのオブジェクトが健全か軽量に確認する。破損（空オブジェクト等）なら False。
        --connectivity-only は内容ハッシュ検証を省くが到達可能オブジェクトの読み取りは行うため、
        サイズ 0 の loose object があれば非 0 で失敗する。バス履歴は tiny なので高速。"""
        try:
            p = subprocess.run(
                ["git", "-C", self.workdir, "fsck", "--connectivity-only", "--no-dangling",
                 "--no-reflogs"], capture_output=True, text=True, env=self._git_env())
        except OSError:
            return False
        # fsck 自体が動かない（git dir 破損等）ケースも破損として扱い作り直させる。
        return p.returncode == 0 and not self._is_corrupt_error(p)

    def _rebuild_clone(self) -> None:
        """破損したノード専用クローンを丸ごと捨て、リモート（真実）から作り直す。
        未 push の作業は孤児 reclaim が続きから再実行するため、捨てても情報は失われない。"""
        log(os.path.basename(self.workdir),
            f"クローン {self.workdir} のオブジェクト破損を検知——リモートから作り直します")
        self._reset_clone_dir()
        self._ensure_clone()

    def _git(self, args, check=True):
        p = None
        for i in range(GIT_LOCK_RETRIES):
            p = subprocess.run(["git", "-C", self.workdir] + args, capture_output=True, text=True,
                               env=self._git_env())
            if p.returncode == 0 or not self._is_lock_error(p):
                break
            # ロック起因の失敗: 残骸（十分古い）なら消して即再試行、稼働中の他 git が
            # 保持する新しいロックなら短く待って再試行する。クローンはノード専有が原則
            # なので、恒久的に残るロックはほぼ残骸＝ここで自己回復できる。
            if self._remove_stale_git_locks(GIT_LOCK_STALE_SEC) == 0 and i < GIT_LOCK_RETRIES - 1:
                time.sleep(2 ** i)
        if check and p.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} 失敗: {p.stderr.strip()[:300]}")
        return p

    def _is_own_repo_root(self) -> bool:
        """workdir が「自分自身を root とする git 作業ツリー」か（親リポジトリを掴んでいない）。
        _git_env の ceiling により、workdir 直下に .git が無ければ rev-parse は失敗するので親を拾わない。"""
        top = self._git(["rev-parse", "--show-toplevel"], check=False).stdout.strip()
        return bool(top) and os.path.realpath(top) == os.path.realpath(self.workdir)

    def _origin_matches(self) -> bool:
        origin = self._git(["remote", "get-url", "origin"], check=False).stdout.strip()
        return origin == self.remote or (
            bool(origin) and os.path.realpath(origin) == os.path.realpath(self.remote))

    def _is_managed_bus_clone(self) -> bool:
        """workdir が「agent-flow が管理する self.remote の sparse バスクローン」か。
        これを満たすときのみ sparse-checkout/checkout を適用してよい。ユーザーのフルチェックアウト
        （目印も sparse 設定も無い）を間引いて作業ファイルを隠す事故を防ぐためのガード。"""
        if not self._is_own_repo_root() or not self._origin_matches():
            return False
        # 1) 自前で付けた目印があれば管理クローン
        if self._git(["config", "--get", self.MANAGED_FLAG], check=False).stdout.strip() == "1":
            return True
        # 2) 目印が無くても、既に sparse-checkout 済みなら過去の自前バスクローンとみなし採用（後方互換）。
        #    ユーザーのフルチェックアウトは sparseCheckout 未設定なので false になり、間引かれない。
        sparse = self._git(["config", "--get", "core.sparseCheckout"], check=False).stdout.strip()
        return sparse.lower() == "true"

    def _reset_clone_dir(self) -> None:
        """失敗したクローンが残した部分ディレクトリを消す（再試行が「宛先が空でない」で
        失敗しないように）。対象はクローン専用の workdir のみ。非空の管理外ディレクトリは
        _ensure_clone の事前ガードで既に除外済みなので、ここで消すのは自前のクローン残骸だけ。"""
        shutil.rmtree(self.workdir, ignore_errors=True)

    def _clone_once(self):
        """blob フィルタ付き → 非対応サーバ向けフォールバックの順でクローンを 1 回試みる。"""
        r = subprocess.run(
            ["git", "clone", "--no-checkout", "--filter=blob:none", self.remote, self.workdir],
            capture_output=True, text=True)
        if r.returncode != 0:
            # blob filter 非対応サーバ向けフォールバック（フィルタ版が残した部分クローンを消してから）
            self._reset_clone_dir()
            r = subprocess.run(["git", "clone", "--no-checkout", self.remote, self.workdir],
                               capture_output=True, text=True)
        return r

    def _clone_with_retry(self):
        """初回クローンを指数バックオフ（2,4,8,16s）でリトライする。push/pull と同じ流儀で、
        一過性のネットワーク障害による起動失敗を吸収する。成否は CompletedProcess で返す
        （最終的に失敗なら returncode != 0）。"""
        r = None
        for i in range(CLONE_RETRIES):
            r = self._clone_once()
            if r.returncode == 0:
                return r
            if i < CLONE_RETRIES - 1:
                self._reset_clone_dir()                 # 部分クローンを消してから
                time.sleep(2 ** i if i < 4 else 16)     # バックオフして再試行
        return r

    def _recover_reused_clone(self) -> None:
        """再利用する管理クローンから、前プロセスの異常終了（SIGKILL・電源断・daemon の
        terminate）が残した残骸を回復する。ロック残骸は以後の add/checkout が「File exists」
        で失敗し続ける原因、中断 rebase の残骸は以後の pull --rebase が失敗し続ける原因になる。"""
        self._remove_stale_git_locks(GIT_LOCK_STALE_SEC)
        gitdir = os.path.join(self.workdir, ".git")
        if any(os.path.isdir(os.path.join(gitdir, d)) for d in ("rebase-merge", "rebase-apply")):
            self._git(["rebase", "--abort"], check=False)
            for d in ("rebase-merge", "rebase-apply"):
                shutil.rmtree(os.path.join(gitdir, d), ignore_errors=True)

    def _setup_worktree(self, strict: bool = True) -> bool:
        """コミット用 ID・sparse-checkout・対象ブランチへの checkout を整える。
        strict=False は失敗を False で返す（呼び出し側がクローンを作り直して再試行する）。"""
        # コミット用 ID（未設定環境向けのフォールバック）
        if not self._git(["config", "user.email"], check=False).stdout.strip():
            self._git(["config", "user.email", "agent-flow@local"], check=False)
            self._git(["config", "user.name", "agent-flow"], check=False)
        # durable write（電源断でのサイズ 0 オブジェクト対策）を毎回冪等に適用する
        self._apply_durable_writes(self.workdir)
        # sparse checkout（cone モード）を設定 — バスのサブツリーだけ作業ツリーに置く
        self._git(["sparse-checkout", "init", "--cone"], check=False)
        self._git(["sparse-checkout", "set"] + self._sparse_paths(), check=False)
        # 対象ブランチへ。無ければ作成（空リポジトリ初回も含む）
        if self._git(["checkout", self.branch], check=False).returncode == 0:
            return True
        return self._git(["checkout", "-B", self.branch], check=strict).returncode == 0

    def _ensure_clone(self) -> None:
        # workdir が自前管理の sparse バスクローンなら回復して再利用。そうでなければ新規 clone する。
        # （ユーザーのフルチェックアウトや親/別リポジトリへ sparse-checkout を効かせて作業ツリーを
        #   壊さないため、「自前のバスクローンである」ことを確認してからでないと sparse-checkout に進まない。）
        self._harden_remote_durability()  # ローカルパスのリモートにも durable write を効かせる
        if self._is_managed_bus_clone():
            self._recover_reused_clone()
            # 電源断でオブジェクトが空/破損したクローンは lock/rebase 回復では直らない。
            # 健全性を確認し、破損していれば以下の「作り直し」へ落とす（真実はリモート側）。
            if self._probe_integrity() and self._setup_worktree(strict=False):
                return
            # 回復しても使えない（新しいロックを他プロセスが握ったまま消えた・index 破損・
            # 電源断でのオブジェクト破損等）。バスの真実はリモート側にあり管理クローンは使い捨てに
            # できるため、作り直して自己回復する（作り直せないままだと orchestrator の run 作成が
            # 失敗し続け、daemon が同じ要求を毎 poll 再 claim する無限ループの起点になる）。
            log(os.path.basename(self.workdir),
                f"再利用クローン {self.workdir} を回復できないため作り直します")
            self._reset_clone_dir()
        elif os.path.isdir(self.workdir) and os.listdir(self.workdir):
            # 既存の非空ディレクトリ（ユーザーの作業チェックアウト・親/別リポジトリ等）は上書きせず中断。
            # ここで sparse-checkout すると subdir 以外の追跡ファイルを作業ツリーから隠してしまう。
            raise RuntimeError(
                f"クローン先 {self.workdir} が空でない既存ディレクトリ（agent-flow 管理外のクローン/作業"
                f"ツリー）です。sparse-checkout で作業ファイルを隠す事故を防ぐため中断します"
                f"（専用の空ディレクトリを --bus に指定してください）。")
        os.makedirs(os.path.dirname(self.workdir) or ".", exist_ok=True)
        # sparse checkout: --no-checkout で取得し、必要なパスだけ展開する。
        # 一過性のネットワーク障害で起動時クローンが即死しないよう、push/pull と同様に
        # 指数バックオフでリトライする（分散・委譲構成では各ノードが起動毎に clone するため、
        # ここがネットワーク不安定時の「起動できない」原因になりやすい）。
        r = self._clone_with_retry()
        if r.returncode != 0:
            if self._is_corrupt_error(r):
                # クローンできない破損は「リモート（共有リポジトリ本体）」側にある。クローンは使い捨て
                # なので作り直しでは直らない——健全な PC のクローンから objects を移植するか、
                # `git fsck` で壊れたオブジェクトを特定して復旧する必要がある（README「破損リポジトリの
                # 復旧」参照）。ここでは作り直しループに陥らないよう明確な理由付きで中断する。
                raise RuntimeError(
                    f"共有リポジトリ {self.remote} 自体のオブジェクトが破損している可能性があります"
                    f"（clone がオブジェクト破損で失敗）。健全な PC のクローンから復旧してください: "
                    f"{r.stderr.strip()[:300]}")
            raise RuntimeError(
                f"git clone が {CLONE_RETRIES} 回失敗しました: {r.stderr.strip()[:300]}")
        if not self._is_own_repo_root():
            # clone 後も workdir 自身がリポジトリのルートでなければ、以降の sparse-checkout が
            # 親リポジトリへ波及しうる。安全側に倒して中断する。
            raise RuntimeError(
                f"git clone 後も {self.workdir} がクローンのルートになっていません。"
                "親リポジトリへの sparse-checkout を防ぐため中断します。")
        self._git(["config", self.MANAGED_FLAG, "1"])   # 自前管理クローンの目印
        self._setup_worktree(strict=True)

    def sync_pull(self) -> None:
        # リモートに当該ブランチが無い初回などは黙って無視
        p = self._git(["pull", "--rebase", "origin", self.branch], check=False)
        # 電源断で pull 先クローンのオブジェクトが壊れていれば、作り直してもう一度だけ引き直す。
        if p.returncode != 0 and self._is_corrupt_error(p):
            self._rebuild_clone()
            self._git(["pull", "--rebase", "origin", self.branch], check=False)

    def _commit_pending(self, msg: str) -> None:
        """作業ツリーの未確定分を add + commit する（コミット対象が無ければ何もしない）。
        add/commit がローカルオブジェクト破損で失敗したらクローンを作り直して 1 度だけ再コミットする。"""
        p = self._git(["add", "-A"], check=False)
        if p.returncode != 0 and self._is_corrupt_error(p):
            self._rebuild_clone()
            self._git(["add", "-A"], check=False)
        # commit の失敗は「対象なし」（正常・頻出）と破損を区別する。破損時のみ作り直す。
        c = self._git(["commit", "-m", msg], check=False)
        if c.returncode != 0 and self._is_corrupt_error(c):
            self._rebuild_clone()
            self._git(["add", "-A"], check=False)
            self._git(["commit", "-m", msg], check=False)

    def sync_push(self, msg: str = "agent-flow update") -> None:
        self._commit_pending(msg)
        for i in range(5):
            push = self._git(["push", "-u", "origin", self.branch], check=False)
            if push.returncode == 0:
                return
            # push 中に露見したローカル破損 → 作り直して即座に再 push へ（バックオフ不要）。
            if self._is_corrupt_error(push):
                self._rebuild_clone()
                self._commit_pending(msg)
                continue
            # 競合 → 取り込んで再試行（disjoint なので基本コンフリクトしない）。破損なら作り直す。
            p = self._git(["pull", "--rebase", "origin", self.branch], check=False)
            if p.returncode != 0 and self._is_corrupt_error(p):
                self._rebuild_clone()
                self._commit_pending(msg)
            time.sleep(2 ** i if i < 4 else 16)
        raise RuntimeError(f"git push が {self.branch} へ反映できませんでした")

    def remove_run(self, run_id: str) -> None:
        # バスサブディレクトリを考慮したリポジトリ相対パスで git rm
        rel = os.path.join(self.subdir, "runs", run_id) if self.subdir else f"runs/{run_id}"
        self._git(["rm", "-r", "-q", "--ignore-unmatch", rel], check=False)
        super().remove_run(run_id)  # 未追跡の残骸も掃除（commit/push は呼び出し側）

    def cleanup_clone(self) -> None:
        """作業後にこのノード専用の sparse-checkout クローンを丸ごと削除する。
        共有リポジトリ本体ではなく、ローカルの作業ツリー（.git を含むクローン）だけを
        対象にする。push 済みのデータはリモートにあるため、消しても情報は失われない。"""
        wd = os.path.abspath(self.workdir)
        if os.path.isdir(os.path.join(wd, ".git")):
            shutil.rmtree(wd, ignore_errors=True)


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)


# 作業後に削除する候補の GitBus クローン（make_bus で登録し main の finally で掃除）
_active_clones: list = []


def make_bus(args, node_id: str) -> Bus:
    """--git があれば GitBus（ノードごとに専用クローン）、無ければローカル Bus。"""
    run_id = args.run_id or "_"  # gc 等 run 横断コマンドでは run_id 不要
    if getattr(args, "git", None):
        clone_dir = os.path.join(os.path.abspath(args.bus), _safe(node_id))
        bus = GitBus(clone_dir, run_id, remote=args.git, branch=args.git_branch,
                     subdir=getattr(args, "git_subdir", "") or "")
        _active_clones.append(bus)  # 作業後に cleanup_clone で消す
        return bus
    return Bus(os.path.abspath(args.bus), run_id)


def ensure_bus_root(args) -> None:
    """起動初回にバスフォルダが無ければ作成する。git バスでは各ノードのクローンが
    作業後に削除されてフォルダが空になるため、空ディレクトリを git 管理下に残せるよう
    .gitkeep も置く（既にあれば触らない＝冪等）。"""
    bus_root = os.path.abspath(args.bus)
    os.makedirs(bus_root, exist_ok=True)
    if getattr(args, "git", None):
        keep = os.path.join(bus_root, ".gitkeep")
        if not os.path.exists(keep):
            with open(keep, "w", encoding="utf-8"):
                pass


def cleanup_active_clones() -> None:
    """このプロセスが作った sparse-checkout クローンを作業後にまとめて削除する。"""
    while _active_clones:
        bus = _active_clones.pop()
        try:
            bus.cleanup_clone()
        except Exception:  # noqa: BLE001 — 掃除失敗で終了処理を止めない
            pass

