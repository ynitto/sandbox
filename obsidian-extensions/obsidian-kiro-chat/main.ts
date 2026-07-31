import {
    App,
    ItemView,
    Platform,
    Plugin,
    PluginSettingTab,
    Setting,
    WorkspaceLeaf,
} from 'obsidian';
import { spawn, ChildProcess } from 'child_process';

const VIEW_TYPE_KIRO = 'kiro-chat-view';


// ---------------------------------------------------------------------------
// 設定
// ---------------------------------------------------------------------------

interface KiroSettings {
    /** kiro-cli へのパス（PATH が通っていれば "kiro-cli" のみでも可） */
    kiroPath: string;
    /** 起動時の作業ディレクトリ（空の場合は Vault ルートを使用） */
    workingDirectory: string;
}

const DEFAULT_SETTINGS: KiroSettings = {
    kiroPath: 'kiro-cli',
    workingDirectory: '',
};

// ---------------------------------------------------------------------------
// ACP クライアント
// kiro-cli acp を子プロセスとして起動し、
// JSON-RPC 2.0 over NDJSON (stdio) で通信する。
// ---------------------------------------------------------------------------

type TextHandler = (text: string) => void;
type VoidHandler = () => void;

class KiroAcpClient {
    private proc: ChildProcess | null = null;
    private nextId = 0;
    private pending = new Map<
        number,
        { resolve: (v: unknown) => void; reject: (e: Error) => void }
    >();
    private buf = '';
    private sessionId: string | null = null;
    // session/prompt は JSON-RPC レスポンスが来ないため turn_end で解決する
    private turnPending: { resolve: () => void; reject: (e: Error) => void } | null = null;

    onText: TextHandler | null = null;
    onTurnEnd: VoidHandler | null = null;
    onDisconnect: VoidHandler | null = null;
    onStderr: TextHandler | null = null;

    constructor(
        private readonly kiroPath: string,
        private readonly cwd: string,
    ) {}

    /** kiro-cli acp を起動し ACP ハンドシェイクを完了する */
    async connect(): Promise<void> {
        // Windows: .cmd ラッパーのため shell: true が必要
        // Mac/Linux: bash ラッパーは stdin 扱いが不安定なため直接 spawn し PATH を補完する
        let proc: ChildProcess;
        if (Platform.isWin) {
            proc = spawn(this.kiroPath, ['acp'], {
                cwd: this.cwd || undefined,
                stdio: ['pipe', 'pipe', 'pipe'],
                shell: true,
                windowsHide: true,
            });
        } else {
            const home = process.env.HOME ?? '';
            const extraPaths = [
                home && `${home}/.local/bin`,
                home && `${home}/.kiro/bin`,
                '/opt/homebrew/bin',
                '/usr/local/bin',
                '/usr/bin',
            ].filter(Boolean);
            const env = {
                ...process.env,
                PATH: [...extraPaths, process.env.PATH].filter(Boolean).join(':'),
            };
            proc = spawn(this.kiroPath, ['acp'], {
                cwd: this.cwd || undefined,
                stdio: ['pipe', 'pipe', 'pipe'],
                env,
            });
        }
        this.proc = proc;

        this.proc.stdout!.setEncoding('utf8');
        this.proc.stdout!.on('data', (chunk: string) => {
            this.buf += chunk;
            this.flush();
        });

        this.proc.stderr!.setEncoding('utf8');
        this.proc.stderr!.on('data', (data: string) => {
            console.warn('[kiro-acp] stderr:', data);
            // UI にも表示して問題の特定を容易にする
            for (const line of data.split('\n')) {
                const t = line.trim();
                if (t) this.onStderr?.(t);
            }
        });

        // プロセス起動失敗（コマンドが見つからない等）→ pending をすべて reject
        this.proc.on('error', (err) => {
            this.rejectAllPending(err);
            this.proc = null;
            this.sessionId = null;
            this.onDisconnect?.();
        });

        // 早期終了時も pending / turnPending を reject
        this.proc.on('exit', (code) => {
            const hasWork = this.pending.size > 0 || this.turnPending !== null;
            if (hasWork) {
                const findCmd = Platform.isWin ? 'where.exe kiro-cli' : 'which kiro-cli';
                const msg =
                    code === 127
                        ? `kiro-cli が見つかりません (code 127)。\n設定でフルパスを指定してください。\n「${findCmd}」を実行するとパスを確認できます。`
                        : code === 0
                        ? 'kiro-cli が終了しました。再接続してください。'
                        : `kiro-cli が予期せず終了しました (code: ${code})`;
                this.rejectAllPending(new Error(msg));
            }
            this.proc = null;
            this.sessionId = null;
            this.onDisconnect?.();
        });

        // ACP initialize
        await this.request('initialize', {
            protocolVersion: 1,
            clientCapabilities: {},
            clientInfo: { name: 'obsidian-kiro-chat', version: '1.0.0' },
        });

        // セッション作成
        const res = await this.request('session/new', {
            cwd: this.cwd || '.',
            mcpServers: [],
        });
        this.sessionId = (res as { sessionId: string }).sessionId;
    }

