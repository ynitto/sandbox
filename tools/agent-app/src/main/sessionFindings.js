'use strict';

// 会話内で明示された問題・対処を原文のまま拾う。推測や要約は行わない。
const crypto = require('crypto');

const LABEL = /^(?:[-*•]\s*)?(問題点|課題|原因|失敗|エラー|不具合|回避策|対策|対処|工夫|解決策|改善点)[：:\s]*(.*)$/;
const PROBLEM = /(?:問題|課題|不具合)(?:が|は)(?:発生|判明|残|あり|ある|見つか)|原因(?:は|が)|(?:失敗|エラー)(?:した|が|は|で)|動かなかった|通らなかった|できなかった/;
const WORKAROUND = /回避(?:した|できた|するため|策)|対処(?:した|できた|策)|工夫(?:した|として)|代替策|代わりに|迂回(?:した|して)|解消(?:した|できた)/;
const NEGATED = /(?:問題|課題|エラー|不具合)(?:は|が)?(?:ありません|ない|なし)|失敗(?:は|が)?(?:ありません|ない|なし)/;
const MAX_PER_MESSAGE = 4;
const MAX_PER_SESSION = 20;

function clean(line) {
  return String(line || '').trim().replace(/^[-*•]\s+/, '').replace(/^#{1,6}\s+/, '').trim();
}

function kindOf(label) {
  return /回避|対策|対処|工夫|解決/.test(label) ? 'workaround' : 'problem';
}

function extractText(value) {
  const found = [];
  let heading = '';
  let fenced = false;
  for (const raw of String(value || '').slice(0, 30000).split(/\r?\n/)) {
    const line = raw.trim();
    if (/^(```|~~~)/.test(line)) { fenced = !fenced; heading = ''; continue; }
    if (fenced || !line || /^>/.test(line)) { if (!line) heading = ''; continue; }
    const label = clean(line).match(LABEL);
    if (label && !label[2]) { heading = kindOf(label[1]); continue; }
    const body = clean(label ? label[2] : line);
    if (body.length < 8 || body.length > 500 || NEGATED.test(body) || /^https?:\/\//.test(body)) continue;
    let kind = label ? kindOf(label[1]) : '';
    if (!kind && heading && /^[-*•]\s+/.test(line)) kind = heading;
    if (!kind && WORKAROUND.test(body)) kind = 'workaround';
    if (!kind && PROBLEM.test(body)) kind = 'problem';
    if (!kind) { if (/^#{1,6}\s+/.test(line)) heading = ''; continue; }
    found.push({ kind, excerpt: body.slice(0, 300) });
    if (found.length >= MAX_PER_MESSAGE) break;
  }
  return found;
}

function fromSession(session) {
  const out = [];
  const seen = new Set();
  for (const [index, message] of (session.messages || []).entries()) {
    if (!message || message.role !== 'assistant' || message.stopped) continue;
    for (const item of extractText(message.text)) {
      const normalized = item.excerpt.replace(/\s+/g, ' ').toLowerCase();
      if (seen.has(normalized)) continue;
      seen.add(normalized);
      const hash = crypto.createHash('sha256').update(`${index}\0${item.kind}\0${normalized}`).digest('hex').slice(0, 16);
      out.push({ ...item, id: `${session.id}:${hash}`, index, at: message.at || session.updatedAt || '' });
      if (out.length >= MAX_PER_SESSION) return out;
    }
  }
  return out;
}

module.exports = { extractText, fromSession };
