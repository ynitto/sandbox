'use strict';

// 「ファイル」画面: 登録したリポジトリのツリーと、コード / Markdown / 画像のビュアー。
(function initFiles() {
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };

  // worktree … 見ているフォルダ（'' はリポジトリ本体）。main 側はこの名前だけを受け取り、
  // <リポジトリ>/.worktrees/<名前> を組み立てる（画面から生のパスは渡さない）。
  const state = { repo: '', worktree: '', open: null, mode: 'code', wrap: false, expanded: new Set(), filterTimer: null };

  const LANG_LABEL = { javascript: 'JS', typescript: 'TS', python: 'Python', markdown: 'Markdown', json: 'JSON', yaml: 'YAML', xml: 'XML/HTML', bash: 'Shell', plaintext: 'Text', csharp: 'C#', cpp: 'C++' };
  const fmtSize = (n) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);

  // ---- ツリー ----------------------------------------------------------------

  function nodeFor(entry) {
    const li = el('li', `node ${entry.type}`);
    li.dataset.rel = entry.rel;
    const row = el('div', 'row');
    row.append(el('span', 'arrow', entry.type === 'dir' ? '▸' : ''));
    row.append(el('span', 'icon', entry.type === 'dir' ? '📁' : iconFor(entry)));
    row.append(el('span', 'name', entry.name));
    if (entry.type === 'file' && entry.size) row.append(el('span', 'size', fmtSize(entry.size)));
    li.append(row);
    if (entry.type === 'dir') {
      row.onclick = () => toggleDir(li, entry.rel);
      const kids = el('ul', 'children');
      kids.hidden = true;
      li.append(kids);
    } else {
      row.onclick = () => openFile(entry.rel);
    }
    return li;
  }

  function iconFor(entry) {
    const l = entry.language;
    if (l === 'markdown') return '📝';
    if (/\.(png|jpe?g|gif|webp|svg|bmp|ico|avif)$/i.test(entry.name)) return '🖼';
    if (l) return '📄';
    return '▫';
  }

  async function loadChildren(li, rel) {
    const kids = li.querySelector(':scope > ul.children');
    kids.replaceChildren(el('li', 'empty', '読み込み中…'));
    try {
      const res = await api.listDir(state.repo, state.worktree, rel);
      kids.replaceChildren();
      for (const e of res.entries) kids.append(nodeFor(e));
      if (!res.entries.length) kids.append(el('li', 'empty', '（空）'));
    } catch (err) {
      kids.replaceChildren(el('li', 'empty', err.message));
    }
  }

  async function toggleDir(li, rel, force) {
    const kids = li.querySelector(':scope > ul.children');
    const open = force != null ? force : kids.hidden;
    if (open && !kids.dataset.loaded) { await loadChildren(li, rel); kids.dataset.loaded = '1'; }
    kids.hidden = !open;
    li.querySelector(':scope > .row > .arrow').textContent = open ? '▾' : '▸';
    if (open) state.expanded.add(rel); else state.expanded.delete(rel);
  }

  async function renderRoot() {
    const root = $('tree');
    root.replaceChildren();
    if (!state.repo) { root.append(el('li', 'empty', 'リポジトリを選ぶ')); return; }
    root.append(el('li', 'empty', '読み込み中…'));
    try {
      const res = await api.listDir(state.repo, state.worktree, '');
      root.replaceChildren();
      for (const e of res.entries) root.append(nodeFor(e));
      if (!res.entries.length) root.append(el('li', 'empty', '（空のフォルダ）'));
    } catch (err) {
      root.replaceChildren(el('li', 'empty', err.message));
    }
  }

  // パスを辿って親フォルダを開き、該当ノードを選択状態にする。
  async function reveal(rel) {
    const parts = rel.split('/');
    let ul = $('tree');
    for (let i = 0; i < parts.length - 1; i += 1) {
      const dirRel = parts.slice(0, i + 1).join('/');
      const li = [...ul.children].find((n) => n.dataset && n.dataset.rel === dirRel);
      if (!li) return;
      await toggleDir(li, dirRel, true);
      ul = li.querySelector(':scope > ul.children');
    }
    markActive(rel);
  }

  function markActive(rel) {
    document.querySelectorAll('#tree .row.active').forEach((n) => n.classList.remove('active'));
    const li = document.querySelector(`#tree li.node[data-rel="${CSS.escape(rel)}"]`);
    if (li) { li.querySelector(':scope > .row').classList.add('active'); li.scrollIntoView({ block: 'nearest' }); }
  }

  // ---- 絞り込み（名前検索） ----------------------------------------------------

  async function applyFilter() {
    const q = $('tree-filter').value.trim();
    const box = $('tree-results');
    if (!q) { box.hidden = true; $('tree').hidden = false; return; }
    $('tree').hidden = true;
    box.hidden = false;
    box.replaceChildren(el('li', 'empty', '検索中…'));
    try {
      const hits = await api.findFiles(state.repo, state.worktree, q);
      box.replaceChildren();
      for (const h of hits) {
        const li = el('li', `node ${h.type}`);
        const row = el('div', 'row');
        row.append(el('span', 'icon', h.type === 'dir' ? '📁' : '📄'), el('span', 'name', h.rel));
        row.title = h.rel;
        row.onclick = () => { if (h.type === 'file') openFile(h.rel); };
        li.append(row);
        box.append(li);
      }
      if (!hits.length) box.append(el('li', 'empty', '見つからない'));
    } catch (err) {
      box.replaceChildren(el('li', 'empty', err.message));
    }
  }

  // ---- ビュアー ----------------------------------------------------------------

  async function openFile(rel, { silent = false } = {}) {
    if (!state.repo) return;
    let file;
    try { file = await api.readFile(state.repo, state.worktree, rel); } catch (err) { showNotice(err.message); return; }
    state.open = file;
    state.mode = file.language === 'markdown' ? (state.mdPreferred === false ? 'code' : 'preview') : 'code';
    if (!silent) markActive(rel);
    renderViewer();
    if (!state.worktree) api.saveConfig({ lastFiles: { [state.repo]: rel } }).catch(() => {});
  }

  function showNotice(text) {
    const body = $('viewer-body');
    body.className = 'viewer-body';
    body.replaceChildren(el('div', 'viewer-empty', text));
  }

  function renderViewer() {
    const f = state.open;
    const head = $('viewer-head');
    const body = $('viewer-body');
    if (!f) {
      head.hidden = true;
      body.className = 'viewer-body';
      body.replaceChildren(el('div', 'viewer-empty', 'ツリーからファイルを選ぶ'));
      return;
    }
    head.hidden = false;
    const crumbs = $('viewer-path');
    crumbs.replaceChildren();
    const parts = f.rel.split('/');
    parts.forEach((p, i) => {
      if (i) crumbs.append(el('span', 'sep', '/'));
      crumbs.append(el('span', i === parts.length - 1 ? 'leaf' : '', p));
    });
    const meta = [];
    if (f.language) meta.push(LANG_LABEL[f.language] || f.language);
    meta.push(fmtSize(f.size));
    if (f.kind === 'text') meta.push(`${f.lines} 行`);
    if (f.truncated) meta.push('先頭 2 MB のみ');
    $('viewer-meta').textContent = meta.join(' · ');
    const isMd = f.kind === 'text' && f.language === 'markdown';
    $('viewer-mode').hidden = !isMd;
    $('viewer-mode-code').classList.toggle('on', state.mode === 'code');
    $('viewer-mode-preview').classList.toggle('on', state.mode === 'preview');
    $('viewer-wrap').classList.toggle('on', state.wrap);
    $('viewer-wrap').hidden = f.kind !== 'text';

    body.className = 'viewer-body';
    body.replaceChildren();
    if (f.kind === 'image') {
      const img = document.createElement('img');
      img.src = f.dataUrl;
      img.alt = f.name;
      body.classList.add('image');
      body.append(img);
    } else if (f.kind === 'binary') {
      body.append(el('div', 'viewer-empty', `${f.reason}のため表示できない（「開く」で既定のアプリへ）`));
    } else if (isMd && state.mode === 'preview') {
      const box = el('div', 'md-preview');
      body.classList.add('preview');
      body.append(box);
      MD.mount(box, f.text).catch(() => {});
    } else {
      const box = el('div', `code-view${state.wrap ? ' wrap' : ''}`);
      body.append(box);
      MD.mountCode(box, f.text, f.language || '');
    }
  }

  // ---- 配線 --------------------------------------------------------------------

  // 見るフォルダを決める。リポジトリか作業フォルダのどちらかが変わったら読み直す。
  async function setRoot(repo, worktree = '', { lastFile = '' } = {}) {
    const next = String(worktree || '');
    if (repo === state.repo && next === state.worktree) return;
    state.repo = repo || '';
    state.worktree = next;
    state.open = null;
    state.expanded.clear();
    $('tree-filter').value = '';
    $('tree-results').hidden = true;
    $('tree').hidden = false;
    renderViewer();
    renderRootSelect();
    await renderRoot();
    if (lastFile) { await openFile(lastFile, { silent: true }); await reveal(lastFile); }
  }

  // 作業フォルダの選択肢（一覧は renderer 側が持っている）。見る先を変えるのは setRoot だけ。
  let roots = [];
  function renderRoots(items) {
    roots = (items || []).filter((w) => w.main || w.selectable);
    renderRootSelect();
  }

  function renderRootSelect() {
    const sel = $('tree-root');
    sel.replaceChildren();
    const add = (value, label) => { const o = el('option', '', label); o.value = value; sel.append(o); };
    add('', 'リポジトリ本体');
    for (const w of roots.filter((x) => !x.main)) add(w.name, `${w.name}（${w.branch || 'detached'}）`);
    if (state.worktree && !roots.some((w) => w.name === state.worktree)) add(state.worktree, state.worktree);
    sel.value = state.worktree;
  }

  function init() {
    $('tree-root').onchange = () => setRoot(state.repo, $('tree-root').value, {});
    $('tree-filter').addEventListener('input', () => { clearTimeout(state.filterTimer); state.filterTimer = setTimeout(applyFilter, 250); });
    $('tree-refresh').onclick = async () => { state.expanded.clear(); await renderRoot(); if (state.open) reveal(state.open.rel); };
    $('viewer-mode-code').onclick = () => { state.mode = 'code'; state.mdPreferred = false; renderViewer(); };
    $('viewer-mode-preview').onclick = () => { state.mode = 'preview'; state.mdPreferred = true; renderViewer(); };
    $('viewer-wrap').onclick = () => { state.wrap = !state.wrap; renderViewer(); };
    $('viewer-reload').onclick = () => state.open && openFile(state.open.rel, { silent: true });
    $('viewer-open').onclick = () => state.open && api.openFile(state.repo, state.worktree, state.open.rel).catch((e) => showNotice(e.message));
    $('viewer-show').onclick = () => state.open && api.showFile(state.repo, state.worktree, state.open.rel).catch(() => {});
    // プレビュー内のリンクは外へ飛ばさない（Electron の中でナビゲートさせない）
    $('viewer-body').addEventListener('click', (e) => {
      const a = e.target.closest('a[href]');
      if (!a) return;
      e.preventDefault();
      const href = a.getAttribute('href') || '';
      if (/^https?:/i.test(href)) return;                      // 外部リンクは何もしない（main の will-navigate でも止める）
      if (!state.open || /^#/.test(href)) return;
      const base = state.open.rel.split('/').slice(0, -1);
      const target = href.split('/').reduce((acc, seg) => { if (seg === '..') acc.pop(); else if (seg && seg !== '.') acc.push(seg); return acc; }, base).join('/');
      openFile(target).then(() => reveal(target));
    });
  }

  window.Files = { init, setRoot, renderRoots, openFile, reveal, state };
})();
