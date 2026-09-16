'use strict';

// 会話をテキストにする（src/main/sessionExport.js）。制御文字・飾り・前後の空白の落とし方と、
// 書き出したあとに開く配線を固定する。

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const sessionExport = require('../src/main/sessionExport');

const ESC = String.fromCharCode(27);
const BELL = String.fromCharCode(7);

test('端末の装飾（色・カーソル移動）と改行以外の制御文字を落とす', () => {
  assert.strictEqual(sessionExport.cleanText(`${ESC}[32m成功${ESC}[0m`), '成功');
  assert.strictEqual(sessionExport.cleanText(`${ESC}]0;タイトル${BELL}本文`), '本文');
  assert.strictEqual(sessionExport.cleanText(`前${String.fromCharCode(0)}後${BELL}`), '前後');
  // 改行は残し、復帰改行は改行 1 つにそろえる
  assert.strictEqual(sessionExport.cleanText('1 行目\r\n2 行目\r3 行目'), '1 行目\n2 行目\n3 行目');
});

test('飾りの文字（罫線・ブロック・スピナー）と幅を持たない文字を落とす', () => {
  assert.strictEqual(sessionExport.cleanText('╭──────╮'), '');
  assert.strictEqual(sessionExport.cleanText('│ 枠の中 │'), '枠の中');
  assert.strictEqual(sessionExport.cleanText('⠋ 実行中'), '実行中');
  assert.strictEqual(sessionExport.cleanText('█▓░ 進捗 50%'), '進捗 50%');
  assert.strictEqual(sessionExport.cleanText('前​後﻿'), '前後');
  // 内容として意味のある記号は残す
  assert.strictEqual(sessionExport.cleanText('✓ 完了 → 次へ（100%）'), '✓ 完了 → 次へ（100%）');
});

test('行の先頭と末尾の空白、続いた空行、前後の空行を落とす', () => {
  assert.strictEqual(sessionExport.cleanText('   前後に空白   '), '前後に空白');
  assert.strictEqual(sessionExport.cleanText('\tタブ\t'), 'タブ');
  assert.strictEqual(sessionExport.cleanText('\n\n1 行目\n\n\n\n2 行目\n\n\n'), '1 行目\n\n2 行目');
  assert.strictEqual(sessionExport.cleanText('   \n   \n'), '');
  assert.strictEqual(sessionExport.cleanText(null), '');
});

test('依頼と回答を順に並べ、添付と失敗の理由を添える', () => {
  const at = new Date(2026, 8, 16, 20, 53);
  const text = sessionExport.render({
    title: 'テストを直す',
    repo: '/home/me/repo',
    worktree: 'fix-tests',
    messages: [
      { role: 'user', at: new Date(2026, 8, 16, 20, 41), cli: 'claude', readonly: true, text: '  テストを直して  ', attachments: [{ name: 'log.txt' }, { name: 'shot.png' }] },
      { role: 'assistant', at: new Date(2026, 8, 16, 20, 42), cli: 'claude', model: 'opus', text: `${ESC}[1m直しました${ESC}[0m`, error: '' },
      { role: 'assistant', at: new Date(2026, 8, 16, 20, 45), cli: 'codex', text: '', error: '  認証が切れています  ' },
    ],
  }, { at });
  assert.strictEqual(text, [
    '会話: テストを直す',
    'リポジトリ: /home/me/repo',
    '作業フォルダ: fix-tests',
    '書き出し: 2026-09-16 20:53',
    '',
    '='.repeat(40),
    '',
    '[依頼]  2026-09-16 20:41  claude / 読み取り専用',
    'テストを直して',
    '添付: log.txt, shot.png',
    '',
    '[回答]  2026-09-16 20:42  claude / opus',
    '直しました',
    '',
    '[回答]  2026-09-16 20:45  codex',
    '失敗: 認証が切れています',
    '',
  ].join('\n'));
});

test('作業フォルダの無い会話とやり取りの無い会話も書ける', () => {
  const at = new Date(2026, 8, 16, 9, 5);
  const text = sessionExport.render({ title: '', repo: '/repo', worktree: '', messages: [] }, { at });
  assert.ok(!text.includes('作業フォルダ'));
  assert.match(text, /会話: （名前なし）/);
  assert.match(text, /（やり取りはまだありません）/);
  // 思考・進捗や実行情報は載せない
  const withParts = sessionExport.render({
    title: 'x', repo: '/repo', messages: [
      { role: 'assistant', at, text: '答え', parts: { thinking: [{ text: '考え中' }], information: [{ title: '実行しました' }] } },
    ],
  }, { at });
  assert.ok(!withParts.includes('考え中') && !withParts.includes('実行しました'));
});

test('保存名は会話名と時刻から決め、ファイル名に使えない文字を落とす', () => {
  const at = new Date(2026, 8, 16, 20, 53);
  assert.strictEqual(sessionExport.fileName({ title: 'テストを直す' }, at), 'テストを直す-20260916-2053.txt');
  assert.strictEqual(sessionExport.fileName({ title: 'a/b:c*d?"<>|e' }, at), 'a_b_c_d_____e-20260916-2053.txt');
  assert.strictEqual(sessionExport.fileName({ title: '' }, at), '会話-20260916-2053.txt');
  assert.strictEqual(sessionExport.fileName({ title: '名前.' }, at), '名前-20260916-2053.txt');
  assert.strictEqual(sessionExport.fileName({ title: 'あ'.repeat(80) }, at).length, 40 + '-20260916-2053.txt'.length);
  assert.strictEqual(sessionExport.fileName({ title: '1 行目\n2 行目' }, at), '1 行目-20260916-2053.txt');
});

test('userData の exports/ へ書き、そのパスと名前を返す', () => {
  const ud = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-export-'));
  const at = new Date(2026, 8, 16, 20, 53);
  const out = sessionExport.write(ud, { title: '会話名', repo: '/repo', messages: [{ role: 'user', at, text: 'やあ' }] }, { at });
  assert.strictEqual(out.name, '会話名-20260916-2053.txt');
  assert.strictEqual(out.path, path.join(ud, 'exports', out.name));
  assert.match(fs.readFileSync(out.path, 'utf8'), /\[依頼\].*\nやあ/);
});

test('ipc は書き出したファイルを開き、開けなければ理由だけ返す', () => {
  const ipc = fs.readFileSync(path.join(__dirname, '..', 'src', 'main', 'ipc.js'), 'utf8');
  assert.match(ipc, /handle\('session:export'/);
  assert.match(ipc, /const out = sessionExport\.write\(ud, sess\);/);
  assert.match(ipc, /const error = await shell\.openPath\(out\.path\);/);
  assert.match(ipc, /warning: error \? `書き出したファイルを開けませんでした/);
  const preload = fs.readFileSync(path.join(__dirname, '..', 'src', 'preload.js'), 'utf8');
  assert.match(preload, /exportSession: \(id\) => invoke\('session:export', \{ id \}\)/);
  const html = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'index.html'), 'utf8');
  assert.match(html, /<button type="button" id="session-export" hidden>テキストに書き出す<\/button>/);
  const renderer = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'renderer.js'), 'utf8');
  assert.match(renderer, /api\.exportSession\(state\.current\.id\)/);
  assert.match(renderer, /\$\('session-export'\)\.hidden = !cur;/);
});
