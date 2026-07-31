import * as vscode from 'vscode';
import {
  createServer,
  IncomingMessage,
  Server,
  ServerResponse,
} from 'http';

const CONFIG_SECTION = 'terminalBridge';
const DEFAULT_PORT = 52718;
const DEFAULT_BUFFER_LINES = 200;
const WAIT_DEFAULT_TIMEOUT_MS = 30_000;
const WAIT_MAX_TIMEOUT_MS = 120_000;
const WAIT_POLL_INTERVAL_MS = 500;

interface TerminalLocator {
  terminalIndex?: number;
  terminalName?: string;
  processId?: number;
}

interface ExecuteBody extends TerminalLocator {
  command: string;
}

interface SendBody extends TerminalLocator {
  text: string;
}

interface CreateBody {
  name?: string;
  command?: string;
  cwd?: string;
}

interface WaitBody extends TerminalLocator {
  pattern: string;
  timeoutMs?: number;
}

class OutputBuffer {
  private readonly store = new Map<string, string[]>();
  private maxLines: number;

  constructor(maxLines: number) {
    this.maxLines = Math.max(1, maxLines);
  }

  setMaxLines(value: number): void {
    this.maxLines = Math.max(1, value);
    for (const [key, lines] of this.store) {
      if (lines.length > this.maxLines) {
        this.store.set(key, lines.slice(-this.maxLines));
      }
    }
  }

  append(terminalName: string, chunk: string): void {
    if (!chunk) {
      return;
    }
    const lines = this.store.get(terminalName) ?? [];
    const incoming = chunk.replace(/\r\n/g, '\n').split('\n');
    if (lines.length > 0 && incoming.length > 0) {
      lines[lines.length - 1] = lines[lines.length - 1] + incoming.shift()!;
    }
    for (const line of incoming) {
      lines.push(line);
    }
    if (lines.length > this.maxLines) {
      lines.splice(0, lines.length - this.maxLines);
    }
    this.store.set(terminalName, lines);
  }

  markCommand(terminalName: string, command: string): number {
    const lines = this.store.get(terminalName) ?? [];
    lines.push(`$ ${command}`);
    if (lines.length > this.maxLines) {
      lines.splice(0, lines.length - this.maxLines);
    }
    this.store.set(terminalName, lines);
    return lines.length;
  }

  lengthOf(terminalName: string): number {
    return (this.store.get(terminalName) ?? []).length;
  }

  read(terminalName: string): string[] {
    return [...(this.store.get(terminalName) ?? [])];
  }

  sliceFrom(terminalName: string, startIndex: number): string[] {
    return (this.store.get(terminalName) ?? []).slice(startIndex);
  }

  knownTerminals(): string[] {
    return Array.from(this.store.keys());
  }
}

type RouteHandler = (
  req: IncomingMessage,
  res: ServerResponse,
  url: URL,
) => Promise<void>;

class TerminalBridge {
  private readonly buffer: OutputBuffer;
  private readonly channel: vscode.OutputChannel;
  private server: Server | undefined;
  private port: number;
  private readonly routes: Array<{
    method: string;
    pathname: string;
    handler: RouteHandler;
  }>;

  constructor(channel: vscode.OutputChannel, port: number, bufferLines: number) {
    this.channel = channel;
    this.port = port;
    this.buffer = new OutputBuffer(bufferLines);
    this.routes = [
      { method: 'GET', pathname: '/api/health', handler: this.handleHealth },
      { method: 'GET', pathname: '/api/terminals', handler: this.handleListTerminals },
      { method: 'GET', pathname: '/api/output', handler: this.handleReadOutput },
      { method: 'POST', pathname: '/api/execute', handler: this.handleExecute },
      { method: 'POST', pathname: '/api/send', handler: this.handleSend },
      { method: 'POST', pathname: '/api/create', handler: this.handleCreate },
      { method: 'POST', pathname: '/api/close', handler: this.handleClose },
      { method: 'POST', pathname: '/api/wait-for-output', handler: this.handleWait },
    ];
  }

  setBufferLines(value: number): void {
    this.buffer.setMaxLines(value);
  }

