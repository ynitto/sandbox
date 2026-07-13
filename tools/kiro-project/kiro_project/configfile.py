from __future__ import annotations
# configfile.py — 元 kiro-project.py の 10415-10913 行目（機械分割・内容無改変）。
# 単体 import しない。kiro_project/__init__.py が共有名前空間へ順に exec 合成する。
# CLI
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 設定ファイル（kiro-flow と同じ流儀: YAML 任意 / JSON フォールバック）
#   優先順位 CLI > 設定ファイル > 組み込み既定。環境ごとに決まる値をファイルに、
#   その場限りの上書きだけ CLI で渡す。PyYAML 無し環境は JSON（同じキー）で。
# ---------------------------------------------------------------------------
try:
    import yaml  # type: ignore

    def _load_config_file(path: str) -> dict:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
except ImportError:  # PyYAML 無し → JSON のみ
    yaml = None  # type: ignore

    def _load_config_file(path: str) -> dict:  # type: ignore[misc]
        if path.lower().endswith((".yaml", ".yml")):
            print("[kiro-project] ERROR: YAML 設定には PyYAML が必要です（pip install pyyaml）。"
                  "JSON 設定（kiro-project.json・同じキー）なら不要です。", file=sys.stderr)
            sys.exit(1)
        with open(path, encoding="utf-8") as f:
            return json.load(f)


DEFAULT_CONFIG_NAMES = ["kiro-project.yaml", "kiro-project.yml", "kiro-project.json"]

