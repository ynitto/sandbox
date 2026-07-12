'use strict';

// プロジェクトのリセット（charter 以外の全消去 + kiro-flow daemon 停止）のテスト。
// 追加依存なしで `node test/reset.test.js` で走る。
//   - reset.planReset: charter.md を残し、ドット始まりの同期内部（.state-git）を温存、
//     .replan.request マーカーだけはドット始まりでも削除対象、charter 無しは拒否
//   - reset.executeReset: remover 注入で全対象を削除、失敗は errors に集めて続行
//   - flow.stopDaemon: 稼働なし＝冪等、同一ホストのロック pid へ SIGTERM → 終了待ち

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const reset = require('../src/main/reset');
const flow = require('../src/main/flow');

let passed = 0;
async function test(name, fn) {
  await fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function mkProject() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-reset-'));
  const dir = path.join(root, 'projects', 'demo');
  fs.mkdirSync(path.join(dir, 'backlog'), { recursive: true });
  fs.mkdirSync(path.join(dir, 'needs'), { recursive: true });
  fs.mkdirSync(path.join(dir, 'bus', 'runs', 'run-1'), { recursive: true });
  fs.mkdirSync(path.join(dir, 'bus', '.state-git'), { recursive: true });
  fs.writeFileSync(path.join(dir, 'bus', '.state-git', 'manifest'), '{}', 'utf8');
  fs.writeFileSync(path.join(dir, 'bus', 'status.json'), '{}', 'utf8');
  fs.mkdirSync(path.join(dir, '.state-git'), { recursive: true });
  fs.mkdirSync(path.join(dir, 'flow-archive'), { recursive: true });
  fs.writeFileSync(path.join(dir, 'flow-archive', 'run-1.json'), '{"run":{}}', 'utf8');
  fs.writeFileSync(path.join(dir, 'charter.md'), '# Charter: demo\n## goal\nx\n', 'utf8');
  fs.writeFileSync(path.join(dir, 'backlog', 'T1.md'), '## T1: t\n- status: ready\n', 'utf8');
  fs.writeFileSync(path.join(dir, 'journal.md'), 'log\n', 'utf8');
  fs.writeFileSync(path.join(dir, 'run-log.jsonl'), '{}\n', 'utf8');
  fs.writeFileSync(path.join(dir, '.replan.request'), '{"reason":"x"}', 'utf8');
  fs.writeFileSync(path.join(dir, '.state-git', 'marker'), 'clone', 'utf8');
  return { root, dir };
}

