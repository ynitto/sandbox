import * as cp from 'child_process';
import * as os from 'os';
import { AgentConfig } from './agentConfig';
import { loadInstructionsFile } from './agentLoader';

export interface CommandConfig {
  cmd: string;
  args: string[];
  label: string;
}

/**
 * エージェント設定とプロンプトから実行するコマンドを組み立てる。
 * agent-cli-proxy SKILL.md の呼び出し方法に従う。
 *
 * instructions が指定されている場合はプロンプトの先頭に付加する。
 */
export function buildCommand(agent: AgentConfig, userPrompt: string): CommandConfig {
  const isWindows = os.platform() === 'win32';

  // システムプロンプト（instructions / instructionsFile）を解決
  let systemPrompt = agent.instructions ?? '';
  if (!systemPrompt && agent.instructionsFile) {
    systemPrompt = loadInstructionsFile(agent.instructionsFile) ?? '';
  }

  // プロンプトにシステムプロンプトを結合
  const prompt = systemPrompt
    ? `${systemPrompt.trimEnd()}\n\n---\n\n${userPrompt}`
    : userPrompt;

  const extra = agent.extraArgs ?? [];

  switch (agent.tool) {
    case 'claude':
      // 非インタラクティブモード: claude -p "<prompt>"
      return { cmd: 'claude', args: ['-p', prompt, ...extra], label: agent.name };

    case 'gh-copilot-suggest':
      // シェルコマンドを提案: gh copilot suggest -t shell "<prompt>"
      return { cmd: 'gh', args: ['copilot', 'suggest', '-t', 'shell', prompt, ...extra], label: agent.name };

    case 'gh-copilot-suggest-git':
      return { cmd: 'gh', args: ['copilot', 'suggest', '-t', 'git', prompt, ...extra], label: agent.name };

    case 'gh-copilot-suggest-gh':
      return { cmd: 'gh', args: ['copilot', 'suggest', '-t', 'gh', prompt, ...extra], label: agent.name };

    case 'gh-copilot-explain':
      // コマンドの説明: gh copilot explain "<prompt>"
      return { cmd: 'gh', args: ['copilot', 'explain', prompt, ...extra], label: agent.name };

    case 'codex':
      // コード生成: codex "<prompt>"
      return { cmd: 'codex', args: [prompt, ...extra], label: agent.name };

    case 'q':
      // 非インタラクティブチャット: q chat -p "<prompt>"
      return { cmd: 'q', args: ['chat', '-p', prompt, ...extra], label: agent.name };

    case 'kiro-cli':
      // 非インタラクティブ: kiro-cli chat --no-interactive "<prompt>"
      // Windows は WSL2 経由
      if (isWindows) {
        return { cmd: 'wsl', args: ['kiro-cli', 'chat', '--no-interactive', prompt, ...extra], label: agent.name };
      }
      return { cmd: 'kiro-cli', args: ['chat', '--no-interactive', prompt, ...extra], label: agent.name };

    default:
      return { cmd: 'claude', args: ['-p', prompt, ...extra], label: agent.name };
  }
}

/**
 * コマンドをサブプロセスとして実行し、stdout/stderr をコールバックでストリーミングする。
 */
export function runCommand(
  config: CommandConfig,
  cwd: string | undefined,
  onData: (chunk: string) => void,
  onError: (chunk: string) => void,
  onClose: (code: number | null) => void
): cp.ChildProcess {
  const proc = cp.spawn(config.cmd, config.args, {
    cwd,
    env: { ...process.env },
    shell: false,
  });

  proc.stdout.on('data', (data: Buffer) => {
    onData(data.toString());
  });

  proc.stderr.on('data', (data: Buffer) => {
    onError(data.toString());
  });

  proc.on('close', (code) => {
    onClose(code);
  });

  proc.on('error', (err) => {
    onError(`コマンド起動失敗: ${err.message}\n`);
    onClose(null);
  });

  return proc;
}
