from __future__ import annotations
# config.py — 元 agent-project.py の 4641-5062 行目（機械分割・内容無改変）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
# 設定
# ---------------------------------------------------------------------------
# このツールがスキルリポジトリ内に置かれているサブディレクトリ（自動アップデートの参照先）。
# 自動アップデートは update_repo のこのパス以下だけを temp 領域へ sparse-checkout して
# install.sh を実行する（doctor と同じ流儀で、操作は決定的・無関係ファイルは取得しない）。
TOOL_SUBDIR = "tools/agent-project"
# スキルリポジトリ（git URL/パス）の既定。空なら install.py が生成する skill-registry.json から
# 自動解決する（repositories.origin.url → install_dir）。設定ファイルの update_repo で明示も可。
DEFAULT_UPDATE_REPO = ""
# skill-registry.json を探すエージェントホーム（install.py の AGENT_DIRS に対応）。
_AGENT_HOME_DIRS = (".agent", ".kiro", ".claude", ".copilot", ".codex")

# 自己更新の再起動先 cwd（main で起動時の cwd を捕捉。「動いていたカレントディレクトリ」へ戻す）。
_START_CWD: "str | None" = None


@dataclass
class Config:
    backlog: Path      # ディレクトリ（案件毎ファイル）
    policy: Path       # ファイル
    decisions: Path    # ディレクトリ（案件毎）
    journal: Path      # ファイル
    needs: Path        # ディレクトリ（案件毎）
    workdir: Path
    bus: Path
    git_bus: "str | None" = None
    git_branch: str = "main"
    git_subdir: "str | None" = None
    # 状態の git 保存・共有（state_git）: ワーク内容（プロジェクトルートの状態）を共有 git リポジトリへ
    # 双方向同期し、リモートの agent-dashboard と結果/指示を往復する。fetch/push は
    # state_git_interval で律速。ルート自体が git クローンなら管理クローンを介さず直接コミット・push
    # する（direct モード。state_git 未設定でも有効）。
    state_git: "str | None" = None        # 共有リポジトリ（URL/パス）。None で無効（direct モードを除く）
    state_git_branch: str = "main"        # 同期先ブランチ
    state_git_subdir: str = "agent-project"  # リポジトリ内の保存先サブディレクトリ（多重コミッタとの名前空間分離）
    state_git_interval: float = 300.0     # fetch/push の最短間隔（秒）。0 で毎同期（リモート負荷は増える）
    # 実行層 agent-flow daemon をこのプロジェクト用に agent-project が起動・監視する（opt-in）。
    manage_flow_daemon: bool = False
    # daemon に --config で渡す共有 agent-flow.yaml（任意。未指定は agent-flow の既定発見に委ねる）。
    # agent-flow の設定値（executor / state_git_subdir / gitlab.* / defer_waits 等）は個別に CLI 注入
    # せず、この設定ファイルに集約して agent-flow に読ませる（agent-project 側に agent-flow 設定を増やさない）。
    # 例外は「バスをどのリポジトリへ鏡写しするか」の routing（--state-git 等）のみで、これは
    # agent-project の役割なので CLI 注入し続ける。
    flow_config: "str | None" = None
    flow_max_workers: int = 4          # agent-flow daemon の worker 上限
    # 状態 worktree（build_config が root を差し替える。下の _redirect_root_to_state_worktree 参照）
    state_worktree_dir: str = ""
    state_branch: str = "agent-state"
    state_commit: bool = True
    state_commit_interval: float = 300.0
    state_push: bool = False
    state_backup_branch: str = "main"  # 状態のバックアップ先（正本ブランチ）。空で無効
    state_top: "Path | None" = None    # 本体リポジトリのトップ（状態を worktree へ逃がしたときだけ入る）
    # 設定・CLI で指定された素の root（worktree へリダイレクトする前）。プロジェクトの同一性は
    # これで判定する: 実書き込み先（backlog.parent）は worktree 側を指すので、start/stop が照合に
    # 使う root（_resolved_root＝リダイレクトしない）と食い違い、重複検出が空振りする。
    # 外部操作者が --root に渡すのもこの値（worktree 側を渡すと二重リダイレクトになる）。
    source_root: "Path | None" = None
    force: bool = False                # 同じプロジェクトを監視中でも起動する（watch の重複を許す）
    status_interval: float = 0.0          # watch アイドル中に status.json の生存信号を更新する間隔（秒）。
                                           # 既定 0=無効（idle 中は追加コミットを一切生まない）。>0 でこの間隔
                                           # ごとに 1 回だけ書き直し、state_git の commit-if-diff に乗る
    lock_dir: "str | None" = None   # agent-flow daemon ロックの置き場（外部 daemon 発見のため agent-flow と一致させる）
    agent_flow: "str | None" = None
    planner: str = "agent"         # 優先順位付け戦略: agent（エージェント委譲）/ none（priority＋古さ）
    flow_planner: str = "flow-planner"  # agent-flow run に渡す planner
    # ルーティング: タスク → ちょうど1つの書込先ワークスペースを決める自動判断。agent=曖昧時に
    # エージェント委譲で推定（charter owns: と route: の決定論を先に適用）/ none=決定論のみ（推定しない）。
    route_planner: str = "agent"
    default_workspace: str = ""    # route で決まらないタスクの既定ワークスペース（charter の name/url）。空で無効
    location: str = "auto"         # act の実行モード: auto / local / daemon / remote
    executor: str = "agent"
    model: "str | None" = None
    agent_cli: str = "kiro"        # LLM 実行に使うエージェント CLI: kiro / claude / copilot / codex
    agent_timeout: float = 300.0   # エージェント CLI 1 呼び出しのタイムアウト秒（0 以下で無効）
    # バックログ分解の粒度: coarse（ストーリー相当・既定）/ fine（単機能）/ finest（1ファイル/1関数）
    granularity: str = "coarse"
    max_iterations: int = 3
    max_cycles: int = 20
    max_seconds: float = 0.0
    max_tokens: int = 0            # 予算: 消費トークン上限（0=無制限）。act 出力の @cost を計上
    max_cost: float = 0.0          # 予算: 金額(USD)上限（0=無制限）
    max_retries: int = 2
    pace: float = 0.0
    # verify の打ち切り時間（秒）。既定 120 秒では足りない: 「テストスイート全体を green にする」
    # 類の完了条件は数分かかる（実際 990 件で 130 秒）。短すぎると **完了しているのに時間切れで
    # NG** と判定し、リトライを積み上げた末に人へエスカレーションする（retries=6 まで無駄に
    # 積み直して blocked になった）。act_timeout（既定 1800 秒）より十分短く、ハングの保護は保つ。
    verify_timeout: float = 600.0
    verify_confirm: int = 1         # verify を最大この回数まで再実行し PASS/FAIL が跨いだら flake として人へ隔離（1=従来）
    verify_cwd: "str | None" = None  # verify/acceptance を実行する作業ディレクトリ（既定 workdir）。git-bus 等で
                                     # workdir に成果が無いとき、対象 repo のクローン先を指す。未指定かつ charter に
                                     # 単一 repo があれば acceptance はその repo を一時 clone して実行する。
    require_progress: bool = False  # verify=PASS でも act が baseline 以降に変更を生んでなければ done せず人へ（履歴一致 verify の偽 done 対策）
    auto_level: bool = False         # 実績連動の自動昇格（track 毎に手戻り率で level を上げ下げ）。既定 off
    auto_level_max: str = "assisted" # 自動昇格の ceiling。既定 assisted（unattended への自動到達は明示時のみ）
    level_promote_after: int = 5     # 昇格に要する連続 clean 完了数
    level_window: int = 10           # 手戻り率の評価窓（直近 N 件の完了）
    level_rework_max: float = 0.0    # 昇格を許す最大 rework_rate（既定 0＝手戻りゼロ）
    act_timeout: float = 1800.0
    # 非ブロッキング委譲: daemon/remote への submit で結果を待たず次のタスクへ進み、offloaded にして
    # 次パスでポーリングして回収する。gitlab 等の長期委譲でループを塞がない（専用 daemon が run を保持）。
    act_async: bool = False
    notify_cmd: "str | None" = None
    actor: str = "user"
    archive: "Path | None" = None   # done の退避先ディレクトリ（既定 archive/）
    do_archive: bool = True         # done を archive/ へ退避（False なら削除）
    learn: bool = True              # DR 学習: 過去の人の判断から類似案件を自動解決
    learn_capture: bool = True      # 人の判断（approve 理由・hold 理由・gitlab 却下コメント）から learn/avoid を蓄積
    distill_learn: bool = True      # 人コメントを一般化ルールへ蒸留してから learn 化（off で生の指摘をそのまま残す）
    verify_validate: str = "synth"  # red-green 検証: 合成 verify が act 前でも PASS（=変更を弁別しない偽 done）を弾く。
                                    # off=無効 / synth=自動生成(synth/template)のみ / all=常時。per-task `- verify_validate: none` で除外
    reject_recur: int = 2           # 同種の gitlab 却下がこの回数に達したら、silent 積み直しをやめ「系の再考」で人へ（0/負で無効）
    intake_recall: bool = True      # 投入/triage 時に過去の hold 判断（avoid）と照合し類似は inbox（人へ）へ寄せる
    learn_threshold: float = 0.5    # タイトル類似度（Jaccard）のしきい値
    auto_adjudicate: bool = True    # needs に落とす前に エージェント CLI が積み直し可否を裁定（既定 on）
    adjudicate_max: int = 1         # 1タスクあたりの自律裁定の上限回数（有限停止のため）
    max_spawn: int = 20             # 1 run で生成できる派生タスク数の上限（0 で生成無効。暴走防止）
    regression_cmd: "str | None" = None  # done 確定前に走らせるグローバル回帰検査（巻き込み事故の検知）
    regression_revert: bool = False      # 回帰時に作業ツリーの未コミット変更を巻き戻す（既定 off）
    intake_cmd: "str | None" = None      # 外部の決定的ゲート/検出器から修復タスクを汲み上げる取り込みコマンド
    intake_interval: float = 600.0       # intake_cmd の実行間隔（秒）。0 以下なら毎回（パス開始/idle poll 毎）
    ltm: bool = False               # ltm-use 長期記憶への昇格＋横断 recall（既定 off: home へ書くため明示）
    ltm_home: "Path | None" = None  # ltm-use ストアのルート（既定 KIRO_LTM_HOME→~/.claude）
    promote_threshold: int = 2      # learn ルールがこの回数以上効いたら昇格
    rot: bool = False               # rot 検知（古い/重複/実行不能を triage で掃除）
    rot_age_days: float = 14.0      # stale とみなす経過日数
    cleanup: bool = True            # run 後に agent-flow バスの一時状態を掃除
    bus_keep_runs: int = 20         # 掃除しても残す直近 run 数（viewer のフロータブが読む一次ソース）
    delivery: "Path | None" = None  # 納品一覧（受領書）DELIVERY.md
    inbox: "Path | None" = None     # 取り込み待ちのドロップ口（外部ソースがここへファイルを置く）
    debounce: float = 3.0           # watch 中、最終保存からこの秒数は feedback 取込を待つ
    watch: bool = False     # 終了条件後もプロセスを残し backlog を監視
    poll: float = 5.0       # watch のポーリング間隔（秒）
    concurrency: int = 1    # 1サイクルで daemon/remote へ並行 submit する独立タスク数（1=逐次）
    level: str = "unattended"  # 自律度: report(実行せず計画報告) / assisted(実行するが done は人が承認) / unattended(現行)
    # 実行前レビュー（plan review・既定 on）: 新規タスクは proposed で入り、人の承認で ready になる。
    plan_review: bool = True
    # 投入時アセスメント（既定 on）: 新規タスクを c=複雑さ / r=リスク / a=曖昧さ（各1-3）で採点し
    # `- assess:` に記録する。採点は情報であり実行可否を変えない（読むのは plan-review 票・
    # リスクダイジェスト・spec ルーティング）。知能は委譲・stub/失敗時は決定的ヒューリスティック。
    assess: bool = True
    # spec ルーティング（既定 off）: 採点 max(c,r,a) が spec_threshold に達したタスクに spec 前段
    # タスク（specs/<id>/ の spec.md/design.md/tasks.md 作成・人の承認で実装タスクへ展開）を前置する。
    spec_track: bool = False
    spec_threshold: int = 3
    # リポジトリ理解の成果物化（既定 off）: plan の直前に charter の書込先 repo ごとに
    # context/<repo名>.md を生成（HEAD sha キャッシュ・変化時のみ再生成）。読み出しは常時
    # （人が手書きした context/*.md も plan / act / verify 合成へ有界注入される）。
    repo_map: bool = False
    # プロジェクトルールの自動昇格（既定 on）: 効果が再現した learn（auto-resolve が
    # promote_threshold 回以上）を rules.md（全タスク常時注入層）へ決定的に追記する。
    # rules.md の読み出し・注入自体は設定に依らず常時（人が書けば必ず効く）。
    rules_capture: bool = True
    # 処理毎のエージェント上書き（設定ファイル専用・CLI フラグ無し）。キーは AGENT_PURPOSES
    # （plan/review/prioritize/route/adjudicate/verify/distill/assess/repo_map/doctor）、
    # 値は {agent_cli, model}。未指定の処理はグローバル agent_cli / model を使う。
    agents: dict = field(default_factory=dict)
    # タスク単位ターゲットブランチ（既定 on）: 成果を ap/<task-id> に集約する（リトライも同一ブランチ）。
    task_branch: bool = True
    task_branch_prefix: str = "ap/"
    # 成果物レビュー（既定 on）: verify PASS 後、level に依らず常に review（検収待ち）へ。
    # 人の承認で done 確定（GitLab 設定があれば MR を自動マージ規則で決着）。false で従来の自動 done。
    delivery_review: bool = True
    throttle: float = 0.0   # ソフト予算比率(0=off)。max_tokens/max_cost のこの割合で run を打ち切り watch は report 降格
    runlog: "Path | None" = None    # 構造化 run-log（JSONL・run 毎に1行追記）。既定 <root>/run-log.jsonl
    registry: "list" = field(default_factory=list)  # 共有レジストリ（別ホスト発見用。NFS/同期/git バス）
    dry_run: bool = False
    once: bool = False
    project_name: str = ""               # プロジェクト名（ルートのディレクトリ名。milestone id の一次ソース）
    # プロジェクト層（charter 駆動の plan→execute→evaluate ループ）。`project` サブコマンドでのみ使う。
    charter: "Path | None" = None        # 人が書く目標/制約/前提/成果物/acceptance（既定 <root>/charter.md）
    review_project: bool = False         # evaluate で敵対的レビューを上乗せ（opt-in・知能は委譲）
    max_project_cycles: int = 5          # 改善サイクルの上限（有限停止）
    max_project_cost: float = 0.0        # プロジェクト累計コスト上限(USD・0=無制限)
    project_stall: int = 2               # acceptance PASS 数が増えない連続回数の上限→人へ
    with_flow: bool = False              # doctor: 実行層 agent-flow doctor も連携実行し findings を統合（CLI 既定 on）
    # 自動アップデート（既定 on）。更新元は skill-registry.json から自動解決。watch のアイドル時に
    # git ls-remote で main の先頭を確認し、適用済みと違えば temp 領域へ sparse-checkout
    # （tools/agent-project/ だけ）→ install.sh 実行 → graceful 再起動する。起動直後にも 1 回実施。
    update_enabled: bool = True          # 自動アップデートの ON/OFF（false で完全無効・既定 on）
    update_check_interval: float = 21600.0  # 更新チェック間隔（秒）。既定 6 時間。0 以下で自動チェック無効
    update_repo: "str | None" = None     # スキルリポジトリ（git URL/パス）。空/None なら registry から自動解決
    update_branch: str = "main"          # 追従するブランチ
    update_subdir: str = TOOL_SUBDIR     # リポジトリ内のこのツールのサブディレクトリ
    update_installer: str = "install.sh"  # サブディレクトリ内で実行するインストーラ

    def archive_dir(self) -> Path:
        return self.archive or (self.backlog.parent / "archive")

    def cohorts_dir(self) -> Path:
        return self.backlog.parent / "cohorts"

    def __post_init__(self):
        if self.delivery is None:
            self.delivery = self.backlog.parent / "DELIVERY.md"
        if self.runlog is None:
            self.runlog = self.backlog.parent / "run-log.jsonl"
        if self.charter is None:
            self.charter = self.backlog.parent / "charter.md"


