'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ui = require('../src/renderer/features/participation');

test('参加カードは利用者向け情報と参加操作だけを表示する', () => {
  const html = ui.participationHtml([{
    key: 'flow:run-1', workload: 'flow', title: '検索画面の修正',
    goal: '**入力した名前**で候補を探せるようにする\n- 部分一致に対応する', context: 'Alpha', available: 2,
    actionLabel: '参加する',
  }], {}, () => '<div class="md"><p><strong>入力した名前</strong>で候補を探せるようにする</p><ul><li>部分一致に対応する</li></ul></div>');

  assert.match(html, /検索画面の修正/);
  assert.match(html, /class="participation-description"/);
  assert.match(html, /tabindex="0" role="region"/);
  assert.match(html, /aria-label="検索画面の修正の作業内容の詳細"/);
  assert.match(html, /<strong>入力した名前<\/strong>/);
  assert.match(html, /<li>部分一致に対応する<\/li>/);
  assert.match(html, /プロジェクト作業/);
  assert.match(html, />参加する</);
  assert.doesNotMatch(html, /busDir|executor|claim|owner-picks|workload/);
});

test('参加候補は選択中だけでなく発見済みの全プロジェクトから集める', async () => {
  const projects = [
    { dir: '/alpha', name: 'Alpha', exists: true, isProject: true },
    { dir: '/beta', charterName: 'Beta', exists: true, isProject: true },
    { dir: '/routine', name: 'Routine', exists: true, isProject: false },
  ];
  const api = {
    readProject: async (dir) => ({ dir, workspace: dir, busDir: `${dir}/bus` }),
    flowRuns: async () => ({ runs: [{ runId: 'same-run', status: 'running' }] }),
  };
  const model = {
    flowCandidates: (runs, context) => runs.map((run) => ({
      key: run.runId, context: context.projectName,
    })),
  };

  const candidates = await ui.loadFlowCandidates(projects, api, model);

  assert.deepEqual(candidates, [
    { key: 'same-run@%2Falpha%2Fbus', context: 'Alpha' },
    { key: 'same-run@%2Fbeta%2Fbus', context: 'Beta' },
  ]);
});

test('参加候補はホーム描画後に温め、説明全文は固定高カード内でスクロールできる', () => {
  const bootstrap = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'bootstrap.js'), 'utf8');
  const css = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'styles.css'), 'utf8');
  const feature = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'features', 'participation.js'), 'utf8');

  assert.ok(
    bootstrap.indexOf("await switchArea('home')") < bootstrap.indexOf('refreshFeatureTabs(),'),
    '参加候補の全プロジェクト走査はホームの初期描画を塞がず、その直後に温める'
  );
  assert.ok(!bootstrap.includes('await selectProject(target.dir)'),
    'ホーム表示のために前回プロジェクトの詳細を先読みしない');
  const descriptionRule = css.match(/\.participation-description\s*\{[^}]*\}/s)[0];
  const cardRule = css.match(/\.participation-card\s*\{[^}]*\}/s)[0];
  assert.match(cardRule, /height:\s*300px/);
  assert.match(descriptionRule, /min-height:\s*4\.5em/, '説明を最低3行表示する');
  assert.match(descriptionRule, /overflow-y:\s*auto/, '長い説明はカード内で縦スクロールする');
  assert.match(descriptionRule, /white-space:\s*normal/);
  assert.doesNotMatch(descriptionRule, /max-height|-webkit-line-clamp|display:\s*-webkit-box/,
    '説明を見切れさせない');
  assert.match(feature, /<summary>表示条件を見る<\/summary>/);
  assert.doesNotMatch(feature, /実行中の run も出ます/);
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
