from __future__ import annotations
# plugins.py — 元 agent-flow.py の 3483-3660 行目（機械分割・内容無改変）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
# --------------------------------------------------------------------------
# executor プラグイン — agent/stub は組み込み、それ以外はプラグインを動的ロードする
#
#   kiro-loop の event_hook と同じ流儀で、executor をプラグイン化する。`--executor`
#   （設定 executor）には次のいずれかを指定できる:
#     - "agent" / "stub"  : 組み込み executor（agent はエージェント CLI に委譲。設定 agent_cli
#       で kiro/claude/copilot を切替）
#     - プラグイン名（例 "gitlab"）: 検索ディレクトリの executors/<name>.py を解決
#     - .py への明示パス : そのファイルをプラグインとしてロード
#   プラグインは `execute(kind, goal, dep_results, model, art_dir, dep_arts)` を公開し、
#   (text, data) を返す。任意で末尾に `repo_instruction`（成果物リポジトリの clone 指示・
#   キーワード可）を受け取れる。受け取れる executor には goal とは別引数で渡すので、
#   本来の goal を汚さずに使える（gitlab はイシューのタイトル/目的に本来の goal を出せる）。
#   受け取れない旧プラグインには、従来どおり clone 指示を goal 先頭へ結合して渡す（後方互換）。
#   プラグイン固有の設定は、同名のトップレベル設定ブロック（例 gitlab:）を JSON 化して
#   環境変数 AGENT_FLOW_EXECUTOR_CONFIG で渡す。
# --------------------------------------------------------------------------
# 組み込み executor の名前 → 実体は呼び出し時に globals() から解決する
# （テストの monkeypatch やホットリロードが効くよう、import 時の参照を握らない）。
BUILTIN_EXECUTORS = {"agent": "execute_agent", "stub": "execute_stub"}


def _executor_accepts(execute, name: str) -> bool:
    """executor が キーワード引数 `name` を受け取れるか（名前付き引数 or **kwargs）。"""
    try:
        sig = inspect.signature(execute)
    except (TypeError, ValueError):
        return False
    for p in sig.parameters.values():
        if p.name == name or p.kind is inspect.Parameter.VAR_KEYWORD:
            return True
    return False


def call_executor(execute, kind: str, goal: str, dep_results: dict, model: "str | None",
                  art_dir, dep_arts, repo_instruction: str = "", workspace: "dict | None" = None,
                  references: "list[dict] | None" = None, request: str = ""):
    """executor を呼ぶ単一の入口。
    - `repo_instruction`（ワークスペース＋参照の作業指示テキスト）は、受け取れる executor には**別引数**で
      渡して goal を汚さない（gitlab のイシュータイトル/目的が指示で埋まらないようにする）。
    - `workspace`（構造化 spec dict: url/path/base/target）は、受け取れる executor へそのまま渡す
      （gitlab は起票先プロジェクトをこの url から解決する）。
    - `references`（参照リポジトリ spec 列）も、受け取れる executor へそのまま渡す
      （gitlab はイシュー本文に参照節を出す）。
    どれも受け取れない executor には、指示を goal 先頭へ結合して渡す。"""
    kwargs = {}
    if repo_instruction and _executor_accepts(execute, "repo_instruction"):
        kwargs["repo_instruction"] = repo_instruction
    if workspace is not None and _executor_accepts(execute, "workspace"):
        kwargs["workspace"] = workspace
    if references and _executor_accepts(execute, "references"):
        kwargs["references"] = references
    if request and _executor_accepts(execute, "request"):
        kwargs["request"] = request  # run の元要求（worker が全体文脈として使う）
    if kwargs or not repo_instruction:
        return execute(kind, goal, dep_results, model, art_dir, dep_arts, **kwargs)
    g = (repo_instruction + "\n\n" + goal) if repo_instruction else goal
    return execute(kind, g, dep_results, model, art_dir, dep_arts)

# executor プラグインモジュールの mtime キャッシュ: {path: (mtime, module)}
_executor_module_cache: "dict[str, tuple[float, object]]" = {}


