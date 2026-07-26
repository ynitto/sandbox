from __future__ import annotations
# configfile.py — 元 agent-project.py の 10415-10913 行目（機械分割・内容無改変）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
# CLI
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 設定ファイル（agent-flow と同じ流儀: YAML 任意 / JSON フォールバック）
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
            print("[agent-project] ERROR: YAML 設定には PyYAML が必要です（pip install pyyaml）。"
                  "JSON 設定（agent-project.json・同じキー）なら不要です。", file=sys.stderr)
            sys.exit(1)
        with open(path, encoding="utf-8") as f:
            return json.load(f)


DEFAULT_CONFIG_NAMES = ["agent-project.yaml", "agent-project.yml", "agent-project.json"]


def _auto_node_name() -> str:
    """この PC の既定 node 名（`--node` / `AGENT_PROJECT_NODE` / host.yaml のどれも無いとき）。

    導出は `agentcore.nodeid.default_node_id` の 1 実装に寄せてある（P0-3）。以前はここに
    独自のサニタイズ（**小文字化しない**・60 文字で切る）があり、大文字を含むホスト名の PC が
    プロジェクト状態側と板側で 2 名義に割れていた——`Config.node` は `status/<node>.json` の
    ファイル名にもタスクの `- node:` にもなるので、綴りが割れると人が板の端末一覧を見て
    書いた割当を誰も拾わない。"""
    return default_node_id()


def _resolved_node(args) -> str:
    """`Config.node` を板と同じ綴りへ倒す（P0-3）。

    非正規形が入る経路は 3 つ: host.yaml 未宣言（ホスト名）・`AGENT_PROJECT_NODE`・`--node`。
    後ろ 2 つは `HostConfig` を通らないので正規化されていなかった。ここで 1 箇所に集める。

    **倒したときは黙らない**——「指定した名前で動いていない」ことに気付けないと、
    `- node:` の割当や板の名義がずれた理由が分からなくなる。"""
    raw = str(getattr(args, "node", None) or "").strip()
    if not raw:
        return default_node_id()
    node = normalize_node_id(raw)
    if node != raw:
        print(f"[agent-project] node 名を正規形へ揃えました: {raw} → {node}"
              f"（板・状態ファイル名の綴りを 1 つに保つため）", file=sys.stderr)
    return node

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
    # node 未指定タスクを既定でどの PC（ノード）が拾うか。プロジェクト共有設定（空＝誰でも／従来）。
    # 各 PC 固有の node 名は共有 yaml に置かず CLI --node / 環境変数 AGENT_PROJECT_NODE から取る。
    "default_node": "",
    "controller_heartbeat_sec": 30.0,
    "controller_lease_sec": 120.0,
    "coordination_retries": 3,
    "clock_skew_tolerance_sec": 30.0,
    "default_workspace": "",
    "location": "auto",
    "board": "",
    "board_workdir": None,
    "board_workload": "flow",
    "model": None,
    # LLM 実行に使うエージェント CLI: kiro（kiro-cli chat）/ claude（Claude Code `claude -p`）/
    # copilot（GitHub Copilot CLI `copilot -p`）/ codex（OpenAI Codex CLI `codex exec`）。
    # agent-project 自身の LLM 呼び出し
    # （分解・優先順位・裁定・ルーティング）に効く。実行層 agent-flow の CLI は
    # agent-flow 側の設定（flow_config / agent-flow.yaml の agent_cli）で揃える。
    "agent_cli": "kiro",
    "agent_timeout": 300.0,   # エージェント CLI 1 呼び出しのタイムアウト秒（0 以下で無効）
    # バックログ分解の粒度: coarse（ストーリー相当・既定）/ fine（単機能）/ finest（1ファイル/1関数）。
    # agent-flow の同名設定と語彙を揃えている（あちらは実行時 DAG、こちらは backlog の分解に効く）。
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
    # agent-flow バスの置き場（絶対パスで明示すると外部 daemon を検知できる）。None なら <root>/bus。
    # 設定ファイルの bus: をここに載せておかないと resolve_config が読まず黙って既定バスに落ちる
    # （daemon 非検知の原因になる）。CLI --bus と同義。
    "bus": None,
    "git_bus": None,
    "git_branch": "main",
    "git_subdir": None,
    "state_git_interval": 300.0,        # fetch/push の最短間隔（秒）。0 で毎同期
    # journal のローテーション: 閾値を超えたら journal-archive/ へ退避して新しい journal を始める。
    # 追記専用ファイルの肥大と、direct 同期での EOF 追記マージ衝突の温床を抑える。
    "journal_max_bytes": 262144,        # 閾値バイト（既定 256KB）。0 以下でローテーション無効
    "journal_keep": 20,                 # journal-archive/ の保持世代数。0 以下で無制限
    "flow_config": None,        # agent-flow へ --config で渡す共有 agent-flow.yaml（任意。agent-flow の設定はここに集約）
    # 状態専用リポジトリ（S1: 状態ルートは常に状態専用リポジトリの通常 clone）。
    # **宣言の唯一の置き場は host.yaml の `projects[]`**（clone を作る前に読める場所が要るため。
    # 状態リポジトリの中に書いても clone 済みの後にしか読めず、意味を持てない）。ここに残して
    # あるのは「Config が現在どの状態リポジトリで動いているか」を doctor・status が示すための
    # 導出値で、プロジェクト yaml から設定しても効かない（`_INERT_PROJECT_KEYS`＝警告して無視）。
    "state_repo": "",                   # 状態専用リポジトリの URL/パス（host.yaml projects[].state_repo）
    "state_repo_branch": "main",        # 状態を載せるブランチ（host.yaml projects[].branch）
    "status_interval": 0.0,             # watch アイドル中の status.json 生存信号更新間隔（秒）。既定 0=無効
    "agent_flow": None,
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
    "intake_cmd": None,         # JSON を返す外部の決定的検出器から修復タスクを汲み上げるコマンド
    "hooks": {},                # 任意フックのプロバイダ指定（能力キー -> module 名）。既定は sibling 自動検出
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
    "update_subdir": TOOL_SUBDIR,        # 取得対象パス（カンマ/空白区切りで複数。既定は本体+agentcore）
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
    # 3 段ルーティング（S7）: full 以上=フル spec / light 以上=ライト spec（design.md 1 枚）/
    # それ未満=スキップ。full は未設定（None）なら旧 `spec_threshold` を読む＝既存設定は
    # そのままフル spec の閾値として効く。採点は各軸 1〜3 なので上限 3。
    "spec_threshold": 3,
    "spec_threshold_full": None,
    "spec_threshold_light": 2,
    # リポジトリ理解の成果物化: plan 前に context/<repo名>.md を生成（sha キャッシュ）。読み出しは常時。
    "repo_map": False,
    # 効いた learn を rules.md（全タスク常時注入のプロジェクトルール）へ自動昇格。読み出しは常時。
    "rules_capture": True,
    # 処理毎のエージェント上書き（yaml 専用）。キーは plan/review/prioritize/route/adjudicate/
    # verify/distill/assess/repo_map/doctor、値は {agent_cli, model}。
    "agents": {},
    # タスク単位ターゲットブランチ: 成果物を ap/<task-id> に集約（agent-flow の workspace branch へ注入。
    # リトライ（r0/r1…）も同一ブランチに積み増す）。false で従来の run 毎 af/<run-id>。
    "task_branch": True,
    "task_branch_prefix": "ap/",
    # 成果物レビュー: verify PASS 後、常に review（検収待ち）→ 人の承認で done 確定。
    # review 到達時に GitLab 設定（GITLAB_TOKEN/GL_TOKEN）があれば ap/<task-id> → target の MR を
    # 自動作成し、承認時にクリーン（コンフリクト無し・未解決ディスカッション無し）なら自動マージする。
    # false で従来の unattended 自動 done。
    "delivery_review": True,
    # フォージ（MR/PR）側の決定的シグナルからの決着（S4）。
    #   settle  … マージ=承認 / 未マージクローズ=却下 / changes-requested=差し戻し として決着する
    #   observe … 照会結果を journal に残すだけ（移行用）
    # コメント本文のキーワード推定は使わない——書き手の言い回し 1 つで判定が変わり、
    # 変わったことに気づけない。差し戻しは「人がラベル／レビュー状態を明示したとき」。
    "remote_review": "settle",
    # 証跡ベースの検証（S5）。受入基準チェックリスト（`- acceptance:` 複数行）に対して
    # 検証エージェントが実行時にコマンドを試行錯誤し、基準ごとの判定＋証跡を返す。
    #   verifier          … false で従来どおり決定的 verify のみ（acceptance は表示だけ・移行用）
    #   verifier_skill    … プロンプト・出力契約を供給するスキル名（上位に置けば差し替え可）
    #   verify_side_effects … workspace=作業ツリー内のみ / network=読み取りの HTTP 到達まで許す
    #     （DB・外部サービスへの書き込みはどちらでも不可。検証は失敗するとリトライで何度も
    #      走るので副作用が累積する。そこまで要る検証は人が verify: に明示的に書く）
    "verifier": True,
    "verifier_skill": "backlog-verifier",
    "verify_side_effects": "workspace",
    # バックログ分解（S6）。charter → タスクの分解を backlog-planner スキルへ出す。
    #   planner_skill … プロンプト・出力契約を供給するスキル名（上位に置けば差し替え可。
    #     見つからなければ組み込みプロンプトへ落ちる＝計画は止めない）
    #   plan_sections … required=必須項目（why/desc/acceptance/size）の欠落を 1 回再要求し、
    #     なお欠けるタスクは proposed（plan_review off なら draft）で人の目へ回す /
    #     warn=注記だけ付けてそのまま投入
    "planner_skill": "backlog-planner",
    "plan_sections": "required",
    # 真偽フラグ（CLI > 設定ファイル > 既定）。CLI 未指定（None）なら設定ファイル→この既定で確定
    "watch": False, "once": False, "dry_run": False, "rot": False, "ltm": False,
    "require_progress": False, "auto_level": False, "review_project": False,
    "do_archive": True, "learn": True, "cleanup": True,   # do_archive: --archive はパス用なので別名
    "bus_keep_runs": 20,  # 掃除しても残す直近 run 数（viewer のフロータブが読む一次ソース）
    "with_flow": True,   # doctor: 実行層 agent-flow doctor も連携実行（CLI 既定 on・直接 Config は off）
}


