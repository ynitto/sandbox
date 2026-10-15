'use strict';
// 指摘の出力（text / json / markdown）。

const { CATEGORY, SEVERITY_ORDER, SEVERITY_LABEL } = require('./rules');
const { version } = require('../package.json');

const CATEGORIES = Object.values(CATEGORY);

const sortKey = (a, b) => (a.file < b.file ? -1 : a.file > b.file ? 1 : 0) || (a.line - b.line)
  || (SEVERITY_ORDER[b.severity] - SEVERITY_ORDER[a.severity]) || (a.code < b.code ? -1 : 1);

function summary(result) {
  const byCategory = Object.fromEntries(CATEGORIES.map((c) => [c, 0]));
  const bySeverity = { error: 0, warning: 0, info: 0 };
  for (const f of result.findings) { byCategory[f.category]++; bySeverity[f.severity]++; }
  return { files: result.files.length, cases: result.cases.length, byCategory, bySeverity, spec: result.spec, source: result.source };
}

function summaryLine(s) {
  const cat = CATEGORIES.map((c) => `${c} ${s.byCategory[c]}`).join(' / ');
  const sev = Object.entries(s.bySeverity).map(([k, n]) => `${SEVERITY_LABEL[k]} ${n}`).join('・');
  const sources = [s.spec && `仕様 ${s.spec.files} ファイル`, s.source && `実装 ${s.source.files} ファイル`].filter(Boolean).join('・');
  return `${s.files} ファイル・ケース ${s.cases} 件${sources ? `（${sources}と突き合わせ）` : ''}: ${cat}（${sev}）`;
}

function where(f) {
  const rel = f.related.filter((r) => r.line).map((r) => (r.file === f.file ? `${r.line} 行目` : `${r.file}:${r.line}`));
  return rel.length ? `（ほかに ${rel.join('・')}）` : '';
}

// 何を点検したか（オプション無しで動かしたとき、どこを見たかが分かるように）
function inputsLine(result) {
  const i = result.inputs;
  if (!i) return '';
  const parts = [`ケース: ${i.cases.join('、')}`];
  if (i.spec.length) parts.push(`仕様: ${i.spec.join('、')}`);
  if (i.source.length) parts.push(`実装: ${i.source.join('、')}`);
  const from = i.config ? `（${i.config}）` : i.auto ? '（設定ファイルが無いので、見つけたフォルダを使用。testcase-lint init で設定を作れる）' : '';
  return `${parts.join(' / ')}${from}`;
}

function renderText(result) {
  const s = summary(result);
  const head = inputsLine(result);
  const out = head ? [head] : [];
  let current = null;
  for (const f of [...result.findings].sort(sortKey)) {
    if (f.file !== current) { current = f.file; out.push('', f.file); }
    const line = f.line ? String(f.line).padStart(5) : '    -';
    const ids = f.cases.filter((x) => !x.startsWith('(')); // ID のないケースは行番号で示す
    const who = ids.length ? ` ${ids.slice(0, 4).join(', ')}${ids.length > 4 ? ' ほか' : ''}` : '';
    out.push(`${line}  ${SEVERITY_LABEL[f.severity].padEnd(3, ' ')} ${f.code} ${f.category}${who}  ${f.message}${where(f)}`);
    if (f.excerpt) out.push(`${' '.repeat(7)}  「${f.excerpt}」`);
    if (f.hint) out.push(`${' '.repeat(7)}  → ${f.hint}`);
  }
  out.push('');
  for (const e of [...((result.spec && result.spec.errors) || []), ...((result.source && result.source.errors) || [])]) out.push(`注意: ${e}`);
  const untouched = (result.spec && result.spec.untouched) || [];
  if (untouched.length) out.push(`どのケースも触れていない仕様（突き合わせの対象外）: ${untouched.slice(0, 5).join('、')}${untouched.length > 5 ? ` ほか ${untouched.length - 5} 件` : ''}`);
  out.push(result.findings.length ? summaryLine(s) : `${summaryLine(s)} — 指摘はありません`);
  return `${out.join('\n')}\n`;
}

function renderJson(result) {
  return `${JSON.stringify({ tool: 'testcase-lint', version, inputs: result.inputs, summary: summary(result), findings: [...result.findings].sort(sortKey) }, null, 2)}\n`;
}

const md = (s) => String(s).replace(/\|/g, '\\|').replace(/\n/g, ' ');

function renderMarkdown(result) {
  const s = summary(result);
  const head = inputsLine(result);
  const out = ['# テストケースの点検結果', '', ...(head ? [head, ''] : []), summaryLine(s), ''];
  for (const cat of CATEGORIES) {
    const fs = result.findings.filter((f) => f.category === cat).sort(sortKey);
    out.push(`## ${cat}（${fs.length} 件）`, '');
    if (!fs.length) { out.push('なし', ''); continue; }
    out.push('| 重さ | 規則 | 場所 | ケース | 指摘 | 直し方 |', '|---|---|---|---|---|---|');
    for (const f of fs) {
      const msg = f.message + (f.excerpt ? `<br>「${f.excerpt}」` : '') + where(f);
      out.push(`| ${SEVERITY_LABEL[f.severity]} | ${f.code} ${f.name} | ${md(f.line ? `${f.file}:${f.line}` : f.file)} | ${md(f.cases.join(', '))} | ${md(msg)} | ${md(f.hint)} |`);
    }
    out.push('');
  }
  return out.join('\n');
}

const RENDERERS = { text: renderText, json: renderJson, markdown: renderMarkdown, md: renderMarkdown };

module.exports = { RENDERERS, summary, summaryLine };
