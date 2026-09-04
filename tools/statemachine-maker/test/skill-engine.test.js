'use strict';

// 結合: このツールが書いた定義を、statemachine-use スキルの本物のスクリプト（run_machine.py --dry-run）
// が受け入れること。書式の正典はスキル側なので、ここが最終の判定になる。
// Python か PyYAML が無い環境では飛ばす（CI は python を持つ）。

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');
const store = require('../src/main/store');
const model = require('../src/main/model');

const REPO = path.join(__dirname, '..', '..', '..');
const SKILL = path.join(REPO, '.github', 'skills', 'statemachine-use');
const RUNNER = path.join(SKILL, 'scripts', 'run_machine.py');

function python() {
  for (const cmd of ['python3', 'python']) {
    const res = spawnSync(cmd, ['-c', 'import yaml'], { encoding: 'utf8' });
    if (res.status === 0) return cmd;
  }
  return '';
}

function dryRun(py, workflow, cwd) {
  return spawnSync(py, [RUNNER, workflow, '--dry-run'], { cwd, encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
}

test('生成した定義はスキルの run_machine.py --dry-run を通る', (t) => {
  const py = python();
  if (!py || !fs.existsSync(RUNNER)) { t.skip('python + PyYAML かスキルのスクリプトが無い'); return; }
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'smk-engine-'));
  const res = store.save(root, {
    name: '月次の勤怠集計', machine: 'kintai', purpose: '集計して差し戻し候補を出す',
    steps: [
      { kind: 'windows', title: '集計を出力', target: '勤怠管理', detail: '「出力」を押す', check: 'winauto wait name:=完了 --app 勤怠管理' },
      { kind: 'browser', target: 'https://intra.example/list', detail: '一覧を読む {{month}}',
        recorded: [{ op: 'fill', target: "getByRole('textbox', { name: '対象月' })", role: 'textbox', label: '対象月', value: '{{month}}', example: '2026-09' }] },
      { kind: 'skill', target: 'redmine-use', detail: 'チケットを取る', check: 'python scripts/check.py' },
      { kind: 'agent', title: '判断', detail: '差し戻しが要るか', outcomes: [{ label: 'APPROVED', to: 'done' }, { label: 'RETRY', to: 'step:2' }, { label: 'NG', to: 'abort' }] },
    ],
  });
  const out = dryRun(py, path.join(res.dir, 'workflow.yaml'), root);
  assert.strictEqual(out.status, 0, `${out.stdout}\n${out.stderr}`);
  assert.ok(out.stdout.includes('検証成功'), out.stdout);
  assert.ok(out.stdout.includes('step_4 → step_2'));
});

test('スキルの例を読み戻して書き直した定義も --dry-run を通る（保持した部分を壊さない）', (t) => {
  const py = python();
  if (!py || !fs.existsSync(RUNNER)) { t.skip('python + PyYAML かスキルのスクリプトが無い'); return; }
  const examples = path.join(SKILL, 'examples');
  for (const name of fs.readdirSync(examples).filter((f) => f.endsWith('.yaml'))) {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'smk-example-'));
    const machine = name.replace(/\.yaml$/, '');
    const dir = path.join(root, '.statemachine', machine);
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, 'workflow.yaml'), fs.readFileSync(path.join(examples, name), 'utf8'));
    // 例の中には actions/*.md を前提にするものがある。読み戻しは本文が無くても落ちない。
    const read = store.read(root, machine);
    assert.ok(read.raw.steps.length >= 1, name);
    // 手で書いた定義でも、工程はすべて画面で直せる形に写る
    assert.deepStrictEqual(read.raw.steps.filter((s) => s.rawTransitions).map((s) => s.id), [],
      `${name}: 画面で直せない工程が残っている`);
    assert.deepStrictEqual(read.warnings, [], `${name}: ${read.warnings.join(' / ')}`);
    const raw = { ...read.raw, name: read.raw.name || machine };
    for (const s of raw.steps) if (!s.detail) s.detail = `（${s.id}）`;
    store.save(root, raw);
    const out = dryRun(py, path.join(dir, 'workflow.yaml'), root);
    assert.strictEqual(out.status, 0, `${name}\n${out.stdout}\n${out.stderr}`);
    const before = require('yaml').parse(fs.readFileSync(path.join(examples, name), 'utf8'));
    const after = read.workflow && require('yaml').parse(fs.readFileSync(path.join(dir, 'workflow.yaml'), 'utf8'));
    assert.deepStrictEqual(Object.keys(after.states).sort(), Object.keys(before.states).sort(), `${name}: ステートを増減しない`);
    assert.strictEqual(after.transitions.length, before.transitions.length, `${name}: 遷移の数を変えない`);
  }
});

test('構造検査（JS）と engine の判定は一致する: 壊れた定義は両方が落とす', (t) => {
  const py = python();
  if (!py || !fs.existsSync(RUNNER)) { t.skip('python + PyYAML かスキルのスクリプトが無い'); return; }
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'smk-broken-'));
  const dir = path.join(root, '.statemachine', 'broken');
  fs.mkdirSync(dir, { recursive: true });
  const text = ['name: broken', 'initial_state: a', 'states:', '  a:', '    action: x', '  done:', '    terminal: true',
    'transitions:', '  - from: a', '    to: done', '    condition_rule: "equals:check_ok:true"', ''].join('\n');
  fs.writeFileSync(path.join(dir, 'workflow.yaml'), text);
  const errors = model.validateWorkflow(require('yaml').parse(text));
  assert.ok(errors.some((e) => e.includes('check がありません')));
  const out = dryRun(py, path.join(dir, 'workflow.yaml'), root);
  assert.notStrictEqual(out.status, 0);
});
