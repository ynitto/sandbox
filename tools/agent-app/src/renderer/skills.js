'use strict';

// 「設定 > スキル」: 使えるスキルを 1 つの一覧で見せ、まだ公開していないものを先頭に出す。
//
// **公開**はリポジトリへ push して人に渡すこと（artifactShare.js）。LAN の参加者に
// 見せる「共有」とは別の経路。スキルタブの「公開」は選択したスキルを公開先へ送る。
//
// 形は「保存データ」の面をそのまま借りる——行は `.setting-check`（チェック・名前と
// 1 行の補助・右端に状態）。選択・件数・削除・公開は設定の下にまとめる。
// 行ごとにボタンを並べない（一覧がボタンの壁になる）。
(function initSkills() {
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };

  const PLACE = { repo: 'リポジトリ', home: '共通' };
  const VERDICT = { trial: '様子見', blocked: '使わない' };

  let request = 0;
  const state = { doc: null, error: '', repo: '', agent: '', busy: false,
    selecting: false, selectedKeys: new Set() };
  let savedRepo = ''; let savedAgent = ''; let tokenChanged = false;
  let savedPublish = { repo: '', pushToMain: false };

  // ローカルの版が新しいものを先頭にする。
  function sorted(items) {
    return [...items].sort((a, b) => Number(b.versionComparison === 'local-newer') - Number(a.versionComparison === 'local-newer') || a.name.localeCompare(b.name));
  }

  function label(item) {
    return item.versionComparison === 'local-newer' ? '未公開' : '';
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
    parts.push(PLACE[item.place] || '');
    if (item.improving) parts.push('改善案を出しました');
    else if (VERDICT[item.verdict]) parts.push(VERDICT[item.verdict]);
    return parts.filter(Boolean).join(' · ');
  }

  function chosen(item) {
    return state.selecting && state.selectedKeys.has(item.removalKey || item.name);
  }

  function toggle(name, on) {
    if (!state.selecting || state.busy) return;
    if (on) state.selectedKeys.add(name); else state.selectedKeys.delete(name);
    renderFoot();
  }

  // 選択は削除と公開で共用し、トグルをONにするたび未選択から始める。
  function renderFoot() {
    const items = (state.doc && state.doc.items) || [];
    const selected = items.filter(chosen);
    const improvable = items.filter((item) => item.canImprove);
    const count = selected.length;
    const configured = !!state.doc?.configured;
    const unsaved = tokenChanged || $('audit-share-repo').value.trim() !== savedPublish.repo || $('audit-push-main').checked !== savedPublish.pushToMain;
    $('skills-count').textContent = count ? `${count} 件選択` : '';
    const button = $('skills-publish');
    button.hidden = false;
    button.disabled = state.busy || !configured || !count || unsaved || selected.some(item => !item.canPublish);
    button.textContent = '公開';
    button.title = unsaved ? '公開設定を保存してください' : !configured ? '公開先リポジトリを設定して保存してください'
      : count && selected.some(item => !item.canPublish) ? '公開できないスキルが含まれています' : '選択したスキルを公開先リポジトリへ公開';
    // 実測が基準を割ったものだけ、足元にもう 1 つ（要るまで出さない）
    const previous = $('skills-improve');
    if (previous) previous.remove();
    const removeMode = $('skills-remove-mode');
    removeMode.hidden = false;
    removeMode.disabled = state.busy || (!items.length && !state.selecting);
    removeMode.textContent = '選択';
    removeMode.ariaPressed = String(state.selecting);
    const remove = $('skills-remove');
    remove.hidden = false;
    remove.disabled = state.busy || !count || selected.some(item => !item.removalKey);
    remove.textContent = 'ゴミ箱へ移動';
    remove.title = count && selected.some(item => !item.removalKey) ? '削除できないスキルが含まれています' : '';
    if (!state.selecting && configured && improvable.length) {
      const next = el('button', 'small quiet', improvable.length > 1 ? `改善案を出す（${improvable.length} 件）` : '改善案を出す');
      next.type = 'button';
      next.id = 'skills-improve';
      next.disabled = state.busy || unsaved;
      next.onclick = () => runImprove(improvable);
      button.parentNode.insertBefore(next, button);
    }
  }

  function render() {
    const box = $('skills-list');
    if (!box) return;
    $('skills-repo').disabled = state.busy;
    $('skills-agent').disabled = state.busy;
    if (state.error) { box.replaceChildren(el('div', 'sub', state.error)); renderFoot(); return; }
    if (!state.doc) { box.replaceChildren(el('div', 'sub', '読み込んでいます…')); renderFoot(); return; }
    const items = state.doc.items || [];
    if (!items.length) {
      box.replaceChildren(el('div', 'sub', 'このAIが読むスキルは見つかりません'));
      renderFoot();
      return;
    }
    box.replaceChildren(...sorted(items).map((item) => {
      const row = el('label', 'setting-check');
      const check = el('input');
      check.type = 'checkbox';
      check.dataset.skill = item.name;
      check.hidden = !state.selecting;
      check.checked = chosen(item);
      check.disabled = state.busy || !state.selecting || (!item.removalKey && !item.canPublish);
      check.onchange = () => toggle(item.removalKey || item.name, check.checked);
      const text = el('span');
      const version = item.localVersion || item.version;
      const title = `${item.name}  ${version ? `v${version.replace(/^v/, '')}` : 'バージョン未設定'}`;
      text.append(el('strong', '', title), el('small', '', state.selecting
        ? item.deletePath || item.removalError || '削除できません' : detail(item)));
      row.append(check, text);
      const mark = label(item);
      if (mark) row.append(el('span', 'status warn', mark));
      if (item.error) row.title = item.error;
      return row;
    }));
    renderFoot();
  }

  function say(text) { $('skills-status').textContent = text; }

  function fail(error) {
    $('settings-error').textContent = error.message;
    $('settings-error').hidden = false;
  }

  // 1 件ずつ順に出す。途中で失敗したら、そこで止めて理由を 1 行で出す。
  async function each(names, run, verb) {
    state.busy = true;
    render();
    const done = [];
    try {
      for (const name of names) {
        say(`${name} を出しています…（${done.length + 1} / ${names.length}）`);
        const result = await run(name);
        if (result.skipped) {
          say(result.skipped === 'no-share-repo' ? '公開先リポジトリを入れてください'
            : `${name} は出せませんでした（${result.error || result.skipped}）`);
          return;
        }
        done.push(result.branch);
      }
      say(done.length === 1 ? `${done[0]} に${verb}しました` : `${done.length} 件を${verb}しました`);
    } catch (error) {
      fail(error);
    } finally {
      state.busy = false;
      state.selectedKeys.clear();
      await load();
    }
  }

  function publish() {
    if (state.busy || !state.selecting || $('skills-publish').disabled) return;
    const selected = (state.doc?.items || []).filter(chosen);
    const names = selected.map(item => item.name);
    if (!names.length) return;
    each(names, (name) => {
      const item = selected.find(item => item.name === name);
      return window.api.publish.submit({ repo: state.repo, kind: 'skill', name,
        ...(item.publicationKey ? { publicationKey: item.publicationKey, agent: state.agent } : {}) });
    }, '公開');
  }

  function runImprove(items) {
    if (state.busy || state.selecting) return;
    each(items.map((item) => item.name), (name) => window.api.publish.improve({ repo: state.repo, kind: 'skill', name }), '提出');
  }

  function toggleSelection() {
    if (state.busy || !state.doc) return;
    state.selecting = !state.selecting;
    state.selectedKeys.clear();
    say('');
    render();
  }

  async function removeSelected() {
    if (state.busy || !state.selecting || $('skills-remove').disabled) return;
    const keys = ((state.doc && state.doc.items) || []).filter(chosen).map((item) => item.removalKey);
    if (!keys.length) return;
    const token = ++request;
    state.busy = true;
    say('削除対象を確認しています…');
    render();
    try {
      const result = await window.api.removeSkills(state.repo, state.agent, keys);
      if (token !== request) return;
      if (result.cancelled) { say('削除をキャンセルしました'); return; }
      say(`${result.removed.length} 件をゴミ箱へ移動しました`
        + (result.failed.length ? `。削除できなかった項目: ${result.failed.map((item) => `${item.name}（${item.error}）`).join('、')}` : ''));
    } catch (error) {
      if (token === request) { say('削除できませんでした'); fail(error); }
    } finally {
      if (token === request) {
        state.busy = false;
        state.selectedKeys.clear();
        await load();
      }
    }
  }

  async function load() {
    const token = ++request;
    state.repo = $('skills-repo').value || '';
    state.agent = $('skills-agent').value || '';
    state.error = '';
    state.doc = null;
    render();
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
    const previousRepo = repoSelect.value || savedRepo;
    const repos = (config && config.repos) || [];
    repoSelect.replaceChildren(...repos.map((repo) => {
      const option = el('option', '', repo.split(/[\\/]/).filter(Boolean).pop() || repo);
      option.value = repo;
      return option;
    }));
    repoSelect.value = repos.includes(previousRepo) ? previousRepo : repos[0] || '';
    const agentSelect = $('skills-agent');
    const previousAgent = agentSelect.value || savedAgent;
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
    $('audit-share-token').value = '';
    $('audit-share-token').placeholder = cfg.shareTokenEncrypted ? '保存済み（変更する場合に入力）' : '未設定';
    tokenChanged = false;
    savedRepo = cfg.skillRepo || '';
    savedAgent = cfg.skillAgent || '';
    $('audit-push-main').checked = !!cfg.pushToMain;
    savedPublish = { repo: cfg.shareRepo || '', pushToMain: !!cfg.pushToMain };
    renderPublishRepoRow();
  }

  function renderPublishRepoRow() {
    $('audit-push-main-row').hidden = !$('audit-share-repo').value.trim();
    renderFoot();
  }

  function patch() {
    return {
      shareRepo: $('audit-share-repo').value.trim(),
      ...(tokenChanged ? { shareToken: $('audit-share-token').value.trim() } : {}),
      skillRepo: $('skills-repo').value || savedRepo,
      skillAgent: $('skills-agent').value || savedAgent,
      pushToMain: $('audit-push-main').checked,
    };
  }

  function reset() {
    request += 1; state.doc = null; state.error = ''; state.busy = false;
    state.selecting = false; state.selectedKeys.clear();
  }

  function open(config, agents) {
    state.selecting = false;
    state.selectedKeys.clear();
    fillChoices(config || {}, agents || []);
    say('');
    load();
  }

  function init() {
    const changeScope = () => {
      state.selecting = false; state.selectedKeys.clear(); say(''); load();
    };
    $('skills-repo').onchange = changeScope;
    $('skills-agent').onchange = changeScope;
    $('skills-publish').onclick = publish;
    $('skills-remove-mode').onclick = toggleSelection;
    $('skills-remove').onclick = removeSelected;
    $('audit-share-repo').oninput = renderPublishRepoRow;
    $('audit-share-token').oninput = () => { tokenChanged = true; renderFoot(); };
    $('audit-push-main').onchange = renderFoot;
  }

  window.Skills = { init, open, reset, fill, patch, render, load };
}());
