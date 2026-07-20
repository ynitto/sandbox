'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const participation = require('../src/features/participation/main/participation');

test('flowCandidates は実行可能な工程がある非終端 run だけを参加候補にする', () => {
  const candidates = participation.flowCandidates([
    {
      runId: 'req-a-T1-r0', status: 'running', taskId: 'T1', request: '画面を直す',
      nodes: {
        ready: { id: 'ready', state: 'pending' },
        blocked: { id: 'blocked', state: 'waiting' },
      },
    },
    { runId: 'done-run', status: 'done', nodes: { n1: { id: 'n1', state: 'pending' } } },
  ], { busDir: '/bus', projectDir: '/project', projectName: 'Alpha' });

  assert.deepEqual(candidates, [{
    key: 'flow:req-a-T1-r0',
    workload: 'flow',
    title: 'T1',
    goal: '画面を直す',
    context: 'Alpha',
    available: 1,
    busDir: '/bus',
    projectDir: '/project',
    runId: 'req-a-T1-r0',
  }]);
});

test('buildFlowWorkerLaunch は Windows から対象WSLディストリビューションの worker を組み立てる', () => {
  const launch = participation.buildFlowWorkerLaunch({
    platform: 'win32',
    busDir: '\\\\wsl.localhost\\Ubuntu-24.04\\home\\me\\project\\bus',
    projectDir: '\\\\wsl.localhost\\Ubuntu-24.04\\home\\me\\project',
    runId: 'req-a-T1-r0',
    nodeId: 'dashboard-pc-1',
  });

  assert.equal(launch.command, 'wsl.exe');
  assert.deepEqual(launch.args.slice(0, 5), ['-d', 'Ubuntu-24.04', '-e', 'sh', '-lc']);
  const script = launch.args[5];
  assert.match(script, /cd '\/home\/me\/project'/);
  assert.match(script, /command -v agent-flow/);
  assert.match(script, /agent-flow --bus '\/home\/me\/project\/bus'/);
  assert.match(script, /--run-id 'req-a-T1-r0' work --node-id 'dashboard-pc-1' --idle-exit/);
});

test('startFlowWorker は現在のPCで1回限りのrunワーカーを起動する', async () => {
  const calls = [];
  const child = new EventEmitter();
  child.pid = 4321;
  child.unref = () => { child.unrefCalled = true; };
  const started = participation.startFlowWorker({
    busDir: '/work/bus', projectDir: '/work/project', runId: 'req-a-T1-r0',
  }, {
    platform: 'linux', hostname: 'pc-a', nextId: () => 'abc123',
    spawn: (command, args, options) => {
      calls.push({ command, args, options });
      queueMicrotask(() => child.emit('spawn'));
      return child;
    },
  });

  assert.deepEqual(await started, {
    started: true, pid: 4321, runId: 'req-a-T1-r0', nodeId: 'dashboard-pc-a-abc123',
  });
  assert.equal(calls[0].command, 'agent-flow');
  assert.deepEqual(calls[0].args, [
    '--bus', '/work/bus', '--run-id', 'req-a-T1-r0', 'work',
    '--node-id', 'dashboard-pc-a-abc123', '--idle-exit',
  ]);
  assert.equal(calls[0].options.cwd, '/work/project');
  assert.equal(calls[0].options.detached, true);
  assert.equal(child.unrefCalled, true);
});

test('startFlowWorker は WSL 内に agent-flow が無ければ起動せず分かるエラーを返す', async () => {
  let spawned = false;
  await assert.rejects(
    participation.startFlowWorker({
      busDir: 'C:\\work\\bus', projectDir: 'C:\\work\\project', runId: 'req-a-T1-r0',
    }, {
      platform: 'win32', hostname: 'pc-a', nextId: () => 'abc123',
      spawnSync: () => ({ status: 1, error: null }),
      spawn: () => { spawned = true; throw new Error('呼ばれない'); },
    }),
    /WSLにagent-flowがインストールされていません/
  );
  assert.equal(spawned, false);
});

test('参加IPCは全体設定で agent-flow が停止中ならワーカーを起動しない', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'participation-control-'));
  fs.writeFileSync(path.join(dir, 'control.json'), JSON.stringify({
    version: 1, revision: 1, workloads: { flow: { lifecycle: 'stop' } },
  }));
  const handlers = {};
  require('../src/features/participation/main/ipc').registerIpc({
    handle: (name, fn) => { handlers[name] = fn; },
    loadConfig: () => ({ orchestration: { controlDir: dir } }),
  });

  await assert.rejects(
    handlers['participation:flowJoin']({ busDir: '/bus', projectDir: '/project', runId: 'run-1' }),
    /agent-flowは全体設定で停止中です/
  );
});

test('amigosCandidates は未充足の役割を参加候補にし応募方式の文言を分ける', () => {
  const candidates = participation.amigosCandidates({ missions: [{
    id: 'mission-1', title: '調査ミッション', goal: '原因を調べる', phase: 'open',
    home: '/amigos/home', assignmentPolicy: 'owner-picks',
    roles: [
      { id: 'research', displayName: '調査担当', responsibility: 'ログを確認する', node: null },
      { id: 'write', displayName: '文書担当', responsibility: 'まとめる', node: 'pc-b' },
    ],
  }] });

  assert.deepEqual(candidates, [{
    key: 'amigos:mission-1:research',
    workload: 'amigos',
    title: '調査担当',
    goal: 'ログを確認する',
    context: '調査ミッション',
    home: '/amigos/home',
    missionId: 'mission-1',
    roleId: 'research',
    actionLabel: '参加を申し込む',
  }]);
});
