const crypto = require('crypto');
const fs = require('fs');
const http = require('http');
const os = require('os');
const path = require('path');
const vscode = require('vscode');

const MAX_BODY = 1024 * 1024;
const endpointFile = () => process.env.VSCODE_COPILOT_BRIDGE_FILE ||
  path.join(os.homedir(), '.vscode-copilot-bridge.json');

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
      catch (_) { reject(Object.assign(new Error('body must be valid JSON'), { status: 400 })); }
    });
    request.on('error', reject);
  });
}

async function askCopilot(body, cancellationToken) {
  if (!body || typeof body.prompt !== 'string' || !body.prompt.trim()) {
    throw Object.assign(new Error('prompt must be a non-empty string'), { status: 400 });
  }
  const selector = { vendor: 'copilot' };
  if (body.family) selector.family = body.family;
  const models = await vscode.lm.selectChatModels(selector);
  if (!models.length) {
    throw Object.assign(new Error('no Copilot chat model is available in VS Code'), { status: 503 });
  }
  const model = models[0];
  const messages = [vscode.LanguageModelChatMessage.User(body.prompt)];
  const response = await model.sendRequest(messages, {}, cancellationToken);
  let text = '';
  for await (const fragment of response.text) text += fragment;
  return { text, model: { id: model.id, family: model.family, name: model.name } };
}

function createServer(token) {
  return http.createServer(async (request, response) => {
    if (request.method !== 'POST' || request.url !== '/v1/chat') {
      json(response, 404, { error: 'not found' });
      return;
    }
    if (request.headers.authorization !== `Bearer ${token}`) {
      json(response, 401, { error: 'unauthorized' });
      return;
    }
    try {
      const body = await readBody(request);
      json(response, 200, await askCopilot(body, new vscode.CancellationTokenSource().token));
    } catch (error) {
      console.error('[vscode-copilot-bridge]', error);
      json(response, error.status || 500, { error: error.message || String(error) });
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
    const token = crypto.randomBytes(32).toString('hex');
    server = createServer(token);
    server.listen(0, '127.0.0.1', () => writeEndpoint(server, token));
    server.on('error', error => vscode.window.showErrorMessage(`Copilot Bridge: ${error.message}`));
  };
  start();
  context.subscriptions.push(
    vscode.commands.registerCommand('vscodeCopilotBridge.restart', start),
    { dispose: () => server && server.close() },
    { dispose: () => { try { fs.unlinkSync(endpointFile()); } catch (_) {} } },
  );
}

function deactivate() {}

module.exports = { activate, deactivate };
