'use strict';

/* global api */

// ---------------------------------------------------------------------------
// 状態
// ---------------------------------------------------------------------------

const state = {
  config: null,
  candidates: [],
  selectedIndex: -1,
  // 選択した候補とそれに紐づくイシュー / MR（コメント等の操作対象の母集団）
  pages: [],
  // 左ペイン(0)=イシュー、右ペイン(1)=MR。
  // タブは GitLab ページ（kind:'page'）のほか、リーダーモード（kind:'reader'）と
  // 要約（kind:'summary'）のローカルタブを持てる。
  panes: [
    { tabs: [], active: -1 },
    { tabs: [], active: -1 },
  ],
  lastSummary: '',
  busy: false,
};

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// ユーティリティ
// ---------------------------------------------------------------------------

let toastTimer = null;
function toast(msg, isError = false) {
  const el = $('toast');
  el.textContent = msg;
  el.classList.toggle('error', isError);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), isError ? 8000 : 3500);
}

async function guard(label, fn) {
  if (state.busy) {
    toast('前の操作が完了するまでお待ちください', true);
    return undefined;
  }
  state.busy = true;
  try {
    return await fn();
  } catch (err) {
    toast(`${label}: ${err.message}`, true);
    return undefined;
  } finally {
    state.busy = false;
  }
}

function targetOf(page) {
  return { projectId: page.projectId, type: page.type, iid: page.iid };
}

// window.confirm は Electron ではダイアログを閉じた後にキーボード入力が
// 効かなくなる既知問題があるため、<dialog> ベースの確認を使う
function confirmDialog(message) {
  return new Promise((resolve) => {
    const dlg = $('confirm-dialog');
    $('confirm-desc').textContent = message;
    const finish = (ok) => {
      cleanup();
      if (dlg.open) dlg.close();
      resolve(ok);
    };
    const onOk = () => finish(true);
    const onCancel = () => finish(false);
    const onClose = () => finish(false); // Esc キーで閉じた場合
    function cleanup() {
      $('btn-confirm-ok').removeEventListener('click', onOk);
      $('btn-confirm-cancel').removeEventListener('click', onCancel);
      dlg.removeEventListener('close', onClose);
    }
    $('btn-confirm-ok').addEventListener('click', onOk);
    $('btn-confirm-cancel').addEventListener('click', onCancel);
    dlg.addEventListener('close', onClose);
    dlg.showModal();
  });
}

// ペインの表示中ページ（リーダー / 要約タブが手前ならその元ページ）を返す
function activePage(pane) {
  const tab = paneCurrentPageTab(pane);
  return tab ? tab.page : null;
}

// 操作対象: 表示されているイシューがあればそれ、MR だけなら MR
function primaryTarget() {
  return activePage(0) || activePage(1);
}

function statusOf(item) {
  const label = (item.labels || []).find((l) => l.startsWith('status:'));
  return label ? label.slice('status:'.length) : '';
}

// 承認・却下の操作対象 MR: 右ペインのアクティブタブに表示中の MR。
// イシューと MR のタイトルはブレることがあるため、タイトルの一致は
// 操作対象の解決やボタンのグレーアウト条件には使わない。
function activeMR() {
  return activePage(1);
}

// 各アクションボタン共通のコメント本文（# ボタン名 + 入力テキスト）
function actionComment(name) {
  const text = $('comment-input').value.trim();
  return text ? `# ${name}\n\n${text}` : `# ${name}`;
}

function escapeHtml(s) {
  return String(s)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}

