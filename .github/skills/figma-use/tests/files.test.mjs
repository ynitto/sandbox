import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { isInside, NameRegistry, nodeIdToFileName, resolveInside, safeName, sniffExtension, writeFileInside } from '../scripts/modules/files.mjs';

test('Windows で使えない文字・予約名・末尾のドットを避ける', () => {
  assert.equal(safeName('icon/close:24?'), 'icon_close_24_');
  assert.equal(safeName('CON'), '_CON');
  assert.equal(safeName('lpt1.svg'), '_lpt1.svg');
  assert.equal(safeName('name. '), 'name');
  assert.equal(safeName('../../etc/passwd'), '_.._etc_passwd');
  assert.equal(safeName(''), 'item');
  assert.ok(safeName('あ'.repeat(300)).length <= 100);
});

test('node ID をファイル名にする', () => {
  assert.equal(nodeIdToFileName('1:2'), '1-2');
  assert.equal(nodeIdToFileName('I1:2;3:4'), 'I1-2_3-4');
});

test('大文字小文字だけ違う名前も衝突として扱う', () => {
  const r = new NameRegistry();
  assert.equal(r.take('Icon', '.svg'), 'Icon.svg');
  assert.equal(r.take('icon', '.svg'), 'icon-2.svg');
  assert.equal(r.take('ICON', '.svg'), 'ICON-3.svg');
});

test('Windows・Linux のどちらの区切りでもパストラバーサルを止める', () => {
  assert.equal(isInside('/out', 'a/b.png', path.posix), true);
  assert.equal(isInside('/out', '../x.png', path.posix), false);
  assert.equal(isInside('/out', '/etc/passwd', path.posix), false);
  assert.equal(isInside('C:\\out', 'a\\b.png', path.win32), true);
  assert.equal(isInside('C:\\out', '..\\x.png', path.win32), false);
  assert.equal(isInside('C:\\out', 'D:\\x.png', path.win32), false);
  assert.equal(isInside('C:\\out', 'a/../../x.png', path.win32), false);
  assert.throws(() => resolveInside('/out', '../../x', path.posix), /出力先の外/);
});

test('出力先の外へは書かない', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'figma-use-files-'));
  writeFileInside(dir, 'a/b.txt', 'ok');
  assert.equal(fs.readFileSync(path.join(dir, 'a/b.txt'), 'utf8'), 'ok');
  assert.throws(() => writeFileInside(dir, '../escape.txt', 'x'));
  assert.equal(fs.existsSync(path.join(dir, '..', 'escape.txt')), false);
});

test('先頭バイトから画像の種類を決める', () => {
  assert.equal(sniffExtension(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0, 0, 0, 0])), '.png');
  assert.equal(sniffExtension(Buffer.from([0xff, 0xd8, 0xff, 0xe0])), '.jpg');
  assert.equal(sniffExtension(Buffer.from('<svg xmlns="x"></svg>')), '.svg');
  assert.equal(sniffExtension(Buffer.from('????'), 'binary/octet-stream'), '.bin');
});
