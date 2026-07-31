"""agentcore.transport — git 転送層の共通実装（設計 §4.1・R1）。

agent-flow の GitBus に実証されていた護り（stale lock 掃除・中断 rebase の abort・
fsck プローブ・破損時の退避→再クローン→復元・durable-write 設定・clone/push の
指数バックオフリトライ・force push 禁止）を、転送 5 実装（agent-flow の GitBus・
agent-project の StateGit と BoardRepo・agent-amigos の BoardMirror・dashboard の
git.js）の唯一の実装として切り出したもの。

`GitTransport` は「1 つのリモート ⇔ 1 つのローカルクローン」を表す。sparse チェックアウト
（cone モード・`sparse_paths` 指定時）と通常のフルチェックアウト（未指定時）の両方に使える
汎用実装で、GitBus（sparse・run 削除あり）・BoardRepo/BoardMirror（フル・削除なし）の
両方の要求を満たす。独立プロダクトではない: 配布しない・CLI を持たない（R10）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from typing import Optional, Sequence

__all__ = ["GitTransport", "GIT_LOCK_STALE_SEC", "GIT_LOCK_RETRIES", "CLONE_RETRIES",
           "GIT_LOCAL_TIMEOUT_SEC", "GIT_NET_TIMEOUT_SEC", "GIT_TIMEOUT_RC",
           "git_timeout_for", "timed_out_result", "harden_git_env"]

# 初回クローンの最大試行回数（push/pull と同じ指数バックオフでリトライ）。
CLONE_RETRIES = 5
# .git 直下のロック（index.lock 等）を「異常終了の残骸」と断定する最小経過秒。書き手が
# 常駐体 1 プロセスだけになる前提では、これより長寿のロックは常にクラッシュ残骸（設計 §4.1）。
GIT_LOCK_STALE_SEC = 30.0
# ロック起因で git コマンドが失敗したときの再試行回数（合間に 1,2,4s バックオフ）。
GIT_LOCK_RETRIES = 4
# push 競合時の再試行回数（2,4,8,16s バックオフ）。
PUSH_RETRIES = 5

# --- git の強制打ち切り（常駐体のハング防止） -----------------------------------------
# 常駐体のスケジューラ（agent-project `resident/scheduler.py`）は tick を専用スレッドで回し、
# 「強制打ち切りは tick 側が持つサブプロセスの timeout に委ねる」設計になっている（Python
# スレッドは安全に kill できない）。よってここで timeout を切らないと、その前提が成立しない
# ——ネットワーク停止や資格情報待ちで git が固まると tick スレッドが永久にブロックし、
# self-watchdog が常駐体ごと abort → 起動系が再起動 → 同じ所で再び固まる（自己回復しない）。
# ローカル操作（config/rev-parse/add/commit）とネットワーク操作（clone/fetch/pull/push）で
# 正当な所要時間が桁違いなので、上限を分ける。
GIT_LOCAL_TIMEOUT_SEC = 60.0
GIT_NET_TIMEOUT_SEC = 600.0
# timeout で打ち切ったときの returncode（GNU timeout(1) の慣習に合わせる）。
GIT_TIMEOUT_RC = 124
# `remote get-url` / `remote add` はローカル操作なのでここには入れない（短い上限のままにして
# ハング検知を早める）。
_GIT_NET_SUBCOMMANDS = frozenset({"clone", "fetch", "pull", "push", "ls-remote"})


def git_timeout_for(args: "Sequence[str]") -> float:
    """この git 呼び出しに掛けてよい上限秒。ネットワークを触るサブコマンドが含まれていれば
    長い方を返す。

    「先頭がサブコマンド」と決め打たないのは、呼び出し側が `git -C <path> push …` の形で
    渡してくるため——`-C` の**値**（パス）は `-` で始まらないので、先頭走査だと常にパスを
    サブコマンドと読んで push/fetch にローカル上限を掛けてしまう。含有判定にすると
    `log --grep=push` のような偽陽性で上限が伸びるが、伸びる側の誤りは害が無い。"""
    return (GIT_NET_TIMEOUT_SEC if any(a in _GIT_NET_SUBCOMMANDS for a in args)
            else GIT_LOCAL_TIMEOUT_SEC)


def harden_git_env(env: dict) -> dict:
    """常駐体から git を呼ぶときの共通環境を env へ足して返す（破壊的・同じ dict を返す）。

    資格情報を対話で聞かせない。常駐体には端末が無く、聞かれた git は誰も答えられないまま
    待ち続ける——timeout で打ち切れるとはいえ、毎 tick を上限まで浪費するので「失敗して次へ」
    に倒す。ssh も BatchMode で即座に諦めさせる。LC_ALL=C はロック競合・破損検知の文字列
    マッチが英語メッセージ前提であるため、GIT_EDITOR は rebase --continue がエディタを
    開かないため。転送層以外（agent-project の StateGit・agent-flow / agent-amigos の
    gitbus）も同じ護りが要るので、ここを唯一の定義にする。"""
    env["LC_ALL"] = "C"
    env["GIT_EDITOR"] = "true"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = ""        # 空 = ヘルパを起動しない（GUI プロンプトも出さない）
    env["SSH_ASKPASS"] = ""
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    # バックグラウンドの自動メンテナンスを禁じる。commit/fetch の後に git が fork する
    # `gc --auto` / `maintenance run --auto` は呼び出しプロセスより長生きし、状態リポジトリの
    # `.git/objects` へ書き続ける——一時ディレクトリの掃除・worktree の削除と競合して
    # 「Directory not empty」で片付けが失敗する（CI で実際に踏んだ）。gc 自体は禁止しない:
    # autoDetach=false で**同期実行**に倒す（有界・後始末と競合しない）。既存の
    # GIT_CONFIG_COUNT 注入があれば後ろに積む（上書きしない）。
    try:
        base = int(env.get("GIT_CONFIG_COUNT") or 0)
    except ValueError:
        base = 0
    for i, (k, v) in enumerate((("maintenance.auto", "false"),
                                ("gc.autoDetach", "false"))):
        env[f"GIT_CONFIG_KEY_{base + i}"] = k
        env[f"GIT_CONFIG_VALUE_{base + i}"] = v
    env["GIT_CONFIG_COUNT"] = str(base + 2)
    return env


def timed_out_result(cmd: "Sequence[str]", limit: float):
    """timeout で打ち切った git を、成功/失敗を見る既存コードがそのまま扱える形に包む。
    例外で貫通させると `check=False` の呼び出し（存在確認・冪等な config 適用など）まで
    巻き添えで落ちるため、返り値の形を揃える。"""
    return subprocess.CompletedProcess(
        list(cmd), GIT_TIMEOUT_RC, "",
        f"git timeout: {limit:.0f}s を超えたため打ち切りました")


_STALE_GIT_LOCKS = ("index.lock", "HEAD.lock", "config.lock", "shallow.lock",
                     "packed-refs.lock")

# --- 電源断によるオブジェクト破損への耐性（durable write / 自己修復） -----------------
# git は既定で loose object を「一時ファイル→rename」で書くが *中身の fsync をしない*。
# 電源断が書き込み途中に起きると rename のメタデータだけが残り中身は未フラッシュ——
# 再起動後にサイズ 0 のオブジェクトファイルが残る。
#   対策 A（予防）: core.fsync=all / fsyncMethod=batch を設定し rename 前に中身を durable 化。
#   対策 B（自己修復）: それでも壊れたクローンは検知して捨て、リモート（真実）から作り直す。
_DURABLE_GIT_CONFIG = (("core.fsync", "all"), ("core.fsyncMethod", "batch"))
# git がオブジェクト破損時に stderr へ出す代表的シグネチャ（LC_ALL=C 固定なので英語で判定できる）。
_GIT_CORRUPT_MARKERS = (
    "object file", "loose object", "corrupt", "did not match content",
    "bad object", "sha1 mismatch", "unable to unpack", "invalid object",
    "unable to read tree", "unable to read sha1",
)


def is_lock_error(p) -> bool:
    err = p.stderr or ""
    return ".lock" in err and ("File exists" in err or "another git process" in err.lower())


def is_corrupt_error(p) -> bool:
    """git のオブジェクト破損（空/壊れた loose object 等）を示す stderr かを判定する。"""
    err = (p.stderr or "").lower()
    return any(m in err for m in _GIT_CORRUPT_MARKERS)


def backoff_sleep(seconds: float) -> None:
    """リトライのバックオフ待ち。**テストが差し替える唯一の seam**。

    なぜ素の `time.sleep` を直接呼ばないか: リトライ回数やバックオフ量を検証するテストは
    `time.sleep` を差し替えて呼び出しを記録する。ところが `time` は stdlib の共有モジュール
    なので、その差し替えは **CPython の `subprocess` 内部**にも効く——`subprocess.run(timeout=…)`
    はプロセス終了を 0.001 秒から倍々（上限 0.05）でポーリングしており、CPU 高負荷で
    `git clone` が長引くとその sleep が記録に混入して「バックオフ 1 回」の検証が壊れる。
    実際に高負荷時だけ落ちるテストとして表面化した。

    自分のバックオフだけを差し替えられる名前をここに 1 つ置き、リトライ経路はすべてこれを通す。
    """
    time.sleep(seconds)


class GitTransport:
    """1 リモート ⇔ 1 ローカルクローンの git 転送（clone / pull / push / 自己回復）。

    パラメータ:
        workdir: このクローンの作業ツリー（このパス専有——他の作業ツリーと共有しない）。
        remote: リモート URL / パス。
        branch: 同期対象ブランチ。
        subdir: リモートの中で扱う名前空間（sparse 時のパス・commit 対象の pathspec）。
        sparse_paths: 指定すれば cone モード sparse-checkout（このパスだけ作業ツリーに展開）。
            省略時はフルチェックアウト（BoardRepo/BoardMirror のような小さな板リポジトリ向け）。
        managed_flag: このクローンが「agentcore が管理する専用クローン」である目印（git config
            のキー名）。ユーザーの他クローンを誤って sparse-checkout/作り直ししないためのガード。
        commit_user_name/email: 未設定環境向けのコミット ID フォールバック。
        lock_stale_sec: これ以上古いロックは残骸とみなして削除する閾値（秒）。
        interval: sync_pull の間隔律速（秒）。0 なら毎回 pull する。
        on_log: 自己回復イベントの通知コールバック（省略時は何もしない）。
    """

    def __init__(self, workdir: str, remote: str, branch: str = "main", subdir: str = "",
                 sparse_paths: "Optional[Sequence[str]]" = None,
                 managed_flag: str = "agentcore.managed",
                 commit_user_name: str = "agentcore",
                 commit_user_email: str = "agentcore@local",
                 lock_stale_sec: float = GIT_LOCK_STALE_SEC,
                 interval: float = 0.0,
                 on_log: "Optional[callable]" = None):
        self.workdir = workdir
        self.remote = remote
        self.branch = branch or "main"
        self.subdir = (subdir or "").strip("/")
        self.sparse_paths = list(sparse_paths) if sparse_paths else None
        self.managed_flag = managed_flag
        self.commit_user_name = commit_user_name
        self.commit_user_email = commit_user_email
        self.lock_stale_sec = lock_stale_sec
        self.interval = max(0.0, interval)
        self._on_log = on_log
        self._last_pull = 0.0
        self._ensured = False

    def _log(self, msg: str) -> None:
        if self._on_log:
            self._on_log(msg)

    # --- 低レベル git 実行（ceiling ガード・C ロケール・ロック残骸の自己回復） ---
    def _git_env(self) -> dict:
        """`git -C workdir` が workdir の親ディレクトリへ遡ってリポジトリを探さないようにする環境。
        workdir 直下に .git が無い場合でも親リポジトリを掴んで sparse-checkout 等を波及させる
        事故を物理的に防ぐ（多重防御）。"""
        env = dict(os.environ)
        parent = os.path.dirname(os.path.realpath(self.workdir)) or "/"
        ceil = env.get("GIT_CEILING_DIRECTORIES")
        env["GIT_CEILING_DIRECTORIES"] = parent + (os.pathsep + ceil if ceil else "")
        env["GIT_DISCOVERY_ACROSS_FILESYSTEM"] = "0"
        return harden_git_env(env)

    def _remove_stale_git_locks(self, min_age_sec: "Optional[float]" = None) -> int:
        """min_age_sec 以上更新の無いロック残骸を削除して削除数を返す。新しいロックは
        稼働中の git が保持している可能性があるため残す。"""
        min_age_sec = self.lock_stale_sec if min_age_sec is None else min_age_sec
        removed = 0
        gitdir = os.path.join(self.workdir, ".git")
        now = time.time()
        for name in _STALE_GIT_LOCKS:
            path = os.path.join(gitdir, name)
            try:
                if os.path.isfile(path) and now - os.path.getmtime(path) >= min_age_sec:
                    os.remove(path)
                    removed += 1
            except OSError:
                pass
        return removed

    def _apply_durable_writes(self, cwd: str) -> None:
        """cwd のリポジトリに durable-write 設定（core.fsync/fsyncMethod）を冪等に適用する。
        rename 前にオブジェクト内容を fsync させ、電源断でのサイズ 0 オブジェクト発生を防ぐ。"""
        for key, val in _DURABLE_GIT_CONFIG:
            try:
                cur = subprocess.run(["git", "-C", cwd, "config", "--local", "--get", key],
                                     capture_output=True, text=True, encoding="utf-8",
                                     errors="replace", env=self._git_env(),
                                     timeout=GIT_LOCAL_TIMEOUT_SEC)
                if cur.returncode == 0 and cur.stdout.strip() == val:
                    continue  # 既に設定済み（冪等）
                subprocess.run(["git", "-C", cwd, "config", "--local", key, val],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", env=self._git_env(),
                               timeout=GIT_LOCAL_TIMEOUT_SEC)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def _harden_remote_durability(self) -> None:
        """リモートがローカルパスの共有リポジトリなら、そちらにも durable-write を適用する。
        URL（http/ssh 等）のリモートは触れないので黙って skip。"""
        try:
            if not self.remote or not os.path.isdir(self.remote):
                return
            probe = subprocess.run(["git", "-C", self.remote, "rev-parse", "--git-dir"],
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", env=self._git_env(),
                                   timeout=GIT_LOCAL_TIMEOUT_SEC)
            if probe.returncode == 0:
                self._apply_durable_writes(self.remote)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def _probe_integrity(self) -> bool:
        """再利用クローンのオブジェクトが健全か軽量に確認する。破損なら False。"""
        try:
            p = subprocess.run(
                ["git", "-C", self.workdir, "fsck", "--connectivity-only", "--no-dangling",
                 "--no-reflogs"], capture_output=True, text=True, encoding="utf-8",
                errors="replace", env=self._git_env(), timeout=GIT_LOCAL_TIMEOUT_SEC)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return p.returncode == 0 and not is_corrupt_error(p)

    def _git(self, args: "Sequence[str]", check: bool = True):
        p = None
        limit = git_timeout_for(args)
        cmd = ["git", "-C", self.workdir, *args]
        for i in range(GIT_LOCK_RETRIES):
            try:
                p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", env=self._git_env(), timeout=limit)
            except subprocess.TimeoutExpired:
                p = timed_out_result(cmd, limit)
                break  # 打ち切りはロック競合ではない——再試行しても同じだけ待つ
            if p.returncode == 0 or not is_lock_error(p):
                break
            # ロック起因の失敗: 残骸（十分古い）なら消して即再試行、稼働中の他 git が
            # 保持する新しいロックなら短く待って再試行する。
            if self._remove_stale_git_locks() == 0 and i < GIT_LOCK_RETRIES - 1:
                backoff_sleep(2 ** i)
        if check and p.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} 失敗: {(p.stderr or '').strip()[:300]}")
        return p

    def git(self, *args: str, check: bool = True):
        """呼び出し側が transport の管理外の git 操作（例: 個別ファイルの `rm`）を
        同じ護り（ロック残骸自己回復・環境）付きで実行するための公開口。"""
        return self._git(list(args), check=check)

    # --- クローンの用意（自前管理クローンのみ再利用。他人の作業ツリーは決して触らない） ---
    def _is_own_repo_root(self) -> bool:
        top = self._git(["rev-parse", "--show-toplevel"], check=False).stdout.strip()
        return bool(top) and os.path.realpath(top) == os.path.realpath(self.workdir)

    def _origin_matches(self) -> bool:
        origin = self._git(["remote", "get-url", "origin"], check=False).stdout.strip()
        return origin == self.remote or (
            bool(origin) and os.path.realpath(origin) == os.path.realpath(self.remote))

    def _is_managed_clone(self) -> bool:
        """workdir が「agentcore が管理する self.remote のクローン」か。
        これを満たすときのみ sparse-checkout/作り直しを適用してよい。

        目印（managed_flag）が無くても、次のいずれかなら過去の自前クローンとみなし採用する
        （後方互換 — BoardRepo/BoardMirror 等、この移植より前に作られたクローンを「管理外の
        非空ディレクトリ」として拒否してしまわないため。GitBus 由来の判定を踏襲）:
        (a) フルチェックアウト運用（sparse_paths 未指定）で、かつ origin が一致し自分が
            リポジトリのルートである（この専有ディレクトリに他人の作業ツリーが来ることはない）。
        (b) sparse チェックアウト運用で、既に sparse-checkout 済み。
        いずれの場合も ensure_clone 側で目印を事後に付け直す。"""
        if not (os.path.isdir(self.workdir) and os.path.isdir(os.path.join(self.workdir, ".git"))):
            return False
        if not self._is_own_repo_root() or not self._origin_matches():
            return False
        if self._git(["config", "--get", self.managed_flag], check=False).stdout.strip() == "1":
            return True
        if self.sparse_paths is None:
            return True
        sparse = self._git(["config", "--get", "core.sparseCheckout"], check=False).stdout.strip()
        return sparse.lower() == "true"

    def _reset_clone_dir(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)

    def _recover_reused_clone(self) -> None:
        """前プロセスの異常終了が残した残骸を回復する。ロック残骸は以後の add/checkout が
        「File exists」で失敗し続ける原因、中断 rebase の残骸は以後の pull --rebase が
        失敗し続ける原因になる。"""
        self._remove_stale_git_locks()
        gitdir = os.path.join(self.workdir, ".git")
        if any(os.path.isdir(os.path.join(gitdir, d)) for d in ("rebase-merge", "rebase-apply")):
            self._git(["rebase", "--abort"], check=False)
            for d in ("rebase-merge", "rebase-apply"):
                shutil.rmtree(os.path.join(gitdir, d), ignore_errors=True)

    def _salvage_paths(self) -> "list[str]":
        if self.sparse_paths:
            return self.sparse_paths
        return [self.subdir] if self.subdir else [""]

    def _salvage_files(self) -> "Optional[str]":
        """再クローン前に、作業ツリー上の管理対象ファイルを一時ディレクトリへ退避する。
        オブジェクト DB が壊れていてもチェックアウト済みの実ファイルは大抵無事。"""
        try:
            dst_root = tempfile.mkdtemp(prefix="agentcore-salvage-")
        except OSError:
            return None
        copied = 0
        for rel in self._salvage_paths():
            src = os.path.join(self.workdir, rel) if rel else self.workdir
            dst = os.path.join(dst_root, rel) if rel else dst_root
            if not os.path.isdir(src):
                continue
            try:
                shutil.copytree(src, dst, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns(".git"))
                copied += 1
            except OSError:
                pass
        if not copied:
            shutil.rmtree(dst_root, ignore_errors=True)
            return None
        return dst_root

    def _restore_salvaged_files(self, salvage_root: "Optional[str]") -> None:
        """退避したファイルのうち、再クローン後の作業ツリーに存在しないものだけを書き戻す
        （未 push の新規ファイルの救出）。既存ファイルは上書きしない
        ——リモート側の方が新しい可能性があるため。"""
        if not salvage_root:
            return
        restored = 0
        try:
            for dirpath, _dirs, files in os.walk(salvage_root):
                for name in files:
                    src = os.path.join(dirpath, name)
                    rel = os.path.relpath(src, salvage_root)
                    dst = os.path.join(self.workdir, rel)
                    if os.path.exists(dst):
                        continue
                    try:
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        shutil.copy2(src, dst)
                        restored += 1
                    except OSError:
                        pass
        finally:
            shutil.rmtree(salvage_root, ignore_errors=True)
        if restored:
            self._log(f"再クローン後に未 push のファイル {restored} 件を復元しました")

    def _rebuild_clone(self) -> None:
        """破損したクローンを丸ごと捨て、リモート（真実）から作り直す。
        丸ごと捨てると未 push の書き込みまで消えるため、先に退避してから作り直し、
        リモートに存在しないファイルだけを書き戻す。"""
        self._log(f"クローン {self.workdir} のオブジェクト破損を検知——リモートから作り直します")
        salvage = self._salvage_files()
        self._reset_clone_dir()
        self.ensure_clone()
        self._restore_salvaged_files(salvage)

    def _clone_once(self):
        # env は `_git` と同じものを使う——資格情報プロンプト抑止（GIT_TERMINAL_PROMPT=0 /
        # BatchMode）は clone でこそ効く必要があり、LC_ALL=C は失敗理由の文字列判定の前提。
        def _clone(*extra: str):
            cmd = ["git", "clone", "--no-checkout", *extra, self.remote, self.workdir]
            try:
                return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                      errors="replace", env=self._git_env(),
                                      timeout=GIT_NET_TIMEOUT_SEC)
            except subprocess.TimeoutExpired:
                return timed_out_result(cmd, GIT_NET_TIMEOUT_SEC)

        r = _clone("--filter=blob:none")
        if r.returncode != 0:
            # blob filter 非対応サーバ向けフォールバック
            self._reset_clone_dir()
            r = _clone()
        return r

    def _clone_with_retry(self):
        """初回クローンを指数バックオフ（2,4,8,16s）でリトライする。"""
        r = None
        for i in range(CLONE_RETRIES):
            r = self._clone_once()
            if r.returncode == 0:
                return r
            if i < CLONE_RETRIES - 1:
                self._reset_clone_dir()
                backoff_sleep(2 ** i if i < 4 else 16)
        return r

    def _setup_worktree(self, strict: bool = True) -> bool:
        """コミット用 ID・sparse-checkout（指定時）・対象ブランチへの checkout を整える。"""
        if not self._git(["config", "user.email"], check=False).stdout.strip():
            self._git(["config", "user.email", self.commit_user_email], check=False)
            self._git(["config", "user.name", self.commit_user_name], check=False)
        self._apply_durable_writes(self.workdir)
        if self.sparse_paths is not None:
            self._git(["sparse-checkout", "init", "--cone"], check=False)
            self._git(["sparse-checkout", "set"] + self.sparse_paths, check=False)
        if self._git(["checkout", self.branch], check=False).returncode == 0:
            return True
        return self._git(["checkout", "-B", self.branch], check=strict).returncode == 0

    def ensure_clone(self) -> None:
        """クローンを用意する（再利用可能なら回復して使い、そうでなければ新規 clone する）。
        既存の非空ディレクトリ（管理外）は上書きせず中断する。"""
        self._harden_remote_durability()
        if self._is_managed_clone():
            self._recover_reused_clone()
            if self._probe_integrity() and self._setup_worktree(strict=False):
                self._git(["config", self.managed_flag, "1"], check=False)  # 後方互換クローンにも印を付け直す
                self._ensured = True
                return
            self._log(f"再利用クローン {self.workdir} を回復できないため作り直します")
            self._reset_clone_dir()
        elif os.path.isdir(self.workdir) and os.listdir(self.workdir):
            raise RuntimeError(
                f"クローン先 {self.workdir} が空でない既存ディレクトリ（agentcore 管理外の"
                "クローン/作業ツリー）です。事故を防ぐため中断します"
                "（専用の空ディレクトリを指定してください）。")
        os.makedirs(os.path.dirname(self.workdir) or ".", exist_ok=True)
        r = self._clone_with_retry()
        if r.returncode != 0:
            if is_corrupt_error(r):
                raise RuntimeError(
                    f"共有リポジトリ {self.remote} 自体のオブジェクトが破損している可能性が"
                    f"あります（clone がオブジェクト破損で失敗）: {(r.stderr or '').strip()[:300]}")
            raise RuntimeError(
                f"git clone が {CLONE_RETRIES} 回失敗しました: {(r.stderr or '').strip()[:300]}")
        if not self._is_own_repo_root():
            raise RuntimeError(
                f"git clone 後も {self.workdir} がクローンのルートになっていません。"
                "親リポジトリへの sparse-checkout を防ぐため中断します。")
        self._git(["config", self.managed_flag, "1"])
        self._setup_worktree(strict=True)
        self._ensured = True

    def _ensure(self) -> None:
        if not self._ensured:
            self.ensure_clone()

    # --- pull / push ---
    def sync_pull(self, force: bool = False) -> bool:
        """fetch/pull（間隔律速）。呼んだら常に ensure_clone を先に済ませる。
        リモートに当該ブランチが無い初回などは黙って無視する。失敗時は間隔クロックを
        進めない（次回パスで即再試行——リモートの指示の取り込みが遅れないようにする）。
        戻り値は「実際に pull を試みたか」（interval 未到達で skip したら False）。"""
        self._ensure()
        now = time.time()
        if not force and self.interval > 0 and (now - self._last_pull) < self.interval:
            return False
        p = self._git(["pull", "--rebase", "origin", self.branch], check=False)
        if p.returncode != 0 and is_corrupt_error(p):
            self._rebuild_clone()
            p = self._git(["pull", "--rebase", "origin", self.branch], check=False)
        if p.returncode == 0 or "couldn't find remote ref" in (p.stderr or "").lower():
            self._last_pull = now
        return True

    def _commit_pending(self, msg: str) -> None:
        """作業ツリーの未確定分を add + commit する（コミット対象が無ければ何もしない）。
        subdir 指定時はその名前空間だけをステージする（多重コミッタの下で他者のパスを
        巻き込まない）。"""
        args = ["add", "-A", "--", self.subdir] if self.subdir else ["add", "-A"]
        p = self._git(args, check=False)
        if p.returncode != 0 and is_corrupt_error(p):
            self._rebuild_clone()
            p = self._git(args, check=False)
        c = self._git(["commit", "-m", msg], check=False)
        if c.returncode != 0 and is_corrupt_error(c):
            self._rebuild_clone()
            self._git(args, check=False)
            self._git(["commit", "-m", msg], check=False)

    def _ahead(self) -> bool:
        """リモート追跡ブランチより先に進んだコミットがあるか（＝push するものがあるか）。
        リモートにまだブランチが無い初回は rev-list が失敗するので「HEAD があれば push 要」
        と読む（BoardRepo/BoardMirror が持っていた `status --porcelain` の空振り抑止を、
        コミット済み未 push まで含めた形で置き換えたもの）。"""
        r = self._git(["rev-list", "--count", f"origin/{self.branch}..HEAD"], check=False)
        if r.returncode == 0:
            try:
                return int(r.stdout.strip() or 0) > 0
            except ValueError:
                return True
        return self._git(["rev-parse", "-q", "--verify", "HEAD"], check=False).returncode == 0

    def sync_push(self, msg: str = "agentcore update") -> None:
        """add -A && commit && push（push 競合は pull --rebase → 再 push の指数バックオフ。
        force push はしない）。押し出すものが無ければ push 自体を省く——バスは毎パス
        sync_push を呼ぶので、変更の無いパスでネットワークを叩かないため。"""
        self._ensure()
        self._commit_pending(msg)
        if not self._ahead():
            return
        for i in range(PUSH_RETRIES):
            push = self._git(["push", "-u", "origin", self.branch], check=False)
            if push.returncode == 0:
                return
            if is_corrupt_error(push):
                self._rebuild_clone()
                self._commit_pending(msg)
                continue
            p = self._git(["pull", "--rebase", "origin", self.branch], check=False)
            if p.returncode != 0 and is_corrupt_error(p):
                self._rebuild_clone()
                self._commit_pending(msg)
            backoff_sleep(2 ** i if i < 4 else 16)
        raise RuntimeError(f"git push が {self.branch} へ反映できませんでした")

    def cleanup_clone(self) -> None:
        """作業後にこのクローンを丸ごと削除する。push 済みのデータはリモートにあるため、
        消しても情報は失われない。"""
        wd = os.path.abspath(self.workdir)
        if os.path.isdir(os.path.join(wd, ".git")):
            shutil.rmtree(wd, ignore_errors=True)

    def has_status_line(self) -> bool:
        """作業ツリーに未コミットの変更があるか（呼び出し側の判断補助・任意）。"""
        self._ensure()
        return bool(self._git(["status", "--porcelain"], check=False).stdout.strip())
