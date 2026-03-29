import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import { AgentConfig } from './agentConfig';
import { buildCommand, runCommand } from './commandRunner';
import { fetchClaudeModels, FALLBACK_CLAUDE_MODELS } from './modelFetcher';

export class ChatViewProvider implements vscode.WebviewViewProvider {
  public static readonly viewId = 'commandExecutor.chatView';

  private _view?: vscode.WebviewView;
  private _currentProcess?: cp.ChildProcess;
  private _agents: AgentConfig[];
  private _claudeModels: string[] = FALLBACK_CLAUDE_MODELS;

  constructor(
    private readonly _context: vscode.ExtensionContext,
    agents: AgentConfig[],
    private readonly _onSync?: () => void
  ) {
    this._agents = agents;
  }

  /** エージェント一覧を更新して WebView を再描画する */
  updateAgents(agents: AgentConfig[]): void {
    this._agents = agents;
    if (this._view) {
      this._view.webview.html = this._getHtml(this._view.webview);
    }
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
      }
    });

    webviewView.webview.html = this._getHtml(webviewView.webview);

    // モデル一覧を非同期取得して webview に反映
    fetchClaudeModels().then((models) => {
      this._claudeModels = models;
      webviewView.webview.postMessage({ type: 'models', models });
    });

    webviewView.onDidChangeVisibility(() => {
      if (webviewView.visible) {
        webviewView.webview.html = this._getHtml(webviewView.webview);
        // 可視化時にも最新モデルを再送信
        fetchClaudeModels().then((models) => {
          this._claudeModels = models;
          webviewView.webview.postMessage({ type: 'models', models });
        });
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

    const workspacePath = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    const expandedPrompt = expandFileRefs(prompt, workspacePath);
    const config = buildCommand(agent, expandedPrompt, workspacePath, model);

    this._currentProcess = runCommand(
      config,
      (data) => this._view?.webview.postMessage({ type: 'data', text: data }),
      (data) => this._view?.webview.postMessage({ type: 'error', text: data }),
      (code) => {
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

  private _syncConfig(): void {
    if (this._onSync) {
      this._onSync();
      this._view?.webview.postMessage({ type: 'syncDone' });
    }
  }

  private _getHtml(webview: vscode.Webview): string {
    const nonce = getNonce();
    const modelOptionsHtml = ['', ...this._claudeModels]
      .map((m) => {
        const val = escapeHtmlAttribute(m);
        const label = m ? escapeHtmlText(m) : 'Default';
        return `<option value="${val}">${label}</option>`;
      })
      .join('\n      ');
    const agentOptionsHtml = this._agents
      .map((agent) => {
        const id = escapeHtmlAttribute(agent.id);
        const name = escapeHtmlText(agent.name);
        const description = escapeHtmlAttribute(agent.description ?? '');
        const tool = escapeHtmlAttribute(agent.tool);
        return `<option value="${id}" title="${description}" data-tool="${tool}">${name}</option>`;
      })
      .join('\n      ');

    return /* html */ `<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy"
        content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: var(--vscode-font-family);
      font-size: var(--vscode-font-size);
      background: var(--vscode-sideBar-background);
      color: var(--vscode-sideBar-foreground);
      display: flex;
      flex-direction: column;
      height: 100vh;
      overflow: hidden;
    }

    /* ツールバー */
    #toolbar {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 6px 8px;
      border-bottom: 1px solid var(--vscode-panel-border);
      flex-shrink: 0;
    }

    #toolbar label {
      font-size: 0.85em;
      color: var(--vscode-descriptionForeground);
      white-space: nowrap;
    }

    #agent-select, #model-select {
      flex: 1;
      background: var(--vscode-dropdown-background);
      color: var(--vscode-dropdown-foreground);
      border: 1px solid var(--vscode-dropdown-border);
      padding: 3px 6px;
      border-radius: 2px;
      font-size: 0.9em;
    }

    #model-row {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 4px 8px;
      border-bottom: 1px solid var(--vscode-panel-border);
      flex-shrink: 0;
    }

    #model-row label {
      font-size: 0.85em;
      color: var(--vscode-descriptionForeground);
      white-space: nowrap;
    }

    #model-row.hidden { display: none; }

    #clear-btn, #stop-btn, #sync-btn {
      background: var(--vscode-button-secondaryBackground);
      color: var(--vscode-button-secondaryForeground);
      border: none;
      padding: 3px 8px;
      border-radius: 2px;
      cursor: pointer;
      font-size: 0.85em;
      white-space: nowrap;
    }

    #clear-btn:hover, #stop-btn:hover, #sync-btn:hover {
      background: var(--vscode-button-secondaryHoverBackground);
    }

    #sync-btn:disabled { opacity: 0.5; cursor: not-allowed; }

    #stop-btn { display: none; }
    #stop-btn.visible { display: block; }

    /* エージェント説明 */
    #agent-description {
      padding: 4px 8px;
      font-size: 0.8em;
      color: var(--vscode-descriptionForeground);
      border-bottom: 1px solid var(--vscode-panel-border);
      min-height: 22px;
      flex-shrink: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    /* メッセージ一覧 */
    #messages {
      flex: 1;
      overflow-y: auto;
      padding: 8px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .message {
      padding: 8px 10px;
      border-radius: 4px;
      word-break: break-word;
      line-height: 1.5;
    }

    .message.user {
      background: var(--vscode-inputOption-activeBackground);
      border-left: 3px solid var(--vscode-focusBorder);
      font-size: 0.9em;
      color: var(--vscode-input-foreground);
    }

    .message.assistant {
      background: var(--vscode-editor-inactiveSelectionBackground);
      white-space: pre-wrap;
      font-family: var(--vscode-editor-font-family);
      font-size: var(--vscode-editor-font-size);
    }

    .message.assistant .stderr {
      color: var(--vscode-errorForeground);
    }

    .message.system {
      color: var(--vscode-descriptionForeground);
      font-style: italic;
      font-size: 0.85em;
    }

    /* 実行中インジケーター */
    .running-indicator {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      color: var(--vscode-descriptionForeground);
      font-style: italic;
      font-size: 0.85em;
    }

    .spinner {
      width: 12px;
      height: 12px;
      border: 2px solid var(--vscode-descriptionForeground);
      border-top-color: transparent;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
      flex-shrink: 0;
    }

    @keyframes spin { to { transform: rotate(360deg); } }

    /* 入力エリア */
    #input-area {
      display: flex;
      flex-direction: column;
      gap: 6px;
      padding: 8px;
      border-top: 1px solid var(--vscode-panel-border);
      flex-shrink: 0;
    }

    #prompt-input {
      width: 100%;
      background: var(--vscode-input-background);
      color: var(--vscode-input-foreground);
      border: 1px solid var(--vscode-input-border, transparent);
      padding: 6px 8px;
      border-radius: 2px;
      resize: vertical;
      min-height: 72px;
      font-family: var(--vscode-font-family);
      font-size: var(--vscode-font-size);
      line-height: 1.4;
    }

    #prompt-input:focus {
      outline: none;
      border-color: var(--vscode-focusBorder);
    }

    #input-footer {
      display: flex;
      justify-content: space-between;
      align-items: center;
    }

    #hint {
      font-size: 0.78em;
      color: var(--vscode-descriptionForeground);
    }

    #send-btn {
      background: var(--vscode-button-background);
      color: var(--vscode-button-foreground);
      border: none;
      padding: 5px 14px;
      border-radius: 2px;
      cursor: pointer;
      font-size: 0.9em;
    }

    #send-btn:hover { background: var(--vscode-button-hoverBackground); }
    #send-btn:disabled { opacity: 0.5; cursor: not-allowed; }

    /* ファイル参照ボタン */
    #file-ref-bar {
      display: flex;
      gap: 4px;
    }

    #add-file-btn, #add-selection-btn {
      background: var(--vscode-button-secondaryBackground);
      color: var(--vscode-button-secondaryForeground);
      border: none;
      padding: 2px 8px;
      border-radius: 2px;
      cursor: pointer;
      font-size: 0.8em;
      white-space: nowrap;
    }

    #add-file-btn:hover, #add-selection-btn:hover {
      background: var(--vscode-button-secondaryHoverBackground);
    }
  </style>
</head>
<body>
  <div id="toolbar">
    <label for="agent-select">Agent:</label>
    <select id="agent-select">
      ${agentOptionsHtml}
    </select>
    <button id="stop-btn" title="実行中のコマンドを停止">Stop</button>
    <button id="clear-btn" title="メッセージをクリア">Clear</button>
    <button id="sync-btn" title="~/.copilot/ を各 CLI ホームへ同期">Sync</button>
  </div>

  <div id="model-row" class="hidden">
    <label for="model-select">Model:</label>
    <select id="model-select">
      ${modelOptionsHtml}
    </select>
  </div>

  <div id="agent-description"></div>

  <div id="messages"></div>

  <div id="input-area">
    <div id="file-ref-bar">
      <button id="add-file-btn" title="アクティブなファイルを参照として追加 (#file:path)">+ File</button>
      <button id="add-selection-btn" title="選択中の行を参照として追加 (#file:path:start-end)">+ Selection</button>
    </div>
    <textarea id="prompt-input" placeholder="プロンプトを入力 (Ctrl+Enter で送信)&#10;#file:path や #file:path:10-20 でファイル参照を挿入できます"></textarea>
    <div id="input-footer">
      <span id="hint">Ctrl+Enter で送信</span>
      <button id="send-btn">Send</button>
    </div>
  </div>

  <script nonce="${nonce}">
    (function() {
      const vscode = acquireVsCodeApi();
      const messagesEl = document.getElementById('messages');
      const promptInput = document.getElementById('prompt-input');
      const sendBtn = document.getElementById('send-btn');
      const stopBtn = document.getElementById('stop-btn');
      const clearBtn = document.getElementById('clear-btn');
      const agentSelect = document.getElementById('agent-select');
      const agentDescription = document.getElementById('agent-description');
      const syncBtn = document.getElementById('sync-btn');
      const addFileBtn = document.getElementById('add-file-btn');
      const addSelectionBtn = document.getElementById('add-selection-btn');
      const modelRow = document.getElementById('model-row');
      const modelSelect = document.getElementById('model-select');

      if (!messagesEl || !promptInput || !sendBtn || !stopBtn || !clearBtn || !agentSelect || !agentDescription || !syncBtn) {
        return;
      }

      /** モデル選択をサポートするツールかどうかを判定して表示/非表示 */
      function updateModelRowVisibility() {
        const selected = agentSelect.options[agentSelect.selectedIndex];
        const tool = selected ? selected.getAttribute('data-tool') : '';
        const supportsModel = tool === 'claude' || tool === 'kiro-cli';
        if (modelRow) { modelRow.classList.toggle('hidden', !supportsModel); }
      }

      updateModelRowVisibility();
      agentSelect.addEventListener('change', updateModelRowVisibility);

      let currentAssistantEl = null;
      let currentOutputEl = null;
      let running = false;

      function scrollToBottom() {
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }

      function setRunning(val) {
        running = val;
        sendBtn.disabled = val;
        stopBtn.classList.toggle('visible', val);
      }

      function updateDescription() {
        const selected = agentSelect.options[agentSelect.selectedIndex];
        agentDescription.textContent = selected ? (selected.title || '') : '';
      }

      updateDescription();
      agentSelect.addEventListener('change', updateDescription);

      sendBtn.addEventListener('click', function() {
        const prompt = promptInput.value.trim();
        const agentId = agentSelect.value;
        if (!prompt || !agentId || running) { return; }

        const div = document.createElement('div');
        div.className = 'message user';
        div.textContent = prompt;
        messagesEl.appendChild(div);
        scrollToBottom();

        setRunning(true);
        currentAssistantEl = document.createElement('div');
        currentAssistantEl.className = 'message assistant';

        const indicator = document.createElement('div');
        indicator.className = 'running-indicator';
        indicator.innerHTML = '<div class="spinner"></div><span>実行中...</span>';
        currentAssistantEl.appendChild(indicator);

        currentOutputEl = document.createElement('span');
        currentAssistantEl.appendChild(currentOutputEl);
        messagesEl.appendChild(currentAssistantEl);
        scrollToBottom();

        promptInput.value = '';
        const model = modelSelect ? modelSelect.value : '';
        vscode.postMessage({ type: 'run', agentId: agentId, prompt: prompt, model: model || undefined });
      });

      promptInput.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
          e.preventDefault();
          sendBtn.click();
        }
      });

      stopBtn.addEventListener('click', function() {
        vscode.postMessage({ type: 'kill' });
        setRunning(false);
        currentAssistantEl = null;
        currentOutputEl = null;
      });

      clearBtn.addEventListener('click', function() {
        messagesEl.innerHTML = '';
      });

      syncBtn.addEventListener('click', function() {
        syncBtn.disabled = true;
        syncBtn.textContent = 'Syncing...';
        vscode.postMessage({ type: 'sync' });
      });

      if (addFileBtn) {
        addFileBtn.addEventListener('click', function() {
          vscode.postMessage({ type: 'addFile' });
        });
      }

      if (addSelectionBtn) {
        addSelectionBtn.addEventListener('click', function() {
          vscode.postMessage({ type: 'addSelection' });
        });
      }

      window.addEventListener('message', function(event) {
        const msg = event.data || {};

        switch (msg.type) {
          case 'data':
            if (currentAssistantEl && currentOutputEl) {
              const indicator = currentAssistantEl.querySelector('.running-indicator');
              if (indicator) { indicator.remove(); }
              currentOutputEl.textContent += msg.text;
              scrollToBottom();
            }
            break;

          case 'error':
            if (currentAssistantEl) {
              const indicator = currentAssistantEl.querySelector('.running-indicator');
              if (indicator) { indicator.remove(); }
              const err = document.createElement('div');
              err.className = 'stderr';
              err.textContent = msg.text;
              currentAssistantEl.appendChild(err);
              scrollToBottom();
            }
            break;

          case 'done':
            setRunning(false);
            currentAssistantEl = null;
            currentOutputEl = null;
            break;

          case 'syncDone':
            syncBtn.disabled = false;
            syncBtn.textContent = 'Sync';
            break;

          case 'models': {
            if (modelSelect && Array.isArray(msg.models)) {
              const prev = modelSelect.value;
              modelSelect.innerHTML = '';
              const defaultOpt = document.createElement('option');
              defaultOpt.value = '';
              defaultOpt.textContent = 'Default';
              modelSelect.appendChild(defaultOpt);
              for (const id of msg.models) {
                const opt = document.createElement('option');
                opt.value = id;
                opt.textContent = id;
                modelSelect.appendChild(opt);
              }
              // 以前の選択を維持（存在する場合）
              if (prev && msg.models.includes(prev)) {
                modelSelect.value = prev;
              }
            }
            break;
          }

          case 'insertText': {
            const pos = promptInput.selectionStart;
            const before = promptInput.value.slice(0, pos);
            const after = promptInput.value.slice(promptInput.selectionEnd);
            promptInput.value = before + msg.text + after;
            const newPos = pos + msg.text.length;
            promptInput.selectionStart = promptInput.selectionEnd = newPos;
            promptInput.focus();
            break;
          }
        }
      });
    })();
  </script>
</body>
</html>`;
  }
}

function getNonce(): string {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  let nonce = '';
  for (let i = 0; i < 32; i++) {
    nonce += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return nonce;
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

    const fullPath = workspacePath ? path.resolve(workspacePath, filePath) : filePath;

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
