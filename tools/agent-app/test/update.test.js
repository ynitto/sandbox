'use strict';

// 自動更新（src/main/update.js）と配布物を置く側（scripts/publish-update.js）。
// 更新元は一時フォルダ、ホストのシェルは偽物で、取得・照合・判定・入れ替え台本を通す。

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');
const update = require('../src/main/update');
const settings = require('../src/main/settings');
const store = require('../src/main/store');
const publish = require('../scripts/publish-update');

const tmp = (name) => fs.mkdtempSync(path.join(os.tmpdir(), `agent-app-${name}-`));
const sha = (buf) => crypto.createHash('sha256').update(buf).digest('hex');

// 更新元（フォルダ）を作る。app / tools のどちらも中身は適当なバイト列でよい。
function makeSource({ appVersion = '0.3.0', toolsVersion = '20260914-abc1234', notes = '' } = {}) {
  const dir = tmp('source');
  const exe = Buffer.from(`exe ${appVersion}`);
  const tar = Buffer.from(`tar ${toolsVersion}`);
  fs.writeFileSync(path.join(dir, `agent-app-${appVersion}.exe`), exe);
  fs.writeFileSync(path.join(dir, `agent-tools-${toolsVersion}.tar.gz`), tar);
  fs.writeFileSync(path.join(dir, 'manifest.json'), JSON.stringify({
    schema: 1, notes,
    app: { version: appVersion, file: `agent-app-${appVersion}.exe`, sha256: sha(exe), size: exe.length },
    tools: { version: toolsVersion, file: `agent-tools-${toolsVersion}.tar.gz`, sha256: sha(tar), size: tar.length },
  }));
  return dir;
}

// ホストのシェルの偽物。走った台本を覚え、agent-tools の印を answer で返す。
function fakeShell({ version = '', installed = true, installOk = true } = {}) {
  const runs = [];
  return {
    runs,
    async run(script) {
      runs.push(script);
      if (script.includes('command -v agent-herd')) {
        return { ok: true, status: 0, output: `version=${version}\nagent-herd=${installed ? '/home/me/.local/bin/agent-herd' : ''}\nagent-loop=\nagent-flow=\n`, error: '' };
      }
      return installOk ? { ok: true, status: 0, output: '[OK] installed', error: '' } : { ok: false, status: 1, output: 'boom\ninstall.sh failed', error: 'install.sh failed' };
    },
  };
}

function makeUpdater(overrides = {}) {
  const posts = [];
  const shell = overrides.shell || fakeShell();
  const updater = new update.Updater({
    userData: tmp('userdata'),
    appVersion: '0.2.0',
    loadConfig: () => ({ update: { source: overrides.source || '', onStartup: true, intervalHours: 24 }, wslDistro: '' }),
    shellFor: () => shell,
    post: (channel, payload) => posts.push({ channel, payload }),
    quit: () => { posts.push({ channel: 'quit' }); },
    platform: overrides.platform || 'linux',
    portableFile: overrides.portableFile || '',
    pid: 4242,
    spawnFn: (cmd, args) => { posts.push({ channel: 'spawn', payload: { cmd, args } }); return { unref() {} }; },
    tmpdir: tmp('tmp'),
  });
  return { updater, posts, shell };
}

test('版の比較は数の並びで行い、正式版は先行版より新しい', () => {
  assert.equal(update.compareVersions('0.3.0', '0.2.0'), 1);
  assert.equal(update.compareVersions('0.2.10', '0.2.9'), 1);
  assert.equal(update.compareVersions('1.0.0', '1.0'), 0);
  assert.equal(update.compareVersions('v0.2.0', '0.2.0'), 0);
  assert.equal(update.compareVersions('0.3.0-beta.1', '0.3.0'), -1);
  assert.equal(update.compareVersions('0.2.0', '0.3.0'), -1);
});

