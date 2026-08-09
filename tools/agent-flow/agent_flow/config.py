from __future__ import annotations
# config.py — 元 agent-flow.py の 84-303 行目（機械分割・内容無改変）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
# --------------------------------------------------------------------------
# 設定ファイル（agent-loop と同じ流儀: YAML 任意 / JSON フォールバック）
# --------------------------------------------------------------------------
try:
    import yaml  # type: ignore

    def _load_config_file(path: str) -> dict:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
except ImportError:  # PyYAML 無し → JSON のみ
    yaml = None  # type: ignore

    def _load_config_file(path: str) -> dict:  # type: ignore[misc]
        if path.lower().endswith((".yaml", ".yml")):
            print("[agent-flow] ERROR: YAML 設定には PyYAML が必要です（pip install pyyaml）。"
                  "JSON 設定なら不要です。", file=sys.stderr)
            sys.exit(1)
        with open(path, encoding="utf-8") as f:
            return json.load(f)


DEFAULT_CONFIG_NAMES = ["agent-flow.yaml", "agent-flow.yml", "agent-flow.json"]

# このツールがスキルリポジトリ内に置かれているサブディレクトリ（自動アップデートの参照先）。
# 自動アップデートは update_repo のこのパス以下だけを temp 領域へ sparse-checkout して
# install.sh を実行する（doctor と同じ流儀で、操作は決定的・無関係ファイルは取得しない）。
# 取得対象パス（カンマ/空白区切りで複数）。**共有物 tools/agent-tools（統合インストーラ +
# agentcore）も並べる**——cone mode の sparse-checkout は兄弟を含まないので、本体だけ取ると
# installer が agentcore を同梱できず必ず失敗する（自己更新がサイレントに見送られ続ける）。
# 先頭が installer とダイジェストの基準ディレクトリ。
TOOL_SUBDIR = "tools/agent-flow tools/agent-tools"
# スキルリポジトリ（git URL/パス）の既定。空なら install.py が生成する skill-registry.json から
# 自動解決する（repositories.origin.url → install_dir）。設定ファイルの update_repo で明示も可。
DEFAULT_UPDATE_REPO = ""
# skill-registry.json を探すエージェントホーム（install.py の AGENT_DIRS に対応）。
_AGENT_HOME_DIRS = (AGENT_HOME, ".kiro", ".claude", ".copilot", ".codex")

