from __future__ import annotations
# board.py — 委譲公示板（agent-board）への依頼側アクセス。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
#
# agent-project は依頼側（バックログを持ち、重い作業を board へ出す）。板は「リポジトリ＋契約」
# だけで処理を持たない（schemas/board.schema.json）。入札・実行は請負側（agent-flow /
# agent-amigos の board 参加デーモン）が担い、完了したら板の result.json へ書き戻す
# （agent_flow/board.py・agent_amigos/board.py の report_results）。ここではその板を
# ポーリングして post を書き・result を読むだけ（結合はデータ契約のみ・エンジンの中身は
# import しない）。手動投函（`board-offload` サブコマンド）と daemon の自動配線
# （§ decide_location location=board・flow.py の _act_board）の両方がこのモジュールを使う。

try:
    import fcntl  # POSIX（macOS/Linux/WSL・install.sh の対象 OS）
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore

from agentcore import transport as _transport  # noqa: E402 — 共通 git 転送層（設計 §4.1・R1）


class BoardRepo:
    """委譲公示板への依頼側アクセス。ローカル dir / git+<url> の両対応。

    agent-project の並列消費（`_act_batch` の ThreadPoolExecutor）から複数タスクが同時に board を
    叩きうるため、git 操作（clone・pull・push）はプロセス間 flock で直列化する
    （agent-flow / agent-amigos の claim ロックと同じ技法・別実装）。転送の実体は
    `agentcore.transport.GitTransport`（stale lock 掃除・中断 rebase の abort・
    fsck プローブ・破損時の退避→再クローン→復元・durable-write・push 指数バックオフ
    ——force push はしない）。ブランチは board の規約どおり単一 main
    （設計 §4.2 — 会話が無く書き込み頻度が低いためミッション別分離は不要）。"""

    def __init__(self, spec: str, workdir: "str | None" = None):
        spec = str(spec or "").strip()
        self.git = spec.startswith("git+")
        if self.git:
            self.remote = spec[4:]
            base = workdir or os.path.join(
                os.path.expanduser("~/.agents/project-board"),
                hashlib.sha1(self.remote.encode()).hexdigest()[:8])
            self.dir = os.path.abspath(base)
            self._transport = _transport.GitTransport(
                self.dir, self.remote, branch="main",
                managed_flag="agent-project.board",
                commit_user_name="agent-project", commit_user_email="agent-project@local")
        else:
            self.remote = None
            self.dir = os.path.abspath(spec)
            self._transport = None

    def _lock_path(self) -> str:
        h = hashlib.sha1(os.path.realpath(self.dir).encode()).hexdigest()
        d = os.path.join(tempfile.gettempdir(), "agent-project-board-locks")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, f"{h}.lock")

    @contextlib.contextmanager
    def _locked(self):
        if fcntl is None:  # pragma: no cover — 非 POSIX 環境のみ（想定外）
            yield
            return
        f = open(self._lock_path(), "a+")
        try:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        finally:
            f.close()

    def _ensure(self) -> None:
        """dir を用意する（呼び出しは _locked() の中から）。git なら transport.ensure_clone に
        委ねる（未クローン時は clone・再利用時は自己回復。git でなければ delegations/ だけ作る）。"""
        if not self.git:
            os.makedirs(os.path.join(self.dir, "delegations"), exist_ok=True)
            return
        self._transport.ensure_clone()
        os.makedirs(os.path.join(self.dir, "delegations"), exist_ok=True)

    def sync_pull(self, force: bool = False, interval: float = 20.0) -> None:
        """fetch/pull（間隔律速）。ローカル dir なら no-op（毎回最新）。"""
        with self._locked():
            self._ensure()
            if not self.git:
                return
            self._transport.interval = interval
            self._transport.sync_pull(force=force)

    def sync_push(self, msg: str) -> None:
        """add -A && commit && push（push 競合は pull --rebase → 再 push の指数バックオフ。
        force push はしない）。ローカル dir なら no-op。"""
        with self._locked():
            if not self.git:
                return
            self._ensure()
            self._transport.sync_push(msg or "board update")

    def delegation_dir(self, did: str) -> str:
        return os.path.join(self.dir, "delegations", str(did))

    def has_post(self, did: str) -> bool:
        return os.path.exists(os.path.join(self.delegation_dir(did), "post.json"))

    def write_post(self, env: dict) -> bool:
        """post.json を書く（冪等 — 既存なら何もせず False。新規に書けたら True）。
        呼び出し側は True のときだけ sync_push すればよい（無駄な空 commit を作らない・
        同一 id の再投函は同一公示という設計の二重公示防止をここで担保する）。"""
        with self._locked():
            self._ensure()
            path = os.path.join(self.delegation_dir(env["id"]), "post.json")
            if os.path.exists(path):
                return False
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.tmp.{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(env, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            return True

    def read_result(self, did: str) -> "dict | None":
        path = os.path.join(self.delegation_dir(did), "result.json")
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, ValueError):
            return None

    def is_cancelled(self, did: str) -> bool:
        return os.path.exists(os.path.join(self.delegation_dir(did), "cancelled.json"))


def _deleg_id_from_task(tid: str) -> str:
    """タスク id だけから委譲 id を作る（[A-Za-z0-9_-]{1,64}）。delegation_id 未指定時のフォールバック。
    自動配線（_act_board）は常に _board_delegation_id（cfg を使う決定的版）を渡すため、
    これは手動呼び出し・テストでの簡易フォールバックに留まる。"""
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", str(tid or "")).strip("-") or "task"
    return ("dg-" + safe)[:64]


def _board_delegation_id(task: "Task", cfg: "Config") -> str:
    """タスクから委譲 id を決定的に作る（[A-Za-z0-9_-]{1,64}）。(backlog, task.id, retries, rev) が
    同じなら同じ id になる＝再投函は同一公示（冪等・二重公示防止。agent-flow の _req_id_for と
    同じ再試行契約 — retries/rev が変われば新しい委譲になる）。"""
    h = hashlib.sha1(str(cfg.backlog.resolve()).encode()).hexdigest()[:8]
    tid = re.sub(r"[^A-Za-z0-9_-]+", "-", str(task.id)).strip("-")[:40] or "task"
    rev = str(task.get("rev", "") or "").strip()
    rev_sfx = ("-v" + re.sub(r"[^A-Za-z0-9_-]+", "-", rev)) if rev else ""
    return f"dg-{h}-{tid}-r{task.retries}{rev_sfx}"[:64]


def task_to_delegation(task: "Task", spec: "dict | None", workload: str = "flow",
                       delegation_id: "str | None" = None, request: "str | None" = None,
                       references: "list[dict] | None" = None) -> dict:
    """タスク＋解決済み workspace spec から delegation post 封筒を組み立てる。

    goal は request（build_request の全文。省略時は task.title）をそのまま使う——ローカル run /
    daemon submit と同じ文脈（charter・rules・decisions・run ブリーフ等）を board 経由でも
    欠かさない（自動配線が location を board に振り替えても、実行者が受け取る指示は変わらない）。
    workspace.url がそのまま「そのリポジトリを担当する board ノードだけが入札する」選別条件になる
    （board_eligible は workspace.url を URL 正規化で突き合わせる。requires.repos は追加しない —
    spec["name"] は依頼側のローカルなルーティング名で、請負側ノードが同じリポジトリを別名で
    宣言しているとURL一致でも入札不能になる誤検出を生むため）。"""
    did = delegation_id or _deleg_id_from_task(task.id)
    goal = request if request else (task.title or task.id)
    env: dict = {
        "op": "post", "version": 1, "id": did, "workload": workload,
        "goal": goal, "title": task.title or "", "requested_by": "agent-project",
    }
    if not request:
        # request（全文）が無いフォールバック時だけ、desc/why から design を簡易合成する
        desc = task.get("desc") or task.get("why") or ""
        if desc:
            env["design"] = str(desc)
    if isinstance(spec, dict) and spec.get("url"):
        ws = {"url": spec["url"]}
        for k in ("path", "base", "target"):
            if spec.get(k):
                ws[k] = spec[k]
        env["workspace"] = ws
    if references:
        refs = []
        for r in references:
            if isinstance(r, dict) and r.get("url"):
                refs.append({k: r[k] for k in ("url", "path", "base", "desc") if r.get(k)})
        if refs:
            env["references"] = refs
    return env


def write_board_post(board_repo: str, env: dict, workdir: "str | None" = None) -> str:
    """post.json を書く薄いラッパー（BoardRepo 経由・冪等・git+ にも対応）。書いたパス（新規/
    既存いずれも）を返す。手動 CLI（board-offload）向けの単発呼び出し用。daemon 内の自動配線
    （_act_board）は sync_pull/write_post/sync_push を個別に呼び、無駄な pull/push を避ける。"""
    repo = BoardRepo(board_repo, workdir=workdir)
    repo.sync_pull()
    if repo.write_post(env):
        repo.sync_push(f"post {env['id']}")
    return os.path.join(repo.delegation_dir(env["id"]), "post.json")


def cmd_board_offload(cfg: "Config", args) -> int:
    """`agent-project board-offload <task-id> [--board <repo>]`:
    ready なタスクをルーティングで workspace を確定し、委譲公示板へ手動で委譲する
    （daemon による自動配線は location: board / policy.offload を参照）。"""
    board_repo = getattr(args, "board", None) or cfg.board
    if not board_repo:
        print("エラー: --board <公示板リポジトリ> か設定 board: が必要です", file=sys.stderr)
        return 2
    tasks = load_tasks(cfg.backlog)
    task = next((t for t in tasks if t.id == args.id), None)
    if task is None:
        task = next((t for t in tasks if t.matches(args.id)), None)
    if task is None:
        print(f"エラー: タスクが見つかりません: {args.id}", file=sys.stderr)
        return 2
    try:
        spec, routed = resolve_workspace(cfg, task, load_policy(cfg.policy))
    except (OSError, ValueError) as e:
        spec, routed = None, f"routing-error: {e}"
    did = _board_delegation_id(task, cfg)
    workload = getattr(args, "board_workload", None) or cfg.board_workload or "flow"
    env = task_to_delegation(task, spec, workload=workload, delegation_id=did,
                             request=build_request(task, cfg),
                             references=task_reference_specs(cfg, task))
    workdir = getattr(args, "board_workdir", None) or cfg.board_workdir
    path = write_board_post(board_repo, env, workdir=workdir)
    print(env["id"])
    print(f">>> タスク {task.id} を委譲公示板へ委譲しました: {env['id']}"
          f"（workspace={routed}）→ {path}", file=sys.stderr)
    return 0
