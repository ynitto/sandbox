from __future__ import annotations
# cli.py — 元 agent-project.py の 11228-11492 行目（機械分割・内容無改変）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
def main(argv=None) -> int:
    global _START_CWD
    _START_CWD = os.getcwd()   # 自己更新の graceful 再起動で「動いていた cwd」へ戻すために捕捉
    if argv is None:
        argv = sys.argv[1:]
    p = argparse.ArgumentParser(
        prog="agent-project",
        description="backlog/ を優先順位付け・検証・収束させる制御層（Loop Engineering MVP）。"
                    "サブコマンドを省略すると常駐体（serve）で起動し、host.yaml に宣言した"
                    "プロジェクトをまとめて面倒見る")
    # metavar を置くと usage 行が `<command>` に畳まれ、各コマンドは本文に説明付きで並ぶ。
    # help=SUPPRESS の内部配線（flow-participate / flow-run）は本文からは消えるが、
    # metavar が無いと usage 行の選択肢一覧には名前だけ残る（R10: 利用者向けの表示に
    # 内部の語彙を出さない）。
    sub = p.add_subparsers(dest="cmd", required=False, metavar="<command>")

    run = sub.add_parser("run", help="正準ループ（優先順位付け→実行→検証→積み直し→収束）。"
                                     "<project>/charter.md があれば自動で目標駆動（plan→execute→evaluate）")
    _add_common(run)
    run.add_argument("--watch", action=argparse.BooleanOptionalAction, default=None,
                     help="終了条件後もプロセスを残し backlog を監視（エージェントは待機しない）")
    run.add_argument("--force", action="store_true",
                     help="同じプロジェクトを既に監視中でも起動する（watch の重複を許す）")
    run.add_argument("--poll", type=float, default=None, help="watch のポーリング間隔（秒。既定 5）")
    run.add_argument("--level", default=None, choices=["report", "assisted", "unattended"],
                     help="自律度の段階導入（既定 unattended）。report=実行せず計画報告のみ／"
                          "assisted=実行するが done は人が承認（全件 review）／unattended=現行（自動 done）。"
                          "タスク毎に `- level:` で上書き可、`- track:` 群は --auto-level で実績連動昇格")
    run.add_argument("--auto-level", action=argparse.BooleanOptionalAction, default=None,
                     help="実績連動の自動昇格（opt-in）。`- track:` 群の手戻り率が低ければ level を自動で上げ、"
                          "手戻りで下げる。ceiling は --auto-level-max（既定 assisted）")
    run.add_argument("--auto-level-max", default=None, choices=["report", "assisted", "unattended"],
                     help="自動昇格の上限（既定 assisted）。unattended にすると完全無人化への自動到達を解禁")
    run.add_argument("--throttle", type=float, default=None,
                     help="ソフト予算比率(0=off)。max_tokens/max_cost のこの割合(例 0.8)で run を打ち切り、"
                          "watch は以降 report へ降格（act 停止）。ハード上限の手前で緩やかに止める")
    run.add_argument("--concurrency", type=int, default=None,
                     help="1サイクルで daemon/remote へ並行 submit する独立タスク数（既定 1=逐次。"
                          "agent-flow の worker 並列に委ねる。local 実行は逐次のまま）")
    run.add_argument("--no-archive", dest="do_archive", action="store_const", const=False,
                     default=None, help="done を archive/ へ退避せず削除（既定は退避。config: do_archive）")
    run.add_argument("--rot", action=argparse.BooleanOptionalAction, default=None,
                     help="triage で rot（古い/重複/実行不能）を検知し人の判断へ回す")
    run.add_argument("--require-progress", action=argparse.BooleanOptionalAction, default=None,
                     help="verify=PASS でも act が baseline 以降に変更を生んでなければ done せず人へ"
                          "（履歴一致 verify の偽 done 対策。タスク毎に - expect: changes / none で上書き）")
    run.add_argument("--cleanup", action=argparse.BooleanOptionalAction, default=None,
                     help="run 後に agent-flow バスの一時状態を掃除（--no-cleanup で残す。既定 on）")
    run.add_argument("--bus-keep-runs", type=int, default=None,
                     help="掃除しても残す直近 run 数（viewer のフローが読む。既定 20・0 で全消し）")
    run.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=None,
                     help="act を飛ばし verify のみ")
    run.add_argument("--once", action=argparse.BooleanOptionalAction, default=None,
                     help="1 タスクだけ処理して終了")
    # charter 駆動（目標から回す）。<project>/charter.md があれば run が自動で plan→execute→evaluate に入る
    run.add_argument("--charter", default=None,
                     help="プロジェクト憲章ファイル（既定 <project>/charter.md。あれば run が目標駆動になる）")
    run.add_argument("--review-project", action=argparse.BooleanOptionalAction, default=None,
                     help="charter 駆動時、evaluate で敵対的レビューを上乗せ（全 PASS でも短絡的達成を疑う・opt-in）")
    run.add_argument("--max-project-cycles", type=int, default=None,
                     help="charter 駆動時の改善サイクル上限（有限停止・既定 5）")
    run.add_argument("--max-project-cost", type=float, default=None,
                     help="charter 駆動時のプロジェクト累計コスト上限(USD・0=無制限)")
    run.add_argument("--project-stall", type=int, default=None,
                     help="charter 駆動時、acceptance PASS 数が増えない連続回数の上限→人へ（既定 2）")

    for name, helptext in [("triage", "優先順位付けのみ（inbox→ready 昇格・policy 適用）"),
                           ("needs", "人の判断待ち（blocked / need_intake）を表示"),
                           ("promote", "効いた学習を ltm-use 長期記憶へ昇格（エージェント不要）")]:
        _add_common(sub.add_parser(name, help=helptext))
    rot = sub.add_parser("rot", help="rot（古い/重複/実行不能）を検出して報告（--fix で blocked 化）")
    _add_common(rot); rot.add_argument("--fix", action="store_true", help="検出した rot を人の判断へ回す")

    st = sub.add_parser("stats", help="ループの計測値（スループット・自動化率・retry・人対応待ち）")
    _add_common(st); st.add_argument("--json", action="store_true", help="JSON で出力")

    au = sub.add_parser("audit", help="Loop Readiness を採点（L0–L3・スコア・赤旗・提案）")
    _add_common(au); au.add_argument("--json", action="store_true", help="JSON で出力")
    au.add_argument("--strict", action="store_true",
                    help="スコア<40 か critical 赤旗があれば exit 2（CI ゲート用）")

    gc = sub.add_parser("gc", help="agent-flow バスの一時ファイル・古い run アーカイブを掃除する"
                                   "（実装計画 W1-11 残・ノード常駐体 gc tick 用の単発版）")
    _add_common(gc); gc.add_argument("--json", action="store_true", help="JSON で出力")

    # 常駐体（serve）の内部配線。人が直接叩くコマンドではないので help から隠す
    # （利用者向けの語彙を増やさない）。flow tick の 1 巡と、その受理分の実行。
    # help を渡さない（help=SUPPRESS ではない）。argparse は 'help' が kwargs にあるだけで
    # 選択肢の疑似アクションを作るため、SUPPRESS でも一覧に名前が残る（3.9 では
    # `==SUPPRESS==` がそのまま印字される）。渡さなければ一覧に載らない。
    fp = sub.add_parser("flow-participate")
    _add_common(fp); fp.add_argument("--json", action="store_true", help="JSON で出力")
    fp.add_argument("--running", default="",
                    help="常駐体が今走らせている / これから走らせる run-id（カンマ区切り）")
    fp.add_argument("--node-declaration", dest="node_declaration", default="",
                    help="このノードの宣言（agent-project.host.yaml）のパス。板の入札選別に使う "
                         "repos / tags / agent_cli の供給元として agent-flow へ渡す")

    fr = sub.add_parser("flow-run")
    _add_common(fr); fr.add_argument("--run-id", dest="run_id", required=True,
                                     help="実行する run-id（flow-participate が出したもの）")

    rl = sub.add_parser("runlog", help="構造化 run-log（run-log.jsonl）の末尾を表示")
    _add_common(rl); rl.add_argument("--json", action="store_true", help="JSON で出力")
    rl.add_argument("--tail", type=int, default=10, help="表示する直近の件数（既定 10・0 で全件）")

    dr = sub.add_parser("doctor", help="ログ/状態/環境から稼働を診断（エージェント CLI）。env/config は "
                                       "--fix で修正・program は gitlab-idd でイシュー起票")
    _add_common(dr); dr.add_argument("--json", action="store_true", help="JSON で出力")
    dr.add_argument("--fix", action="store_true",
                    help="env/config の問題を修正し、program の不具合を gitlab-idd で起票"
                         "（スキルが無ければ出力のみ。既定は診断のみ）")
    dr.add_argument("--with-flow", dest="with_flow", action="store_true", default=None,
                    help="実行層 agent-flow の doctor も連携実行して所見を統合（既定 on）")
    dr.add_argument("--no-flow", dest="with_flow", action="store_false",
                    help="agent-flow との連携を無効化し本体のみ診断する")
    dr.add_argument("--node-id-cutover", dest="node_id_cutover", default=None, metavar="OLD",
                    help="node_id 切替の事前チェック（旧 node_id を渡す）。旧名義に未決着の委譲・"
                         "amigos ロール状態が残っていないか、新名義が板で使用中でないかを見る。"
                         "手順は docs/guides/node-id-cutover.md")

    up = sub.add_parser("update",
                        help="スキルリポジトリ(main)の更新を確認。--now で temp に sparse-checkout "
                             "して install.sh を実行し再起動する")
    _add_common(up)
    up.add_argument("--now", action="store_true",
                    help="更新があれば即座に install.sh を実行して再起動する")
    up.add_argument("--check", action="store_true", help="更新の有無だけを表示（取り込まない）")

    enq = sub.add_parser("enqueue", help="汎用の取り込み口（CLI/stdin/JSON から backlog タスクを作る）")
    _add_common(enq)
    enq.add_argument("--title", default=None, help="タスクのタイトル（必須・--json 時は不要）")
    enq.add_argument("--verify", default=None, help="done 確定の verify コマンド（書ければこれが最良）")
    enq.add_argument("--acceptance", action="append", default=None,
                     help="受入基準（自然文・複数指定可）。settle 時に検証エージェントが基準ごとに"
                          "証跡付きで判定する。verify が書けないときの一次表現")
    enq.add_argument("--accept", default=None,
                     help="完了条件を自然言語で 1 行（--acceptance 1 件と同じ扱い・後方互換）")
    enq.add_argument("--verify-template", default=None,
                     help="決定的テンプレで verify を生成（例 'file-contains :: path :: 文字列'。エージェント不要）")
    enq.add_argument("--priority", type=int, default=0, help="優先度（大きいほど高優先・既定 0）")
    enq.add_argument("--source", default=None, help="出所（既定 enqueue）")
    enq.add_argument("--status", default=None, help="status を明示（既定: verify 有→ready / 無→inbox）")
    enq.add_argument("--after", default=None, help="依存タスク ID（カンマ区切り。DAG）")
    enq.add_argument("--repos", default=None,
                     help="このタスクが clone して作業する成果物リポジトリ（charter の name か URL・"
                          "カンマ区切りで複数可）。worker が temp 領域へ clone してから作業し作業後に消す")
    enq.add_argument("--cohort-items", dest="cohort_items", default=None,
                     help="同様手順の繰り返しタスクの対象一覧（カンマ区切り）。先頭を pilot として"
                          "先行実行し review:human で指示を固め、承認後に残りを生成する。"
                          "title/verify 中の {item} に各対象を差し込む")
    enq.add_argument("--review", default=None, help="検収ゲート（human で done 前に承認）")
    enq.add_argument("--note", default=None, help="メモ（保持される）")
    enq.add_argument("--why", default=None, help="背景・目的（なぜやるか。レビューと実装判断の基準になる）")
    enq.add_argument("--desc", default=None, help="作業内容の詳細（タイトルで足りない具体の指示。改行は ⏎）")
    enq.add_argument("--scope", default=None, help="変更してよい範囲（ファイル/領域。この外は変更させない）")
    enq.add_argument("--out-of-scope", dest="out_of_scope", default=None,
                     help="やらないこと（非目標。スコープ膨張を防ぐ）")
    enq.add_argument("--constraints", default=None, help="タスク固有の制約（守るべき規約・禁止事項）")
    enq.add_argument("--hints", default=None, help="実装の手がかり（関連ファイル・参考実装・調査済み情報）")
    enq.add_argument("--risks", action="append", default=None,
                     help="実装・運用上のリスクと対策（複数指定可。該当なしは「なし」）")
    enq.add_argument("--demo", default=None, help="人の確認観点（検収で何をどう確かめるか）")
    enq.add_argument("--id", default=None, help="タスク ID を明示（既定はタイトルから自動生成）")
    enq.add_argument("--json", action="store_true", help="stdin か --file の JSON（オブジェクト/配列）で投入")
    enq.add_argument("--file", default=None, help="--json の入力ファイル（既定 stdin）")

    ap = sub.add_parser("approve", help="判断待ちを修正承認して積み直し（決定記録）。"
                                        "--complete で成果を受け入れて完了（done 確定）にする")
    _add_common(ap); ap.add_argument("id"); ap.add_argument("--reason", required=True)
    ap.add_argument("--complete", action="store_true",
                    help="成果を受け入れて完了にする（検収待ちの blocked / review が対象）。"
                         "省略時は従来どおりブロックを解いて積み直す")
    rm = sub.add_parser("retry-mr", help="検収到達時に作成できなかったタスク MR を冪等に再作成")
    _add_common(rm); rm.add_argument("id")
    hd = sub.add_parser("hold", help="policy に deny 追加し保留（決定記録）")
    _add_common(hd); hd.add_argument("id"); hd.add_argument("--reason", required=True)
    rp = sub.add_parser("reprioritize", help="policy に pin/defer 追加（決定記録）")
    _add_common(rp); rp.add_argument("id")
    g = rp.add_mutually_exclusive_group(required=True)
    g.add_argument("--pin", action="store_true"); g.add_argument("--defer", action="store_true")
    rp.add_argument("--reason", required=True)

    rv = sub.add_parser("revise",
                        help="タスクを人が即時修正（内容・依存 after・優先度＋feedback 注入。"
                             "実行中なら現在の試行を確定せず修正内容で積み直す。決定記録）")
    _add_common(rv); rv.add_argument("id")
    # dest は rv_ プレフィックスで分離する（level 等は CONFIG_DEFAULTS のキーでもあり、
    # 素の dest だと resolve_config が設定既定値を注入して「指定していない編集」になるため）
    rv.add_argument("--title", dest="rv_title", default=None, help="タイトルを置換")
    rv.add_argument("--priority", dest="rv_priority", type=int, default=None,
                    help="優先度を置換（整数・大ほど高）")
    rv.add_argument("--verify", dest="rv_verify", default=None,
                    help="verify コマンドを置換（'' / none で削除）")
    rv.add_argument("--accept", dest="rv_accept", default=None,
                    help="自然言語の完了条件を置換（'' / none で削除）")
    rv.add_argument("--acceptance", dest="rv_acceptance", action="append", default=None,
                    help="受入基準を置換（複数指定可＝指定した分で全行を差し替え。"
                         "1 つだけ '' / none を渡すと全削除）")
    rv.add_argument("--after", dest="rv_after", default=None,
                    help="依存タスク ID を置換（カンマ区切り。'' / none で解除。循環は拒否）")
    rv.add_argument("--note", dest="rv_note", default=None, help="メモを置換（'' / none で削除）")
    rv.add_argument("--why", dest="rv_why", default=None, help="背景・目的を置換（'' / none で削除）")
    rv.add_argument("--desc", dest="rv_desc", default=None,
                    help="作業内容の詳細を置換（改行は ⏎。'' / none で削除）")
    rv.add_argument("--scope", dest="rv_scope", default=None,
                    help="変更してよい範囲を置換（'' / none で削除）")
    rv.add_argument("--out-of-scope", dest="rv_out_of_scope", default=None,
                    help="やらないことを置換（'' / none で削除）")
    rv.add_argument("--constraints", dest="rv_constraints", default=None,
                    help="タスク固有の制約を置換（'' / none で削除）")
    rv.add_argument("--hints", dest="rv_hints", default=None,
                    help="実装の手がかりを置換（'' / none で削除）")
    rv.add_argument("--risks", dest="rv_risks", action="append", default=None,
                    help="リスクと対策を置換（複数指定可。1つだけ '' / none で全削除）")
    rv.add_argument("--demo", dest="rv_demo", default=None,
                    help="人の確認観点を置換（'' / none で削除）")
    rv.add_argument("--level", dest="rv_level", default=None,
                    help="自律度を置換（report/assisted/unattended）")
    rv.add_argument("--track", dest="rv_track", default=None, help="track を置換（'' / none で削除）")
    rv.add_argument("--feedback", dest="rv_feedback", default=None,
                    help="次の act に必ず反映させる指示（例: e2e はローカルでなく実サーバに配備して実施）")
    rv.add_argument("--reason", default=None, help="決定記録に残す理由（省略時は feedback を流用）")

    rr = sub.add_parser("resume-run",
                        help="停滞・失敗した run を『続きから』再開（last_run を固定して ready へ。"
                             "失敗ノードだけやり直し・done は温存。決定記録）")
    _add_common(rr); rr.add_argument("id")
    rr.add_argument("--run", required=True, help="再開する run-id（bus/runs/<id>）")
    rr.add_argument("--reason", default=None, help="決定記録に残す理由")

    rj = sub.add_parser("reject",
                        help="タスクを却下（廃止して archive へ退避。依存先を再審査に戻し、"
                             "charter があれば再計画を要求。決定記録・avoid 記録）")
    _add_common(rj); rj.add_argument("id"); rj.add_argument("--reason", required=True)

    imp = sub.add_parser("impact",
                         help="タスクの依存関係（前提／依存先・推移）を一覧表示（変更・却下の影響範囲）")
    _add_common(imp); imp.add_argument("id")
    imp.add_argument("--json", action="store_true", help="JSON で出力")

    bo = sub.add_parser("board-offload",
                        help="タスクを委譲公示板（agent-board）へ委譲（ルーティングで workspace を確定）。"
                             "--board / --board-workload は _add_common 由来（設定 board: / "
                             "board_workload: と共通）")
    _add_common(bo); bo.add_argument("id")

    rpl = sub.add_parser("replan",
                         help="charter からバックログを再分解（エラー回復。done/既存と類似は投入しない）")
    _add_common(rpl)
    rpl.add_argument("--reason", default=None, help="決定記録に残す理由")
    rpl.add_argument("--charter", default=None,
                     help="対象 charter 名（charters/ 複数運用時。未指定は全 charter で消化可能）")
    rpl.add_argument("--revive", action="store_true",
                     help="墓標（却下済みタスク）を今回の再分解だけ無視する。"
                          "墓標そのものを消すには `agent-project revive <タイトル>`")

    dn = sub.add_parser("distill-notes",
                        help="観点メモ（notes/*.md）をバックログ候補へ分解する"
                             "（plan は notes を自動では消費しない。ここだけが入口）")
    _add_common(dn)
    dn.add_argument("--charter", default=None,
                    help="対象 charter 名（charters/ 複数運用時）")

    rvv = sub.add_parser("revive",
                         help="墓標を解除する（却下したタスクを再び提案されうる状態へ戻す）")
    _add_common(rvv)
    rvv.add_argument("title", help="解除するタスクのタイトル（正規化して照合する）")
    rvv.add_argument("--charter", default=None,
                     help="対象 charter 名（その charter 向けとタグ無しの墓標だけを解除）。"
                          "未指定で charter が複数あり対象が割れているときは、消さずに一覧を出す")
    rvv.add_argument("--all", dest="all_charters", action="store_true",
                     help="charter を問わず指紋一致の墓標をすべて解除する")

    _host_help = "agent-project.host.yaml の場所（既定: cwd → ~/.agents の順に探索）"
    srv = sub.add_parser("serve",
                         help="常駐体（resident）を起動: host.yaml のプロジェクトを監督し、"
                              "心拍・子状態を .agents/engine/status.json へ書く（実装計画 W1-11）")
    srv.add_argument("--host-config", default=None, help=_host_help)
    sts = sub.add_parser("status", help="常駐体の心拍・子状態（.agents/engine/status.json）を表示")
    sts.add_argument("--json", action="store_true", help="JSON で出力")
    wkr = sub.add_parser("worker",
                         help="ワーカーノード（lite）: 常駐体の起動、または init で host.yaml 生成"
                              "（serve と同一実装 — host.yaml の projects を空にして使う。設計 §4.3）")
    wkr.add_argument("--host-config", default=None, help=_host_help)
    wksub = wkr.add_subparsers(dest="worker_cmd")
    wki = wksub.add_parser("init", help="ワーカーノード用 host.yaml を生成する")
    wki.add_argument("--node-id", default=None, help="ノード ID（既定: ホスト名）")
    wki.add_argument("--tags", default=None, help="能力タグ（カンマ区切り）")
    wki.add_argument("--agent-cli", default=None, help="使える agent CLI（カンマ区切り）")
    wki.add_argument("--board", default=None, help="板（agent-board）の場所")
    wki.add_argument("--max-concurrent", type=int, default=None,
                     help="同時実行数の上限（0/省略=無制限。指定したときだけ host.yaml に書く）")
    wki.add_argument("--out", default=None, help="書き出し先（既定: ~/.agents/agent-project.host.yaml）")
    wki.add_argument("--force", action="store_true", help="既存ファイルを上書きする")

    # サブコマンドを省略して呼ばれたら「常駐体（serve）」を既定にする。
    # 常駐は PC に 1 本で、持つプロジェクトの宣言は host.yaml が単一ソース——裸起動が
    # cwd 1 件の `run --watch` に化けると、常駐体が監督している子と二重に回って
    # claim を奪い合う。`run` は常駐体が子として起動する経路なので、明示したときだけ動く。
    _subcommands = {"run", "triage", "needs", "promote", "rot", "stats", "audit",
                    "runlog", "doctor", "update", "enqueue", "approve", "retry-mr", "hold", "reprioritize",
                    "revise", "reject", "resume-run", "impact", "replan", "revive",
                    "distill-notes", "board-offload", "gc",
                    # 常駐体の内部配線（help からは隠すが、ここに載せないと
                    # 「サブコマンド無し＝serve」の既定に飲まれて別物が起動する）
                    "flow-participate", "flow-run",
                    "serve", "status", "worker"}
    if not argv or (argv[0] not in _subcommands and argv[0] not in ("-h", "--help")):
        argv = ["serve", *argv]

    args = p.parse_args(argv)

    if args.cmd == "serve":
        return cmd_serve(args)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "worker":
        return cmd_worker_init(args) if getattr(args, "worker_cmd", None) == "init" \
            else cmd_worker(args)

    resolve_config(args)      # CLI 未指定値を 設定ファイル → 組み込み既定 で確定
    cfg = build_config(args)

    if args.cmd in ("triage", "needs", "rot") and not cfg.backlog.exists():
        print(f"エラー: バックログディレクトリがありません: {cfg.backlog}", file=sys.stderr)
        return 2

    return {
        "run": lambda: cmd_run(cfg),
        "gc": lambda: cmd_gc(cfg, getattr(args, "json", False)),
        "flow-participate": lambda: cmd_flow_participate(
            cfg, getattr(args, "running", "") or "", getattr(args, "json", False),
            getattr(args, "node_declaration", "") or ""),
        "flow-run": lambda: cmd_flow_run(cfg, getattr(args, "run_id", "")),
        "triage": lambda: cmd_triage(cfg),
        "needs": lambda: cmd_needs(cfg),
        "enqueue": lambda: cmd_enqueue(cfg, args),
        "stats": lambda: cmd_stats(cfg, getattr(args, "json", False)),
        "audit": lambda: cmd_audit(cfg, getattr(args, "json", False),
                                   getattr(args, "strict", False)),
        "runlog": lambda: cmd_runlog(cfg, getattr(args, "json", False),
                                     getattr(args, "tail", 10)),
        "doctor": lambda: cmd_doctor(cfg, getattr(args, "fix", False),
                                     getattr(args, "json", False),
                                     cutover_from=getattr(args, "node_id_cutover", None)),
        "update": lambda: cmd_update(cfg, getattr(args, "now", False),
                                     getattr(args, "check", False)),
        "promote": lambda: cmd_promote(cfg),
        "rot": lambda: cmd_rot(cfg, getattr(args, "fix", False)),
        "approve": lambda: cmd_approve(cfg, args.id, args.reason,
                                       complete=bool(getattr(args, "complete", False))),
        "retry-mr": lambda: cmd_retry_mr(cfg, args.id),
        "reject": lambda: cmd_reject(cfg, args.id, args.reason),
        "board-offload": lambda: cmd_board_offload(cfg, args),
        "resume-run": lambda: cmd_resume_run(cfg, args.id, args.run,
                                             args.reason or "run の続きから再開"),
        "impact": lambda: cmd_impact(cfg, args.id, getattr(args, "json", False)),
        "hold": lambda: cmd_hold(cfg, args.id, args.reason),
        "reprioritize": lambda: cmd_reprioritize(
            cfg, args.id, "pin" if args.pin else "defer", args.reason),
        "revise": lambda: cmd_revise(
            cfg, args.id, {k: getattr(args, "node" if k == "node" else f"rv_{k}", None)
                           for k in REVISE_FIELDS},
            args.rv_feedback or "", args.reason or ""),
        "replan": lambda: cmd_replan(cfg, args.reason or "charter からのバックログ再分解",
                                     getattr(args, "charter", None) or "",
                                     revive=bool(getattr(args, "revive", False))),
        "revive": lambda: cmd_revive(cfg, args.title,
                                     getattr(args, "charter", None) or "",
                                     bool(getattr(args, "all_charters", False))),
        "distill-notes": lambda: cmd_distill_notes(cfg, getattr(args, "charter", None) or ""),
    }[args.cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