# 環境ごとに変わる値の組み込み既定。設定ファイルのキーもこの名前（snake_case）。
CONFIG_DEFAULTS = {
    # バスはカレントディレクトリ（=プロジェクトルート）直下の bus/。agent-project の既定
    # <root>/bus と同じ場所を指す（1 root = 1 プロジェクト・root 相対で両ツールが一致する）。
    "bus": "./bus",
    "git": None,
    "git_branch": "main",
    "git_subdir": "",
    # ノード識別子（既定: PC 名を正規化した値）。--node-id は argparse 既定が None のため、
    # ここに無いと設定ファイルの node_id が resolve_config でマージされず既定のままになっていた
    # （board 参加ノードは再起動のたび ID が変わり、落札済み委譲の成果報告 status/<who>.json を
    # 見失う＝board 側がスタックする。固定 ID が必須の場合はここで明示する）。
    "node_id": None,
    # 状態の git 保存・共有（state_git）: ローカルバスのワーク内容（runs/・inbox/）を共有 git
    # リポジトリへ双方向同期し、リモートの agent-dashboard が run の進捗/結果を読めるようにする。
    # GitBus（--git）とは独立（--git 指定時はバス自体が共有 git なので state_git は無視される）。
    "state_git": None,                  # 共有リポジトリ（URL/パス）。None で無効
    "state_git_branch": "main",         # 同期先ブランチ
    "state_git_subdir": "agent-flow",    # リポジトリ内の保存先サブディレクトリ（多重コミッタとの名前空間分離）
    "state_git_interval": 300.0,        # fetch/push の最短間隔（秒）。0 で毎同期（リモート負荷は増える）
    "lease": 1800.0,
    "poll": 2.0,
    # 委譲公示板（agent-board）への参加（請負・入札）。board を与えると participate が板を巡回し、
    # workload=flow の公示に repos/tags/agent_cli で照合して入札、勝てば自分の inbox へ取り込む。
    # 板は「リポジトリ＋契約」だけで処理を持たない（schemas/board.schema.json）。既定 None で無効。
    #
    # **入札選別に使うノード宣言の正典は各 PC の `agent-project.host.yaml`**（S1 の host.yaml
    # 専有項目）。下の board_repos / board_tags / board_agent_cli は**明示上書き**で、空なら
    # host.yaml の repos / tags / agent_cli を読む（node_declaration でその場所を明示できる）。
    "board": None,                      # 板の場所（ローカル dir / git+<url>）。None で board 参加なし
    "board_workdir": None,              # git+ 板のクローン作業領域（既定は自動）
    "board_branch": "main",             # 板ブランチ
    "board_repos": {},                  # 明示上書き: 担当リポジトリ（repos.schema.json 形 / [{url,local}]）
    "board_tags": [],                   # 明示上書き: 能力タグ（公示 requires.tags との突き合わせ）
    "board_agent_cli": [],              # 明示上書き: 使える CLI（公示 requires.agent_cli との OR）
    "board_lease": 900.0,               # 板入札の lease（秒）
    "node_declaration": None,           # host.yaml のパス（未指定なら cwd → ~/.agents の順で探索）
    "model": None,
    # LLM 実行に使うエージェント CLI: kiro（kiro-cli chat）/ claude（Claude Code `claude -p`）/
    # copilot（GitHub Copilot CLI `copilot -p`）/ codex（OpenAI Codex CLI `codex exec`）。
    # planner・executor・verify 等、このツールが行う LLM 呼び出しすべてに効く。
    "agent_cli": "kiro",
    # 役割毎のエージェント上書き（yaml 専用）。キーは planner / evaluator / worker（全 kind の
    # 既定）/ 個別 kind（work/generate/classify/synthesize/verify/filter/judge/reduce/split/map）、
    # 値は {agent_cli, model}。未指定はグローバル agent_cli / model。
    "agents": {},
    "planner": "flow-planner",
    "executor": "agent",
    # executor=agent の実行系プロンプトを供給するスキル（worker/verify/evaluator）。
    # flow-planner と同じ検索順で自動発見し、見つからなければ組み込みプロンプトに
    # フォールバックする。none/builtin/空 で常に組み込みを使う（yaml 専用）。
    "worker_skill": "flow-worker",
    # 計画（3 段パイプライン）のプロンプトを供給するスキル（planner=flow-planner のとき）。
    # worker_skill と同じ検索順で自動発見し、見つからなければエージェント planner へ
    # フォールバックする。上位に同名スキルを置けば全面差し替えできる（yaml 専用）。
    "planner_skill": "flow-planner",
    "granularity": "auto",     # 分解の細かさ: auto(complexity導出・既定)/coarse/fine/finest(明示)
    "exemplar_first": False,   # map-reduce で「1件先行→検証ゲート→残り展開」の見本先行分解にする
    # doctor が LLM へ渡す稼働シグナル（recent/stuck/failed/errors 等）と worker プロンプトの
    # deps 構造化 data のうち、均質な dict 配列を表形式へ畳んでトークンを削る（案 K-2・内容は
    # 不変）。既定 off。設計: docs/plans/2026-08-05-json-prompt-compression-study.md
    "prompt_table": False,
    # 差分修復リトライ（案 B-1・オプトイン）。verify=fail / 失敗ノードの作り直し（replaces で
    # 前ノードを差し替えるノード）に、前回の出力（有界抜粋）・成果物パス・verify の指摘・
    # 「前回の変更は作業ブランチへ反映済み」を伝え、全作り直しではなく指摘箇所の修復を促す。
    # 修復は同一系統 1 回だけ（node["retries"] が 2 以上なら従来の全作り直しへ戻る——
    # 壊れた前回に引きずられて収束しないケースを有界化する。既存 max_retries の内側）。
    # 既定 off では worker プロンプトは 1 バイトも変わらない。
    # 設計: docs/plans/2026-08-05-phase1-token-efficiency-detailed-design.md §2
    "repair_retry": False,
    "repair_excerpt_bytes": 4000,   # ブリーフに載せる前回出力の抜粋上限（バイト）
    # 1 ノードが同時に実行する run（orchestrator プロセス）の上限。バックログ一括投入
    # （板からの委譲の受理等）や再起動直後の孤児一斉再開で「run 数ぶんの orchestrator
    # ＋計画エージェント」が同時に立ち上がるのを防ぐ。全ノードが park（承認待ち等）の run は
    # worker も計画エージェントも使わないため枠に数えない（gitlab 長期委譲は上限で詰まらない）。
    # 超過した要求は inbox に残り、枠が空いた poll で受理される（取りこぼさない）。
    # 0 以下で無制限（従来動作）。
    "max_runs": 8,
    "max_iterations": 3,
    "max_fanout": 50,
    # 集約（reduce）1 ノードが受け持つ依存の上限。map がこれを超えると、チャンクごとの
    # 中間 reduce を挟んで集約を木構造にする（単段集約は件数に比例して 1 ノードへ成果が
    # 集中し規模で破綻する）。review 時の検証 gate も同じ幅でチャンク分割される。
    # 幅以下なら従来と同一構造（gate 1 つ・reduce は map 全件に依存）。
    "reduce_width": 8,
    # judge/評価役のサーキットブレーカー: 同一系統（verify/失敗）の作り直しをこの回数で打ち切る。
    # 達成不可能な完了条件で無限に再タスクを生み続けるのを防ぐ（max_iterations と二重ガード）。
    "max_retries": 3,
    # --- 自己回復リトライ（設計: docs/designs/agent-flow-design.md「失敗の 4 層」）---
    # レイヤ1: transient 分類（接続断・5xx・timeout 等）の in-place 再試行。run_agent 内で
    # 指数バックオフ再試行し、上位（再計画の retries 予算）を消費しない。0 で無効。
    "transient_retries": 2,
    "transient_backoff": 5.0,    # レイヤ1 の初回バックオフ秒（指数×2＋ジッタ）
    # レイヤ2: 出力契約違反（split の JSON 配列・evaluator/planner の JSON 崩れ）の修復再呼び出し
    # 回数。「前回の出力はこう契約違反だった」と指摘して同じ役割で呼び直す。0 で無効。
    "format_retries": 1,
    # レイヤ4: transient 起因で failed 終端した run を、cooldown 後に自動再開する（done 温存）。
    # participate の巡回と cmd_run の監視ループで働く。人の cancel・superseded は触らない。
    "auto_heal": True,
    "heal_backoff": 300.0,       # heal cooldown 初期値（秒・heal_count に応じ指数）
    "max_heals": 2,              # 進捗なし heal の上限（done ノードが増えれば数え直し）
    "heal_quota": False,         # quota（利用上限）失敗も回収するか（opt-in）
    "quota_cooldown": 3600.0,    # quota 回収時の cooldown（秒）
    # 孤児 run（駆動プロセスの消失＝PC シャットダウン・クラッシュ等）の自動再開の上限。
    # 「前回の再開から進捗（新しい results/）ゼロのままの連続再開」をこの回数で打ち切り
    # failed に確定する（起動のたびに即死する壊れた run を無限に蘇生しない）。進捗があれば
    # 数え直すため、毎日シャットダウンされる PC 上の長期 run は何日でも再開を継続できる。
    # 0 以下で自動再開を無効化（従来どおり孤児は即 failed）。
    "max_resumes": 3,
    # エージェント CLI へ argv で渡すプロンプトの最大バイト数。超過分は一時ファイルへ退避し参照渡しに
    # 切り替える（依存成果物が大きいときに OS の ARG_MAX に達して起動失敗するのを防ぐ）。
    "argv_limit": 100000,
    # エージェント CLI 1 呼び出しのタイムアウト秒（既定 600、0/負で無効化）。None なら環境変数
    # AGENT_FLOW_TIMEOUT（旧名 AGENT_FLOW_KIRO_TIMEOUT も後方互換で受理）→ 600 にフォールバック。
    # ハングしたエージェント CLI を止める唯一の手段。
    "agent_timeout": None,
    # stub executor の擬似実行スリープ上限秒（既定 1〜5 秒）。None なら環境変数
    # AGENT_FLOW_STUB_SLEEP_MAX → 5 にフォールバック。テスト/動作確認では 0 で高速化できる。
    "stub_sleep_max": None,
    "review": "auto",  # auto: 集約パターンで自動有効 / True/False: 明示上書き
    "workers": 2,
    # 一時ファイルのクリーンアップ（`agent-flow cleanup` の単発実行。周期は常駐体の gc tick が持つ）
    "cleanup_age": 24.0,         # 孤立クローンを掃除するまでのアイドル時間（時間）
    # 作業後に sparse-checkout クローンを削除するか（True で削除 / False で残して再利用）
    "cleanup_clone": True,
    "cleanup_per_node": False,   # 各ノード完了後に成果物リポジトリの clone を即削除（長命 worker のディスク抑制）
    # --- 自動アップデート（既定 on）。スキルリポジトリ main の更新を participate のアイドル巡回で取り込む ---
    # 更新元は skill-registry.json から自動解決（repositories.origin.url → install_dir）。
    # 受理する要求も引き継ぐ孤児も無い巡回で git ls-remote だけを叩き、適用済みと違えば
    # 取り込み（sparse-checkout → install.sh）を切り離した子プロセスへ渡す（maybe_self_update）。
    "update_enabled": True,              # 自動アップデートの ON/OFF（false で完全無効・既定 on）
    "update_check_interval": 21600.0,    # 更新チェック間隔（秒）。既定 6 時間。0 以下で自動チェック無効
    "update_repo": DEFAULT_UPDATE_REPO,  # スキルリポジトリ（git URL/パス）。空なら skill-registry.json から自動解決
    "update_branch": "main",             # 追従するブランチ
    "update_subdir": TOOL_SUBDIR,        # リポジトリ内のこのツールのサブディレクトリ
    "update_installer": "install.sh",    # サブディレクトリ内で実行するインストーラ
    # executor プラグインの追加検索ディレクトリ（既定の検索先に加えて優先探索する）。
    "executor_dir": None,
    # gitlab executor プラグイン（opt-in のワーカーバス）の設定。executor: gitlab を選んだ
    # ときだけ使われ、この dict が JSON 化され環境変数経由でプラグインに渡される。
    # タスクを GitLab イシュー化し、リモートのワーカーが拾って実行する。status:approved
    # ラベル（レビュー承認）が付いたら、クリーンな関連 MR（コンフリクト無し・未解決レビュー
    # コメント無し）を**自動マージしてイシューをクローズ**する（auto_merge・既定 on。
    # gitlab-review-viewer の承認ボタンと同じ規則。false で従来の人マージ待ちに戻す）。
    # イシュー API は GitLab REST を stdlib で直叩き（gl.py 不要・フォールバックもしない）。
    # 起票先 URL は repo_url が権威（git origin へ流れない）。トークンはここには置かず、
    # gl.py と同じ場所（connections.yaml / 環境変数 GITLAB_TOKEN・GL_TOKEN / シェル rc）から解決する。
    # ※ 自動マージには api スコープのトークンが必要（read 系のみだとマージで 403 になり、
    #   人が GitLab 上でマージするまで待ち続ける）。
    "gitlab": {
        "conn_label": "default",            # connections.yaml の接続ラベル（トークン解決に使用）
        "repo_url": "",                     # 起票先プロジェクト URL（権威）。必ずこの URL を使う
        "labels": "status:open,assignee:any",  # 起票するイシューに付ける初期ラベル
        "priority": "priority:normal",      # 付与する優先度ラベル（空文字で付けない）
        "poll_interval": 300.0,             # イシュー1件の最短再確認間隔（秒）。レビューは遅延しうる
                                            # 前提で即応性は求めない（十分待つ）
        # 完了＝approved のクリーンな MR を自動マージ＝イシュークローズ。
        # レビュー往復は時間がかかるため待機は長めにする（0/負で無限）。
        # gitlab executor プラグインの _DEFAULTS と一致させる（以前ここだけ 86400 で食い違っていた）。
        "timeout": 604800.0,                # 全体タイムアウト（既定 7 日）。決着に至るまでの上限
        "approved_timeout": 1209600.0,      # レビュー活動検知後の猶予（既定 14 日）
        "approved_label": "status:approved",  # この状態に達したら自動マージ判定に入る（= 受け入れ承認）
        "done_label": "status:done",        # approved 以外に完了とみなすラベル
        "auto_merge": True,                 # 自動承認: approved＋クリーンな MR を自動マージ・クローズ
                                            # （false で従来の「人が関連 MR を管理」モード）
        "close_issues": "auto",             # イシューのクローズ主体。auto=決着時に executor がクローズ／
                                            # manual=クローズは人（承認条件が揃ったら案内ノートを出して
                                            # 人がクローズするのを監視。クローズで決着）
        "rework_label": "status:needs-rework",  # 差し戻し時に approved から付け替えるラベル
        # park & poll（承認待ちを worker スロットから切り離す）のパラメータ。
        # defer_waits=false で park & poll を無効化し、従来モード（worker がイシューを監視して
        # ブロック待機。1 worker=1 イシュー）に戻す。承認待ちが worker 枠を占有するが、
        # 挙動が単純で分散の監視分担も不要。既定 true（park & poll 有効）。
        "defer_waits": True,
        "max_open_issues": 0,               # 同時に開ける未決着イシューの上限（0=無制限）。
                                            # 上限到達で起票を一時停止＝バックプレッシャ（エラーにしない）。
                                            # defer_waits=false のときは無効（park しないため）。
        "watch_interval": 90.0,             # service_waits が park をまとめて再確認する間隔（秒）
        # --- 人/エージェント判別（gitlab-idd 実行前提。人コメントのみを還元へ運ぶ）---
        # gitlab-idd の worker/reviewer が動くアカウント（username/id・カンマ区切り）を
        # エージェント扱いで除外。空でも bot 名・全 gitlab-idd マーカー・per-issue 自動学習で除外する。
        "agent_authors": "",
        "human_reviewers": "",              # 人間レビュアーの allowlist（指定するとそれ以外を除外・最も厳密）
        "trust_unmarked_comments": False,   # 著者不明の曖昧コメントも拾うか（既定 False＝precision 優先）
        # 途中の差し戻し: 人コメントの見出しにこの語があれば approve/reject 決着を待たず却下級として拾う
        # （汎用コントラクト decision=rejected+guidance へ変換。空で無効）。
        "rework_heading": "差し戻し",
    },
}

