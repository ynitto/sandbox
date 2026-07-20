'use strict';

// 監視担当（チーム運用）のテスト。
//   - actions.setTaskOwner … assignments.json（viewer 管理のサイドカー）への読み書き。
//     タスク状態ファイル（backlog/*.md）には一切触れないこと。
//   - project.readAssignments / effectiveOwner … 正規化と優先順位
//     （assignments.json ＞ backlog md の `- owner:`）。
//   - project.readProject … backlog / archive / needs（合成票含む）への owner 付与。
//   - renderer … 担当フィルタの純ロジックと配線・バッジの存在。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const project = require('../src/main/project');
const actions = require('../src/main/actions');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function tmpProject() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-owner-'));
  fs.mkdirSync(path.join(dir, 'backlog'), { recursive: true });
  return dir;
}

// --- actions.setTaskOwner ---

test('setTaskOwner は assignments.json を作り、担当とメンバーを記録する', () => {
  const dir = tmpProject();
  const res = actions.setTaskOwner(dir, 'T1', 'alice');
  assert.strictEqual(res.owner, 'alice');
  const saved = JSON.parse(fs.readFileSync(path.join(dir, 'assignments.json'), 'utf8'));
  assert.strictEqual(saved.tasks.T1, 'alice');
  assert.deepStrictEqual(saved.members, ['alice']);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('setTaskOwner は既存の割り当てを置き換え、メンバーは累積する', () => {
  const dir = tmpProject();
  actions.setTaskOwner(dir, 'T1', 'alice');
  actions.setTaskOwner(dir, 'T1', 'bob');
  const saved = JSON.parse(fs.readFileSync(path.join(dir, 'assignments.json'), 'utf8'));
  assert.strictEqual(saved.tasks.T1, 'bob');
  // ミーティングで一度出た名前は選択肢（datalist）に残す
  assert.deepStrictEqual([...saved.members].sort(), ['alice', 'bob']);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('setTaskOwner は空の owner で割り当てを解除する（メンバーは残る）', () => {
  const dir = tmpProject();
  actions.setTaskOwner(dir, 'T1', 'alice');
  actions.setTaskOwner(dir, 'T1', '');
  const saved = JSON.parse(fs.readFileSync(path.join(dir, 'assignments.json'), 'utf8'));
  assert.strictEqual(saved.tasks.T1, undefined);
  assert.deepStrictEqual(saved.members, ['alice']);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('setTaskOwner は不正なタスク ID（パス断片）を拒否する', () => {
  const dir = tmpProject();
  assert.throws(() => actions.setTaskOwner(dir, '../evil', 'alice'), /不正なタスク ID/);
  assert.throws(() => actions.setTaskOwner(dir, '', 'alice'), /不正なタスク ID/);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('setTaskOwner はタスク状態ファイルに触れない', () => {
  const dir = tmpProject();
  const taskFile = path.join(dir, 'backlog', 'T1.md');
  fs.writeFileSync(taskFile, '## T1: タスク\n- status: ready\n', 'utf8');
  const before = fs.readFileSync(taskFile, 'utf8');
  actions.setTaskOwner(dir, 'T1', 'alice');
  assert.strictEqual(fs.readFileSync(taskFile, 'utf8'), before);
  fs.rmSync(dir, { recursive: true, force: true });
});

// --- project.readAssignments / effectiveOwner ---

test('readAssignments は壊れた・欠けた形を正規化する', () => {
  const dir = tmpProject();
  assert.deepStrictEqual(project.readAssignments(dir), { members: [], tasks: {} });
  fs.writeFileSync(path.join(dir, 'assignments.json'), '{broken', 'utf8');
  assert.deepStrictEqual(project.readAssignments(dir), { members: [], tasks: {} });
  fs.writeFileSync(
    path.join(dir, 'assignments.json'),
    JSON.stringify({ members: [' alice ', ''], tasks: { T1: ' bob ', T2: '' } }),
    'utf8'
  );
  const norm = project.readAssignments(dir);
  assert.deepStrictEqual(norm.members, ['alice']);
  assert.deepStrictEqual(norm.tasks, { T1: 'bob' });
  fs.rmSync(dir, { recursive: true, force: true });
});

test('effectiveOwner は assignments.json を md の owner: より優先する', () => {
  const t = { id: 'T1', extra: { owner: 'bob' } };
  assert.strictEqual(project.effectiveOwner({ members: [], tasks: { T1: 'alice' } }, t), 'alice');
  assert.strictEqual(project.effectiveOwner({ members: [], tasks: {} }, t), 'bob');
  assert.strictEqual(project.effectiveOwner({ members: [], tasks: {} }, { id: 'T2', extra: {} }), '');
});

// --- readProject への付与（backlog / archive / 合成 needs） ---

test('readProject は backlog・合成 needs へ owner を付与し assignments を返す', () => {
  const dir = tmpProject();
  fs.writeFileSync(
    path.join(dir, 'backlog', 'T1.md'),
    '## T1: 担当ありのタスク\n- status: ready\n- owner: bob\n',
    'utf8'
  );
  // blocked タスク（needs ファイル無し）→ 合成票が立ち、owner が載るはず
  fs.writeFileSync(
    path.join(dir, 'backlog', 'T2.md'),
    '## T2: 止まっているタスク\n- status: blocked\n',
    'utf8'
  );
  fs.writeFileSync(
    path.join(dir, 'assignments.json'),
    JSON.stringify({ members: ['carol'], tasks: { T2: 'alice' } }),
    'utf8'
  );
  const snap = project.readProject(dir, {});
  const t1 = snap.backlog.find((t) => t.id === 'T1');
  const t2 = snap.backlog.find((t) => t.id === 'T2');
  assert.strictEqual(t1.owner, 'bob'); // md の owner:（未知キー保持）フォールバック
  assert.strictEqual(t2.owner, 'alice'); // assignments.json が優先
  // メンバーは登録済み + 割り当てから合流
  assert.deepStrictEqual([...snap.assignments.members].sort(), ['alice', 'bob', 'carol']);
  const need = snap.needs.find((n) => String(n.taskId || n.id) === 'T2');
  assert.ok(need, '合成された要対応票が見つかりません');
  assert.strictEqual(need.owner, 'alice');
  fs.rmSync(dir, { recursive: true, force: true });
});

// --- renderer（担当フィルタの純ロジックと配線） ---

const renderer = require('./helpers/renderer-src').read();
function grab(name) {
  const at = renderer.indexOf(`function ${name}(`);
  assert.ok(at >= 0, `renderer に function ${name} が見つかりません`);
  let i = at + `function ${name}`.length;
  let depth = 0;
  while (i < renderer.length && renderer[i] !== '{') i++;
  for (; i < renderer.length; i++) {
    if (renderer[i] === '{') depth++;
    else if (renderer[i] === '}') {
      depth--;
      if (depth === 0) return renderer.slice(at, i + 1);
    }
  }
  throw new Error(`function ${name} の閉じ括弧が見つかりません`);
}

// eslint-disable-next-line no-new-func
const filterTasksByOwner = new Function(
  `const OWNER_UNASSIGNED = '__none__'; ${grab('filterTasksByOwner')}; return filterTasksByOwner;`
)();
// eslint-disable-next-line no-new-func
const ownerFilterChoices = new Function(
  `const OWNER_UNASSIGNED = '__none__'; ${grab('ownerFilterChoices')}; return ownerFilterChoices;`
)();

test('filterTasksByOwner は 全員／担当者名／未担当 で正しく絞る', () => {
  const tasks = [
    { id: 'T1', owner: 'alice' },
    { id: 'T2', owner: 'bob' },
    { id: 'T3', owner: '' },
    { id: 'T4' },
  ];
  assert.strictEqual(filterTasksByOwner(tasks, '').length, 4);
  assert.deepStrictEqual(filterTasksByOwner(tasks, 'alice').map((t) => t.id), ['T1']);
  assert.deepStrictEqual(filterTasksByOwner(tasks, '__none__').map((t) => t.id), ['T3', 'T4']);
});

test('ownerFilterChoices はメンバーがいるときだけチップを出す', () => {
  assert.deepStrictEqual(ownerFilterChoices(null), []);
  assert.deepStrictEqual(ownerFilterChoices({ assignments: { members: [] } }), []);
  const chips = ownerFilterChoices({ assignments: { members: ['alice', 'bob'] } });
  assert.deepStrictEqual(chips.map(([v]) => v), ['', 'alice', 'bob', '__none__']);
});

test('renderer は担当フィルタ・割り当て UI・要対応バッジを配線している', () => {
  assert.ok(renderer.includes('data-owner-filter'), '担当チップの data 属性がありません');
  assert.ok(renderer.includes('state.backlogOwner'), '担当フィルタの state がありません');
  assert.ok(renderer.includes('api.setTaskOwner'), '割り当て API の呼び出しがありません');
  assert.ok(renderer.includes('btn-task-owner'), 'タスクダイアログの保存ボタンがありません');
  assert.ok(renderer.includes('ownerBadgeHtml(n.owner)'), '要対応詳細の担当バッジがありません');
  assert.ok(renderer.includes('ownerBadgeHtml(item.owner)'), '要対応一覧の担当バッジがありません');
});

console.log(`\n${passed} tests passed`);
