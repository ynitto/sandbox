import * as crypto from 'crypto';
import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import { AgentConfig } from './agentConfig';
import { buildCommand, runCommand } from './commandRunner';
import { fetchClaudeModels, fetchKiroModels, FALLBACK_CLAUDE_MODELS, FALLBACK_KIRO_MODELS } from './modelFetcher';

type ChatHistoryEntry =
  | { role: 'user'; content: string }
  | { role: 'assistant'; stdout: string; stderr: string };

export class ChatViewProvider implements vscode.WebviewViewProvider {
  public static readonly viewId = 'agentExecutor.chatView';

  private _view?: vscode.WebviewView;
  private _currentProcess?: cp.ChildProcess;
  private _agents: AgentConfig[];
  private _selectedAgentId?: string;
  private _selectedModel?: string;
  private _claudeModels: string[] = FALLBACK_CLAUDE_MODELS;
  private _kiroModels: string[] = FALLBACK_KIRO_MODELS;
  private _chatHistory: ChatHistoryEntry[];
  private _currentStdout = '';
  private _currentStderr = '';
  /** エージェントごとの継続セッション ID（claude: --resume、kiro-cli: --session-id として渡す） */
  private _agentSessions: Map<string, string> = new Map();

  constructor(
    private readonly _context: vscode.ExtensionContext,
    agents: AgentConfig[],
    private readonly _onSync?: () => void
  ) {
    this._agents = agents;
    this._selectedAgentId = agents[0]?.id;
    this._selectedModel = this._context.globalState.get<string>('selectedModel');
    this._chatHistory = this._context.globalState.get<ChatHistoryEntry[]>('chatHistory', []);
  }

  /** エージェント一覧を更新して WebView を再描画する */
  updateAgents(agents: AgentConfig[]): void {
    this._agents = agents;
    // エージェントが再読み込みされたら古いセッション ID を破棄する。
    // 同一 ID で設定が変わったエージェントに前のセッションが引き継がれないようにするため。
    this._agentSessions.clear();
    if (this._view) {
      this._killCurrentProcess();
      this._view.webview.html = this._getHtml(this._view.webview);
    }
  }

  /** 実行中のプロセスを終了する（拡張機能 deactivate 時に呼ばれる） */
  dispose(): void {
    this._killCurrentProcess();
  }

  resolveWebviewView(
    webviewView: vscode.WebviewView,
    _context: vscode.WebviewViewResolveContext,
    _token: vscode.CancellationToken
  ): void {
    this._view = webviewView;

    webviewView.webview.options = {
      enableScripts: true,
    };

    webviewView.webview.onDidReceiveMessage((msg) => {
      switch (msg.type) {
        case 'run':
          this._runCommand(msg.agentId, msg.prompt, msg.model);
          break;
        case 'kill':
          this._killCurrentProcess();
          break;
        case 'sync':
          this._syncConfig();
          break;
        case 'clearHistory':
          this._chatHistory = [];
          this._agentSessions.clear();
          void this._context.globalState.update('chatHistory', []);
          break;
        case 'addFile': {
          const editor = vscode.window.activeTextEditor;
          if (editor) {
            const filePath = vscode.workspace.asRelativePath(editor.document.uri);
            this._view?.webview.postMessage({ type: 'insertText', text: `#file:${filePath} ` });
          }
          break;
        }
        case 'addSelection': {
          const editor = vscode.window.activeTextEditor;
          if (editor) {
            const filePath = vscode.workspace.asRelativePath(editor.document.uri);
            if (!editor.selection.isEmpty) {
              const start = editor.selection.start.line + 1;
              const end = editor.selection.end.line + 1;
              this._view?.webview.postMessage({ type: 'insertText', text: `#file:${filePath}:${start}-${end} ` });
            } else {
              this._view?.webview.postMessage({ type: 'insertText', text: `#file:${filePath} ` });
            }
          }
          break;
        }
        case 'fetchModels':
          this._fetchModelsForTool(msg.tool);
          break;
        case 'agentChanged':
          if (typeof msg.agentId === 'string') {
            this._selectedAgentId = msg.agentId;
          }
          break;
        case 'modelChanged':
          if (typeof msg.model === 'string') {
            this._selectedModel = msg.model || undefined;
            void this._context.globalState.update('selectedModel', this._selectedModel);
          }
          break;
      }
    });

    webviewView.webview.html = this._getHtml(webviewView.webview);

    // 非表示（タブ切替等）でJSコンテキストが破棄されるため、再表示時にHTMLを再生成して
    // チャット履歴・進行中ストリームを復元する
    webviewView.onDidChangeVisibility(() => {
      if (webviewView.visible && this._view) {
        this._view.webview.html = this._getHtml(this._view.webview);
      }
    });

    webviewView.onDidDispose(() => {
      this._killCurrentProcess();
      this._view = undefined;
    });
  }