# 設定ファイルで上書きできるキー（snake_case）と組み込み既定。
# CLI 引数の default は None にし、resolve_config で「設定ファイル→ここ」の順に埋める。
# 真偽フラグ（--watch / --ltm / --no-archive 等）と個別パス上書きは CLI 専用。
CONFIG_DEFAULTS = {
    "root": ".",
    "workdir": ".",
    "executor": "agent",
    "planner": "agent",
    "flow_planner": "flow-planner",
    "route_planner": "agent",
    "default_workspace": "",
    "location": "auto",
    "model": None,
    # LLM 実行に使うエージェント CLI: kiro（kiro-cli chat）/ claude（Claude Code `claude -p`）/
    # copilot（GitHub Copilot CLI `copilot -p`）/ codex（OpenAI Codex CLI `codex exec`）。
    # kiro-project 自身の LLM 呼び出し
    # （分解・優先順位・裁定・ルーティング）に効く。実行層 kiro-flow の CLI は
    # kiro-flow 側の設定（flow_config / kiro-flow.yaml の agent_cli）で揃える。
    "agent_cli": "kiro",
    "agent_timeout": 300.0,   # エージェント CLI 1 呼び出しのタイムアウト秒（0 以下で無効）
    # バックログ分解の粒度: coarse（ストーリー相当・既定）/ fine（単機能）/ finest（1ファイル/1関数）。
    # kiro-flow の同名設定と語彙を揃えている（あちらは実行時 DAG、こちらは backlog の分解に効く）。
    "granularity": "coarse",
    "poll": 5.0,
    "concurrency": 1,
    "level": "unattended",
    "throttle": 0.0,
    "debounce": 3.0,
    "pace": 0.0,
    "max_cycles": 20,
    "max_seconds": 0.0,
    "max_tokens": 0,
    "max_cost": 0.0,
    "max_retries": 2,
    "max_iterations": 3,
    "verify_timeout": 600.0,   # テストスイート全体を回す verify は数分かかる（120 秒だと完了しても時間切れ NG）
    "verify_confirm": 1,
    "verify_cwd": None,
    "act_timeout": 1800.0,
    "act_async": False,   # 非ブロッキング委譲（daemon/remote へ submit して待たず offloaded で回収）
    # kiro-flow バスの置き場（絶対パスで明示すると外部 daemon を検知できる）。None なら <root>/bus。
    # 設定ファイルの bus: をここに載せておかないと resolve_config が読まず黙って既定バスに落ちる
    # （daemon 非検知の原因になる）。CLI --bus と同義。
    "bus": None,
    "git_bus": None,
    "git_branch": "main",
    "git_subdir": None,
    "state_git": None,                  # 状態の git 保存・共有（プロジェクト状態を双方向同期。None で無効）
    "state_git_branch": "main",
    "state_git_subdir": "kiro-project",   # リポジトリ内の保存先サブディレクトリ（多重コミッタとの分離）
    "state_git_interval": 300.0,        # fetch/push の最短間隔（秒）。0 で毎同期
    # journal のローテーション: 閾値を超えたら journal-archive/ へ退避して新しい journal を始める。
    # 追記専用ファイルの肥大と、direct 同期での EOF 追記マージ衝突の温床を抑える。
    "journal_max_bytes": 262144,        # 閾値バイト（既定 256KB）。0 以下でローテーション無効
    "journal_keep": 20,                 # journal-archive/ の保持世代数。0 以下で無制限
    # 実行層 kiro-flow daemon をこのプロジェクト用に kiro-project が起動・監視する（opt-in）。
    "manage_flow_daemon": False,
    "flow_config": None,        # daemon に --config で渡す共有 kiro-flow.yaml（任意。kiro-flow の設定はここに集約）
    "flow_max_workers": 4,      # kiro-flow daemon の worker 上限
    # 状態 worktree: root が git の作業ツリー内にあるとき、状態の読み書きを専用ブランチの
    # worktree（切りっぱなし）へ逃がす。設定の root は本体のまま書ける（人が書く自然な形）。
    # 本体の作業ツリー・index を一切汚さず、状態の履歴は同じリポジトリの別ブランチに残る。
    # git 管理外・worktree を作れない場合は自動で本体へフォールバックする（設定は要らない）。
    "state_worktree_dir": "",           # 既定: <repo>-kiro-state（リポジトリの隣）
    "state_branch": "kiro-state",       # 状態を載せるブランチ（無ければ作る）
    "state_commit": True,               # 状態 worktree の変更を git にコミットする
    "state_commit_interval": 300.0,     # 実行の副産物だけの変化をまとめる間隔（秒）。0 で毎回コミット
    "state_push": False,                # コミットを origin へ push する（共有運用）
    "state_backup_branch": "main",      # 状態のバックアップ先（正本ブランチ）。空で無効
    "status_interval": 0.0,             # watch アイドル中の status.json 生存信号更新間隔（秒）。既定 0=無効
    "lock_dir": None,   # kiro-flow daemon ロックの置き場（外部 daemon 発見のため kiro-flow と一致させる）
    "kiro_flow": None,
    "notify_cmd": None,
    "actor": os.environ.get("USER", "user"),
    "learn_capture": True,      # approve/hold 理由・gitlab 却下コメントから learn/avoid を自動抽出（三値 --learn-capture/--no-...）
    "distill_learn": True,      # 人コメントを一般化ルールへ蒸留してから learn 化（三値 --distill-learn/--no-distill-learn）
    "verify_validate": "synth", # red-green 検証（off/synth/all）。合成 verify が変更を弁別するか実行で確かめる
    "reject_recur": 2,          # 同種 gitlab 却下がこの回数で「系の再考」へ格上げ（0/負で無効）
    "intake_recall": True,      # 投入/triage 時の予防リコール（過去 hold に類似→inbox）（三値フラグ）
    "learn_threshold": 0.5,
    "promote_threshold": 2,
    "ltm_home": None,
    "rot_age_days": 14.0,
    "auto_adjudicate": True,    # 真偽だが --auto-adjudicate/--no-... の三値で config 上書き可（既定 on）
    "adjudicate_max": 1,
    "max_spawn": 20,            # 1 run の派生タスク生成上限（0 で無効）
    "regression_cmd": None,     # done 確定前のグローバル回帰検査コマンド（巻き込み事故の検知）
    "regression_revert": False,
    "intake_cmd": None,         # 外部ゲート/検出器から修復タスクを汲み上げるコマンド（例: codd-gate tasks --debt）
    "intake_interval": 600.0,   # intake の実行間隔（秒）。0 以下で毎パス/毎 poll
    "auto_level_max": "assisted",   # 自動昇格の ceiling（unattended への自動到達は明示時のみ）
    "level_promote_after": 5,       # 昇格に要する連続 clean 数
    "level_window": 10,             # 手戻り率の評価窓（直近 N 件）
    "level_rework_max": 0.0,        # 昇格を許す最大 rework_rate
    "max_project_cycles": 5,        # project: 改善サイクルの上限（有限停止）
    "max_project_cost": 0.0,        # project: 累計コスト上限(USD・0=無制限)
    "project_stall": 2,             # project: acceptance PASS 数が増えない連続回数→人へ
    # 自動アップデート（既定 on）。watch のアイドル時に更新を取り込む。更新元は skill-registry.json から自動解決
    "update_enabled": True,              # 自動アップデートの ON/OFF（false で完全無効）
    "update_check_interval": 21600.0,    # 更新チェック間隔（秒）。既定 6 時間。0 以下で自動チェック無効
    "update_repo": DEFAULT_UPDATE_REPO,  # スキルリポジトリ（git URL/パス）。空なら skill-registry.json から自動解決
    "update_branch": "main",             # 追従するブランチ
    "update_subdir": TOOL_SUBDIR,        # リポジトリ内のこのツールのサブディレクトリ
    "update_installer": "install.sh",    # サブディレクトリ内で実行するインストーラ
    # 実行前レビュー（plan review）: 新規タスクは proposed で入り、人の承認（approve）で実行可能になる。
    # false で従来の自動投入（verify ありは即 ready）へ戻す。
    "plan_review": True,
    # 投入時アセスメント: 新規タスクを c=複雑さ/r=リスク/a=曖昧さ（各1-3）で採点し `- assess:` に記録。
    # 採点は情報のみ（plan-review 票・リスクダイジェスト・spec ルーティングが読む）。
    "assess": True,
    # spec ルーティング: 採点 max(c,r,a) >= spec_threshold のタスクに spec 前段タスクを前置
    # （specs/<id>/ の spec/design/tasks を人が承認してから実装へ）。opt-in。
    "spec_track": False,
    "spec_threshold": 3,
    # リポジトリ理解の成果物化: plan 前に context/<repo名>.md を生成（sha キャッシュ）。読み出しは常時。
    "repo_map": False,
    # 効いた learn を rules.md（全タスク常時注入のプロジェクトルール）へ自動昇格。読み出しは常時。
    "rules_capture": True,
    # 処理毎のエージェント上書き（yaml 専用）。キーは plan/review/prioritize/route/adjudicate/
    # verify/distill/assess/repo_map/doctor、値は {agent_cli, model}。
    "agents": {},
    # タスク単位ターゲットブランチ: 成果物を kp/<task-id> に集約（kiro-flow の workspace branch へ注入。
    # リトライ（r0/r1…）も同一ブランチに積み増す）。false で従来の run 毎 kf/<run-id>。
    "task_branch": True,
    "task_branch_prefix": "kp/",
    # 成果物レビュー: verify PASS 後、常に review（検収待ち）→ 人の承認で done 確定。
    # review 到達時に GitLab 設定（GITLAB_TOKEN/GL_TOKEN）があれば kp/<task-id> → target の MR を
    # 自動作成し、承認時にクリーン（コンフリクト無し・未解決ディスカッション無し）なら自動マージする。
    # false で従来の unattended 自動 done。
    "delivery_review": True,
    # 真偽フラグ（CLI > 設定ファイル > 既定）。CLI 未指定（None）なら設定ファイル→この既定で確定
    "watch": False, "once": False, "dry_run": False, "rot": False, "ltm": False,
    "require_progress": False, "auto_level": False, "review_project": False,
    "do_archive": True, "learn": True, "cleanup": True,   # do_archive: --archive はパス用なので別名
    "bus_keep_runs": 20,  # 掃除しても残す直近 run 数（viewer のフロータブが読む一次ソース）
    "with_flow": True,   # doctor: 実行層 kiro-flow doctor も連携実行（CLI 既定 on・直接 Config は off）
}


