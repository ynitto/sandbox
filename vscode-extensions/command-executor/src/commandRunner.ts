import * as cp from 'child_process';
import * as os from 'os';
import * as vscode from 'vscode';
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
  /**
   * true のとき stdout を stream-json として行単位でパースし、
   * アシスタントのテキストブロックのみ onData に渡す。
   * セッション ID は onClose の第 2 引数として返す。
   */
  streamJson?: boolean;
}

function escapePosixShellArg(value: string): string {
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

/**
 * エージェント設定とプロンプトから実行するコマンドを組み立てる。
 * agent-cli-proxy SKILL.md の呼び出し方法に従う。
 *
 * @param agent        エージェント設定
 * @param userPrompt   ユーザー入力プロンプト
 * @param workspacePath VS Code の workspace uri.fsPath（未設定の場合は undefined）
 * @param model        使用するモデル名（claude / kiro-cli ツールのみ有効、未指定の場合はデフォルト）
 * @param sessionId    継続するセッション ID（claude: --resume、kiro-cli: --session-id として渡す）
 */
export function buildCommand(
  agent: AgentConfig,
  userPrompt: string,
  workspacePath: string | undefined,
  model?: string,
  sessionId?: string
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
    case 'claude': {
      const modelArgs = model ? ['--model', model] : [];
      const sessionArgs = sessionId ? ['--resume', sessionId] : [];
      return {
        cmd: 'claude',
        args: ['-p', prompt, '--output-format', 'stream-json', ...modelArgs, ...sessionArgs, ...extra],
        label: agent.name,
        cwd: workspacePath,
        streamJson: true,
      };
    }

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

    case 'kiro-cli': {
      const modelArgs = model ? ['--model', model] : [];
      // kiro-cli は --session-id を持たず、--resume で直前セッションを継続する
      const sessionArgs = sessionId !== undefined ? ['--resume'] : [];
      if (isWindows) {
        // Windows から WSL2 経由で実行
        // wsl --cd <linuxPath> で WSL 内のカレントディレクトリを明示指定する
        // spawn の cmd (Windows 側) には元の fsPath を渡す
        const wslCwd = workspacePath ? toWslPath(workspacePath) : undefined;
        // チェックと同時に wsl -lc 経由で起動し WSL 側 PATH 解決を一致させる
        const escapedOptionArgs = [...extra].map(escapePosixShellArg);
        const wslShellArgs = [
          '-e', 'sh', '-lc',
          ['kiro-cli', 'chat', '--no-interactive', '--trust-all-tools', ...modelArgs, ...sessionArgs, ...escapedOptionArgs, '--', '"$1"'].join(' '),
          'sh', prompt,
        ];
        const wslArgs = wslCwd
          ? ['--cd', wslCwd, ...wslShellArgs]
          : wslShellArgs;
        return {
          cmd: 'wsl.exe',
          args: wslArgs,
          label: agent.name,
          cwd: workspacePath, // wsl.exe の Windows 側 cwd
        };
      }
      return {
        cmd: 'kiro-cli',
        args: ['chat', '--no-interactive', '--trust-all-tools', ...modelArgs, ...sessionArgs, ...extra, '--', prompt],
        label: agent.name,
        cwd: workspacePath,
      };
    }

    default: {
      const modelArgs = model ? ['--model', model] : [];
      return {
        cmd: 'claude',
        args: ['-p', prompt, ...modelArgs, ...extra],
        label: agent.name,
        cwd: workspacePath,
      };
    }
  }
}

function getMergedExtraArgs(agent: AgentConfig): string[] {
  const config = vscode.workspace.getConfiguration('agentExecutor');
  const globalExtra= config.get<string[]>('extraArgs', []);
  const toolExtraMap = config.get<Record<string, unknown>>('cliExtraArgsByTool', {});
  const toolExtraRaw = toolExtraMap[agent.tool];

  const toolExtra = Array.isArray(toolExtraRaw)
    ? toolExtraRaw.filter((x): x is string => typeof x === 'string')
    : [];

  const agentExtra = agent.extraArgs ?? [];
  return [...globalExtra, ...toolExtra, ...agentExtra];
}

/**
 * コマンドをサブプロセスとして実行し、stdout/stderr をコールバックでストリーミングする。
 *
 * config.streamJson が true の場合は stdout を stream-json として行単位でパースし、
 * アシスタントのテキストブロックのみを onData に渡す。
 * セッション ID が取得できた場合は onClose の第 2 引数として返す。
 */
export function runCommand(
  config: CommandConfig,
  onData: (chunk: string) => void,
  onError: (chunk: string) => void,
  onClose: (code: number | null, sessionId?: string) => void
): cp.ChildProcess {
  const env = { ...process.env };
  if (os.platform() === 'win32' && config.cmd.toLowerCase() === 'wsl.exe') {
    // 拡張ホスト経由で発生しうる systemd user session 起動失敗を回避する
    env.WSL_SYSTEMD_NO_SESSION = '1';
  }
  const proc = cp.spawn(config.cmd, config.args, {
    cwd: config.cwd,
    env,
    shell: false,
  });

  // eslint-disable-next-line no-control-regex
  const stripAnsi = (s: string) => s.replace(/\x1b\[[\x20-\x3f]*[\x40-\x7e]/g, '');

  let lineBuffer = '';
  let capturedSessionId: string | undefined;

  /**
   * stream-json の 1 行を解析してテキスト表示とセッション ID 抽出を行う。
   */
  function processStreamJsonLine(line: string): void {
    const trimmed = line.trim();
    if (!trimmed) { return; }
    let obj: Record<string, unknown>;
    try {
      obj = JSON.parse(trimmed) as Record<string, unknown>;
    } catch {
      // JSON でない行はそのまま表示する
      onData(stripAnsi(line) + '\n');
      return;
    }
    // system / result メッセージから session_id を取得する
    if (typeof obj['session_id'] === 'string') {
      capturedSessionId = obj['session_id'] as string;
    }
    // assistant メッセージからテキストブロックを抽出して表示する
    if (obj['type'] === 'assistant') {
      type ContentBlock = { type: string; text?: string };
      type AssistantMessage = { content?: ContentBlock[] };
      const msg = obj['message'] as AssistantMessage | undefined;
      for (const block of msg?.content ?? []) {
        if (block.type === 'text' && block.text) {
          onData(block.text);
        }
      }
    }
  }

  proc.stdout.on('data', (data: Buffer) => {
    if (config.streamJson) {
      lineBuffer += data.toString();
      const lines = lineBuffer.split('\n');
      lineBuffer = lines.pop() ?? '';
      for (const line of lines) {
        processStreamJsonLine(line);
      }
    } else {
      onData(stripAnsi(data.toString()));
    }
  });

  proc.stderr.on('data', (data: Buffer) => {
    onError(stripAnsi(data.toString()));
  });

  proc.on('close', (code) => {
    // バッファに残った最終行を処理する
    if (config.streamJson && lineBuffer.trim()) {
      processStreamJsonLine(lineBuffer);
    }
    onClose(code, capturedSessionId);
  });

  proc.on('error', (err) => {
    onError(`コマンド起動失敗: ${err.message}\n`);
    onClose(null);
  });

  return proc;
}
