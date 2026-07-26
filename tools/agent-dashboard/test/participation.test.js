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

// --- 板（agent-board）の公示を「引き受ける」候補にする（S8-3） --------------------
//
// 手動入札は**自己抑制の上書き**。自動入札は担当リポジトリ・タグの照合で自分を抑えるので、
// 人が押す意味があるのは条件を満たさないが引き受けたいときと owner-picks の応募。
// ただし**押しても実行できない端末ではボタンを出さない**——「操作だけ増えて実行できない」
// 状態を構造的に防ぐのが、この画面に板を載せる条件だった。

const READY_BOARD = {
  configured: true, lastTick: '2026-07-26T10:00:00Z', intakeProjects: ['alpha'], myBids: [],
};

function boardView(id, extra = {}) {
  return {
    id, target: 'board', phase: 'open', workload: 'flow', title: `T ${id}`, goal: 'g',
    boardRepo: '/board', bids_open: false,
    units: [{ unit: id, kind: 'flow', state: 'open', bids: [] }],
    ...extra,
  };
}

test('boardCandidates は募集中の公示だけを候補にする', () => {
  const out = participation.boardCandidates([
    boardView('dg-1'),
    boardView('dg-2', { phase: 'working' }),
    boardView('dg-3', { phase: 'done' }),
    { id: 'x', target: 'flow', phase: 'open' },
  ], { board: READY_BOARD });
  assert.deepEqual(out.map((c) => c.id), ['dg-1']);
  assert.equal(out[0].workload, 'board');
  assert.equal(out[0].disabled, false);
  assert.equal(out[0].actionLabel, '引き受ける');
});

test('boardCandidates: owner-picks は「申し込む」', () => {
  const out = participation.boardCandidates([boardView('dg-1', { bids_open: true })],
                                            { board: READY_BOARD });
  assert.equal(out[0].actionLabel, '引き受けを申し込む');
});

test('boardCandidates: 既に入札済み / 指示を投函済みなら押させない', () => {
  const mine = participation.boardCandidates([boardView('dg-1')],
    { board: { ...READY_BOARD, myBids: ['dg-1'] } });
  assert.equal(mine[0].joined, true);
  const sent = participation.boardCandidates([boardView('dg-2')],
    { board: READY_BOARD, commands: { 'dg-2': { state: 'pending' } } });
  assert.equal(sent[0].joined, true);
  // 失敗した指示は「まだ何もしていない」——押し直せる。
  const failed = participation.boardCandidates([boardView('dg-3')],
    { board: READY_BOARD, commands: { 'dg-3': { state: 'error', error: '終端済み' } } });
  assert.equal(failed[0].joined, false);
});

test('boardReason: 実行できない端末では理由を添えて押させない', () => {
  assert.match(participation.boardReason(null), /参加していません/);
  assert.match(participation.boardReason({ configured: true, intakeProjects: ['a'] }),
    /動いていない/, '常駐体が動いていなければ指示は届かない');
  assert.match(
    participation.boardReason({ ...READY_BOARD, intakeProjects: [] }),
    /まだ板の仕事を実行できません/,
    'プロジェクトを持たない端末の落札実行は未対応（R2b）');
  assert.equal(participation.boardReason(READY_BOARD), '');
});

test('boardCandidates: 引き受けられない端末の候補は disabled + 理由つき', () => {
  const out = participation.boardCandidates([boardView('dg-1')],
    { board: { ...READY_BOARD, intakeProjects: [] } });
  assert.equal(out[0].disabled, true);
  assert.match(out[0].reason, /実行できません/);
});
