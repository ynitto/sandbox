import * as cp from 'child_process';
import * as os from 'os';
import { AgentConfig } from './agentConfig';
import { loadInstructionsFile } from './agentLoader';
import { toWslPath } from './pathUtils';

export interface CommandConfig {
  cmd: string;
  args: string[];
  label: string;
  /**
   * spawn に渡す cwd（OS ネイティブパス）。
   * kiro-cli on Windows の場合は wsl.exe を起動する Windows 側の cwd。
   * WSL 内部の cwd は args の --cd で別途指定される。
   */
  cwd: string | undefined;
}

/**
 * エージェント設定とプロンプトから実行するコマンドを組み立てる。
 * agent-cli-proxy SKILL.md の呼び出し方法に従う。
 *
 * @param agent        エージェント設定
 * @param userPrompt   ユーザー入力プロンプト
 * @param workspacePath VS Code の workspace uri.fsPath（未設定の場合は undefined）
 */
export function buildCommand(
  agent: AgentConfig,
  userPrompt: string,
  workspacePath: string | undefined
): CommandConfig {
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
      return {
        cmd: 'claude',
        args: ['-p', prompt, ...extra],
        label: agent.name,
        cwd: workspacePath,
      };

    case 'gh-copilot-suggest':
      return {
        cmd: 'gh',
        args: ['copilot', 'suggest', '-t', 'shell', prompt, ...extra],
        label: agent.name,
        cwd: workspacePath,
      };

    case 'gh-copilot-suggest-git':
      return {
        cmd: 'gh',
        args: ['copilot', 'suggest', '-t', 'git', prompt, ...extra],
        label: agent.name,
        cwd: workspacePath,
      };

    case 'gh-copilot-suggest-gh':
      return {
        cmd: 'gh',
        args: ['copilot', 'suggest', '-t', 'gh', prompt, ...extra],
        label: agent.name,
        cwd: workspacePath,
      };

    case 'gh-copilot-explain':
      return {
        cmd: 'gh',
        args: ['copilot', 'explain', prompt, ...extra],
        label: agent.name,
        cwd: workspacePath,
      };

    case 'codex':
      return {
        cmd: 'codex',
        args: [prompt, ...extra],
        label: agent.name,
        cwd: workspacePath,
      };

    case 'q':
      return {
        cmd: 'q',
        args: ['chat', '-p', prompt, ...extra],
        label: agent.name,
        cwd: workspacePath,
      };

    case 'kiro-cli':
      if (isWindows) {
        // Windows から WSL2 経由で実行。
        // wsl --cd <linuxPath> で WSL 内カレントディレクトリを明示指定する。
        // spawn の cwd（Windows 側）には元の fsPath を渡す。
        const wslCwd = workspacePath ? toWslPath(workspacePath) : undefined;
        const wslArgs = wslCwd
          ? ['--cd', wslCwd, 'kiro-cli', 'chat', '--no-interactive', prompt, ...extra]
          : ['kiro-cli', 'chat', '--no-interactive', prompt, ...extra];
        return {
          cmd: 'wsl',
          args: wslArgs,
          label: agent.name,
          cwd: workspacePath,  // wsl.exe の Windows 側 cwd
        };
      }
      return {
        cmd: 'kiro-cli',
        args: ['chat', '--no-interactive', prompt, ...extra],
        label: agent.name,
        cwd: workspacePath,
      };

    default:
      return {
        cmd: 'claude',
        args: ['-p', prompt, ...extra],
        label: agent.name,
        cwd: workspacePath,
      };
  }
}

/**
 * コマンドをサブプロセスとして実行し、stdout/stderr をコールバックでストリーミングする。
 */
export function runCommand(
  config: CommandConfig,
  onData: (chunk: string) => void,
  onError: (chunk: string) => void,
  onClose: (code: number | null) => void
): cp.ChildProcess {
  const proc = cp.spawn(config.cmd, config.args, {
    cwd: config.cwd,
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
