from __future__ import annotations
# cli.py — 元 kiro-flow.py の 6577-6832 行目（機械分割・内容無改変）。
# 単体 import しない。kiro_flow/__init__.py が共有名前空間へ順に exec 合成する。
def build_parser() -> argparse.ArgumentParser:
    """CLI パーサを構築して返す。main と、子プロセス起動 argv の妥当性を検証する
    テスト（_spawn_orchestrator/_spawn_worker が組み立てた argv を parse できるか）で共有する。
    グローバル引数とサブコマンド引数の置き場を取り違えると usage エラーで子が即死するため、
    その回帰を単体テストで捕まえられるように公開関数として切り出している。"""
    p = argparse.ArgumentParser(description="kiro-flow — git 共有型・分散 Dynamic Workflow")
    # 設定値の優先順位: CLI > 設定ファイル(kiro-flow.yaml) > 組み込み既定。
    # 設定ファイル対象のオプションは既定 None にし、parse 後 resolve_config で確定する。
    p.add_argument("--config", default=None,
                   help="設定ファイルのパス（未指定なら ./ → ./.kiro → ~/.kiro の kiro-flow.{yaml,yml,json}）")
    p.add_argument("--bus", default=None,
                   help="ローカルバスのルート / git モードでは各ノードのクローン親ディレクトリ")
    p.add_argument("--run-id", default=None, help="run 識別子")
    p.add_argument("--git", default=None,
                   help="共有 git リポジトリ URL/パス。指定で複数 PC 分散モードになる")
    p.add_argument("--git-branch", default=None, help="バスに使う git ブランチ（既定 main）")
    p.add_argument("--git-subdir", default=None,
                   help="リポジトリ内のバスにするサブディレクトリ（既定: リポジトリ直下）")
    p.add_argument("--lock-dir", dest="lock_dir", default=None,
                   help="daemon singleton ロックの置き場（設定ファイル lock_dir と同義。"
                        "外部起動の daemon を別ツールから発見させるため起動側と一致させる）")
    p.add_argument("--state-git", dest="state_git", default=None,
                   help="ワーク内容（ローカルバスの runs/・inbox/）を保存・共有する git リポジトリ"
                        "（URL/パス）。リモートの kiro-projects-viewer が進捗/結果を読める"
                        "（未指定で無効。--git のバス分散とは独立で、--git 指定時は無視）")
    p.add_argument("--state-git-branch", dest="state_git_branch", default=None,
                   help="state_git の同期先ブランチ（既定 main）")
    p.add_argument("--state-git-subdir", dest="state_git_subdir", default=None,
                   help="state_git リポジトリ内の保存先サブディレクトリ（既定 kiro-flow）。"
                        "同一リポジトリへ他プログラムもコミットする前提の名前空間分離")
    p.add_argument("--state-git-interval", dest="state_git_interval", type=float, default=None,
                   help="state_git の fetch/push の最短間隔（秒。既定 300）。リモートサーバへの"
                        "負荷を一定に保つ律速。0 で毎同期")
    p.add_argument("--executor-dir", dest="executor_dir", default=None,
                   help="executor プラグイン（<name>.py）の追加検索ディレクトリ（設定 executor_dir と同義）")
    p.add_argument("--workspace", dest="workspace", default=None,
                   help="この run（=バックログ単位）の唯一の書込先リポジトリ。素の URL でも、構造化 JSON "
                        "（{url,path,base,target,desc}）でも可。worker が temp 領域へ clone し、作業ブランチ "
                        "kf/<run-id> を base から作って作業、変更があれば kiro-flow が commit/push する。"
                        "path はモノレポの作業フォルダ、target は MR/PR のターゲットブランチ。"
                        "省略時は読み取り専用 run")
    p.add_argument("--reference", dest="references", action="append", default=None,
                   help="参照リポジトリ（読むだけ・書き込まない／複数可）。素の URL でも JSON "
                        "（{url,path,base,desc}）でも可。エージェントのプロンプトと gitlab イシュー本文に"
                        "参照節として載る（clone はしない）")
    p.add_argument("--agent-cli", dest="agent_cli", default=None, choices=["kiro", "claude", "copilot", "codex"],
                   help="LLM 実行に使うエージェント CLI（設定 agent_cli と同義）。kiro=kiro-cli chat（既定）/ "
                        "claude=Claude Code ヘッドレス（claude -p）/ copilot=GitHub Copilot CLI（copilot -p）/ "
                        "codex=OpenAI Codex CLI（codex exec）")
    p.add_argument("--granularity", default=None, choices=["coarse", "fine", "finest"],
                   help="タスク分解の細かさ（設定 granularity と同義）。coarse=現状 / fine=1段細かい / "
                        "finest=2段細かい（既定）。細かいほど小さなタスクに多く分解する")
    p.add_argument("--exemplar-first", dest="exemplar_first", action="store_const", const=True,
                   default=None,
                   help="map-reduce の fan-out を見本先行にする（設定 exemplar_first と同義）。"
                        "先頭1件を検証ゲートに通してから残りを展開し、同様手順を1件で固めてから流す")
    p.add_argument("--lease", type=float, default=None,
                   help="claim のリース秒数（超過すると他ノードが再 claim 可能。既定 1800）")
    p.add_argument("--argv-limit", dest="argv_limit", type=int, default=None,
                   help="kiro-cli へ argv で渡すプロンプトの最大バイト数（設定 argv_limit と同義）。"
                        "超過分は一時ファイルへ退避し参照渡しにする（既定 100000）")
    p.add_argument("--keep-clone", dest="cleanup_clone", action="store_const", const=False,
                   default=None,
                   help="作業後に sparse-checkout クローンを削除せず残す（既定: 削除して再利用しない）")
    p.add_argument("--cleanup-per-node", dest="cleanup_per_node", action="store_const", const=True,
                   default=None,
                   help="各ノード完了後に成果物リポジトリの clone を即削除する（設定 cleanup_per_node と同義）。"
                        "長命 worker（--keep-alive）のディスク積み上がりを抑える（既定: worker 終了時に一括削除）")
    # サブコマンド未指定なら daemon として扱う（required=False）
    sub = p.add_subparsers(dest="cmd")

    run = sub.add_parser("run", help="単発実行。既存 --run-id なら再開、無ければ新規（状態で自動判断）")
    run.add_argument("request", nargs="?", default=None,
                     help="ワークフローへの要求（再開時は省略可）")
    run.add_argument("--workers", type=int, default=None)
    run.add_argument("--planner", choices=["agent", "stub", "flow-planner"], default=None)
    run.add_argument("--executor", default=None,
                     help="ワーカーバス: 組み込み agent / stub、または executor プラグイン名"
                          "（例 gitlab）/ .py パス（opt-in。gitlab はタスクを GitLab イシューに"
                          "して委譲し approved まで待つ）")
    run.add_argument("--max-iterations", type=int, default=None,
                     help="再計画（evaluator-optimizer）の最大反復回数")
    run.add_argument("--max-fanout", type=int, default=None,
                     help="データ駆動 fan-out の最大展開数（既定 50）")
    run.add_argument("--max-retries", type=int, default=None,
                     help="同一系統の作り直し打ち切り回数（サーキットブレーカー, 既定 3）")
    run.add_argument("--review", dest="review", action="store_const", const=True, default=None,
                     help="統合（synthesize/reduce）の前に検証 gate を必ず挟む（既定: 集約パターンで自動）")
    run.add_argument("--no-review", dest="review", action="store_const", const=False,
                     help="自動の検証 gate を無効化する")
    run.add_argument("--model", default=None)
    run.add_argument("--poll", type=float, default=None)
    run.add_argument("--inherit-from", dest="inherit_from", default=None,
                     help="リトライ: 指定した先行 run-id から確定済み（done）ノードの結果・計画・"
                          "中間成果物を引き継ぎ、先行 run を掃除する（新規時のみ有効）。先行 run が"
                          "完全 done なら状態は引き継がず掃除だけ行う（feedback 付きで新規にやり直す）")
    run.set_defaults(func=cmd_run)

    orch = sub.add_parser("orchestrate", help="計画役")
    orch.add_argument("--request", required=True)
    orch.add_argument("--planner", choices=["agent", "stub", "flow-planner"], default=None)
    orch.add_argument("--executor", default=None,
                      help="ワーカーバス（agent/stub/プラグイン名/.py パス）。"
                           "評価役（evaluator）は stub 以外ならローカルのエージェント CLI で判断")
    orch.add_argument("--max-iterations", type=int, default=None)
    orch.add_argument("--max-fanout", type=int, default=None)
    orch.add_argument("--max-retries", type=int, default=None)
    orch.add_argument("--review", dest="review", action="store_const", const=True, default=None)
    orch.add_argument("--no-review", dest="review", action="store_const", const=False)
    orch.add_argument("--node-id", default="orchestrator")
    orch.add_argument("--model_opt", dest="model", default=None)
    orch.add_argument("--poll", type=float, default=None)
    orch.add_argument("--inherit-from", dest="inherit_from", default=None,
                      help="リトライ: 先行 run-id から確定済みノードを引き継ぎ先行 run を掃除する")
    orch.set_defaults(func=cmd_orchestrate)

    work = sub.add_parser("work", help="ワーカー役")
    work.add_argument("--node-id", default=f"{socket.gethostname()}-{os.getpid()}")
    work.add_argument("--executor", default=None,
                      help="ワーカーバス（agent/stub/プラグイン名/.py パス）")
    work.add_argument("--model_opt", dest="model", default=None)
    work.add_argument("--poll", type=float, default=None)
    work.add_argument("--keep-alive", action="store_true", help="run 完了後も待機し続ける")
    work.add_argument("--idle-exit", action="store_true",
                      help="claim 可能タスクが無くなったら終了（デーモンのオンデマンド起動用）")
    work.set_defaults(func=cmd_work)

    dm = sub.add_parser("daemon", help="常駐し、要求に応じ orchestrator/worker をオンデマンド起動")
    dm.add_argument("--node-id", default=None, help="デーモン識別子（既定: host-pid）")
    dm.add_argument("--max-workers", type=int, default=None,
                    help="このデーモンが同時に走らせる worker 上限（既定 4）")
    dm.add_argument("--max-runs", dest="max_runs", type=int, default=None,
                    help="同時に実行する run（orchestrator）の上限（既定 8）。全 park（承認待ち）の "
                         "run は数えない。超過要求は inbox に残り枠が空き次第受理。0 以下で無制限")
    dm.add_argument("--planner", choices=["agent", "stub", "flow-planner"], default=None)
    dm.add_argument("--executor", default=None,
                    help="ワーカーバス（agent/stub/プラグイン名/.py パス）")
    dm.add_argument("--max-iterations", type=int, default=None)
    dm.add_argument("--max-fanout", type=int, default=None)
    dm.add_argument("--max-retries", type=int, default=None)
    dm.add_argument("--max-resumes", dest="max_resumes", type=int, default=None,
                    help="孤児 run（owning daemon 消失）の自動再開の上限（進捗なしの連続回数, "
                         "既定 3）。進捗があれば数え直す。0 以下で無効（孤児は即 failed）")
    dm.add_argument("--review", dest="review", action="store_const", const=True, default=None)
    dm.add_argument("--no-review", dest="review", action="store_const", const=False)
    dm.add_argument("--model", default=None)
    dm.add_argument("--poll", type=float, default=None)
    dm.add_argument("--cleanup-interval", dest="cleanup_interval", type=float, default=None,
                    help="一時ファイル自動掃除の実行間隔（秒, 既定 3600）。0 以下で無効化")
    dm.add_argument("--cleanup-age", dest="cleanup_age", type=float, default=None,
                    help="孤立クローンを掃除するまでのアイドル時間（時間, 既定 24）")
    dm.add_argument("--no-cleanup", dest="cleanup_interval", action="store_const", const=0.0,
                    help="一時ファイルの自動掃除を無効化する")
    dm.add_argument("--status-interval", dest="status_interval", type=float, default=None,
                    help="state_git（鏡）越しにリモートの kiro-projects-viewer が daemon の生存を"
                         "判定するための status.json を、アイドル中もこの間隔（秒）で更新する"
                         "（既定 0＝無効。無効時はアイドル中 status.json に一切触れず、state_git の"
                         "commit-if-diff で追加コミットを作らない）。real な run イベント時は"
                         "この設定に関わらず既存の sync に相乗りして常に最新化される")
    dm.set_defaults(func=cmd_daemon)

    sb = sub.add_parser("submit", help="要求を inbox に投入（デーモンが拾う）")
    sb.add_argument("request", help="ワークフローへの要求")
    sb.add_argument("--inherit-from", dest="inherit_from", default=None,
                    help="リトライ: 先行 run-id から確定済みノードを引き継ぎ先行 run を掃除する"
                         "（daemon の orchestrate に伝搬される）")
    sb.set_defaults(func=cmd_submit)

    cn = sub.add_parser("cancel",
                        help="run を canceled に終端化（人の明示指示による run スコープの恒久停止）。"
                             "承認待ちで park 中の run も暴走中の run も止められる緊急回避手段")
    cn.add_argument("run_id", help="キャンセルする run-id（submit の戻り値／status --list で確認）")
    cn.add_argument("--reason", default="", help="キャンセル理由（meta / イベントに記録）")
    cn.add_argument("--close-issues", dest="close_issues", action="store_true",
                    help="park 済みの GitLab イシューに取消コメントを付けてクローズする"
                         "（既定: イシューは残し、追跡だけやめる）")
    cn.set_defaults(func=cmd_cancel)

    st = sub.add_parser("status", help="run の状態表示（既定 1 回 / --follow でライブ監視）")
    st.add_argument("--follow", "-f", action="store_true", help="ライブ監視（tmux ペイン向け）")
    st.add_argument("--interval", type=float, default=1.0, help="更新間隔（秒, --follow 時）")
    st.add_argument("--events", type=int, default=8, help="表示する直近イベント数")
    st.add_argument("--until-done", action="store_true", help="run 完了で自動終了（--follow 時）")
    st.add_argument("--list", "-l", action="store_true", help="run 一覧を表示して終了")
    st.set_defaults(func=cmd_status)

    rs = sub.add_parser("result",
                        help="完了した run の最終結果を探して提示（status 相当・進捗でなく成果を返す）")
    rs.add_argument("--json", action="store_true", help="機械可読な JSON で出力")
    rs.set_defaults(func=cmd_result)

    gc = sub.add_parser("gc", help="古い run を掃除（対応する inbox 要求・claim も削除）。"
                                   "run を伴わない孤児 inbox 要求（不要 run の再起動元）も掃除する")
    gc.add_argument("--older-than", type=float, default=7.0,
                    help="この日数より古い run が対象（孤児 inbox 要求もこの閾値で掃除）")
    gc.add_argument("--keep", type=int, default=3, help="新しい順にこの件数は無条件で保護")
    gc.add_argument("--status", default=None, help="この status の run のみ対象（例: done）")
    gc.add_argument("--dry-run", action="store_true", help="削除せず対象だけ表示")
    gc.set_defaults(func=cmd_gc)

    dr = sub.add_parser("doctor", help="ログ/状態/環境から稼働を診断（kiro-cli）。env/config は "
                                       "--fix で修正・program は gitlab-idd でイシュー起票")
    dr.add_argument("--json", action="store_true", help="JSON で出力（連携呼び出し用の findings を含む）")
    dr.add_argument("--fix", action="store_true",
                    help="env/config の問題を修正し、program の不具合を gitlab-idd で起票"
                         "（スキルが無ければ出力のみ。既定は診断のみ）")
    dr.set_defaults(func=cmd_doctor)

    up = sub.add_parser("update",
                        help="スキルリポジトリ(main)の更新を確認。--now で temp に sparse-checkout "
                             "して install.sh を実行し再起動する")
    up.add_argument("--now", action="store_true",
                    help="更新があれば即座に install.sh を実行して再起動する")
    up.add_argument("--check", action="store_true", help="更新の有無だけを表示（取り込まない）")
    up.set_defaults(func=cmd_update)
    return p


