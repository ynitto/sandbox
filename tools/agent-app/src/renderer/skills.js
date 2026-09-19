'use strict';

// 「設定 > スキル」: 使えるスキルを 1 つの一覧で見せ、まだ公開していないものを先頭に出す。
//
// **公開**はリポジトリへ push して人に渡すこと（artifactShare.js）。LAN の参加者に
// 見せる「共有」とは別物なので、この画面では「共有」という言葉を使わない。
//
// 形は「保存データ」の面をそのまま借りる——行は `.setting-check`（チェック・名前と
// 1 行の補助・右端に状態）、足元の `.environment-status` に選んだ数と**操作を 1 つだけ**。
// 行ごとにボタンを並べない（一覧がボタンの壁になる）。
(function initSkills() {
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };

  const PLACE = { repo: 'リポジトリ', home: '共通' };
  const VERDICT = { trial: '様子見', blocked: '使わない' };

  let request = 0;
  // picked … 利用者が外した行を覚える（未公開は既定で入れる。保存データの面と同じ作法）
  const state = { doc: null, error: '', repo: '', agent: '', busy: false, picked: null };
  let savedRepo = ''; let savedAgent = ''; let tokenChanged = false;

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

  // 既定は「未公開のものすべて」。一度でも触ったら、その選択を覚える。
  function chosen(item) {
    if (!item.canPublish) return false;
    return state.picked ? state.picked.has(item.name) : true;
  }

  function picks() {
    const items = (state.doc && state.doc.items) || [];
    return items.filter((item) => chosen(item)).map((item) => item.name);
  }

  function toggle(name, on) {
    if (!state.picked) {
      const items = (state.doc && state.doc.items) || [];
      state.picked = new Set(items.filter((item) => item.canPublish).map((item) => item.name));
    }
    if (on) state.picked.add(name); else state.picked.delete(name);
    renderFoot();
  }

  // 足元は 1 行。選んだ数と、押せる操作を 1 つだけ。
  function renderFoot() {
    const items = (state.doc && state.doc.items) || [];
    const waiting = items.filter((item) => item.canPublish).length;
    const improvable = items.filter((item) => item.canImprove);
    const count = picks().length;
    const configured = !state.doc || state.doc.configured;
    $('skills-count').textContent = !configured ? '公開先リポジトリを入れると公開できます'
      : waiting ? `未公開 ${waiting} 件` : '';
    const button = $('skills-publish');
    button.hidden = !configured || !waiting;
    button.disabled = state.busy || !count;
    button.textContent = count > 1 ? `選んだ ${count} 件を公開` : '公開する';
    // 実測が基準を割ったものだけ、足元にもう 1 つ（要るまで出さない）
    const previous = $('skills-improve');
    if (previous) previous.remove();
    if (configured && improvable.length) {
      const next = el('button', 'small quiet', improvable.length > 1 ? `改善案を出す（${improvable.length} 件）` : '改善案を出す');
      next.type = 'button';
      next.id = 'skills-improve';
      next.disabled = state.busy;
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
      check.checked = chosen(item);
      check.disabled = !item.canPublish || state.busy;
      check.onchange = () => toggle(item.name, check.checked);
      const text = el('span');
      const version = item.localVersion || item.version;
      const title = `${item.name}  ${version ? `v${version.replace(/^v/, '')}` : 'バージョン未設定'}`;
      text.append(el('strong', '', title), el('small', '', detail(item)));
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
      state.picked = null;
      await load();
    }
  }

  function publish() {
    const names = picks();
    if (!names.length) return;
    each(names, (name) => window.api.publish.submit({ repo: state.repo, kind: 'skill', name }), '公開');
  }

  function runImprove(items) {
    each(items.map((item) => item.name), (name) => window.api.publish.improve({ repo: state.repo, kind: 'skill', name }), '提出');
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
    renderPublishRepoRow();
  }

  function renderPublishRepoRow() {
    $('audit-push-main-row').hidden = !$('audit-share-repo').value.trim();
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

  function reset() { request += 1; state.doc = null; state.error = ''; state.busy = false; state.picked = null; }

  function open(config, agents) {
    fillChoices(config || {}, agents || []);
    say('');
    load();
  }

  function init() {
    $('skills-repo').onchange = () => { state.picked = null; load(); };
    $('skills-agent').onchange = () => { state.picked = null; load(); };
    $('skills-publish').onclick = publish;
    $('audit-share-repo').oninput = renderPublishRepoRow;
    $('audit-share-token').oninput = () => { tokenChanged = true; };
  }

  window.Skills = { init, open, reset, fill, patch, render, load };
}());
