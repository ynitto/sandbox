import * as vscode from 'vscode';
import {
  createServer,
  IncomingMessage,
  Server,
  ServerResponse,
} from 'http';

const CONFIG_SECTION = 'copilotChatBridge';
const DEFAULT_PORT = 52719;
const DEFAULT_VENDOR = 'copilot';
const DEFAULT_REQUEST_TIMEOUT_MS = 120_000;
const MAX_REQUEST_TIMEOUT_MS = 600_000;

interface AskBody {
  prompt: string;
  system?: string;
  vendor?: string;
  family?: string;
  modelId?: string;
  timeoutMs?: number;
  justification?: string;
}

interface AskWithContextBody extends AskBody {
  files?: string[];
  useActiveSelection?: boolean;
  useActiveEditor?: boolean;
}

interface OpenChatBody {
  query?: string;
  isPartialQuery?: boolean;
  mode?: 'ask' | 'edit' | 'agent';
}

interface ModelSelector {
  modelId?: string;
  vendor?: string;
  family?: string;
}

interface DefaultModelConfig {
  vendor: string;
  family: string | undefined;
}

interface SerializedModel {
  id: string;
  name: string;
  vendor: string;
  family: string;
  version: string;
  maxInputTokens: number;
}

type RouteHandler = (
  req: IncomingMessage,
  res: ServerResponse,
  url: URL,
) => Promise<void>;

class CopilotChatBridge {
  private readonly channel: vscode.OutputChannel;
  private readonly port: number;
  private readonly defaultModel: DefaultModelConfig;
  private readonly defaultTimeoutMs: number;
  private server: Server | undefined;
  private readonly routes: Array<{
    method: string;
    pathname: string;
    handler: RouteHandler;
  }>;

  constructor(
    channel: vscode.OutputChannel,
    port: number,
    defaultModel: DefaultModelConfig,
    defaultTimeoutMs: number,
  ) {
    this.channel = channel;
    this.port = port;
    this.defaultModel = defaultModel;
    this.defaultTimeoutMs = defaultTimeoutMs;
    this.routes = [
      { method: 'GET', pathname: '/api/health', handler: this.handleHealth },
      { method: 'GET', pathname: '/api/models', handler: this.handleListModels },
      { method: 'POST', pathname: '/api/ask', handler: this.handleAsk },
      {
        method: 'POST',
        pathname: '/api/ask-with-context',
        handler: this.handleAskWithContext,
      },
      { method: 'POST', pathname: '/api/open', handler: this.handleOpenChat },
      { method: 'POST', pathname: '/api/new-session', handler: this.handleNewSession },
    ];
  }

  start(): void {
    const server = createServer((req, res) => {
      this.dispatch(req, res).catch((err) => {
        this.channel.appendLine(`[http] unhandled error: ${err}`);
        this.respondJson(res, 500, { error: String(err) });
      });
    });
    server.listen(this.port, '127.0.0.1', () => {
      this.channel.appendLine(
        `[bridge] listening on http://127.0.0.1:${this.port}`,
      );
    });
    server.on('error', (err) => {
      this.channel.appendLine(`[bridge] server error: ${err}`);
    });
    this.server = server;
  }

  stop(): void {
    this.server?.close();
    this.server = undefined;
  }

  private async dispatch(
    req: IncomingMessage,
    res: ServerResponse,
  ): Promise<void> {
    const url = new URL(req.url ?? '/', `http://127.0.0.1:${this.port}`);
    for (const route of this.routes) {
      if (route.method === req.method && route.pathname === url.pathname) {
        await route.handler.call(this, req, res, url);
        return;
      }
    }
    this.respondJson(res, 404, { error: 'route not found', path: url.pathname });
  }

  // ------- handlers -------

  private handleHealth: RouteHandler = async (_req, res) => {
    let modelCount = 0;
    try {
      const models = await vscode.lm.selectChatModels();
      modelCount = models.length;
    } catch (err) {
      this.channel.appendLine(`[health] selectChatModels failed: ${err}`);
    }
    this.respondJson(res, 200, {
      status: 'ok',
      defaultVendor: this.defaultModel.vendor,
      defaultFamily: this.defaultModel.family ?? null,
      availableModels: modelCount,
    });
  };

