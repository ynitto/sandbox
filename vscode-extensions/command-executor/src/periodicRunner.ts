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
  private _runningPrompts = new Set<string>();
  private _runningPromptTimers = new Map<string, NodeJS.Timeout>();
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

    const config = vscode.workspace.getConfiguration('agentExecutor');
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
    // 実行中プロンプトのタイムアウトタイマーをクリアして Set を空にする
    for (const timerId of this._runningPromptTimers.values()) {
      clearTimeout(timerId);
    }
    this._runningPromptTimers.clear();
    this._runningPrompts.clear();
  }

  dispose(): void {
    this.stop();
    this._outputChannel.dispose();
  }

  private _runPrompt(entry: PeriodicPromptConfig): void {
    const key = `${entry.agentId}:::${entry.intervalMinutes}:::${entry.prompt}`;
    if (this._runningPrompts.has(key)) {
      this._outputChannel.appendLine(
        `[${timestamp()}] スキップ (前回の実行が継続中): ${entry.agentId}`
      );
      return;
    }

    const agent = this._agents.find((a) => a.id === entry.agentId);
    if (!agent) {
      this._outputChannel.appendLine(
        `[${timestamp()}] エージェント "${entry.agentId}" が見つかりません`
      );
      return;
    }

    this._runningPrompts.add(key);

    // インターバルと同じ時間を上限として、プロセスが完了しない場合はキーを自動解除する
    const timeoutMs = entry.intervalMinutes * 60 * 1000;
    const timeoutId = setTimeout(() => {
      this._runningPrompts.delete(key);
      this._runningPromptTimers.delete(key);
      this._outputChannel.appendLine(
        `[${timestamp()}] タイムアウトによりスキップ制限を解除: ${entry.agentId}`
      );
    }, timeoutMs);
    this._runningPromptTimers.set(key, timeoutId);

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
        // 正常完了時はタイムアウトタイマーをキャンセルして即座にキーを解放する
        const timerId = this._runningPromptTimers.get(key);
        if (timerId !== undefined) {
          clearTimeout(timerId);
          this._runningPromptTimers.delete(key);
        }
        this._runningPrompts.delete(key);
        this._outputChannel.appendLine(`--- 完了 (終了コード: ${code}) ---\n`);
      }
    );
  }
}

function timestamp(): string {
  return new Date().toLocaleString('ja-JP');
}
