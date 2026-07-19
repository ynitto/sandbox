'use strict';

// 委譲タブ renderer モジュールのテスト（Electron 不使用・最小フェイク DOM）。
// - registerFeatureTab で自分のタブを登録する（renderer.js のコアに触らない新パターン）
// - render() がフェイク DOM で例外なく描画し、入札状況（落札待ち/応募中）を出す
// - renderer.js に拡張シーム（featureTabs / renderFeatureTab）が入っている
// - index.html に委譲タブのマーカーとスクリプトが配線されている

const assert = require('assert');
const fs = require('fs');
const path = require('path');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

// --- モジュールの登録と描画（フェイク DOM で require） -----------------------

let registered = null;
global.registerFeatureTab = (n, hooks) => { registered = { name: n, hooks }; };

const paneEl = {
  innerHTML: '',
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: () => {},
};
global.document = {
  getElementById: (id) => (id === 'tab-delegation' ? paneEl : null),
};

const mod = require('../src/renderer/features/delegation.js');

test('registerFeatureTab で delegation タブを登録する', () => {
  assert.ok(registered, '登録された');
  assert.strictEqual(registered.name, 'delegation');
  assert.strictEqual(typeof registered.hooks.render, 'function');
  assert.strictEqual(typeof registered.hooks.refresh, 'function');
});

test('esc は HTML 特殊文字をエスケープする', () => {
  assert.strictEqual(mod.esc('<a>&"\''), '&lt;a&gt;&amp;&quot;&#39;');
});

test('refresh は delegationList の結果を取り込む', async () => {
  global.api = {
    delegationList: async () => ({
      items: [{ id: 'x', workload: 'amigos', phase: 'open', units: [] }],
      errors: ['warn1'],
    }),
  };
  await mod.refresh();
  assert.strictEqual(mod.S.items.length, 1);
  assert.deepStrictEqual(mod.S.errors, ['warn1']);
});

test('render は入札状況（落札待ち/応募中）を例外なく描画する', () => {
  mod.S.loaded = true;
  mod.S.filter = 'all';
  mod.S.items = [
    {
      id: 'am1', workload: 'amigos', phase: 'open', title: 'AM1', goal: '目標',
      bids_open: true,
      progress: { units_total: 1, units_done: 0, units_open: 1, units_failed: 0 },
      home: '/home/x',
      units: [
        {
          unit: 'impl', kind: '実装', state: 'open', assignee: '',
          bids: [
            { who: 'nodeA', state: 'applied' },
            { who: 'nodeB', state: 'expired' },
          ],
        },
      ],
    },
    {
      id: 'req-a-t1-r0', workload: 'flow', phase: 'working', title: 't1', goal: 'やること',
      stale: true, bids_open: false,
      progress: { units_total: 1, units_done: 0, units_open: 0, units_failed: 0 },
      busDir: '/bus',
      units: [{ unit: 'n1', kind: 'work', state: 'claimed', assignee: 'wA', bids: [{ who: 'wA', state: 'winner' }] }],
    },
  ];
  mod.render();
  const html = paneEl.innerHTML;
  assert.ok(html.includes('落札待ち'), 'owner-picks の落札待ちバッジ');
  assert.ok(html.includes('応募中'), '応募中の入札');
  assert.ok(html.includes('落札'), '落札ボタン（applied に対して）');
  assert.ok(html.includes('応答なし'), 'flow の stale バッジ');
  assert.ok(html.includes('新規委譲'), '公示フォーム');
});

// --- renderer.js の拡張シーム -----------------------------------------------

test('renderer.js にフィーチャータブ登録簿がある', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'renderer.js'), 'utf8');
  assert.ok(src.includes('function registerFeatureTab('), 'registerFeatureTab を定義');
  assert.ok(src.includes('globalThis.registerFeatureTab'), 'グローバルへ公開');
  assert.ok(src.includes('function renderFeatureTab('), 'renderFeatureTab で描画');
  assert.ok(src.includes('for (const [name] of featureTabs)'), 'renderAllTabs で全タブ描画');
});

// --- index.html の配線 -------------------------------------------------------

test('index.html に委譲タブのマーカーとスクリプトがある', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'index.html'), 'utf8');
  assert.ok(html.includes('data-tab="delegation"'), 'タブボタン');
  assert.ok(html.includes('id="tab-delegation"'), 'タブペイン');
  assert.ok(html.includes('data-feature="delegation"'), 'feature マーカー');
  assert.ok(html.includes('features/delegation.js'), 'モジュールのスクリプト読み込み');
});

console.log(`\n${passed} tests passed`);
