import assert from 'node:assert/strict';
import { test } from 'node:test';
import { InputError, normalizeNodeId, normalizeNodeIds, parseFigmaInput, sanitizeSourceUrl } from '../scripts/modules/url.mjs';

const KEY = 'AbCdEfGhIjKlMnOpQrStUv';

test('design URL のハイフン表記の node-id をコロン表記にする', () => {
  const r = parseFigmaInput(`https://www.figma.com/design/${KEY}/Login-App?node-id=12-34&t=abc`);
  assert.equal(r.fileKey, KEY);
  assert.deepEqual(r.nodeIds, ['12:34']);
  assert.equal(r.branchKey, null);
});

test('URL エンコードされたコロン表記・インスタンス内ノードを受ける', () => {
  assert.deepEqual(parseFigmaInput(`https://figma.com/file/${KEY}/x?node-id=12%3A34`).nodeIds, ['12:34']);
  assert.equal(normalizeNodeId('I1-2%3B3-4'), 'I1:2;3:4');
  assert.equal(normalizeNodeId('I1:2;3:4'), 'I1:2;3:4');
});

test('proto URL と node-id なし、file key だけを受ける', () => {
  assert.deepEqual(parseFigmaInput(`https://www.figma.com/proto/${KEY}/App?node-id=1-2&starting-point-node-id=1%3A2`).nodeIds, ['1:2']);
  assert.deepEqual(parseFigmaInput(`https://www.figma.com/design/${KEY}`).nodeIds, []);
  assert.equal(parseFigmaInput(KEY).fileKey, KEY);
});

test('branch の URL では branch key を API の file key にする', () => {
  const r = parseFigmaInput(`https://www.figma.com/design/${KEY}/branch/BrAnChKeY1234567890/App?node-id=1-2`);
  assert.equal(r.fileKey, 'BrAnChKeY1234567890');
  assert.equal(r.mainFileKey, KEY);
  assert.equal(r.branchKey, 'BrAnChKeY1234567890');
});

test('Figma Make・FigJam・Slides・Sites・他ホストを理由付きで拒否する', () => {
  assert.throws(() => parseFigmaInput(`https://www.figma.com/make/${KEY}/x`), /Figma Make/);
  assert.throws(() => parseFigmaInput(`https://www.figma.com/board/${KEY}/x`), /FigJam/);
  assert.throws(() => parseFigmaInput(`https://www.figma.com/slides/${KEY}/x`), /Slides/);
  assert.throws(() => parseFigmaInput(`https://www.figma.com/site/${KEY}/x`), /Sites/);
  assert.throws(() => parseFigmaInput(`https://evil.example.com/design/${KEY}/x`), /figma.com の URL ではありません/);
  assert.throws(() => parseFigmaInput(`https://figma.com.evil.example/design/${KEY}`), InputError);
  assert.throws(() => parseFigmaInput('not a url'), InputError);
});

test('壊れた node-id を拒否し、複数指定は重複を除く', () => {
  assert.throws(() => normalizeNodeId('abc'), /形式が不正/);
  assert.throws(() => normalizeNodeId('1:2/../3'), /形式が不正/);
  assert.deepEqual(normalizeNodeIds(['1-2,3:4', '1:2']), ['1:2', '3:4']);
});

test('成果物に残す URL から共有トークンなどの余計な問い合わせを落とす', () => {
  assert.equal(sanitizeSourceUrl(`https://www.figma.com/design/${KEY}/App?node-id=1-2&t=SECRET`), `https://www.figma.com/design/${KEY}/App?node-id=1-2`);
});
