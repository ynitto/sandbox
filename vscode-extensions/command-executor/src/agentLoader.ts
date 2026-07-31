/**
 * ~/.config/agent-cli-executor/agents/*.json からエージェント設定を読み込む。
 * Copilot の ~/.copilot/agents/*.agent.md に相当するユーザー設定ディレクトリ。
 *
 * WSL 対応:
 *   - VS Code が WSL 上で動作する場合: os.homedir() は Linux パスを返す (/home/user)
 *   - VS Code が Windows ネイティブで動作する場合: os.homedir() は Windows パスを返す (C:\Users\user)
 *   どちらも os.homedir() で正しく解決されるため、特別な変換は不要。
 */

import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';
import { AgentConfig, BUILTIN_AGENTS } from './agentConfig';

/** エージェント設定のルートディレクトリ（Copilot の ~/.copilot/ 相当） */
export const CONFIG_DIR = path.join(os.homedir(), '.config', 'agent-cli-executor');

/** エージェント定義ディレクトリ（Copilot の agents/ 相当） */
const AGENTS_DIR = path.join(CONFIG_DIR, 'agents');

/**
 * ユーザー定義エージェントを読み込む。
 * エージェントが定義されていない場合はビルトインのみを返す。
 * ユーザー定義エージェントがある場合はビルトインの前に追加する。
 */
export function loadAgents(): AgentConfig[] {
  const userAgents = readUserAgents();

  if (userAgents.length === 0) {
    return BUILTIN_AGENTS;
  }

  // ユーザー定義を先頭に、ビルトインを後ろに
  const userIds = new Set(userAgents.map((a) => a.id));
  const remainingBuiltins = BUILTIN_AGENTS.filter((a) => !userIds.has(a.id));
  return [...userAgents, ...remainingBuiltins];
}

function readUserAgents(): AgentConfig[] {
  try {
    if (!fs.existsSync(AGENTS_DIR)) {
      return [];
    }

    const files = fs.readdirSync(AGENTS_DIR).filter((f) => f.endsWith('.json'));
    const agents: AgentConfig[] = [];

    for (const file of files) {
      try {
        const raw = fs.readFileSync(path.join(AGENTS_DIR, file), 'utf-8');
        const parsed = JSON.parse(raw) as Partial<AgentConfig>;

        // 必須フィールドの検証
        if (!parsed.name || !parsed.tool) {
          continue;
        }

        // id が未指定の場合はファイル名（拡張子なし）を使用
        const id = parsed.id ?? path.basename(file, '.json');
        agents.push({ id, ...parsed } as AgentConfig);
      } catch {
        // 不正な JSON はスキップ
      }
    }

    return agents;
  } catch {
    return [];
  }
}

/**
 * instructionsFile を解決してファイルの内容を返す。
 * @param instructionsFile 絶対パス、または CONFIG_DIR からの相対パス
 */
export function loadInstructionsFile(instructionsFile: string): string | undefined {
  try {
    const resolved = path.isAbsolute(instructionsFile)
      ? instructionsFile
      : path.join(CONFIG_DIR, instructionsFile);

    // 相対パス指定の場合は CONFIG_DIR 外へのトラバーサル（../../etc/passwd 等）を拒否する。
    // 絶対パス指定は意図的な参照として許容する。
    if (!path.isAbsolute(instructionsFile)) {
      const normalizedResolved = path.normalize(resolved);
      const normalizedConfig = path.normalize(CONFIG_DIR);
      if (!normalizedResolved.startsWith(normalizedConfig + path.sep) && normalizedResolved !== normalizedConfig) {
        return undefined;
      }
    }

    return fs.readFileSync(resolved, 'utf-8');
  } catch {
    return undefined;
  }
}