def _find_config(explicit):
    """設定ファイルの探索: 1) --config 明示 2) ./（ルート直下）3) ./.kiro/ 4) ~/.kiro/。
    ルート直下を最優先にするのは 1 root = 1 プロジェクト構成で kiro-project.yaml が
    プロジェクトのマニフェスト（viewer の自動発見マーカー）を兼ねるため。"""
    if explicit:
        p = os.path.expanduser(explicit)
        if not os.path.isfile(p):
            print(f"[kiro-project] 設定ファイルが見つかりません: {explicit}", file=sys.stderr)
            sys.exit(1)
        return p
    for base in (os.getcwd(),
                 os.path.join(os.getcwd(), ".kiro"),
                 os.path.join(os.path.expanduser("~"), ".kiro")):
        for name in DEFAULT_CONFIG_NAMES:
            cand = os.path.join(base, name)
            if os.path.isfile(cand):
                return cand
    return None


def resolve_config(args):
    """CLI 未指定（None）の設定値だけを 設定ファイル→組み込み既定 で埋める（CLI > config > 既定）。"""
    path = _find_config(getattr(args, "config", None))
    cfg = _load_config_file(path) if path else {}
    args._config_path = path
    for key, dflt in CONFIG_DEFAULTS.items():
        if getattr(args, key, None) is None:
            setattr(args, key, cfg.get(key, dflt))
    return args