# ---------------------------------------------------------------------------
# 設定 2 層の責務分離（S1）。「どのキーをどちらのファイルに書けるか」を **契約として固定** する。
#
#   agent-project.host.yaml（~/.agents/・共有しない） … このノードの宣言（何を動かすか・資源）
#   agent-project.yaml（状態リポジトリ直下・全 PC 共有） … プロジェクトの合意（どう動かすか）
#
# 分類が無いと、ノード固有の絶対パスや資源上限が state repo 経由で全 PC へ配られ（C1/C3 の
# 元凶）、逆に「全ノードで同一であるべき動作」が PC ごとに食い違って実行が非決定になる。
# 帰属は静的に検査する（`_validate_layers`）——黙って読み替えると、設定した本人が
# 「効いていない」ことに気付けない。
# ---------------------------------------------------------------------------

# 両方に書けるキー（ノード事情による上書きを許す群）。優先順位は
#   CLI > host.yaml projects[].overrides > host.yaml defaults > プロジェクト yaml > 組み込み既定。
# ここに入れてよいのは「ノードごとに違って **よい**（違っても実行の意味が変わらない）」もの
# だけ: 導入済み CLI・マシン性能・ノード局所のパス。判断や収束条件は入れない。
SHARED_KEYS = frozenset({
    "agent_cli", "model", "act_timeout", "verify_timeout", "location", "concurrency",
    # ノードごとに CLI 性能・操作者名義・通知手段が異なる
    "agent_timeout", "actor", "notify_cmd",
    # ノード局所のパスを指しうるキー。共有 yaml に絶対パスが載ると他 PC で壊れるため、
    # プロジェクト共通の既定を置きつつノードで差し替えられるようにする
    "ltm_home", "flow_config", "verify_cwd",
})

# host.yaml だけが値を持つ CONFIG_DEFAULTS キー（プロジェクト yaml から読まない）。
HOST_SOURCED_KEYS = frozenset({
    "board_workdir",            # git+ 板の clone 作業領域＝ノード局所パス
    "state_repo", "state_repo_branch",   # clone 前に読む宣言。projects[] が唯一の置き場
    # ツールの自動アップデートは「このノードのインストール管理」。プロジェクト共有設定に置くと
    # 更新の停止・更新元の差し替えが全 PC へ一斉に飛ぶ（あるノードだけ固定したい運用ができない）
    "update_enabled", "update_check_interval", "update_repo", "update_branch",
    "update_subdir", "update_installer",
})

