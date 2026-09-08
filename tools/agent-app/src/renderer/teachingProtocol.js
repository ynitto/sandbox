'use strict';

// タスクを AI と作る tmux 会話の「約束事」。main（依頼文を組む側）と renderer（AI の返答から
// 見本の依頼を拾う側）の両方が同じ 1 か所を読む。Node からも読める純粋なモジュール。
//
//   見本の依頼 … AI が画面操作の見本を要るとき、返答の中に次の 1 行を単独で書く。
//                 @record browser <開始 URL>
//                 @record windows <アプリ名>
//               記録そのものは利用者の端末（このアプリ）で取る。AI（Windows では WSL の tmux）は
//               playwright-cli / winauto の記録を起こさない。
(function exposeTeachingProtocol() {
  const MARKER = '@record';
  const SOURCES = ['browser', 'windows'];
  const LINE_RE = /^\s*(?:[>*\-•]\s*)?@record\s+(browser|windows)(?:\s*[:：]?\s*(.*?))?\s*$/i;

  // 返答本文から見本の依頼を拾う（最後の 1 件）。無ければ null。
  function parseRecordRequest(text) {
    const lines = String(text || '').split(/\r?\n/);
    let found = null;
    for (const line of lines) {
      const m = LINE_RE.exec(line);
      if (!m) continue;
      const source = m[1].toLowerCase();
      const target = String(m[2] || '').trim().replace(/^[`"'「]+|[`"'」]+$/g, '');
      found = { source, target };
    }
    return found;
  }

  function recordLine(source, target = '') {
    const kind = SOURCES.includes(source) ? source : 'browser';
    return `${MARKER} ${kind}${target ? ` ${target}` : ''}`;
  }

  const protocol = { MARKER, SOURCES, parseRecordRequest, recordLine };
  if (typeof window === 'undefined') module.exports = protocol;
  else window.TeachingProtocol = protocol;
}());
