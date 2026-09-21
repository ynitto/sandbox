'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const routing = require('../src/main/requestRouting');

const tasks = [
  { id: 'daily-report', name: '日報', description: '前日の commit から日報を書く' },
  { id: 'lint', name: '静的検査', description: 'lint を回して直す' },
  { id: 'stock', name: '在庫', description: '倉庫の棚卸し' },
];
const flows = [{ id: 'release-check', name: 'リリース前点検', description: '点検して納品する' }];
const skills = [
  { name: 'api-designer', description: 'REST API の設計と OpenAPI', tags: ['api'] },
  { name: 'report-writer', description: '日報や週報をまとめて書く', tags: [] },
  { name: 'self-checking', description: '成果物の検証', tags: [] },
];
const text = '前月分の日報をまとめて';
const cands = routing.candidates({ text, tasks, flows, skills, repo: 'sandbox', attachments: ['a.md'], readonly: false });

test('振り分けを呼ばない理由は LLM の前に決まる', () => {
  assert.equal(routing.skipReason({ text }), '');
  assert.equal(routing.skipReason({ text, mode: 'off' }), 'off');
  assert.equal(routing.skipReason({ text, skillMode: 'manual' }), 'manual-skills');
  assert.equal(routing.skipReason({ text: '/sm daily-report' }), 'slash');
  assert.equal(routing.skipReason({ text: '/usr/bin/env は見ない' }), '', 'パスの行は呼び出しではない');
  assert.equal(routing.skipReason({ text: '変更をコミットする', quickRequests: [{ label: 'コミット', text: '変更をコミットする ' }] }), 'quick');
  assert.equal(routing.skipReason({ text: '   ' }), 'empty');
});

test('候補は一致する上位だけ渡し、明示のスキルは判定に訊かない', () => {
  assert.deepEqual(cands.tasks.map((t) => t.id), ['daily-report'], '一致しない候補（在庫・静的検査）は渡さない');
  assert.deepEqual(cands.flows, [], 'ワークフローは一致しない');
  assert.deepEqual(cands.skills.map((s) => s.name), ['report-writer'], '一致しないスキル（成果物の検証）は判定に訊かない');
  assert.deepEqual(cands.context, { repo: 'sandbox', attachments: ['a.md'], readonly: false });
  const explicit = routing.candidates({ text: 'api-designer で OpenAPI を設計して', skills });
  assert.equal(explicit.skills.some((s) => s.name === 'api-designer'), false, '依頼に名前が出たスキルは候補から外す（明示として確定）');
  const many = routing.candidates({ text: '日報', tasks: Array.from({ length: 30 }, (_, i) => ({ id: `t${i}`, name: `日報 ${i}`, description: '' })) });
  assert.equal(many.tasks.length, routing.LIMITS.tasks);
});

function fakeCapture(reply, seen = {}, localFile = '') {
  return async (name, args, opts) => {
    const exists = !!localFile && fs.existsSync(localFile);
    Object.assign(seen, { name, args, opts, fileExisted: exists, fileBody: exists ? fs.readFileSync(localFile, 'utf8') : '' });
    return typeof reply === 'function' ? reply() : reply;
  };
}

function tmpFile() {
  return path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-routing-')), 'routing', 's1.json');
}