# プロジェクト yaml に書いたらエラーにするキー（構造キーを含む）。
# 「読んでしまうと全 PC で誤って効く」ものだけを入れる——無害な残骸は `_INERT_PROJECT_KEYS`。
HOST_ONLY_KEYS = frozenset({
    "node", "node_id", "projects", "repos", "availability", "budget", "tags",
    "amigos_bus", "amigos_config", "residency", "defaults", "update",
    "board_workdir",
    "update_enabled", "update_check_interval", "update_repo", "update_branch",
    "update_subdir", "update_installer",
})

# プロジェクト yaml に残っていても **構造上ただの無効値** になるキー（警告して無視）。
# エラーにしない理由: このファイルは state repo 経由で全 PC が共有するため、無害な残骸で
# 全ノードを同時に落とす方が害が大きい。値 = 案内文。
_INERT_PROJECT_KEYS = {
    "root": "このファイルの置き場所（状態リポジトリ直下）が root です",
    "state_repo": "host.yaml の projects[].state_repo が唯一の置き場です（clone 前に読む必要があるため）",
    "state_repo_branch": "host.yaml の projects[].branch へ書いてください",
    "state_repo_dir": "clone 先は host.yaml の projects[].root で宣言してください",
    "state_git": "状態 clone の origin が同期先です（設定は不要になりました）",
    "state_commit_interval": "state_git_interval に一本化しました",
}

# 廃止した状態 worktree 方式のキー（検出したら fail-fast）。値 = 移行の案内。
# 黙って無視すると「バックアップされているつもりの未バックアップ状態」が続く——
# state_push / state_backup_branch は「共有できている」と誤認させる分だけ危険度が高い。
_REMOVED_WORKTREE_KEYS = {
    "state_worktree_dir", "state_branch", "state_commit", "state_push", "state_backup_branch",
}

# 上記のどれでもない CONFIG_DEFAULTS キー＝プロジェクト yaml 専有（host.yaml に書いたらエラー）。
# 差集合で定義するのは、キーを増やしたときに分類漏れが起きないようにするため
# （新しいキーは既定でプロジェクト共有側に落ちる＝安全側）。
PROJECT_ONLY_KEYS = frozenset(
    set(CONFIG_DEFAULTS) - SHARED_KEYS - HOST_SOURCED_KEYS - set(_INERT_PROJECT_KEYS))


def _layer_error(lines: "list[str]") -> "None":
    """設定の層違反を fail-fast で報せる（起動を止める）。"""
    print("\n".join(["[agent-project] 設定の置き場所が契約と違います:", *lines]), file=sys.stderr)
    sys.exit(2)


def _validate_layers(project: dict, cfg_path, defaults: dict, overrides: dict) -> None:
    """設定 2 層の帰属契約を検査する（S1）。

    プロジェクト yaml 側 = 「全 PC へ配られてしまうと困るもの」を弾く。
    host.yaml の defaults/overrides 側 = 「ノードごとに食い違うと実行が非決定になるもの」を弾く。
    どちらにも属さない未知キーは警告のみ（typo の検出。既存の黙殺をやめる）。"""
    where = f"（{cfg_path}）" if cfg_path else ""
    bad = sorted(k for k in project if k in _REMOVED_WORKTREE_KEYS)
    if bad:
        _layer_error([
            f"  廃止した状態 worktree 方式のキーが残っています{where}: {', '.join(bad)}",
            "  状態ルートは状態専用リポジトリの clone に一本化しました（S1）。",
            "  移行: bash tools/agent-project/migrate-state-repo.sh --state-dir <状態フォルダ> "
            "--state-repo <URL>",
            "  手順: docs/guides/state-repo-migration.md",
        ])
    bad = sorted(k for k in project if k in HOST_ONLY_KEYS)
    if bad:
        _layer_error([
            f"  ノード固有のキーがプロジェクト yaml にあります{where}: {', '.join(bad)}",
            "  これらは PC ごとに違う宣言なので、state repo 経由で全 PC へ配ると壊れます。",
            "  ~/.agents/agent-project.host.yaml へ移してください。",
        ])
    for key in sorted(k for k in project if k in _INERT_PROJECT_KEYS):
        print(f">>> 警告: {key} はプロジェクト yaml では効きません{where}（無視します）— "
              f"{_INERT_PROJECT_KEYS[key]}", file=sys.stderr)
    for layer, label in ((overrides, "projects[].overrides"), (defaults, "defaults")):
        bad = sorted(k for k in layer if k not in SHARED_KEYS)
        if bad:
            _layer_error([
                f"  host.yaml の {label}: に上書きできないキーがあります: {', '.join(bad)}",
                "  『プロジェクトとしてどう進めるか』の合意はノードごとに食い違うと実行が"
                "非決定になるため、状態リポジトリ直下の agent-project.yaml へ書いてください。",
                f"  上書きできるのは次のキーだけです: {', '.join(sorted(SHARED_KEYS))}",
            ])
    unknown = sorted(k for k in project
                     if k not in CONFIG_DEFAULTS and k not in _INERT_PROJECT_KEYS
                     and not str(k).startswith("_"))
    if unknown:
        print(f">>> 警告: 未知の設定キー{where}: {', '.join(unknown)}（無視します。"
              f"綴りを確認してください）", file=sys.stderr)


def _normalize_hooks(raw) -> dict:
    """`hooks:` を 能力キー -> module 名 の対応表へ正規化する。

    設定ファイルは人が手で書くので型は保証されない。壊れていても例外にせず空（＝sibling 自動
    検出）へ落とす——設定ミスでプロセスが起動しない方が、任意フックが効かないことより高くつく。
    値の妥当性（その名前が import できるか）はここでは見ない。解決は _hook_provider の責務で、
    明示指定が効いていないことは doctor が warn として可視化する。"""
    if not isinstance(raw, dict):
        return {}
    return {str(k).strip(): str(v).strip()
            for k, v in raw.items() if isinstance(v, str) and str(v).strip()}


