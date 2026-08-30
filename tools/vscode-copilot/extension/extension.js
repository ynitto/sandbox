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
  return { text, model: describeModel(model) };
}

function describeModel(model) {
  return { id: model.id, family: model.family, name: model.name };
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

// LanguageModelToolResult は text part と prompt-tsx part の配列。CLI へ渡せるのは
// text だけなので、それ以外は種別だけ残して落とす（黙って消すと空応答に見える）。
function serializablePart(value) {
  try {
    return JSON.parse(JSON.stringify(value));
  } catch (_) {
    return undefined;  // 循環参照など。中身は諦めるが種別だけは残す。
  }
}

// prompt-tsx の直列化形（PromptElementJSON）から本文を拾う。要素ノードは children を
// 持ち、テキストノードは `text` を持つ。各 text は自分の改行を含むので、出現順に
// 連結すれば元の本文が戻る（lineBreakBefore を見て改行を足すと二重になる）。
//
// 降りるのは children と node だけにする。木を無差別に舐めると references の中など
// 本文でない場所の text まで拾いうる。
function collectText(node, out) {
  if (Array.isArray(node)) {
    for (const child of node) collectText(child, out);
    return;
  }
  if (!node || typeof node !== 'object') return;
  if (typeof node.text === 'string') out.push(node.text);
  if (node.children) collectText(node.children, out);
  if (node.node) collectText(node.node, out);
}

function toolResultToJson(result) {
  const content = [];
  const texts = [];
  for (const part of (result && result.content) || []) {
    if (typeof part.value === 'string') {
      content.push({ type: 'text', value: part.value });
      texts.push(part.value);
      continue;
    }
    // prompt-tsx などの非テキスト部品。種別だけ残して捨てると「成功したのに空」と
    // 見分けが付かないので、JSON にできる範囲で中身も返す（--json で読める）。
    const value = serializablePart(part.value);
    if (value === undefined) {
      content.push({ type: 'other' });
      continue;
    }
    const collected = [];
    collectText(value, collected);
    if (collected.length) texts.push(collected.join(''));
    content.push({ type: 'other', value });
  }
  return { content, text: texts.join('') };
}

// 任意のツールを名前で呼ぶ。スキーマは VS Code が持っていて invokeTool が検証するので、
// こちら側はツールごとの知識を持たない——持つと環境差で必ず古くなる。
async function invokeToolByName(body, cancellationToken) {
  if (!body || typeof body.name !== 'string' || !body.name.trim()) {
    throw badRequest('name must be a non-empty string');
  }
  if (!vscode.lm.tools.some(tool => tool.name === body.name)) {
    throw Object.assign(new Error(`tool not registered in vscode.lm.tools: ${body.name}`), { status: 404 });
  }
  const input = body.input === undefined ? {} : body.input;
  if (input === null || typeof input !== 'object' || Array.isArray(input)) {
    throw badRequest('input must be a JSON object');
  }
  // toolInvocationToken を渡せるのは chat request の中だけ。外から呼ぶときは undefined で、
  // 進捗 UI は出ないが承認ダイアログは出る（ターミナル実行などはここで人が止められる）。
  const result = await vscode.lm.invokeTool(
    body.name, { toolInvocationToken: undefined, input }, cancellationToken);
  return toolResultToJson(result);
}

const DEFAULT_MAX_ROUNDS = 12;

// 使うツールは呼び出し側が名前で指定する。ここは vscode.lm.tools に居るものだけを
// 通す——居ない名前を黙って捨てると「頼んだ道具を使わないエージェント」になる。
function resolveTools(names) {
  return (names || []).map(name => {
    const info = vscode.lm.tools.find(tool => tool.name === name);
    if (!info) throw badRequest(`tool not registered in vscode.lm.tools: ${name}`);
    return { name: info.name, description: info.description, inputSchema: info.inputSchema };
  });
}

// モデルにツールを持たせて回す。ツール本体も承認も VS Code のものを使い、ここが持つのは
// 「どのツールを呼ぶか決めさせて、結果を返して、また訊く」というループだけ。
async function runAgent(body, cancellationToken, emit) {
  const tools = resolveTools(body.tools);
  const offered = new Set(tools.map(tool => tool.name));
  const selector = { vendor: 'copilot' };
  if (body.family) selector.family = body.family;
  const models = await vscode.lm.selectChatModels(selector);
  if (!models.length) {
    throw Object.assign(new Error('no Copilot chat model is available in VS Code'), { status: 503 });
  }
  const model = models[0];
  const messages = toMessages(body);
  const maxRounds = Number.isInteger(body.maxRounds) && body.maxRounds > 0
    ? body.maxRounds : DEFAULT_MAX_ROUNDS;

  for (let round = 1; round <= maxRounds; round++) {
    const response = await model.sendRequest(messages, { tools }, cancellationToken);
    const parts = [];
    const calls = [];
    let text = '';
    for await (const part of response.stream) {
      if (part instanceof vscode.LanguageModelTextPart) {
        text += part.value;
        parts.push(part);
        emit({ delta: part.value });
      } else if (part instanceof vscode.LanguageModelToolCallPart) {
        parts.push(part);
        calls.push(part);
      }
    }
    if (!calls.length) return { text, rounds: round, model: describeModel(model) };

    // 手番を積み直す。呼び出しを Assistant 側へ、結果を User 側へ入れるのが契約で、
    // callId で対応が付く。片方だけ積むと次の往復でモデルが文脈を失う。
    messages.push(vscode.LanguageModelChatMessage.Assistant(parts));
    const results = [];
    for (const call of calls) {
      emit({ tool: call.name, input: call.input });
      if (!offered.has(call.name)) {
        // 渡していないツールは実行しない。**allowlist は渡す側だけでなく実行する側でも
        // 守る。** モデルが提示外の名前を返すことはあり、ここを素通しすると読み取り
        // 専用の手番で書き込みツールが動く（定義が readonly: enforced を名乗る以上、
        // それは嘘になる）。実測でスタブが提示外の copilot_applyPatch を呼び、
        // ファイルが書き換わった。
        const message = `tool not offered for this request: ${call.name}`;
        results.push(new vscode.LanguageModelToolResultPart(
          call.callId, [new vscode.LanguageModelTextPart(`tool error: ${message}`)]));
        emit({ tool: call.name, error: message });
        continue;
      }
      try {
        const result = await vscode.lm.invokeTool(
          call.name, { toolInvocationToken: undefined, input: call.input }, cancellationToken);
        results.push(new vscode.LanguageModelToolResultPart(call.callId, result.content));
        emit({ tool: call.name, ok: true });
      } catch (error) {
        // 失敗もモデルへ返す。黙って落とすと同じ呼び出しを繰り返すだけになる。
        const message = error && error.message ? error.message : String(error);
        results.push(new vscode.LanguageModelToolResultPart(
          call.callId, [new vscode.LanguageModelTextPart(`tool error: ${message}`)]));
        emit({ tool: call.name, error: message });
      }
    }
    messages.push(vscode.LanguageModelChatMessage.User(results));
  }
  throw Object.assign(
    new Error(`gave up after ${maxRounds} rounds without a final answer`), { status: 409 });
}

// エージェントは何往復もするので常に NDJSON で流す。何をしているか見えないまま
// 数分黙るのが一番困る。ヘッダは最初の 1 行を書くまで送らないので、モデル不在などの
// 失敗は status 付きの JSON で返る（streamChat と同じ作法）。
async function streamAgent(response, body, cancellationToken) {
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
    const result = await runAgent(body, cancellationToken, event => { start(); write(event); });
    start();
    write({ done: true, text: result.text, rounds: result.rounds, model: result.model });
    response.end();
  } catch (error) {
    if (!started) throw error;
    write({ error: error.message || String(error) });
    response.end();
  }
}

function createServer(token) {
  return http.createServer(async (request, response) => {
    const route = `${request.method} ${request.url}`;
    if (route !== 'POST /v1/chat' && route !== 'POST /v1/tool'
        && route !== 'POST /v1/agent' && route !== 'GET /v1/tools') {
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
      if (route === 'POST /v1/tool') json(response, 200, await invokeToolByName(body, source.token));
      else if (route === 'POST /v1/agent') await streamAgent(response, body, source.token);
      else if (body && body.stream) await streamChat(response, body, source.token);
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
