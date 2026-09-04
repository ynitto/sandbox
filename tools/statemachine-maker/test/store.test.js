'use strict';

// `.statemachine/<識別名>/` の読み書き。書き先はルート + 識別名からだけ組み立て、既存の資料を壊さない。

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const store = require('../src/main/store');

function tmpRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'smk-store-'));
}

const SPEC = {
  name: 'テスト', machine: 'demo', purpose: '試す',
  steps: [{ kind: 'agent', title: '考える', detail: '考える' }, { kind: 'command', target: 'python check.py', check: 'python verify.py' }],
};

test('保存 → 一覧 → 読み戻し', () => {
  const root = tmpRoot();
  assert.deepStrictEqual(store.list(root), []);
  const res = store.save(root, SPEC);
  assert.strictEqual(res.dir, path.join(root, '.statemachine', 'demo'));
  assert.deepStrictEqual(res.written.sort(), ['actions/step_1.md', 'actions/step_2.md', 'maker.json', 'workflow.yaml']);
  assert.ok(!fs.existsSync(path.join(res.dir, 'maker.json.prev')));
  const list = store.list(root);
  assert.deepStrictEqual(list.map((m) => [m.machine, m.name, m.description, m.maker]), [['demo', 'テスト', '試す', true]]);
  const read = store.read(root, 'demo');
  assert.deepStrictEqual(read.raw.steps.map((s) => [s.id, s.kind, s.title]), [['step_1', 'agent', '考える'], ['step_2', 'command', '']]);
  assert.strictEqual(read.raw.machine, 'demo');
  assert.deepStrictEqual(read.warnings, []);
  assert.ok(store.exists(root, 'demo') && !store.exists(root, 'nope'));
});

test('工程を消して保存すると、このツールが書いた古い action だけ消え、手で置いた資料は残る', () => {
  const root = tmpRoot();
  store.save(root, SPEC);
  const dir = path.join(root, '.statemachine', 'demo');
  fs.writeFileSync(path.join(dir, 'actions', 'notes.md'), '手で書いたメモ');
  fs.writeFileSync(path.join(dir, 'README.md'), 'readme');
  store.save(root, { ...SPEC, steps: [SPEC.steps[0]] });
  assert.ok(!fs.existsSync(path.join(dir, 'actions', 'step_2.md')), '工程に無くなった action は消える');
  assert.ok(fs.existsSync(path.join(dir, 'actions', 'notes.md')) && fs.existsSync(path.join(dir, 'README.md')));
});

test('不正な識別名や検証エラーでは書かない', () => {
  const root = tmpRoot();
  assert.throws(() => store.save(root, { ...SPEC, machine: '../escape' }), /識別名が不正/);
  assert.throws(() => store.machineDir(root, '..'), /識別名/);
  assert.throws(() => store.save(root, { ...SPEC, machine: '' }), /識別名を入力/);
  assert.throws(() => store.save(root, { ...SPEC, steps: [{ kind: 'command', target: 'a', check: 'x | y' }] }), /シェル記号/);
  assert.deepStrictEqual(fs.readdirSync(root), []);
});

test('スキルの例（手書きの定義）を読み戻して書き直しても、保持した部分が残る', () => {
  const root = tmpRoot();
  const src = path.join(__dirname, '..', '..', '..', '.github', 'skills', 'statemachine-use', 'examples', 'gated_implement.yaml');
  if (!fs.existsSync(src)) return;
  const dir = path.join(root, '.statemachine', 'gated');
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'workflow.yaml'), fs.readFileSync(src, 'utf8'));
  const read = store.read(root, 'gated');
  assert.deepStrictEqual(read.raw.steps.map((s) => s.id), ['implement', 'review']);
  assert.strictEqual(read.raw.steps[0].check, 'python3 -m pytest tests/test_humansize.py -q');
  // `startswith:verdict:APPROVED` は結果の名前ではなく式（別のキーを見ている）。
  // 名前に読み替えると書き戻しで別物になるので、式のまま持つ。
  assert.deepStrictEqual(read.raw.steps[1].outcomes, [
    { when: 'rule', rule: 'startswith:verdict:APPROVED', to: 'next' },
    { when: 'rule', rule: 'startswith:verdict:CHANGES', to: 'abort' }]);
  const saved = store.save(root, read.raw);
  const again = store.read(root, 'gated');
  assert.strictEqual(again.workflow.states.implement.write, 'src/humansize.py', 'write の割付は保持される');
  assert.strictEqual(again.workflow.states.implement.check_on_exhausted, 'escalate');
  assert.ok(saved.written.includes('actions/implement.md'));
});
