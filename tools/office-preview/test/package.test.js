'use strict';

// ZIP と XML の読み取り（Office のファイルの入れ物の部分）

const { test } = require('node:test');
const assert = require('node:assert');
const { ZipArchive } = require('../src/zip');
const { parseXml, kid, attr, textOf } = require('../src/xml');
const { makeZip } = require('./helpers');

test('ZIP: 圧縮あり・なしの部品を読み、部品名は大文字小文字を区別しない', () => {
  for (const deflate of [true, false]) {
    const zip = new ZipArchive(makeZip({ 'word/document.xml': '<a>日本語</a>', 'x.bin': Buffer.from([1, 2, 3]) }, { deflate }));
    assert.strictEqual(zip.readText('word/document.xml'), '<a>日本語</a>');
    assert.strictEqual(zip.readText('/Word/Document.XML'), '<a>日本語</a>');
    assert.deepStrictEqual([...zip.read('x.bin')], [1, 2, 3]);
    assert.strictEqual(zip.read('missing.xml'), null);
  }
});

test('ZIP: ZIP でないもの・古い形式・大きすぎる部品は code 付きで止める', () => {
  assert.throws(() => new ZipArchive(Buffer.from('not a zip at all, just text')), { code: 'NOT_ZIP' });
  const cfb = Buffer.alloc(512);
  cfb.writeUInt32BE(0xd0cf11e0, 0);
  assert.throws(() => new ZipArchive(cfb), { code: 'ENCRYPTED_OR_LEGACY' });
  const big = new ZipArchive(makeZip({ 'a.xml': 'x'.repeat(5000) }), { maxEntryBytes: 1000 });
  assert.throws(() => big.read('a.xml'), { code: 'TOO_LARGE' });
});

test('XML: 実体参照・CDATA・属性値の中の > を読み、DOCTYPE の実体は展開しない', () => {
  const tree = parseXml('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "BAD">]><w:root xmlns:w="u" w:a="1 &gt; 0" b=\'q\'><w:t>A&amp;B &#x3042;&#12354;</w:t><c><![CDATA[<raw>]]></c><d>&e;</d></w:root>');
  assert.strictEqual(tree.local, 'root');
  assert.strictEqual(attr(tree, 'a'), '1 > 0');
  assert.strictEqual(attr(tree, 'b'), 'q');
  assert.strictEqual(textOf(kid(tree, 't')), 'A&B ああ');
  assert.strictEqual(textOf(kid(tree, 'c')), '<raw>');
  assert.strictEqual(textOf(kid(tree, 'd')), '&e;');
});

test('XML: 関係の名前空間の属性は、接頭辞が r でなくても r: で引ける', () => {
  const tree = parseXml('<x:blip xmlns:x="a" xmlns:rel="http://schemas.openxmlformats.org/officeDocument/2006/relationships" rel:embed="rId7" id="1"/>');
  assert.strictEqual(tree.attrs['r:embed'], 'rId7');
  assert.strictEqual(attr(tree, 'id'), '1');
});

test('XML: 閉じタグが合わない・途中で終わる XML は BROKEN', () => {
  assert.throws(() => parseXml('<a><b></a>'), { code: 'BROKEN' });
  assert.throws(() => parseXml('<a><b>'), { code: 'BROKEN' });
});
