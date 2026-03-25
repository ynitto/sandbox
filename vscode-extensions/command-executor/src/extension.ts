import * as vscode from 'vscode';
import { ChatViewProvider } from './chatPanel';
import { loadAgents, CONFIG_DIR } from './agentLoader';
import { filterAvailableAgents } from './cliChecker';
import { syncCopilotConfig } from './homeSetup';

export function activate(context: vscode.ExtensionContext): void {
  const agents = filterAvailableAgents(loadAgents());
  const availableTools = new Set(agents.map((a) => a.tool));
  syncCopilotConfig(availableTools);

  const provider = new ChatViewProvider(context, agents);

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
      vscode.window.showInformationMessage(
        `AI CLI Executor: エージェントを再読み込みしました (${reloaded.length} 件)`
      );
    })
  );

  // ~/.config/ai-cli-executor/agents/ を監視して自動再読み込み
  const watcher = vscode.workspace.createFileSystemWatcher(
    new vscode.RelativePattern(vscode.Uri.file(CONFIG_DIR), 'agents/*.json')
  );

  const reload = () => provider.updateAgents(filterAvailableAgents(loadAgents()));
  watcher.onDidChange(reload);
  watcher.onDidCreate(reload);
  watcher.onDidDelete(reload);
  context.subscriptions.push(watcher);
}

export function deactivate(): void {}