  private _runCommand(agentId: string, prompt: string, model?: string): void {
    if (!this._view) {
      return;
    }

    this._killCurrentProcess();

    const agent = this._agents.find((a) => a.id === agentId);
    if (!agent) {
      this._view.webview.postMessage({ type: 'error', text: `エージェント "${agentId}" が見つかりません\n` });
      this._view.webview.postMessage({ type: 'done', code: 1 });
      return;
    }

    this._chatHistory.push({ role: 'user', content: prompt });
    this._currentStdout = '';
    this._currentStderr = '';

    const workspacePath = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    const expandedPrompt = expandFileRefs(prompt, workspacePath);
    const sessionId = this._agentSessions.get(agentId);
    const config = buildCommand(agent, expandedPrompt, workspacePath, model, sessionId);

    this._currentProcess = runCommand(
      config,
      (data) => {
        this._currentStdout += data;
        this._view?.webview.postMessage({ type: 'data', text: data });
      },
      (data) => {
        this._currentStderr += data;
        this._view?.webview.postMessage({ type: 'error', text: data });
      },
      (code, newSessionId) => {
        if (newSessionId) {
          // claude: stream-json から取得した実際のセッション ID を保存する
          this._agentSessions.set(agentId, newSessionId);
        } else if (agent.tool === 'kiro-cli' && code === 0 && !this._agentSessions.has(agentId)) {
          // kiro-cli: 正常終了時のみ --resume マーカーをセットする。
          // code が null（スポーン失敗）や非ゼロ（エラー終了）の場合はセッションが作成されていないためスキップする。
          this._agentSessions.set(agentId, '__resume__');
        }
        this._chatHistory.push({ role: 'assistant', stdout: this._currentStdout, stderr: this._currentStderr });
        if (this._chatHistory.length > 100) {
          this._chatHistory = this._chatHistory.slice(-100);
        }
        void this._context.globalState.update('chatHistory', this._chatHistory);
        this._currentProcess = undefined;
        this._view?.webview.postMessage({ type: 'done', code });
      }
    );
  }

  private _killCurrentProcess(): void {
    if (this._currentProcess) {
      this._currentProcess.kill();
      this._currentProcess = undefined;
    }
  }

  private _fetchModelsForTool(tool: string): void {
    if (!this._view) { return; }
    const webview = this._view.webview;
    const fetch = tool === 'kiro-cli' ? fetchKiroModels : fetchClaudeModels;
    fetch().then((models) => {
      if (tool === 'kiro-cli') {
        this._kiroModels = models;
      } else {
        this._claudeModels = models;
      }
      webview.postMessage({ type: 'toolModels', toolModels: { [tool]: models } });
    }).catch(() => {
      const fallback = tool === 'kiro-cli' ? FALLBACK_KIRO_MODELS : FALLBACK_CLAUDE_MODELS;
      if (tool === 'kiro-cli') {
        this._kiroModels = fallback;
      } else {
        this._claudeModels = fallback;
      }
      webview.postMessage({ type: 'toolModels', toolModels: { [tool]: fallback } });
    });
  }

  private _syncConfig(): void {
    if (this._onSync) {
      this._onSync();
      this._view?.webview.postMessage({ type: 'syncDone' });
    }
  }

