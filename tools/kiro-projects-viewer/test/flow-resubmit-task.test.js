'use strict';

// flow 画面の「やり直す」が、kiro-project 配下の run では **タスクの積み直し** になること。
// 追加依存なしで `node test/flow-resubmit-task.test.js` で走る。
//
// 背景: 以前は bus/inbox に新しい run を投入していた。しかし inbox は kiro-flow の daemon が
// 拾う契約で、kiro-project は daemon を使わず run を都度起動する（manage_flow_daemon の既定は
// false）。そのため誰も拾わず「押しても何も起きないボタン」になっていた。さらに inbox 投入は
// kiro-project のタスク状態に触らないので、仮に走っても結果が settle されずタスクは doing の
// まま取り残される。タスクを ready へ戻せば、本体が新しい run を起こし結果も回収する。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const flow = require('../src/main/flow');
const actions = require('../src/main/actions');

let passed = 0;
async function test(name, fn) {
  await fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

// ipc の flow:resubmit と同じ判断をするヘルパ（ipc は electron 依存で単体では読めないため、
// 分岐条件そのものをここで検証する）
async function resubmit({ dir, busDir, runId }) {
  const taskId = flow.parseRunId(runId).taskId;
  if (dir && taskId && fs.existsSync(path.join(dir, 'backlog', `${taskId}.md`))) {
    const res = await actions.runAction(
      { kiro: { actionMode: 'file' } },      // commands/ ドロップに固定（CLI を呼ばない）
      { dir, action: 'approve', id: taskId, reason: `実行画面から再実行（元の run: ${runId}）` }
    );
    return { ...res, viaTask: true, taskId };
  }
  return flow.resubmitRun(busDir, runId);
}

function scaffold({ withTask = true } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-resub-'));
  const busDir = path.join(root, 'bus');
  const runId = 'req-a1b2c3d4-TASK-9-r1';
  const runDir = path.join(busDir, 'runs', runId);
  fs.mkdirSync(runDir, { recursive: true });
  fs.writeFileSync(
    path.join(runDir, 'meta.json'),
    JSON.stringify({ status: 'failed', request: 'do it', workspace: null })
  );
  if (withTask) {
    fs.mkdirSync(path.join(root, 'backlog'), { recursive: true });
    fs.writeFileSync(path.join(root, 'backlog', 'TASK-9.md'),
      '## TASK-9: 何かする\n- status: blocked\n- retries: 3\n');
  }
  return { root, busDir, runId };
}

(async () => {
  await test('kiro-project の run はタスクを積み直す（inbox には入れない）', async () => {
    const { root, busDir, runId } = scaffold();
    const res = await resubmit({ dir: root, busDir, runId });

    assert.strictEqual(res.viaTask, true);
    assert.strictEqual(res.taskId, 'TASK-9');

    // commands/ に approve が落ちている（本体の ingest_commands が拾う）
    const cmds = fs.readdirSync(path.join(root, 'commands'));
    assert.strictEqual(cmds.length, 1);
    const rec = JSON.parse(fs.readFileSync(path.join(root, 'commands', cmds[0]), 'utf8'));
    assert.strictEqual(rec.command, 'approve');
    assert.strictEqual(rec.id, 'TASK-9');
    assert.match(rec.reason, /実行画面から再実行/);

    // bus/inbox には何も入れない（誰も拾わないため）
    assert.ok(!fs.existsSync(path.join(busDir, 'inbox')), 'inbox に投げない');
  });

  await test('タスクに紐づかない run は従来どおり inbox へ（kiro-flow 単体運用）', async () => {
    const { root, busDir, runId } = scaffold({ withTask: false });
    const res = await resubmit({ dir: root, busDir, runId });

    assert.ok(!res.viaTask, 'タスク経路には乗らない');
    const inbox = fs.readdirSync(path.join(busDir, 'inbox'));
    assert.strictEqual(inbox.length, 1, 'inbox に新しい run が入る');
  });

  await test('素の run-id（タスク ID を持たない）も inbox へ', async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-resub-'));
    const busDir = path.join(root, 'bus');
    const runId = 'run-20260712-120000-1234';           // 手動投入の run
    const runDir = path.join(busDir, 'runs', runId);
    fs.mkdirSync(runDir, { recursive: true });
    fs.writeFileSync(path.join(runDir, 'meta.json'),
      JSON.stringify({ status: 'failed', request: 'x' }));
    fs.mkdirSync(path.join(root, 'backlog'), { recursive: true });

    const res = await resubmit({ dir: root, busDir, runId });
    assert.ok(!res.viaTask);
    assert.strictEqual(fs.readdirSync(path.join(busDir, 'inbox')).length, 1);
  });

  console.log(`\n${passed} passed`);
})().catch((e) => {
  console.error('FAILED:', e.message);
  process.exit(1);
});