    /** プロンプトを送信する（応答は session/notification 経由、turn_end で完了） */
    prompt(text: string): Promise<void> {
        if (!this.sessionId) throw new Error('セッションが初期化されていません');
        if (!this.proc?.stdin) throw new Error('プロセスが起動していません');
        return new Promise<void>((resolve, reject) => {
            this.turnPending = { resolve, reject };
            // JSON-RPC 2.0 では id なしは通知扱いで処理されない場合があるため
            // id 付きリクエストとして送信し、即時応答（acknowledgment）は pending で受け取る
            const id = this.nextId++;
            this.pending.set(id, {
                resolve: () => { /* acknowledgment は無視 */ },
                reject: (e) => {
                    this.turnPending?.reject(e);
                    this.turnPending = null;
                },
            });
            const line = JSON.stringify({
                jsonrpc: '2.0',
                id,
                method: 'session/prompt',
                params: {
                    sessionId: this.sessionId,
                    content: [{ type: 'text', text }],
                },
            }) + '\n';
            this.proc!.stdin!.write(line);
        });
    }

    disconnect(): void {
        if (this.proc) {
            this.proc.kill();
            this.proc = null;
        }
        this.sessionId = null;
    }

    get connected(): boolean {
        return this.proc !== null && this.sessionId !== null;
    }

    // ── プライベート ──────────────────────────────────────────────────────

    private flush(): void {
        const lines = this.buf.split('\n');
        this.buf = lines.pop() ?? '';
        for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed) continue;
            try {
                this.handle(JSON.parse(trimmed) as Record<string, unknown>);
            } catch {
                console.error('[kiro-acp] JSON parse error:', trimmed);
            }
        }
    }

    private writeMsg(obj: unknown): void {
        const line = JSON.stringify(obj) + '\n';
        this.proc?.stdin?.write(line);
    }

    private handle(msg: Record<string, unknown>): void {
        const id = msg.id as number | string | null | undefined;
        const method = msg.method as string | undefined;
        const hasId = id !== undefined && id !== null;

        // ① kiro → us へのリクエスト（id あり + method あり）
        //    応答しないと kiro がハングして exit する
        if (hasId && method) {
            if (method === 'permission/request') {
                // 全パーミッションを自動承認
                this.writeMsg({ jsonrpc: '2.0', id, result: { granted: true } });
            } else {
                // 未実装メソッドはエラー応答
                this.writeMsg({
                    jsonrpc: '2.0',
                    id,
                    error: { code: -32601, message: `Method not found: ${method}` },
                });
            }
            this.onStderr?.(`[req→] ${method}`);
            return;
        }

        // ② us → kiro へのリクエストに対するレスポンス（id あり + method なし）
        if (hasId && !method && this.pending.has(id as number)) {
            const p = this.pending.get(id as number)!;
            this.pending.delete(id as number);
            if (msg.error) {
                p.reject(
                    new Error(
                        ((msg.error as Record<string, unknown>).message as string) ??
                            'Unknown error',
                    ),
                );
            } else {
                p.resolve(msg.result);
            }
            return;
        }

        // ③ 通知（id なし + method あり）
        if (!hasId && method === 'session/notification') {
            const params = msg.params as Record<string, unknown> | undefined;
            const data = params?.data as Record<string, unknown> | undefined;
            if (!data) return;

            if (data.type === 'agent_message_chunk') {
                const content = data.content as Array<{
                    type: string;
                    text?: string;
                }>;
                for (const part of content ?? []) {
                    if (part.type === 'text' && part.text) this.onText?.(part.text);
                }
            } else if (data.type === 'turn_end') {
                this.onTurnEnd?.();
                this.turnPending?.resolve();
                this.turnPending = null;
            }
            return;
        }

        // ④ その他の通知は無視（ログのみ）
        if (method) {
            this.onStderr?.(`[notif] ${method}`);
        }
    }

    private request(method: string, params: unknown): Promise<unknown> {
        return new Promise((resolve, reject) => {
            if (!this.proc?.stdin) {
                reject(new Error('プロセスが起動していません'));
                return;
            }
            const id = this.nextId++;
            this.pending.set(id, { resolve, reject });
            const line =
                JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n';
            this.proc.stdin.write(line);
        });
    }

    private rejectAllPending(err: Error): void {
        for (const [, p] of this.pending) p.reject(err);
        this.pending.clear();
        this.turnPending?.reject(err);
        this.turnPending = null;
    }
}