def _executor_search_dirs() -> "list[str]":
    """executor プラグイン（<name>.py）を探すディレクトリ群（優先順）。"""
    dirs = []
    # 1. スクリプトと同階層の executors/（リポジトリ実行時の同梱プラグイン／インストーラが
    #    本体 bin と同じフォルダに配置した同梱プラグインを発見＝kiro-loop と同じ「本体隣」流儀）
    dirs.append(os.path.join(os.path.dirname(self_path()), "executors"))
    # 2. git リポジトリの tools/agent-flow/executors（cwd がサブディレクトリでも届く）
    try:
        root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
        ).stdout.strip()
        if root:
            dirs.append(os.path.join(root, "tools", "agent-flow", "executors"))
    except Exception:  # noqa: BLE001
        pass
    # 3. ~/.agent/agent-flow/executors（旧インストーラの配置先・後方互換）
    dirs.append(os.path.expanduser("~/.agent/agent-flow/executors"))
    # 4. 設定 executor_dir（任意の追加ディレクトリ）
    extra = _EXECUTOR_DIR
    if extra:
        dirs.insert(0, os.path.expanduser(extra))
    # 重複を保ちつつ除去
    seen, out = set(), []
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


# executor 名 → 解決済みプラグインパスのキャッシュ。_executor_search_dirs() が git rev-parse を
# 走らせるため、service_waits の毎 tick 解決で無用なサブプロセスを撒かないよう一度だけ解決する。
_executor_path_cache: "dict[str, str | None]" = {}


def _resolve_executor_plugin(spec: str) -> "str | None":
    """executor 名 or パスからプラグイン .py の絶対パスを解決する。無ければ None。
    プロセス内で結果をキャッシュする（同一 spec の再解決で git rev-parse を繰り返さない）。"""
    if spec in _executor_path_cache:
        return _executor_path_cache[spec]
    resolved = None
    # 明示パス（.py）
    p = os.path.expanduser(spec)
    if p.endswith(".py") and os.path.isfile(p):
        resolved = os.path.abspath(p)
    # 検索ディレクトリの <name>.py
    elif not os.sep in spec and not spec.endswith(".py"):
        for d in _executor_search_dirs():
            cand = os.path.join(d, f"{spec}.py")
            if os.path.isfile(cand):
                resolved = cand
                break
    _executor_path_cache[spec] = resolved
    return resolved


def _load_executor_module(path: str):
    """executor プラグインを importlib でロードする（mtime キャッシュ付き）。"""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        raise RuntimeError(f"executor プラグインが見つかりません: {path}")
    cached = _executor_module_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    import importlib.util
    spec = importlib.util.spec_from_file_location("agent_flow_executor", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"executor プラグインの spec 生成に失敗: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _executor_module_cache[path] = (mtime, module)
    return module


def resolve_executor_config_json(args) -> "str | None":
    """executor プラグインの設定ブロック（executor 名と同名のトップレベル設定。例 `executor: gitlab`
    なら `args.gitlab`）を親（daemon/orchestrator）で解決し、JSON 文字列にして返す。組み込み executor
    （agent/stub）や、設定ブロックが無い/空のときは None。
    worker 起動時に環境変数 `AGENT_FLOW_EXECUTOR_CONFIG` として明示的に渡し、worker が `--config` を
    再解決できない/別の設定を拾う場合でも、親が解決した設定（例 gitlab の repo_url/conn_label）を
    確実に届けるために使う。"""
    spec = getattr(args, "executor", None) or "agent"
    if spec in BUILTIN_EXECUTORS:
        return None
    cfg = getattr(args, spec, None)
    if isinstance(cfg, dict) and cfg:
        return json.dumps(cfg, ensure_ascii=False)
    return None


def make_executor(args):
    """args.executor を解決し、execute(kind, goal, dep_results, model, art_dir, dep_arts)
    形の呼び出し可能オブジェクトを返す。プラグインのときは設定ブロックを環境変数で渡す。"""
    spec = getattr(args, "executor", None) or "agent"
    if spec in BUILTIN_EXECUTORS:
        return globals()[BUILTIN_EXECUTORS[spec]]
    path = _resolve_executor_plugin(spec)
    if not path:
        dirs = "、".join(_executor_search_dirs())
        raise SystemExit(
            f"[agent-flow] executor '{spec}' を解決できません。組み込み（agent/stub）か、"
            f"プラグイン .py（検索: {dirs}）か、明示パスを指定してください。")
    module = _load_executor_module(path)
    fn = getattr(module, "execute", None)
    if not callable(fn):
        raise SystemExit(f"[agent-flow] executor プラグインに execute() がありません: {path}")
    # プラグイン固有設定: 同名のトップレベル設定ブロック（例 gitlab:）を JSON で環境変数に渡す。
    # 親が解決済みで既に渡されている（worker が再解決できない）場合は、その値を尊重して上書きしない。
    cfgjson = resolve_executor_config_json(args)
    if cfgjson is not None:
        os.environ["AGENT_FLOW_EXECUTOR_CONFIG"] = cfgjson
    log("executor", f"プラグイン '{spec}' をロードしました: {path}")
    return fn