def build_config(args) -> Config:
    # プロジェクトルート = --root（cwd 相対、既定 . = cwd）が唯一のアンカー。charter.md /
    # repos.json / backlog/ 等はすべてこの直下（1 プロジェクト = 1 ディレクトリ = 1 プロセス）で、
    # 相対パスの上書きもすべて root 基準で解決する（viewer の bus 解決とも一致する）。
    root = Path(str(args.root or ".")).expanduser()
    root = (root if root.is_absolute() else (Path.cwd() / root)).resolve()
    # 状態の読み書きは、本体の作業ツリーから切り離した専用 worktree へ逃がす（設定の root は
    # 本体を指したままでよい）。以降のパスはすべてこの実効 root を基準に組まれる。
    state_top: "Path | None" = None
    source_root = root                    # リダイレクト前＝人・外部操作者が --root に渡す値
    root, state_top = _redirect_root_to_state_worktree(
        root,
        str(getattr(args, "state_worktree_dir", "") or ""),
        str(getattr(args, "state_branch", "kiro-state") or "kiro-state"))
    # act / verify の作業ディレクトリ。相対値は root 基準（既定 . = root）。
    wd = Path(str(getattr(args, "workdir", None) or ".")).expanduser()
    workdir = (wd if wd.is_absolute() else (root / wd)).resolve()

    def under(name, sub):
        """個別指定があればそれ（相対は root 基準）を、無ければプロジェクトルート配下に集約。"""
        v = getattr(args, name, None)
        if v:
            p = Path(v)
            return p if p.is_absolute() else (root / p)
        return root / sub

    # エージェント CLI（分解・優先順位・裁定等の free 関数が参照）をここで確定する。
    global _AGENT_CLI, _AGENT_TIMEOUT, _AGENT_OVERRIDES
    _AGENT_CLI = str(getattr(args, "agent_cli", "kiro") or "kiro").lower()
    _AGENT_TIMEOUT = float(getattr(args, "agent_timeout", 300.0) or 0.0)
    _AGENT_OVERRIDES = _normalize_agent_overrides(getattr(args, "agents", None))
    # journal ローテーション（append_journal が参照する free 関数向け設定）も同時に確定する。
    global _JOURNAL_MAX_BYTES, _JOURNAL_KEEP
    try:
        _JOURNAL_MAX_BYTES = int(getattr(args, "journal_max_bytes", 262144) or 0)
    except (TypeError, ValueError):
        _JOURNAL_MAX_BYTES = 262144
    try:
        _JOURNAL_KEEP = int(getattr(args, "journal_keep", 20) or 0)
    except (TypeError, ValueError):
        _JOURNAL_KEEP = 20

    return Config(
        backlog=under("backlog", "backlog"),
        policy=under("policy", "policy.md"),
        decisions=under("decisions", "decisions"),
        journal=under("journal", "journal.md"),
        needs=under("needs", "needs"),
        workdir=workdir,
        bus=under("bus", "bus"),
        git_bus=args.git_bus, git_branch=args.git_branch, git_subdir=args.git_subdir,
        state_git=getattr(args, "state_git", None) or None,
        state_git_branch=str(getattr(args, "state_git_branch", "main") or "main"),
        state_git_subdir=str(getattr(args, "state_git_subdir", "kiro-project") or "").strip("/"),
        state_git_interval=max(0.0, float(getattr(args, "state_git_interval", 300.0) or 0.0)),
        manage_flow_daemon=bool(getattr(args, "manage_flow_daemon", False)),
        flow_config=getattr(args, "flow_config", None) or None,
        flow_max_workers=max(1, int(getattr(args, "flow_max_workers", 4) or 4)),
        status_interval=max(0.0, float(getattr(args, "status_interval", 0.0) or 0.0)),
        state_worktree_dir=str(getattr(args, "state_worktree_dir", "") or ""),
        state_branch=str(getattr(args, "state_branch", "kiro-state") or "kiro-state"),
        state_commit=bool(getattr(args, "state_commit", True)),
        state_commit_interval=max(0.0, float(getattr(args, "state_commit_interval", 300.0) or 0.0)),
        state_push=bool(getattr(args, "state_push", False)),
        state_backup_branch=str(getattr(args, "state_backup_branch", "main") or ""),
        state_top=state_top,
        source_root=source_root,
        force=bool(getattr(args, "force", False)),
        lock_dir=getattr(args, "lock_dir", None),
        kiro_flow=args.kiro_flow, planner=args.planner, flow_planner=args.flow_planner,
        route_planner=str(getattr(args, "route_planner", "agent") or "agent"),
        default_workspace=str(getattr(args, "default_workspace", "") or ""),
        location=args.location, executor=args.executor,
        model=args.model,
        agent_cli=_AGENT_CLI, agent_timeout=_AGENT_TIMEOUT,
        granularity=str(getattr(args, "granularity", "coarse") or "coarse").lower(),
        max_iterations=args.max_iterations,
        max_cycles=args.max_cycles, max_seconds=args.max_seconds,
        max_tokens=getattr(args, "max_tokens", 0) or 0,
        max_cost=getattr(args, "max_cost", 0.0) or 0.0,
        max_retries=args.max_retries, pace=args.pace, verify_timeout=args.verify_timeout,
        verify_confirm=max(1, int(getattr(args, "verify_confirm", 1) or 1)),
        verify_cwd=getattr(args, "verify_cwd", None),
        act_timeout=args.act_timeout, act_async=bool(getattr(args, "act_async", False)),
        notify_cmd=args.notify_cmd, actor=args.actor,
        archive=under("archive", "archive"), do_archive=bool(getattr(args, "do_archive", True)),
        learn=bool(getattr(args, "learn", True)),
        learn_capture=bool(getattr(args, "learn_capture", True)),
        distill_learn=bool(getattr(args, "distill_learn", True)),
        verify_validate=str(getattr(args, "verify_validate", "synth") or "synth"),
        reject_recur=int(getattr(args, "reject_recur", 2) or 0),
        intake_recall=bool(getattr(args, "intake_recall", True)),
        learn_threshold=args.learn_threshold,
        auto_adjudicate=bool(getattr(args, "auto_adjudicate", True)),
        adjudicate_max=getattr(args, "adjudicate_max", 1),
        max_spawn=getattr(args, "max_spawn", 20),
        regression_cmd=getattr(args, "regression_cmd", None),
        regression_revert=bool(getattr(args, "regression_revert", False)),
        intake_cmd=getattr(args, "intake_cmd", None),
        intake_interval=float(getattr(args, "intake_interval", 600.0) or 0.0),
        require_progress=bool(getattr(args, "require_progress", False)),
        auto_level=bool(getattr(args, "auto_level", False)),
        auto_level_max=str(getattr(args, "auto_level_max", "assisted") or "assisted"),
        level_promote_after=max(1, int(getattr(args, "level_promote_after", 5) or 5)),
        level_window=max(1, int(getattr(args, "level_window", 10) or 10)),
        level_rework_max=max(0.0, float(getattr(args, "level_rework_max", 0.0) or 0.0)),
        ltm=bool(getattr(args, "ltm", False)), ltm_home=resolve_ltm_home(getattr(args, "ltm_home", None)),
        promote_threshold=getattr(args, "promote_threshold", 2),
        rot=bool(getattr(args, "rot", False)), rot_age_days=args.rot_age_days,
        cleanup=bool(getattr(args, "cleanup", True)),
        bus_keep_runs=max(0, int(getattr(args, "bus_keep_runs", 20) or 0)),
        delivery=under("delivery", "DELIVERY.md"), inbox=under("inbox", "inbox"),
        runlog=under("runlog", "run-log.jsonl"),
        throttle=max(0.0, float(getattr(args, "throttle", 0.0) or 0.0)),
        debounce=args.debounce,
        watch=bool(getattr(args, "watch", False)), poll=getattr(args, "poll", 5.0),
        concurrency=max(1, int(getattr(args, "concurrency", 1) or 1)),
        level=getattr(args, "level", None) or "unattended",
        plan_review=bool(getattr(args, "plan_review", True)),
        assess=bool(getattr(args, "assess", True)),
        spec_track=bool(getattr(args, "spec_track", False)),
        spec_threshold=min(3, max(1, int(getattr(args, "spec_threshold", 3) or 3))),
        repo_map=bool(getattr(args, "repo_map", False)),
        rules_capture=bool(getattr(args, "rules_capture", True)),
        agents=_AGENT_OVERRIDES,
        task_branch=bool(getattr(args, "task_branch", True)),
        task_branch_prefix=str(getattr(args, "task_branch_prefix", "kp/") or "kp/"),
        delivery_review=bool(getattr(args, "delivery_review", True)),
        registry=_split_registry(getattr(args, "registry", None)),
        dry_run=bool(getattr(args, "dry_run", False)), once=bool(getattr(args, "once", False)),
        project_name=root.name,
        charter=under("charter", "charter.md"),
        review_project=bool(getattr(args, "review_project", False)),
        max_project_cycles=max(1, int(getattr(args, "max_project_cycles", 5) or 5)),
        max_project_cost=max(0.0, float(getattr(args, "max_project_cost", 0.0) or 0.0)),
        project_stall=max(1, int(getattr(args, "project_stall", 2) or 2)),
        with_flow=bool(getattr(args, "with_flow", False)),
        update_enabled=bool(getattr(args, "update_enabled", True)),
        update_check_interval=max(0.0, float(getattr(args, "update_check_interval", 0.0) or 0.0)),
        update_repo=getattr(args, "update_repo", None) or None,
        update_branch=str(getattr(args, "update_branch", "main") or "main"),
        update_subdir=str(getattr(args, "update_subdir", TOOL_SUBDIR) or TOOL_SUBDIR),
        update_installer=str(getattr(args, "update_installer", "install.sh") or "install.sh"),
    )


