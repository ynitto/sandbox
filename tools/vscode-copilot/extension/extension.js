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

// ツール結果を、モデルへ返せる形（テキスト部品）へ畳む。
//
// **prompt-tsx の部品をそのまま返すと 400 になる。** 実測（2026-08-30）: 結果が
// prompt-tsx の copilot_getChangedFiles を呼ぶと、次の往復が
// `messages with role 'tool' must be a response to a preceeding message with
// 'tool_calls'` で落ちる。こちらが積む形（呼び出しを Assistant へ、結果を User へ、
// callId で対応）は --debug で正しいことを確認済みで、履歴の有無にも依らない。
// テキストを返すツール（copilot_readProjectStructure）では同じ往復が通る。
//
// 本文の取り出しは --call と同じ collectText を使う。ここで畳んでおけば、VS Code の
// prompt-tsx 変換に依らずに済む。
// ツール結果 1 件の上限（文字数）。トークン数の代わりに文字数で測る——正確な
// トークン化はモデル依存で、ここでは持てない。**大きく見積もる側へ倒す**（日本語は
// おおむね 1 文字 1 トークン）。
//
// 実測（2026-08-30）: copilot_getChangedFiles をそのまま返して
// `Message exceeds token limit` になった。往復ごとに積み上がるので、1 件が通っても
// 数往復で溢れる。切り詰めたことはモデルへ言葉で伝える——黙って削ると、モデルは
// 「全部見た」つもりで結論を出す。
const MAX_TOOL_RESULT_CHARS = 16000;

function truncateForModel(text, limit) {
  if (text.length <= limit) return text;
  return text.slice(0, limit)
    + `\n\n…（結果が長いので ${limit} 文字で切りました。`
    + `全体は ${text.length} 文字あります。範囲を絞って呼び直してください）`;
}