(async () => {
  await test('planReset は charter.md を残し .state-git を温存し .replan.request は対象にする', async () => {
    const { root, dir } = mkProject();
    try {
      const plan = reset.planReset(dir);
      const names = plan.targets.map((t) => t.name);
      assert.deepStrictEqual(plan.keep, ['charter.md']);
      assert.ok(!names.includes('charter.md'), 'charter.md は削除対象にしない');
      assert.ok(!names.includes('.state-git'), '同期クローンは温存（削除の伝播に必要）');
      assert.ok(names.includes('.replan.request'), '再分解マーカーはデータなので対象');
      for (const n of ['backlog', 'needs', 'journal.md', 'run-log.jsonl', 'flow-archive']) {
        assert.ok(names.includes(n), `${n} は削除対象`);
      }
      // バスは丸ごとではなく直下の非ドットだけ（bus/.state-git の manifest を残し、
      // run の削除が復活ではなく「削除の伝播」になるようにする）
      assert.ok(!names.includes('bus'), 'bus はディレクトリ丸ごと消さない');
      assert.ok(names.includes('bus/runs'), 'bus 直下の runs は対象');
      assert.ok(names.includes('bus/status.json'), 'bus 直下のファイルも対象');
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('planReset は charter.md が無いプロジェクトを拒否する', async () => {
    const { root, dir } = mkProject();
    try {
      fs.rmSync(path.join(dir, 'charter.md'));
      assert.throws(() => reset.planReset(dir), /charter\.md/);
      assert.throws(() => reset.planReset(path.join(root, 'no-such')), /ありません/);
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('executeReset は対象を全削除し charter と .state-git だけが残る', async () => {
    const { root, dir } = mkProject();
    try {
      const plan = reset.planReset(dir);
      const res = await reset.executeReset(plan, async (p) => {
        fs.rmSync(p, { recursive: true, force: true });
        return 'delete';
      });
      assert.strictEqual(res.errors.length, 0);
      assert.strictEqual(res.removed.length, plan.targets.length);
      // run アーカイブ（flow-archive/）もプロジェクトのデータなので一緒に消える
      assert.deepStrictEqual(fs.readdirSync(dir).sort(), ['.state-git', 'bus', 'charter.md']);
      // bus には kiro-flow の同期クローンだけが残る（run の削除がリモートへ伝播する）
      assert.deepStrictEqual(fs.readdirSync(path.join(dir, 'bus')), ['.state-git']);
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('executeReset は 1 件の失敗で止まらず errors に集めて続行する', async () => {
    const { root, dir } = mkProject();
    try {
      const plan = reset.planReset(dir);
      const res = await reset.executeReset(plan, async (p) => {
        if (path.basename(p) === 'backlog') throw new Error('boom');
        fs.rmSync(p, { recursive: true, force: true });
        return 'delete';
      });
      assert.strictEqual(res.errors.length, 1);
      assert.strictEqual(res.errors[0].name, 'backlog');
      assert.strictEqual(res.removed.length, plan.targets.length - 1);
      assert.ok(fs.existsSync(path.join(dir, 'backlog')), '失敗した対象は残る');
      assert.ok(!fs.existsSync(path.join(dir, 'needs')), '他の対象は削除済み');
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('stopDaemon は稼働していなければ何もしない（冪等）', async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-reset-bus-'));
    try {
      const res = await flow.stopDaemon(path.join(tmp, 'bus'), path.join(tmp, 'locks'));
      assert.strictEqual(res.running, false);
      assert.strictEqual(res.stopped, true);
    } finally {
      fs.rmSync(tmp, { recursive: true, force: true });
    }
  });

  await test('stopDaemon は同一ホストのロック pid へ SIGTERM を送って終了を待つ', async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-reset-bus-'));
    const busDir = path.join(tmp, 'bus');
    const lockDir = path.join(tmp, 'locks');
    fs.mkdirSync(busDir, { recursive: true });
    fs.mkdirSync(lockDir, { recursive: true });
    // daemon 役: SIGTERM 既定動作（終了）で生き続ける子プロセス
    const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' });
    try {
      // flow と同じ導出でロックパスを組んで pid を書く（本物の daemon ロックと同形）
      const st = flow.daemonStatus(busDir, lockDir);
      fs.writeFileSync(st.lockPath, `${child.pid}\n`, 'utf8');
      assert.strictEqual(flow.daemonStatus(busDir, lockDir).running, true, '前提: 稼働中と判定される');
      const res = await flow.stopDaemon(busDir, lockDir, { timeoutMs: 5000 });
      assert.strictEqual(res.stopped, true, 'SIGTERM で停止する');
      assert.strictEqual(res.pid, child.pid);
      assert.strictEqual(flow.daemonStatus(busDir, lockDir).running, false, '停止後は稼働なし');
    } finally {
      if (child.exitCode === null) child.kill('SIGKILL');
      fs.rmSync(tmp, { recursive: true, force: true });
    }
  });

  await test('daemonStatus は status.json から orchestrator/worker 数を添える（lock 経路）', () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-reset-cnt-'));
    const busDir = path.join(tmp, 'bus');
    const lockDir = path.join(tmp, 'locks');
    fs.mkdirSync(busDir, { recursive: true });
    fs.mkdirSync(lockDir, { recursive: true });
    try {
      // 生存 pid（このプロセス自身）をロックに書く＝lock 経路で running:true になる
      const lockPath = flow.daemonStatus(busDir, lockDir).lockPath;
      fs.writeFileSync(lockPath, `${process.pid}\n`, 'utf8');
      // daemon が書く生存信号（新しい updated_iso ＋ 稼働数）
      fs.writeFileSync(path.join(busDir, 'status.json'), JSON.stringify({
        host: 'h', pid: process.pid, node_id: 'h-1', orchestrators: 1, workers: 2,
        updated_iso: new Date().toISOString(), fresh_after_sec: 600,
      }), 'utf8');
      const st = flow.daemonStatus(busDir, lockDir);
      assert.strictEqual(st.running, true, '生存 pid ＝ 稼働中');
      assert.strictEqual(st.via, 'lock', 'ロックが正');
      assert.strictEqual(st.orchestrators, 1, 'orchestrator 数が添う');
      assert.strictEqual(st.workers, 2, 'worker 数が添う');
    } finally {
      fs.rmSync(tmp, { recursive: true, force: true });
    }
  });

  await test('daemonStatus は古い status.json の数は添えない（生存判定は lock が正）', () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-reset-cnt2-'));
    const busDir = path.join(tmp, 'bus');
    const lockDir = path.join(tmp, 'locks');
    fs.mkdirSync(busDir, { recursive: true });
    fs.mkdirSync(lockDir, { recursive: true });
    try {
      const lockPath = flow.daemonStatus(busDir, lockDir).lockPath;
      fs.writeFileSync(lockPath, `${process.pid}\n`, 'utf8');
      fs.writeFileSync(path.join(busDir, 'status.json'), JSON.stringify({
        orchestrators: 5, workers: 9,
        updated_iso: new Date(Date.now() - 3600 * 1000).toISOString(), fresh_after_sec: 120,
      }), 'utf8');
      const st = flow.daemonStatus(busDir, lockDir);
      assert.strictEqual(st.running, true, '生存判定は lock 由来で維持');
      assert.strictEqual(st.orchestrators, undefined, '古い数は添えない');
      assert.strictEqual(st.workers, undefined, '古い数は添えない');
    } finally {
      fs.rmSync(tmp, { recursive: true, force: true });
    }
  });

  console.log(`\n${passed} passed`);
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
