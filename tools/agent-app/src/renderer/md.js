'use strict';

// Markdown / コードの描画。marked（解析）→ DOMPurify（無害化）→ highlight.js（配色）→ mermaid（図）。
// 会話の応答・Markdown プレビュー・コードビュアーが共有する。
(function initMd() {
  const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  let mermaidReady = false;
  function ensureMermaid() {
    if (mermaidReady || typeof mermaid === 'undefined') return;
    // htmlLabels: false … ラベルを foreignObject（HTML）ではなく SVG の text で出す。無害化で HTML が落ちても文字が残る
    mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'neutral', fontFamily: 'inherit', htmlLabels: false, flowchart: { htmlLabels: false } });
    mermaidReady = true;
  }

  // highlight.js の言語が無いときは自動判定（短いものは plaintext）。
  function highlight(code, lang) {
    if (typeof hljs === 'undefined') return escapeHtml(code);
    try {
      if (lang && hljs.getLanguage(lang)) return hljs.highlight(code, { language: lang, ignoreIllegals: true }).value;
      if (code.length < 200000) return hljs.highlightAuto(code).value;
    } catch { /* 配色に失敗しても本文は出す */ }
    return escapeHtml(code);
  }

  const renderer = {
    code({ text, lang }) {
      const l = String(lang || '').trim().split(/\s+/)[0].toLowerCase();
      if (l === 'mermaid') return `<pre class="mermaid-src"><code>${escapeHtml(text)}</code></pre>`;
      return `<pre class="code"><code class="hljs${l ? ` language-${escapeHtml(l)}` : ''}">${highlight(text, l)}</code></pre>`;
    },
  };
  let md = null;
  function engine() {
    if (md) return md;
    const M = typeof marked !== 'undefined' ? marked : null;
    if (!M) return null;
    md = new M.Marked({ gfm: true, breaks: false });
    md.use({ renderer });
    return md;
  }

  // Markdown → 無害化済み HTML 文字列。
  function render(text) {
    const m = engine();
    let html;
    try { html = m ? m.parse(String(text || '')) : `<pre>${escapeHtml(text)}</pre>`; } catch { html = `<pre>${escapeHtml(text)}</pre>`; }
    if (typeof DOMPurify === 'undefined') return html;
    return DOMPurify.sanitize(html, { FORBID_TAGS: ['style', 'form', 'button'] });
  }

  // 描画後の要素に対して mermaid を差し込む（非同期）。失敗した図は元のソースのまま残す。
  async function mountMermaid(root) {
    ensureMermaid();
    if (!mermaidReady) return;
    // ソースは <code> の本文から取る（属性は無害化で落ちることがある）
    const nodes = [...root.querySelectorAll('pre.mermaid-src')];
    for (const pre of nodes) {
      const code = pre.querySelector('code');
      const src = code ? code.textContent : '';
      if (!src.trim()) continue;
      const id = `mmd-${Math.random().toString(36).slice(2, 10)}`;
      try {
        const { svg } = await mermaid.render(id, src);
        const box = document.createElement('div');
        box.className = 'mermaid-box';
        box.innerHTML = DOMPurify.sanitize(svg, { USE_PROFILES: { svg: true, svgFilters: true, html: true }, ADD_TAGS: ['foreignObject'], ADD_ATTR: ['dominant-baseline', 'marker-end', 'marker-start', 'xmlns:xlink'] });
        pre.replaceWith(box);
      } catch (err) {
        pre.classList.add('mermaid-error');
        pre.title = `Mermaid: ${(err && err.message) || err}`;
        document.querySelectorAll(`#${id}, #d${id}`).forEach((n) => n.remove());
      }
    }
  }

  // Markdown を要素へ流し込む（リンクは外部ブラウザ想定で target を付けない。クリックは呼び出し側が抑止）。
  async function mount(el, text) {
    el.innerHTML = render(text);
    el.classList.add('md');
    await mountMermaid(el);
  }

  // コードビュアー: 行番号付きの表を組む。ハイライトの span は行をまたぐので、行ごとに閉じて開き直す。
  function codeLines(code, lang) {
    const html = highlight(code, lang);
    const lines = [];
    let open = [];
    let cur = '';
    const re = /<span class="([^"]*)">|<\/span>|\n/g;
    let last = 0;
    let m;
    const push = () => { lines.push(open.map((c) => `<span class="${c}">`).join('') + cur + '</span>'.repeat(open.length)); cur = ''; };
    while ((m = re.exec(html))) {
      cur += html.slice(last, m.index);
      last = re.lastIndex;
      if (m[0] === '\n') push();
      else if (m[0] === '</span>') { cur += '</span>'; open.pop(); }
      else { cur += m[0]; open.push(m[1]); }
    }
    cur += html.slice(last);
    push();
    return lines;
  }

  function mountCode(el, code, lang) {
    const lines = codeLines(code, lang);
    const rows = lines.map((h, i) => `<tr><td class="ln">${i + 1}</td><td class="lc"><span class="hljs">${h || ''}</span></td></tr>`).join('');
    el.innerHTML = `<table class="code-table"><tbody>${rows}</tbody></table>`;
  }

  window.MD = { render, mount, mountMermaid, mountCode, codeLines, highlight, escapeHtml };
})();
