'use strict';

// agent-project / agent-flow 制御面の既定設定。
// base の DEFAULT_CONFIG に deepMerge される（features/index 経由）。

module.exports = {
  projects: {
    // 監視するワークスペースの一覧（1 行 1 プロジェクト）。
    // 成果物リポジトリを登録すると、配下の .agents/agent-project.yaml の
    // state_repo / state_repo_dir から状態専用 clone をルートとして解決する。
    // 状態 clone を直接登録してもよい（従来どおり）。
    // 例: ["C:\\clones\\payments", "/home/me/clones/webapp"]
    // プロジェクトでないフォルダを登録すると「プロジェクトを束ねる親フォルダ」と
    // みなし、配下から agent-project.yaml（または charter.md / backlog/ 等）を持つ
    // ディレクトリを自動発見して、それぞれ 1 プロジェクトとして追加する。
    roots: [],
    // 親フォルダ登録時に配下を探索する深さ（既定 2 階層）。
    scanDepth: 2,
    // ~/.agent-project/instances/*.json（稼働発見レコード）から
    // 稼働中プロジェクトを自動発見して roots に加える。
    autoDiscover: true,
    // 自動リロードの間隔（秒）。0 で無効（手動リロードのみ）。
    refreshSec: 5,
    // 要対応（人の判断待ち）の SLA しきい値（時間）。needs の最終更新からの経過が
    // この値を超えると一覧で赤バッジ、1/3 を超えると黄バッジで停滞を知らせる。
    // 未対応は「待ち時間の長い順」で並べ、最も停滞した判断待ちを上に出す。
    needsSlaHours: 24,
    // 選択中プロジェクトのリポジトリを git pull で最新化する間隔（秒）。
    // 0 で自動 pull なし（サイドバーの ⇣ ボタンで手動 pull は常にできる）。
    // ポーリング（refreshSec）よりずっと長い間隔にしてリモートへの負荷を抑える。
    // 60 秒未満は 60 秒に切り上げる。
    gitPullSec: 300,
    // 状態共有 git 同期の push 側: ユーザー操作（指示ドロップ・inbox 投入・
    // needs 記入・削除）のたびに、操作したディレクトリだけをコミットして push する。
    // 「プロジェクトルート = 状態共有リポジトリの clone」を一次経路とするため既定 on。
    // 非 git のパスでは commitPush が notRepo で無害にスキップされる。
    // 有効時は pull も --rebase で取り込む。
    gitAutoPush: true,
    // approve / hold / reprioritize（決定記録を残す人の操作）に使う
    // agent-project CLI。PATH に無い場合はフルパスや
    // "python3 /path/to/agent-project.py" 形式でも指定できる。
    // command は本体（agent-project）の起動（start）にのみ使う。人の指示（approve / hold / pin /
    // defer / revise / replan）は commands/<name>.json のファイルドロップ一本で届ける（案2後半で
    // actionMode の auto/cli 分岐とサイレントフォールバックを撤去。稼働中の本体が同期越しに取り込み、
    // 受理レシートでカードへ反映する。停止中は取り込み待ちで残り「押しても何も起きない」を排除）。
    command: 'agent-project',
    // agent-flow の明示バス（agent-project を --bus / 設定 bus: 付きで運用している
    // 場合）のパス。空なら <root>/bus → agent-project 設定ファイル（.agent/）の bus: の
    // 順にファイルから自動発見する。
    flowBus: '',
    // プロジェクト名 → agent-flow バスパスの写像。pure-remote（clone のみ・ローカル daemon
    // 無し）で agent-flow の鏡写し先 clone を各プロジェクトに割り当てる。例:
    //   { "alpha": "C:\\clones\\alpha\\agent-flow", "beta": "/home/me/clones/beta/agent-flow" }
    // <root>/bus が実在（runs/ あり）ならそちらが優先される。
    flowBusByProject: {},
    // agent-flow daemon ロック（daemon-<sha1>.lock）の置き場。空なら ~/.agent の
    // 設定ファイル lock_dir → 両ツール既定の $TMPDIR/agent-flow-locks を使う。
    flowLockDir: '',
  },
  notifications: {
    // 新しい「要対応（人の判断待ち）」が現れたら OS 通知・タスクバーバッジ・ウィンドウの
    // フラッシュで知らせる（張り付き監視を不要にする＝人の省力化）。ウィンドウを見ている
    // 間はポップアップとフラッシュを抑制し、バッジ（未対応の総数）だけを更新する。
    // discover() が各プロジェクトに載せる needsCount（サイドバーの要対応バッジと同じ数）の
    // 増分で検知する。起動直後の既存分では通知しない（初回はベースライン取得のみ）。
    enabled: true,
  },
  agent: {
    // charter の AI 下書き・補完と読み取り専用 Doctor に共通で使うエージェント CLI。
    //   kiro / claude / copilot / codex / cursor / ollama
    cli: 'kiro',
    // エージェント CLI に渡すモデル（空 = CLI / プロジェクト設定の既定）。
    // 例: claude は sonnet・opus、copilot は claude-sonnet-4.5・gpt-5 等。
    model: '',
    // 1 回の補完呼び出しのタイムアウト秒（下限 30 秒）
    timeoutSec: 180,
  },
};