# 集約点（reduce/synthesize）を持ち、独立レビューが結果の信頼性を高めるパターン。
# 公式 dynamic workflows の「集約前に互いの成果をレビューする品質パターン」に倣い、
# これらでは検証 gate を既定で自動挿入する。generate-and-filter/tournament/
# adversarial-verification は元々 filter/judge/verify を内包するため対象外。
AGGREGATING_PATTERNS = {"map-reduce", "fan-out-and-synthesize"}


def this_pc(args) -> str:
    """このプロセスが載っている **PC** の名義（板・バス共通の node_id）。

    `args.node_id` を見ないのが要点。`work` サブコマンドでは `--node-id` が
    **ワーカーの名義**（`<node_id>-w1` 等）で埋まっており、PC の身元とは別物だからだ。
    設定ファイルの `node_id` 宣言（あれば）→ `agentcore.nodeid.default_node_id()` の順で解決する
    ——`_default_daemon_id` が CLI 上書きを尊重したうえで最後に落ちる先と同じ値になる。"""
    cfg = getattr(args, "_config", None)
    declared = (cfg or {}).get("node_id") if isinstance(cfg, dict) else None
    return declared or default_node_id()


def _review_decision(review_setting, patterns) -> bool:
    """review の三値解決。True/False は明示指定として尊重。'auto'（既定）や None は
    集約パターンを含むときのみ自動で有効化する。"""
    if isinstance(review_setting, bool):
        return review_setting
    return bool(set(patterns or []) & AGGREGATING_PATTERNS)


