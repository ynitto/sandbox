import * as vscode from 'vscode';
import { ChatViewProvider } from './chatPanel';
import { loadAgents, CONFIG_DIR } from './agentLoader';
import { filterAvailableAgents } from './cliChecker';
import { syncCopilotConfig } from './homeSetup';
import { PeriodicRunner } from './periodicRunner';

let _chatViewProvider: ChatViewProvider | undefined;

export function activate(context: vscode.ExtensionContext): void {
  const agents = filterAvailableAgents(loadAgents());
  const availableTools = new Set(agents.map((a) => a.tool));

  const config = vscode.workspace.getConfiguration('commandExecutor');
  if (config.get<boolean>('syncOnStartup', true)) {
    syncCopilotConfig(availableTools);
  }

  const syncCallback = () => {
    syncCopilotConfig(availableTools);
    vscode.window.showInformationMessage('Agent CLI Executor: ~/.copilot/ の同期が完了しました');
  };
  const provider = new ChatViewProvider(context, agents, syncCallback);
  _chatViewProvider = provider;

  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(ChatViewProvider.viewId, provider)
  );

  // コマンドパレットからチャットビューにフォーカス
  context.subscriptions.push(
    vscode.commands.registerCommand('commandExecutor.openChat', () => {
      vscode.commands.executeCommand(`${ChatViewProvider.viewId}.focus`);
    })
  );

  // エージェントを再読み込みするコマンド
  context.subscriptions.push(
    vscode.commands.registerCommand('commandExecutor.reloadAgents', () => {
      const reloaded = filterAvailableAgents(loadAgents());
      provider.updateAgents(reloaded);
      periodicRunner.updateAgents(reloaded);
      vscode.window.showInformationMessage(
        `Agent CLI Executor: エージェントを再読み込みしました (${reloaded.length} 件)`
      );
    })
  );

  // ~/.config/agent-cli-executor/agents/ を監視して自動再読み込み
  const watcher = vscode.workspace.createFileSystemWatcher(
    new vscode.RelativePattern(vscode.Uri.file(CONFIG_DIR), 'agents/*.json')
  );

  const reload = () => {
    const reloaded = filterAvailableAgents(loadAgents());
    provider.updateAgents(reloaded);
    periodicRunner.updateAgents(reloaded);
  };
  watcher.onDidChange(reload);
  watcher.onDidCreate(reload);
  watcher.onDidDelete(reload);
  context.subscriptions.push(watcher);

  // 定期プロンプト
  const workspacePath = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  const periodicRunner = new PeriodicRunner(agents, workspacePath);
  periodicRunner.start();
  context.subscriptions.push({ dispose: () => periodicRunner.dispose() });

  // 設定変更時に定期プロンプトを再起動
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration('commandExecutor.periodicPrompts')) {
        periodicRunner.start();
      }
    })
  );
}

export function deactivate(): void {
  _chatViewProvider?.dispose();
  _chatViewProvider = undefined;
}
