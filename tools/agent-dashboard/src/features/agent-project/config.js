'use strict';

// agent-project / agent-flow 制御面の既定設定。
// base の DEFAULT_CONFIG に deepMerge される（features/index 経由）。

module.exports = {
  // 実行エンジン（常駐体 `agent-project serve`）の在り処。プロジェクトの一覧・稼働・
  // 共有の状況はすべて <home>/engine/status.json から読む（実装計画 W2-4）。
  engine: {
    // WSL のディストリビューション名（空 = 既定）。Windows からエンジンの置き場と
    // プロジェクトのフォルダを開くのに使う。
    distro: '',
    // 状況ファイルを置いている `.agents` フォルダ。空なら WSL（distro）の ~/.agents を
    // 自動で引き、WSL が無ければこの PC の ~/.agents を使う。
    home: '',
  },
  projects: {
    // 自動リロードの間隔（秒）。0 で無効（手動リロードのみ）。
    refreshSec: 5,
    // 要対応（人の判断待ち）の SLA しきい値（時間）。needs の最終更新からの経過が
    // この値を超えると一覧で赤バッジ、1/3 を超えると黄バッジで停滞を知らせる。
    // 未対応は「待ち時間の長い順」で並べ、最も停滞した判断待ちを上に出す。
    needsSlaHours: 24,
    // agent-flow の明示バス（agent-project を --bus / 設定 bus: 付きで運用している
    // 場合）のパス。空なら <root>/bus → agent-project 設定ファイル（.agent/）の bus: の
    // 順にファイルから自動発見する。
    flowBus: '',
    // プロジェクト名 → agent-flow バスパスの写像。pure-remote（clone のみ・ローカル daemon
    // 無し）で agent-flow の鏡写し先 clone を各プロジェクトに割り当てる。例:
    //   { "alpha": "C:\\clones\\alpha\\agent-flow", "beta": "/home/me/clones/beta/agent-flow" }
    // <root>/bus が実在（runs/ あり）ならそちらが優先される。
    flowBusByProject: {},
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