def _find_config(explicit):
    """設定ファイルの探索（フォールバック順）:
       1. --config で明示指定
       2. カレントディレクトリ（=プロジェクトルート）直下の agent-flow.{yaml,yml,json}
       3. カレントディレクトリの .agents/agent-flow.{yaml,yml,json}
       4. カレントディレクトリの .agent/agent-flow.{yaml,yml,json}（旧ホーム）
       5. ~/.agents/agent-flow.{yaml,yml,json}
    ルート直下を最優先にするのは 1 root = 1 プロジェクト構成でこのファイルが
    プロジェクトのマニフェスト（発見マーカー）を兼ねるため（agent-project と同じ規則）。"""
    if explicit:
        p = os.path.expanduser(explicit)
        if not os.path.isfile(p):
            print(f"[agent-flow] 設定ファイルが見つかりません: {explicit}", file=sys.stderr)
            sys.exit(1)
        return p
    cwd = os.getcwd()
    bases = [cwd, os.path.join(cwd, AGENT_HOME)]
    if os.path.realpath(cwd) != os.path.realpath(os.path.expanduser("~")):
        bases.append(os.path.join(cwd, AGENT_HOME_LEGACY))
    bases.append(agent_home_dir())
    for base in bases:
        for name in DEFAULT_CONFIG_NAMES:
            cand = os.path.join(base, name)
            if os.path.isfile(cand):
                return cand
    return None


def resolve_config(args):
    """優先順位 CLI > 設定ファイル > 組み込み既定 で各値を確定する。
    CLI 未指定（None）の設定値だけを設定ファイル→既定で埋める。"""
    path = _find_config(getattr(args, "config", None))
    cfg = _load_config_file(path) if path else {}
    args._config_path = path
    # 生の設定値も残す。サブコマンドの引数が同名のグローバル設定を覆い隠す場合に、
    # 元の宣言を読み直す唯一の口になる（例: `work --node-id <worker 名義>` は args.node_id を
    # ワーカー名で埋めるので、その PC の node_id 宣言はここからしか取れない — this_pc）。
    args._config = cfg
    # 後方互換: 旧キー kiro_timeout は agent_timeout の別名として受理する（新キー未指定時のみ）。
    if "agent_timeout" not in cfg and "kiro_timeout" in cfg:
        cfg["agent_timeout"] = cfg["kiro_timeout"]
    for key, dflt in CONFIG_DEFAULTS.items():
        if getattr(args, key, None) is None:
            setattr(args, key, cfg.get(key, dflt))
    return args
