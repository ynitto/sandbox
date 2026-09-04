'use strict';
// `vscode` の代役。extension.js が触る範囲だけを持つ。本物の VS Code は起こせないので、
// ループの往復数・メッセージの積み方・bridge ツールの挙動をここで固定する。
const Module = require('module');
const path = require('path');

class LanguageModelTextPart { constructor(value) { this.value = value; } }
class LanguageModelToolCallPart {
  constructor(callId, name, input) { this.callId = callId; this.name = name; this.input = input; }
}
class LanguageModelToolResultPart {
  constructor(callId, content) { this.callId = callId; this.content = content; }
}
class LanguageModelToolResult { constructor(content) { this.content = content; } }
class Position { constructor(line, character) { this.line = line; this.character = character; } }
class Range { constructor(start, end) { this.start = start; this.end = end; } }
class Uri {
  constructor(fsPath) { this.fsPath = fsPath; }
  static file(fsPath) { return new Uri(fsPath); }
}
class WorkspaceEdit {
  constructor() { this.edits = []; }
  replace(uri, range, text) { this.edits.push({ uri, range, text }); }
}

// 開いたドキュメント。offset ↔ position は改行だけで数える（テストの中身で足りる）。
class Document {
  constructor(uri, files) { this.uri = uri; this.files = files; this.saved = 0; }
  getText() { return this.files.get(this.uri.fsPath); }
  positionAt(offset) {
    const before = this.getText().slice(0, offset).split('\n');
    return new Position(before.length - 1, before[before.length - 1].length);
  }
  offsetAt(position) {
    const lines = this.getText().split('\n');
    let offset = 0;
    for (let i = 0; i < position.line; i++) offset += lines[i].length + 1;
    return offset + position.character;
  }
  async save() { this.saved++; return true; }
}

function makeVscode(options = {}) {
  const files = new Map(Object.entries(options.files || {}));
  const roots = options.roots || ['/ws'];
  const state = { files, invoked: [], requests: [], registered: [] };
  const vscode = {
    LanguageModelTextPart, LanguageModelToolCallPart, LanguageModelToolResultPart,
    LanguageModelToolResult, Range, Uri, WorkspaceEdit,
    LanguageModelChatMessage: {
      User: content => ({ role: 'user', content }),
      Assistant: content => ({ role: 'assistant', content }),
    },
    CancellationTokenSource: class { constructor() { this.token = {}; } cancel() {} dispose() {} },
    window: { showErrorMessage() {} },
    commands: { registerCommand: () => ({ dispose() {} }) },
    workspace: {
      workspaceFolders: roots.map(root => ({ uri: Uri.file(root) })),
      fs: {
        async readFile(uri) {
          if (!files.has(uri.fsPath)) throw new Error(`ENOENT: ${uri.fsPath}`);
          return Buffer.from(files.get(uri.fsPath), 'utf8');
        },
        async writeFile(uri, data) { files.set(uri.fsPath, Buffer.from(data).toString('utf8')); },
      },
      async openTextDocument(uri) {
        if (!files.has(uri.fsPath)) throw new Error(`ENOENT: ${uri.fsPath}`);
        return new Document(uri, files);
      },
      async applyEdit(edit) {
        // 後ろから当てる。前から当てると後続の offset がずれる。
        const byFile = new Map();
        for (const item of edit.edits) {
          if (!byFile.has(item.uri.fsPath)) byFile.set(item.uri.fsPath, []);
          byFile.get(item.uri.fsPath).push(item);
        }
        for (const [fsPath, items] of byFile) {
          const doc = new Document(Uri.file(fsPath), files);
          const spans = items.map(item => ({
            start: doc.offsetAt(item.range.start), end: doc.offsetAt(item.range.end), text: item.text,
          })).sort((a, b) => b.start - a.start);
          let text = files.get(fsPath);
          for (const span of spans) text = text.slice(0, span.start) + span.text + text.slice(span.end);
          files.set(fsPath, text);
        }
        return true;
      },
    },
    lm: {
      tools: options.tools || [],
      registerTool(name, tool) { state.registered.push(name); return { dispose() {} }; },
      async selectChatModels(selector) {
        const models = options.models || [makeModel(options.script || [])];
        return selector.family ? models.filter(m => m.family === selector.family) : models;
      },
      async invokeTool(name, { input }) {
        state.invoked.push({ name, input });
        const handler = options.invoke || (() => new LanguageModelToolResult([new LanguageModelTextPart(`result of ${name}`)]));
        return handler(name, input);
      },
    },
  };
  // script: 往復ごとのモデル応答。文字列は本文、{calls:[...]} はツール呼び出し。
  function makeModel(script) {
    let round = 0;
    return {
      id: 'm', family: 'test-family', name: 'Test Model', vendor: 'copilot', version: '1', maxInputTokens: 1000,
      async sendRequest(messages, opts) {
        const index = round++;
        state.requests.push({ messages: messages.slice(), tools: (opts && opts.tools) || [] });
        const step = script[index] === undefined ? '（台本切れ）' : script[index];
        const parts = [];
        if (typeof step === 'string') parts.push(new LanguageModelTextPart(step));
        else {
          if (step.text) parts.push(new LanguageModelTextPart(step.text));
          (step.calls || []).forEach((call, i) => parts.push(
            new LanguageModelToolCallPart(`c${index}-${i}`, call.name, call.input || {})));
        }
        return { text: (async function* () {})(), stream: (async function* () { yield* parts; })() };
      },
    };
  }
  return { vscode, state };
}

// extension.js を、差し替えた `vscode` で新しく読み込む。
function loadExtension(options) {
  const { vscode, state } = makeVscode(options);
  const fakePath = path.join(__dirname, '__vscode__');
  const original = Module._resolveFilename;
  Module._resolveFilename = function (request, ...rest) {
    return request === 'vscode' ? fakePath : original.call(this, request, ...rest);
  };
  require.cache[fakePath] = { id: fakePath, filename: fakePath, loaded: true, exports: vscode };
  const target = require.resolve('../extension.js');
  delete require.cache[target];
  try {
    const extension = require(target);
    return { extension, vscode, state };
  } finally {
    Module._resolveFilename = original;
    delete require.cache[fakePath];
  }
}

module.exports = { loadExtension };