def ensure_dirs(cfg: Config) -> None:
    for d in (cfg.backlog, cfg.needs, cfg.decisions):
        d.mkdir(parents=True, exist_ok=True)
    if cfg.inbox:                       # 外部ソースが投入先を見つけられるよう作っておく
        cfg.inbox.mkdir(parents=True, exist_ok=True)
    commands_dir(cfg).mkdir(parents=True, exist_ok=True)  # 指示ドロップ口も同様に作っておく
    cfg.journal.parent.mkdir(parents=True, exist_ok=True)


def extract_delivery_ref(act_msg: str, cfg: Config,
                         baseline: "tuple[str, frozenset] | None" = None) -> str:
    """成果物の参照を得る。act 出力の PR URL / commit SHA を優先。
    baseline（act 前スナップショット）が渡されたら **baseline 以降の新規コミット/未コミット変更のみ**を
    成果物とみなし、変化が無ければ `(変更なし)` を返す（既存コミットを成果物と偽らない＝偽 done の可視化）。
    baseline=None のときは従来どおり `git log -1`（後方互換）。"""
    m = re.search(r"https?://\S+/(?:pull|merge_requests)/\d+", act_msg or "")
    if m:
        return m.group(0)
    m = re.search(r"\b[0-9a-f]{7,40}\b", act_msg or "")
    if m:
        return f"commit {m.group(0)}"
    if baseline is not None:
        head0, _ = baseline
        head1 = _git_out(cfg.workdir, "rev-parse", "HEAD").strip()
        if head1 and head1 != head0:                      # baseline 以降の新規コミット
            line = _git_out(cfg.workdir, "log", "-1", "--format=%h %s").strip()
            return f"git: {line}" if line else f"commit {head1[:8]}"
        if meaningful_changes(cfg, baseline):             # 未コミットの作業ツリー変更（プロジェクト状態は除外）
            return "git: 未コミットの変更あり"
        return "(変更なし)"                               # ← 既存コミットを成果物として報告しない
    try:
        r = subprocess.run(["git", "-C", str(cfg.workdir), "log", "-1", "--format=%h %s"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return f"git: {r.stdout.strip()}"
    except Exception:  # noqa: BLE001
        pass
    return "(参照なし)"


def _current_branch(cfg: "Config") -> str:
    """作業ツリーの現在ブランチ（git でなければ空）。成果物の所在をブランチ単位で示すのに使う。"""
    if not (cfg.workdir / ".git").exists():
        return ""
    return _git_out(cfg.workdir, "rev-parse", "--abbrev-ref", "HEAD").strip()


def _source_repo(cfg: "Config") -> Path:
    """成果物（worker が書いたコード）が置かれるリポジトリ。

    cfg.workdir は状態 worktree（<repo>-agent-state/.agent-project）を指すので、そこの git を見ても
    出てくるのは bus/ の claims や events ばかりで、レビューしたいコードは 1 行も出てこない。
    コードは本体リポジトリの作業ブランチ ap/<task-id> にある。"""
    return cfg.state_top or cfg.workdir


def _task_run_meta(cfg: "Config", task: "Task") -> dict:
    """タスクの直近 run（last_run）の meta.json。無ければ {}。"""
    rid = str(task.get("last_run") or "").strip()
    if not rid or rid != os.path.basename(rid):
        return {}
    try:
        return json.loads((cfg.bus / "runs" / rid / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _task_work_branch(cfg: "Config", task: "Task") -> "tuple[str, str] | None":
    """タスクの作業ブランチ (base, branch)。agent-flow が run メタへ記録した workspace から取る。"""
    ws = (_task_run_meta(cfg, task).get("workspace") or {})
    branch = str(ws.get("branch") or "").strip()
    if not branch:
        return None
    return (str(ws.get("base") or "main").strip() or "main"), branch


def work_branch_changes(cfg: "Config", base: str, branch: str,
                        repo: "Path | None" = None) -> "tuple[str, list[str]]":
    """作業ブランチの成果 (ref, 変更ファイル一覧)。無ければ ("", [])。

    worker は成果を origin へ push するので、ローカルに無ければ取り込んでから差分を取る。"""
    repo = Path(repo) if repo is not None else _source_repo(cfg)

    def _has(ref: str) -> bool:
        return bool(_git_out(repo, "rev-parse", "--verify", "--quiet", ref).strip())

    ref = branch if _has(branch) else f"origin/{branch}"
    if not _has(ref):
        try:
            subprocess.run(["git", "-C", str(repo), "fetch", "-q", "origin", branch],
                           capture_output=True, timeout=180)
        except (OSError, subprocess.SubprocessError):
            return "", []
    if not _has(ref):
        return "", []
    files = [ln.strip() for ln in
             _git_out(repo, "diff", "--name-only", f"{base}...{ref}").splitlines() if ln.strip()]
    return ref, files


def _repo_label(url: str, fallback: str = "") -> str:
    """git URL から表示用の短い名前（path basename）を取る。"""
    s = str(url or "").rstrip("/").removesuffix(".git")
    if "/" in s:
        return s.rsplit("/", 1)[-1] or fallback or "repo"
    return fallback or s or "repo"


def delivery_entries(cfg: "Config", task: "Task | None" = None,
                     mr_url: str = "", max_files: int = 40) -> "list[dict]":
    """検収用のリポジトリ単位エントリ一覧（viewer の構造化ペイロード）。

    書込先 workspace を必ず先頭に置き、references（読取専用）もブランチ情報があれば並べる。
    実体差分を取れるのはローカルで解決できるリポジトリだけ（通常は state_top）。"""
    if task is None:
        return []
    meta = _task_run_meta(cfg, task)
    ws = meta.get("workspace") or {}
    branch = str(ws.get("branch") or "").strip()
    base = str(ws.get("base") or "main").strip() or "main"
    entries: "list[dict]" = []
    if branch:
        repo = _source_repo(cfg)
        ref, files = work_branch_changes(cfg, base, branch, repo=repo)
        url = str(ws.get("url") or "")
        name = _repo_label(url, fallback=repo.name)
        entry = {
            "name": name,
            "role": "write",
            "url": url,
            "path": str(repo),
            "base": base,
            "branch": branch,
            "ref": ref,
            "files": files[:max_files],
            "files_total": len(files),
            "diff_cmd": f"git -C {repo} diff {base}...{ref}" if ref else "",
            "mr_url": str(mr_url or task.get("mr_url") or "").strip(),
        }
        entries.append(entry)
    # 参照リポジトリ（読取）: 差分は通常無いが、複数 repo 案件で「どの repo を見るか」を明示する
    for ref_spec in (meta.get("references") or []):
        if not isinstance(ref_spec, dict):
            continue
        rurl = str(ref_spec.get("url") or "").strip()
        if not rurl:
            continue
        entries.append({
            "name": _repo_label(rurl),
            "role": "reference",
            "url": rurl,
            "path": "",
            "base": str(ref_spec.get("base") or base),
            "branch": str(ref_spec.get("branch") or ""),
            "ref": "",
            "files": [],
            "files_total": 0,
            "diff_cmd": "",
            "mr_url": "",
        })
    return entries


def delivery_evidence(cfg: "Config", act_msg: str, git_base, location: str = "local",
                      verify: "str | None" = None, vmsg: str = "", ok: "bool | None" = None,
                      max_files: int = 12, task: "Task | None" = None,
                      mr_url: str = "") -> str:
    """人が「成果物がどこにあり・何が差分で・検証はどうだったか」を判断できる材料を作る。
    needs（判断待ち）と DELIVERY/archive（受領）双方の説明欄に使う。git でなければ ref/差分は空。

    成果物は **タスクの作業ブランチ（ap/<task-id>）** にある。cfg.workdir を見ると状態 worktree の
    bus/ 内部ファイル（claims/events の JSON）が「差分」として並び、人は何をレビューすればいいか
    分からない。作業ブランチが分かるならそちらの実体差分を出し、差分を開くコマンドも添える。
    複数リポジトリ案件では書込先を先頭に、参照リポジトリを続けて明示する。"""
    entries = delivery_entries(cfg, task, mr_url=mr_url, max_files=max_files) if task is not None else []
    # 書込先ブランチが meta にあれば structured 経路を使う。ref 未解決でも workdir（bus）へ
    # フォールバックしない（frontmatter delivery と判断材料の食い違いを防ぐ）。
    write_entries = [e for e in entries if e.get("role") == "write"]
    if write_entries:
        lines: "list[str]" = []
        multi = len(entries) > 1
        for e in entries:
            if multi or e.get("role") == "reference":
                role = "書込先" if e.get("role") == "write" else "参照（読取）"
                lines.append(f"### リポジトリ: {e['name']}（{role}）")
            if e.get("role") == "reference":
                lines.append(f"- 参照: {e.get('url') or e['name']}")
                if e.get("branch"):
                    lines.append(f"- ブランチ指定: `{e['branch']}`")
                lines.append("- 注: 参照リポジトリ。本タスクの成果差分は書込先を見る")
                continue
            if not e.get("ref"):
                lines += [
                    f"- 成果物: ブランチ `{e['branch']}`（ローカルで ref 未解決・差分取得不可）",
                    f"- 所在: {e.get('path') or '(ローカル path 未解決)'}",
                ]
                if e.get("mr_url"):
                    lines.append(f"- MR: {e['mr_url']}（承認時にクリーンなら自動マージ）")
                lines.append("- 注: 作業ブランチの ref を解決できなかったためローカル差分は省略"
                             "（MR があればそちらを確認）")
                continue
            files = e.get("files") or []
            total = int(e.get("files_total") or len(files))
            lines += [
                f"- 成果物: ブランチ `{e['branch']}`（{total} ファイル変更・base `{e['base']}`）",
                f"- 所在: {e.get('path') or '(ローカル path 未解決)'}",
            ]
            if e.get("diff_cmd"):
                lines.append(f"- 差分を見る: `{e['diff_cmd']}`")
            if e.get("mr_url"):
                lines.append(f"- MR: {e['mr_url']}（承認時にクリーンなら自動マージ）")
            if files:
                lines.append(f"- 変更ファイル（{total} 件）:")
                lines += [f"    - {p}" for p in files]
                if total > len(files):
                    lines.append(f"    - …他 {total - len(files)} 件")
            else:
                lines.append(f"- 変更ファイル: なし（`{e['base']}` と差が無い＝成果物が空）")
        lines.append(f"- 実行先: {location}")
        if verify is not None:
            res = "PASS" if ok else ("FAIL" if ok is not None else "?")
            vm = (vmsg or "").replace("\n", " ").strip()[:200]
            lines.append(f"- 検証: `{verify}` → {res}" + (f"（{vm}）" if vm else ""))
        return "\n".join(lines)

    # 作業ブランチが特定できないとき（単発実行・git 以外）は従来どおり workdir を見る
    ref = extract_delivery_ref(act_msg, cfg, git_base)
    branch = _current_branch(cfg)
    changed = sorted(meaningful_changes(cfg, git_base)) if git_base is not None else []
    where = str(cfg.workdir)
    if location == "remote" and cfg.git_bus:
        where += f"（git-bus: {cfg.git_bus}@{cfg.git_branch}）"
    lines = [f"- 成果物: {ref}",
             f"- 所在: {where}" + (f" / ブランチ {branch}" if branch else ""),
             f"- 実行先: {location}"]
    if mr_url:
        lines.append(f"- MR: {mr_url}（承認時にクリーンなら自動マージ）")
    if changed:
        shown = changed[:max_files]
        lines.append(f"- 差分: {len(changed)} ファイル")
        lines += [f"    - {p}" for p in shown]
        if len(changed) > len(shown):
            lines.append(f"    - …他 {len(changed) - len(shown)} 件")
    elif git_base is not None:
        lines.append("- 差分: baseline 以降の変更なし")
    if verify is not None:
        res = "PASS" if ok else ("FAIL" if ok is not None else "?")
        vm = (vmsg or "").replace("\n", " ").strip()[:200]
        lines.append(f"- 検証: `{verify}` → {res}" + (f"（{vm}）" if vm else ""))
    return "\n".join(lines)


_COST_RE = re.compile(r"@cost\b(?P<rest>.*)")


def parse_cost(act_msg: str) -> "tuple[int, float]":
    """act 出力からコストを計上する。エージェントが `@cost tokens=1234 usd=0.05` 形式の行を吐けば
    それを合算（1タスクで複数回呼ぶこともあるので加算）。マーカが無ければ (0, 0.0)。決定的・LLM 不要。"""
    tokens, usd = 0, 0.0
    for line in (act_msg or "").splitlines():
        m = _COST_RE.search(line)
        if not m:
            continue
        rest = m.group("rest")
        tm = re.search(r"tokens?\s*[=:]\s*([\d_]+)", rest)
        um = re.search(r"(?:usd|cost)\s*[=:]\s*([\d.]+)", rest)
        if tm:
            tokens += int(tm.group(1).replace("_", ""))
        if um:
            usd += float(um.group(1))
    return tokens, usd


def append_delivery(cfg: Config, task: Task, ref: str, ts: str, branch: str = "") -> None:
    """納品一覧（受領書）DELIVERY.md に1行追記する。成果参照はブランチも併記して所在を明確にする。"""
    path = cfg.delivery
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "" if path.exists() else (
        "# 納品一覧（受領書）\n\n| id | タイトル | 検収 | 成果参照 | 完了 |\n|---|---|---|---|---|\n")
    title = task.title.replace("|", "\\|")
    # 実成果物があるときだけブランチを併記する（"(変更なし)"/"(参照なし)" 等のセンチネルには付けない）
    show_branch = branch and not ref.startswith("(")
    cell = (f"{ref} @ {branch}" if show_branch else ref).replace("|", "\\|").replace("\n", " ")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{header}| {task.id} | {title} | PASS | {cell} | {ts} |\n")


def archive_task(cfg: Config, task: Task, vmsg: str, ref: str, ts: str, evidence: str = "") -> None:
    """done タスクを archive/<id>.md へ退避し、検収用の『納品書』を付す（backlog と1:1）。
    evidence（成果物の所在・差分・検証）を載せ、後から「どこに何が入ったか」を辿れるようにする。"""
    cfg.archive_dir().mkdir(parents=True, exist_ok=True)
    task.extra.append(("archived", ts))
    body = serialize_task(task) + (
        f"\n## 納品書\n"
        f"- 完了 : {ts}\n"
        f"- verify: `{task.verify}` → PASS（{vmsg}）\n"
        f"- 成果 : {ref}\n"
    )
    if evidence:
        body += f"\n## 判断材料（成果物の所在・差分・検証）\n{evidence}\n"
    _archive_write(cfg, task.id, body)
    delete_task_file(cfg, task)


def _archive_write(cfg: "Config", tid: str, body: str) -> None:
    """archive/<id>.md へ書く。既存（過去の同 id の退避）があれば上書きせず -2, -3… で退避する
    （明示 id の再投入や複数 charter の同名タスクで過去の記録を失わない）。"""
    adir = cfg.archive_dir()
    adir.mkdir(parents=True, exist_ok=True)
    dest = adir / f"{tid}.md"
    n = 2
    while dest.exists():
        dest = adir / f"{tid}-{n}.md"
        n += 1
    dest.write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
