'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { loadExtension } = require('./fake-vscode');

const READ = { name: 'copilot_readFile', description: 'read', inputSchema: {} };
const EDIT = { name: 'bridge_replaceString', description: 'edit', inputSchema: {} };
const call = (name, input) => ({ calls: [{ name, input }] });

async function run(options, body) {
  const { extension, state } = loadExtension(options);
  const events = [];
  const result = await extension._internal.runAgent(
    { messages: [{ role: 'user', content: '依頼' }], tools: [READ.name], ...body }, {}, e => events.push(e));
  return { result, events, state };
}

// --- 往復の予算 ------------------------------------------------------------------

test('a plain answer ends the loop in one round and is not exhausted', async () => {
  const { result } = await run({ tools: [READ], script: ['答え'] });
  assert.equal(result.text, '答え');
  assert.equal(result.rounds, 1);
  assert.equal(result.exhausted, false);
});

test('the final round is sent without tools so the turn ends with words, not a 409', async () => {
  const script = [call(READ.name, { filePath: '/ws/a' }), call(READ.name, { filePath: '/ws/a' }), '途中まで'];
  const { result, state, events } = await run({ tools: [READ], script }, { maxRounds: 3 });
  assert.equal(result.exhausted, true);
  assert.equal(result.rounds, 3);
  assert.equal(result.text, '途中まで');
  assert.deepEqual(state.requests.map(r => r.tools.length), [1, 1, 0], '最後だけツール無し');
  assert.equal(state.invoked.length, 2);
  assert.ok(events.some(e => e.tool === READ.name && e.ok));
});

test('the model is told how many rounds it has and to batch independent calls', async () => {
  const { state } = await run({ tools: [READ], script: ['ok'] }, { maxRounds: 7 });
  const head = state.requests[0].messages[0].content;
  assert.match(head, /最大 7 回/);
  assert.match(head, /1 回の応答にまとめて/);
  assert.match(head, /^いま開いているワークスペース:\n- \/ws\n/);
  assert.ok(head.endsWith('依頼'), '申し送りは依頼文の頭に付く');
});

test('edit guidance appears only when the edit tool is offered', async () => {
  const withEdit = await run({ tools: [READ, EDIT], script: ['ok'] }, { tools: [READ.name, EDIT.name] });
  assert.match(withEdit.state.requests[0].messages[0].content, /replaceAll: true/);
  const readOnly = await run({ tools: [READ], script: ['ok'] });
  assert.doesNotMatch(readOnly.state.requests[0].messages[0].content, /replaceAll/);
});

test('a budget warning rides inside the last tool result, not as a separate message', async () => {
  const script = [call(READ.name, {}), call(READ.name, {}), call(READ.name, {}), '終'];
  const { state } = await run({ tools: [READ], script }, { maxRounds: 4 });
  const textOf = request => {
    const last = request.messages[request.messages.length - 1];
    return last.content.map(part => part.content.map(c => c.value).join('')).join('');
  };
  // 往復 1 の後: 残り 3 → ツールを使える往復は残り 2
  assert.match(textOf(state.requests[1]), /残り 2 回/);
  // 往復 2 の後: 残り 2 → 残り 1
  assert.match(textOf(state.requests[2]), /残り 1 回/);
  // 往復 3 の後: 次が最後（ツール無し）
  assert.match(textOf(state.requests[3]), /最後の往復で、ツールは使えません/);
  // 別メッセージとして足していない: user(結果) の中身は ToolResultPart だけ。
  for (const request of state.requests.slice(1)) {
    const last = request.messages[request.messages.length - 1];
    assert.equal(last.role, 'user');
    assert.ok(last.content.every(part => part.callId));
  }
});

test('no warning while the budget is comfortable', async () => {
  const { state } = await run({ tools: [READ], script: [call(READ.name, {}), '終'] }, { maxRounds: 25 });
  const last = state.requests[1].messages[state.requests[1].messages.length - 1];
  const text = last.content.map(part => part.content.map(c => c.value).join('')).join('');
  assert.doesNotMatch(text, /注意/);
});

test('maxRounds below the minimum is raised so tools are offered at least once', async () => {
  const { state } = await run({ tools: [READ], script: [call(READ.name, {}), '終'] }, { maxRounds: 1 });
  assert.equal(state.requests[0].tools.length, 1);
  assert.equal(state.requests[1].tools.length, 0);
});

test('a tool the model was not offered is refused but the loop goes on', async () => {
  const { result, state } = await run(
    { tools: [READ, EDIT], script: [call(EDIT.name, {}), '諦めた'] });
  assert.equal(state.invoked.length, 0);
  assert.equal(result.text, '諦めた');
});