def main() -> int:
    p = build_parser()
    args = p.parse_args()
    # CLI 未指定の設定値を設定ファイル→組み込み既定で確定（CLI > config > 既定）
    resolve_config(args)
    # args を持たない free 関数（run_kiro 等）が読む閾値をモジュール変数へ確定させる
    _configure_thresholds(args)
    # ワークスペース clone の削除を二重化（main の finally に加え、想定外の早期 exit でも回収）
    atexit.register(cleanup_workspace)
    # 子プロセスから渡る空文字の --model_opt は「モデル指定なし」を意味する
    if getattr(args, "model", None) == "":
        args.model = None
    # executor の早期検証: 不正名のまま worker を起動すると run がハングするため、
    # 親プロセスでプラグイン解決を試し、解決できなければここで明確に失敗する。
    spec = getattr(args, "executor", None)
    if spec and spec not in BUILTIN_EXECUTORS and _resolve_executor_plugin(spec) is None:
        dirs = "、".join(_executor_search_dirs())
        print(f"[kiro-flow] executor '{spec}' を解決できません。組み込み（kiro/stub）か、"
              f"プラグイン .py（検索: {dirs}）か、明示パスを指定してください。", file=sys.stderr)
        return 2
    # 起動初回にバスフォルダが無ければ作成する（git バスでは .gitkeep も置く）。
    # 診断/読み取り専用コマンドは副作用を持たせない（doctor の「未作成」所見を潰さない）。
    if getattr(args, "func", None) in (
            cmd_run, cmd_daemon, cmd_orchestrate, cmd_work, cmd_submit, cmd_cancel, None):
        ensure_bus_root(args)
    # サブコマンド未指定 → daemon として処理
    try:
        if getattr(args, "func", None) is None:
            args.node_id = getattr(args, "node_id", None)
            return cmd_daemon(args)
        return args.func(args)
    finally:
        # 作業後に sparse-checkout クローンを削除する（--keep-clone で抑止可）
        if getattr(args, "cleanup_clone", True):
            cleanup_active_clones()
        cleanup_workspace()   # ワークスペースの clone は常に消す（作業後クリーンは必須）


