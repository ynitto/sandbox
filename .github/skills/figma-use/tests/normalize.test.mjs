import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { colorToHex, normalizeEntry, toPlain } from '../scripts/lib/normalize.mjs';

const FIX = path.join(path.dirname(fileURLToPath(import.meta.url)), 'fixtures');
const file = JSON.parse(fs.readFileSync(path.join(FIX, 'design-file.json'), 'utf8'));
const vars = JSON.parse(fs.readFileSync(path.join(FIX, 'variables-local.json'), 'utf8')).meta;
const login = file.document.children[0].children[0];
const entry = { document: login, components: file.components, componentSets: file.componentSets, styles: file.styles };

function find(node, id) {
  if (node.id === id) return node;
  for (const c of node.children || []) {
    const hit = find(c, id);
    if (hit) return hit;
  }
  return null;
}

test('色を 16 進にする', () => {
  assert.equal(colorToHex({ r: 1, g: 0.5, b: 0, a: 1 }), '#FF8000');
});

test('Auto Layout・テキスト・スタイル・コンポーネントを正規化する', () => {
  const r = toPlain(normalizeEntry('1:2', entry));
  assert.equal(r.root.layout.mode, 'VERTICAL');
  assert.deepEqual(r.root.layout.padding, { top: 48, right: 24, bottom: 48, left: 24 });
  assert.equal(r.root.layout.gap, 16);
  assert.equal(r.root.styles.fill.name, 'Surface/Default');
  const title = find(r.root, '1:3');
  assert.equal(title.text.characters, 'ログイン');
  assert.equal(title.text.style.fontSize, 24);
  assert.equal(title.styles.text.name, 'Heading/L');
  const button = find(r.root, '1:4');
  assert.equal(button.component.setName, 'Button');
  assert.equal(button.component.properties.State.value, 'Default');
  assert.equal(r.facts.components[0].name, 'Button');
  assert.deepEqual(r.facts.imageFills.map((f) => f.imageRef), ['abc123imageref']);
  assert.deepEqual(r.facts.svgCandidates.map((s) => s.id), ['1:6'], '入れ子のベクターは外側の候補だけ残す');
  assert.ok(r.facts.texts.find((t) => t.id === '1:10').hidden, '非表示の親の下のテキストに印を付ける');
});

test('応答に無い属性を既定値で補わない', () => {
  const r = toPlain(normalizeEntry('1:2', entry));
  const hero = find(r.root, '1:5');
  assert.equal('layout' in hero, false);
  assert.equal('cornerRadius' in hero, false);
  assert.equal('opacity' in hero, false);
  const label = find(r.root, 'I1:4;5:3');
  assert.equal('letterSpacing' in label.text.style, false);
  assert.equal(find(r.root, '1:8').visible, false);
});

function truncate(node, depth) {
  const copy = { ...node };
  if (depth <= 0) delete copy.children;
  else if (copy.children) copy.children = copy.children.map((c) => truncate(c, depth - 1));
  return copy;
}

test('未解決・未取得・属性なしを区別して残す', () => {
  const r = toPlain(normalizeEntry('1:2', { ...entry, document: truncate(login, 1) }, { depth: 1 }));
  const reasons = new Set(r.missing.map((m) => m.reason));
  assert.ok(reasons.has('unresolved'));
  assert.ok(reasons.has('not_fetched'));
  assert.ok(r.missing.some((m) => m.field === 'styles.fill' && m.nodeId === '1:3'));
  const noBox = toPlain(normalizeEntry('9:9', { document: { id: '9:9', name: 'x', type: 'FRAME' } }));
  assert.ok(noBox.missing.some((m) => m.reason === 'absent' && m.field === 'absoluteBoundingBox'));
  assert.ok(r.missing.some((m) => m.reason === 'not_fetched' && m.nodeId === '1:4' && m.field === 'children'));
  const icon = find(r.root, '1:6');
  assert.equal(icon.childrenStatus, 'not_fetched');
});

test('変数はバインド ID を残し、解決できたものだけ値を付ける', () => {
  const none = toPlain(normalizeEntry('1:2', entry));
  assert.ok(none.missing.some((m) => m.detail.startsWith('VariableID:1:2') && m.reason === 'unresolved'));
  const denied = toPlain(normalizeEntry('1:2', entry, { variablesStatus: 'inaccessible' }));
  assert.ok(denied.missing.some((m) => m.reason === 'inaccessible' && m.detail.startsWith('VariableID:1:2')));
  const ok = toPlain(normalizeEntry('1:2', entry, { variables: vars, variablesStatus: 'resolved' }));
  const v = ok.facts.variables.find((x) => x.variableId === 'VariableID:1:2');
  assert.equal(v.name, 'color/primary');
  assert.deepEqual(v.valuesByMode.Dark, { color: '#6699FF', alpha: 1 });
  assert.ok(ok.missing.some((m) => m.detail.startsWith('VariableID:9:9')), 'ライブラリの変数は未解決のまま');
});

test('遷移先の名前を引き、範囲外なら未取得と書く', () => {
  const r = toPlain(normalizeEntry('1:2', entry));
  const action = r.facts.interactions[0].actions[0];
  assert.equal(action.destinationId, '1:9');
  assert.match(action.destinationNote, /範囲の外/);
});

test('手動マッピングでコード側の部品を対応付ける', () => {
  const r = toPlain(normalizeEntry('1:2', entry, { mapping: { 'button-set-key': { component: 'Button', import: '@/components/Button', source: 'manual' } } }));
  assert.equal(find(r.root, '1:4').codeMapping.component, 'Button');
});