test('repository instructions are put in front of the task', async () => {
  const files = { '/ws/.github/copilot-instructions.md': '# 決まり\nテストを先に書く\n' };
  const { state } = await run({ tools: [READ], script: ['ok'], files });
  const head = state.requests[0].messages[0].content;
  assert.match(head, /リポジトリの申し送り（\/ws\/\.github\/copilot-instructions\.md）:\n# 決まり\nテストを先に書く/);
});

test('long instructions are cut and the cut is announced', async () => {
  const files = { '/ws/AGENTS.md': 'x'.repeat(9000) };
  const { state } = await run({ tools: [READ], script: ['ok'], files });
  const head = state.requests[0].messages[0].content;
  assert.match(head, /8000 文字で切りました。全体は 9000 文字/);
});

test('models are listed with their limits', async () => {
  const { extension } = loadExtension({ script: ['ok'] });
  const { models } = await extension._internal.listModels();
  assert.deepEqual(models, [{
    id: 'm', family: 'test-family', name: 'Test Model', vendor: 'copilot', version: '1', maxInputTokens: 1000,
  }]);
});

// --- bridge_replaceString ---------------------------------------------------------

async function replace(files, input) {
  const { extension, state } = loadExtension({ files });
  const tool = extension._internal.BRIDGE_TOOLS.bridge_replaceString;
  try {
    const result = await tool.invoke({ input });
    return { text: result.content[0].value, files: state.files };
  } catch (error) {
    return { error: error.message, files: state.files };
  }
}

const SOURCE = 'def old():\n    return 1\n\n\ndef caller():\n    return old()\n';

test('a unique match is replaced and the edited region comes back numbered', async () => {
  const { text, files } = await replace({ '/ws/a.py': SOURCE },
    { filePath: '/ws/a.py', oldString: 'def old():', newString: 'def renamed():' });
  assert.equal(files.get('/ws/a.py'), SOURCE.replace('def old():', 'def renamed():'));
  assert.match(text, /Replaced 1 occurrence in \/ws\/a\.py \(now lines 1-1\)/);
  assert.match(text, /1\| def renamed\(\):\n2\|     return 1\n3\| /);
});

test('several matches fail with their line numbers unless replaceAll is set', async () => {
  const { error, files } = await replace({ '/ws/a.py': SOURCE },
    { filePath: '/ws/a.py', oldString: 'old(', newString: 'renamed(' });
  assert.match(error, /2 箇所と一致します（1, 6 行目）/);
  assert.match(error, /replaceAll: true/);
  assert.equal(files.get('/ws/a.py'), SOURCE, '失敗時は何も変えない');
  const all = await replace({ '/ws/a.py': SOURCE },
    { filePath: '/ws/a.py', oldString: 'old(', newString: 'renamed(', replaceAll: true });
  assert.equal(all.files.get('/ws/a.py'), 'def renamed():\n    return 1\n\n\ndef caller():\n    return renamed()\n');
  assert.match(all.text, /Replaced 2 occurrences .* lines 1, 6/);
});

test('an indentation-only mismatch is diagnosed with the real lines', async () => {
  const { error } = await replace({ '/ws/a.py': SOURCE },
    { filePath: '/ws/a.py', oldString: 'def caller():\n  return old()', newString: 'x' });
  assert.match(error, /空白・インデントの違いだけ/);
  assert.match(error, /5\| def caller\(\):\n6\|     return old\(\)/);
});

test('a first line that exists but a continuation that does not shows the real continuation', async () => {
  const { error } = await replace({ '/ws/a.py': SOURCE },
    { filePath: '/ws/a.py', oldString: 'def caller():\n    return new()', newString: 'x' });
  assert.match(error, /1 行目は 5 行目にありますが、続きが一致しません/);
  assert.match(error, /6\|     return old\(\)/);
});

test('a string that is nowhere points at re-reading the file', async () => {
  const { error } = await replace({ '/ws/a.py': SOURCE },
    { filePath: '/ws/a.py', oldString: 'nothing like this', newString: 'x' });
  assert.match(error, /読み直して/);
});

test('writes outside the workspace are refused', async () => {
  const { error } = await replace({ '/etc/hosts': 'x' },
    { filePath: '/etc/hosts', oldString: 'x', newString: 'y' });
  assert.match(error, /ワークスペースの外/);
});

test('an oldString that starts with a blank line is still located by its first real line', async () => {
  const { error } = await replace({ '/ws/a.py': SOURCE },
    { filePath: '/ws/a.py', oldString: '\ndef caller():\n    return new()', newString: 'x' });
  assert.match(error, /1 行目は 5 行目にありますが/);
  assert.match(error, /4\| \n5\| def caller\(\):/);
});