test('更新元はフォルダか URL で、manifest のファイル名は更新元の外を指せない', () => {
  assert.equal(update.sourceKind(''), '');
  assert.equal(update.sourceKind('\\\\server\\share\\agent-app'), 'dir');
  assert.equal(update.sourceKind('https://intra.example/agent-app/'), 'url');
  assert.equal(update.joinSource('https://intra.example/agent-app/', 'manifest.json'), 'https://intra.example/agent-app/manifest.json');
  const m = update.normalizeManifest({ app: { version: '1.0.0', file: '../x.exe' }, tools: { version: 'a', file: 'agent-tools-a.tar.gz', sha256: 'ABC' }, notes: 'n' });
  assert.equal(m.app, null);
  assert.deepEqual(m.tools, { version: 'a', file: 'agent-tools-a.tar.gz', sha256: 'abc', size: 0 });
  assert.equal(m.notes, 'n');
});

test('何を更新できるかは版の違いと起動形態で決まる', () => {
  const manifest = update.normalizeManifest({ app: { version: '0.3.0', file: 'a.exe' }, tools: { version: 'b', file: 't.tar.gz' } });
  const portable = update.plan({ manifest, appVersion: '0.2.0', canApplyApp: true, tools: { version: 'a', installed: true } });
  assert.equal(portable.app.available, true);
  assert.equal(portable.app.applicable, true);
  assert.equal(portable.tools.available, true);
  assert.equal(portable.any, true);
  // 開発起動では本体は案内だけ。agent-tools が同じ版なら何も無い
  const dev = update.plan({ manifest, appVersion: '0.2.0', canApplyApp: false, tools: { version: 'b', installed: true } });
  assert.equal(dev.app.available, true);
  assert.equal(dev.app.applicable, false);
  assert.equal(dev.tools.available, false);
  assert.equal(dev.any, false);
  // 本体が同じか新しければ本体は無し
  const same = update.plan({ manifest, appVersion: '0.3.1', canApplyApp: true, tools: { version: 'b', installed: true } });
  assert.equal(same.app.available, false);
});

test('設定の update は既定で起動時 ON・1 日ごと、間隔と更新元の長さを抑える', () => {
  assert.deepEqual(settings.normalize({}).update, { source: '', onStartup: true, intervalHours: 24 });
  const n = settings.normalize({ update: { source: '  \\\\srv\\agent-app  ', onStartup: false, intervalHours: 99999 } }).update;
  assert.deepEqual(n, { source: '\\\\srv\\agent-app', onStartup: false, intervalHours: 720 });
  const ud = tmp('cfg');
  const saved = store.saveConfig(ud, { update: { source: 'https://intra/agent-app', intervalHours: 6 } });
  assert.deepEqual(saved.update, { source: 'https://intra/agent-app', onStartup: true, intervalHours: 6 });
  assert.deepEqual(store.loadConfig(ud).update, saved.update);
});

test('取得は sha256 を照合し、合わなければ置かない', async () => {
  const source = makeSource();
  const manifest = await update.readManifest(source);
  const dest = path.join(tmp('fetch'), 'agent-tools.tar.gz');
  await update.fetchToFile(source, manifest.tools, dest);
  assert.equal(fs.readFileSync(dest, 'utf8'), 'tar 20260914-abc1234');
  const bad = { ...manifest.app, sha256: 'deadbeef' };
  await assert.rejects(update.fetchToFile(source, bad, `${dest}.exe`), /sha256/);
  assert.equal(fs.existsSync(`${dest}.exe`), false);
  assert.equal(fs.existsSync(`${dest}.exe.part`), false);
});

test('更新元が無い・読めないときは手動なら断り、自動なら記録だけする', async () => {
  const none = makeUpdater();
  await assert.rejects(none.updater.check({ manual: true }), /更新元が設定されていません/);
  assert.equal(await none.updater.check(), null);
  const broken = makeUpdater({ source: path.join(tmp('nosuch'), 'x') });
  await assert.rejects(broken.updater.check({ manual: true }), /更新元を読めません/);
  assert.equal(await broken.updater.check(), null);
  assert.match(broken.updater.status().error, /更新元を読めません/);
  assert.ok(broken.posts.some((p) => p.channel === 'update:changed' && p.payload.trigger === 'auto'));
});

