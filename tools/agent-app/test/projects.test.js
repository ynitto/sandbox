'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const projects = require('../src/main/projects');
const projectImport = require('../src/main/projectImport');

const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-projects-'));

test('URL は scp 形式と https を同じものとして比べる（資格情報・.git・末尾の / を落とす）', () => {
  const a = projects.normalizeUrl('git@Example.com:team/app.git');
  assert.equal(a, 'example.com/team/app');
  assert.equal(projects.normalizeUrl('https://user:tok@example.com/team/app.git/'), a);
  assert.equal(projects.normalizeUrl('ssh://git@example.com:2222/team/app'), a);
  assert.equal(projects.repoLabel('git@example.com:team/app.git'), 'app');
});

test('主は 1 つだけに正規化する（無ければ最初の作業、2 つ目以降は作業へ）', () => {
  const p = projects.normalize({ name: 'x', repos: [
    { url: 'a', role: 'reference' }, { url: 'b', role: 'work' }, { url: 'c', role: 'work' }, { url: 'b.git', role: 'work' },
  ] });
  assert.deepEqual(p.repos.map((r) => [r.url, r.role]), [['a', 'reference'], ['b', 'main'], ['c', 'work']]);
  const twice = projects.normalize({ repos: [{ url: 'a', role: 'main' }, { url: 'b', role: 'main' }] });
  assert.deepEqual(twice.repos.map((r) => r.role), ['main', 'work']);
});

test('定義ファイルは小さく保つ（状態を持たず、大きすぎるものは読まない・書かない）', () => {
  const body = projects.serialize({ name: '受注', repos: [{ url: 'git@h:t/app.git', role: 'main', desc: '本体', owns: ['apps/**'] }], instructions: '' });
  assert.ok(Buffer.byteLength(body) < 400, body);
  assert.deepEqual(Object.keys(YAMLparse(body)), ['version', 'name', 'repos']);
  assert.throws(() => projects.parse(`name: x\ninstructions: "${'a'.repeat(projects.MAX_BYTES)}"\n`), /大きすぎます/);
  assert.throws(() => projects.parse('version: 2\nname: x\n'), /version 2/);
});

function YAMLparse(text) { return require('yaml').parse(text); }

test('一覧は時刻が変わった定義だけ読み直し、読めない定義も error を付けて並べる', () => {
  const kb = tmp();
  projects.write(kb, 'app', { name: 'アプリ', repos: [{ url: 'git@h:t/app.git', role: 'main' }] });
  fs.mkdirSync(path.join(kb, 'projects', 'broken'), { recursive: true });
  fs.writeFileSync(path.join(kb, 'projects', 'broken', 'project.yaml'), '[1, 2]\n');
  fs.mkdirSync(path.join(kb, 'projects', 'empty'), { recursive: true });
  const first = projects.list([kb]);
  assert.deepEqual(first.map((item) => [item.folder, !!item.error]), [['app', false], ['broken', true]]);
  assert.ok(fs.existsSync(path.join(kb, 'projects', 'app', 'README.md')), '索引の雛形を作る');
  const again = projects.list([kb]);
  assert.equal(again[0].project, first[0].project, '変わっていなければ同じものを返す');
  const read = projects.read(first[0].key);
  assert.equal(read.project.name, 'アプリ');
  assert.throws(() => projects.read(`${kb}#../x`), /見つかりません/);
});

test('始めるリポジトリ: owns か名前が一意に当たったときだけ主から外れる。参照では始めない', () => {
  const resolved = projects.resolve({ repos: [
    { url: 'git@h:t/app.git', role: 'main' },
    { url: 'git@h:t/api.git', role: 'work', owns: ['services/**'] },
    { url: 'git@h:t/spec.git', role: 'reference' },
  ] }, {
    [projects.normalizeUrl('git@h:t/app.git')]: '/src/app',
    [projects.normalizeUrl('git@h:t/api.git')]: '/src/api',
    [projects.normalizeUrl('git@h:t/spec.git')]: '/src/spec',
  });
  assert.equal(projects.chooseRepo(resolved, 'ログイン画面を直して').repo.path, '/src/app');
  assert.deepEqual(projects.chooseRepo(resolved, 'services/order/handler.js の例外を直して'), { repo: resolved[1], reason: 'owns' });
  assert.equal(projects.chooseRepo(resolved, 'api のテストを足して').reason, 'name');
  assert.equal(projects.chooseRepo(resolved, 'spec を読んで').repo.path, '/src/app', '参照リポジトリは選ばない');
  assert.equal(projects.chooseRepo(resolved, 'app と api をそろえて').reason, 'main', '複数に当たれば主');
});

