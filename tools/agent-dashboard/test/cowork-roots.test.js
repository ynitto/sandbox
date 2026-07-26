'use strict';

// S2: 定常業務フォルダの dashboard 管理（cowork.roots）と、S3-4: CLIチャットの起動先候補。
// 追加依存なしで `node test/cowork-roots.test.js`。
//
// どちらも「宣言は実行側が持つ」の適用:
//   ・定常業務の実行側は dashboard 自身なので、宣言も dashboard 設定（cowork.roots）に置く
//   ・成果物クローンの実行側は各 PC なので、宣言は host.yaml の repos[] に置く

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const discover = require('../src/features/cowork/main/discover');
const cowork = require('../src/features/cowork/main/cowork');
const project = require('../src/features/agent-project/main/project');
const nodeRepos = require('../src/features/agent-project/main/nodeRepos');
const agent = require('../src/features/agent-project/main/agent');
const { engineConfig } = require('./helpers/engine-status');

let passed = 0;
function test(name, fn) {
  try {
    fn();
    passed += 1;
    console.log(`ok - ${name}`);
  } catch (e) {
    console.error(`not ok - ${name}`);
    throw e;
  }
}

function mkRoutineFolder(base, name) {
  const dir = path.join(base, name);
  fs.mkdirSync(path.join(dir, '.kiro'), { recursive: true });
  fs.writeFileSync(path.join(dir, '.kiro', 'kiro-loop.yaml'),
    'prompts:\n  - name: nightly\n    prompt: やる\n', 'utf8');
  return dir;
}

function tmp(prefix) {
  return fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), prefix)));
}

// ---------------------------------------------------------------------------
// S2: 走査ルートの合流
// ---------------------------------------------------------------------------