// 要約（Markdown）タブ表示用の最小レンダラ。
// 見出し・箇条書き・コードブロック・太字・インラインコード・リンクに対応。
function mdToHtml(md) {
  const inline = (s) =>
    escapeHtml(s)
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(https?:\/\/[^\s<)]+)/g, '<a href="$1">$1</a>');

  const text = String(md).replace(/<!--[\s\S]*?-->/g, '');
  const lines = text.split(/\r?\n/);
  const out = [];
  let inCode = false;
  let listOpen = false;
  const closeList = () => {
    if (listOpen) {
      out.push('</ul>');
      listOpen = false;
    }
  };
  for (const ln of lines) {
    if (ln.startsWith('```')) {
      closeList();
      inCode = !inCode;
      out.push(inCode ? '<pre>' : '</pre>');
      continue;
    }
    if (inCode) {
      out.push(escapeHtml(ln));
      continue;
    }
    const h = ln.match(/^(#{1,4}) (.+)$/);
    if (h) {
      closeList();
      const lv = h[1].length;
      out.push(`<h${lv}>${inline(h[2])}</h${lv}>`);
      continue;
    }
    const li = ln.match(/^\s*- (.+)$/);
    if (li) {
      if (!listOpen) {
        out.push('<ul>');
        listOpen = true;
      }
      out.push(`<li>${inline(li[1])}</li>`);
      continue;
    }
    closeList();
    if (ln.trim() !== '') out.push(`<p>${inline(ln)}</p>`);
  }
  closeList();
  if (inCode) out.push('</pre>');
  return out.join('\n');
}

// ---------------------------------------------------------------------------
// 検索条件のキャッシュ（config.searchCache に保存し、次回起動時に復元）
// ---------------------------------------------------------------------------

function selectOptions(sel) {
  return [...sel.options]
    .slice(1) // 先頭の「（指定なし）」を除く
    .map((o) => ({ value: o.value, text: o.textContent }));
}

function fillSelect(sel, options, selected) {
  sel.innerHTML = '<option value="">（指定なし）</option>';
  for (const o of options || []) {
    const opt = document.createElement('option');
    opt.value = o.value;
    opt.textContent = o.text;
    sel.appendChild(opt);
  }
  sel.value = selected || '';
  if (sel.value !== (selected || '')) sel.value = '';
}

function collectSearchCache() {
  return {
    groupSearch: $('group-search').value,
    groups: selectOptions($('group-select')),
    groupId: $('group-select').value,
    projectSearch: $('project-search').value,
    projects: selectOptions($('project-select')),
    projectId: $('project-select').value,
    labels: $('label-input').value,
    author: $('author-input').value,
    type: $('type-select').value,
    state: $('state-select').value,
    keyword: $('keyword-input').value,
  };
}

let persistTimer = null;
function persistSearchCache() {
  clearTimeout(persistTimer);
  persistTimer = setTimeout(async () => {
    try {
      state.config.searchCache = collectSearchCache();
      state.config = await api.saveConfig(state.config);
    } catch {
      /* キャッシュ保存の失敗は致命的ではない */
    }
  }, 400);
}

function restoreSearchCache() {
  const c = state.config.searchCache;
  if (!c || typeof c !== 'object') return;
  $('group-search').value = c.groupSearch || '';
  fillSelect($('group-select'), c.groups, c.groupId);
  $('project-search').value = c.projectSearch || '';
  fillSelect($('project-select'), c.projects, c.projectId);
  $('label-input').value = c.labels || '';
  $('author-input').value = c.author || '';
  if (c.type) $('type-select').value = c.type;
  if (c.state) $('state-select').value = c.state;
  $('keyword-input').value = c.keyword || '';
}

// ---------------------------------------------------------------------------
// フィルタ（グループ / プロジェクト / ラベル）
// ---------------------------------------------------------------------------

async function loadGroups() {
  await guard('グループ取得', async () => {
    const groups = await api.glGroups($('group-search').value.trim());
    fillSelect(
      $('group-select'),
      groups.map((g) => ({ value: String(g.id), text: g.full_path })),
      $('group-select').value
    );
    toast(`グループ ${groups.length} 件を取得しました`);
    persistSearchCache();
  });
}

async function loadProjects() {
  await guard('プロジェクト取得', async () => {
    const groupId = $('group-select').value || undefined;
    const projects = await api.glProjects({
      groupId,
      search: $('project-search').value.trim() || undefined,
    });
    fillSelect(
      $('project-select'),
      projects.map((p) => ({ value: String(p.id), text: p.path_with_namespace })),
      $('project-select').value
    );
    toast(`プロジェクト ${projects.length} 件を取得しました`);
    persistSearchCache();
  });
}

async function loadLabelSuggestions() {
  const projectId = $('project-select').value || undefined;
  const groupId = $('group-select').value || undefined;
  if (!projectId && !groupId) return;
  try {
    const labels = await api.glLabels({ projectId, groupId });
    const dl = $('label-list');
    dl.innerHTML = '';
    for (const l of labels) {
      const opt = document.createElement('option');
      opt.value = l.name;
      dl.appendChild(opt);
    }
  } catch {
    /* サジェストは失敗しても致命的ではない */
  }
}

// ---------------------------------------------------------------------------
// 候補検索・一覧
// ---------------------------------------------------------------------------

function collectFilters() {
  return {
    type: $('type-select').value, // 種別は候補一覧の絞り込みにのみ使う
    groupId: $('group-select').value || undefined,
    projectId: $('project-select').value || undefined,
    labels: $('label-input')
      .value.split(',')
      .map((s) => s.trim())
      .filter(Boolean),
    state: $('state-select').value,
    search: $('keyword-input').value.trim(),
    author: $('author-input').value.trim(),
  };
}

async function searchCandidates() {
  await guard('候補検索', async () => {
    const items = await api.glSearch(collectFilters());
    state.candidates = items;
    state.selectedIndex = -1;
    renderCandidates();
    $('candidate-count').textContent = `候補: ${items.length} 件`;
    persistSearchCache();
  });
}

function renderCandidates() {
  const ul = $('candidates');
  ul.innerHTML = '';
  state.candidates.forEach((c, i) => {
    const li = document.createElement('li');
    li.classList.toggle('selected', i === state.selectedIndex);
    const labels = c.labels
      .slice(0, 4)
      .map((l) => `<span class="chip">${escapeHtml(l)}</span>`)
      .join('');
    li.innerHTML =
      `<span class="badge ${c.type}">${c.type === 'issue' ? 'Issue' : 'MR'}</span>` +
      `<span class="cand-meta">${escapeHtml(c.ref)} · ${escapeHtml(c.state)}</span>` +
      `<span class="cand-title">${escapeHtml(c.title)}</span>` +
      labels;
    li.addEventListener('click', () => selectCandidate(i));
    ul.appendChild(li);
  });
}

// タイトル比較用の正規化。GitLab がイシューから MR を作るときの
// 「Draft:」「WIP:」接頭辞と『Resolve "<イシュータイトル>"』形式を吸収する。
function normalizeTitle(s) {
  let t = String(s || '').trim();
  let prev;
  do {
    prev = t;
    t = t.replace(/^(draft|wip)\s*:\s*/i, '');
  } while (t !== prev);
  const m = t.match(/^resolve\s+"(.+)"$/i);
  if (m) t = m[1];
  return t.toLowerCase();
}

// タイトルの類似度（0〜1）。正規化後の文字バイグラムの Dice 係数による簡易判定。
// イシューと MR のタイトルは完全一致しないことが多いため、アクティブタブの
// 初期選択には一致ではなくこの類似度を使う。
// 「feat:」等の conventional commit 接頭辞と空白・記号は比較から除外する。
function titleSimilarity(a, b) {
  const strip = (s) =>
    normalizeTitle(s)
      .replace(/^[a-z]+(\([^)]*\))?!?\s*:\s*/, '')
      .replace(/[\s　!-/:-@[-`{-~、。・「」『』（）]/g, '');
  const na = strip(a);
  const nb = strip(b);
  if (!na || !nb) return 0;
  if (na === nb) return 1;
  const grams = (s) => {
    const set = new Set();
    if (s.length < 2) set.add(s);
    for (let i = 0; i < s.length - 1; i++) set.add(s.slice(i, i + 2));
    return set;
  };
  const ga = grams(na);
  const gb = grams(nb);
  let hit = 0;
  for (const g of ga) if (gb.has(g)) hit++;
  return (2 * hit) / (ga.size + gb.size);
}

// 候補を選択すると、候補 + 紐づくページを取得し、
// イシューを左ペイン・MR を右ペインへ振り分けてタブ表示する。
async function selectCandidate(index) {
  await guard('関連ページ取得', async () => {
    state.selectedIndex = index;
    renderCandidates();
    const cand = state.candidates[index];
    const related = await api.glRelated(targetOf(cand));
    state.pages = [cand, ...related];
    const issues = state.pages.filter((p) => p.type === 'issue');
    const mrs = state.pages.filter((p) => p.type === 'mr');
    state.panes[0] = {
      tabs: issues.map((p) => ({ kind: 'page', page: p })),
      active: issues.length ? 0 : -1,
    };
    state.panes[1] = {
      tabs: mrs.map((p) => ({ kind: 'page', page: p })),
      active: mrs.length ? 0 : -1,
    };
    // イシューに紐づく MR が複数ある場合は、イシューとタイトルが最も似ている
    // MR のタブをアクティブにする（タイトルはブレるため一致ではなく類似度で判定）
    if (cand.type === 'issue' && mrs.length > 1) {
      let best = -1;
      let bestScore = 0;
      mrs.forEach((m, i) => {
        const score = titleSimilarity(m.title, cand.title);
        if (score > bestScore) {
          bestScore = score;
          best = i;
        }
      });
      if (best >= 0) state.panes[1].active = best;
    }
    state.lastSummary = '';
    renderPanes();
    renderTargetInfo();
    if (related.length === 0) {
      toast('紐づくイシュー / MR は見つかりませんでした');
    }
    // 表示した MR のコンフリクト / 未解決レビューコメントを事前チェックする
    // （guard の完了を待たせないため待たずに走らせる。失敗は内部で握りつぶす）
    checkActiveMRHealth();
  });
}

// ディープリンク（gitlab-review-viewer://open?url=<GitLab web_url>）から
// 対象を解決し、候補一覧の先頭に挿入して選択する。kiro-projects-viewer 等の
// 外部ツールがレビューをシームレスに引き継ぐための入り口。
async function openFromUrl(rawUrl) {
  let target = String(rawUrl || '');
  try {
    const u = new URL(target);
    if (u.protocol === 'gitlab-review-viewer:') target = u.searchParams.get('url') || '';
  } catch {
    /* そのまま GitLab URL として扱う */
  }
  if (!target) return toast('開く対象の URL がありません', true);
  const resolved = await guard('外部から開く', async () => {
    const item = await api.glResolveUrl(target);
    const i = state.candidates.findIndex(
      (c) => c.projectId === item.projectId && c.type === item.type && c.iid === item.iid
    );
    if (i >= 0) state.candidates.splice(i, 1);
    state.candidates.unshift(item);
    $('candidate-count').textContent = `候補: ${state.candidates.length} 件`;
    return item;
  });
  // selectCandidate は自前の guard を持つためネストさせずに続けて呼ぶ
  if (resolved) await selectCandidate(0);
}

// ---------------------------------------------------------------------------
// ペイン表示（タブ + URL バー + webview / ローカル表示）
// ---------------------------------------------------------------------------

function pageLabel(p) {
  const kind = p.type === 'issue' ? 'Issue' : 'MR';
  return `${kind} ${p.ref || `#${p.iid}`}`;
}

// タブを開いたときの初期 URL。MR はレビュー対象の差分（/diffs）を最初から表示する
function pageStartUrl(p) {
  return p.type === 'mr' && p.url ? `${p.url}/diffs` : p.url;
}

function tabLabel(tab) {
  if (tab.kind === 'page') return pageLabel(tab.page);
  return tab.title;
}

function renderPanes() {
  renderPane(0);
  renderPane(1);
}

function renderPane(pane) {
  const p = state.panes[pane];
  const bar = $(`tabbar-${pane}`);
  bar.innerHTML = '';
  p.tabs.forEach((tab, i) => {
    const btn = document.createElement('button');
    btn.textContent = tabLabel(tab);
    btn.title = tab.kind === 'page' ? tab.page.title : tab.title;
    btn.classList.toggle('active', p.active === i);
    btn.addEventListener('click', () => {
      p.active = i;
      renderPane(pane);
      renderTargetInfo(); // 操作対象は表示中タブに追従する
      if (pane === 1) checkActiveMRHealth(); // 切り替えた MR を事前チェックする
    });
    if (tab.kind !== 'page') {
      const x = document.createElement('span');
      x.className = 'tab-close';
      x.textContent = '×';
      x.title = 'タブを閉じる';
      x.addEventListener('click', (e) => {
        e.stopPropagation();
        closeTab(pane, i);
      });
      btn.appendChild(x);
    }
    bar.appendChild(btn);
  });
  syncPane(pane);
}

function closeTab(pane, index) {
  const p = state.panes[pane];
  p.tabs.splice(index, 1);
  if (p.active >= p.tabs.length) p.active = p.tabs.length - 1;
  renderPane(pane);
  renderTargetInfo();
}

// pages からペインのタブを組み直す（リーダー / 要約のローカルタブは残す）
function rebuildPanes() {
  for (const [pane, kind] of [[0, 'issue'], [1, 'mr']]) {
    const pageTabs = state.pages
      .filter((p) => p.type === kind)
      .map((p) => ({ kind: 'page', page: p }));
    const locals = state.panes[pane].tabs.filter((t) => t.kind !== 'page');
    const tabs = [...pageTabs, ...locals];
    state.panes[pane] = { tabs, active: tabs.length ? 0 : -1 };
  }
  renderPanes();
}

function syncPane(pane) {
  const p = state.panes[pane];
  const tab = p.tabs[p.active];
  const wv = $(`wv-${pane}`);
  const lv = $(`lv-${pane}`);
  const urlEl = $(`url-${pane}`);
  if (!tab) {
    wv.style.display = 'flex';
    lv.hidden = true;
    if (wv.getAttribute('src') !== 'about:blank') wv.setAttribute('src', 'about:blank');
    urlEl.value = '';
    return;
  }
  if (tab.kind === 'page') {
    wv.style.display = 'flex';
    lv.hidden = true;
    const startUrl = pageStartUrl(tab.page);
    if (wv.getAttribute('src') !== startUrl) wv.setAttribute('src', startUrl);
    urlEl.value = startUrl;
    return;
  }
  // リーダーモード / 要約のローカルタブ
  wv.style.display = 'none';
  lv.hidden = false;
  lv.innerHTML =
    tab.kind === 'summary'
      ? mdToHtml(tab.content || '')
      : `<div class="reader-text">${escapeHtml(tab.content || '')}</div>`;
  urlEl.value = tab.sourceUrl || '';
}

function bindPaneEvents() {
  for (const pane of [0, 1]) {
    // ローカルタブ内のリンクは OS 既定ブラウザで開く
    $(`lv-${pane}`).addEventListener('click', (e) => {
      const a = e.target.closest('a[href]');
      if (a) {
        e.preventDefault();
        api.openExternal(a.getAttribute('href')).catch((err) => toast(err.message, true));
      }
    });
    const wv = $(`wv-${pane}`);
    for (const ev of ['did-navigate', 'did-navigate-in-page']) {
      wv.addEventListener(ev, (e) => {
        const p = state.panes[pane];
        const tab = p.tabs[p.active];
        const isPage = !tab || tab.kind === 'page';
        if (isPage && e.url && e.url !== 'about:blank') $(`url-${pane}`).value = e.url;
      });
    }
    $(`url-${pane}`).addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        const url = e.target.value.trim();
        if (/^https?:\/\//.test(url)) wv.setAttribute('src', url);
      }
    });
  }
  document.querySelectorAll('.urlbar > button, .menu-wrap > button').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      const pane = btn.dataset.pane;
      const url = $(`url-${pane}`).value;
      if (btn.dataset.act === 'menu') {
        e.stopPropagation();
        const menu = $(`menu-${pane}`);
        const wasHidden = menu.hidden;
        closeAllMenus();
        menu.hidden = !wasHidden;
      } else if (btn.dataset.act === 'reload') {
        $(`wv-${pane}`).reload();
      } else if (btn.dataset.act === 'copy') {
        navigator.clipboard.writeText(url);
        toast('URL をコピーしました');
      } else if (btn.dataset.act === 'external' && url) {
        api.openExternal(url).catch((err) => toast(err.message, true));
      }
    });
  });
  document.querySelectorAll('.pane-menu button').forEach((btn) => {
    btn.addEventListener('click', () => {
      closeAllMenus();
      const pane = Number(btn.dataset.pane);
      if (btn.dataset.menu === 'reader') doReaderMode(pane);
      else if (btn.dataset.menu === 'summary') doSummarizeTab(pane);
      else if (btn.dataset.menu === 'obsidian') doExportPaneTab(pane);
    });
  });
  document.addEventListener('click', closeAllMenus);
}

function closeAllMenus() {
  document.querySelectorAll('.pane-menu').forEach((m) => (m.hidden = true));
}

// Electron の webview はキーボードフォーカスを保持したままになることがあり、
// ホスト側の入力欄にカーソルは出るのに文字が入力できなくなる（既知問題）。
// ホスト側の入力欄へフォーカスが移ったら webview のフォーカスを明示的に解放する。
function bindWebviewFocusFix() {
  document.addEventListener('focusin', (e) => {
    const el = e.target;
    if (!(el instanceof HTMLElement)) return;
    if (!el.matches('input, textarea, select')) return;
    for (const pane of [0, 1]) {
      const wv = $(`wv-${pane}`);
      const frame = wv.shadowRoot && wv.shadowRoot.querySelector('iframe');
      if (frame) frame.blur();
      wv.blur();
    }
    // webview の blur でフォーカスが外れてしまった場合は入力欄へ戻す
    if (document.activeElement !== el) el.focus();
  });
}

// ---------------------------------------------------------------------------
// リーダーモード / 要約（ペインメニュー → ローカルタブ表示）
// ---------------------------------------------------------------------------

// webview 内で本文テキストのみを抽出するスクリプト。
// ナビゲーションやスクリプト等を除去した innerText を返す。
const READER_EXTRACT = `(() => {
  const root =
    document.querySelector('main') ||
    document.querySelector('article') ||
    document.querySelector('[role="main"]') ||
    document.body;
  const clone = root.cloneNode(true);
  clone
    .querySelectorAll('script,style,noscript,svg,canvas,nav,header,footer,aside,form,button')
    .forEach((n) => n.remove());
  const text = (clone.innerText || '').replace(/\\n{3,}/g, '\\n\\n').trim();
  return { title: document.title || location.href, text };
})();`;

async function doReaderMode(pane) {
  const p = state.panes[pane];
  const active = p.tabs[p.active];
  if (!active || active.kind !== 'page') {
    return toast('リーダーモードは GitLab ページのタブで実行してください', true);
  }
  const wv = $(`wv-${pane}`);
  await guard('リーダーモード', async () => {
    const res = await wv.executeJavaScript(READER_EXTRACT);
    if (!res || !res.text) throw new Error('本文を抽出できませんでした');
    p.tabs.push({
      kind: 'reader',
      title: `リーダー: ${pageLabel(active.page)}`,
      content: res.text,
      sourceUrl: $(`url-${pane}`).value || active.page.url,
    });
    p.active = p.tabs.length - 1;
    renderPane(pane);
  });
}

// ペインの表示中ページ（ローカルタブが手前でも、その元ページ）を返す
function paneCurrentPageTab(pane) {
  const p = state.panes[pane];
  const active = p.tabs[p.active];
  if (active && active.kind === 'page') return active;
  return (
    p.tabs.find((t) => t.kind === 'page' && active && t.page.url === active.sourceUrl) ||
    p.tabs.find((t) => t.kind === 'page') ||
    null
  );
}

const SUMMARY_PLACEHOLDER = '要約を生成しています…';

async function doSummarizeTab(pane) {
  const p = state.panes[pane];
  const pageTab = paneCurrentPageTab(pane);
  if (!pageTab) return toast('要約対象の GitLab ページがありません', true);
  const page = pageTab.page;
  const tab = {
    kind: 'summary',
    title: `要約: ${pageLabel(page)}`,
    content: SUMMARY_PLACEHOLDER,
    sourceUrl: page.url,
  };
  p.tabs.push(tab);
  p.active = p.tabs.length - 1;
  renderPane(pane);
  await guard('要約', async () => {
    const { summary } = await api.agentSummarize(targetOf(page));
    tab.content = summary;
    state.lastSummary = summary;
    renderPanes();
  });
  if (tab.content === SUMMARY_PLACEHOLDER) {
    tab.content = '要約に失敗しました。設定のエージェントコマンドを確認してください。';
    renderPanes();
  }
}

// Obsidian へ送る（ペインメニュー）: アクティブタブの内容をそのまま書き出す。
// GitLab ページのタブならリーダーモードと同等の本文抽出テキストを送る。
async function doExportPaneTab(pane) {
  const p = state.panes[pane];
  const tab = p.tabs[p.active];
  if (!tab) return toast('Obsidian へ送るタブがありません', true);
  const pageTab = paneCurrentPageTab(pane); // 出典メタデータ用（ローカルタブは元ページ）
  const page = pageTab ? pageTab.page : null;
  await guard('Obsidian へ送る', async () => {
    let kind;
    let content;
    if (tab.kind === 'page') {
      const res = await $(`wv-${pane}`).executeJavaScript(READER_EXTRACT);
      if (!res || !res.text) throw new Error('本文を抽出できませんでした');
      kind = 'reader';
      content = res.text;
    } else {
      kind = tab.kind; // 'reader' | 'summary'
      content = tab.content || '';
    }
    const { file } = await api.obsidianExportContent({
      page,
      kind,
      title: tab.kind === 'page' ? (page ? page.title : '') : tab.title,
      sourceUrl: tab.kind === 'page' ? (page ? page.url : '') : tab.sourceUrl || '',
      content,
    });
    toast(`Obsidian に書き出しました: ${file}`);
  });
}

// ---------------------------------------------------------------------------
// スプリッター（左右ペインのドラッグリサイズ）
// ---------------------------------------------------------------------------

function bindSplitter() {
  const splitter = $('splitter');
  const leftPane = document.querySelector('#panes .pane[data-pane="0"]');
  let dragging = false;
  splitter.addEventListener('mousedown', (e) => {
    dragging = true;
    document.body.classList.add('splitting'); // webview のマウスイベントを止める
    e.preventDefault();
  });
  window.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const rect = $('panes').getBoundingClientRect();
    const pct = Math.min(85, Math.max(15, ((e.clientX - rect.left) / rect.width) * 100));
    leftPane.style.flex = `0 0 ${pct}%`;
  });
  window.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    document.body.classList.remove('splitting');
  });
}

// ---------------------------------------------------------------------------
// 操作対象・ラベル表示とアクションボタンの活性制御
// ---------------------------------------------------------------------------

function renderTargetInfo() {
  const t = primaryTarget();

  $('target-label').textContent = t ? `${pageLabel(t)} — ${t.title.slice(0, 40)}` : '（対象なし）';
  $('target-state').textContent = t ? t.state : '';
  const wrap = $('target-labels');
  wrap.innerHTML = '';
  if (t) {
    for (const l of t.labels) {
      const chip = document.createElement('span');
      chip.className = 'chip';
      chip.textContent = l;
      wrap.appendChild(chip);
    }
  }

  const status = t ? statusOf(t) : '';

  const sendback = $('btn-sendback');
  const sendbackTo = SENDBACK_FLOW[status];
  sendback.disabled = !t || !sendbackTo;
  sendback.title = sendbackTo
    ? `${sendbackTo} へ戻す`
    : 'status:elaborated → status:draft / status:approved → status:needs-rework のときだけ使えます';

  $('btn-reject').disabled = !t;
  $('btn-change').disabled = !t;

  const approve = $('btn-approve');
  if (!t) {
    approve.disabled = true;
    approve.title = '';
  } else if (status === 'elaborated') {
    approve.disabled = false;
    approve.title = 'status:open に進める';
  } else if (status === 'approved') {
    // マージ対象は右ペインのアクティブタブの MR。マージ可否（コンフリクト等）の
    // 確認は実行時に行い、ここでは対象 MR の有無だけで活性を決める。
    const mr = t.type === 'mr' ? t : activeMR();
    approve.disabled = !mr;
    approve.title = mr
      ? `${pageLabel(mr)} をマージ${t.type === 'issue' ? 'してイシューをクローズ' : ''}する`
      : '右ペインに操作対象の MR がありません';
  } else {
    approve.disabled = true;
    approve.title = 'status:elaborated / status:approved のときだけ使えます';
  }
}

function applyUpdatedItem(updated) {
  // pages / candidates 双方の同一アイテムを更新後の状態に置き換える。
  // pages はペインのタブが同じオブジェクトを参照しているため、その場で書き換える。
  const match = (p) =>
    p.projectId === updated.projectId && p.type === updated.type && p.iid === updated.iid;
  for (const p of state.pages) {
    if (match(p)) Object.assign(p, updated);
  }
  state.candidates = state.candidates.map((c) => (match(c) ? { ...c, ...updated } : c));
  renderCandidates();
  renderTargetInfo();
}

// ---------------------------------------------------------------------------
// ラベルプリセット（「変更」ダイアログとキーボードショートカットで使用）
// ---------------------------------------------------------------------------

// プリセットのラベル変更内容（付ける / 外す）を計算する
function presetChange(preset, t) {
  let add = [preset.label];
  let remove = [];
  if (preset.toggle && t.labels.includes(preset.label)) {
    add = [];
    remove = [preset.label];
  } else if (preset.exclusivePrefix) {
    remove = t.labels.filter(
      (l) => l.startsWith(preset.exclusivePrefix) && l !== preset.label
    );
  }
  return { add, remove };
}

// キーボードショートカットからの即時適用（コメントなし・従来動作）
async function applyPreset(index) {
  const preset = (state.config.labelPresets || [])[index];
  const t = primaryTarget();
  if (!preset || !t) return;
  await guard('ラベル更新', async () => {
    const { add, remove } = presetChange(preset, t);
    const updated = await api.glUpdateLabels(targetOf(t), add, remove);
    applyUpdatedItem(updated);
    reloadPanes();
    toast(
      add.length
        ? `ラベルを ${preset.label} に更新しました`
        : `ラベル ${preset.label} を外しました`
    );
  });
}

// ---------------------------------------------------------------------------
// コメント / 承認 / 差し戻し / 却下 / 変更
// ---------------------------------------------------------------------------

async function postComment() {
  const t = primaryTarget();
  const body = $('comment-input').value.trim();
  if (!t) return toast('操作対象がありません', true);
  if (!body) return toast('コメントが空です', true);
  await guard('コメント投稿', async () => {
    await api.glComment(targetOf(t), body);
    $('comment-input').value = '';
    reloadPanes();
    toast(`${pageLabel(t)} にコメントを投稿しました`);
  });
}

// 左右ペインに表示中の GitLab ページを再読み込みする。
// ステータス変更・コメント投稿などの結果を埋め込み表示へ反映させる。
function reloadPanes() {
  for (const pane of [0, 1]) {
    const p = state.panes[pane];
    const tab = p.tabs[p.active];
    if (tab && tab.kind === 'page') $(`wv-${pane}`).reload();
  }
}

// 対象の status:* ラベルを newLabel に付け替え、アクションコメントを投稿する
async function commentAndSetStatus(t, actionName, newLabel) {
  await api.glComment(targetOf(t), actionComment(actionName));
  const remove = t.labels.filter((l) => l.startsWith('status:') && l !== newLabel);
  const updated = await api.glUpdateLabels(targetOf(t), [newLabel], remove);
  applyUpdatedItem(updated);
  $('comment-input').value = '';
  reloadPanes();
}

// 承認: status:elaborated → status:open。
// status:approved は対象 MR をマージしてイシューをクローズ。
async function doApprove() {
  const t = primaryTarget();
  if (!t) return;
  const status = statusOf(t);
  if (status === 'elaborated') {
    await guard('承認', async () => {
      await commentAndSetStatus(t, '承認', 'status:open');
      toast(`${pageLabel(t)} を status:open に進めました`);
    });
    return;
  }
  if (status !== 'approved') return;
  const mr = t.type === 'mr' ? t : activeMR();
  if (!mr) return toast('右ペインに操作対象の MR がありません', true);
  const closesIssue = t.type === 'issue';
  await guard('承認', async () => {
    // マージ可否は実行時に確認し、できない理由をトーストで明示する
    const cur = await api.glMRStatus(targetOf(mr));
    // 既にマージ済み（手元の表示が古い・外部でマージされた等）の場合は、
    // マージをスキップしてイシューのクローズだけを確認のうえ実行する
    if (cur.state === 'merged') {
      applyUpdatedItem(cur);
      if (!closesIssue) {
        throw new Error(`${pageLabel(mr)} は既にマージ済みです`);
      }
      const msg = `${pageLabel(mr)} は既にマージ済みです。${pageLabel(t)} のクローズだけを行います。よろしいですか？`;
      if (!(await confirmDialog(msg))) return;
      await api.glComment(targetOf(t), actionComment('承認'));
      const closed = await api.glSetState(targetOf(t), 'close');
      applyUpdatedItem(closed);
      $('comment-input').value = '';
      reloadPanes();
      toast(`${pageLabel(t)} をクローズしました（${pageLabel(mr)} はマージ済み）`);
      return;
    }
    if (cur.state !== 'opened') {
      throw new Error(`${pageLabel(mr)} がオープンではありません（${cur.state}）`);
    }
    if (cur.hasConflicts) {
      throw new Error(`${pageLabel(mr)} にコンフリクトがあるためマージできません`);
    }
    if (!cur.blockingDiscussionsResolved) {
      throw new Error(`${pageLabel(mr)} に未解決のレビューコメントがあるためマージできません`);
    }
    const msg = `${pageLabel(mr)} をマージ${closesIssue ? `し、${pageLabel(t)} をクローズ` : ''}します。よろしいですか？`;
    if (!(await confirmDialog(msg))) return;
    await api.glComment(targetOf(t), actionComment('承認'));
    const merged = await api.glMerge(targetOf(mr));
    applyUpdatedItem(merged);
    if (closesIssue) {
      const closed = await api.glSetState(targetOf(t), 'close');
      applyUpdatedItem(closed);
    }
    $('comment-input').value = '';
    reloadPanes();
    toast(`${pageLabel(mr)} をマージ${closesIssue ? `し、${pageLabel(t)} をクローズ` : ''}しました`);
  });
}

// 差し戻しのステータス遷移（現在のステータス → 戻し先）
const SENDBACK_FLOW = {
  elaborated: 'status:draft',
  approved: 'status:needs-rework',
};

// 差し戻し: ステータスを前に戻す（SENDBACK_FLOW にあるものだけ）
async function doSendBack() {
  const t = primaryTarget();
  const to = t && SENDBACK_FLOW[statusOf(t)];
  if (!to) return;
  await guard('差し戻し', async () => {
    await commentAndSetStatus(t, '差し戻し', to);
    toast(`${pageLabel(t)} を ${to} に戻しました`);
  });
}

// ---------------------------------------------------------------------------
// MR の事前チェック（コンフリクト / 未解決レビューコメントの検知 → 差し戻し確認）
// ---------------------------------------------------------------------------
// MR を表示したとき（候補選択・MR タブ切替）に現在状態を取得し、コンフリクトまたは
// 未解決（未クローズ）のレビューコメントがあれば「差し戻すか」の確認ダイアログを出す。
// 差し戻しは、検知内容を伝える固定コメントを MR へ投稿し、操作対象のステータスを
// アクションバーの差し戻しと同じ遷移（SENDBACK_FLOW）で戻す（対象のステータスが
// 遷移表に無ければコメント投稿のみ）。
// 同じ MR への確認はセッション中 1 回だけ（タブ切替のたびに出すと邪魔になる）。

const MR_SENDBACK_COMMENTS = {
  conflict:
    'ベースブランチとコンフリクトしています。ベースブランチを取り込んで解消し、MR を更新してください。',
  unresolved:
    '未解決（未クローズ）のレビューコメントが残っています。各スレッドに対応して解決（Resolve）してください。',
};

const mrHealthChecked = new Set();
let mrSendback = null; // { mr, kinds: ('conflict'|'unresolved')[] }

async function checkActiveMRHealth() {
  const mr = activeMR();
  if (!mr || mr.type !== 'mr' || (mr.state && mr.state !== 'opened')) return;
  const key = `${mr.projectId}:${mr.iid}`;
  if (mrHealthChecked.has(key)) return;
  mrHealthChecked.add(key);
  let cur;
  try {
    cur = await api.glMRReview(targetOf(mr));
  } catch {
    mrHealthChecked.delete(key); // 取得失敗は未チェック扱い（次の表示で再試行）
    return;
  }
  if (cur.state !== 'opened') return;

  const kinds = [];
  const problems = [];
  if (cur.hasConflicts) {
    kinds.push('conflict');
    problems.push('ベースブランチとコンフリクトしています（このままではマージできません）');
  }
  if (cur.unresolvedCount > 0 || !cur.blockingDiscussionsResolved) {
    kinds.push('unresolved');
    problems.push(
      `未解決（未クローズ）のレビューコメントが${
        cur.unresolvedCount > 0 ? ` ${cur.unresolvedCount} 件` : ''
      }あります`
    );
  }
  if (!kinds.length) return;

  const dlg = $('mr-sendback-dialog');
  if (dlg.open) return; // 別 MR の確認を表示中なら重ねない
  mrSendback = { mr, kinds };
  $('mr-sendback-desc').textContent = `${pageLabel(mr)} — ${mr.title.slice(0, 60)}`;
  $('mr-sendback-problems').innerHTML = problems
    .map((p) => `<li>${escapeHtml(p)}</li>`)
    .join('');
  const t = primaryTarget();
  const to = t && SENDBACK_FLOW[statusOf(t)];
  $('mr-sendback-status').textContent = to
    ? `「差し戻す」を押すと、上記の内容を伝える固定コメントを MR に投稿し、${pageLabel(t)} を ${to} に戻します。`
    : '「差し戻す」を押すと、上記の内容を伝える固定コメントを MR に投稿します（対象のステータスが差し戻し対象外のため、ラベルは変更しません）。';
  dlg.showModal();
}

// 差し戻し（固定コメント投稿 + ステータス差し戻し）: 検知内容ごとの定型文を
// 「# 差し戻し」見出しで MR に投稿し、操作対象のステータスを SENDBACK_FLOW で戻す
async function doMRSendback() {
  $('mr-sendback-dialog').close();
  const sb = mrSendback;
  mrSendback = null;
  if (!sb) return;
  await guard('差し戻し', async () => {
    const lines = sb.kinds.map((k) => `- ${MR_SENDBACK_COMMENTS[k]}`);
    await api.glComment(targetOf(sb.mr), `# 差し戻し\n\n${lines.join('\n')}`);
    const t = primaryTarget();
    const to = t && SENDBACK_FLOW[statusOf(t)];
    if (to) {
      const remove = t.labels.filter((l) => l.startsWith('status:') && l !== to);
      const updated = await api.glUpdateLabels(targetOf(t), [to], remove);
      applyUpdatedItem(updated);
    }
    reloadPanes();
    toast(
      to
        ? `${pageLabel(sb.mr)} に差し戻しコメントを投稿し、${pageLabel(t)} を ${to} に戻しました`
        : `${pageLabel(sb.mr)} に差し戻しコメントを投稿しました`
    );
  });
}

// 却下: イシュー名と似たタイトルの MR をクローズしてソースブランチを削除し、
// イシューは「閉じる」。
// - MR の絞り込み: related_merge_requests には本文で言及しただけの無関係な MR も
//   混ざるため、破壊的操作（クローズ＋ブランチ削除）は**イシューとタイトルが似ている
//   MR のみ**を対象にする（タブ選択と同じ titleSimilarity。対象はダイアログに事前表示）
// - イシューは削除しない — コメント・経緯が記録として残り、委譲元ツール
//   （kiro-flow はイシューのクローズで却下を検知し、人コメントをやり直し指示として
//   取り込む。削除すると 404 の一般エラーになりフィードバックごと壊れる）にも
//   決着が正しく伝わる。
const REJECT_TITLE_SIMILARITY = 0.5;

let rejectTargets = { mrs: [], skipped: 0 };

async function openRejectDialog() {
  const t = primaryTarget();
  if (!t) return;
  await guard('却下対象の取得', async () => {
    let mrs = [];
    let skipped = 0;
    if (t.type === 'mr') {
      mrs = [t];
    } else {
      const related = ((await api.glRelated(targetOf(t)).catch(() => [])) || []).filter(
        (r) => r.type === 'mr' && r.state === 'opened'
      );
      mrs = related.filter((m) => titleSimilarity(m.title, t.title) >= REJECT_TITLE_SIMILARITY);
      skipped = related.length - mrs.length;
    }
    rejectTargets = { mrs, skipped };
    $('reject-desc').textContent = `${pageLabel(t)} — ${t.title.slice(0, 60)} を却下します。`;
    $('reject-mrs').innerHTML = [
      mrs.length
        ? `クローズしてブランチを削除する MR: ${mrs
            .map((m) => `<span class="mono">!${m.iid}</span> ${escapeHtml(String(m.title || '').slice(0, 40))}`)
            .join(' ／ ')}`
        : '対象の MR はありません（イシューのみ閉じます）',
      skipped ? `（タイトルが似ていない関連 MR ${skipped} 件は対象外）` : '',
    ]
      .filter(Boolean)
      .join('<br>');
    $('reject-dialog').showModal();
  });
}

async function doReject() {
  $('reject-dialog').close();
  const t = primaryTarget();
  if (!t) return;
  const mrs = rejectTargets.mrs;
  await guard('却下', async () => {
    await api.glComment(targetOf(t), actionComment('却下'));

    for (const mr of mrs) {
      if (mr.state === 'opened') {
        const closed = await api.glSetState(targetOf(mr), 'close');
        applyUpdatedItem(closed);
      }
      if (mr.sourceBranch) {
        try {
          await api.glDeleteBranch(mr.projectId, mr.sourceBranch);
        } catch (err) {
          toast(`ソースブランチ ${mr.sourceBranch} の削除に失敗: ${err.message}`, true);
        }
      }
    }

    if (t.type === 'issue') {
      // 表示キャッシュの state に頼らず常に明示的にクローズする（既にクローズ済みなら
      // no-op）。委譲元の自動クローズ（kiro-flow）は daemon 停止中などで走らないことがある
      const closed = await api.glSetState(targetOf(t), 'close');
      applyUpdatedItem(closed);
      toast(`${pageLabel(t)} を却下しました（MR ${mrs.length} 件をクローズ・ブランチ削除、イシューは閉じる）`);
    } else {
      toast(`${pageLabel(t)} をクローズしてソースブランチを削除しました`);
    }
    $('comment-input').value = '';
    reloadPanes();
  });
}

// 変更: プリセットをプレフィックス（status: など）ごとのブロックに分類して表示。
// 現在対象に付いているラベルを選択済み状態で開き、選択の差分を「実行」で適用する。
let changeSelection = new Set(); // 選択中のプリセット index
let changeInitial = new Set(); // ダイアログを開いた時点の選択（= 現在のラベル）

// ブロック分類キー: exclusivePrefix があればそれ、なければラベルの「xxx:」部分
function presetGroupKey(preset) {
  if (preset.exclusivePrefix) return preset.exclusivePrefix;
  const m = String(preset.label).match(/^([^:]+:)/);
  return m ? m[1] : '';
}

function openChangeDialog() {
  const t = primaryTarget();
  if (!t) return;
  const presets = state.config.labelPresets || [];

  const groups = new Map();
  presets.forEach((preset, i) => {
    const key = presetGroupKey(preset);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(i);
  });

  changeSelection = new Set(
    presets.map((_, i) => i).filter((i) => t.labels.includes(presets[i].label))
  );
  changeInitial = new Set(changeSelection);

  const wrap = $('change-presets');
  wrap.innerHTML = '';
  for (const [key, indices] of groups) {
    const block = document.createElement('div');
    block.className = 'preset-group';
    const title = document.createElement('div');
    title.className = 'preset-group-title';
    title.textContent = key ? key.replace(/:$/, '') : 'その他';
    block.appendChild(title);
    const grid = document.createElement('div');
    grid.className = 'preset-grid';
    for (const i of indices) {
      const preset = presets[i];
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.dataset.index = String(i);
      btn.innerHTML =
        escapeHtml(preset.label) +
        (preset.shortcut ? `<span class="kbd">${escapeHtml(preset.shortcut)}</span>` : '');
      if (t.labels.includes(preset.label)) btn.classList.add('current');
      btn.classList.toggle('on', changeSelection.has(i));
      btn.addEventListener('click', () => {
        if (changeSelection.has(i)) {
          changeSelection.delete(i); // 再クリックで解除（ラベルを外す）
        } else {
          for (const j of indices) changeSelection.delete(j); // 同一ブロック内は排他
          changeSelection.add(i);
        }
        for (const b of grid.querySelectorAll('button')) {
          b.classList.toggle('on', changeSelection.has(Number(b.dataset.index)));
        }
      });
      grid.appendChild(btn);
    }
    block.appendChild(grid);
    wrap.appendChild(block);
  }
  $('change-dialog').showModal();
}

async function executeChange() {
  const t = primaryTarget();
  if (!t) return;
  const presets = state.config.labelPresets || [];

  // 開いた時点との選択差分から付ける / 外すラベルを求める
  const add = [];
  const remove = [];
  presets.forEach((preset, i) => {
    const selected = changeSelection.has(i);
    if (selected === changeInitial.has(i)) return;
    if (selected) {
      add.push(preset.label);
      if (preset.exclusivePrefix) {
        for (const l of t.labels) {
          if (l.startsWith(preset.exclusivePrefix) && l !== preset.label) remove.push(l);
        }
      }
    } else {
      remove.push(preset.label);
    }
  });
  const addSet = new Set(add);
  const removeList = [...new Set(remove)].filter((l) => !addSet.has(l));
  if (!add.length && !removeList.length) {
    return toast('変更するラベルを選択してください', true);
  }

  $('change-dialog').close();
  await guard('変更', async () => {
    await api.glComment(targetOf(t), actionComment('変更'));
    const updated = await api.glUpdateLabels(targetOf(t), add, removeList);
    applyUpdatedItem(updated);
    $('comment-input').value = '';
    reloadPanes();
    const parts = [];
    if (add.length) parts.push(`${add.join(', ')} を付けました`);
    if (removeList.length) parts.push(`${removeList.join(', ')} を外しました`);
    toast(`ラベルを更新しました: ${parts.join(' / ')}`);
  });
}

// ---------------------------------------------------------------------------
// 要約（アクションバー → ダイアログ）と Obsidian エクスポート
// ---------------------------------------------------------------------------

async function doSummarize() {
  const t = primaryTarget();
  if (!t) return toast('操作対象がありません', true);
  const dlg = $('summary-dialog');
  $('summary-status').textContent =
    `${pageLabel(t)} の内容をエージェントに送信しています…（コマンド: ` +
    `${state.config.agent.command.split(' ')[0]}）`;
  $('summary-text').value = '';
  dlg.showModal();
  await guard('要約', async () => {
    const { summary } = await api.agentSummarize(targetOf(t));
    state.lastSummary = summary;
    $('summary-text').value = summary;
    $('summary-status').textContent = `${pageLabel(t)} の要約が完了しました`;
  });
  if (!$('summary-text').value) {
    $('summary-status').textContent = '要約に失敗しました。設定のエージェントコマンドを確認してください。';
  }
}

// 要約ダイアログの「Obsidian へ送る」用（本文・コメント・変更ファイル付きの詳細形式）
async function doExport() {
  const t = primaryTarget();
  if (!t) return toast('操作対象がありません', true);
  const summary = $('summary-dialog').open ? $('summary-text').value : state.lastSummary;
  await guard('Obsidian エクスポート', async () => {
    const { file } = await api.obsidianExport(targetOf(t), summary);
    toast(`Obsidian に書き出しました: ${file}`);
  });
}

// ---------------------------------------------------------------------------
// ショートカット
// ---------------------------------------------------------------------------

function parseShortcut(str) {
  if (!str) return null;
  const parts = String(str).split('+').map((s) => s.trim());
  const key = parts[parts.length - 1].toLowerCase();
  return {
    ctrl: parts.some((p) => /^ctrl$/i.test(p)),
    shift: parts.some((p) => /^shift$/i.test(p)),
    alt: parts.some((p) => /^alt$/i.test(p)),
    key,
  };
}

function eventKey(e) {
  // Shift+数字 は e.key が記号（! " # …）になるため物理キーで判定する
  if (/^Digit\d$/.test(e.code)) return e.code.slice(5);
  if (/^Numpad\d$/.test(e.code)) return e.code.slice(6);
  return e.key.toLowerCase();
}

function matchShortcut(e, sc) {
  if (!sc) return false;
  return (
    e.ctrlKey === sc.ctrl &&
    e.shiftKey === sc.shift &&
    e.altKey === sc.alt &&
    eventKey(e) === sc.key
  );
}

function handleKeydown(e) {
  const sc = state.config.actionShortcuts || {};
  const actions = {
    postComment,
    summarize: doSummarize,
  };
  for (const [name, fn] of Object.entries(actions)) {
    if (matchShortcut(e, parseShortcut(sc[name]))) {
      e.preventDefault();
      fn();
      return;
    }
  }
  const presets = state.config.labelPresets || [];
  for (let i = 0; i < presets.length; i++) {
    if (matchShortcut(e, parseShortcut(presets[i].shortcut))) {
      e.preventDefault();
      applyPreset(i);
      return;
    }
  }
}

// ---------------------------------------------------------------------------
// 設定ダイアログ
// ---------------------------------------------------------------------------

function openSettings() {
  const c = state.config;
  $('cfg-gitlab-url').value = c.gitlab.baseUrl;
  $('cfg-gitlab-token').value = c.gitlab.token;
  $('cfg-agent-command').value = c.agent.command;
  $('cfg-agent-timeout').value = c.agent.timeoutSec;
  $('cfg-agent-prompt').value = c.agent.promptTemplate;
  $('cfg-obsidian-vault').value = c.obsidian.vaultDir;
  $('cfg-obsidian-subdir').value = c.obsidian.subDir;
  $('cfg-obsidian-open').checked = !!c.obsidian.openAfterExport;
  $('cfg-label-presets').value = JSON.stringify(c.labelPresets, null, 2);
  $('cfg-action-shortcuts').value = JSON.stringify(c.actionShortcuts, null, 2);
  $('settings-dialog').showModal();
}

async function saveSettings() {
  let labelPresets;
  let actionShortcuts;
  try {
    labelPresets = JSON.parse($('cfg-label-presets').value);
    actionShortcuts = JSON.parse($('cfg-action-shortcuts').value);
  } catch (err) {
    return toast(`JSON の形式が不正です: ${err.message}`, true);
  }
  const cfg = {
    ...state.config,
    gitlab: {
      baseUrl: $('cfg-gitlab-url').value.trim(),
      token: $('cfg-gitlab-token').value.trim(),
    },
    agent: {
      ...state.config.agent,
      command: $('cfg-agent-command').value.trim(),
      timeoutSec: Number($('cfg-agent-timeout').value) || 0,
      promptTemplate: $('cfg-agent-prompt').value,
    },
    obsidian: {
      vaultDir: $('cfg-obsidian-vault').value.trim(),
      subDir: $('cfg-obsidian-subdir').value.trim(),
      openAfterExport: $('cfg-obsidian-open').checked,
    },
    labelPresets,
    actionShortcuts,
  };
  await guard('設定保存', async () => {
    state.config = await api.saveConfig(cfg);
    $('settings-dialog').close();
    toast('設定を保存しました');
  });
}

// ---------------------------------------------------------------------------
// 初期化
// ---------------------------------------------------------------------------

// 設定を読めなかったときの最小構成。設定ダイアログを開いて保存し直せるよう、
// UI が参照するセクションだけ揃える（正式な既定値は main 側の DEFAULT_CONFIG）。
function fallbackConfig() {
  return {
    gitlab: { baseUrl: 'https://gitlab.com', token: '' },
    searchCache: {},
    agent: { command: '', timeoutSec: 300, promptTemplate: '' },
    obsidian: { vaultDir: '', subDir: '', openAfterExport: false },
    labelPresets: [],
    actionShortcuts: {},
  };
}

// 受け取った設定の形を検証し、壊れたセクションだけ最小構成で置き換える。
// 旧バージョンが書いた config.json や手編集で形が崩れていても起動できるようにする
function ensureConfigShape(cfg) {
  const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
  const base = fallbackConfig();
  if (!isObj(cfg)) return base;
  for (const [k, def] of Object.entries(base)) {
    if (Array.isArray(def)) {
      if (!Array.isArray(cfg[k])) cfg[k] = def;
    } else if (!isObj(cfg[k])) {
      cfg[k] = def;
    }
  }
  return cfg;
}

async function init() {
  let configError = null;
  try {
    state.config = ensureConfigShape(await api.getConfig());
  } catch (err) {
    configError = err;
    state.config = fallbackConfig();
  }
  try {
    restoreSearchCache();
  } catch {
    /* 前回の検索条件の復元に失敗しても起動は続ける */
  }

  $('btn-load-groups').addEventListener('click', loadGroups);
  $('btn-load-projects').addEventListener('click', loadProjects);
  $('group-select').addEventListener('change', () => {
    loadProjects();
    loadLabelSuggestions();
  });
  $('project-select').addEventListener('change', loadLabelSuggestions);
  $('btn-search').addEventListener('click', searchCandidates);
  $('btn-author-self').addEventListener('click', () =>
    guard('自分のユーザー名取得', async () => {
      const user = await api.glCurrentUser();
      $('author-input').value = user.username || '';
      persistSearchCache();
    })
  );
  $('keyword-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') searchCandidates();
  });

  // 検索条件はすべて変更のたびにキャッシュへ保存する
  for (const id of [
    'group-search',
    'group-select',
    'project-search',
    'project-select',
    'label-input',
    'author-input',
    'type-select',
    'state-select',
    'keyword-input',
  ]) {
    $(id).addEventListener('change', persistSearchCache);
    $(id).addEventListener('input', persistSearchCache);
  }

  bindPaneEvents();
  bindSplitter();
  bindWebviewFocusFix();

  $('btn-comment').addEventListener('click', postComment);
  $('comment-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.ctrlKey) {
      e.preventDefault();
      postComment();
    }
  });
  $('btn-approve').addEventListener('click', doApprove);
  $('btn-sendback').addEventListener('click', doSendBack);
  $('btn-reject').addEventListener('click', openRejectDialog);
  $('btn-reject-ok').addEventListener('click', () => doReject());
  $('btn-change').addEventListener('click', openChangeDialog);
  $('btn-reject-cancel').addEventListener('click', () => $('reject-dialog').close());
  $('btn-mr-sendback-ok').addEventListener('click', doMRSendback);
  $('btn-mr-sendback-cancel').addEventListener('click', () => {
    mrSendback = null;
    $('mr-sendback-dialog').close();
  });
  $('btn-change-run').addEventListener('click', executeChange);
  $('btn-change-cancel').addEventListener('click', () => $('change-dialog').close());

  $('btn-summary-close').addEventListener('click', () => $('summary-dialog').close());
  $('btn-summary-copy').addEventListener('click', () => {
    navigator.clipboard.writeText($('summary-text').value);
    toast('要約をコピーしました');
  });
  $('btn-summary-to-comment').addEventListener('click', () => {
    $('comment-input').value = $('summary-text').value;
    state.lastSummary = $('summary-text').value;
    $('summary-dialog').close();
  });
  $('btn-summary-export').addEventListener('click', () => {
    state.lastSummary = $('summary-text').value;
    doExport();
  });

  $('btn-settings').addEventListener('click', openSettings);
  $('btn-settings-save').addEventListener('click', saveSettings);
  $('btn-settings-cancel').addEventListener('click', () => $('settings-dialog').close());

  document.addEventListener('keydown', handleKeydown);

  // 外部ツール（kiro-projects-viewer 等）からのディープリンク
  if (api.onOpenTarget) {
    api.onOpenTarget(({ url }) => openFromUrl(url));
  }

  renderTargetInfo();
  loadLabelSuggestions();

  if (configError) {
    toast(
      `設定の読み込みに失敗したため既定値で起動しました。⚙ 設定から保存し直してください: ${configError.message}`,
      true
    );
  } else if (!state.config.gitlab.token) {
    toast('GitLab のアクセストークンが未設定です。⚙ 設定から登録してください。', true);
  }
}

init().catch((err) => toast(`初期化に失敗しました: ${err.message}`, true));