function toolResultParts(result, limit) {
  const parts = [];
  for (const part of (result && result.content) || []) {
    if (part instanceof vscode.LanguageModelTextPart) {
      parts.push(part);
      continue;
    }
    if (typeof part.value === 'string') {
      parts.push(new vscode.LanguageModelTextPart(part.value));
      continue;
    }
    const collected = [];
    collectText(serializablePart(part.value), collected);
    if (collected.length) parts.push(new vscode.LanguageModelTextPart(collected.join('')));
  }
  // 空のまま返さない。「道具は動いたが何も言わない」を、モデルには言葉で伝える
  // （空の結果を積むのは、空の assistant を積むのと同じ穴）。
  if (!parts.length) return { parts: [new vscode.LanguageModelTextPart('(このツールは本文を返しませんでした)')], full: 0 };
  // 上限は部品ごとではなく結果 1 件で見る。部品ごとに切ると、部品数だけ上限が増える。
  const text = parts.map(part => part.value).join('');
  const capped = truncateForModel(text, limit || MAX_TOOL_RESULT_CHARS);
  return { parts: [new vscode.LanguageModelTextPart(capped)], full: text.length };
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

// --- bridge が自分で持つ編集ツール -------------------------------------------
//
// **Copilot の編集ツールは chat request の外から呼べません。** 実測（2026-08-30）:
// `copilot_applyPatch` は `Missing patch text or stream`、`copilot_replaceString` は
// `no prompt context found`、`copilot_createFile` は `Invalid stream` で必ず落ちます。
// どれも invoke の頭で `this._promptContext?.stream` を要求していて、その
// `_promptContext` は Copilot 自身のチャットループが `resolveInput()` 経由でしか
// 入れません（`vscode.lm.invokeTool` は `resolveInput` を呼ばない）。runSubagent が
// `toolInvocationToken` を要求するのと同じ壁で、こちらからは越えられません。
//
// なので編集はここで持ちます。patch 形式は起こしません——差分の当て直しを自前で
// 実装する価値はなく、「厳密一致で 1 箇所だけ置換」と「丸ごと書く」で足ります。

function workspaceRoots() {
  return ((vscode.workspace && vscode.workspace.workspaceFolders) || [])
    .map(folder => folder && folder.uri && folder.uri.fsPath).filter(Boolean);
}

// 書き込み先はワークスペースの中だけにする。パスを組み立てるのはモデルで、
// 打ち間違いも思い違いもワークスペースの外へ届く——読むのと違って戻せない。
function writableUri(input) {
  if (typeof input !== 'string' || !input.trim()) {
    throw new Error('filePath は絶対パスの文字列で渡してください');
  }
  const target = path.resolve(input);
  const roots = workspaceRoots();
  if (!roots.length) {
    throw new Error('VS Code がフォルダを開いていないので、書き込んでよい場所を決められません');
  }
  if (!roots.some(root => target === root || target.startsWith(root + path.sep))) {
    throw new Error(`ワークスペースの外へは書けません: ${target}（${roots.join(', ')} の下だけ）`);
  }
  return vscode.Uri.file(target);
}

function toolText(message) {
  return new vscode.LanguageModelToolResult([new vscode.LanguageModelTextPart(message)]);
}

const BRIDGE_TOOLS = {
  // 厳密一致で 1 箇所だけ置換する。0 件も複数件も失敗させる——「どこかが変わった」を
  // 黙って返すと、モデルは直った気で次の手番へ進む。
  bridge_replaceString: {
    async invoke(options) {
      const { filePath, oldString, newString } = options.input || {};
      if (typeof oldString !== 'string' || !oldString) {
        throw new Error('oldString は空でない文字列で渡してください');
      }
      if (typeof newString !== 'string') throw new Error('newString は文字列で渡してください');
      const uri = writableUri(filePath);
      // ドキュメント経由で編集する。エディタに未保存の変更があるとき fs へ直に書くと、
      // それを黙って捨てることになる。
      const document = await vscode.workspace.openTextDocument(uri);
      const text = document.getText();
      const at = text.indexOf(oldString);
      if (at < 0) throw new Error(`oldString がファイルに見つかりません: ${uri.fsPath}`);
      if (text.indexOf(oldString, at + oldString.length) >= 0) {
        throw new Error(
          `oldString が複数の場所と一致します。前後の行を足して 1 箇所に絞ってください: ${uri.fsPath}`);
      }
      const range = new vscode.Range(document.positionAt(at), document.positionAt(at + oldString.length));
      const edit = new vscode.WorkspaceEdit();
      edit.replace(uri, range, newString);
      if (!await vscode.workspace.applyEdit(edit)) {
        throw new Error(`編集を適用できませんでした: ${uri.fsPath}`);
      }
      // 保存まで済ませる。開いたバッファの中だけ直しても、呼び出し側からは何も
      // 変わっていない。
      if (!await document.save()) throw new Error(`保存できませんでした: ${uri.fsPath}`);
      return toolText(`Replaced 1 occurrence in ${uri.fsPath}`);
    },
  },
  // 丸ごと書く。既存ファイルは中身を置き換える（部分的に直すなら
  // bridge_replaceString を使う）。
  bridge_createFile: {
    async invoke(options) {
      const { filePath, content } = options.input || {};
      if (typeof content !== 'string') throw new Error('content は文字列で渡してください');
      const uri = writableUri(filePath);
      // ponytail: 未保存のエディタがあれば、この書き込みは競合する（VS Code 側で
      // 「ディスクが変わった」になる）。丸ごと置き換える道具なので今はこれで足りる。
      await vscode.workspace.fs.writeFile(uri, Buffer.from(content, 'utf8'));
      return toolText(`Wrote ${content.length} chars to ${uri.fsPath}`);
    },
  },
};

function registerBridgeTools() {
  return Object.entries(BRIDGE_TOOLS).map(([name, tool]) => vscode.lm.registerTool(name, tool));
}

const DEFAULT_MAX_ROUNDS = 12;

// **モデルはワークスペースの場所を知らない。** Copilot のファイルツールは絶対パスしか
// 受け取らないのに（`Invalid input path: README.md. Be sure to use an absolute path.`）、
// どこを起点にすればよいかを知らせる口が無かった。実測（2026-08-30）: `/README.md`
// `./README.md` `agents/README.md` を当てずっぽうで叩き続け、12 往復を使い切った。
// 場所を知っているのは拡張（VS Code が開いているフォルダ）なので、ここで渡す。
function workspaceNote() {
  const folders = (vscode.workspace && vscode.workspace.workspaceFolders) || [];
  const roots = folders.map(folder => folder && folder.uri && folder.uri.fsPath).filter(Boolean);
  if (!roots.length) return '';
  return 'いま開いているワークスペース:\n'
    + roots.map(root => `- ${root}`).join('\n')
    + '\n\nファイルを扱うツールへ渡すパスは、この下の**絶対パス**にすること'
    + '（相対パスは受け付けられない）。\n\n';
}

// デバッグ用に「送った形」だけを写す（本文は出さない——依頼文が丸ごとログへ出ると困る）。
function describeMessage(message) {
  const content = message.content;
  if (typeof content === 'string') return { role: message.role, content: `string(${content.length})` };
  if (!Array.isArray(content)) return { role: message.role, content: typeof content };
  return {
    role: message.role,
    content: content.map(part => {
      const kind = part && part.constructor ? part.constructor.name : typeof part;
      if (part instanceof vscode.LanguageModelToolCallPart) return `${kind}(${part.callId}:${part.name})`;
      if (part instanceof vscode.LanguageModelToolResultPart) {
        // 中身の部品まで見る。**ツール結果の形も疑わしい**——prompt-tsx を返すツールと
        // 文字列を返すツールで、モデル側の変換が同じとは限らない。
        const inner = Array.isArray(part.content)
          ? part.content.map(c => (c && c.constructor ? c.constructor.name : typeof c)).join('+')
          : typeof part.content;
        return `${kind}(${part.callId}:${inner})`;
      }
      if (part instanceof vscode.LanguageModelTextPart) return `${kind}(${String(part.value).length})`;
      return kind;
    }),
  };
}

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
function withWorkspaceNote(body) {
  const note = workspaceNote();
  if (!note) return body;
  if (Array.isArray(body.messages) && body.messages.length) {
    const messages = body.messages.slice();
    const last = messages[messages.length - 1];
    if (last && last.role === 'user' && typeof last.content === 'string') {
      messages[messages.length - 1] = { ...last, content: note + last.content };
      return { ...body, messages };
    }
    return body;
  }
  if (typeof body.prompt === 'string') return { ...body, prompt: note + body.prompt };
  return body;
}

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
  // 環境の申し送りは**いまの手番の頭**へ置く。別のメッセージとして足すと、履歴の
  // 並び（user/assistant の交互）が崩れる。
  const messages = toMessages(withWorkspaceNote(body));
  const maxRounds = Number.isInteger(body.maxRounds) && body.maxRounds > 0
    ? body.maxRounds : DEFAULT_MAX_ROUNDS;
  let lastToolError = '';

  for (let round = 1; round <= maxRounds; round++) {
    // 何を送ったのかは、失敗してからでは分からない。VS Code の変換の向こうで
    // 400 が返るとき、手前で持っていた形が唯一の手がかりになる。
    if (body.debug) emit({ debug: { round, messages: messages.map(describeMessage) } });
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
    // 空文字の text part を混ぜない。**空の assistant を積むと 400 で弾かれ続ける**のは
    // 履歴で既に踏んでいる（toMessages が空文字を拒む理由がそれ）。同じ穴が手番の中にも
    // ある——モデルはツール呼び出しの前に空や改行だけの text を吐くことがある。
    const speech = parts.filter(
      part => !(part instanceof vscode.LanguageModelTextPart) || String(part.value).trim());
    messages.push(vscode.LanguageModelChatMessage.Assistant(speech));
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
        lastToolError = `${call.name}: ${message}`;
        emit({ tool: call.name, error: message });
        continue;
      }
      try {
        const result = await vscode.lm.invokeTool(
          call.name, { toolInvocationToken: undefined, input: call.input }, cancellationToken);
        const folded = toolResultParts(result, body.maxToolResultChars);
        results.push(new vscode.LanguageModelToolResultPart(call.callId, folded.parts));
        const limit = body.maxToolResultChars || MAX_TOOL_RESULT_CHARS;
        emit(folded.full > limit
          ? { tool: call.name, ok: true, truncated: folded.full, limit }
          : { tool: call.name, ok: true });
      } catch (error) {
        // 失敗もモデルへ返す。黙って落とすと同じ呼び出しを繰り返すだけになる。
        const message = error && error.message ? error.message : String(error);
        results.push(new vscode.LanguageModelToolResultPart(
          call.callId, [new vscode.LanguageModelTextPart(`tool error: ${message}`)]));
        lastToolError = `${call.name}: ${message}`;
        emit({ tool: call.name, error: message });
      }
    }
    messages.push(vscode.LanguageModelChatMessage.User(results));
  }
  // 打ち切りだけを伝えても、何に詰まっていたのかが分からない。最後のツール失敗を添える
  // ——実測で「絶対パスが要る」を 12 回繰り返して終わった（それが見えないと直せない）。
  throw Object.assign(
    new Error(`gave up after ${maxRounds} rounds without a final answer`
      + (lastToolError ? `（最後のツール失敗: ${lastToolError}）` : '')), { status: 409 });
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
    ...registerBridgeTools(),
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