test('決めた答えは候補で検証し、hold は流用先があるときだけ真', async () => {
  const seen = {};
  const file = tmpFile();
  const stdout = JSON.stringify({
    handling: { choice: 'task', confidence: 0.82 }, task: { choice: 'daily-report', confidence: 0.77 }, flow: null,
    skills: [{ name: 'report-writer', probability: 0.71 }, { name: 'self-checking', probability: 0.9 }],
    routine: { value: true, probability: 0.66 }, hold: true, stage: 'judge', abstained: [],
  });
  const result = await routing.route({ text, candidates: cands, cwd: '/repo', file, toHostPath: (p) => `/mnt/c${p}`, capture: fakeCapture({ ok: true, status: 0, stdout }, seen, file) });
  assert.deepEqual(seen.args.slice(0, 2), ['route', '--candidates']);
  assert.equal(seen.args[2].startsWith('/mnt/c'), true, '候補ファイルは CLI が動く側の表記で渡す');
  assert.equal(seen.opts.input, text, '依頼文は stdin');
  assert.equal(seen.opts.cwd, '/repo');
  assert.equal(seen.fileExisted, true);
  assert.deepEqual(JSON.parse(seen.fileBody), cands, '候補の 1 枚をそのまま書く');
  assert.equal(fs.existsSync(file), false, '候補ファイルは終わったら消す');
  assert.equal(result.decided, true);
  assert.equal(result.stage, 'judge');
  assert.deepEqual(result.handling, { choice: 'task', confidence: 0.82 });
  assert.deepEqual(result.target, { id: 'daily-report', name: '日報', confidence: 0.77 });
  assert.deepEqual(result.skills, [{ name: 'report-writer', probability: 0.71 }], '候補に無いスキル（渡していない self-checking）は捨てる');
  assert.equal(result.routine.value, true);
  assert.equal(result.hold, true);
  assert.equal(routing.information(result).title, '振り分け：タスク「日報」を流用できます');
  const held = routing.heldMessage(result, { text, attachments: [{ rel: 'a.md' }] });
  assert.equal(held.message.role, 'routing');
  assert.deepEqual(held.message.routing, { kind: 'task', id: 'daily-report', name: '日報', request: text, attachments: [{ rel: 'a.md' }], inputs: {} });
  assert.match(held.notice, /日報/);
});

test('流用先の id が候補に無ければ hold しない（converse へ）', async () => {
  const stdout = JSON.stringify({ handling: { choice: 'task', confidence: 0.9 }, task: { choice: 'ghost', confidence: 0.9 }, hold: true, stage: 'jev' });
  const result = await routing.route({ text, candidates: cands, file: tmpFile(), capture: fakeCapture({ ok: true, status: 0, stdout }) });
  assert.equal(result.decided, true);
  assert.equal(result.target, null);
  assert.equal(result.hold, false);
  assert.equal(routing.information(result).title, '振り分け：会話で実行（流用先を決められず）');
});

test('answer は読み取りだけ、converse と読み取り専用の依頼はそのまま', async () => {
  const answer = await routing.route({ text, candidates: cands, file: tmpFile(), capture: fakeCapture({ ok: true, status: 0, stdout: JSON.stringify({ handling: { choice: 'answer', confidence: 0.89 }, stage: 'judge', skills: [], routine: { value: false, probability: 0.1 } }) }) });
  assert.equal(routing.information(answer).title, '振り分け：答えるだけ（実行しない）');
  assert.equal(routing.information(answer).detail, '選択方法：ローカル判定 0.89');
  const readonly = await routing.route({ text, candidates: cands, file: tmpFile(), capture: fakeCapture({ ok: true, status: 0, stdout: JSON.stringify({ handling: null, stage: 'jev', skills: [] }) }) });
  assert.equal(readonly.decided, true);
  assert.equal(readonly.handling, null);
  assert.equal(routing.information(readonly).title, '振り分け：読み取り専用のまま');
});

test('決めず・旧版・無い・壊れた出力は従来の動きへ倒す', async () => {
  const undecided = await routing.route({ text, candidates: cands, file: tmpFile(), capture: fakeCapture({ ok: false, status: 1, stdout: JSON.stringify({ stage: null, reason: 'どの段も扱いを決められませんでした' }) }) });
  assert.equal(undecided.decided, false);
  assert.equal(undecided.skills, null, '判定が無いので bigram の従来へ');
  assert.equal(routing.information(undecided).title, '振り分け：決めず（会話で実行）');
  assert.match(routing.information(undecided).detail, /どの段も/);
  for (const reply of [
    { ok: false, status: 127, stdout: '', stderr: 'agent-herd: command not found' },
    { ok: false, status: 2, stdout: '', stderr: '未知のサブコマンド: \'route\'' },
    { ok: false, status: -1, stdout: '', error: 'spawn agent-herd ENOENT' },
  ]) {
    const result = await routing.route({ text, candidates: cands, file: tmpFile(), capture: fakeCapture(reply) });
    assert.equal(result.decided, false);
    assert.equal(result.reason, 'unavailable');
  }
  const thrown = await routing.route({ text, candidates: cands, file: tmpFile(), capture: fakeCapture(() => { throw Object.assign(new Error('nope'), { code: 'ENOENT' }); }) });
  assert.equal(thrown.reason, 'unavailable');
  const garbage = await routing.route({ text, candidates: cands, file: tmpFile(), capture: fakeCapture({ ok: true, status: 0, stdout: 'not json' }) });
  assert.equal(garbage.decided, false);
  assert.equal(garbage.reason, 'invalid');
  const unknownStage = await routing.route({ text, candidates: cands, file: tmpFile(), capture: fakeCapture({ ok: true, status: 0, stdout: JSON.stringify({ stage: 'audit', handling: { choice: 'answer' } }) }) });
  assert.equal(unknownStage.decided, false, '決定的な段は route に無い');
});