  registerCapture(context: vscode.ExtensionContext): void {
    const handler = vscode.window.onDidStartTerminalShellExecution;
    if (!handler) {
      this.channel.appendLine(
        '[capture] shell integration API unavailable in this VS Code build; live capture disabled.',
      );
      return;
    }
    context.subscriptions.push(
      handler(async (event) => {
        const terminalName = event.terminal.name;
        const command = event.execution.commandLine?.value ?? '';
        this.buffer.markCommand(terminalName, command);
        this.channel.appendLine(`[capture] ${terminalName}: ${command}`);
        try {
          for await (const chunk of event.execution.read()) {
            this.buffer.append(terminalName, chunk);
          }
        } catch (err) {
          this.channel.appendLine(`[capture] error in ${terminalName}: ${err}`);
        }
      }),
    );
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

  private async dispatch(req: IncomingMessage, res: ServerResponse): Promise<void> {
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
    this.respondJson(res, 200, {
      status: 'ok',
      terminals: vscode.window.terminals.length,
      capturedTerminals: this.buffer.knownTerminals(),
    });
  };

  private handleListTerminals: RouteHandler = async (_req, res) => {
    const items = await Promise.all(
      vscode.window.terminals.map(async (terminal, index) => ({
        index,
        name: terminal.name,
        processId: (await terminal.processId) ?? null,
        hasShellIntegration: Boolean(terminal.shellIntegration),
        cwd: terminal.shellIntegration?.cwd?.toString() ?? null,
      })),
    );
    this.respondJson(res, 200, items);
  };

  private handleReadOutput: RouteHandler = async (_req, res, url) => {
    const name = url.searchParams.get('terminal');
    if (!name) {
      this.respondJson(res, 200, { available: this.buffer.knownTerminals() });
      return;
    }
    this.respondJson(res, 200, {
      terminal: name,
      lines: this.buffer.read(name),
    });
  };

  private handleExecute: RouteHandler = async (req, res) => {
    const body = await this.readJson<ExecuteBody>(req);
    const terminal = await this.locateTerminal(body);
    if (!terminal) {
      this.respondJson(res, 404, { error: 'terminal not found' });
      return;
    }
    const integration = terminal.shellIntegration;
    if (!integration) {
      this.respondJson(res, 409, {
        error: 'shell integration is not available for this terminal',
      });
      return;
    }
    const execution = integration.executeCommand(body.command);
    let collected = '';
    for await (const chunk of execution.read()) {
      collected += chunk;
    }
    this.respondJson(res, 200, { output: collected });
  };

  private handleSend: RouteHandler = async (req, res) => {
    const body = await this.readJson<SendBody>(req);
    const terminal = await this.locateTerminal(body);
    if (!terminal) {
      this.respondJson(res, 404, { error: 'terminal not found' });
      return;
    }
    terminal.sendText(body.text);
    this.respondJson(res, 200, { success: true });
  };

  private handleCreate: RouteHandler = async (req, res) => {
    const body = await this.readJson<CreateBody>(req);
    const terminal = vscode.window.createTerminal({
      name: body.name,
      cwd: body.cwd,
    });
    terminal.show(true);
    if (body.command) {
      terminal.sendText(body.command);
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
    const terminals = vscode.window.terminals;
    this.respondJson(res, 200, {
      index: terminals.indexOf(terminal),
      name: terminal.name,
      hasShellIntegration: Boolean(terminal.shellIntegration),
    });
  };

  private handleClose: RouteHandler = async (req, res) => {
    const body = await this.readJson<TerminalLocator>(req);
    const terminal = await this.locateTerminal(body);
    if (!terminal) {
      this.respondJson(res, 404, { error: 'terminal not found' });
      return;
    }
    const closedName = terminal.name;
    terminal.dispose();
    this.respondJson(res, 200, { success: true, closed: closedName });
  };

  private handleWait: RouteHandler = async (req, res) => {
    const body = await this.readJson<WaitBody>(req);
    const terminal = await this.locateTerminal(body);
    if (!terminal) {
      this.respondJson(res, 404, { error: 'terminal not found' });
      return;
    }
    const timeoutMs = Math.min(
      WAIT_MAX_TIMEOUT_MS,
      Math.max(0, body.timeoutMs ?? WAIT_DEFAULT_TIMEOUT_MS),
    );
    let regex: RegExp;
    try {
      regex = new RegExp(body.pattern);
    } catch (err) {
      this.respondJson(res, 400, { error: `invalid pattern: ${err}` });
      return;
    }
    const result = await this.waitForPattern(terminal.name, regex, timeoutMs);
    this.respondJson(res, 200, result);
  };

  // ------- support helpers -------

  private async locateTerminal(
    locator: TerminalLocator,
  ): Promise<vscode.Terminal | undefined> {
    const terminals = vscode.window.terminals;
    if (typeof locator.processId === 'number') {
      for (const terminal of terminals) {
        if ((await terminal.processId) === locator.processId) {
          return terminal;
        }
      }
      return undefined;
    }
    if (
      typeof locator.terminalIndex === 'number' &&
      locator.terminalIndex >= 0 &&
      locator.terminalIndex < terminals.length
    ) {
      return terminals[locator.terminalIndex];
    }
    if (locator.terminalName) {
      return terminals.find((t) => t.name === locator.terminalName);
    }
    return undefined;
  }

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

  private waitForPattern(
    terminalName: string,
    pattern: RegExp,
    timeoutMs: number,
  ): Promise<{
    matched: boolean;
    matchedText?: string;
    output: string;
    timedOut?: boolean;
  }> {
    const startIndex = this.buffer.lengthOf(terminalName);
    return new Promise((resolve) => {
      let settled = false;

      const finish = (
        matched: boolean,
        timedOut: boolean,
        matchedText?: string,
      ) => {
        if (settled) {
          return;
        }
        settled = true;
        clearInterval(poller);
        clearTimeout(timer);
        const output = this.buffer.sliceFrom(terminalName, startIndex).join('\n');
        resolve({ matched, matchedText, output, timedOut: timedOut || undefined });
      };

      const probe = () => {
        const slice = this.buffer.sliceFrom(terminalName, startIndex).join('\n');
        const match = pattern.exec(slice);
        if (match) {
          finish(true, false, match[0]);
        }
      };

      const poller = setInterval(probe, WAIT_POLL_INTERVAL_MS);
      const timer = setTimeout(() => finish(false, true), timeoutMs);
      probe();
    });
  }
}

let bridge: TerminalBridge | undefined;

export function activate(context: vscode.ExtensionContext): void {
  const channel = vscode.window.createOutputChannel('Terminal Bridge');
  const config = vscode.workspace.getConfiguration(CONFIG_SECTION);
  const port = config.get<number>('port') ?? DEFAULT_PORT;
  const bufferLines =
    config.get<number>('captureBufferLines') ?? DEFAULT_BUFFER_LINES;

  bridge = new TerminalBridge(channel, port, bufferLines);
  bridge.registerCapture(context);
  bridge.start();

  context.subscriptions.push(channel);
  context.subscriptions.push({ dispose: () => bridge?.stop() });

  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration(`${CONFIG_SECTION}.captureBufferLines`)) {
        const updated = vscode.workspace
          .getConfiguration(CONFIG_SECTION)
          .get<number>('captureBufferLines');
        if (typeof updated === 'number') {
          bridge?.setBufferLines(updated);
          channel.appendLine(`[config] capture buffer resized to ${updated}`);
        }
      }
      if (event.affectsConfiguration(`${CONFIG_SECTION}.port`)) {
        channel.appendLine(
          '[config] port change requires VS Code reload to take effect',
        );
      }
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('terminalBridge.showStatus', () => {
      channel.appendLine(
        `[status] port=${port} terminals=${vscode.window.terminals.length}`,
      );
      channel.show(true);
    }),
    vscode.commands.registerCommand('terminalBridge.listTerminals', async () => {
      const items = await Promise.all(
        vscode.window.terminals.map(async (terminal, index) => ({
          index,
          name: terminal.name,
          processId: (await terminal.processId) ?? null,
          hasShellIntegration: Boolean(terminal.shellIntegration),
        })),
      );
      channel.appendLine(`[list] ${JSON.stringify(items, null, 2)}`);
      channel.show(true);
    }),
  );
}

export function deactivate(): void {
  bridge?.stop();
  bridge = undefined;
}
