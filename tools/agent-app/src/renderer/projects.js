'use strict';

// プロジェクト: サイドバーの選択欄、編集のダイアログ、回答の下の「ナレッジに保存」。
// 定義の読み書き・リポジトリの決め方・送る文面は main（projects.js / projectIpc.js）が持ち、
// ここは並べて選ばせるだけ。形は借りもの:
//   選択欄 … リポジトリと同じ `.repository-control`（select と ••• の管理メニュー）
//   編集   … 作業フォルダ（#wt-dialog）と同じダイアログ。行は設定の `.setting-field`、
//            リポジトリの並びは設定の `.wt-table.settings-table`
//   保存   … 回答の下の `.message-action` と、`.more-menu` の選択肢
//   ホーム … プロジェクトを選んでいるときの空状態。見出し → 1 行 → `.execution-card` の「指示」「ナレッジ」
//            （「概要」の手動実行と同じカード）。ナレッジの一覧はホームを開いたときだけ読む
// renderer.js の state / selectRepo / renderRepos / notice を使う（読み込み順で後ろに来るので呼ぶ時に引く）。
(function initProjects() {
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const baseName = (p) => String(p || '').replace(/[\\/]+$/, '').split(/[\\/]/).pop();
  const ROLE = { main: '主', work: '作業', reference: '参照' };
  const SAVE_KINDS = [
    ['project', 'note', 'メモとして保存'],
    ['project', 'decision', '決めたこととして保存'],
    ['project', 'rule', '守ることに追記'],
    ['shared', 'note', '共通のメモとして保存'],
  ];

  // items … 一覧（projects:list）。current … 選んでいるプロジェクト（projects:open。フォルダ付き）
  // files … ホームのナレッジ（projects:files。{ key, recent, total }、読み込み中は null）
  const P = { items: [], current: null, dialog: null, files: null };

  function key() { return (state.config && state.config.lastProject) || ''; }

  async function load() {
    try {
      const listed = await api.projects.list();
      P.items = listed.items;
      P.current = key() && P.items.some((item) => item.key === key() && !item.error) ? await api.projects.open(key()) : null;
    } catch (err) {
      P.items = [];
      P.current = null;
      notice(err.message, 'error');
    }
    render();
  }

  function render() {
    const select = $('project-select');
    select.replaceChildren(new Option('プロジェクトなし', ''));
    for (const item of P.items) {
      const option = new Option(item.error ? `${item.folder}（読めません）` : item.project.name, item.key);
      option.disabled = !!item.error;
      option.title = item.error || `${baseName(item.kb)} / projects/${item.folder}`;
      select.append(option);
    }
    select.value = P.current ? P.current.key : '';
    $('project-row').hidden = !P.items.length;
    $('repository-context').classList.toggle('has-project', !!P.current);
    $('project-edit').disabled = !P.current;
    $('project-pull').disabled = !P.current;
    const kb = (state.config && state.config.knowledgeRepos) || [];
    $('repo-knowledge').textContent = kb.includes(state.repo) ? 'ナレッジリポジトリから外す' : 'ナレッジリポジトリとして読む';
    $('repo-knowledge').disabled = !state.repo;
  }

  // サイドバーのリポジトリの選択肢。プロジェクトを選んでいるときはその中だけ（役割を添える）
  function repoOptions() {
    if (!P.current) return null;
    const out = P.current.repos.filter((repo) => repo.path).map((repo) => ({ value: repo.path, label: `${repo.label}（${ROLE[repo.role]}）` }));
    if (!out.some((item) => item.value === P.current.kb)) out.push({ value: P.current.kb, label: `${baseName(P.current.kb)}（ナレッジ）` });
    return out;
  }

  function mainPath() {
    const main = P.current && P.current.repos.find((repo) => repo.role === 'main' && repo.path);
    return main ? main.path : '';
  }

  async function choose(value) {
    state.config = await api.projects.select(value);
    P.current = value ? await api.projects.open(value) : null;
    render();
    const repos = repoOptions();
    const target = mainPath() || (repos && repos[0] && repos[0].value) || '';
    if (target && target !== state.repo) await selectRepo(target);
    else renderRepos();
    // 会話の一覧はプロジェクトで絞られるので、選び直したら読み直す
    state.sessions = state.repo ? await api.listSessions(state.repo) : [];
    renderAreaContext();
    if (state.area === 'home') { refreshHome(); renderMessages(); }
    renderHeader();
    const missing = P.current ? P.current.repos.filter((repo) => !repo.path).length : 0;
    if (missing) notice(`この PC のフォルダが未設定のリポジトリが ${missing} 件あります（プロジェクトの編集で選べます）`);
  }

  // 新しい会話を送る直前。依頼に合うリポジトリへ移る（移ったらその名前を返す）
  async function routeDraft(text) {
    if (!P.current || state.current) return '';
    const chosen = await api.projects.choose(P.current.key, text);
    if (!chosen.repo || chosen.repo === state.repo || chosen.reason === 'main') return '';
    await selectRepo(chosen.repo);
    return baseName(chosen.repo);
  }

  // 回答の下: ナレッジに保存（プロジェクトの会話だけ）。選ぶと保存の指示を同じ会話へ送る
  function saveActions(session) {
    if (!session || !session.project) return null;
    const menu = el('details', 'more-menu');
    menu.append(el('summary', 'message-action', 'ナレッジに保存'));
    const panel = el('div', 'menu-panel');
    for (const [scope, kind, label] of SAVE_KINDS) {
      const button = el('button', '', label);
      button.type = 'button';
      button.onclick = async () => {
        menu.open = false;
        try {
          const { prompt } = await api.projects.knowledgePrompt(session.id, scope, kind);
          $('prompt').value = prompt;
          state.filledPrompt = prompt;
          await sendPrompt();
        } catch (err) { notice(err.message, 'error'); }
      };
      panel.append(button);
    }
    menu.append(panel);
    return menu;
  }

  // ---- ホーム（プロジェクトの入口） ----
  function refreshHome() {
    if (!P.current) { P.files = null; return; }
    const target = P.current.key;
    P.files = null;
    api.projects.files(target).then((files) => {
      if (!P.current || P.current.key !== target) return;
      P.files = { key: target, ...files };
      if (state.area === 'home' && !state.current) renderMessages();
    }).catch((err) => notice(err.message, 'error'));
  }

  function cardHead(title, lead, ...actions) {
    const head = el('div', 'execution-card-head');
    const text = el('div');
    text.append(el('h3', '', title));
    if (lead) text.append(el('p', '', lead));
    head.append(text);
    if (actions.length) {
      const row = el('div', 'row');
      row.append(...actions);
      head.append(row);
    }
    return head;
  }

  function button(label, onclick) {
    const b = el('button', 'small', label);
    b.type = 'button';
    b.onclick = () => Promise.resolve(onclick()).catch((err) => notice(err.message, 'error'));
    return b;
  }

  // ナレッジのファイルを、既存のファイルビュアーで開く（ナレッジリポジトリへ移って開く）
  async function openKnowledge(rel) {
    const kb = P.current.kb;
    if (kb !== state.repo) { await selectRepo(kb); renderRepos(); }
    await showArea('conversation');
    renderHeader();
    showView('files');
    await Files.setRoot(kb, '', {});
    await Files.openFile(rel);
    Files.reveal(rel);
  }

  async function afterAdd(result) {
    if (!result) return;
    notice(result.warning || `ナレッジに追加しました（${result.written.length} 件）`);
    refreshHome();
  }

  async function dropFiles(list) {
    const added = { written: [], warning: '' };
    for (const file of [...(list || [])]) {
      const data = new Uint8Array(await file.arrayBuffer());
      const result = await api.projects.addFile(P.current.key, file.name, data);
      added.written.push(...result.written);
      if (result.warning) added.warning = result.warning;
    }
    if (added.written.length) await afterAdd(added);
  }

  // 空状態に描く（描いたら true）。プロジェクトを選んでいないときは今のまま（呼び出し側が描く）
  function renderHome(start) {
    if (!P.current) return false;
    const cur = P.current;
    const main = cur.repos.find((repo) => repo.role === 'main') || cur.repos[0];
    const others = cur.repos.length - (main ? 1 : 0);
    start.append(el('h2', '', cur.project.name));
    start.append(el('p', '', main ? `${main.label}${others ? ` ほか ${others} リポジトリ` : ''}` : 'リポジトリが未設定です'));
    const cards = el('div', 'project-home');
    const instructions = el('section', 'execution-card');
    const firstLine = String(cur.project.instructions || '').split('\n').find((line) => line.trim()) || '';
    instructions.append(cardHead('指示', firstLine || '未設定', button('編集', () => openDialog('edit'))));
    const knowledge = el('section', 'execution-card');
    const files = P.files && P.files.key === cur.key ? P.files : null;
    knowledge.append(cardHead('ナレッジ', files ? `${files.total} 件` : '',
      button('追加', async () => afterAdd(await api.projects.pickFiles(cur.key))),
      button('索引を開く', () => openKnowledge(`projects/${cur.folder}/README.md`))));
    if (files && files.recent.length) {
      const list = el('ul', 'list project-files');
      for (const file of files.recent) {
        const li = el('li', 'row-item');
        const pick = el('button', 'list-pick');
        const body = el('span', 'grow');
        body.append(el('div', '', file.name));
        pick.append(body, el('span', 'sub', new Date(file.mtime).toLocaleDateString(undefined, { month: 'numeric', day: 'numeric' })));
        pick.title = file.rel;
        pick.onclick = () => openKnowledge(file.rel).catch((err) => notice(err.message, 'error'));
        li.append(pick);
        list.append(li);
      }
      knowledge.append(list);
      if (files.total > files.recent.length) knowledge.append(el('span', 'sub', `ほか ${files.total - files.recent.length} 件`));
    } else if (files) knowledge.append(el('span', 'sub', 'まだありません'));
    knowledge.addEventListener('dragover', (e) => { if (e.dataTransfer && [...e.dataTransfer.types].includes('Files')) { e.preventDefault(); e.stopPropagation(); knowledge.classList.add('drop'); } });
    knowledge.addEventListener('dragleave', () => knowledge.classList.remove('drop'));
    knowledge.addEventListener('drop', (e) => {
      e.preventDefault();
      e.stopPropagation();
      knowledge.classList.remove('drop');
      dropFiles(e.dataTransfer.files).catch((err) => notice(err.message, 'error'));
    });
    cards.append(instructions, knowledge);
    start.append(cards);
    return true;
  }

  // ---- 会話の見出し・••• ----
  // その会話のプロジェクト名（一覧にあるものだけ）
  function label(session) {
    const item = session && session.project ? P.items.find((entry) => entry.key === session.project && entry.project) : null;
    return item ? item.project.name : '';
  }

  function canAssign(session) {
    if (!session || session.project || !P.current) return false;
    return session.repo === P.current.kb || P.current.repos.some((repo) => repo.path === session.repo);
  }

  async function assign(session) {
    if (!canAssign(session)) return;
    await api.projects.assign(session.id);
    state.current = await api.readSession(session.id);
    state.sessions = await api.listSessions(state.repo);
    renderHeader();
    renderSessions();
    notice(`「${P.current.project.name}」に入れました`);
  }

  // ---- 編集のダイアログ ----
  function dialogError(text) {
    $('project-error').textContent = text || '';
    $('project-error').hidden = !text;
  }

  function renderDialog() {
    const d = P.dialog;
    const importing = d.mode === 'import';
    $('project-title').textContent = d.mode === 'edit' ? 'プロジェクトを編集' : importing ? 'agent-project から取り込む' : '新しいプロジェクト';
    $('project-name').value = d.project.name;
    const kb = $('project-kb');
    const repos = state.config.repos || [];
    kb.replaceChildren(...repos.map((repo) => new Option(baseName(repo), repo)));
    if (!repos.length) kb.append(new Option('リポジトリを登録してください', ''));
    kb.value = d.kb || (state.config.knowledgeRepos || [])[0] || '';
    d.kb = kb.value;
    kb.disabled = d.mode === 'edit';
    $('project-kb-path').textContent = d.kb ? `${d.kb.replace(/[\\/]+$/, '')}/projects/${d.folder || '…'}` : '';
    const body = $('project-repos');
    body.replaceChildren();
    d.repos.forEach((repo, index) => {
      const row = el('tr');
      row.append(el('td', '', repo.label));
      const role = el('select');
      role.setAttribute('aria-label', `${repo.label} の役割`);
      for (const [value, text] of Object.entries(ROLE)) role.append(new Option(text, value));
      role.value = repo.role;
      role.disabled = importing;
      role.onchange = () => {
        if (role.value === 'main') for (const other of d.repos) if (other !== repo && other.role === 'main') other.role = 'work';
        repo.role = role.value;
        renderDialog();
      };
      const desc = el('input');
      desc.value = repo.desc || '';
      desc.placeholder = '説明（任意）';
      desc.setAttribute('aria-label', `${repo.label} の説明`);
      desc.disabled = importing;
      desc.oninput = () => { repo.desc = desc.value; };
      const cellRole = el('td');
      cellRole.append(role);
      const cellDesc = el('td');
      cellDesc.append(desc);
      const cellPath = el('td');
      if (repo.path) cellPath.append(el('span', 'sub', baseName(repo.path)));
      else if (!importing) {
        const pick = el('button', 'small', 'フォルダを選ぶ');
        pick.type = 'button';
        pick.onclick = async () => {
          try { const dir = await api.projects.pickPath(repo.url); if (dir) { repo.path = dir; state.config = await api.getConfig(); renderDialog(); } }
          catch (err) { dialogError(err.message); }
        };
        cellPath.append(pick);
      } else cellPath.append(el('span', 'sub', '未設定'));
      const cellAct = el('td', 'wt-act');
      if (!importing) {
        const remove = el('button', 'small quiet', '外す');
        remove.type = 'button';
        remove.onclick = () => { d.repos.splice(index, 1); renderDialog(); };
        cellAct.append(remove);
      }
      row.append(cellRole, cellDesc, cellPath, cellAct);
      body.append(row);
    });
    const add = $('project-add-repo');
    const known = new Set(d.repos.map((repo) => repo.path).filter(Boolean));
    add.replaceChildren(...repos.filter((repo) => !known.has(repo) && repo !== d.kb).map((repo) => new Option(baseName(repo), repo)));
    $('project-add-row').hidden = importing || !add.options.length;
    $('project-instructions').value = d.project.instructions || '';
    $('project-instructions-row').hidden = importing;
    $('project-import').hidden = d.mode !== 'new';
    $('project-save').textContent = importing ? '取り込む' : '保存';
    $('project-summary').textContent = d.summary || '';
  }

  function openDialog(mode) {
    dialogError('');
    const cur = mode === 'edit' ? P.current : null;
    P.dialog = {
      mode, key: cur ? cur.key : '', kb: cur ? cur.kb : (state.config.knowledgeRepos || []).includes(state.repo) ? state.repo : '',
      folder: cur ? cur.folder : '',
      project: { name: cur ? cur.project.name : '', instructions: cur ? cur.project.instructions : '' },
      repos: cur ? cur.repos.map((repo) => ({ ...repo })) : [],
      summary: '',
    };
    renderDialog();
    $('project-dialog').showModal();
  }

  async function addRepo() {
    const d = P.dialog;
    const repo = $('project-add-repo').value;
    if (!repo) return;
    try {
      const { url } = await api.projects.remote(repo);
      if (!url) { dialogError(`${baseName(repo)} には origin の URL がありません（git remote add origin … で付けてください）`); return; }
      d.repos.push({ url, role: d.repos.some((item) => item.role === 'main') ? 'work' : 'main', desc: '', owns: [], label: baseName(repo), path: repo });
      dialogError('');
      renderDialog();
    } catch (err) { dialogError(err.message); }
  }

  async function startImport() {
    try {
      const planned = await api.projects.importPlan();
      if (!planned) return;
      const left = Object.entries(planned.leftBehind).map(([name, n]) => `${name} ${n} 件`).join('・');
      Object.assign(P.dialog, {
        mode: 'import', root: planned.root, folder: planned.folder,
        project: { name: planned.name, instructions: '' },
        repos: planned.repos.map((repo) => ({ ...repo, path: '' })),
        summary: `写すファイル ${planned.copies} 件${left ? `。移さないもの: ${left}` : ''}`,
      });
      dialogError('');
      renderDialog();
    } catch (err) { dialogError(err.message); }
  }

  async function save() {
    const d = P.dialog;
    d.project.name = d.project.name.trim();
    d.project.instructions = (d.project.instructions || '').trim();
    d.kb = $('project-kb').value;
    if (!d.project.name) { dialogError('名前を入れてください'); return; }
    if (!d.kb) { dialogError('ナレッジリポジトリを選んでください'); return; }
    $('project-save').disabled = true;
    try {
      const result = d.mode === 'import'
        ? await api.projects.import(d.root, d.kb, d.project.name)
        : await api.projects.save(d.key, d.kb, { ...d.project, repos: d.repos.map(({ url, role, desc, owns }) => ({ url, role, desc, owns })) });
      $('project-dialog').close();
      state.config = await api.getConfig();
      await load();
      await choose(result.key);
      if (result.warning) notice(result.warning);
      else if (d.mode === 'import') notice(`取り込みました（${result.written} ファイル）。旧フォルダはそのまま残っています`);
    } catch (err) { dialogError(err.message); }
    finally { $('project-save').disabled = false; }
  }

  async function toggleKnowledge() {
    $('repo-more').open = false;
    const kb = state.config.knowledgeRepos || [];
    const next = kb.includes(state.repo) ? kb.filter((repo) => repo !== state.repo) : [...kb, state.repo];
    state.config = await api.projects.setKnowledgeRepos(next);
    await load();
    if (!P.current && key()) state.config = await api.projects.select('');
    render();
  }

  function init() {
    $('project-select').onchange = () => choose($('project-select').value).catch((err) => notice(err.message, 'error'));
    $('project-edit').onclick = () => { $('project-more').open = false; openDialog('edit'); };
    $('project-new').onclick = () => { $('project-more').open = false; openDialog('new'); };
    $('repo-project-new').onclick = () => { $('repo-more').open = false; openDialog('new'); };
    $('repo-knowledge').onclick = () => toggleKnowledge().catch((err) => notice(err.message, 'error'));
    $('project-pull').onclick = async () => {
      $('project-more').open = false;
      try { await api.projects.pull(P.current.kb); await load(); notice('ナレッジリポジトリを最新にしました'); }
      catch (err) { notice(err.message, 'error'); }
    };
    $('project-close').onclick = () => $('project-dialog').close();
    $('project-name').oninput = () => { P.dialog.project.name = $('project-name').value; };
    $('project-instructions').oninput = () => { P.dialog.project.instructions = $('project-instructions').value; };
    $('project-kb').onchange = () => { P.dialog.kb = $('project-kb').value; renderDialog(); };
    $('project-add').onclick = addRepo;
    $('project-import').onclick = startImport;
    $('project-save').onclick = save;
  }

  window.Projects = { init, load, render, repoOptions, routeDraft, saveActions, renderHome, refreshHome, label, canAssign, assign };
})();
