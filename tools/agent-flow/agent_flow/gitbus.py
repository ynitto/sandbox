from __future__ import annotations
# gitbus.py — 元 agent-flow.py の 1085-1496 行目（機械分割・内容無改変ではなくなった。
# 常駐一本化 P0・W0-6 で転送の実装を agentcore.transport へ委譲した — 設計 §4.1・R1）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
# --------------------------------------------------------------------------
# GitBus — git 共有リポジトリをバスにする（複数 PC 分散）
# --------------------------------------------------------------------------
# 実際の git 転送（clone・pull・push・ロック残骸/中断 rebase/オブジェクト破損からの自己回復・
# durable-write）は agentcore.transport.GitTransport が唯一の実装として持つ（agent-project の
# BoardRepo・agent-amigos の BoardMirror と共通）。GitBus はこのクラスのまま Bus のサブクラスで
# あり続け、run のレイアウト（claims/<node>/<who>.json 等の名前空間化・disjoint 書き込みによる
# 低コンフリクト設計）と、以下の一部メソッド（_git・_probe_integrity・_clone_with_retry・
# _is_corrupt_error 等）はテスト（白箱テスト・monkey-patch 対象）との互換のため GitBus 自身の
# メソッドとして残し、実体は transport インスタンスへ委譲する薄いラッパーにしてある。
from agentcore import transport as _transport  # noqa: E402

CLONE_RETRIES = _transport.CLONE_RETRIES
GIT_LOCK_STALE_SEC = _transport.GIT_LOCK_STALE_SEC
GIT_LOCK_RETRIES = _transport.GIT_LOCK_RETRIES
# agent_flow/stategit.py（flow バスとは別の state_git 鏡写し実装。W0-6 の対象外）がこの共有名前空間
# 経由でまだ参照しているため、削除せずここで再公開しておく（実体は agentcore.transport 側の唯一の定義）。
_DURABLE_GIT_CONFIG = _transport._DURABLE_GIT_CONFIG
_GIT_CORRUPT_MARKERS = _transport._GIT_CORRUPT_MARKERS


