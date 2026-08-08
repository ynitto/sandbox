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
  // 領域名（参加）は左メニューが持つので、タブは中身の名前になる。
  assert.match(html, /class="tab hidden" data-tab="participation"[^>]*hidden>参加できる仕事<\/button>/);
  assert.match(html, /id="tab-participation"[^>]*hidden/);
  assert.ok(
    html.indexOf('../features/participation/model.js') < html.indexOf('features/participation.js'),
    '候補モデルを先に読み込む'
  );
});

test('参加した仕事は同じ領域の「参加の状況」で追える', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'index.html'), 'utf8');
  assert.match(html, /data-tab="participation-status"[^>]*data-area="participation"[^>]*>参加の状況</);
  assert.match(html, /id="tab-participation-status"/);
});

test('参加の状況は既に読んでいるデータだけで組む（新しい取得経路を作らない）', () => {
  const feature = require('../src/renderer/features/participation.js');
  const records = feature.joinedRecords();
  assert.deepEqual(records, [], 'state が無い環境でも壊れない');
  // 引き受けが 1 件も無ければ、その旨だけを出す（空の表を並べない）
  assert.match(feature.statusHtml([], {}), /まだ引き受けた仕事はありません/);
  // 板の公示 + 投函した指示の受理状況から、届いたかどうかまで見せる
  const html = feature.statusHtml([
    { id: 'b1', title: '検索の改善', goal: '', phase: 'running', kind: 'プロジェクト作業の依頼', awarded: true, sent: null },
    { id: 'b2', title: '棚卸し', goal: '', phase: 'open', kind: 'ミッションの依頼', awarded: false, sent: { state: 'error', error: '届きませんでした' } },
  ], {});
  assert.match(html, /検索の改善/);
  assert.match(html, /実行中/);
  assert.match(html, /この端末が引き受けています/);
  assert.match(html, /指示が通りませんでした/);
});
