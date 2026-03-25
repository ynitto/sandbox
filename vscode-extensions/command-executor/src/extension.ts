import * as vscode from 'vscode';
import { ChatViewProvider } from './chatPanel';

export function activate(context: vscode.ExtensionContext): void {
  const provider = new ChatViewProvider(context);

  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(ChatViewProvider.viewId, provider)
  );

  // コマンドパレットからチャットビューにフォーカス
  context.subscriptions.push(
    vscode.commands.registerCommand('commandExecutor.openChat', () => {
      vscode.commands.executeCommand(`${ChatViewProvider.viewId}.focus`);
    })
  );
}

export function deactivate(): void {}
