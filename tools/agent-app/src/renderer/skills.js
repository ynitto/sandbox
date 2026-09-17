'use strict';

// 「設定 > スキル」: 使えるスキルを 1 つの一覧で見せ、まだ公開していないものを先頭に出す。
//
// **公開**はリポジトリへ push して人に渡すこと（artifactShare.js）。LAN の参加者に
// 見せる「共有」とは別物なので、この画面では「共有」という言葉を使わない。
//
// 行の形は「利用状況」の一覧と同じ `.row`（名前 → 補助の文字 → spacer → 状態 → 操作）。
// 新しい部品は作らない。
(function initSkills() {
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };

  const PLACE = { repo: 'リポジトリ', home: '共通' };
  const VERDICT = { trial: '様子見', blocked: '使わない' };

  let request = 0;
  const state = { doc: null, error: '', loading: false, repo: '', agent: '' };

  // 未公開を先頭に。公開済みと、リポジトリの外にあるものは後ろへ回す。
  const ORDER = { unpublished: 0, updated: 0, published: 1, missing: 1, outside: 2 };
  function sorted(items) {
    return [...items].sort((a, b) => (ORDER[a.status] ?? 3) - (ORDER[b.status] ?? 3) || a.name.localeCompare(b.name));
  }

  function label(item) {
    if (item.status === 'unpublished') return '未公開';
    if (item.status === 'updated') return '未公開の変更';
    return '';
  }

  // 説明は 1 行に収める。長い説明をそのまま出すと、行ごとに高さが変わって一覧が読めなくなる。
  const CUT = 60;
  function short(text) {
    const body = String(text || '').replace(/\s+/g, ' ').trim();
    return body.length > CUT ? `${body.slice(0, CUT)}…` : body;
  }

  function detail(item) {
    const parts = [];
    if (item.description) parts.push(short(item.description));
    if (item.version) parts.push(`v${item.version}`);
    parts.push(PLACE[item.place] || '');
    if (item.status === 'published' && item.branch) parts.push(`公開済み ${item.branch}`);
    if (item.improving) parts.push('改善案を出しました');
    else if (VERDICT[item.verdict]) parts.push(VERDICT[item.verdict]);
    return parts.filter(Boolean).join(' · ');
  }

  function render() {
    const box = $('skills-list');
    if (!box) return;
    if (state.error) { box.replaceChildren(el('div', 'sub', state.error)); return; }
    if (!state.doc) { box.replaceChildren(el('div', 'sub', '読み込んでいます…')); return; }
    const items = state.doc.items || [];
    if (!items.length) {
      box.replaceChildren(el('div', 'sub', 'このAIが読むスキルは見つかりません'));
      return;
    }
    box.replaceChildren(...sorted(items).map((item) => {
      const row = el('div', 'row');
      const name = el('span', '', item.name);
      row.append(name);
      const mark = label(item);
      if (mark) row.append(el('span', 'status warn', mark));
      row.append(el('small', 'sub', detail(item)), el('span', 'spacer'));
      if (item.canPublish) {
        const button = el('button', 'small', '公開する');
        button.type = 'button';
        button.onclick = () => publish(item, button);
        row.append(button);
      }
      if (item.canImprove) {
        const button = el('button', 'small', '改善案を出す');
        button.type = 'button';
        button.onclick = () => improve(item, button);
        row.append(button);
      }
      return row;
    }));
    if (!state.doc.configured) box.append(el('small', 'sub', '公開するには、下の公開先リポジトリを入れてください'));
  }

  function say(text) { $('settings-status').textContent = text; }

  function fail(error) {
    $('settings-error').textContent = error.message;
    $('settings-error').hidden = false;
  }

  async function publish(item, button) {
    button.disabled = true;
    const before = button.textContent;
    button.textContent = '出しています…';
    try {
      const result = await window.api.publish.submit({ repo: state.repo, kind: 'skill', name: item.name });
      if (result.skipped) say(result.skipped === 'no-share-repo' ? '公開先を入れてください' : `公開できませんでした（${result.error || result.skipped}）`);
      else say(`${result.branch} に公開しました`);
      await load();
    } catch (error) { fail(error); } finally { button.disabled = false; button.textContent = before; }
  }

  async function improve(item, button) {
    button.disabled = true;
    button.textContent = '出しています…';
    try {
      const result = await window.api.publish.improve({ repo: state.repo, kind: 'skill', name: item.name });
      if (result.skipped) say(result.skipped === 'no-share-repo' ? '公開先を入れてください' : `出せませんでした（${result.error || result.skipped}）`);
      else say(`${result.branch} を出しました`);
      await load();
    } catch (error) { fail(error); } finally { button.disabled = false; button.textContent = '改善案を出す'; }
  }

  async function load() {
    const token = ++request;
    state.repo = $('skills-repo').value || '';
    state.agent = $('skills-agent').value || '';
    state.error = '';
    if (!state.doc) render();
    try {
      const doc = await window.api.publish.skills(state.repo, state.agent);
      if (token !== request) return;
      state.doc = doc;
    } catch (error) {
      if (token !== request) return;
      state.doc = null;
      state.error = error.message;
    }
    render();
  }

  // 選べるものは開くたびに入れ直す（リポジトリや使える AI は外で増える）。
  function fillChoices(config, agents) {
    const repoSelect = $('skills-repo');
    const previousRepo = repoSelect.value;
    repoSelect.replaceChildren(...(config.repos || []).map((repo) => {
      const option = el('option', '', repo.split(/[\\/]/).filter(Boolean).pop() || repo);
      option.value = repo;
      return option;
    }));
    repoSelect.value = (config.repos || []).includes(previousRepo) ? previousRepo : (config.repos || [])[0] || '';
    const agentSelect = $('skills-agent');
    const previousAgent = agentSelect.value;
    // AI が引けていないときは、置き場を絞らずに全部見せる（空の選択肢を出さない）。
    const names = [...new Set((agents || []).map((agent) => String((agent && agent.name) || agent || '')).filter(Boolean))];
    const choices = names.length ? names : [''];
    agentSelect.replaceChildren(...choices.map((name) => {
      const option = el('option', '', name || 'すべて');
      option.value = name;
      return option;
    }));
    agentSelect.value = choices.includes(previousAgent) ? previousAgent : choices[0];
  }

  function fill(config) {
    const cfg = (config && config.audit) || {};
    $('audit-share-repo').value = cfg.shareRepo || '';
    $('audit-push-main').checked = !!cfg.pushToMain;
    renderPublishRepoRow();
  }

  function renderPublishRepoRow() {
    $('audit-push-main-row').hidden = !$('audit-share-repo').value.trim();
  }

  function patch() {
    return {
      shareRepo: $('audit-share-repo').value.trim(),
      pushToMain: $('audit-push-main').checked,
    };
  }

  function reset() { request += 1; state.doc = null; state.error = ''; }

  function open(config, agents) {
    fillChoices(config || {}, agents || []);
    load();
  }

  function init() {
    $('skills-repo').onchange = load;
    $('skills-agent').onchange = load;
    $('audit-share-repo').oninput = renderPublishRepoRow;
  }

  window.Skills = { init, open, reset, fill, patch, render };
}());
