'use strict';

// 添付ファイル。画面から受け取ったファイル（選択・ドロップ・貼り付け）を userData の
// attachments/<id>/<名前> へ写し、CLI にはそのパスを依頼文の末尾で伝える（CLI 自身の
// ファイル読み取りツールで読ませる。画像も同じ）。リポジトリの中のファイルは写さず、
// 相対パスで伝えるだけ（それは attachments ではなく { rel } として扱う）。
//
// 画面から生のパスは受け取らない: 選択ダイアログは main で開いて main が写す。ドロップと
// 貼り付けは中身（bytes）を受け取って書く。以後の参照は id（UUID）だけ。

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const MAX_BYTES = 25 * 1024 * 1024;
const MAX_PER_TURN = 20;

function dir(userData) { return path.join(userData, 'attachments'); }

function checkId(id) {
  if (!/^[0-9a-f-]{36}$/.test(String(id || ''))) throw new Error(`添付の ID が不正です: ${id}`);
  return String(id);
}

// ファイル名は 1 要素だけ（区切りと .. を持ち込めない）。空なら file にする。
function safeName(name) {
  const base = String(name || '').split(/[\\/]/).pop().replace(/[\x00-\x1f]/g, '').trim();
  const s = base === '.' || base === '..' ? '' : base;
  return (s || 'file').slice(0, 120);
}

function pathOf(userData, id, name) {
  return path.join(dir(userData), checkId(id), safeName(name));
}

function stage(userData, name, bytes) {
  const buf = Buffer.isBuffer(bytes) ? bytes : Buffer.from(bytes);
  if (buf.length > MAX_BYTES) throw new Error(`添付が大きすぎます（${Math.round(buf.length / 1048576)} MB。上限 ${MAX_BYTES / 1048576} MB）`);
  const id = crypto.randomUUID();
  const file = pathOf(userData, id, name);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, buf);
  return { id, name: safeName(name), size: buf.length };
}

function stageFile(userData, src) {
  const st = fs.statSync(src);
  if (!st.isFile()) throw new Error(`ファイルではありません: ${src}`);
  if (st.size > MAX_BYTES) throw new Error(`添付が大きすぎます: ${path.basename(src)}（上限 ${MAX_BYTES / 1048576} MB）`);
  const id = crypto.randomUUID();
  const file = pathOf(userData, id, path.basename(src));
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.copyFileSync(src, file);
  return { id, name: safeName(path.basename(src)), size: st.size };
}

// id の添付の実体（無ければ例外）
function resolve(userData, id, name) {
  const file = pathOf(userData, id, name);
  let st;
  try { st = fs.statSync(file); } catch { st = null; }
  if (!st || !st.isFile()) throw new Error(`添付が見つかりません: ${safeName(name)}`);
  return { path: file, size: st.size };
}

function discard(userData, id) {
  fs.rmSync(path.join(dir(userData), checkId(id)), { recursive: true, force: true });
  return true;
}

// 会話が持つ添付（メッセージが参照しているもの）をまとめて消す
function discardAll(userData, sess) {
  for (const m of (sess && sess.messages) || []) {
    for (const a of m.attachments || []) if (a && a.id) { try { discard(userData, a.id); } catch { /* 無ければよい */ } }
  }
}

// どの会話からも参照されていない添付（写したが送らなかったもの）を消す。起動時に 1 回。
function sweep(userData, sessions) {
  const used = new Set();
  for (const sess of sessions || []) {
    for (const m of (sess && sess.messages) || []) for (const a of m.attachments || []) if (a && a.id) used.add(String(a.id));
  }
  let names;
  try { names = fs.readdirSync(dir(userData)); } catch { return 0; }
  let n = 0;
  for (const name of names) {
    if (!/^[0-9a-f-]{36}$/.test(name) || used.has(name)) continue;
    try { discard(userData, name); n += 1; } catch { /* 消せなければ次回 */ }
  }
  return n;
}

module.exports = { MAX_BYTES, MAX_PER_TURN, dir, checkId, safeName, pathOf, stage, stageFile, resolve, discard, discardAll, sweep };