test('確認は manifest とホストの印を突き合わせ、renderer へ知らせる', async () => {
  const source = makeSource({ notes: '端末の表示を直した' });
  const { updater, posts, shell } = makeUpdater({ source, shell: fakeShell({ version: '20260901-0000000' }) });
  const plan = await updater.check({ manual: true });
  assert.equal(plan.app.next, '0.3.0');
  assert.equal(plan.app.applicable, false);           // linux の開発起動
  assert.equal(plan.tools.current, '20260901-0000000');
  assert.equal(plan.tools.next, '20260914-abc1234');
  assert.equal(plan.notes, '端末の表示を直した');
  assert.ok(shell.runs[0].includes('agent-tools.version'));
  const last = posts.filter((p) => p.channel === 'update:changed').pop();
  assert.equal(last.payload.trigger, 'manual');
  assert.equal(last.payload.plan.tools.available, true);
  assert.ok(last.payload.lastCheckAt > 0);
});

test('agent-tools の更新はホストで tar を展開して install.sh を叩き、印を書く', async () => {
  const source = makeSource();
  const { updater, shell } = makeUpdater({ source });
  await assert.rejects(updater.apply({ tools: true }), /先に更新を確認/);
  await updater.check({ manual: true });
  const result = await updater.apply({ tools: true });
  assert.equal(result.tools, '20260914-abc1234');
  const script = shell.runs[shell.runs.length - 1];
  assert.match(script, /tar xzf "\$archive"/);
  assert.match(script, /bash "\$dir\/tools\/agent-tools\/install.sh" --only agent-flow,agent-herd <\/dev\/null/);
  assert.match(script, /printf '%s\\n' '20260914-abc1234' > \$HOME\/\.local\/share\/agent-app\/agent-tools\.version/);
  assert.equal(updater.plan.tools.available, false);
  assert.equal(updater.plan.tools.current, '20260914-abc1234');
  assert.equal(updater.status().applying, false);
  assert.equal(fs.readdirSync(path.join(updater.userData, 'updates')).length, 0, '取得した tar は消す');
});

test('install.sh が失敗したら末尾の出力を添えて断り、印は変えない', async () => {
  const source = makeSource();
  const { updater } = makeUpdater({ source, shell: fakeShell({ version: 'old', installOk: false }) });
  await updater.check({ manual: true });
  await assert.rejects(updater.apply({ tools: true }), /agent-tools の更新に失敗しました\n.*install\.sh failed/s);
  assert.equal(updater.plan.tools.current, 'old');
  assert.equal(updater.plan.tools.available, true);
});

test('本体の入れ替えは portable 版だけ。新しい exe を隣に置き、入れ替えの cmd を切り離して終了する', async () => {
  const source = makeSource();
  const dir = tmp('portable');
  const portableFile = path.join(dir, 'agent-app.exe');
  fs.writeFileSync(portableFile, 'old exe');
  const dev = makeUpdater({ source, platform: 'win32', portableFile: '' });
  await dev.updater.check({ manual: true });
  await assert.rejects(dev.updater.apply({ app: true }), /先に更新を確認|この起動形態/);
  const { updater, posts } = makeUpdater({ source, platform: 'win32', portableFile, shell: fakeShell({ version: '20260914-abc1234' }) });
  const plan = await updater.check({ manual: true });
  assert.equal(plan.app.applicable, true);
  assert.equal(plan.tools.available, false);
  const result = await updater.apply({ app: true, tools: true });
  assert.equal(result.app, '0.3.0');
  assert.equal(fs.readFileSync(`${portableFile}.new`, 'utf8'), 'exe 0.3.0');
  const spawned = posts.find((p) => p.channel === 'spawn');
  assert.equal(spawned.payload.cmd, 'cmd.exe');
  const script = fs.readFileSync(spawned.payload.args[1], 'utf8');
  assert.match(script, /PID eq 4242/);
  assert.match(script, /move \/y "%TARGET%" "%OLD%"/);
  assert.match(script, /move \/y "%STAGED%" "%TARGET%"/);
  assert.match(script, /start "" "%TARGET%"/);
  assert.ok(script.includes(`set "TARGET=${portableFile}"`));
  assert.ok(script.includes(`set "STAGED=${portableFile}.new"`));
  await new Promise((r) => setTimeout(r, 400));
  assert.ok(posts.some((p) => p.channel === 'quit'), '入れ替えのために終了する');
});

