/**
 * エージェント設定の型定義とビルトインエージェント一覧。
 *
 * ユーザー定義エージェントは ~/.config/agent-cli-executor/agents/*.json に置く。
 * Copilot の ~/.github/copilot/agents/*.agent.md に相当。
 */

export interface AgentConfig {
  /** ユニーク識別子（ファイル名から自動設定も可） */
  id: string;
  /** ドロップダウンに表示する名前 */
  name: string;
  /** 説明（ツールチップ用） */
  description?: string;
  /**
   * 使用する CLI ツール。
   * "claude" | "gh-copilot-suggest" | "gh-copilot-suggest-git" |
   * "gh-copilot-suggest-gh" | "gh-copilot-explain" | "codex" | "q" | "kiro-cli"
   */
  tool: string;
  /**
   * システムプロンプト（インライン）。
   * プロンプトの先頭に付加される。
   */
  instructions?: string;
  /**
   * システムプロンプトファイルのパス。
   * 絶対パス、または ~/.config/agent-cli-executor/ からの相対パス。
   * Copilot の instructions/*.instructions.md に相当。
   */
  instructionsFile?: string;
  /** ツール呼び出しに追加する CLI オプション */
  extraArgs?: string[];
}

/** ~/.config/agent-cli-executor/agents/ が存在しない場合のフォールバック */
export const BUILTIN_AGENTS: AgentConfig[] = [
  {
    id: 'claude',
    name: 'Claude Code',
    description: '汎用的な Claude Code エージェント',
    tool: 'claude',
  },
  {
    id: 'gh-copilot-suggest',
    name: 'Copilot: suggest (shell)',
    description: 'シェルコマンドを提案',
    tool: 'gh-copilot-suggest',
  },
  {
    id: 'gh-copilot-suggest-git',
    name: 'Copilot: suggest (git)',
    description: 'Git コマンドを提案',
    tool: 'gh-copilot-suggest-git',
  },
  {
    id: 'gh-copilot-suggest-gh',
    name: 'Copilot: suggest (gh)',
    description: 'gh CLI コマンドを提案',
    tool: 'gh-copilot-suggest-gh',
  },
  {
    id: 'gh-copilot-explain',
    name: 'Copilot: explain',
    description: 'コマンドを説明',
    tool: 'gh-copilot-explain',
  },
  {
    id: 'codex',
    name: 'Codex',
    description: 'OpenAI Codex でコード生成',
    tool: 'codex',
  },
  {
    id: 'amazon-q',
    name: 'Amazon Q',
    description: 'Amazon Q Developer CLI',
    tool: 'q',
  },
  {
    id: 'kiro',
    name: 'Kiro',
    description: 'Kiro CLI',
    tool: 'kiro-cli',
  },
];
