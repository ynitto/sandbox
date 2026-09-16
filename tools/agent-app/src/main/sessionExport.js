'use strict';

// 会話 1 つを、そのまま読めるテキストにして userData/exports/ へ置く。
// 残すのは依頼と回答の本文・添付の名前・失敗の理由だけで、思考や実行情報、端末の画面記録は載せない。
// 本文からは次を落とす:
//   - 端末の装飾（色・カーソル移動などの制御列）と、改行以外の制御文字
//   - 飾りの文字（罫線・ブロック・点字のスピナー）と、幅を持たない文字
//   - 行の先頭と末尾の空白、続いた空行、前後の空行

const fs = require('fs');
const path = require('path');
const { stripAnsi } = require('./text');

// 罫線（U+2500–257F）・ブロック（U+2580–259F）・点字のスピナー（U+2800–28FF）。
// TUI が枠や進捗に使うもので、読み返すときに意味を持たない。
const DECORATION = /[\u2500-\u257f\u2580-\u259f\u2800-\u28ff]/g;
// 制御文字。改行（\n）とタブ（\t）は別に扱うので、この範囲から外してある。
const CONTROL = /[\u0000-\u0008\u000b-\u001f\u007f-\u009f]/g;
// 幅を持たない文字（ゼロ幅・行区切り・異体字選択子・BOM）。
const INVISIBLE = /[\u200b-\u200f\u2028\u2029\ufe00-\ufe0f\ufeff]/g;

const MAX_NAME_CHARS = 40;

function cleanText(raw) {
  const text = stripAnsi(String(raw == null ? '' : raw))
    .replace(/\r\n?/g, '\n')
    .replace(/\t/g, ' ')
    .replace(INVISIBLE, '')
    .replace(DECORATION, '')
    .replace(CONTROL, '');
  const out = [];
  for (const line of text.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed && (!out.length || !out[out.length - 1])) continue;   // 先頭の空行と、続いた空行
    out.push(trimmed);
  }
  while (out.length && !out[out.length - 1]) out.pop();
  return out.join('\n');
}

function pad(n) { return String(n).padStart(2, '0'); }

function stamp(value) {
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return '';
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fileStamp(value) {
  const d = value instanceof Date ? value : new Date(value);
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}`;
}

// 保存名は会話名から決める（画面では聞かない）。
function fileName(sess, at = new Date()) {
  const title = cleanText((sess && sess.title) || '').split('\n')[0]
    .replace(/[\\/:*?"<>|]/g, '_')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, MAX_NAME_CHARS)
    .replace(/[. ]+$/, '');
  return `${title || '会話'}-${fileStamp(at)}.txt`;
}

// 依頼・回答の 1 行目に出す、そのターンの条件（エージェント・モデル・モード）
function tagsOf(message) {
  const tags = [message.cli, message.model, message.tier].map((v) => String(v || '').trim()).filter(Boolean);
  if (message.role === 'user' && message.readonly) tags.push('読み取り専用');
  return tags;
}

function blockOf(message) {
  const label = message.role === 'user' ? '依頼' : '回答';
  const head = [`[${label}]`, stamp(message.at), tagsOf(message).join(' / ')].filter(Boolean).join('  ');
  const lines = [head];
  const body = cleanText(message.text);
  if (body) lines.push(body);
  const attachments = (Array.isArray(message.attachments) ? message.attachments : [])
    .map((a) => cleanText((a && (a.name || a.rel)) || '')).filter(Boolean);
  if (attachments.length) lines.push(`添付: ${attachments.join(', ')}`);
  const error = cleanText(message.error);
  if (error) lines.push(`失敗: ${error}`);
  return lines.join('\n');
}

function render(sess, { at = new Date() } = {}) {
  const head = [
    `会話: ${cleanText(sess.title).split('\n')[0] || '（名前なし）'}`,
    `リポジトリ: ${sess.repo || ''}`,
  ];
  if (sess.worktree) head.push(`作業フォルダ: ${sess.worktree}`);
  head.push(`書き出し: ${stamp(at)}`);
  const messages = (Array.isArray(sess.messages) ? sess.messages : [])
    .filter((m) => m && (m.role === 'user' || m.role === 'assistant'));
  const body = messages.length ? messages.map(blockOf).join('\n\n') : '（やり取りはまだありません）';
  return `${head.join('\n')}\n\n${'='.repeat(40)}\n\n${body}\n`;
}

function dir(userData) { return path.join(userData, 'exports'); }

function write(userData, sess, { at = new Date() } = {}) {
  const base = dir(userData);
  fs.mkdirSync(base, { recursive: true });
  const name = fileName(sess, at);
  const file = path.join(base, name);
  fs.writeFileSync(file, render(sess, { at }), 'utf8');
  return { path: file, name };
}

module.exports = { cleanText, fileName, render, write, dir };