test('入れ替えの cmd は失敗したら元の exe で起動し直す', () => {
  const script = update.applyScript({ target: 'C:\\apps\\agent-app.exe', staged: 'C:\\apps\\agent-app.exe.new', pid: 1, log: 'C:\\t\\u.log' });
  assert.match(script, /:fail\r\n(?:.*\r\n)*?start "" "%TARGET%"/);
  assert.match(script, /if %N% lss 60/);
  assert.ok(script.endsWith('\r\n'));
});

test('起動時と定期の確認は設定に従う', async () => {
  const source = makeSource();
  const { updater } = makeUpdater({ source });
  updater.schedule({ startupDelayMs: 10, tickMs: 10 });
  await new Promise((r) => setTimeout(r, 120));
  updater.unschedule();
  assert.ok(updater.lastCheckAt > 0);
  const checkedAt = updater.lastCheckAt;
  // 1 日ごとなので、直後の tick では確認し直さない
  updater.schedule({ startupDelayMs: 100000, tickMs: 10 });
  await new Promise((r) => setTimeout(r, 60));
  updater.unschedule();
  assert.equal(updater.lastCheckAt, checkedAt);
});

test('publish-update は portable 版と tools/ の tar を写し、manifest に版と sha256 を書く', () => {
  const dest = tmp('dest');
  const exe = path.join(tmp('exe'), 'agent-app.exe');
  fs.writeFileSync(exe, 'portable exe');
  const manifest = publish.publish({ dest, app: true, tools: true, notes: '試験', exe });
  const pkg = require('../package.json');
  assert.equal(manifest.app.version, pkg.version);
  assert.equal(manifest.app.file, `agent-app-${pkg.version}.exe`);
  assert.equal(manifest.app.sha256, sha(Buffer.from('portable exe')));
  assert.match(manifest.tools.version, /^\d{8}-[0-9a-f]{7,}$/);
  assert.equal(manifest.tools.file, `agent-tools-${manifest.tools.version}.tar.gz`);
  const tar = path.join(dest, manifest.tools.file);
  assert.equal(manifest.tools.sha256, sha(fs.readFileSync(tar)));
  const listing = execFileSync('tar', ['tzf', tar], { encoding: 'utf8' });
  assert.match(listing, /^tools\/agent-tools\/install\.sh$/m);
  assert.match(listing, /^tools\/agent-flow\//m);
  assert.match(listing, /^tools\/agent-loop\/install\.sh$/m);
  assert.match(listing, /^tools\/agent-tools\/agentcore\//m);
  assert.match(listing, /^agents\/[^/]+\.json$/m, 'CLI 定義も配る');
  assert.match(listing, /^commands\//m);
  assert.doesNotMatch(listing, /^tools\/agent-project\//m, 'agent-app が呼ばないものは入れない');
  assert.doesNotMatch(listing, /^tools\/agent-app\//m);
  // 受け手がそのまま読める
  const read = update.normalizeManifest(JSON.parse(fs.readFileSync(path.join(dest, 'manifest.json'), 'utf8')));
  assert.equal(read.app.version, pkg.version);
  assert.equal(read.notes, '試験');
  // 片方だけ置き直すと、もう片方は前の manifest から引き継ぐ
  const again = publish.publish({ dest, app: false, tools: true });
  assert.deepEqual(again.app, manifest.app);
  assert.throws(() => publish.parseArgs([]), /使い方/);
  assert.deepEqual(publish.parseArgs(['X', '--tools-only', '--notes', 'n']), { dest: 'X', app: false, tools: true, notes: 'n', exe: '' });
});