// ---------------------------------------------------------------------------
// チャット ビュー
// ---------------------------------------------------------------------------

class KiroChatView extends ItemView {
    private client: KiroAcpClient | null = null;
    private messagesEl!: HTMLDivElement;
    private inputEl!: HTMLTextAreaElement;
    private sendBtn!: HTMLButtonElement;
    private connectBtn!: HTMLButtonElement;
    private statusEl!: HTMLSpanElement;
    private currentBubble: HTMLDivElement | null = null;

    constructor(
        leaf: WorkspaceLeaf,
        private readonly plugin: KiroChatPlugin,
    ) {
        super(leaf);
    }

    getViewType() {
        return VIEW_TYPE_KIRO;
    }
    getDisplayText() {
        return 'Kiro Chat';
    }
    getIcon() {
        return 'bot';
    }

    async onOpen() {
        const root = this.containerEl;
        root.empty();
        root.addClass('kiro-view');

        // ヘッダー
        const header = root.createDiv('kiro-header');
        this.connectBtn = header.createEl('button', {
            text: '接続',
            cls: 'kiro-btn',
        });
        this.connectBtn.addEventListener('click', () => this.toggleConnection());
        this.statusEl = header.createEl('span', {
            text: '未接続',
            cls: 'kiro-status off',
        });

        // メッセージエリア
        this.messagesEl = root.createDiv('kiro-messages');

        // フッター（入力欄 + 送信ボタン）
        const footer = root.createDiv('kiro-footer');
        this.inputEl = footer.createEl('textarea', {
            cls: 'kiro-input',
            attr: {
                placeholder: 'メッセージを入力… (Ctrl+Enter で送信)',
                rows: '3',
            },
        });
        this.inputEl.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && e.ctrlKey) {
                e.preventDefault();
                this.send();
            }
        });

        this.sendBtn = footer.createDiv('kiro-send-row').createEl('button', {
            text: '送信',
            cls: 'kiro-btn primary',
        });
        this.sendBtn.disabled = true;
        this.sendBtn.addEventListener('click', () => this.send());
    }

    async onClose() {
        this.client?.disconnect();
    }

    // ── 接続 / 切断 ──────────────────────────────────────────────────────

    private async toggleConnection() {
        if (this.client?.connected) {
            this.client.disconnect();
            this.client = null;
            this.setStatus(false);
            this.addSys('切断しました');
        } else {
            await this.connect();
        }
    }

    private async connect() {
        const { kiroPath, workingDirectory } = this.plugin.settings;
        const vaultPath = (this.app.vault.adapter as { basePath?: string })
            .basePath;
        const cwd = workingDirectory || vaultPath || '.';

        const cli = new KiroAcpClient(kiroPath, cwd);
        cli.onText = (t) => this.appendText(t);
        cli.onTurnEnd = () => this.onTurnEnd();
        cli.onStderr = (line) => this.addSys(`▶ ${line}`, false, true);
        cli.onDisconnect = () => {
            this.client = null;
            this.setStatus(false);
            this.addSys('切断されました');
        };

        this.setStatus(false, true);
        this.addSys('接続中…');
        try {
            await cli.connect();
            this.client = cli;
            this.setStatus(true);
            this.addSys('接続しました（kiro-cli acp）');
        } catch (e) {
            this.addSys(`接続失敗: ${(e as Error).message}`, true);
            this.setStatus(false);
        }
    }

    // ── 送受信 ───────────────────────────────────────────────────────────

    private async send() {
        const text = this.inputEl.value.trim();
        if (!text || !this.client?.connected) return;

        this.inputEl.value = '';
        this.addBubble('user', text);
        this.setSending(true);

        // アシスタント応答バブルを事前に作成（ストリーミング用）
        this.currentBubble = this.messagesEl.createDiv('kiro-bubble assistant');
        this.currentBubble.createEl('span', { text: 'Kiro', cls: 'kiro-role' });
        this.currentBubble.createDiv('kiro-body');
        this.scroll();

        try {
            await this.client.prompt(text);
        } catch (e) {
            this.addSys(`送信失敗: ${(e as Error).message}`, true);
            this.currentBubble = null;
            this.setSending(false);
        }
    }

    private appendText(text: string) {
        const body = this.currentBubble?.querySelector(
            '.kiro-body',
        ) as HTMLElement | null;
        if (body) body.textContent = (body.textContent ?? '') + text;
        this.scroll();
    }

    private onTurnEnd() {
        this.currentBubble = null;
        this.setSending(false);
    }

    // ── UI ヘルパー ──────────────────────────────────────────────────────

    private addBubble(role: 'user', text: string) {
        const el = this.messagesEl.createDiv(`kiro-bubble ${role}`);
        el.createEl('span', { text: 'You', cls: 'kiro-role' });
        el.createDiv('kiro-body').textContent = text;
        this.scroll();
    }

    private addSys(text: string, isError = false, isStderr = false) {
        const el = this.messagesEl.createDiv('kiro-sys');
        if (isError) el.addClass('error');
        if (isStderr) el.addClass('stderr');
        el.textContent = text;
        this.scroll();
    }

    private setStatus(on: boolean, connecting = false) {
        if (connecting) {
            this.statusEl.textContent = '接続中…';
            this.statusEl.className = 'kiro-status off';
            this.connectBtn.textContent = '接続中';
            this.connectBtn.disabled = true;
            this.sendBtn.disabled = true;
        } else {
            this.statusEl.textContent = on ? '接続中' : '未接続';
            this.statusEl.className = `kiro-status ${on ? 'on' : 'off'}`;
            this.connectBtn.textContent = on ? '切断' : '接続';
            this.connectBtn.disabled = false;
            this.sendBtn.disabled = !on;
        }
    }

    private setSending(sending: boolean) {
        this.sendBtn.disabled = sending || !this.client?.connected;
        this.inputEl.disabled = sending;
    }

    private scroll() {
        this.messagesEl.scrollTo({
            top: this.messagesEl.scrollHeight,
            behavior: 'smooth',
        });
    }
}

