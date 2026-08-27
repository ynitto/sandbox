const crypto = require('crypto');
const fs = require('fs');
const http = require('http');
const os = require('os');
const path = require('path');
const vscode = require('vscode');

// 会話履歴を丸ごと受けるので、単発 prompt 時代の 1 MiB では長い対話が入らない。
const MAX_BODY = 4 * 1024 * 1024;
const endpointFile = () => process.env.VSCODE_COPILOT_BRIDGE_FILE ||
  path.join(os.homedir(), '.vscode-copilot-bridge.json');
const configuredPort = () => Number.parseInt(process.env.VSCODE_COPILOT_BRIDGE_PORT || '0', 10);

function badRequest(message) {
  return Object.assign(new Error(message), { status: 400 });
}

function json(response, status, body) {
  const payload = Buffer.from(JSON.stringify(body));
  response.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': payload.length,
    'Cache-Control': 'no-store',
  });
  response.end(payload);
}

function readBody(request) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    request.on('data', chunk => {
      size += chunk.length;
      if (size > MAX_BODY) {
        reject(Object.assign(new Error('request body is too large'), { status: 413 }));
        request.destroy();
        return;
      }
      chunks.push(chunk);
    });
    request.on('end', () => {
      try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); }
      catch (_) { reject(badRequest('body must be valid JSON')); }
    });
    request.on('error', reject);
  });
}

// 会話状態は CLI 側が持ち、拡張は毎回すべての手番を受け取る stateless な変換器でいる。
// bridge を再起動しても会話が消えず、複数の CLI セッションが 1 つの拡張を同時に使える。
function toMessages(body) {
  if (body && Array.isArray(body.messages)) {
    if (!body.messages.length) throw badRequest('messages must not be empty');
    return body.messages.map((message, index) => {
      const content = message && typeof message.content === 'string' ? message.content : '';
      if (!content.trim()) throw badRequest(`messages[${index}].content must be a non-empty string`);
      const role = message.role;
      if (role === 'assistant') return vscode.LanguageModelChatMessage.Assistant(content);
      if (role === 'user') return vscode.LanguageModelChatMessage.User(content);
      throw badRequest(`messages[${index}].role must be "user" or "assistant"`);
    });
  }
  // 単発 prompt は旧 CLI との互換のために残す。
  if (body && typeof body.prompt === 'string' && body.prompt.trim()) {
    return [vscode.LanguageModelChatMessage.User(body.prompt)];
  }
  throw badRequest('messages[] or a non-empty prompt is required');
}

async function askCopilot(body, cancellationToken, onDelta) {
  const messages = toMessages(body);
  const selector = { vendor: 'copilot' };
  if (body.family) selector.family = body.family;
  const models = await vscode.lm.selectChatModels(selector);
  if (!models.length) {
    throw Object.assign(new Error('no Copilot chat model is available in VS Code'), { status: 503 });
  }
  const model = models[0];
  const response = await model.sendRequest(messages, {}, cancellationToken);
  let text = '';
  for await (const fragment of response.text) {
    text += fragment;
    if (onDelta) onDelta(fragment);
  }
  return { text, model: { id: model.id, family: model.family, name: model.name } };
}

// NDJSON: {"delta":"..."} を流し、最後に {"done":true,"model":{...}}。最初の 1 片を書くまでは
// ヘッダを送らないので、モデル不在などの失敗は従来どおり HTTP status 付きの JSON で返る。
async function streamChat(response, body, cancellationToken) {
  let started = false;
  const start = () => {
    if (started) return;
    started = true;
    response.writeHead(200, {
      'Content-Type': 'application/x-ndjson; charset=utf-8',
      'Cache-Control': 'no-store',
    });
  };
  const write = event => response.write(`${JSON.stringify(event)}\n`);
  try {
    const result = await askCopilot(body, cancellationToken, delta => { start(); write({ delta }); });
    start();
    write({ done: true, model: result.model });
    response.end();
  } catch (error) {
    if (!started) throw error;
    write({ error: error.message || String(error) });
    response.end();
  }
}

// VS Code に今そのとき登録されているツールをそのまま返す。中身は VS Code の
// バージョン・設定・入れている MCP サーバで変わるので、こちらで持つ一覧は正にならない。
// Copilot Chat 拡張自身も自分のツール一覧をこの `vscode.lm.tools` から取っている。
function listTools() {
  return {
    tools: vscode.lm.tools.map(tool => ({
      name: tool.name,
      description: tool.description,
      tags: [...(tool.tags || [])],
      inputSchema: tool.inputSchema,
    })),
  };
}

function createServer(token) {
  return http.createServer(async (request, response) => {
    const route = `${request.method} ${request.url}`;
    if (route !== 'POST /v1/chat' && route !== 'GET /v1/tools') {
      json(response, 404, { error: 'not found' });
      return;
    }
    if (request.headers.authorization !== `Bearer ${token}`) {
      json(response, 401, { error: 'unauthorized' });
      return;
    }
    if (route === 'GET /v1/tools') {
      try {
        json(response, 200, listTools());
      } catch (error) {
        console.error('[vscode-copilot-bridge]', error);
        json(response, 500, { error: error.message || String(error) });
      }
      return;
    }
    const source = new vscode.CancellationTokenSource();
    // 対話 CLI で Ctrl-C を押すと接続が切れる。モデルを回し続けない。監視するのは
    // request ではなく response——request の 'close' は「本文を読み終えた」でも発火するため、
    // そちらを見ると毎回そのままキャンセルしてしまう。
    response.on('close', () => { if (!response.writableFinished) source.cancel(); });
    try {
      const body = await readBody(request);
      if (body && body.stream) await streamChat(response, body, source.token);
      else json(response, 200, await askCopilot(body, source.token));
    } catch (error) {
      console.error('[vscode-copilot-bridge]', error);
      if (!response.headersSent) json(response, error.status || 500, { error: error.message || String(error) });
      else response.end();
    } finally {
      source.dispose();
    }
  });
}

function writeEndpoint(server, token) {
  const address = server.address();
  const target = endpointFile();
  const temporary = `${target}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, JSON.stringify({
    version: 1,
    url: `http://127.0.0.1:${address.port}/v1/chat`,
    token,
    pid: process.pid,
  }) + '\n', { mode: 0o600 });
  fs.renameSync(temporary, target);
}

function activate(context) {
  let server;
  const start = () => {
    if (server) server.close();
    const token = process.env.VSCODE_COPILOT_BRIDGE_TOKEN || crypto.randomBytes(32).toString('hex');
    server = createServer(token);
    server.listen(configuredPort(), '127.0.0.1', () => {
      // WSL launcher modeではCLIがport/tokenを既に保持しているので、Windows側へ
      // discovery fileを重複して書かない。
      if (!process.env.VSCODE_COPILOT_BRIDGE_TOKEN) writeEndpoint(server, token);
    });
    server.on('error', error => vscode.window.showErrorMessage(`Copilot Bridge: ${error.message}`));
  };
  start();
  context.subscriptions.push(
    vscode.commands.registerCommand('vscodeCopilotBridge.restart', start),
    { dispose: () => server && server.close() },
    { dispose: () => {
      if (!process.env.VSCODE_COPILOT_BRIDGE_TOKEN) {
        try { fs.unlinkSync(endpointFile()); } catch (_) {}
      }
    } },
  );
}

function deactivate() {}

module.exports = { activate, deactivate };
