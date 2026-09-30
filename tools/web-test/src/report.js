'use strict';
// 実行結果を results.json / report.md / report.html に書き出す。
// 画像は相対パスで参照するので、出力ディレクトリごと渡せばそのまま開ける。

const fs = require('fs');
const path = require('path');

const MARK = { passed: '✅', failed: '❌', skipped: '⏭' };
const LABEL = { passed: '合格', failed: '不合格', skipped: 'スキップ' };

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function caseLabel(c) {
  return c.variant ? `${c.id} [${c.variant}]` : c.id;
}

// 実行記録（コマンド・版・参照元のコミット）を行の配列にする
function contextLines(ctx) {
  if (!ctx) return [];
  const lines = [`コマンド: ${ctx.command}`, `環境: ${ctx.env ? ctx.env.name : 'local'}${ctx.env && ctx.env.config ? `（${ctx.env.config}）` : ''}`, `Node ${ctx.node} ・ Playwright ${ctx.playwright || '不明'} ・ web-test ${ctx.webTest} ・ ${ctx.os}`];
  for (const r of ctx.repos || []) lines.push(`${r.roles.join('・')}: ${r.root} @ ${r.sha ? r.sha.slice(0, 12) : '不明'}${r.branch ? ` (${r.branch})` : ''}${r.dirty ? ' ＋未コミットの変更' : ''}`);
  for (const f of ctx.files || []) lines.push(`ケース: ${f.path} sha256:${f.sha256 ? f.sha256.slice(0, 12) : '不明'}`);
  return lines;
}

function toMarkdown(report) {
  const { summary } = report;
  const lines = [`# テスト結果`, '', `- 実行: ${new Date(report.startedAt).toLocaleString('ja-JP')}`, `- 合計 ${summary.total} 件: 合格 ${summary.passed} / 不合格 ${summary.failed} / スキップ ${summary.skipped}`, ''];
  const ctxLines = contextLines(report.context);
  if (ctxLines.length) lines.push('<details><summary>実行記録</summary>', '', ...ctxLines.map((l) => `- ${l}`), '', '</details>', '');
  for (const s of report.suites) {
    lines.push(`## ${s.suite}`, '', `対象: ${s.baseUrl || '（未指定）'} ・ ブラウザ: ${s.browser}`, '');
    lines.push('| ID | 結果 | タイトル | 要件 | 失敗理由 |', '|---|---|---|---|---|');
    for (const c of s.cases) {
      lines.push(`| ${caseLabel(c)} | ${MARK[c.status]} ${LABEL[c.status]} | ${c.title.replace(/\|/g, '\\|')} | ${c.requirement || ''} | ${(c.error || '').replace(/\|/g, '\\|')} |`);
    }
    lines.push('');
    for (const c of s.cases) {
      if (!c.screenshots || !c.screenshots.length) continue;
      lines.push(`### ${caseLabel(c)} ${c.title}`, '');
      for (const sh of c.screenshots) lines.push(`![${sh.name}](${sh.file})`);
      lines.push('');
    }
  }
  return lines.join('\n');
}

