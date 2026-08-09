'use strict';

// 決着カード（検証が決着しない票）の UI。
// 設計: docs/plans/2026-08-09-verification-settlement-design.md
//
// このカードの要点は「出口を 4 つに固定して、そこへ材料を揃える」こと。したがってテストが
// 縛るのも 2 点だけ: **出口が増えていないこと**と、**4 つとも既存の操作へ写ること**。
// 新しい迂回路（判定を緩めて通す道）が生えていないことが、この機能の本体。

const assert = require('assert');
const { test } = require('node:test');

const renderer = require('./helpers/renderer-src').read();
const projectMain = require('../src/features/agent-project/main/project.js');

function grab(name) {
  const at = renderer.indexOf(`function ${name}(`);
  assert.ok(at >= 0, `renderer に function ${name} が見つかりません`);
  let i = renderer.indexOf('{', at);
  let depth = 0;
  for (; i < renderer.length; i++) {
    if (renderer[i] === '{') depth++;
    else if (renderer[i] === '}') {
      depth--;
      if (depth === 0) return renderer.slice(at, i + 1);
    }
  }
  throw new Error(`function ${name} の閉じ括弧が見つかりません`);
}

function grabConst(name) {
  const at = renderer.indexOf(`const ${name} = {`);
  assert.ok(at >= 0, `renderer に const ${name} が見つかりません`);
  let i = renderer.indexOf('{', at);
  let depth = 0;
  for (; i < renderer.length; i++) {
    if (renderer[i] === '{') depth++;
    else if (renderer[i] === '}') {
      depth--;
      if (depth === 0) return `${renderer.slice(at, i + 1)};`;
    }
  }
  throw new Error(`const ${name} の閉じ括弧が見つかりません`);
}

// eslint-disable-next-line no-new-func
const settlementCardHtml = new Function(
  'esc',
  `${grabConst('SETTLEMENT_ACTIONS')}\n${grab('settlementCardHtml')}; return settlementCardHtml;`
)((v) => String(v == null ? '' : v));

const FULL = {
  id: 'T1',
  settlement: {
    situation: 'unverifiable',
    options: ['retry', 'amend', 'park', 'accept-unverified'],
    current: '',
    attempts: 3,
    held: false,
    blocked: [{ id: 'C1', text: 'E2E fixture がある', note: '判定エージェントが時間切れ' }],
    verifiedWith: {
      agentCli: 'ollama', model: 'qwen3.5:9b',
      timeoutSec: 1800, elapsedSec: 5400, source: 'node',
    },
  },
};

test('決着カード', async (t) => {
  await t.test('4 つの出口をすべて出す（増やさない）', () => {
    const html = settlementCardHtml(FULL);
    const opts = [...html.matchAll(/data-settle="([^"]+)"/g)].map((m) => m[1]);
    assert.deepStrictEqual(opts, ['retry', 'amend', 'park', 'accept-unverified']);
  });

  await t.test('何で・どれだけ待って確かめたかを材料として出す', () => {
    const html = settlementCardHtml(FULL);
    assert.match(html, /ollama\/qwen3\.5:9b/, '使ったエージェントを出す');
    assert.match(html, /上限 1800 秒/);
    assert.match(html, /実測 5400 秒/);
    assert.match(html, /このPCの設定/, '条件の出所が分かる');
    assert.match(html, /同じ理由で続いた回数: 3/);
    assert.match(html, /E2E fixture がある/, '決着しなかった基準を出す');
  });

  await t.test('保留中は park を出さない（押しても何も変わらない選択肢を出さない）', () => {
    const held = { id: 'T1', settlement: { ...FULL.settlement, held: true, options: ['retry', 'amend', 'accept-unverified'] } };
    const html = settlementCardHtml(held);
    assert.doesNotMatch(html, /data-settle="park"/);
    assert.match(html, /保留中です/);
  });

  await t.test('retry のときだけ「何で検証するか」の入力を出す', () => {
    assert.match(settlementCardHtml(FULL), /settlement-agent-input/);
    const noRetry = { id: 'T1', settlement: { ...FULL.settlement, options: ['park'] } };
    assert.doesNotMatch(settlementCardHtml(noRetry), /settlement-agent-input/);
  });

  await t.test('判定を緩めて通す道は案内しない', () => {
    const html = settlementCardHtml(FULL);
    assert.match(html, /未検証で締める/);
    assert.match(html, /判定を緩めて通す設定はありません/);
  });

  await t.test('カードが無い票には何も足さない', () => {
    assert.strictEqual(settlementCardHtml({ id: 'T1' }), '');
    assert.strictEqual(settlementCardHtml({ id: 'T1', settlement: { options: [] } }), '');
  });
});

test('決着カードの 4 択は既存の操作へ写る（新しい指示を増やさない）', () => {
  const at = renderer.indexOf('async function handleSettlement(');
  assert.ok(at >= 0, 'handleSettlement が見つかりません');
  const body = renderer.slice(at, at + 4000);
  // 4 つとも既存の action 名で送る。ここに新しい動詞が生えたら、それは迂回路の追加。
  assert.match(body, /action: 'revise'[\s\S]*verify_agent/, 'retry は revise の verify_agent');
  assert.match(body, /showTaskDialog\(/, 'amend は既存のタスク編集画面へ');
  assert.match(body, /action: 'hold'/, 'park は hold');
  assert.match(body, /action: 'force-complete'/, 'accept-unverified は force-complete');
  const actions = [...body.matchAll(/action: '([a-z-]+)'/g)].map((m) => m[1]).sort();
  assert.deepStrictEqual([...new Set(actions)].sort(),
    ['force-complete', 'hold', 'revise'], '既存の 3 操作以外は使わない');
});

test('needs の frontmatter から settlement を読む', () => {
  const md = [
    '---',
    'status: proposed',
    'task-id: T1',
    'kind: blocked',
    'settlement: {"situation":"unverifiable","options":["retry","park"],"current":"codex",'
      + '"attempts":2,"held":true,"blocked":[{"id":"C1","text":"t","note":"n"}],'
      + '"verified_with":{"agent_cli":"ollama","model":"qwen3.5:9b","timeout_sec":1800,'
      + '"elapsed_sec":90,"source":"plan"}}',
    '---',
    '',
    '# 要対応: T1 — x',
  ].join('\n');
  const need = projectMain.parseNeeds(md, 'T1');
  assert.deepStrictEqual(need.settlement.options, ['retry', 'park']);
  assert.strictEqual(need.settlement.held, true);
  assert.strictEqual(need.settlement.current, 'codex');
  assert.strictEqual(need.settlement.attempts, 2);
  assert.strictEqual(need.settlement.verifiedWith.agentCli, 'ollama');
  assert.strictEqual(need.settlement.verifiedWith.source, 'plan');
  assert.deepStrictEqual(need.settlement.blocked, [{ id: 'C1', text: 't', note: 'n' }]);
});

test('壊れた settlement はカードを出さない（誤った案内より出さない方がまし）', () => {
  const base = ['---', 'task-id: T1', 'kind: blocked', 'settlement: {壊れた', '---', '', '# 要対応: T1 — x'];
  assert.strictEqual(projectMain.parseNeeds(base.join('\n'), 'T1').settlement, null);
  const noOpts = base.slice();
  noOpts[3] = 'settlement: {"situation":"unverifiable"}';
  assert.strictEqual(projectMain.parseNeeds(noOpts.join('\n'), 'T1').settlement, null);
});
