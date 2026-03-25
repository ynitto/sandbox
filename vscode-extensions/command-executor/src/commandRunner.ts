import * as cp from 'child_process';
import * as os from 'os';

export interface CommandConfig {
  cmd: string;
  args: string[];
  label: string;
}

/**
 * ツール名とプロンプトから実行するコマンドを組み立てる。
 * agent-cli-proxy SKILL.md の呼び出し方法に従う。
 */
export function buildCommand(tool: string, prompt: string): CommandConfig {
  const isWindows = os.platform() === 'win32';

  switch (tool) {
    case 'claude':
      // 非インタラクティブモード: claude -p "<prompt>"
      return { cmd: 'claude', args: ['-p', prompt], label: 'Claude Code' };

    case 'gh-copilot-suggest':
      // シェルコマンドを提案: gh copilot suggest -t shell "<prompt>"
      return { cmd: 'gh', args: ['copilot', 'suggest', '-t', 'shell', prompt], label: 'Copilot: suggest (shell)' };

    case 'gh-copilot-suggest-git':
      return { cmd: 'gh', args: ['copilot', 'suggest', '-t', 'git', prompt], label: 'Copilot: suggest (git)' };

    case 'gh-copilot-suggest-gh':
      return { cmd: 'gh', args: ['copilot', 'suggest', '-t', 'gh', prompt], label: 'Copilot: suggest (gh)' };

    case 'gh-copilot-explain':
      // コマンドの説明: gh copilot explain "<prompt>"
      return { cmd: 'gh', args: ['copilot', 'explain', prompt], label: 'Copilot: explain' };

    case 'codex':
      // コード生成: codex "<prompt>"
      return { cmd: 'codex', args: [prompt], label: 'Codex' };

    case 'q':
      // 非インタラクティブチャット: q chat -p "<prompt>"
      return { cmd: 'q', args: ['chat', '-p', prompt], label: 'Amazon Q' };

    case 'kiro-cli':
      // Kiro: Windows は WSL2 経由
      if (isWindows) {
        return { cmd: 'wsl', args: ['kiro-cli', 'agent', prompt], label: 'Kiro (via WSL2)' };
      }
      return { cmd: 'kiro-cli', args: ['agent', prompt], label: 'Kiro' };

    default:
      return { cmd: 'claude', args: ['-p', prompt], label: 'Claude Code' };
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
