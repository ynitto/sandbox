'use strict';

/* global api */

// ---------------------------------------------------------------------------
// 状態
// ---------------------------------------------------------------------------

const state = {
  config: null,
  mode: 'gitlab', // 'gitlab' | 'needs'
  candidates: [],
  selectedIndex: -1,
  needsItems: [],
  needsSelectedIndex: -1,
  // pages[0] = 選択した候補、pages[1..] = 関連イシュー / MR（needs 選択時は 1 件のみ）
  pages: [],
  paneActive: [0, 1], // 各ペインが表示している pages のインデックス
  targetIndex: 0, // コメント・ラベル等の操作対象（pages のインデックス)
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

function currentTarget() {
  return state.pages[state.targetIndex] || null;
}

function escapeHtml(s) {
  return String(s)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}

// needs（ローカル Markdown）表示用の最小レンダラ。
// 見出し・箇条書き・コードブロック・太字・インラインコード・リンク・frontmatter に対応。
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
  let idx = 0;
  if (lines[0] === '---') {
    const end = lines.indexOf('---', 1);
    if (end > 0) {
      out.push('<div class="md-fm">');
      for (let j = 1; j < end; j++) out.push(`<div>${inline(lines[j])}</div>`);
      out.push('</div>');
      idx = end + 1;
    }
  }
  for (; idx < lines.length; idx++) {
    const ln = lines[idx];
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
// フィルタ（グループ / プロジェクト / ラベル）
// ---------------------------------------------------------------------------

async function loadGroups() {
  await guard('グループ取得', async () => {
    const groups = await api.glGroups($('group-search').value.trim());
    const sel = $('group-select');
    sel.innerHTML = '<option value="">（指定なし）</option>';
    for (const g of groups) {
      const opt = document.createElement('option');
      opt.value = g.id;
      opt.textContent = g.full_path;
      sel.appendChild(opt);
    }
    toast(`グループ ${groups.length} 件を取得しました`);
  });
}

async function loadProjects() {
  await guard('プロジェクト取得', async () => {
    const groupId = $('group-select').value || undefined;
    const projects = await api.glProjects({
      groupId,
      search: $('project-search').value.trim() || undefined,
    });
    const sel = $('project-select');
    sel.innerHTML = '<option value="">（指定なし）</option>';
    for (const p of projects) {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = p.path_with_namespace;
      sel.appendChild(opt);
    }
    toast(`プロジェクト ${projects.length} 件を取得しました`);
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
    type: $('type-select').value,
    groupId: $('group-select').value || undefined,
    projectId: $('project-select').value || undefined,
    labels: $('label-input')
      .value.split(',')
      .map((s) => s.trim())
      .filter(Boolean),
    state: $('state-select').value,
    search: $('keyword-input').value.trim(),
  };
}

async function searchCandidates() {
  await guard('候補検索', async () => {
    const items = await api.glSearch(collectFilters());
    state.candidates = items;
    state.selectedIndex = -1;
    renderCandidates();
    $('candidate-count').textContent = `候補: ${items.length} 件`;
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

async function selectCandidate(index) {
  await guard('関連ページ取得', async () => {
    state.selectedIndex = index;
    renderCandidates();
    const cand = state.candidates[index];
    const related = await api.glRelated(targetOf(cand));
    state.pages = [cand, ...related];
    state.paneActive = [0, state.pages.length > 1 ? 1 : 0];
    state.targetIndex = 0;
    state.lastSummary = '';
    renderPanes();
    renderTargetSelect();
    renderTargetInfo();
    if (related.length === 0) {
      toast('関連するイシュー / MR は見つかりませんでした');
    }
  });
}

// ---------------------------------------------------------------------------
// kiro-autonomous needs（判断待ち/検収待ち・MADR 互換 ADR）
// ---------------------------------------------------------------------------

function setMode(mode) {
  state.mode = mode;
  $('mode-gitlab').classList.toggle('active', mode === 'gitlab');
  $('mode-needs').classList.toggle('active', mode === 'needs');
  $('filters').hidden = mode !== 'gitlab';
  $('candidates').hidden = mode !== 'gitlab';
  $('needs-controls').hidden = mode !== 'needs';
  $('needs-list').hidden = mode !== 'needs';
  $('candidate-count').textContent =
    mode === 'gitlab'
      ? state.candidates.length
        ? `候補: ${state.candidates.length} 件`
        : ''
      : state.needsItems.length
        ? `needs: ${state.needsItems.length} 件`
        : '';
  if (mode === 'needs') {
    $('needs-root').textContent =
      (state.config.kiroAutonomous && state.config.kiroAutonomous.root) || '（未設定）';
  }
}

async function loadNeedsCore() {
  const items = await api.kiroNeedsList();
  state.needsItems = items;
  state.needsSelectedIndex = -1;
  renderNeedsList();
  $('candidate-count').textContent = `needs: ${items.length} 件`;
}

function renderNeedsList() {
  const ul = $('needs-list');
  ul.innerHTML = '';
  state.needsItems.forEach((n, i) => {
    const li = document.createElement('li');
    li.classList.toggle('selected', i === state.needsSelectedIndex);
    const meta = [n.project || '(root)', n.kind || '-', n.submitted ? '確定済' : '未確定']
      .map(escapeHtml)
      .join(' · ');
    li.innerHTML =
      `<span class="badge needs">Needs</span>` +
      `<span class="cand-meta">${meta}</span>` +
      `<span class="cand-title">${escapeHtml(n.title || n.id)}</span>` +
      (n.why ? `<span class="cand-meta">${escapeHtml(n.why.slice(0, 80))}</span>` : '');
    li.addEventListener('click', () => selectNeeds(i));
    ul.appendChild(li);
  });
}

async function selectNeeds(index) {
  await guard('needs 読み込み', async () => {
    state.needsSelectedIndex = index;
    renderNeedsList();
    const item = state.needsItems[index];
    const detail = await api.kiroNeedsRead(item.file);
    const page = {
      kind: 'needs',
      type: 'needs',
      id: item.id,
      project: item.project,
      file: item.file,
      title: detail.title || item.id,
      raw: detail.raw,
      status: detail.status,
      needsKind: detail.kind,
      submitted: detail.submitted,
      why: detail.why,
      url: item.file,
      labels: [],
    };
    state.pages = [page];
    state.paneActive = [0, 0];
    state.targetIndex = 0;
    state.lastSummary = '';
    renderPanes();
    renderTargetSelect();
    renderTargetInfo();
  });
}

// 選択中 needs ページの内容を読み直す（フィードバック確定後など）
async function refreshNeedsPage(t) {
  try {
    const d = await api.kiroNeedsRead(t.file);
    t.raw = d.raw;
    t.submitted = d.submitted;
    t.status = d.status;
  } catch {
    /* 取り込まれて消えた場合はそのまま */
  }
  renderPanes();
  renderTargetInfo();
  try {
    await loadNeedsCore();
  } catch {
    /* 一覧更新失敗は無視 */
  }
}

// ---------------------------------------------------------------------------
// ペイン表示（タブ + URL バー + webview / ローカル表示）
// ---------------------------------------------------------------------------

function pageLabel(p) {
  if (p.kind === 'needs') {
    return `Needs ${p.id}${p.project ? ` @${p.project}` : ''}`;
  }
  const kind = p.type === 'issue' ? 'Issue' : 'MR';
  return `${kind} ${p.ref || `#${p.iid}`}`;
}

function renderPanes() {
  for (const pane of [0, 1]) {
    const bar = $(`tabbar-${pane}`);
    bar.innerHTML = '';
    state.pages.forEach((p, i) => {
      const btn = document.createElement('button');
      btn.textContent = pageLabel(p);
      btn.title = p.title;
      btn.classList.toggle('active', state.paneActive[pane] === i);
      btn.addEventListener('click', () => setPaneTab(pane, i));
      bar.appendChild(btn);
    });
    syncPane(pane);
  }
}

function setPaneTab(pane, index) {
  state.paneActive[pane] = index;
  renderPanes();
}

function syncPane(pane) {
  const page = state.pages[state.paneActive[pane]];
  const wv = $(`wv-${pane}`);
  const lv = $(`lv-${pane}`);
  if (page && page.kind === 'needs') {
    // ローカル Markdown 表示（webview は隠す）
    wv.style.display = 'none';
    lv.hidden = false;
    lv.innerHTML = mdToHtml(page.raw || '');
    $(`url-${pane}`).value = page.file;
    return;
  }
  wv.style.display = 'flex';
  lv.hidden = true;
  const url = page ? page.url : 'about:blank';
  if (wv.getAttribute('src') !== url) {
    wv.setAttribute('src', url);
  }
  $(`url-${pane}`).value = page ? page.url : '';
}

function bindPaneEvents() {
  for (const pane of [0, 1]) {
    // needs 表示内のリンクは OS 既定ブラウザで開く
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
        if (e.url && e.url !== 'about:blank') $(`url-${pane}`).value = e.url;
      });
    }
    $(`url-${pane}`).addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        const url = e.target.value.trim();
        if (/^https?:\/\//.test(url)) wv.setAttribute('src', url);
      }
    });
  }
  document.querySelectorAll('.urlbar button').forEach((btn) => {
    btn.addEventListener('click', () => {
      const pane = btn.dataset.pane;
      const url = $(`url-${pane}`).value;
      if (btn.dataset.act === 'reload') {
        $(`wv-${pane}`).reload();
      } else if (btn.dataset.act === 'copy') {
        navigator.clipboard.writeText(url);
        toast('URL をコピーしました');
      } else if (btn.dataset.act === 'external' && url) {
        api.openExternal(url).catch((err) => toast(err.message, true));
      }
    });
  });
}

// ---------------------------------------------------------------------------
// 操作対象・ラベル表示
// ---------------------------------------------------------------------------

function renderTargetSelect() {
  const sel = $('target-select');
  sel.innerHTML = '';
  state.pages.forEach((p, i) => {
    const opt = document.createElement('option');
    opt.value = i;
    opt.textContent = `${pageLabel(p)} — ${p.title.slice(0, 40)}`;
    sel.appendChild(opt);
  });
  sel.value = String(state.targetIndex);
}

function renderTargetInfo() {
  const t = currentTarget();
  const isNeeds = !!(t && t.kind === 'needs');

  // GitLab 用とニーズ用でアクションバーを切り替える
  for (const id of ['btn-comment', 'btn-merge', 'btn-close', 'btn-reopen', 'btn-export']) {
    $(id).hidden = isNeeds;
  }
  $('btn-feedback').hidden = !isNeeds;
  $('btn-approve').hidden = !isNeeds;
  $('preset-buttons').hidden = isNeeds;
  $('comment-input').placeholder = isNeeds
    ? 'フィードバック（方針・指示）を入力（Ctrl+Enter で確定 [x]）'
    : 'コメントを入力（Ctrl+Enter で投稿）';

  const wrap = $('target-labels');
  wrap.innerHTML = '';
  if (isNeeds) {
    $('target-state').textContent = `${t.needsKind || 'needs'}${t.submitted ? '・確定済' : ''}`;
    for (const text of [t.status ? `status: ${t.status}` : '', t.project ? `@${t.project}` : '']) {
      if (!text) continue;
      const chip = document.createElement('span');
      chip.className = 'chip';
      chip.textContent = text;
      wrap.appendChild(chip);
    }
    renderPresetButtons();
    return;
  }

  $('target-state').textContent = t ? t.state : '';
  if (t) {
    for (const l of t.labels) {
      const chip = document.createElement('span');
      chip.className = 'chip';
      chip.textContent = l;
      wrap.appendChild(chip);
    }
  }
  $('btn-merge').disabled = !t || t.type !== 'mr' || t.state !== 'opened';
  $('btn-close').disabled = !t || t.state !== 'opened';
  $('btn-reopen').disabled = !t || t.state === 'opened' || t.state === 'merged';
  renderPresetButtons();
}

function applyUpdatedItem(updated) {
  // pages / candidates 双方の同一アイテムを更新後の状態に置き換える
  const match = (p) =>
    p.projectId === updated.projectId && p.type === updated.type && p.iid === updated.iid;
  state.pages = state.pages.map((p) => (match(p) ? { ...p, ...updated } : p));
  state.candidates = state.candidates.map((c) => (match(c) ? { ...c, ...updated } : c));
  renderCandidates();
  renderTargetInfo();
}

// ---------------------------------------------------------------------------
// ラベルプリセット
// ---------------------------------------------------------------------------

function renderPresetButtons() {
  const row = $('preset-buttons');
  row.innerHTML = '';
  const t = currentTarget();
  (state.config.labelPresets || []).forEach((preset, i) => {
    const btn = document.createElement('button');
    btn.innerHTML =
      escapeHtml(preset.label) +
      (preset.shortcut ? `<span class="kbd">${escapeHtml(preset.shortcut)}</span>` : '');
    if (t && t.labels.includes(preset.label)) btn.classList.add('on');
    btn.addEventListener('click', () => applyPreset(i));
    row.appendChild(btn);
  });
}

async function applyPreset(index) {
  const preset = (state.config.labelPresets || [])[index];
  const t = currentTarget();
  if (!preset || !t || t.kind === 'needs') return;
  await guard('ラベル更新', async () => {
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
    const updated = await api.glUpdateLabels(targetOf(t), add, remove);
    applyUpdatedItem(updated);
    toast(
      add.length
        ? `ラベルを ${preset.label} に更新しました`
        : `ラベル ${preset.label} を外しました`
    );
  });
}

// ---------------------------------------------------------------------------
// コメント / マージ / クローズ / リオープン
// ---------------------------------------------------------------------------

async function postComment() {
  const t = currentTarget();
  if (t && t.kind === 'needs') return doFeedback(); // needs では Ctrl+Enter = フィードバック確定
  const body = $('comment-input').value.trim();
  if (!t) return toast('操作対象がありません', true);
  if (!body) return toast('コメントが空です', true);
  await guard('コメント投稿', async () => {
    await api.glComment(targetOf(t), body);
    $('comment-input').value = '';
    toast(`${pageLabel(t)} にコメントを投稿しました`);
  });
}

// needs: フィードバックを書き込み、確定チェックボックスを [x] にする
async function doFeedback() {
  const t = currentTarget();
  if (!t || t.kind !== 'needs') return toast('Needs を操作対象に選択してください', true);
  const text = $('comment-input').value.trim();
  const msg = text
    ? 'フィードバックを書き込み、確定（[x]）します。'
    : 'フィードバックなしで確定（[x]）します（blocked はそのまま再実行 / review は承認扱い）。';
  if (!window.confirm(`${pageLabel(t)}: ${msg}\nkiro-autonomous が次パスで自動取り込みします。よろしいですか？`)) {
    return;
  }
  await guard('フィードバック確定', async () => {
    await api.kiroNeedsFeedback(t.file, text);
    $('comment-input').value = '';
    toast('フィードバックを確定しました（次パスで取り込まれます）');
    await refreshNeedsPage(t);
  });
}

// needs: kiro-autonomous approve <id> を実行（検収承認 / 修正承認）
async function doApprove() {
  const t = currentTarget();
  if (!t || t.kind !== 'needs') return toast('Needs を操作対象に選択してください', true);
  const reason = $('comment-input').value.trim() || 'ビュアーから承認';
  if (!window.confirm(`kiro-autonomous approve ${t.id} を実行します。\n理由: ${reason}\nよろしいですか？`)) {
    return;
  }
  await guard('approve', async () => {
    const { output } = await api.kiroNeedsApprove(t.id, t.project, reason);
    $('comment-input').value = '';
    toast(`approve 完了: ${(output || '').split('\n')[0] || 'OK'}`);
    await refreshNeedsPage(t);
  });
}

async function doMerge() {
  const t = currentTarget();
  if (!t || t.type !== 'mr') return toast('MR を操作対象に選択してください', true);
  if (!window.confirm(`${pageLabel(t)} をマージします。よろしいですか？`)) return;
  await guard('マージ', async () => {
    const updated = await api.glMerge(targetOf(t));
    applyUpdatedItem(updated);
    toast(`${pageLabel(t)} をマージしました`);
  });
}

async function doClose() {
  const t = currentTarget();
  if (!t || t.kind === 'needs') return;
  if (!window.confirm(`${pageLabel(t)} をクローズします。よろしいですか？`)) return;
  await guard('クローズ', async () => {
    const updated = await api.glSetState(targetOf(t), 'close');
    applyUpdatedItem(updated);
    toast(`${pageLabel(t)} をクローズしました`);
  });
}

async function doReopen() {
  const t = currentTarget();
  if (!t || t.kind === 'needs') return;
  await guard('リオープン', async () => {
    const updated = await api.glSetState(targetOf(t), 'reopen');
    applyUpdatedItem(updated);
    toast(`${pageLabel(t)} をリオープンしました`);
  });
}

// ---------------------------------------------------------------------------
// 要約（ローカル CLI エージェント）と Obsidian エクスポート
// ---------------------------------------------------------------------------

async function doSummarize() {
  const t = currentTarget();
  if (!t) return toast('操作対象がありません', true);
  const dlg = $('summary-dialog');
  $('summary-status').textContent =
    `${pageLabel(t)} の内容をエージェントに送信しています…（コマンド: ` +
    `${state.config.agent.command.split(' ')[0]}）`;
  $('summary-text').value = '';
  dlg.showModal();
  await guard('要約', async () => {
    const { summary } =
      t.kind === 'needs'
        ? await api.agentSummarizeNeeds(t.file)
        : await api.agentSummarize(targetOf(t));
    state.lastSummary = summary;
    $('summary-text').value = summary;
    $('summary-status').textContent = `${pageLabel(t)} の要約が完了しました`;
  });
  if (!$('summary-text').value) {
    $('summary-status').textContent = '要約に失敗しました。設定のエージェントコマンドを確認してください。';
  }
}

async function doExport() {
  const t = currentTarget();
  if (!t) return toast('操作対象がありません', true);
  if (t.kind === 'needs') return toast('Obsidian エクスポートは GitLab の対象のみ対応です', true);
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
    merge: doMerge,
    close: doClose,
    reopen: doReopen,
    summarize: doSummarize,
    exportObsidian: doExport,
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
  $('cfg-agent-needs-prompt').value = c.agent.needsPromptTemplate;
  $('cfg-kiro-root').value = c.kiroAutonomous.root;
  $('cfg-kiro-approve').value = c.kiroAutonomous.approveCommand;
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
      needsPromptTemplate: $('cfg-agent-needs-prompt').value,
    },
    kiroAutonomous: {
      ...state.config.kiroAutonomous,
      root: $('cfg-kiro-root').value.trim(),
      approveCommand: $('cfg-kiro-approve').value.trim(),
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
    renderPresetButtons();
    if (state.mode === 'needs') setMode('needs'); // needs-root 表示を更新
    toast('設定を保存しました');
  });
}

