'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ui = require('../src/renderer/features/participation');

test('参加カードは利用者向け情報と参加操作だけを表示する', () => {
  const html = ui.participationHtml([{
    key: 'flow:run-1', workload: 'flow', title: '検索画面の修正',
    goal: '入力した名前で候補を探せるようにする', context: 'Alpha', available: 2,
    actionLabel: '参加する',
  }], {});

  assert.match(html, /検索画面の修正/);
  assert.match(html, /入力した名前で候補を探せるようにする/);
  assert.match(html, /プロジェクト作業/);
  assert.match(html, />参加する</);
  assert.doesNotMatch(html, /busDir|executor|claim|owner-picks|workload/);
});

test('joinCandidate はプロジェクト作業への参加をrun限定ワーカー起動へ渡す', async () => {
  const calls = [];
  const result = await ui.joinCandidate({
    workload: 'flow', busDir: '/bus', projectDir: '/project', runId: 'run-1',
  }, {
    participationFlowJoin: async (payload) => { calls.push(payload); return { started: true }; },
  });

  assert.deepEqual(calls, [{ busDir: '/bus', projectDir: '/project', runId: 'run-1' }]);
  assert.match(result.message, /参加を開始しました/);
});

test('joinCandidate はミッション参加を既存の役割引き受けへ渡す', async () => {
  const calls = [];
  const result = await ui.joinCandidate({
    workload: 'amigos', home: '/home/amigos', missionId: 'mission-1', roleId: 'research',
    actionLabel: '参加を申し込む',
  }, {
    amigosClaim: async (...args) => { calls.push(args); },
  });

  assert.deepEqual(calls, [['/home/amigos', 'mission-1', 'research']]);
  assert.match(result.message, /参加を申し込みました/);
});

test('参加タブは候補モデルより後に読み込み、初期状態では隠す', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'index.html'), 'utf8');
  assert.match(html, /class="tab hidden" data-tab="participation"[^>]*hidden>参加<\/button>/);
  assert.match(html, /id="tab-participation"[^>]*hidden/);
  assert.ok(
    html.indexOf('../features/participation/model.js') < html.indexOf('features/participation.js'),
    '候補モデルを先に読み込む'
  );
});