def _project_config_path(explicit, root: Path):
    """プロジェクト設定ファイルの解決: --config 明示、無ければ **状態ルート直下のみ**。

    旧実装の探索チェーン（cwd → ./.agents → ./.agent → ~/.agents）は廃止した（S1）。
    状態ルートは常に状態専用リポジトリの clone で、プロジェクトの合意事項はその中に置く——
    「どこに置いたら読まれるのか」が探索順に依存すると、成果物リポジトリ側の古い yaml が
    黙って優先される事故（移行時に実際に起きた）を招く。"""
    if explicit:
        p = os.path.expanduser(str(explicit))
        if not os.path.isfile(p):
            print(f"[agent-project] 設定ファイルが見つかりません: {explicit}", file=sys.stderr)
            sys.exit(1)
        return p
    for name in DEFAULT_CONFIG_NAMES:
        cand = root / name
        if cand.is_file():
            return str(cand)
    _warn_legacy_config_locations(root)
    return None


def _warn_legacy_config_locations(root: Path) -> None:
    """旧探索先にだけ設定が在るときに 1 度だけ報せる（黙って既定で走らない）。

    探索チェーンを畳んだので、旧レイアウトのままのプロジェクトは「設定を書いたのに
    何も効かない」状態になる。無言だと原因が追えないため、見つけた場所を名指しする。"""
    legacy = [Path(base) / name
              for base in (root / AGENT_HOME, root / AGENT_HOME_LEGACY, agent_home_dir())
              for name in DEFAULT_CONFIG_NAMES]
    found = [str(p) for p in legacy if p.is_file()]
    if found:
        print(f">>> 警告: 設定ファイルの探索は状態ルート直下のみになりました（S1）。"
              f"次のファイルは読まれません: {', '.join(found)} — "
              f"内容を {root / DEFAULT_CONFIG_NAMES[0]} へ移してください", file=sys.stderr)


def _norm_path_key(p) -> str:
    """パス一致判定用の正規化（絶対化・シンボリックリンク解決）。解決できなければ素の絶対パス。"""
    try:
        return str(Path(str(p)).expanduser().resolve())
    except (OSError, RuntimeError, ValueError):
        return str(Path(str(p)).expanduser().absolute())


def _match_host_project(host, args) -> dict:
    """このプロセスが駆動するプロジェクト宣言を host.yaml から特定する（S1 §3.1 の対象決定）。

    a) --project <name> … 名前で選ぶ（無ければ fail-fast。黙って別プロジェクトを動かさない）
    b) --state-repo <url> … ad-hoc 起動（host.yaml の宣言を使わない）
    c) --root / cwd … root 一致の宣言があればそれ（overrides が効く）。無ければ ad-hoc
    """
    projects = [p for p in getattr(host, "projects", []) or [] if isinstance(p, dict)]
    name = str(getattr(args, "project", "") or "").strip()
    if name:
        for p in projects:
            if str(p.get("name") or "").strip() == name or _project_name(p) == name:
                return p
        known = ", ".join(sorted(_project_name(p) for p in projects)) or "(宣言なし)"
        print(f"[agent-project] host.yaml に project '{name}' がありません（宣言済み: {known}）",
              file=sys.stderr)
        sys.exit(2)
    if str(getattr(args, "state_repo", "") or "").strip():
        return {}
    target = _norm_path_key(getattr(args, "root", None) or ".")
    for p in projects:
        if p.get("root") and _norm_path_key(p["root"]) == target:
            return p
    return {}


def _state_root_markers(root: Path) -> "list[str]":
    """状態ルートらしさの根拠（見つかったマーカー名）。空なら状態ルートではない。

    **`agent-project.{yaml,yml,json}` はマーカーに入れない。** 移行前の構成ではまさにそれが
    成果物リポジトリ直下に置かれていた（`state_repo:` を clone 前に読むためのブートストラップ）
    ので、マーカーに数えると「弾きたい相手」が全員すり抜ける。"""
    return [name for name in ("project.json", "backlog", "charter.md", "charters",
                              "needs", "decisions", "archive", "DELIVERY.md",
                              "repos.json", "policy.md", "rules.md")
            if (root / name).exists()]


def _looks_like_fresh_root(root: Path) -> bool:
    """まだ何も入っていないルート（新規プロジェクトの初期化）か。

    「.git 以外に何も無い」で判定する。コミットの有無ではなく中身で見るのは、空リポジトリの
    clone も `git init` 直後のディレクトリも同じ『これから状態を作る場所』だから。"""
    try:
        return not any(p.name != ".git" for p in root.iterdir())
    except OSError:
        return True


def _ensure_state_clone(root: Path, state_repo: str, branch: str) -> None:
    """状態専用リポジトリを root へ確保する（既にあれば origin の同一性だけ確かめる）。

    旧実装は origin 不一致・clone 失敗を **worktree 方式への暗黙フォールバック** で吸収して
    いた（`_redirect_root_to_state_repo`）。移行が効いていないことに誰も気付けないまま、
    状態が旧構成へ書かれ続ける事故の元だったので、S1 では必ず止める。"""
    if (root / ".git").exists():
        origin = _git_line(root, "remote", "get-url", "origin") or ""
        # origin が一致すれば正しい clone。**origin が無い場合も素通りさせない**——成果物
        # リポジトリ（`git init` しただけで remote 未設定）を root に宣言してしまった構成が
        # そのまま通ると、状態がそこへ書かれた上に origin まで生やしてしまう。
        ok = _same_git_remote(origin, state_repo) if origin \
            else bool(_state_root_markers(root) or _looks_like_fresh_root(root))
        if not ok:
            print(f"[agent-project] {root} は宣言された状態リポジトリの clone ではありません。\n"
                  f"  宣言（host.yaml projects[].state_repo）: {state_repo}\n"
                  f"  実際の origin                          : {origin or '(未設定)'}\n"
                  f"  root を退避するか、host.yaml の state_repo / root の宣言を直してください。",
                  file=sys.stderr)
            sys.exit(2)
        return
    if root.exists() and not _looks_like_fresh_root(root):
        print(f"[agent-project] {root} は git リポジトリではないのに中身があります。"
              f"状態リポジトリを clone できません（既存の内容を壊さないため中止）。\n"
              f"  空のディレクトリを root に宣言するか、その場所を退避してください。",
              file=sys.stderr)
        sys.exit(2)
    if not _ensure_state_repo_clone(state_repo, root, branch):
        print(f"[agent-project] 状態リポジトリを clone できませんでした: {state_repo} → {root}\n"
              f"  ネットワーク・認証・URL を確認してください（旧 worktree 方式への"
              f"フォールバックは廃止しました）。", file=sys.stderr)
        sys.exit(2)