// ---------------------------------------------------------------------------
// 初期化
// ---------------------------------------------------------------------------

async function init() {
  state.config = await api.getConfig();

  $('btn-load-groups').addEventListener('click', loadGroups);
  $('btn-load-projects').addEventListener('click', loadProjects);
  $('group-select').addEventListener('change', () => {
    loadProjects();
    loadLabelSuggestions();
  });
  $('project-select').addEventListener('change', loadLabelSuggestions);
  $('btn-search').addEventListener('click', searchCandidates);
  $('keyword-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') searchCandidates();
  });

  $('mode-gitlab').addEventListener('click', () => setMode('gitlab'));
  $('mode-needs').addEventListener('click', () => setMode('needs'));
  $('btn-load-needs').addEventListener('click', () => guard('Needs 取得', loadNeedsCore));
  $('btn-feedback').addEventListener('click', doFeedback);
  $('btn-approve').addEventListener('click', doApprove);

  bindPaneEvents();

  $('target-select').addEventListener('change', (e) => {
    state.targetIndex = Number(e.target.value) || 0;
    renderTargetInfo();
  });
  $('btn-comment').addEventListener('click', postComment);
  $('comment-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.ctrlKey) {
      e.preventDefault();
      postComment();
    }
  });
  $('btn-merge').addEventListener('click', doMerge);
  $('btn-close').addEventListener('click', doClose);
  $('btn-reopen').addEventListener('click', doReopen);
  $('btn-summarize').addEventListener('click', doSummarize);
  $('btn-export').addEventListener('click', doExport);

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

  renderPresetButtons();
  renderTargetInfo();

  if (!state.config.gitlab.token) {
    toast('GitLab のアクセストークンが未設定です。⚙ 設定から登録してください。', true);
  }
}

init().catch((err) => toast(`初期化に失敗しました: ${err.message}`, true));