  private handleListModels: RouteHandler = async (_req, res, url) => {
    const vendor = url.searchParams.get('vendor') ?? undefined;
    const family = url.searchParams.get('family') ?? undefined;
    const selector: vscode.LanguageModelChatSelector = {};
    if (vendor) {
      selector.vendor = vendor;
    }
    if (family) {
      selector.family = family;
    }
    const models = await vscode.lm.selectChatModels(selector);
    this.respondJson(res, 200, models.map(serializeModel));
  };

  private handleAsk: RouteHandler = async (req, res) => {
    const body = await this.readJson<AskBody>(req);
    if (!body.prompt) {
      this.respondJson(res, 400, { error: '"prompt" is required' });
      return;
    }
    const model = await this.resolveModel(body);
    if (!model) {
      this.respondJson(res, 404, this.modelNotFoundDetail(body));
      return;
    }
    const messages = this.buildMessages(body.prompt, body.system);
    await this.runLmRequest(req, res, model, messages, body);
  };

  private handleAskWithContext: RouteHandler = async (req, res) => {
    const body = await this.readJson<AskWithContextBody>(req);
    if (!body.prompt) {
      this.respondJson(res, 400, { error: '"prompt" is required' });
      return;
    }
    const model = await this.resolveModel(body);
    if (!model) {
      this.respondJson(res, 404, this.modelNotFoundDetail(body));
      return;
    }
    let contextText: string;
    try {
      contextText = await this.collectContext(body);
    } catch (err) {
      this.respondJson(res, 400, { error: String(err) });
      return;
    }
    const fullPrompt = contextText
      ? `${contextText}\n\n---\n\n${body.prompt}`
      : body.prompt;
    const messages = this.buildMessages(fullPrompt, body.system);
    await this.runLmRequest(req, res, model, messages, body);
  };

  private handleOpenChat: RouteHandler = async (req, res) => {
    const body = await this.readJson<OpenChatBody>(req);
    const arg: {
      query?: string;
      isPartialQuery?: boolean;
      mode?: string;
    } = {};
    if (typeof body.query === 'string') {
      arg.query = body.query;
    }
    if (typeof body.isPartialQuery === 'boolean') {
      arg.isPartialQuery = body.isPartialQuery;
    }
    if (body.mode) {
      arg.mode = body.mode;
    }
    try {
      await vscode.commands.executeCommand('workbench.action.chat.open', arg);
      this.respondJson(res, 200, { success: true });
    } catch (err) {
      this.respondJson(res, 500, {
        error: 'workbench.action.chat.open failed',
        detail: String(err),
      });
    }
  };

  private handleNewSession: RouteHandler = async (_req, res) => {
    // Different VS Code builds expose different commands. Try the modern one
    // first, fall back to the older alias, then return whatever errors out.
    const candidates = [
      'workbench.action.chat.newChat',
      'workbench.action.chat.clear',
    ];
    const errors: string[] = [];
    for (const command of candidates) {
      try {
        await vscode.commands.executeCommand(command);
        this.respondJson(res, 200, { success: true, executed: command });
        return;
      } catch (err) {
        errors.push(`${command}: ${err}`);
      }
    }
    this.respondJson(res, 500, {
      error: 'no compatible new-chat command succeeded',
      attempted: errors,
    });
  };

  // ------- LM execution -------