test('停止された振り分けは答えを使わない', async () => {
  const controller = new AbortController();
  const result = await routing.route({ text, candidates: cands, file: tmpFile(), signal: controller.signal,
    capture: fakeCapture(() => { controller.abort(); return { ok: true, status: 0, stdout: JSON.stringify({ stage: 'judge', handling: { choice: 'answer', confidence: 0.9 } }) }; }) });
  assert.equal(result.decided, false);
  assert.equal(result.reason, 'aborted');
});

test('流用時の入力値: 日付の語は決定的に写し、残りだけ extract に訊く', async () => {
  const seen = [];
  const capture = async (name, args, opts) => {
    seen.push({ name, args, opts });
    return { ok: true, status: 0, stdout: '{"target": "sandbox リポジトリ", "owner": null, "count": 3, "ghost": "x"}' };
  };
  const dateOnly = await routing.extractInputs({ text: '前月分の日報をまとめて', parameters: ['period', 'report_date'], capture });
  assert.deepEqual(dateOnly, { period: '@date:previous-month', report_date: '@date:previous-month' });
  assert.equal(seen.length, 0, '日付だけなら LLM を呼ばない');
  const mixed = await routing.extractInputs({ text: '昨日の日報を書いて。対象は sandbox リポジトリ', parameters: ['date', 'target', 'owner', 'count'], cwd: '/repo', capture });
  assert.equal(seen.length, 1);
  assert.deepEqual(seen[0].args.slice(0, 3), ['--purpose', 'extract', '--readonly']);
  assert.equal(seen[0].args[3], '-p');
  assert.match(seen[0].args[4], /キー: target, owner, count/, '日付のキーは訊かない');
  assert.match(seen[0].args[4], /昨日の日報を書いて/);
  assert.equal(seen[0].opts.cwd, '/repo');
  assert.deepEqual(mixed, { date: '@date:yesterday', target: 'sandbox リポジトリ', count: '3' }, 'null は落とし、宣言に無い ghost は受けない');
  assert.deepEqual(await routing.extractInputs({ text: 'x', parameters: [], capture }), {});
});

test('流用時の入力値: extract が無い・失敗・壊れた出力でも日付の分は残す', async () => {
  const text = '今月の集計をして';
  for (const capture of [
    async () => { throw Object.assign(new Error('nope'), { code: 'ENOENT' }); },
    async () => ({ ok: false, status: 1, stdout: '', stderr: 'ollama に接続できません' }),
    async () => ({ ok: true, status: 0, stdout: 'not json' }),
    async () => ({ ok: true, status: 0, stdout: '[1,2]' }),
  ]) {
    assert.deepEqual(await routing.extractInputs({ text, parameters: ['month', 'target'], capture }), { month: '@date:month' });
  }
  const dated = await routing.extractInputs({ text: '集計して', parameters: ['period'], capture: async () => ({ ok: true, status: 0, stdout: '{"period": "先月分"}' }) });
  assert.deepEqual(dated, { period: '@date:previous-month' }, 'LLM が返した日付の語も自動入力へ写す');
  assert.equal(routing.dateWord('先週の分'), '', '自動入力に無い語は写さない');
});

test('案内は写した入力値を持ち、実行情報に 1 行出す', async () => {
  const stdout = JSON.stringify({ handling: { choice: 'task', confidence: 0.82 }, task: { choice: 'daily-report', confidence: 0.77 }, hold: true, stage: 'judge' });
  const result = await routing.route({ text, candidates: cands, file: tmpFile(), capture: fakeCapture({ ok: true, status: 0, stdout }) });
  const held = routing.heldMessage(result, { text, inputs: { period: '@date:previous-month', target: 'sandbox' } });
  assert.deepEqual(held.message.routing.inputs, { period: '@date:previous-month', target: 'sandbox' });
  assert.equal(held.message.parts.information[1].title, '入力：period=前月 · target=sandbox');
  assert.equal(routing.heldMessage(result, { text }).message.parts.information.length, 1, '入力が無ければ行を足さない');
});
