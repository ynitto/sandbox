from __future__ import annotations
# board.py — バックログのタスクを委譲公示板（agent-board）へ委譲（オフロード）する。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
#
# agent-project は既に task.schema の所有者・ルーティング（resolve_workspace）を持つ。ここでは
# その資産を使い、ready なタスクを delegation.schema.json の post 封筒へ変換して板へ投函する
# （落札→引き渡し→実行は board デーモンと各エンジンが担う）。結合はデータ契約のみ — agent-board
# のコードは import せず、板のレイアウト（delegations/<id>/post.json）へファイルを書くだけ。


def _deleg_id_from_task(tid: str) -> str:
    """タスク id から委譲 id を作る（[A-Za-z0-9_-]{1,64}）。同一タスクの再投函は同一公示（冪等）。"""
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", str(tid or "")).strip("-") or "task"
    return ("dg-" + safe)[:64]


def task_to_delegation(task: "Task", spec: "dict | None", workload: str = "flow",
                       delegation_id: "str | None" = None) -> dict:
    """タスク＋解決済み workspace spec から delegation post 封筒を組み立てる。
    workspace の repo 名を requires.repos に載せる＝そのリポジトリを担当する board ノードだけが
    入札する（成果物リポジトリに応じたノード選別）。"""
    did = delegation_id or _deleg_id_from_task(task.id)
    goal = task.title or task.id
    env: dict = {
        "op": "post", "version": 1, "id": did, "workload": workload,
        "goal": goal, "title": task.title or "", "requested_by": "agent-project",
    }
    desc = task.get("desc") or task.get("why") or ""
    if desc:
        env["design"] = str(desc)
    if isinstance(spec, dict) and spec.get("url"):
        ws = {"url": spec["url"]}
        for k in ("path", "base", "target"):
            if spec.get(k):
                ws[k] = spec[k]
        env["workspace"] = ws
        if spec.get("name"):
            env["requires"] = {"repos": [spec["name"]]}
    return env


def write_board_post(board_repo: str, env: dict) -> str:
    """板の delegations/<id>/post.json へ封筒を書く（冪等 — 既存なら上書きしない）。書いたパスを返す。
    dashboard の board アダプタと同じ書き込み契約（board.schema.json）。git 同期は board デーモン側。"""
    d = os.path.join(os.path.abspath(board_repo), "delegations", env["id"])
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "post.json")
    if os.path.exists(path):
        return path
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(env, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def cmd_board_offload(cfg: "Config", args) -> int:
    """`agent-project board-offload <task-id> --board <repo>`:
    ready なタスクをルーティングで workspace を確定し、委譲公示板へ委譲する。"""
    board_repo = getattr(args, "board", None)
    if not board_repo:
        print("エラー: --board <公示板リポジトリ> が必要です", file=sys.stderr)
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
    env = task_to_delegation(task, spec, workload=getattr(args, "workload", "flow") or "flow")
    path = write_board_post(board_repo, env)
    print(env["id"])
    print(f">>> タスク {task.id} を委譲公示板へ委譲しました: {env['id']}"
          f"（workspace={routed}）→ {path}", file=sys.stderr)
    return 0