def _check_adhoc_state_root(root: Path) -> None:
    """状態リポジトリの宣言が無いまま起動したときの root 検査（S1 §3.1 の単発 run 契約）。

    守りたいのはただ 1 つ、**成果物リポジトリを状態ルートにしてしまう事故**。そこで動かすと
    人のコードリポジトリに backlog/ や journal.md が生えて git status が永久に汚れる。

    - git 管理外 → 許す（ローカル縮退。次の同期で `git init` される）。**中身の有無は見ない**
      ——普通のディレクトリは誰のリポジトリでもないので、上の事故は起こりえない。
    - 他リポジトリの内側（自分はトップレベルでない） → 中止。`_ensure_direct_state_git` が
      nested repo を作らないために黙って同期を諦める構成で、起動できても状態は共有されない。
    - トップレベルだが状態マーカーが無く、中身がある → 成果物リポジトリとみなして中止。
    """
    if not root.exists():
        return
    top = _git_toplevel_of(root)
    if top is None:
        return
    if _looks_like_fresh_root(root) or _state_root_markers(root):
        return
    if _norm_path_key(top) != _norm_path_key(root):
        print(f"[agent-project] {root} は git リポジトリ {top} の内側です。\n"
              f"  状態ルートは状態専用リポジトリの clone（そのトップレベル）である必要があります。\n"
              f"  そこで起動すると状態が成果物リポジトリへ書き込まれ、共有もされません。",
              file=sys.stderr)
        sys.exit(2)
    hint = ""
    for name in DEFAULT_CONFIG_NAMES:
        legacy = root / name
        if not legacy.is_file():
            continue
        try:
            url = str((_load_config_file(str(legacy)) or {}).get("state_repo") or "").strip()
        except (OSError, ValueError, SystemExit):
            url = ""
        if url:
            hint = (f"\n  この場所の {name} には state_repo: {url} が残っています"
                    f"（旧ブートストラップ設定。もう読まれません）。\n"
                    f"  その URL を ~/.agents/agent-project.host.yaml の projects[].state_repo へ"
                    f"転記し、projects[].root には状態 clone のパスを宣言してください。")
        break
    print(f"[agent-project] {root} は状態ルートに見えません（成果物リポジトリではありませんか）。\n"
          f"  状態ルートは状態専用リポジトリの clone です。--state-repo <URL> を渡すか、\n"
          f"  ~/.agents/agent-project.host.yaml の projects[] で宣言してください。{hint}\n"
          f"  手順: docs/guides/state-repo-migration.md", file=sys.stderr)
    sys.exit(2)


def _resolve_state_root(args, entry: dict) -> Path:
    """状態ルート（= 状態専用リポジトリの clone）を確定して返す（S1 §3.1）。

    旧実装の「成果物リポジトリを root に取り、状態を worktree / 別 clone へリダイレクトする」
    2 段構えは廃止した。root は最初から状態リポジトリで、`source_root` / `state_top` という
    リダイレクト前後の二重表現も要らなくなる。"""
    state_repo = str(getattr(args, "state_repo", "") or "").strip() \
        or str(entry.get("state_repo") or "").strip()
    branch = str(getattr(args, "state_repo_branch", "") or "").strip() \
        or str(entry.get("branch") or "").strip() or "main"
    raw = getattr(args, "root", None) or entry.get("root")
    if not raw and entry:
        # host.yaml の宣言に root が無い＝clone 先が cwd 依存になる。常駐体の子は cwd を
        # 当てにできないので、ここで止めて宣言を促す（黙って cwd 配下に作らない）。
        print(f"[agent-project] host.yaml の projects[] に root がありません: "
              f"{entry.get('name') or entry.get('state_repo') or entry}\n"
              f"  root には状態リポジトリの clone 先を絶対パスで宣言してください。",
              file=sys.stderr)
        sys.exit(2)
    if not raw and state_repo:
        # --state-repo だけで起動したとき（ad-hoc）の既定 clone 先は cwd 配下の <リポジトリ名>。
        raw = Path.cwd() / _repo_basename(state_repo)
    root = Path(str(raw or ".")).expanduser()
    root = (root if root.is_absolute() else (Path.cwd() / root)).resolve()
    args.state_repo, args.state_repo_branch = state_repo, branch
    if state_repo:
        _ensure_state_clone(root, state_repo, branch)
    else:
        _check_adhoc_state_root(root)
    return root


def _repo_basename(url: str) -> str:
    """git URL/パスから clone 先ディレクトリ名を導く（`git clone` の既定と同じ規則）。"""
    s = str(url or "").strip().rstrip("/")
    if s.endswith(".git"):
        s = s[:-4]
    name = re.split(r"[/:]", s)[-1] if s else ""
    return name or "state"


def _host_layer(host, entry: dict) -> dict:
    """host.yaml が値を持つ CONFIG_DEFAULTS キー（`HOST_SOURCED_KEYS`）を集める。"""
    update = getattr(host, "update", None) or {}
    layer = {f"update_{k}": v for k, v in update.items()
             if f"update_{k}" in HOST_SOURCED_KEYS and v is not None}
    if getattr(host, "board_workdir", None):
        layer["board_workdir"] = host.board_workdir
    if entry.get("board_workdir"):
        layer["board_workdir"] = entry["board_workdir"]
    return layer


