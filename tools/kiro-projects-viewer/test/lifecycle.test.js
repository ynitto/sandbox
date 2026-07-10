'use strict';

// プロジェクト単位のライフサイクル操作（pause / resume / stop）のビュアー層テスト。
// 追加依存なしで `node test/lifecycle.test.js` で走る。
//   - actions.requestLifecycle: 常に commands/ ドロップ（id 無し）で届ける
//   - kiro.projectLiveness: status.json の paused を拾う

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const actions = require('../src/main/actions');
const kiro = require('../src/main/kiro');

let passed = 0;
async function test(name, fn) {
  await fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function mkProject() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-lifecycle-'));
  fs.mkdirSync(path.join(dir, 'backlog'), { recursive: true });
  fs.writeFileSync(path.join(dir, 'charter.md'), '# Charter: demo\n## goal\nx\n', 'utf8');
  return dir;
}

function readDropped(dir) {
  const cdir = path.join(dir, 'commands');
  const files = fs.readdirSync(cdir).filter((f) => f.endsWith('.json'));
  assert.strictEqual(files.length, 1, 'commands に 1 件だけドロップされる');
  return JSON.parse(fs.readFileSync(path.join(cdir, files[0]), 'utf8'));
}

(async () => {
  for (const action of ['pause', 'resume', 'stop']) {
    await test(`requestLifecycle(${action}) は id 無しのコマンドをドロップする`, async () => {
      const dir = mkProject();
      const res = actions.requestLifecycle({ kiro: {} }, { dir, action, reason: 'テスト' });
      assert.strictEqual(res.via, 'file');
      const rec = readDropped(dir);
      assert.strictEqual(rec.command, action);
      assert.strictEqual(rec.id, undefined); // プロジェクト単位＝id を載せない
      assert.strictEqual(rec.reason, 'テスト');
    });
  }

  await test('requestLifecycle は不明な操作を拒否する', async () => {
    const dir = mkProject();
    assert.throws(() => actions.requestLifecycle({ kiro: {} }, { dir, action: 'kill' }));
  });

  await test('projectLiveness は status.json の paused を拾う', async () => {
    const dir = mkProject();
    fs.writeFileSync(
      path.join(dir, 'status.json'),
      JSON.stringify({
        host: 'h',
        watch: true,
        level: 'unattended',
        paused: true,
        updated_iso: new Date().toISOString(),
        fresh_after_sec: 600,
      }),
      'utf8'
    );
    const live = kiro.projectLiveness(dir);
    assert.strictEqual(live.paused, true);
    assert.strictEqual(live.running, true); // 稼働中（同期経由の推定）かつ一時停止中
  });

  console.log(`\n${passed} passed`);
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
