'use strict';

// バックログ／フローの説明文（⏎ 畳み込み・Markdown）を読みやすく描画するヘルパーの検証。
// 追加依存なしで `node test/prose-render.test.js` で走る。

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const renderer = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'renderer.js'), 'utf8');
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

const esc = (s) =>
  String(s ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');

// eslint-disable-next-line no-new-func
const api = new Function(
  'esc',
  [
    grab('normalizeProse'),
    grab('inlineMd'),
    grab('mdToHtml'),
    grab('prosePreview'),
    grab('proseHtml'),
    grab('splitRequest'),
    'return { normalizeProse, inlineMd, mdToHtml, prosePreview, proseHtml, splitRequest };',
  ].join('\n')
)(esc);

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

test('normalizeProse は ⏎ と \\n を本物の改行に戻す', () => {
  assert.strictEqual(api.normalizeProse('a ⏎ - b'), 'a \n - b');
  assert.strictEqual(api.normalizeProse('a\\nb'), 'a\nb');
});

test('splitRequest は先頭行を題名、残りを本文にする', () => {
  const r = api.splitRequest(
    'タイトル行\n\nこのタスクは完了条件を満たすまで反復し、満たしたら終了すること（loop-until-done）。\n完了条件: `true`'
  );
  assert.strictEqual(r.title, 'タイトル行');
  assert.match(r.body, /loop-until-done/);
  assert.match(r.body, /完了条件/);
});

test('prosePreview は先頭行だけをインライン装飾付きで返す', () => {
  const html = api.prosePreview('作業する: `pytest -q`\n\n本文は出さない');
  assert.match(html, /prose-inline/);
  assert.match(html, /<code>pytest -q<\/code>/);
  assert.ok(!html.includes('本文は出さない'));
});

test('proseHtml は箇条書きと太字を描画する（⏎ 復元後）', () => {
  const html = api.proseHtml('やること: ⏎ - 移す ⏎ - **統合する**');
  assert.match(html, /<ul>/);
  assert.match(html, /<li>移す<\/li>/);
  assert.match(html, /<strong>統合する<\/strong>/);
  assert.ok(!html.includes('⏎'), '⏎ は生で残さない');
});

test('mdToHtml は番号付きリストも描画する', () => {
  const html = api.mdToHtml('手順:\n1. 開く\n2. 直す');
  assert.match(html, /<ol>/);
  assert.match(html, /<li>開く<\/li>/);
});

test('フロー／タスク prose 用のスタイルと描画呼び出しがある', () => {
  assert.match(css, /\.flow-request-body/);
  assert.match(css, /\.task-prose/);
  assert.match(css, /\.prose-inline/);
  assert.match(renderer, /prosePreview\(r\.request/);
  assert.match(renderer, /splitRequest\(run\.request\)/);
  assert.match(renderer, /PROSE_EXTRA_KEYS\.has\(k\)/);
  assert.match(renderer, /proseHtml\(node\.goal\)/);
});

console.log(`\n${passed} passed`);