  private async runLmRequest(
    req: IncomingMessage,
    res: ServerResponse,
    model: vscode.LanguageModelChat,
    messages: vscode.LanguageModelChatMessage[],
    body: AskBody,
  ): Promise<void> {
    const cancellation = new vscode.CancellationTokenSource();
    req.on('close', () => cancellation.cancel());
    const timeoutMs = clamp(
      body.timeoutMs ?? this.defaultTimeoutMs,
      1,
      MAX_REQUEST_TIMEOUT_MS,
    );
    const timer = setTimeout(() => cancellation.cancel(), timeoutMs);

    try {
      const options: vscode.LanguageModelChatRequestOptions = {};
      if (body.justification) {
        options.justification = body.justification;
      }
      const response = await model.sendRequest(
        messages,
        options,
        cancellation.token,
      );
      let collected = '';
      for await (const fragment of response.text) {
        collected += fragment;
      }
      this.respondJson(res, 200, {
        text: collected,
        model: serializeModel(model),
        timedOut: cancellation.token.isCancellationRequested,
      });
    } catch (err) {
      this.respondJson(res, 502, this.serializeLmError(err));
    } finally {
      clearTimeout(timer);
      cancellation.dispose();
    }
  }

  private buildMessages(
    prompt: string,
    system: string | undefined,
  ): vscode.LanguageModelChatMessage[] {
    const messages: vscode.LanguageModelChatMessage[] = [];
    // Vendors that ignore non-user messages (e.g. Copilot) still parse the
    // resulting content as user text; that's harmless. Prepending a tagged
    // "system" prelude keeps the instruction visible to all providers.
    if (system) {
      messages.push(
        vscode.LanguageModelChatMessage.User(
          `[system instructions]\n${system}`,
        ),
      );
    }
    messages.push(vscode.LanguageModelChatMessage.User(prompt));
    return messages;
  }

  private async resolveModel(
    selector: ModelSelector,
  ): Promise<vscode.LanguageModelChat | undefined> {
    if (selector.modelId) {
      const all = await vscode.lm.selectChatModels();
      return all.find((m) => m.id === selector.modelId);
    }
    const lmSelector: vscode.LanguageModelChatSelector = {};
    const vendor = selector.vendor ?? this.defaultModel.vendor;
    const family = selector.family ?? this.defaultModel.family;
    if (vendor) {
      lmSelector.vendor = vendor;
    }
    if (family) {
      lmSelector.family = family;
    }
    const matches = await vscode.lm.selectChatModels(lmSelector);
    if (matches.length > 0) {
      return matches[0];
    }
    // Fallback: drop the vendor constraint so the caller gets *some* model
    // when the requested vendor isn't installed. We only fall back when the
    // caller didn't pin to a specific vendor.
    if (!selector.vendor) {
      const fallback = await vscode.lm.selectChatModels({});
      return fallback[0];
    }
    return undefined;
  }

  private modelNotFoundDetail(selector: ModelSelector): Record<string, unknown> {
    return {
      error: 'no matching chat model is available',
      hint:
        'Run /api/models (or list_chat_models) to see what is registered. '
        + 'Vendor "copilot" requires an active GitHub Copilot subscription '
        + 'and that the user has granted this extension consent to use it.',
      requested: {
        modelId: selector.modelId ?? null,
        vendor: selector.vendor ?? this.defaultModel.vendor,
        family: selector.family ?? this.defaultModel.family ?? null,
      },
    };
  }

  private async collectContext(body: AskWithContextBody): Promise<string> {
    const parts: string[] = [];

    if (body.useActiveSelection) {
      const editor = vscode.window.activeTextEditor;
      if (editor && !editor.selection.isEmpty) {
        const text = editor.document.getText(editor.selection);
        const lang = editor.document.languageId;
        parts.push(
          `## Active selection (${editor.document.uri.fsPath})\n` +
            `\`\`\`${lang}\n${text}\n\`\`\``,
        );
      }
    }

    if (body.useActiveEditor) {
      const editor = vscode.window.activeTextEditor;
      if (editor) {
        const lang = editor.document.languageId;
        parts.push(
          `## Active editor (${editor.document.uri.fsPath})\n` +
            `\`\`\`${lang}\n${editor.document.getText()}\n\`\`\``,
        );
      }
    }

    if (body.files && body.files.length > 0) {
      for (const file of body.files) {
        const uri = await this.resolveFile(file);
        const document = await vscode.workspace.openTextDocument(uri);
        parts.push(
          `## File (${uri.fsPath})\n` +
            `\`\`\`${document.languageId}\n${document.getText()}\n\`\`\``,
        );
      }
    }

    return parts.join('\n\n');
  }