  private _getHtml(webview: vscode.Webview): string {
    const nonce = getNonce();
    // JSON.stringify は '<' をエスケープしないため、チャット履歴に "</script>" が含まれると
    // <script type="application/json"> タグが途中で閉じられHTMLが破壊される。
    // \u003c にエスケープすることで HTML パーサーに誤認されないようにする。
    const chatData = JSON.stringify({
      history: this._chatHistory,
      toolModels: { claude: this._claudeModels, 'kiro-cli': this._kiroModels },
      selectedModel: this._selectedModel ?? '',
      // 処理中に WebView が非表示になった場合、再表示時に進行中のストリームを復元するために使用
      pending: this._currentProcess
        ? { stdout: this._currentStdout, stderr: this._currentStderr }
        : null,
    }).replace(/</g, '\\u003c');
    const selectedAgentId = this._selectedAgentId;
    const agentOptionsHtml = this._agents
      .map((agent, index) => {
        const id = escapeHtmlAttribute(agent.id);
        const name = escapeHtmlText(agent.name);
        const description = escapeHtmlAttribute(agent.description ?? '');
        const tool = escapeHtmlAttribute(agent.tool);
        const shouldSelect = selectedAgentId
          ? agent.id === selectedAgentId
          : index === 0;
        const selectedAttr = shouldSelect ? ' selected' : '';
        return `<option value="${id}" title="${description}" data-tool="${tool}"${selectedAttr}>${name}</option>`;
      })
      .join('\n      ');

    const htmlPath = path.join(__dirname, '..', 'media', 'chat.html');
    let html = fs.readFileSync(htmlPath, 'utf8');
    html = html
      .replace(/__NONCE__/g, nonce)
      .replace('__AGENT_OPTIONS_HTML__', () => agentOptionsHtml)
      .replace('__CHAT_DATA_JSON__', () => chatData);
    return html;
  }
}

function getNonce(): string {
  return crypto.randomBytes(16).toString('base64');
}

function escapeHtmlText(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function escapeHtmlAttribute(value: string): string {
  return escapeHtmlText(value)
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/**
 * プロンプト内の #file:path[:start-end] 参照をファイル内容に展開する。
 * 読み込みに失敗した参照はそのまま残す。
 */
function expandFileRefs(prompt: string, workspacePath: string | undefined): string {
  return prompt.replace(/#file:([^\s]+)/g, (_match, ref) => {
    const lineMatch = ref.match(/^(.+):(\d+)(?:-(\d+))?$/);
    let filePath: string;
    let startLine: number | undefined;
    let endLine: number | undefined;

    if (lineMatch) {
      filePath = lineMatch[1] as string;
      startLine = parseInt(lineMatch[2], 10);
      endLine = lineMatch[3] ? parseInt(lineMatch[3], 10) : startLine;
    } else {
      filePath = ref as string;
    }

    // 絶対パスは workspacePath を無視して任意ファイルを読めるため拒否する。
    // 相対パスの場合も workspace 外へのトラバーサル（../..）を防ぐ。
    if (path.isAbsolute(filePath)) {
      return _match;
    }

    const fullPath = workspacePath ? path.resolve(workspacePath, filePath) : filePath;

    if (workspacePath) {
      const normalizedRoot = path.normalize(workspacePath);
      const normalizedFull = path.normalize(fullPath);
      if (!normalizedFull.startsWith(normalizedRoot + path.sep) && normalizedFull !== normalizedRoot) {
        return _match;
      }
    }

    let content: string;
    try {
      content = fs.readFileSync(fullPath, 'utf8');
    } catch {
      return _match;
    }

    if (startLine !== undefined && endLine !== undefined) {
      const lines = content.split('\n').slice(startLine - 1, endLine);
      return `\`\`\`\n// ${filePath} (lines ${startLine}-${endLine})\n${lines.join('\n')}\n\`\`\``;
    }
    return `\`\`\`\n// ${filePath}\n${content}\n\`\`\``;
  });
}