def resolve_config(args):
    """CLI 未指定値を host.yaml（ノード固有）→ プロジェクト yaml（共有）→ 既定で埋める（S1）。

    層の優先順位: CLI > projects[].overrides > defaults > プロジェクト yaml > 組み込み既定。
    `HOST_SOURCED_KEYS` だけは host.yaml が単一ソース（プロジェクト yaml を読まない）。"""
    host = load_host_config(getattr(args, "host_config", None))
    entry = _match_host_project(host, args)
    root = _resolve_state_root(args, entry)
    args.root = str(root)
    path = _project_config_path(getattr(args, "config", None), root)
    cfg = _load_config_file(path) if path else {}
    if not isinstance(cfg, dict):
        cfg = {}
    overrides = dict(entry.get("overrides") or {})
    defaults = dict(getattr(host, "defaults", None) or {})
    _validate_layers(cfg, path, defaults, overrides)
    host_layer = _host_layer(host, entry)
    args._config_path = path
    args._host_config_path = getattr(host, "path", None)
    for key, dflt in CONFIG_DEFAULTS.items():
        if getattr(args, key, None) is not None:
            continue                                  # 1) CLI が最優先
        if key in HOST_SOURCED_KEYS:
            setattr(args, key, host_layer.get(key, dflt))
        elif key in overrides:
            setattr(args, key, overrides[key])        # 2) ノード×プロジェクトの上書き
        elif key in defaults:
            setattr(args, key, defaults[key])         # 3) ノード全体の既定
        else:
            setattr(args, key, cfg.get(key, dflt))    # 4) プロジェクトの合意 → 5) 組み込み既定
    if getattr(args, "node", None) is None:
        # 宣言 > 環境変数 > ホスト名（build_config の `_auto_node_name`）。宣言を環境変数に
        # 負けさせると、host.yaml に node_id を書いた PC が起動元シェル次第で別名になる。
        args.node = (getattr(host, "node_id", "") if getattr(host, "node_id_declared", False)
                     else "") or os.environ.get("AGENT_PROJECT_NODE", "")
    # 稼働時間帯は PC 単位の性質なので host.yaml が単一ソース（旧 profile の唯一の実質項目）。
    args.availability = dict(getattr(host, "availability", None) or {})
    _warn_deprecated_coordination(args, cfg, path)
    _warn_removed_flags(args)
    return args


def _warn_removed_flags(args) -> None:
    """廃止した CLI フラグを黙って捨てない（`_warn_deprecated_coordination` と同じ流儀）。"""
    if getattr(args, "_removed_profile", None) is not None:
        _layer_error([
            "  --profile は廃止しました（PC 固有 profile → host.yaml へ吸収・S1）。",
            "  転記: profile.root → projects[].root / profile.node → node_id /",
            "        profile.availability → availability / profile.project_config → 廃止",
            "        （設定は状態リポジトリ直下の agent-project.yaml が正）。",
        ])
    if getattr(args, "_removed_state_repo_dir", None) is not None:
        _layer_error([
            "  --state-repo-dir は廃止しました（S1）。clone 先は root そのものです。",
            "  --root <clone 先> を渡すか、host.yaml の projects[].root で宣言してください。",
        ])


def _warn_deprecated_coordination(args, cfg: dict, path) -> None:
    """廃止した `coordination` 指定（yaml キー・CLI フラグ）を黙って捨てない（実装計画 W1-8）。

    設定読み込みは CONFIG_DEFAULTS のキーだけを走査するため、既存 yaml に残った
    `coordination: git-cas` はエラーにも警告にもならず消える。挙動が変わったのに設定は
    残っている状態は原因を追えないので、一度だけ stderr へ出して移行先を示す。"""
    used = [src for src, hit in (
        (f"設定ファイル {path}" if path else "設定ファイル", "coordination" in (cfg or {})),
        ("--coordination", getattr(args, "_deprecated_coordination", None) is not None),
    ) if hit]
    if used:
        print(f">>> 警告: {' と '.join(used)} の coordination 指定は廃止されました（無視します）。"
              "複数 PC 制御は origin と status/ に観測される他ノードの有無で自動判定します",
              file=sys.stderr)


def _spec_thresholds(args) -> dict:
    """spec ルーティングの 2 閾値を確定する（S7）。

    後方互換: `spec_threshold_full` の明示が無ければ旧 `spec_threshold` を full として読む。
    採点は各軸 1〜3（`_assess_max` は最大値）なので上限は 3 にクランプする——仕様の草案は
    「full=4 相当」としていたが、4 には決して到達しないので **full=3 / light=2** が既定。
    light > full の設定は意味を成さない（ライトの窓が消える）ので light を full に丸める。
    """
    def _clamp(v, dflt):
        try:
            return min(3, max(1, int(v if v is not None else dflt)))
        except (TypeError, ValueError):
            return dflt

    legacy = _clamp(getattr(args, "spec_threshold", None), 3)
    full = _clamp(getattr(args, "spec_threshold_full", None), legacy)
    light = min(_clamp(getattr(args, "spec_threshold_light", None), 2), full)
    return {"spec_threshold": legacy, "spec_threshold_full": full,
            "spec_threshold_light": light}


def _one_of(value, allowed: "tuple[str, ...]", dflt: str, key: str) -> str:
    """値域を持つ設定キーを正規形へ倒す。**倒したときは黙らない**——`observe` を
    `observ` と綴り間違えても既定へ落ちるだけだと、設定した本人が「効いていない」ことに
    気付けない（S1 の設計動機と同じ）。"""
    got = str(value or "").strip().lower()
    if got in allowed:
        return got
    if got:
        print(f"[agent-project] 設定 {key}: 未知の値 '{value}' — 既定 '{dflt}' を使います"
              f"（有効値: {' / '.join(allowed)}）", file=sys.stderr)
    return dflt