function toHtml(report) {
  const { summary } = report;
  const suites = report.suites.map((s) => {
    const cases = s.cases.map((c) => {
      const steps = (c.steps || []).map((st, i) => `
        <li class="step ${st.status}${st.setup ? ' setup' : ''}">
          <div class="step-head"><span class="no">${i + 1}</span><code>${esc(st.action)}</code>${st.note ? `<span class="note">${esc(st.note)}</span>` : ''}<span class="dur">${st.durationMs ?? ''}ms</span></div>
          ${st.error ? `<div class="err">${esc(st.error)}</div>` : ''}
          ${st.screenshot ? `<a href="${esc(st.screenshot)}" target="_blank"><img loading="lazy" src="${esc(st.screenshot)}" alt="${esc(st.action)}"></a>` : ''}
        </li>`).join('');
      const consoleErr = c.consoleErrors && c.consoleErrors.length
        ? `<details class="console"><summary>ブラウザのコンソールエラー ${c.consoleErrors.length} 件</summary><pre>${esc(c.consoleErrors.join('\n'))}</pre></details>` : '';
      return `
      <details class="case ${c.status}" ${c.status === 'failed' ? 'open' : ''}>
        <summary><span class="badge ${c.status}">${LABEL[c.status]}</span><b>${esc(caseLabel(c))}</b> ${esc(c.title)}${c.requirement ? ` <span class="req">${esc(c.requirement)}</span>` : ''}<span class="dur">${(c.durationMs / 1000).toFixed(1)}s</span></summary>
        ${c.error ? `<div class="err">${esc(c.error)}</div>` : ''}
        ${consoleErr}
        <ol class="steps">${steps}</ol>
      </details>`;
    }).join('');
    return `<section><h2>${esc(s.suite)}</h2><p class="sub">${esc(s.baseUrl || '')} ・ ${esc(s.browser)}${s.file ? ` ・ ${esc(path.basename(s.file))}` : ''}</p>${cases}</section>`;
  }).join('');
  return `<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>テスト結果</title>
<style>
:root{--bg:#f6f7f9;--panel:#fff;--line:#dfe3e8;--text:#1f2328;--sub:#656d76;--ok:#1a7f37;--ng:#cf222e;--skip:#8c959f}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--panel:#161b22;--line:#30363d;--text:#e6edf3;--sub:#8d96a0;--ok:#3fb950;--ng:#f85149;--skip:#6e7681}}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 system-ui,-apple-system,"Segoe UI","Hiragino Sans","Meiryo",sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:24px 0 2px}.sub{color:var(--sub);margin:0 0 8px}
.summary{display:flex;gap:12px;margin:12px 0}.summary div{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 14px}
.summary b{font-size:20px;display:block}.summary .ok b{color:var(--ok)}.summary .ng b{color:var(--ng)}
.case{background:var(--panel);border:1px solid var(--line);border-radius:8px;margin:8px 0;padding:0 12px}
.case>summary{cursor:pointer;padding:10px 0;display:flex;gap:8px;align-items:center}
.badge{font-size:12px;border-radius:10px;padding:1px 8px;color:#fff}.badge.passed{background:var(--ok)}.badge.failed{background:var(--ng)}.badge.skipped{background:var(--skip)}
.req{font-size:12px;color:var(--sub);border:1px solid var(--line);border-radius:4px;padding:0 4px}
.dur{margin-left:auto;color:var(--sub);font-size:12px}
.err{color:var(--ng);white-space:pre-wrap;margin:4px 0 8px}
.steps{list-style:none;padding:0;margin:0 0 12px}.step{border-top:1px solid var(--line);padding:6px 0}.step.setup{opacity:.7}
.step-head{display:flex;gap:8px;align-items:baseline}.no{color:var(--sub);min-width:1.5em;text-align:right}
.step.failed code{color:var(--ng)}.note{color:var(--sub)}
.step img{display:block;max-width:min(100%,560px);max-height:360px;margin:6px 0 0 2em;border:1px solid var(--line);border-radius:4px}
.context{margin:0 0 12px;color:var(--sub)}.context ul{margin:6px 0;padding-left:20px;font-size:12px;overflow-wrap:anywhere}
.console pre{font-size:12px;white-space:pre-wrap}
</style></head><body><main>
<h1>テスト結果</h1><p class="sub">${esc(new Date(report.startedAt).toLocaleString('ja-JP'))}</p>
<div class="summary"><div><b>${summary.total}</b>合計</div><div class="ok"><b>${summary.passed}</b>合格</div><div class="ng"><b>${summary.failed}</b>不合格</div><div><b>${summary.skipped}</b>スキップ</div></div>
${report.context ? `<details class="context"><summary>実行記録</summary><ul>${contextLines(report.context).map((l) => `<li>${esc(l)}</li>`).join('')}</ul></details>` : ''}
${suites}
</main></body></html>
`;
}

function writeReport(report, outDir) {
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(path.join(outDir, 'results.json'), JSON.stringify(report, null, 2));
  fs.writeFileSync(path.join(outDir, 'report.md'), toMarkdown(report));
  fs.writeFileSync(path.join(outDir, 'report.html'), toHtml(report));
  return { html: path.join(outDir, 'report.html'), md: path.join(outDir, 'report.md'), json: path.join(outDir, 'results.json') };
}

module.exports = { writeReport, toMarkdown, toHtml };