class GitBus(Bus):
    """共有 git リポジトリをメッセージバスにする転送実装。

    各ノードは自分専用のクローン（root）で作業し、push/pull で同期する。
    書き込みはノードごとに名前空間化されている（claims/<node>/<who>.json、
    results/<node>.json は勝者のみ、meta/graph/tasks は orchestrator のみ）ため、
    rebase はほぼ disjoint なファイルの取り込みで済みコンフリクトしない。
    push 競合は pull --rebase → 再 push のリトライで吸収する（実体は agentcore.transport）。"""

    def __init__(self, clone_dir: str, run_id: str, remote: str, branch: str = "main",
                 subdir: str = ""):
        # git の作業ツリーは clone_dir。バスのルートはその中の subdir（指定時）。
        self.workdir = clone_dir
        self.subdir = (subdir or "").strip("/")
        bus_root = os.path.join(clone_dir, self.subdir) if self.subdir else clone_dir
        super().__init__(bus_root, run_id)
        self.remote = remote
        self.branch = branch
        self._transport = _transport.GitTransport(
            self.workdir, self.remote, branch=self.branch, subdir=self.subdir,
            sparse_paths=self._sparse_paths(), managed_flag=self.MANAGED_FLAG,
            commit_user_name="agent-flow", commit_user_email="agent-flow@local",
            lock_stale_sec=GIT_LOCK_STALE_SEC,
            on_log=lambda msg: log(os.path.basename(self.workdir), msg))
        self._ensure_clone()

    # sparse checkout で作業ツリーに展開するパス（cone モード）
    def _sparse_paths(self):
        return [self.subdir] if self.subdir else ["runs", "inbox"]

    # 自前管理のバスクローンに付ける目印（git config）。ユーザーのフルチェックアウトを
    # 誤って sparse-checkout で間引かないため、再利用は「この目印を持つ／既に sparse 済みの
    # 自前バスクローン」に限定する（判定の実体は agentcore.transport._is_managed_clone）。
    MANAGED_FLAG = "agent-flow.busclone"

    # --- テストが直接参照/monkey-patch する薄いラッパー（実体は agentcore.transport） ---
    _is_lock_error = staticmethod(_transport.is_lock_error)
    _is_corrupt_error = staticmethod(_transport.is_corrupt_error)

    def _git(self, args, check=True):
        return self._transport.git(*args, check=check)

    def _probe_integrity(self) -> bool:
        return self._transport._probe_integrity()

    def _clone_with_retry(self):
        """初回クローンを指数バックオフでリトライする（実体は
        agentcore.transport.GitTransport._clone_with_retry）。クラス単位で monkey-patch
        されるテスト（破損リモートの診断メッセージ検証）のため GitBus 自身のメソッドとして残す。"""
        return self._transport._clone_with_retry()

    def _ensure_clone(self) -> None:
        # workdir が自前管理の sparse バスクローンなら回復して再利用。そうでなければ新規 clone する。
        self._transport._harden_remote_durability()
        if self._transport._is_managed_clone():
            self._transport._recover_reused_clone()
            # 電源断でオブジェクトが空/破損したクローンは lock/rebase 回復では直らない。
            # 健全性を確認し、破損していれば以下の「作り直し」へ落とす（真実はリモート側）。
            if self._transport._probe_integrity() and self._transport._setup_worktree(strict=False):
                self._transport._ensured = True
                return
            log(os.path.basename(self.workdir),
                f"再利用クローン {self.workdir} を回復できないため作り直します")
            self._transport._reset_clone_dir()
        elif os.path.isdir(self.workdir) and os.listdir(self.workdir):
            # 既存の非空ディレクトリ（ユーザーの作業チェックアウト・親/別リポジトリ等）は上書きせず中断。
            raise RuntimeError(
                f"クローン先 {self.workdir} が空でない既存ディレクトリ（agent-flow 管理外のクローン/作業"
                f"ツリー）です。sparse-checkout で作業ファイルを隠す事故を防ぐため中断します"
                f"（専用の空ディレクトリを --bus に指定してください）。")
        os.makedirs(os.path.dirname(self.workdir) or ".", exist_ok=True)
        # 一過性のネットワーク障害で起動時クローンが即死しないよう、指数バックオフでリトライする。
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
        if not self._transport._is_own_repo_root():
            # clone 後も workdir 自身がリポジトリのルートでなければ、以降の sparse-checkout が
            # 親リポジトリへ波及しうる。安全側に倒して中断する。
            raise RuntimeError(
                f"git clone 後も {self.workdir} がクローンのルートになっていません。"
                "親リポジトリへの sparse-checkout を防ぐため中断します。")
        self._git(["config", self.MANAGED_FLAG, "1"])   # 自前管理クローンの目印
        self._transport._setup_worktree(strict=True)
        self._transport._ensured = True

    def sync_pull(self) -> None:
        # リモートに当該ブランチが無い初回などは黙って無視（transport が破損時の作り直しも担う）。
        self._transport.sync_pull()

    def sync_push(self, msg: str = "agent-flow update") -> None:
        self._transport.sync_push(msg)

    def remove_run(self, run_id: str) -> None:
        # バスサブディレクトリを考慮したリポジトリ相対パスで git rm
        rel = os.path.join(self.subdir, "runs", run_id) if self.subdir else f"runs/{run_id}"
        self._git(["rm", "-r", "-q", "--ignore-unmatch", rel], check=False)
        super().remove_run(run_id)  # 未追跡の残骸も掃除（commit/push は呼び出し側）

    def cleanup_clone(self) -> None:
        """作業後にこのノード専用の sparse-checkout クローンを丸ごと削除する。
        共有リポジトリ本体ではなく、ローカルの作業ツリー（.git を含むクローン）だけを
        対象にする。push 済みのデータはリモートにあるため、消しても情報は失われない。"""
        self._transport.cleanup_clone()


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
