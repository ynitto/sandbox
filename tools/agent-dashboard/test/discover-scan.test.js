'use strict';

// 親フォルダ登録 → 配下プロジェクトの自動発見のテスト。
// 追加依存なしで `node test/discover-scan.test.js` で走る。
//   - project.scanForProjects: agent-project.yaml（ルート直下 / .agent/）とマーカーの両方で発見する
//   - project.discover: 非プロジェクトの登録ルートを親フォルダとして展開する／
//                    プロジェクトそのものの登録は従来どおり 1 件のまま

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const project = require('../src/main/project');

let passed = 0;
async function test(name, fn) {
  await fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function mkRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-scan-'));
}

function mkdirp(...parts) {
  const dir = path.join(...parts);
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

(async () => {
  await test('scanForProjects はルート直下の agent-project.yaml を発見する', async () => {
    const root = mkRoot();
    const a = mkdirp(root, 'alpha');
    fs.writeFileSync(path.join(a, 'agent-project.yaml'), 'root: .\n', 'utf8');
    mkdirp(root, 'not-a-project');
    assert.deepStrictEqual(project.scanForProjects(root, 2), [a]);
  });

  await test('scanForProjects は .agent/agent-project.yaml とマーカー（charter.md 等）も発見する', async () => {
    const root = mkRoot();
    const a = mkdirp(root, 'alpha');
    mkdirp(a, '.agent');
    fs.writeFileSync(path.join(a, '.agent', 'agent-project.yaml'), 'root: .\n', 'utf8');
    const b = mkdirp(root, 'beta');
    fs.writeFileSync(path.join(b, 'charter.md'), '# Charter: b\n', 'utf8');
    assert.deepStrictEqual(project.scanForProjects(root, 2), [a, b].sort());
  });

  await test('scanForProjects は深さ上限を守り、プロジェクト配下は掘らない', async () => {
    const root = mkRoot();
    const nested = mkdirp(root, 'grp', 'alpha'); // 深さ 2
    fs.writeFileSync(path.join(nested, 'agent-project.yaml'), 'root: .\n', 'utf8');
    // プロジェクト内部にさらにマーカーがあっても別プロジェクトとして数えない
    const inner = mkdirp(nested, 'sub');
    fs.writeFileSync(path.join(inner, 'charter.md'), '# Charter: inner\n', 'utf8');
    const deep = mkdirp(root, 'g1', 'g2', 'gamma'); // 深さ 3 → 既定 2 では見えない
    fs.writeFileSync(path.join(deep, 'agent-project.yaml'), 'root: .\n', 'utf8');
    assert.deepStrictEqual(project.scanForProjects(root, 2), [nested]);
    assert.deepStrictEqual(project.scanForProjects(root, 3), [nested, deep].sort());
  });

  await test('discover は親フォルダ登録を配下プロジェクトへ展開する', async () => {
    const root = mkRoot();
    const a = mkdirp(root, 'alpha');
    fs.writeFileSync(path.join(a, 'agent-project.yaml'), 'root: .\n', 'utf8');
    const b = mkdirp(root, 'beta');
    mkdirp(b, 'backlog');
    const cfg = { projects: { roots: [root], autoDiscover: false } };
    const { projects } = project.discover(cfg);
    assert.deepStrictEqual(projects.map((p) => p.dir).sort(), [a, b].sort());
    assert.ok(projects.every((p) => p.source === 'scan'));
  });

  await test('discover はプロジェクトそのものの登録を従来どおり 1 件で扱う', async () => {
    const root = mkRoot();
    fs.writeFileSync(path.join(root, 'agent-project.yaml'), 'root: .\n', 'utf8');
    mkdirp(root, 'backlog');
    const cfg = { projects: { roots: [root], autoDiscover: false } };
    const { projects } = project.discover(cfg);
    assert.strictEqual(projects.length, 1);
    assert.strictEqual(projects[0].dir, root);
    assert.strictEqual(projects[0].source, 'config');
    assert.strictEqual(projects[0].isProject, true);
  });

  await test('discover は空の親フォルダを従来どおり非プロジェクトの 1 件として残す', async () => {
    const root = mkRoot();
    const cfg = { projects: { roots: [root], autoDiscover: false } };
    const { projects } = project.discover(cfg);
    assert.strictEqual(projects.length, 1);
    assert.strictEqual(projects[0].isProject, false);
  });

  await test('discover は charter.md の `# Charter: <name>` を charterName として返す（サイドバーの任意名表示に使う）', async () => {
    const root = mkRoot();
    fs.writeFileSync(path.join(root, 'charter.md'), '# Charter: 見やすい名前\n\n## goal\nx\n', 'utf8');
    const cfg = { projects: { roots: [root], autoDiscover: false } };
    const { projects } = project.discover(cfg);
    assert.strictEqual(projects[0].charterName, '見やすい名前');
  });

  await test('discover は空の charter.md でもクラッシュせず charterName を空文字にする', async () => {
    const root = mkRoot();
    fs.writeFileSync(path.join(root, 'charter.md'), '', 'utf8');
    const cfg = { projects: { roots: [root], autoDiscover: false } };
    const { projects } = project.discover(cfg);
    assert.strictEqual(projects[0].charterName, '');
  });

  await test('discover は charter.md が無ければ charterName を空文字にする', async () => {
    const root = mkRoot();
    mkdirp(root, 'backlog');
    const cfg = { projects: { roots: [root], autoDiscover: false } };
    const { projects } = project.discover(cfg);
    assert.strictEqual(projects[0].charterName, '');
  });

  await test('removeProjectRegistration は config.roots のエントリを直接取り除く', async () => {
    const root = mkRoot();
    const a = mkdirp(root, 'alpha');
    const cfg = { projects: { roots: [a, '/other/path'] } };
    const result = project.removeProjectRegistration(cfg, a);
    assert.strictEqual(result.removedFrom, 'roots');
    assert.deepStrictEqual(result.roots, ['/other/path']);
  });

  await test('removeProjectRegistration は ~/.agent-project/instances/*.json の該当レコードを削除する', async () => {
    const root = mkRoot();
    const a = mkdirp(root, 'alpha');
    const home = mkRoot();
    const idir = mkdirp(home, '.agent-project', 'instances');
    const rec = path.join(idir, 'host-123.json');
    fs.writeFileSync(rec, JSON.stringify({ pid: 123, root: a, host: 'host' }), 'utf8');
    const origHome = os.homedir;
    os.homedir = () => home;
    try {
      const cfg = { projects: { roots: [] } };
      const result = project.removeProjectRegistration(cfg, a);
      assert.strictEqual(result.removedFrom, 'instance');
      assert.strictEqual(fs.existsSync(rec), false);
    } finally {
      os.homedir = origHome;
    }
  });

  await test('removeProjectRegistration は登録元が見つからなければ removedFrom: null を返す', async () => {
    const root = mkRoot();
    const a = mkdirp(root, 'alpha');
    const cfg = { projects: { roots: [] } };
    const result = project.removeProjectRegistration(cfg, a);
    assert.strictEqual(result.removedFrom, null);
  });

  await test('discover は Windows から登録した WSL の POSIX パスを UNC へ寄せる（幽霊 C:\\home\\... にしない）', async () => {
    // kiro-loop は WSL 側なので、Windows のビュアーには /home/... の POSIX パスで登録されがち。
    // これを path.resolve すると C:\home\... の幽霊になり exists:false→Cowork のリポジトリ選択
    // （exists で絞る）に出てこない。POSIX 絶対パスは \\wsl.localhost\<distro>\... へ寄せる。
    const origPlatform = Object.getOwnPropertyDescriptor(process, 'platform');
    const origDistro = process.env.WSL_DISTRO_NAME;
    Object.defineProperty(process, 'platform', { value: 'win32', configurable: true });
    process.env.WSL_DISTRO_NAME = 'Ubuntu';
    try {
      const cfg = { projects: { roots: ['/home/dev/kiro-proj'], autoDiscover: false } };
      const dirs = project.discover(cfg).projects.map((p) => p.dir);
      assert.ok(
        dirs.includes('\\\\wsl.localhost\\Ubuntu\\home\\dev\\kiro-proj'),
        `WSL UNC へ寄せていない: ${JSON.stringify(dirs)}`
      );
      assert.ok(!dirs.some((d) => /^[A-Za-z]:\\home\\/.test(d)), `幽霊 C:\\home\\... が残っている: ${JSON.stringify(dirs)}`);
    } finally {
      if (origPlatform) Object.defineProperty(process, 'platform', origPlatform);
      if (origDistro === undefined) delete process.env.WSL_DISTRO_NAME;
      else process.env.WSL_DISTRO_NAME = origDistro;
    }
  });

  console.log(`\n${passed} tests passed`);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