  private async resolveFile(rawPath: string): Promise<vscode.Uri> {
    if (rawPath.startsWith('file://')) {
      return vscode.Uri.parse(rawPath);
    }
    if (
      rawPath.startsWith('/') ||
      /^[A-Za-z]:[\\/]/.test(rawPath) // Windows absolute (C:\ or C:/)
    ) {
      return vscode.Uri.file(rawPath);
    }
    // Resolve relative to the first workspace folder.
    const folders = vscode.workspace.workspaceFolders;
    if (!folders || folders.length === 0) {
      throw new Error(
        `cannot resolve relative path "${rawPath}" without an open workspace`,
      );
    }
    return vscode.Uri.joinPath(folders[0].uri, rawPath);
  }

  private serializeLmError(err: unknown): Record<string, unknown> {
    if (err instanceof vscode.LanguageModelError) {
      return {
        error: 'language model request failed',
        code: err.code,
        message: err.message,
      };
    }
    if (err instanceof vscode.CancellationError) {
      return {
        error: 'language model request cancelled (timed out or aborted)',
      };
    }
    return {
      error: 'language model request failed',
      detail: String(err),
    };
  }

  // ------- support helpers -------

  private readJson<T>(req: IncomingMessage): Promise<T> {
    return new Promise((resolve, reject) => {
      const chunks: Buffer[] = [];
      req.on('data', (chunk: Buffer) => chunks.push(chunk));
      req.on('end', () => {
        const raw = Buffer.concat(chunks).toString('utf8');
        if (!raw) {
          resolve({} as T);
          return;
        }
        try {
          resolve(JSON.parse(raw) as T);
        } catch (err) {
          reject(new Error(`invalid JSON body: ${err}`));
        }
      });
      req.on('error', reject);
    });
  }

  private respondJson(res: ServerResponse, status: number, body: unknown): void {
    res.statusCode = status;
    res.setHeader('Content-Type', 'application/json; charset=utf-8');
    res.end(JSON.stringify(body));
  }
}

function serializeModel(model: vscode.LanguageModelChat): SerializedModel {
  return {
    id: model.id,
    name: model.name,
    vendor: model.vendor,
    family: model.family,
    version: model.version,
    maxInputTokens: model.maxInputTokens,
  };
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

let bridge: CopilotChatBridge | undefined;

export function activate(context: vscode.ExtensionContext): void {
  const channel = vscode.window.createOutputChannel('Copilot Chat Bridge');
  const config = vscode.workspace.getConfiguration(CONFIG_SECTION);
  const port = config.get<number>('port') ?? DEFAULT_PORT;
  const vendor = config.get<string>('defaultVendor') ?? DEFAULT_VENDOR;
  const familyRaw = config.get<string>('defaultFamily') ?? '';
  const family = familyRaw.trim() === '' ? undefined : familyRaw.trim();
  const timeout =
    config.get<number>('requestTimeoutMs') ?? DEFAULT_REQUEST_TIMEOUT_MS;

  bridge = new CopilotChatBridge(
    channel,
    port,
    { vendor, family },
    timeout,
  );
  bridge.start();

  context.subscriptions.push(channel);
  context.subscriptions.push({ dispose: () => bridge?.stop() });

  context.subscriptions.push(
    vscode.commands.registerCommand('copilotChatBridge.showStatus', () => {
      channel.appendLine(
        `[status] port=${port} defaultVendor=${vendor} ` +
          `defaultFamily=${family ?? '(any)'} timeoutMs=${timeout}`,
      );
      channel.show(true);
    }),
    vscode.commands.registerCommand(
      'copilotChatBridge.listModels',
      async () => {
        const models = await vscode.lm.selectChatModels();
        channel.appendLine(
          `[models] ${JSON.stringify(models.map(serializeModel), null, 2)}`,
        );
        channel.show(true);
      },
    ),
  );
}

export function deactivate(): void {
  bridge?.stop();
  bridge = undefined;
}
