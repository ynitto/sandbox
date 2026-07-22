'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const renderer = require('./helpers/renderer-src').read();
const css = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'styles.css'), 'utf8');

function grab(name) {
  const at = renderer.indexOf(`function ${name}(`);
  assert.ok(at >= 0, `renderer.js に function ${name} が見つかりません`);
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

// eslint-disable-next-line no-new-func
const needListItemViewModel = new Function(
  'needKindLabel', 'needDisplayTitle', 'needListSummary', 'needFailureViewModel', 'RISK_LABELS',
  `${grab('needListItemViewModel')}; return needListItemViewModel;`
)(
  () => '検収',
  (need) => need.title,
  () => '成果物を確認し、完了にしてよいか判断してください。',
  () => null,
  { low: 'リスク低', med: 'リスク中', high: 'リスク高' }
);
// eslint-disable-next-line no-new-func
const needListItemHtml = new Function(
  'esc', `${grab('needListItemHtml')}; return needListItemHtml;`
)((value) => String(value == null ? '' : value));

const item = needListItemViewModel(
  { id: 'N1', title: '検索機能の成果を確認', kind: 'review', risk: 'high' },
  'open',
  { label: '9時間待ち', level: 'warn' }
);
assert.deepStrictEqual(item, {
  id: 'N1',
  state: 'open',
  stateText: '未対応',
  kindText: '検収',
  title: '検索機能の成果を確認',
  decision: '成果物を確認し、完了にしてよいか判断してください。',
  failure: false,
  owner: '',
  risk: 'high',
  riskText: 'リスク高',
  ageText: '9時間待ち',
  ageLevel: 'warn',
});

const itemHtml = needListItemHtml(item, true, 24);
for (const className of ['need-list-type', 'need-list-title', 'need-list-summary', 'need-list-age']) {
  assert.ok(itemHtml.includes(className), `${className} を固定表示します`);
}
assert.match(itemHtml, /role="listitem"/);
assert.match(itemHtml, /aria-current="true"/);
assert.ok(!itemHtml.includes('判断材料'));
assert.ok(!itemHtml.includes('need-facts'));
assert.ok(!itemHtml.includes('data-delivery'));

const renderNeedsSource = grab('renderNeeds');
assert.ok(renderNeedsSource.includes('need-list-grid'), '要対応のメイン画面は固定要約一覧を描画します');
assert.ok(renderNeedsSource.includes('needListItemViewModel('));
assert.ok(renderNeedsSource.includes('needListItemHtml('));
assert.ok(renderNeedsSource.includes('master-detail needs-layout'));

assert.match(css, /\.need-list-item\s*\{[^}]*grid-template-columns:\s*140px\s+minmax\(220px,\s*1fr\)\s+minmax\(260px,\s*1\.2fr\)\s+112px\s+24px/s);
assert.match(css, /\.needs-layout:not\(\.show-detail\)\s+\.detail-panel\s*\{[^}]*display:\s*none/s);
assert.match(css, /\.needs-layout\.show-detail\s+\.master-list\s*\{[^}]*display:\s*none/s);
assert.match(
  css,
  /\.needs-layout\s*\{[^}]*height:\s*100%[^}]*min-height:\s*0/s,
  '要対応レイアウトをタブ内の高さに収め、詳細をスクロール可能にします'
);
assert.match(
  css,
  /\.needs-layout\.show-detail\s+\.detail-panel\s*\{[^}]*height:\s*100%[^}]*overflow-y:\s*auto/s,
  '長い詳細でも下部の検収・承認操作までスクロールできる必要があります'
);
assert.match(
  css,
  /#main\s*\{[^}]*min-height:\s*0/s,
  '縦並びになる狭い画面でもメイン領域が内容高へ膨張しないようにします'
);
assert.match(css, /@media \(max-width:\s*1180px\)[\s\S]*\.need-list-item\s*\{[^}]*grid-template-columns:\s*1fr\s+auto/s);

// 項目リストのスクロール: needs-layout は display:block（グリッドでない）ため、master-list に
// 高さ制約が無いと内容ぶんに伸び、#tab-needs の overflow:hidden にクリップされてスクロール
// できなくなる（実際に報告された不具合）。一覧モードの master-list が自前スクロールを持つこと。
assert.match(
  css,
  /\.needs-layout:not\(\.show-detail\)\s+\.master-list\s*\{[^}]*height:\s*100%[^}]*overflow-y:\s*auto/s,
  '要対応の項目リストがスクロールできること（height:100% + overflow-y:auto）'
);

console.log('needs-layout-ui: all tests passed');