test('cowork.roots は走査ルートに合流する', () => {
  const base = tmp('kpv-croots-');
  try {
    const folder = mkRoutineFolder(base, 'routine-only');
    const cfg = { cowork: { roots: [folder] } };
    assert.ok(discover.coworkRoots(cfg).includes(folder));
    const items = discover.discoverCoworkItems(cfg);
    assert.ok(items.length >= 1, 'agent-project 管理外のフォルダから作業が見つかる');
    assert.strictEqual(items[0].repo, folder);
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test('cowork.roots が空でも従来どおり engine のプロジェクトだけを走査する', () => {
  // W2-4（プロジェクト一覧の単一ソース化）を壊さないこと。
  assert.deepStrictEqual(discover.coworkRoots({ cowork: {} }), []);
  assert.deepStrictEqual(discover.coworkRoots({}), []);
});

test('空文字・空白だけのエントリは無視する', () => {
  assert.deepStrictEqual(discover.coworkRoots({ cowork: { roots: ['', '   '] } }), []);
});

// ---------------------------------------------------------------------------
// S2: セレクタへの合流と重複の畳み方
// ---------------------------------------------------------------------------

test('cowork.roots は kind=routine としてセレクタに並ぶ', () => {
  const base = tmp('kpv-croots-sel-');
  try {
    const folder = mkRoutineFolder(base, 'routine');
    const { projects } = project.discover(engineConfig([], { cowork: { roots: [folder] } }));
    const p = projects.find((x) => x.dir === folder);
    assert.ok(p, '登録したフォルダが 1 件として出る');
    assert.strictEqual(p.kind, 'routine');
    assert.strictEqual(p.source, 'cowork');
    assert.strictEqual(p.isProject, false, '定常業務タブだけを出す分岐へ流す');
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test('project root と同じパスの routine は project 側を残して畳む', () => {
  // routine エントリは cowork タブしか持たない。project を上書きすると backlog / charter /
  // needs / 検収が画面から消える。
  const base = tmp('kpv-croots-dup-');
  try {
    const dir = path.join(base, 'proj');
    fs.mkdirSync(path.join(dir, 'backlog'), { recursive: true });
    fs.writeFileSync(path.join(dir, 'charter.md'), '# Charter: demo\n', 'utf8');
    const { projects } = project.discover(engineConfig([dir], { cowork: { roots: [dir] } }));
    const hits = projects.filter((x) => x.dir === dir);
    assert.strictEqual(hits.length, 1, '同じフォルダが 2 件並ばない');
    assert.strictEqual(hits[0].kind, 'project', 'project 側が残る');
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

// ---------------------------------------------------------------------------
// S2: 登録・解除
// ---------------------------------------------------------------------------

test('inspectCoworkRoot はマーカーを検出し、登録済みかを返す', () => {
  const base = tmp('kpv-croots-insp-');
  try {
    const folder = mkRoutineFolder(base, 'routine');
    const fresh = cowork.inspectCoworkRoot({ cowork: { roots: [] } }, folder);
    assert.strictEqual(fresh.registered, false);
    assert.strictEqual(fresh.markers.length, 1);
    assert.strictEqual(fresh.markers[0].loop, 'kiro-loop.yaml');
    const already = cowork.inspectCoworkRoot({ cowork: { roots: [folder] } }, folder);
    assert.strictEqual(already.registered, true);
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test('inspectCoworkRoot はマーカーが無くても例外にせず空で返す', () => {
  // 「まだ定常業務が無いフォルダ」は登録を許す（登録しないと選べず、最初の 1 件も作れない）。
  const base = tmp('kpv-croots-empty-');
  try {
    const got = cowork.inspectCoworkRoot({ cowork: {} }, base);
    assert.deepStrictEqual(got.markers, []);
    assert.strictEqual(got.registered, false);
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test('setCoworkRoot は登録・解除で cowork.roots だけを触る', () => {
  const base = tmp('kpv-croots-set-');
  try {
    const folder = mkRoutineFolder(base, 'routine');
    let saved = null;
    const save = (cfg) => { saved = cfg; return true; };
    const added = cowork.setCoworkRoot({ cowork: { items: [{ id: 'keep' }] } }, save, { dir: folder });
    assert.deepStrictEqual(added.roots, [folder]);
    assert.deepStrictEqual(saved.cowork.items, [{ id: 'keep' }], '他の cowork 設定は保つ');

    const removed = cowork.setCoworkRoot(saved, save, { dir: folder, remove: true });
    assert.deepStrictEqual(removed.roots, []);
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test('setCoworkRoot は同じフォルダを二重登録しない', () => {
  const base = tmp('kpv-croots-dup2-');
  try {
    const folder = mkRoutineFolder(base, 'routine');
    const save = () => true;
    const got = cowork.setCoworkRoot({ cowork: { roots: [folder] } }, save, { dir: folder });
    assert.deepStrictEqual(got.roots, [folder]);
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test('存在しないフォルダは登録できない', () => {
  assert.throws(() => cowork.setCoworkRoot({}, () => true, { dir: '/no/such/folder-xyz' }),
    /フォルダがありません/);
});

// ---------------------------------------------------------------------------
// S3: host.yaml repos[] の読み取りと CLIチャットの起動先候補
// ---------------------------------------------------------------------------

test('parseHostRepos は repos: ブロックの url/local を読む', () => {
  const got = nodeRepos.parseHostRepos([
    'node_id: pc-a',
    'repos:',
    '  - url: https://h/t/app.git',
    '    local: /home/me/mirrors/app',
    '  - url: "git@h:t/lib.git"',
    '    local: /home/me/mirrors/lib',
    'residency: auto',
  ].join('\n'));
  assert.deepStrictEqual(got, [
    { url: 'https://h/t/app.git', local: '/home/me/mirrors/app' },
    { url: 'git@h:t/lib.git', local: '/home/me/mirrors/lib' },
  ]);
});

test('parseHostRepos は repos: の後の別トップレベルキーで止まる', () => {
  const got = nodeRepos.parseHostRepos('repos:\n  - url: u\n    local: /l\nprojects:\n  - root: /r\n');
  assert.deepStrictEqual(got, [{ url: 'u', local: '/l' }]);
});

test('normalizeRepoUrl は Python 側と同じ規則で揃える', () => {
  assert.ok(nodeRepos.sameRepo('https://H/a/B.git', 'https://h/a/b/'));
  assert.ok(!nodeRepos.sameRepo('https://h/a/b.git', 'https://h/a/c.git'));
  // scp 形式をパスとして絶対化しない（cwd 配下に化けて別物になる）
  assert.strictEqual(nodeRepos.normalizeRepoUrl('git@h:t/app.git'), 'git@h:t/app');
});

test('chatCwdChoices はプロジェクトと、この PC にクローンがあるリポジトリを並べる', () => {
  const base = tmp('kpv-chatcwd-');
  const old = process.env.AGENT_PROJECT_AGENTS_HOME;
  try {
    const proj = path.join(base, 'proj-state');
    fs.mkdirSync(proj, { recursive: true });
    const clone = path.join(base, 'mirrors', 'app');
    fs.mkdirSync(clone, { recursive: true });
    // プロジェクトのレジストリには 2 件、このノードの宣言は 1 件だけ。
    fs.writeFileSync(path.join(proj, 'repos.json'), JSON.stringify({
      app: { url: 'https://h/t/app.git' },
      other: { url: 'https://h/t/other.git' },
    }), 'utf8');
    const home = path.join(base, 'agents-home');
    fs.mkdirSync(home, { recursive: true });
    fs.writeFileSync(path.join(home, 'agent-project.host.json'), JSON.stringify({
      schema_version: 1, repos: [{ url: 'https://h/t/app.git', local: clone }],
    }), 'utf8');
    process.env.AGENT_PROJECT_AGENTS_HOME = home;

    const got = agent.chatCwdChoices({}, proj);
    assert.strictEqual(got[0].path, proj, '既定はプロジェクト（従来動作）');
    const app = got.find((c) => c.label === 'app');
    assert.ok(app && app.enabled && app.path === clone, '宣言があるリポジトリは選べる');
    const other = got.find((c) => c.label === 'other');
    assert.ok(other && !other.enabled, '宣言が無いリポジトリは消さずに非活性で見せる');
    assert.match(other.reason, /host\.yaml/, 'なぜ選べないかと直し方を書く');
  } finally {
    if (old === undefined) delete process.env.AGENT_PROJECT_AGENTS_HOME;
    else process.env.AGENT_PROJECT_AGENTS_HOME = old;
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test('宣言された local が実在しなければ理由付きで非活性', () => {
  const base = tmp('kpv-chatcwd-missing-');
  const old = process.env.AGENT_PROJECT_AGENTS_HOME;
  try {
    const proj = path.join(base, 'proj-state');
    fs.mkdirSync(proj, { recursive: true });
    const home = path.join(base, 'agents-home');
    fs.mkdirSync(home, { recursive: true });
    fs.writeFileSync(path.join(home, 'agent-project.host.json'), JSON.stringify({
      schema_version: 1, repos: [{ url: 'https://h/t/app.git', local: path.join(base, 'gone') }],
    }), 'utf8');
    process.env.AGENT_PROJECT_AGENTS_HOME = home;
    const app = agent.chatCwdChoices({}, proj).find((c) => c.label === 'app');
    assert.ok(app && !app.enabled);
    assert.match(app.reason, /見つかりません/);
  } finally {
    if (old === undefined) delete process.env.AGENT_PROJECT_AGENTS_HOME;
    else process.env.AGENT_PROJECT_AGENTS_HOME = old;
    fs.rmSync(base, { recursive: true, force: true });
  }
});

console.log(`\n${passed} passed`);
