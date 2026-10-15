'use strict';
// 指摘を集める入れ物と、ケースの文を取り出す小さな関数。

const procedureText = (c) => [...c.preconditions, ...c.steps].map((i) => i.text).join('\n');
const expectedText = (c) => c.expected.map((i) => i.text).join('\n');
const fullText = (c) => [c.title, procedureText(c), expectedText(c)].filter(Boolean).join('\n');
const allItems = (c) => [...c.preconditions, ...c.steps, ...c.expected];
const label = (c) => c.id || `(${c.line} 行目)`;

function excerpt(s, n = 60) {
  const t = String(s || '').replace(/\s+/g, ' ').trim();
  return t.length <= n ? t : `${t.slice(0, n - 1)}…`;
}

class Findings {
  constructor() { this.list = []; }

  // opts: { case, item, others, hint, excerpt, file, line }
  add(code, message, opts = {}) {
    const f = { code, message, file: opts.file || '', line: opts.line || 0, cases: [], related: [], hint: opts.hint || '', excerpt: opts.excerpt || '' };
    if (opts.case) {
      const c = opts.case;
      f.file = c.file;
      f.line = (opts.item && opts.item.line) || c.line;
      const others = opts.others || [];
      f.cases = [label(c), ...others.map(label)];
      f.related = others.map((o) => ({ file: o.file, line: o.line }));
    }
    this.list.push(f);
    return f;
  }
}

module.exports = { Findings, procedureText, expectedText, fullText, allItems, label, excerpt };
