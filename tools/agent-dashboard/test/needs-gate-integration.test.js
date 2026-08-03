'use strict';

const assert = require('assert');
const renderer = require('./helpers/renderer-src').read();

function grab(name) {
  const at = renderer.indexOf(`function ${name}(`);
  assert.ok(at >= 0, `renderer に function ${name} が見つかりません`);
  let depth = 0;
  for (let i = renderer.indexOf('{', at); i < renderer.length; i++) {
    if (renderer[i] === '{') depth += 1;
    else if (renderer[i] === '}') {
      depth -= 1;
      if (depth === 0) return renderer.slice(at, i + 1);
    }
  }
  throw new Error(`function ${name} の閉じ括弧が見つかりません`);
}

/* eslint-disable no-new-func */
const esc = new Function(`${grab('esc')}; return esc;`)();
const normalizeProse = new Function(`${grab('normalizeProse')}; return normalizeProse;`)();
const inlineMd = new Function('esc', `${grab('inlineMd')}; return inlineMd;`)(esc);
const prosePreview = new Function(
  'normalizeProse', 'inlineMd', `${grab('prosePreview')}; return prosePreview;`
)(normalizeProse, inlineMd);
const deliveryReviewState = new Function(`${grab('deliveryReviewState')}; return deliveryReviewState;`)();
const needFailureViewModel = new Function(`${grab('needFailureViewModel')}; return needFailureViewModel;`)();
const needProjectCheckSource = new Function(
  `${grab('needProjectCheckSource')}; return needProjectCheckSource;`
)();
const renderNeedFacts = new Function(
  'needFailureViewModel', 'needProjectCheckSource', 'esc', 'inlineMd', 'prosePreview', 'deliveryReviewState',
  `${grab('renderNeedFacts')}; return renderNeedFacts;`
)(needFailureViewModel, needProjectCheckSource, esc, inlineMd, prosePreview, deliveryReviewState);
/* eslint-enable no-new-func */

assert.strictEqual(
  needProjectCheckSource(
    { summary: 'make smoke が失敗', context: { command: 'make smoke' } },
    { failurePhase: 'regression' }),
  'regression'
);
assert.strictEqual(
  needProjectCheckSource(
    { summary: 'pytest が失敗', context: { command: 'pytest' } },
    { failurePhase: 'verify' }),
  null
);

const regression = renderNeedFacts(
  { projectCheck: { configured: true, command: 'make smoke' } },
  {
    id: 'T-CHECK', failurePhase: 'regression', why: '回帰検知: make smoke 失敗',
    failureSummary: 'make smoke が失敗しました。',
    failureResolution: '共通チェックを手元で実行してください。',
    failureContext: { command: 'make smoke', exitCode: '1' },
  }
);
assert.ok(regression.includes('プロジェクト共通チェック'));
assert.ok(regression.includes('regression_cmd'));
assert.ok(regression.includes('done を止めています'));
assert.ok(!regression.includes('intake_cmd'));
assert.ok(!regression.includes('codd-gate'));

const verify = renderNeedFacts(
  { projectCheck: { configured: true, command: 'make smoke' } },
  {
    id: 'T-VERIFY', failurePhase: 'verify',
    failureSummary: 'pytest が失敗しました。',
    failureContext: { command: 'pytest', exitCode: '1' },
  }
);
assert.ok(!verify.includes('プロジェクト共通チェック'));
assert.ok(verify.includes('pytest が失敗しました'));

const reason = renderNeedFacts({}, { id: 'T-REASON', why: '確認待ちです。' });
assert.ok(reason.includes('<span class="label-chip">理由</span> <span class="prose-inline">確認待ちです。</span>'));

console.log('needs-gate-integration: ok');