def build_config(args) -> Config:
    # プロジェクトルート = 状態専用リポジトリの clone（`resolve_config` が確定済み・S1）。
    # charter.md / repos.json / backlog/ 等はすべてこの直下（1 プロジェクト = 1 状態リポジトリ =
    # 1 プロセス）で、相対パスの上書きもすべて root 基準で解決する。
    #
    # 旧実装の「成果物リポジトリを root に取り、状態を worktree / 別 clone へリダイレクトする」
    # 2 段構え（state_top / source_root）は廃止した。リダイレクト前後で root が 2 つある状態は、
    # start/stop の照合・検収 diff・インスタンスレジストリがそれぞれ別の値を使う温床だった。
    root = Path(str(args.root or ".")).expanduser()
    root = (root if root.is_absolute() else (Path.cwd() / root)).resolve()
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

    availability = dict(getattr(args, "availability", {}) or {})
    cfg = Config(
        backlog=under("backlog", "backlog"),
        policy=under("policy", "policy.md"),
        decisions=under("decisions", "decisions"),
        journal=under("journal", "journal.md"),
        needs=under("needs", "needs"),
        workdir=workdir,
        bus=under("bus", "bus"),
        git_bus=args.git_bus, git_branch=args.git_branch, git_subdir=args.git_subdir,
        state_git_interval=max(0.0, float(getattr(args, "state_git_interval", 300.0) or 0.0)),
        flow_config=getattr(args, "flow_config", None) or None,
        status_interval=max(0.0, float(getattr(args, "status_interval", 0.0) or 0.0)),
        state_repo=str(getattr(args, "state_repo", "") or ""),
        state_repo_branch=str(getattr(args, "state_repo_branch", "main") or "main"),
        force=bool(getattr(args, "force", False)),
        agent_flow=args.agent_flow, planner=args.planner, flow_planner=args.flow_planner,
        # node は resolve_config が host.yaml（node_id）→ 環境変数の順で確定済み。
        # どこにも無いときだけホスト名由来の既定へ落とす。綴りの正規化はどの経路から来ても
        # `_resolved_node` の 1 箇所で行う（板・状態ファイル名と同じ綴りに保つ・P0-3）。
        node=_resolved_node(args),
        default_node=str(getattr(args, "default_node", "") or "").strip(),
        availability=availability,
        controller_heartbeat_sec=max(1.0, float(getattr(args, "controller_heartbeat_sec", 30.0) or 30.0)),
        controller_lease_sec=max(1.0, float(getattr(args, "controller_lease_sec", 120.0) or 120.0)),
        coordination_retries=max(1, int(getattr(args, "coordination_retries", 3) or 3)),
        clock_skew_tolerance_sec=max(0.0, float(
            availability.get("clock_skew_tolerance_sec",
                             getattr(args, "clock_skew_tolerance_sec", 30.0)) or 0.0)),
        route_planner=str(getattr(args, "route_planner", "agent") or "agent"),
        default_workspace=str(getattr(args, "default_workspace", "") or ""),
        location=args.location,
        board=str(getattr(args, "board", "") or ""),
        board_workdir=getattr(args, "board_workdir", None) or None,
        board_workload=str(getattr(args, "board_workload", "flow") or "flow"),
        executor=args.executor,
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
        act_timeout=args.act_timeout,
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
        # 型が壊れていても例外にせず既定（自動検出）へ落とす。設定ミスは doctor が warn で見せる。
        hooks=_normalize_hooks(getattr(args, "hooks", None)),
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
        **_spec_thresholds(args),
        # S5 の検証設定は CONFIG_DEFAULTS にあるだけで Config へ渡されておらず、
        # `verifier: false` も `verifier_skill:` も**設定しても効かなかった**（読み出しは
        # getattr の既定に落ちていた）。プロジェクト yaml の合意事項が黙って無視される類の
        # 欠落なので、S6 の設定追加と同時に配線する。
        verifier=bool(getattr(args, "verifier", True)),
        verifier_skill=str(getattr(args, "verifier_skill", "") or "backlog-verifier"),
        verify_side_effects=(str(getattr(args, "verify_side_effects", "") or "workspace")
                             if str(getattr(args, "verify_side_effects", "")) in
                             ("workspace", "network") else "workspace"),
        planner_skill=str(getattr(args, "planner_skill", "") or "backlog-planner"),
        plan_sections=(str(getattr(args, "plan_sections", "") or "required")
                       if str(getattr(args, "plan_sections", "")) in ("required", "warn")
                       else "required"),
        repo_map=bool(getattr(args, "repo_map", False)),
        rules_capture=bool(getattr(args, "rules_capture", True)),
        agents=_AGENT_OVERRIDES,
        task_branch=bool(getattr(args, "task_branch", True)),
        task_branch_prefix=str(getattr(args, "task_branch_prefix", "ap/") or "ap/"),
        delivery_review=bool(getattr(args, "delivery_review", True)),
        # S4-5。`CONFIG_DEFAULTS` にキーはあるのに Config へ渡っておらず、プロジェクト yaml に
        # `observe` と書いても常に settle になっていた（verifier キーと同型の 2 度目の欠落）。
        # 再発は tests/test_config_keys.py の構造テストで塞ぐ。
        remote_review=_one_of(getattr(args, "remote_review", "settle"),
                              ("settle", "observe"), "settle", "remote_review"),
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
    return cfg


def _add_common(sp):
    # 設定ファイルで上書き可能なキー（CONFIG_DEFAULTS）は default=None にし、resolve_config で確定する
    # （CLI > 設定ファイル > 組み込み既定）。個別パス上書きと真偽フラグは CLI 専用。
    sp.add_argument("--config", default=None,
                    help="プロジェクト設定ファイル（未指定なら状態ルート直下の "
                         "agent-project.{yaml,yml,json}）")
    sp.add_argument("--host-config", dest="host_config", default=None,
                    help="agent-project.host.yaml の場所（既定: cwd → ~/.agents の順に探索）")
    sp.add_argument("--project", default=None,
                    help="host.yaml の projects[] から名前で選ぶ（root/state_repo/overrides を"
                         "その宣言から取る）")
    sp.add_argument("--root", default=None,
                    help="状態ルート＝状態専用リポジトリの clone（cwd 相対、既定 . = cwd）。"
                         "charter.md / backlog/ 等はこの直下で、相対パスの上書きもすべて"
                         "この root 基準で解決される")
    # 廃止フラグの受け口（`--coordination` と同じ流儀）。**消さずに残す**のは argparse の
    # 前方一致対策——消すと `--profile x` が無関係なフラグ名のエラーになり、`--state-repo-dir`
    # は「unrecognized argument」で移行先を案内できない。値が来たら _warn_removed_flags が
    # 移行手順付きで止める。
    sp.add_argument("--profile", dest="_removed_profile", default=None, help=argparse.SUPPRESS)
    sp.add_argument("--state-repo-dir", dest="_removed_state_repo_dir", default=None,
                    help=argparse.SUPPRESS)
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
    sp.add_argument("--bus", default=None, help="agent-flow バス（root 相対、既定 <root>/bus）")
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
    sp.add_argument("--state-git-interval", type=float, default=None,
                    help="state_git の fetch/push の最短間隔（秒。既定 300）。リモートサーバへの"
                         "負荷を一定に保つ律速。0 で毎同期")
    sp.add_argument("--state-repo", default=None,
                    help="状態専用リポジトリの URL/パス（host.yaml を使わない単発起動用）。"
                         "root がまだ無ければそこへ clone する。既定の clone 先は "
                         "<cwd>/<リポジトリ名>（--root で明示可）")
    sp.add_argument("--state-repo-branch", default=None,
                    help="状態専用リポジトリ内で状態を載せるブランチ（既定 main）")
    sp.add_argument("--status-interval", type=float, default=None,
                    help="watch アイドル中に status.json（生存信号。リモート viewer の稼働判定に使う）を"
                         "更新する間隔（秒。既定 0＝無効）。0 のままなら idle 中に status.json は触らず、"
                         "state_git への追加コミットは生まない（実パスの完了時にのみ書く＝相乗り）。"
                         ">0 にすると idle でもこの間隔で 1 回だけ書き直し、その分だけ state_git の"
                         "コミットが増える（負荷とリモートでの生存判定の鮮度のトレードオフ）")
    sp.add_argument("--agent-flow", default=None)
    sp.add_argument("--node", dest="node", default=None,
                    help="この PC（エンジン）のノード名（複数 PC のバックログ分担）。指定すると "
                         "node 割当が一致するタスクと未割当タスク（default_node 規則）だけを消化する。"
                         "環境変数 AGENT_PROJECT_NODE でも指定可。未指定時は hostname から自動決定")
    sp.add_argument("--controller-heartbeat-sec", type=float, default=None,
                    help="controller lease 更新間隔（秒・既定 30）")
    sp.add_argument("--controller-lease-sec", type=float, default=None,
                    help="controller lease 有効期間（秒・既定 120）")
    sp.add_argument("--coordination-retries", type=int, default=None,
                    help="Git CAS 競合時の再試行回数（既定 3）")
    # 廃止済み（実装計画 W1-8）。**受け口だけ残す**のは argparse の前方一致対策——消すと
    # 旧 `--coordination git-cas` が `--coordination-retries` に解決され、
    # 「invalid int value: 'git-cas'」という無関係なフラグ名のエラーになる（さらに
    # `--coordination 3` はエラーにすらならず retries=3 として通ってしまう）。
    sp.add_argument("--coordination", dest="_deprecated_coordination", default=None,
                    help=argparse.SUPPRESS)
    sp.add_argument("--clock-skew-tolerance-sec", type=float, default=None,
                    help="ノード間の時刻ずれ許容秒（既定 30）")
    sp.add_argument("--planner", default=None, choices=["agent", "none"],
                    help="優先順位付け: agent=エージェント委譲（priority 加味）/ none=priority＋古さ（既定 agent）")
    sp.add_argument("--flow-planner", default=None,
                    choices=["flow-planner", "agent", "stub"], help="agent-flow run に渡す planner（既定 flow-planner）")
    sp.add_argument("--location", default=None,
                    choices=["auto", "local", "board"],
                    help="act の実行モード（既定 auto）")
    sp.add_argument("--board", default=None,
                    help="委譲公示板（agent-board）の場所（ローカル dir / git+<url>）。設定すると "
                         "location=auto は offload ポリシー一致タスクを板へ post する（依頼側自動配線）")
    sp.add_argument("--board-workdir", dest="board_workdir", default=None,
                    help="git+ 板のクローン作業領域（既定は自動）")
    sp.add_argument("--board-workload", dest="board_workload", default=None,
                    choices=["flow", "amigos"], help="板へ公示する workload（既定 flow）")
    sp.add_argument("--executor", default=None,
                    help="act の実体（agent-flow run へ委譲）。組み込み agent / stub、または agent-flow の "
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
    # 自律裁定: needs に落とす前に エージェント CLI が積み直し可否を判断（三値: 未指定→設定ファイル/既定 on）
    sp.add_argument("--auto-adjudicate", dest="auto_adjudicate", action="store_true", default=None,
                    help="人の判断(needs)へ送る前に エージェント CLI が『自律的に積み直すか人へ回すか』を裁定（既定 on）")
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
                    help="外部の決定的検出器から修復タスクを汲み上げるコマンド（stdout の "
                         "enqueue --json 形式を冪等取り込み。"
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
                    help="フル spec の採点しきい値の別名（--spec-threshold-full の旧名。1-3）")
    sp.add_argument("--spec-threshold-full", dest="spec_threshold_full", type=int, default=None,
                    help="フル spec（spec/design/tasks の 3 点セット + 展開）に乗せる採点しきい値"
                         "（max(c,r,a) がこの値以上。1-3・既定 3）")
    sp.add_argument("--spec-threshold-light", dest="spec_threshold_light", type=int, default=None,
                    help="ライト spec（design.md 1 枚・展開なし）に乗せる採点しきい値"
                         "（1-3・既定 2。full 未満のときだけライトになる）")
    sp.add_argument("--planner-skill", dest="planner_skill", default=None,
                    help="バックログ分解のプロンプト・出力契約を供給するスキル名（既定 backlog-planner）")
    sp.add_argument("--plan-sections", dest="plan_sections", default=None,
                    choices=["required", "warn"],
                    help="バックログの必須項目（why/desc/acceptance/size）の扱い。"
                         "required=欠落を 1 回再要求し、なお欠ければ人の目へ回す（既定）/ warn=注記のみ")
    sp.add_argument("--repo-map", action=argparse.BooleanOptionalAction, default=None,
                    help="リポジトリ理解の成果物化: plan 前に書込先 repo ごとに context/<repo名>.md を"
                         "生成（HEAD sha キャッシュ・変化時のみ再生成）。読み出しは常時（既定 off）")
    sp.add_argument("--rules-capture", action=argparse.BooleanOptionalAction, default=None,
                    help="効いた学習（learn の auto-resolve が閾値回数以上）を rules.md（プロジェクト"
                         "ルール・全タスク常時注入）へ自動昇格（既定 on。rules.md の注入自体は常時）")
    sp.add_argument("--task-branch", action=argparse.BooleanOptionalAction, default=None,
                    help="タスク単位ターゲットブランチ（ap/<task-id> に成果を集約。既定 on）")
    sp.add_argument("--delivery-review", action=argparse.BooleanOptionalAction, default=None,
                    help="成果物レビュー: verify PASS 後、常に検収待ち（review）にして人の承認で done"
                         "（--no-delivery-review で従来の自動 done。既定 on）")


# ---------------------------------------------------------------------------