// ---------------------------------------------------------------------------
// プラグイン本体
// ---------------------------------------------------------------------------

export default class KiroChatPlugin extends Plugin {
    settings!: KiroSettings;

    async onload() {
        await this.loadSettings();
        this.registerView(
            VIEW_TYPE_KIRO,
            (leaf) => new KiroChatView(leaf, this),
        );
        this.addRibbonIcon('bot', 'Kiro Chat', () => this.activate());
        this.addCommand({
            id: 'open-kiro-chat',
            name: 'Kiro Chat を開く',
            callback: () => this.activate(),
        });
        this.addSettingTab(new KiroSettingTab(this.app, this));
    }

    async onunload() {
        this.app.workspace.detachLeavesOfType(VIEW_TYPE_KIRO);
    }

    async loadSettings() {
        this.settings = Object.assign(
            {},
            DEFAULT_SETTINGS,
            await this.loadData(),
        );
    }

    async saveSettings() {
        await this.saveData(this.settings);
    }

    private async activate() {
        const { workspace } = this.app;
        let leaf = workspace.getLeavesOfType(VIEW_TYPE_KIRO)[0];
        if (!leaf) {
            leaf = workspace.getRightLeaf(false)!;
            await leaf.setViewState({ type: VIEW_TYPE_KIRO, active: true });
        }
        workspace.revealLeaf(leaf);
    }
}

// ---------------------------------------------------------------------------
// 設定タブ
// ---------------------------------------------------------------------------

class KiroSettingTab extends PluginSettingTab {
    constructor(
        app: App,
        private readonly plugin: KiroChatPlugin,
    ) {
        super(app, plugin);
    }

    display() {
        const { containerEl } = this;
        containerEl.empty();
        containerEl.createEl('h2', { text: 'Kiro Chat 設定' });

        const isWin = Platform.isWin;
        const findCmd = isWin ? 'where.exe kiro-cli' : 'which kiro-cli';
        const placeholder = isWin
            ? '例: C:\\Users\\...\\AppData\\Local\\...\\kiro-cli.exe'
            : '例: /home/user/.local/bin/kiro-cli';

        const pathDesc = document.createDocumentFragment();
        pathDesc.append(
            'kiro-cli へのフルパス。接続失敗 (code 127) の場合はフルパスを設定してください。',
            document.createTextNode(' '),
            Object.assign(document.createElement('code'), {
                textContent: findCmd,
            }),
            ` でパスを確認できます。`,
        );

        new Setting(containerEl)
            .setName('kiro-cli のパス')
            .setDesc(pathDesc)
            .addText((t) =>
                t
                    .setPlaceholder(placeholder)
                    .setValue(this.plugin.settings.kiroPath)
                    .onChange(async (v) => {
                        this.plugin.settings.kiroPath = v.trim();
                        await this.plugin.saveSettings();
                    }),
            );

        new Setting(containerEl)
            .setName('作業ディレクトリ')
            .setDesc(
                'kiro-cli を起動するディレクトリ（空の場合は Vault のルートディレクトリを使用）',
            )
            .addText((t) =>
                t
                    .setPlaceholder('例: C:\\Users\\...\\my-project')
                    .setValue(this.plugin.settings.workingDirectory)
                    .onChange(async (v) => {
                        this.plugin.settings.workingDirectory = v.trim();
                        await this.plugin.saveSettings();
                    }),
            );
    }
}
