'use strict';

// authoring.js（新規作成・編集層）の軽量テスト。追加依存なしで
// `node test/authoring.test.js` で走る。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const authoring = require('../src/main/authoring');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

// --- buildCharter ---
test('buildCharter は見出しと箇条書きを持つ charter.md を作る', () => {
  const text = authoring.buildCharter({
    name: 'demo',
    goal: 'CSV を要約する CLI を完成させる',
    constraints: 'Python 3.9 のみ\n後方互換を壊さない',
    deliverables: 'report.py',
    acceptance: 'pytest -q tests/\naccept: README に使用例がある',
  });
  assert.ok(text.startsWith('# Charter: demo\n'), 'Charter 見出し');
  assert.match(text, /## goal\nCSV を要約する CLI を完成させる/);
  assert.match(text, /## constraints\n- Python 3\.9 のみ\n- 後方互換を壊さない/);
  assert.match(text, /## acceptance/);
  assert.match(text, /- pytest -q tests\//);
  assert.match(text, /- accept: README に使用例がある/);
  // 空でも core セクションの見出しは残す
  for (const key of ['goal', 'constraints', 'assumptions', 'deliverables', 'acceptance', 'repos', 'links']) {
    assert.ok(text.includes(`## ${key}`), `## ${key} が存在する`);
  }
});

test('buildCharter は repos を charter の ## repos に構造化して書く', () => {
  const text = authoring.buildCharter({
    name: 'x',
    repos: [{ name: 'app', url: 'git@h:team/app.git', owns: 'apps/**', base: 'main', desc: '本体' }],
  });
  assert.match(text, /## repos\n[\s\S]*- app = git@h:team\/app\.git/);
  assert.match(text, /\n {2}- owns: apps\/\*\*/);
  assert.match(text, /\n {2}- base: main/);
  assert.match(text, /\n {2}- desc: 本体/);
});

// --- exportReposJson（agent-project の export_repo_registry と同じ形）---
test('exportReposJson は _meta 付き・owns を配列化・キーをソートする', () => {
  const json = authoring.exportReposJson([
    { name: 'app', url: 'git@h:team/app.git', owns: 'apps/**, services/**', base: 'main' },
    { name: 'lib', url: 'git@h:team/lib.git' }, // owns 無し → 参照
  ]);
  const data = JSON.parse(json);
  assert.ok(data._meta && data._meta.generated_from === 'charter.md ## repos');
  assert.deepStrictEqual(data.app.owns, ['apps/**', 'services/**']);
  assert.strictEqual(data.app.base, 'main');
  assert.strictEqual(data.lib.url, 'git@h:team/lib.git');
  assert.ok(!('owns' in data.lib), 'owns 無しの repo は owns キーを持たない');
  assert.ok(json.endsWith('\n'), '末尾改行');
  // sort_keys 相当: トップレベルは _meta が先頭（アルファベット順で _ が先）
  assert.ok(json.indexOf('"_meta"') < json.indexOf('"app"'));
});

// --- createProject ---
test('createProject は <親フォルダ>/<name>/ に charter.md と repos.json を作る', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-auth-'));
  try {
    const res = authoring.createProject({
      root: tmp,
      name: 'my-proj',
      goal: '目標',
      acceptance: 'pytest -q',
      repos: [{ name: 'app', url: 'git@h:team/app.git', owns: 'apps/**' }],
    });
    assert.strictEqual(res.dir, path.join(tmp, 'my-proj'));
    assert.ok(fs.existsSync(res.charterFile), 'charter.md 作成');
    assert.ok(fs.existsSync(res.reposFile), 'repos.json 作成');
    assert.ok(fs.existsSync(path.join(res.dir, 'inbox')), 'inbox/ 作成');
    const charter = fs.readFileSync(res.charterFile, 'utf8');
    assert.match(charter, /# Charter: my-proj/);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('createProject は不正なプロジェクト名を拒否する', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-auth-'));
  try {
    for (const bad of ['', '.', '..', 'a/b', 'a b', 'x\\y']) {
      assert.throws(() => authoring.createProject({ root: tmp, name: bad }), /プロジェクト名/);
    }
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('createProject は既存 charter.md を上書きしない', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-auth-'));
  try {
    authoring.createProject({ root: tmp, name: 'dup', goal: 'g' });
    assert.throws(() => authoring.createProject({ root: tmp, name: 'dup' }), /すでに charter\.md/);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

// --- readProjectFile / writeProjectFile（ホワイトリスト・JSON 検証）---
test('read/writeProjectFile はホワイトリスト外を拒否する', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-auth-'));
  try {
    assert.throws(() => authoring.readProjectFile(tmp, 'backlog/T1.md'), /編集できない/);
    assert.throws(() => authoring.writeProjectFile(tmp, '../evil.md', 'x'), /編集できない|不正/);
    // charter.md は許可
    authoring.writeProjectFile(tmp, 'charter.md', '# Charter: t\n');
    const info = authoring.readProjectFile(tmp, 'charter.md');
    assert.ok(info.exists && info.content.includes('# Charter: t'));
    // rules.md（プロジェクトルール）も人が書く入力として許可
    authoring.writeProjectFile(tmp, 'rules.md', '- テストは pytest -q で回す\n');
    const rules = authoring.readProjectFile(tmp, 'rules.md');
    assert.ok(rules.exists && rules.content.includes('pytest -q'));
    assert.match(rules.label, /プロジェクトルール/);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('writeProjectFile は不正な repos.json を拒否する', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-auth-'));
  try {
    assert.throws(() => authoring.writeProjectFile(tmp, 'repos.json', '{ not json'), /JSON/);
    authoring.writeProjectFile(tmp, 'repos.json', '{"_meta":{"generated_from":"charter.md ## repos"}}');
    const info = authoring.readProjectFile(tmp, 'repos.json');
    assert.strictEqual(info.generated, true, '_meta 付きは generated 判定');
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

// --- deleteCharterVersion ---
test('deleteCharterVersion は未使用の計画バージョンだけを削除する', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-auth-'));
  try {
    const dir = path.join(tmp, 'project');
    fs.mkdirSync(path.join(dir, 'charters'), { recursive: true });
    fs.writeFileSync(path.join(dir, 'charters', 'v2.md'), '# Charter: v2\n');
    const res = authoring.deleteCharterVersion(dir, 'v2');
    assert.strictEqual(res.name, 'v2');
    assert.ok(!fs.existsSync(path.join(dir, 'charters', 'v2.md')));
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('deleteCharterVersion は関連する作業や完了履歴があれば削除しない', () => {
  for (const folder of ['backlog', 'archive']) {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-auth-'));
    try {
      const dir = path.join(tmp, 'project');
      fs.mkdirSync(path.join(dir, 'charters'), { recursive: true });
      fs.mkdirSync(path.join(dir, folder), { recursive: true });
      fs.writeFileSync(path.join(dir, 'charters', 'v2.md'), '# Charter: v2\n');
      fs.writeFileSync(path.join(dir, folder, 'T1.md'), '## T1\n- charter: v2\n');
      assert.throws(() => authoring.deleteCharterVersion(dir, 'v2'), /関連する作業.*削除できません/);
      assert.ok(fs.existsSync(path.join(dir, 'charters', 'v2.md')), `${folder} 使用中の版を保持`);
    } finally {
      fs.rmSync(tmp, { recursive: true, force: true });
    }
  }
});

test('deleteCharterVersion は不正名と存在しない版を明示的に拒否する', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-auth-'));
  try {
    for (const bad of ['', '.', '..', '../v2', 'a/b', 'a b']) {
      assert.throws(() => authoring.deleteCharterVersion(tmp, bad), /バージョン名/);
    }
    assert.throws(() => authoring.deleteCharterVersion(tmp, 'missing'), /見つかりません/);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

console.log(`\n${passed} passed`);
