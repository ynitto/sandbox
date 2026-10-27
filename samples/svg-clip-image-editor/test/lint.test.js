import assert from 'node:assert/strict';
import { test } from 'node:test';
import { lintDocument } from '../src/core/lint.js';
import { DOMParser, doc } from './helpers.js';

const lint = (svg) => lintDocument(new DOMParser().parseFromString(svg, 'image/svg+xml'));
const errors = (svg) => lint(svg).filter((i) => i.severity === 'error').map((i) => i.rule);

test('契約を満たす最小の文書にはエラーがない', () => {
  assert.deepEqual(errors(doc()), []);
});

test('飾りを大量に足してもエラーが出ない', () => {
  const body = `
    <style>text { animation: spin 2s infinite; transform: rotate(3deg); } .deco { clip-path: url(#other); }</style>
    <clipPath id="other"><text x="0" y="10">文字のクリップ</text></clipPath>
    <g class="deco" clip-path="url(#other)" mask="url(#m)"><image href="https://example.com/x.png" width="10" height="10" clip-path="url(#other)"/></g>
    ${Array.from({ length: 50 }, (_, i) => `<text x="${i}" y="90" transform="rotate(${i})">飾り ${i}</text>`).join('')}
    <use href="#other"/>
    <script>alert(1)</script>`;
  assert.deepEqual(errors(doc({ body })), []);
});

const cases = [
  ['S1', doc().replace('<g data-clip-slot="a"', '<svg data-clip-slot="a"').replace('</g></svg>', '</svg></svg>')],
  ['S1', doc().replace('data-clip-slot="a"', 'data-clip-slot="Bad Name"')],
  ['S2', doc().replace(' clip-path="url(#c)"', '')],
  ['S2', doc().replace('clip-path="url(#c)"', 'clip-path="inset(10px)"')],
  ['S3', doc().replace('</g></svg>', '<rect width="1" height="1"/></g></svg>')],
  ['S3', doc().replace(/<image[^>]*\/>/, '<g><image href="a.png" width="1" height="1"/></g>')],
  ['S4', doc({ image: ' x="10%"' })],
  ['S4', doc().replace(' width="200"', ' width="200px"')],
  ['S4', doc({ image: ' clip-path="url(#c)"' })],
  ['S5', doc().replace('href="a.png"', 'href="javascript:alert(1)"')],
  ['S6', doc({ slotAttrs: ' mask="url(#m)"' })],
  ['C1', doc().replace('<clipPath id="c">', '<clipPath id="c" clipPathUnits="objectBoundingBox">')],
  ['C1', doc().replace('url(#c)', 'url(#nope)')],
  ['C2', doc({ clip: '<text>A</text>' })],
  ['C2', doc({ clip: '<use href="#x"/>' })],
  ['C3', doc({ clip: '<rect width="1" height="1" clip-path="url(#c)"/>' })],
  ['A1', doc({ around: (s) => `<a href="#">${s}</a>` })],
  ['A1', doc({ around: (s) => `<svg x="10">${s}</svg>` })],
  ['A2', doc({ around: (s) => `<g transform="scale(0)">${s}</g>` })],
  ['A3', doc({ around: (s) => `<g clip-path="url(#c)">${s}</g>` })],
  ['D1', doc().replace(' viewBox="0 0 100 100"', '')],
  ['D2', doc({ body: '<rect id="c"/>' })],
  ['D3', doc({ image: ' id="img"', body: '<use href="#img"/>' })],
  ['D4', doc({ body: '<style>g[data-clip-slot] { transform: rotate(10deg) }</style>' })],
  ['D4', doc({ body: '<style>@media screen { image { translate: 10px } }</style>' })],
  ['D4', doc({ around: (s) => `<g style="transform-origin: 0 0">${s}</g>` })],
];

for (const [rule, svg] of cases) {
  test(`${rule} を破る例を見つける: ${svg.length > 0 ? svg.slice(svg.indexOf('<defs>') + 6, svg.indexOf('<defs>') + 40) : ''}…`, () => {
    const found = errors(svg);
    assert.ok(found.includes(rule), `${rule} が見つからない（${found.join(', ') || 'なし'}）\n${svg}`);
  });
}

test('警告: opacity / filter は編集できるが再現しない', () => {
  const issues = lint(doc({ slotAttrs: ' opacity="0.5"', around: (s) => `<g filter="url(#f)">${s}</g>` }));
  assert.deepEqual(issues.filter((i) => i.severity === 'error'), []);
  assert.deepEqual(issues.map((i) => i.rule).sort(), ['A4', 'S6']);
});

test('違反の位置を人が読める形で返す', () => {
  const [issue] = lint(doc().replace('</g></svg>', '<rect/></g></svg>')).filter((i) => i.rule === 'S3');
  assert.equal(issue.location, '/svg/g[1]');
  assert.deepEqual(issue.path, [1]);
  assert.equal(issue.slot, 'a');
});