test('最初の依頼に添える節は、ほかのリポジトリとナレッジの場所を伝える', () => {
  const project = { name: '受注', repos: [{ url: 'git@h:t/app.git', role: 'main' }, { url: 'git@h:t/spec.git', role: 'reference', desc: '仕様' }], instructions: '日本語で答える' };
  const resolved = projects.resolve(project, { [projects.normalizeUrl('git@h:t/app.git')]: '/src/app' });
  const block = projects.contextBlock({ project, folder: '受注', resolved, current: '/src/app', kbHost: '/src/kb' });
  assert.match(block, /プロジェクト「受注」/);
  assert.match(block, /カレントディレクトリは app（主）/);
  assert.match(block, /- spec（参照）: この PC に未設定 — 仕様/);
  assert.match(block, /\/src\/kb\/projects\/受注\//);
  assert.match(block, /日本語で答える/);
});

test('節には rules.md の中身と、作業ブランチをそろえる指示と、残す候補の挙げ方が入る', () => {
  const project = { name: '受注', repos: [{ url: 'git@h:t/app.git', role: 'main' }, { url: 'git@h:t/api.git', role: 'work' }] };
  const resolved = projects.resolve(project, { [projects.normalizeUrl('git@h:t/app.git')]: '/src/app', [projects.normalizeUrl('git@h:t/api.git')]: '/src/api' });
  const block = projects.contextBlock({ project, folder: '受注', resolved, current: '/src/app', kbHost: '/src/kb', rules: '- 互換を壊さない', branch: 'feat/login' });
  assert.match(block, /### 守ること（rules\.md）\n- 互換を壊さない/);
  assert.match(block, /ブランチ feat\/login を作って/);
  assert.match(block, /main へ直接コミットせず/);
  assert.match(block, /ナレッジに残す候補/);
  const plain = projects.contextBlock({ project, folder: '受注', resolved, current: '/src/app', kbHost: '/src/kb' });
  assert.doesNotMatch(plain, /ブランチ/);
  assert.match(plain, /README\.md と rules\.md があれば読んで/);
});

test('ナレッジの一覧は新しい順で、定義と索引を除く。足すファイルは files/ に重ならない名前で置く', () => {
  const kb = tmp();
  projects.write(kb, 'p', { name: 'p', repos: [] });
  const dir = path.join(kb, 'projects', 'p');
  fs.mkdirSync(path.join(dir, 'notes'));
  fs.writeFileSync(path.join(dir, 'notes', 'a.md'), 'a');
  fs.writeFileSync(path.join(dir, 'rules.md'), 'r');
  fs.utimesSync(path.join(dir, 'notes', 'a.md'), new Date(2020, 0, 1), new Date(2020, 0, 1));
  const listed = projects.knowledgeFiles(kb, 'p', 1);
  assert.equal(listed.total, 2);
  assert.deepEqual(listed.recent.map((f) => f.rel), ['projects/p/rules.md']);
  assert.equal(projects.fileTarget(kb, 'p', '../x/メモ.txt'), 'projects/p/files/メモ.txt');
  fs.mkdirSync(path.join(dir, 'files'));
  fs.writeFileSync(path.join(dir, 'files', 'メモ.txt'), '');
  assert.equal(projects.fileTarget(kb, 'p', 'メモ.txt'), 'projects/p/files/メモ-2.txt');
  assert.equal(projects.rulesText(kb, 'p'), 'r');
  fs.writeFileSync(path.join(dir, 'rules.md'), 'x'.repeat(5000));
  assert.equal(projects.rulesText(kb, 'p'), '');
});

test('プロジェクトの会話の一覧はリポジトリを問わず、印の付いた会話だけを並べる', () => {
  const store = require('../src/main/store');
  const ud = tmp();
  const a = store.createSession(ud, { repo: '/src/app', cli: 'x', project: 'kb#p' });
  store.createSession(ud, { repo: '/src/app', cli: 'x' });
  const c = store.createSession(ud, { repo: '/src/api', cli: 'x', project: 'kb#p' });
  assert.deepEqual(store.listSessions(ud, '/src/app', { project: 'kb#p' }).map((s) => s.id).sort(), [a.id, c.id].sort());
  assert.equal(store.listSessions(ud, '/src/app').length, 2);
  store.updateSession(ud, a.id, { project: '' });
  assert.equal(store.listSessions(ud, '', { project: 'kb#p' }).length, 1);
});

test('ナレッジに保存の指示は、置き場・索引・コミットの範囲を決めて渡す', () => {
  const prompt = projects.knowledgePrompt({ kbHost: '/src/kb/', folder: '受注', kind: 'decision', date: '2026-09-25T00:00:00Z' });
  assert.match(prompt, /\/src\/kb\/projects\/受注\/decisions\/2026-09-25-/);
  assert.match(prompt, /README\.md/);
  assert.match(prompt, /コミットし、そのまま push する/);
  assert.match(projects.knowledgePrompt({ kbHost: '/src/kb', folder: 'x', scope: 'shared', kind: 'rule' }), /\/src\/kb\/shared\/rules\.md/);
});

test('agent-project の状態フォルダを取り込む（repos・パス・知識を写し、backlog は数えるだけ）', () => {
  const root = tmp();
  fs.writeFileSync(path.join(root, 'charter.md'), [
    '<!-- 説明 -->', '# 受注システム', '', '## goal', '- 何か', '', '## repos',
    '# コメント行', '- app = git@h:t/app.git', '  - owns: apps/**, services/**', '  - desc: 本体',
    '- web = git@h:t/app.git', '  - 担当: web/**', '- core = https://h/t/core.git', '  - 説明: 型の参照元', '', '## links',
  ].join('\n'));
  fs.writeFileSync(path.join(root, 'rules.md'), '- テストを通す\n');
  fs.mkdirSync(path.join(root, 'decisions'));
  fs.writeFileSync(path.join(root, 'decisions', 't1.md'), '# 決定\n');
  fs.mkdirSync(path.join(root, 'backlog'));
  fs.writeFileSync(path.join(root, 'backlog', 'a.md'), '# a\n');
  const hostYaml = path.join(root, 'host.yaml');
  fs.writeFileSync(hostYaml, 'repos:\n  - url: https://h/t/app\n    local: /src/app\n');
  const planned = projectImport.plan({ root, hostYaml });
  assert.equal(planned.project.name, '受注システム');
  assert.deepEqual(planned.project.repos.map((r) => [projects.repoLabel(r.url), r.role]), [['app', 'main'], ['core', 'reference']]);
  assert.deepEqual(planned.project.repos[0].owns, ['apps/**', 'services/**', 'web/**']);
  assert.equal(planned.project.repos[1].desc, '型の参照元');
  assert.deepEqual(planned.repoPaths, { [projects.normalizeUrl('git@h:t/app.git')]: '/src/app' });
  assert.deepEqual(planned.leftBehind, { backlog: 1 });
  const kb = tmp();
  const done = projectImport.apply(kb, planned);
  assert.ok(done.written.includes('projects/受注システム/project.yaml'));
  assert.ok(done.written.includes('projects/受注システム/decisions/t1.md'));
  assert.match(fs.readFileSync(path.join(kb, 'projects', '受注システム', 'README.md'), 'utf8'), /\[rules\.md\]\(rules\.md\)/);
  assert.equal(projects.list([kb])[0].project.repos.length, 2);
  assert.throws(() => projectImport.apply(kb, planned), /既にあります/);
});

test('repos.json があればそちらを正として読む', () => {
  const root = tmp();
  fs.writeFileSync(path.join(root, 'repos.json'), JSON.stringify({ _meta: { generated: true }, lib: { url: 'git@h:t/lib.git', owns: 'packages/**' } }));
  fs.writeFileSync(path.join(root, 'charter.md'), '# x\n## repos\n- other = git@h:t/other.git\n');
  const planned = projectImport.plan({ root });
  assert.equal(planned.source, 'repos.json');
  assert.deepEqual(planned.project.repos.map((r) => projects.repoLabel(r.url)), ['lib']);
});

test('add_dir_args を宣言した CLI にだけ、ほかのフォルダを argv で渡す', () => {
  const agentCli = require('../src/main/agentCli');
  const claude = agentCli.load('claude', '');
  const turn = agentCli.turnCmd(claude, { prompt: 'x', extraDirs: ['/src/api', '/src/kb', '/src/api'] });
  assert.deepEqual(turn.argv.filter((t, i, a) => t === '--add-dir' || a[i - 1] === '--add-dir'), ['--add-dir', '/src/api', '--add-dir', '/src/kb']);
  const inter = agentCli.interactiveCmd(claude, { extraDirs: ['/src/kb'] });
  assert.ok(inter.argv.join(' ').includes('--add-dir /src/kb'));
  const none = agentCli.turnCmd({ ...claude, addDirArgs: [] }, { prompt: 'x', extraDirs: ['/src/kb'] });
  assert.ok(!none.argv.includes('--add-dir'));
});
