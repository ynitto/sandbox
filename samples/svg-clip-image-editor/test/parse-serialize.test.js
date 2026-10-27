import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseClipDocument } from '../src/core/parse.js';
import { serialize } from '../src/core/serialize.js';
import { lintDocument } from '../src/core/lint.js';
import { apply, compose, equals, IDENTITY, multiply, parseTransform, rotate, scale, translate, about } from '../src/core/matrix.js';
import { DOMParser, SAMPLE, doc, env } from './helpers.js';

test('解析: K は祖先からスロット自身までの transform の積', () => {
  const svg = doc({ slotAttrs: ' transform="translate(5 0)"', around: (s) => `<g transform="scale(2)"><g transform="rotate(10)">${s}</g></g>` });
  const r = parseClipDocument(svg, env);
  assert.ok(r.ok, r.message);
  const [slot] = r.model.slots;
  assert.ok(equals(slot.K, compose(scale(2), rotate(10), translate(5, 0))));
  assert.equal(slot.name, 'a');
  assert.deepEqual(slot.image, { href: 'a.png', hrefAttr: 'href', x: 0, y: 0, width: 200, height: 100, preserveAspectRatio: 'xMidYMid meet' });
});

test('解析のエラー', () => {
  assert.equal(parseClipDocument('<svg', env).code, 'parse_error');
  assert.equal(parseClipDocument('<html/>', env).code, 'parse_error');
  const bad = parseClipDocument(doc().replace(' clip-path="url(#c)"', ''), env);
  assert.equal(bad.code, 'contract_violation');
  assert.ok(bad.issues.some((i) => i.rule === 'S2'));
  assert.equal(parseClipDocument('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>', env).code, 'no_slot');
  assert.equal(parseClipDocument(doc({ clip: '<rect width="10" height="0"/>' }), env).code, 'empty_clip');
  assert.equal(parseClipDocument(doc({ clip: '<path d="M0 0 X"/>' }), env).code, 'contract_violation');
});

test('出力: 変更がなければ入力と文字として同一', () => {
  const r = parseClipDocument(SAMPLE, env);
  assert.equal(serialize(r.model, {}), SAMPLE);
  assert.equal(serialize(r.model, { hero: r.model.slots[0].M0 }), SAMPLE);
});

test('出力: <image> の transform だけが変わり、もう一度読める', () => {
  const r = parseClipDocument(SAMPLE, env);
  const [slot] = r.model.slots;
  const M = multiply(about([400, 300], compose(rotate(15), scale(1.1))), slot.M0);
  const out = serialize(r.model, { hero: M });
  const before = SAMPLE.split('\n');
  const after = out.split('\n');
  assert.equal(after.length, before.length);
  const changed = before.map((l, i) => (l === after[i] ? null : i)).filter((i) => i != null);
  assert.equal(changed.length, 1);
  assert.match(after[changed[0]], /transform="matrix\(0\.531259 0\.14235 -0\.14235 0\.531259 [-\d.]+ [-\d.]+\)"/);
  const again = parseClipDocument(out, env);
  assert.ok(again.ok);
  assert.ok(equals(again.model.slots[0].M0, M, 1e-3));
  assert.deepEqual(lintDocument(new DOMParser().parseFromString(out, 'image/svg+xml')).filter((i) => i.severity === 'error'), []);
});

test('出力: transform がない <image> には足し、恒等なら消す。コメントや CDATA の中の <image> は数えない', () => {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <!-- <image href="x.png"/> -->
  <defs><clipPath id="c"><rect width="50" height="50"/></clipPath><style><![CDATA[ /* <image> */ ]]></style></defs>
  <image href="deco.png" width="5" height="5"/>
  <g data-clip-slot="a" clip-path="url(#c)"><image href='a.png' width='100' height='100'/></g>
  <g data-clip-slot="b" clip-path="url(#c)"><image href="b.png" width="100" height="100" transform="translate(1 2)" /></g>
</svg>`;
  const r = parseClipDocument(svg, env);
  assert.ok(r.ok, r.message);
  const out = serialize(r.model, { a: translate(3, 4), b: IDENTITY });
  assert.ok(out.includes(`<image href='a.png' width='100' height='100' transform="matrix(1 0 0 1 3 4)"/>`), out);
  assert.ok(out.includes('<image href="b.png" width="100" height="100" />'), out);
  assert.ok(out.includes('<!-- <image href="x.png"/> -->'));
  assert.ok(out.includes('<image href="deco.png" width="5" height="5"/>'));
});

test('出力: decomposed の書式', () => {
  const r = parseClipDocument(SAMPLE, env);
  const out = serialize(r.model, { hero: compose(translate(10, 20), rotate(30), scale(0.5)) }, { outputTransform: 'decomposed', precision: 1 });
  assert.match(out, /transform="translate\(10 20\) rotate\(30\) scale\(0\.5\)"/);
  assert.ok(apply(parseTransform('translate(10 20) rotate(30) scale(0.5)'), [0, 0])[0] === 10);
});