def _add_common(sp):
    # 設定ファイルで上書き可能なキー（CONFIG_DEFAULTS）は default=None にし、resolve_config で確定する
    # （CLI > 設定ファイル > 組み込み既定）。個別パス上書きと真偽フラグは CLI 専用。
    sp.add_argument("--config", default=None,
                    help="設定ファイル（未指定なら ./ → ./.kiro → ~/.kiro の kiro-project.{yaml,yml,json}）")
    sp.add_argument("--root", default=None,
                    help="プロジェクトルート（cwd 相対、既定 . = cwd）。charter.md / backlog/ 等はこの直下。"
                         "相対パスの上書きはすべてこの root 基準で解決される")
    sp.add_argument("--backlog", default=None, help="バックログディレクトリ（既定 <root>/backlog）")
    sp.add_argument("--policy", default=None, help="（既定 <root>/policy.md）")
    sp.add_argument("--decisions", default=None, help="決定記録ディレクトリ（既定 <root>/decisions）")
    sp.add_argument("--journal", default=None, help="（既定 <root>/journal.md）")
    sp.add_argument("--needs", default=None, help="要対応ディレクトリ（既定 <root>/needs）")
    sp.add_argument("--archive", default=None, help="done の退避先（既定 <root>/archive）")
    sp.add_argument("--delivery", default=None, help="納品一覧（既定 <root>/DELIVERY.md）")
    sp.add_argument("--inbox", default=None, help="取り込み待ちのドロップ口（既定 <project>/inbox）")
    sp.add_argument("--debounce", type=float, default=None,
                    help="watch 中、最終保存からこの秒数は feedback 取込を待つ（誤発火防止。既定 3）")
    sp.add_argument("--workdir", default=None,
                    help="act / verify の作業ディレクトリ（root 相対、既定 . = root）")
    sp.add_argument("--bus", default=None, help="kiro-flow バス（root 相対、既定 <root>/bus）")
    sp.add_argument("--agent-cli", dest="agent_cli", default=None, choices=["kiro", "claude", "copilot", "codex"],
                    help="LLM 実行に使うエージェント CLI（設定 agent_cli と同義）。kiro=kiro-cli chat（既定）/ "
                         "claude=Claude Code ヘッドレス（claude -p）/ copilot=GitHub Copilot CLI（copilot -p）/ "
                         "codex=OpenAI Codex CLI（codex exec）")
    sp.add_argument("--granularity", default=None, choices=["coarse", "fine", "finest"],
                    help="バックログ分解の粒度（設定 granularity と同義）。coarse=ストーリー相当（既定）/ "
                         "fine=単機能 / finest=1ファイル/1関数の最小単位")
    sp.add_argument("--git-bus", default=None, help="分散移譲先の共有 git リポジトリ")
    sp.add_argument("--git-branch", default=None)
    sp.add_argument("--git-subdir", default=None)
    sp.add_argument("--state-git", default=None,
                    help="ワーク内容（プロジェクトルートの状態）を保存・共有する git リポジトリ（URL/パス）。"
                         "リモートの kiro-projects-viewer と結果/指示を双方向で往復する。"
                         "ルート自体が git クローンなら不要（direct モードで直接コミット・push する）")
    sp.add_argument("--state-git-branch", default=None, help="state_git の同期先ブランチ（既定 main）")
    sp.add_argument("--state-git-subdir", default=None,
                    help="state_git リポジトリ内の保存先サブディレクトリ（既定 kiro-project）。"
                         "同一リポジトリへ他プログラムもコミットする前提の名前空間分離")
    sp.add_argument("--state-git-interval", type=float, default=None,
                    help="state_git の fetch/push の最短間隔（秒。既定 300）。リモートサーバへの"
                         "負荷を一定に保つ律速。0 で毎同期")
    sp.add_argument("--status-interval", type=float, default=None,
                    help="watch アイドル中に status.json（生存信号。リモート viewer の稼働判定に使う）を"
                         "更新する間隔（秒。既定 0＝無効）。0 のままなら idle 中に status.json は触らず、"
                         "state_git への追加コミットは生まない（実パスの完了時にのみ書く＝相乗り）。"
                         ">0 にすると idle でもこの間隔で 1 回だけ書き直し、その分だけ state_git の"
                         "コミットが増える（負荷とリモートでの生存判定の鮮度のトレードオフ）")
    sp.add_argument("--lock-dir", dest="lock_dir", default=None,
                    help="kiro-flow daemon ロックの置き場（設定ファイル lock_dir と同義）。"
                         "外部起動の daemon を発見するため kiro-flow 側と一致させる")
    sp.add_argument("--kiro-flow", default=None)
    sp.add_argument("--planner", default=None, choices=["agent", "none"],
                    help="優先順位付け: agent=エージェント委譲（priority 加味）/ none=priority＋古さ（既定 agent）")
    sp.add_argument("--flow-planner", default=None,
                    choices=["flow-planner", "agent", "stub"], help="kiro-flow run に渡す planner（既定 flow-planner）")
    sp.add_argument("--location", default=None,
                    choices=["auto", "local", "daemon", "remote"], help="act の実行モード（既定 auto）")
    sp.add_argument("--executor", default=None,
                    help="act の実体（kiro-flow run へ委譲）。組み込み agent / stub、または kiro-flow の "
                         "executor プラグイン名（例 gitlab）/ .py パスを指定できる（既定 agent）")
    sp.add_argument("--model", default=None)
    sp.add_argument("--max-iterations", type=int, default=None)
    sp.add_argument("--max-cycles", type=int, default=None, help="予算: サイクル数（既定 20）")
    sp.add_argument("--max-seconds", type=float, default=None, help="予算: 実時間（0=無制限）")
    sp.add_argument("--max-tokens", type=int, default=None,
                    help="予算: 消費トークン上限（0=無制限。act 出力の @cost を計上）")
    sp.add_argument("--max-cost", type=float, default=None,
                    help="予算: 金額(USD)上限（0=無制限。act 出力の @cost usd= を計上）")
    sp.add_argument("--max-retries", type=int, default=None)
    sp.add_argument("--pace", type=float, default=None, help="1サイクルの下限間隔（秒）。レーン減速")
    sp.add_argument("--verify-timeout", type=float, default=None)
    sp.add_argument("--verify-confirm", type=int, default=None,
                    help="verify をこの回数まで再実行し PASS/FAIL が跨いだら flake として人へ隔離（既定 1）。"
                         "揺れる verify の NG churn / flaky PASS の done を防ぐ（コストは回数分）")
    sp.add_argument("--verify-cwd", default=None,
                    help="verify/acceptance を実行する作業ディレクトリ（既定 workdir）。git-bus 等で workdir に"
                         "成果が無いとき、対象 repo のクローン先を指す。未指定でも charter に単一 repo があれば"
                         "acceptance はその repo を一時 clone して実行する")
    sp.add_argument("--act-timeout", type=float, default=None)
    sp.add_argument("--act-async", dest="act_async", action="store_true", default=None,
                    help="非ブロッキング委譲: daemon/remote へ submit して待たず offloaded にし、"
                         "次パスでポーリングして回収する（gitlab 等の長期委譲でループを塞がない）")
    sp.add_argument("--notify-cmd", default=None, help="要対応ダイジェストを渡す通知コマンド")
    sp.add_argument("--actor", default=None)
    sp.add_argument("--learn", action=argparse.BooleanOptionalAction, default=None,
                    help="DR 学習（過去の人の判断から類似案件を自動解決）。--no-learn で無効化（既定 on）")
    sp.add_argument("--learn-capture", action=argparse.BooleanOptionalAction, default=None,
                    help="人の判断（approve 理由→learn / hold 理由→avoid / gitlab 却下コメント→learn）を"
                         "自動抽出して蓄積。--no-learn-capture で無効化（既定 on）")
    sp.add_argument("--distill-learn", action=argparse.BooleanOptionalAction, default=None,
                    help="人コメントを一般化ルールへ蒸留してから learn 化。--no-distill-learn で"
                         "生の指摘をそのまま残す（既定 on・蒸留失敗時も生でフォールバック）")
    sp.add_argument("--verify-validate", choices=["off", "synth", "all"], default=None,
                    help="red-green 検証: 合成 verify が act 前でも PASS（=変更を弁別しない偽 done）を弾く。"
                         "off/synth（自動生成のみ・既定）/all。per-task `- verify_validate: none` で除外")
    sp.add_argument("--reject-recur", type=int, default=None,
                    help="同種の gitlab 却下がこの回数に達したら silent 積み直しをやめ『系の再考』で人へ"
                         "（分解/verify/policy の見直し。0/負で無効・既定 2）")
    sp.add_argument("--intake-recall", action=argparse.BooleanOptionalAction, default=None,
                    help="投入/triage 時に過去の hold（avoid）と照合し、類似は ready へ落とさず inbox（人へ）"
                         "寄せる予防リコール。--no-intake-recall で無効化（既定 on）")
    sp.add_argument("--learn-threshold", type=float, default=None,
                    help="DR 学習・予防リコールのタイトル類似度しきい値（0〜1。既定 0.5）")
    # 自律裁定: needs に落とす前に kiro-cli が積み直し可否を判断（三値: 未指定→設定ファイル/既定 on）
    sp.add_argument("--auto-adjudicate", dest="auto_adjudicate", action="store_true", default=None,
                    help="人の判断(needs)へ送る前に kiro-cli が『自律的に積み直すか人へ回すか』を裁定（既定 on）")
    sp.add_argument("--no-auto-adjudicate", dest="auto_adjudicate", action="store_false",
                    default=None, help="自律裁定を無効化して常に人へ回す（明示 off）")
    sp.add_argument("--adjudicate-max", type=int, default=None,
                    help="1タスクあたりの自律裁定の上限回数（有限停止のため。既定 1）")
    sp.add_argument("--max-spawn", type=int, default=None,
                    help="1 run で生成できる派生タスク（followup）数の上限（0 で無効。既定 20）")
    sp.add_argument("--regression-cmd", default=None,
                    help="done 確定前に走らせるグローバル回帰検査（失敗で done にせず人へ。巻き込み事故の検知）")
    sp.add_argument("--regression-revert", action=argparse.BooleanOptionalAction, default=None,
                    help="回帰検知時に作業ツリーの未コミット変更を巻き戻す（best-effort・既定 off）")
    sp.add_argument("--intake-cmd", default=None,
                    help="外部の決定的ゲート/検出器から修復タスクを汲み上げるコマンド（stdout の "
                         "enqueue --json 形式を冪等取り込み。例: codd-gate tasks --debt。"
                         "単発・有界なコマンドであること＝常駐はこちらが持つ）")
    sp.add_argument("--intake-interval", type=float, default=None,
                    help="intake の実行間隔（秒。既定 600。0 以下で毎パス/毎 poll）")
    sp.add_argument("--ltm", action=argparse.BooleanOptionalAction, default=None,
                    help="効いた学習を ltm-use 長期記憶へ昇格＋プロジェクト横断 recall（既定 off）")
    sp.add_argument("--ltm-home", default=None,
                    help="ltm-use ストアのルート（既定 KIRO_LTM_HOME → ~/.claude）")
    sp.add_argument("--promote-threshold", type=int, default=None,
                    help="learn ルールがこの回数以上効いたら昇格（既定 2）")
    sp.add_argument("--rot-age-days", type=float, default=None,
                    help="rot の stale 判定（経過日数。既定 14）")
    sp.add_argument("--plan-review", action=argparse.BooleanOptionalAction, default=None,
                    help="実行前レビュー: 新規タスクを proposed で入れ、人の承認で実行可能にする"
                         "（--no-plan-review で従来の自動投入。既定 on）")
    sp.add_argument("--assess", action=argparse.BooleanOptionalAction, default=None,
                    help="投入時アセスメント: 新規タスクを c=複雑さ/r=リスク/a=曖昧さ（各1-3）で採点し"
                         " `- assess:` に記録（表示のみ・実行可否は不変。既定 on）")
    sp.add_argument("--spec-track", action=argparse.BooleanOptionalAction, default=None,
                    help="spec ルーティング: 採点が --spec-threshold に達したタスクに spec 前段タスク"
                         "（specs/<id>/ の spec/design/tasks 作成→人の承認→実装タスク展開）を前置（既定 off）")
    sp.add_argument("--spec-threshold", type=int, default=None,
                    help="spec ルートに乗せる採点しきい値（max(c,r,a) がこの値以上。1-3・既定 3）")
    sp.add_argument("--repo-map", action=argparse.BooleanOptionalAction, default=None,
                    help="リポジトリ理解の成果物化: plan 前に書込先 repo ごとに context/<repo名>.md を"
                         "生成（HEAD sha キャッシュ・変化時のみ再生成）。読み出しは常時（既定 off）")
    sp.add_argument("--rules-capture", action=argparse.BooleanOptionalAction, default=None,
                    help="効いた学習（learn の auto-resolve が閾値回数以上）を rules.md（プロジェクト"
                         "ルール・全タスク常時注入）へ自動昇格（既定 on。rules.md の注入自体は常時）")
    sp.add_argument("--task-branch", action=argparse.BooleanOptionalAction, default=None,
                    help="タスク単位ターゲットブランチ（kp/<task-id> に成果を集約。既定 on）")
    sp.add_argument("--delivery-review", action=argparse.BooleanOptionalAction, default=None,
                    help="成果物レビュー: verify PASS 後、常に検収待ち（review）にして人の承認で done"
                         "（--no-delivery-review で従来の自動 done。既定 on）")


# ---------------------------------------------------------------------------
