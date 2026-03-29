import * as vscode from 'vscode';
import { AgentConfig } from './agentConfig';
import { buildCommand, runCommand } from './commandRunner';

export interface PeriodicPromptConfig {
  agentId: string;
  prompt: string;
  intervalMinutes: number;
  enabled?: boolean;
}

export class PeriodicRunner {
  private _timers: NodeJS.Timeout[] = [];
  private _outputChannel: vscode.OutputChannel;

  constructor(
    private _agents: AgentConfig[],
    private _workspacePath: string | undefined
  ) {
    this._outputChannel = vscode.window.createOutputChannel('Agent CLI Periodic Prompts');
  }

  updateAgents(agents: AgentConfig[]): void {
    this._agents = agents;
  }

  /** 設定を読み込んで定期タイマーを開始する */
  start(): void {
    this.stop();

    const config = vscode.workspace.getConfiguration('commandExecutor');
    const periodicPrompts: PeriodicPromptConfig[] = config.get('periodicPrompts') ?? [];

    for (const entry of periodicPrompts) {
      if (entry.enabled === false) {
        continue;
      }
      if (!entry.agentId || !entry.prompt || !entry.intervalMinutes || entry.intervalMinutes < 1) {
        continue;
      }

      const intervalMs = entry.intervalMinutes * 60 * 1000;
      const timer = setInterval(() => {
        this._runPrompt(entry);
      }, intervalMs);

      this._timers.push(timer);
    }

    if (this._timers.length > 0) {
      this._outputChannel.appendLine(
        `[${timestamp()}] 定期プロンプト開始: ${this._timers.length} 件`
      );
    }
  }

  /** 全タイマーを停止する */
  stop(): void {
    for (const timer of this._timers) {
      clearInterval(timer);
    }
    this._timers = [];
  }

  dispose(): void {
    this.stop();
    this._outputChannel.dispose();
  }

  private _runPrompt(entry: PeriodicPromptConfig): void {
    const agent = this._agents.find((a) => a.id === entry.agentId);
    if (!agent) {
      this._outputChannel.appendLine(
        `[${timestamp()}] エージェント "${entry.agentId}" が見つかりません`
      );
      return;
    }

    const cmdConfig = buildCommand(agent, entry.prompt, this._workspacePath);
    this._outputChannel.appendLine(
      `[${timestamp()}] 定期プロンプト実行: ${agent.name}`
    );
    this._outputChannel.appendLine(`> ${entry.prompt}`);
    this._outputChannel.appendLine('---');

    runCommand(
      cmdConfig,
      (data) => this._outputChannel.append(data),
      (data) => this._outputChannel.append(data),
      (code) => {
        this._outputChannel.appendLine(`--- 完了 (終了コード: ${code}) ---\n`);
      }
    );
  }
}

function timestamp(): string {
  return new Date().toLocaleString('ja-JP');
}
