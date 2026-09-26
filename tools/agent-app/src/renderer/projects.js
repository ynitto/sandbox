'use strict';

// プロジェクト: サイドバーの選択欄、編集のダイアログ、回答の下の「ナレッジに保存」。
// 定義の読み書き・リポジトリの決め方・送る文面は main（projects.js / projectIpc.js）が持ち、
// ここは並べて選ばせるだけ。形は借りもの:
//   選択欄 … リポジトリと同じ `.repository-control`（select と ••• の管理メニュー）
//   編集   … 作業フォルダ（#wt-dialog）と同じダイアログ。行は設定の `.setting-field`、
//            リポジトリの並びは設定の `.wt-table.settings-table`
//   保存   … 回答の下の `.message-action` と、`.more-menu` の選択肢
//   ホーム … プロジェクトを選んでいるときの空状態。見出し → 1 行 → `.execution-card` の「会話」「指示」「ナレッジ」
//            （「概要」の手動実行と同じカード）。ナレッジの一覧はホームを開いたときだけ読む。
//            「会話」は受信箱（要対応・未読）と応答中の印を、このプロジェクトの会話で絞っただけ（状態を持たない）
// renderer.js の state / selectRepo / renderRepos / notice を使う（読み込み順で後ろに来るので呼ぶ時に引く）。
(function initProjects() {
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const baseName = (p) => String(p || '').replace(/[\\/]+$/, '').split(/[\\/]/).pop();
  const ROLE = { main: '既定の作業先', work: '作業用', reference: '参照専用' };
  const SAVE_KINDS = [
    ['project', 'note', 'メモとして保存'],
    ['project', 'decision', '決めたこととして保存'],
    ['project', 'rule', '守ることに追記'],
    ['project', 'preference', '進め方として保存'],
    ['shared', 'note', '共通のメモとして保存'],
  ];

  // items … 一覧（projects:list）。current … 選んでいるプロジェクト（projects:open。フォルダ付き）
  // files … ホームのナレッジ（projects:files。{ key, recent, total }、読み込み中は null）
  const P = { items: [], current: null, dialog: null, files: null, workflows: [] };

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
    const select = $('repo-select');
    const projectsOnly = state.area === 'projects';
    select.setAttribute('aria-label', projectsOnly ? 'プロジェクト' : 'プロジェクト・リポジトリ');
    document.querySelector('label[for=repo-select]').textContent = projectsOnly ? 'プロジェクト' : 'プロジェクト・リポジトリ';
    select.replaceChildren();
    const projectGroup = el('optgroup');
    projectGroup.label = 'プロジェクト';
    for (const item of P.items) {
      const option = new Option(item.error ? `${item.folder}（読めません）` : item.project.name, `project:${item.key}`);
      option.disabled = !!item.error;
      option.title = item.error || item.project.name;
      projectGroup.append(option);
    }
    if (projectGroup.children.length) select.append(projectGroup);
    const repoGroup = el('optgroup');
    repoGroup.label = 'リポジトリ';
    const repos = [...new Set([...(state.config.repos || []), state.repo].filter(Boolean))];
    for (const repo of repos) {
      const name = baseName(repo);
      const duplicate = repos.some(other => other !== repo && baseName(other) === name);
      const option = new Option(duplicate ? `${name} — ${repo}` : name, repo);
      option.title = repo;
      repoGroup.append(option);
    }
    if (!projectsOnly && repoGroup.children.length) select.append(repoGroup);
    if (!select.options.length || (projectsOnly && !P.current)) select.prepend(new Option(projectsOnly ? 'プロジェクトを選択' : 'プロジェクト・リポジトリを追加', ''));
    select.value = P.current ? `project:${P.current.key}` : projectsOnly ? '' : state.repo;
    select.title = P.current ? P.current.project.name : state.repo;
    $('repo-selection-label').textContent = select.selectedOptions[0]?.textContent || 'プロジェクト・リポジトリを選択';
    select.disabled = projectsOnly ? !P.items.length : !repos.length && !P.items.length;
    $('project-edit').hidden = !P.current;
    $('project-pull').hidden = !P.current;
    $('repo-remove').hidden = projectsOnly || !!P.current;
    $('repo-remove').disabled = !state.repo;
    Files.setRepositories(projectsOnly ? repoOptions() || [] : [], state.repo);
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
    if (target) await selectRepo(target);
    else renderRepos();
    // 会話の一覧はプロジェクトで絞られるので、選び直したら読み直す
    state.sessions = state.repo ? await api.listSessions(state.repo) : [];
    renderAreaContext();
    if (state.area === 'projects') newDraft();
    if (['home', 'projects'].includes(state.area)) { refreshHome(); renderMessages(); }
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
    if (!P.current) { P.files = null; P.workflows = []; return; }
    const target = P.current.key;
    P.files = null;
    P.workflows = [];
    Promise.all([api.projects.files(target), api.projects.workflows(target)]).then(([files, workflows]) => {
      if (!P.current || P.current.key !== target) return;
      P.files = { key: target, ...files };
      P.workflows = workflows;
      if (['home', 'projects'].includes(state.area) && !state.current) renderMessages();
    }).catch((err) => notice(err.message, 'error'));
  }

  async function openImportedWorkflow(item) {
    const flow = await api.projects.openWorkflow(P.current.key, item.id);
    if (state.repo !== flow.root) await selectRepo(flow.root);
    const lastWorkflow = { ...(state.config.lastWorkflow || {}), [flow.root]: flow.id };
    state.config = await api.saveConfig({ lastWorkflow });
    await showArea('workflows');
    state.selectedWorkflow = flow.id;
    await syncAutomationWorkbench();
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
    if (state.area !== 'projects') await showArea('conversation');
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

  // 「会話」: このプロジェクトの会話を 確認待ち → 未読 → 応答中 の順に（言葉は受信箱と会話一覧のもの）。何も無ければ出さない
  const PROGRESS = [['action', '確認待ち'], ['unread', '未読'], ['running', '応答中']];
  const PROGRESS_MAX = 6;
  function progressCard() {
    const sessions = new Map((state.sessions || []).filter((s) => s.kind === 'conversation' && s.project === P.current.key).map((s) => [s.id, s]));
    const queueOf = new Map();
    for (const item of (state.attention && state.attention.items) || []) {
      const t = item.target || {};
      if (t.kind === 'conversation' && sessions.has(t.id) && !queueOf.has(t.id)) queueOf.set(t.id, item);
    }
    const rows = [];
    for (const [queue, labelText] of PROGRESS) {
      for (const s of sessions.values()) {
        const item = queueOf.get(s.id);
        const hit = queue === 'running' ? state.running.has(s.id) : !state.running.has(s.id) && item && item.queue === queue;
        if (hit) rows.push({ session: s, item, label: labelText, queue });
      }
    }
    if (!rows.length) return null;
    const card = el('section', 'execution-card');
    card.id = 'project-progress';
    card.append(cardHead('会話', ''));
    const list = el('ul', 'list project-files');
    for (const row of rows.slice(0, PROGRESS_MAX)) {
      const li = el('li', `row-item${row.queue === 'action' ? ' attention' : row.queue === 'running' ? ' running' : ''}`);
      const pick = el('button', 'list-pick');
      const body = el('span', 'grow');
      body.append(el('div', '', row.session.title || '（無題）'), el('div', 'sub', `${row.label} · ${baseName(row.session.repo)}`));
      pick.append(body);
      pick.onclick = () => (row.item ? openAttentionItem(row.item) : openSessionInRepo(row.session.repo, row.session.id))
        .catch((err) => notice(err.message, 'error'));
      li.append(pick);
      list.append(li);
    }
    card.append(list);
    if (rows.length > PROGRESS_MAX) card.append(el('span', 'sub', `ほか ${rows.length - PROGRESS_MAX} 件`));
    return card;
  }

  // 受信箱や応答中が変わったら、ホームの「会話」だけを描き直す（入力欄やほかのカードは触らない）
  function refreshProgress() {
    if (!P.current || state.area !== 'projects' || state.current) return;
    const cards = document.querySelector('.project-home');
    if (!cards) return;
    const held = document.getElementById('project-progress');
    const next = progressCard();
    if (held && next) held.replaceWith(next);
    else if (held) held.remove();
    else if (next) cards.prepend(next);
  }

  // 空状態に描く（描いたら true）。プロジェクトを選んでいないときは今のまま（呼び出し側が描く）
  function renderHome(start) {
    if (!P.current) return false;
    const cur = P.current;
    const main = cur.repos.find((repo) => repo.role === 'main') || cur.repos[0];
    const others = cur.repos.length - (main ? 1 : 0);
    const heading = el('div', 'project-home-heading');
    heading.append(el('h2', '', cur.project.name), button('プロジェクトを編集', () => openDialog('edit')));
    start.append(heading);
    start.append(el('p', '', main ? `${main.label}${others ? ` ほか ${others} リポジトリ` : ''}` : 'リポジトリが未設定です'));
    const cards = el('div', 'project-home');
    const instructions = el('section', 'execution-card');
    const firstLine = String(cur.project.instructions || '').split('\n').find((line) => line.trim()) || '';
    instructions.append(cardHead('指示', firstLine || '未設定'));
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
    const progress = progressCard();
    if (progress) cards.append(progress);
    cards.append(instructions, knowledge);
    if (P.workflows.length) {
      const tasks = el('section', 'execution-card');
      tasks.append(cardHead('ワークフロー', `${P.workflows.length} 件`));
      const list = el('ul', 'list project-files');
      for (const task of P.workflows) {
        const row = el('li', 'row-item');
        const pick = button(task.name, () => openImportedWorkflow(task));
        pick.className = 'list-pick';
        pick.title = task.error || `ワークフロー画面で開く · ${baseName(task.root)}`;
        row.append(pick); list.append(row);
      }
      tasks.append(list); cards.append(tasks);
    }
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

  function renderDestination() {
    const d = P.dialog;
    const folder = d.mode === 'edit' ? d.folder : ProjectPath.folderName(d.project.name);
    const destination = d.kb ? `${d.kb.replace(/[\\/]+$/, '')}/projects/${folder}/` : '';
    $('project-kb-path').textContent = destination || 'ナレッジリポジトリを選択してください';
    $('project-kb-path').title = destination;
    const summary = $('project-summary');
    summary.replaceChildren();
    summary.hidden = d.mode !== 'import';
    if (summary.hidden || !d.plan) return;
    const result = ProjectImportBundle.build(d.plan.items, [...d.selected]);
    const workflows = d.plan.items.filter(item => item.group === 'pending' && d.selected.has(item.id)).length;
    summary.append(el('strong', '', `${result.sources} 資料 → ${result.documents.length} 文書・${formatSize(result.bytes)}${workflows ? ` ＋ ${workflows} ワークフロー` : ''}`));
    const source = el('p', 'sub', `取り込み元: ${baseName(d.root)}`);
    source.title = d.root;
    summary.append(source);
    summary.append(el('p', 'sub', '重複・管理情報を整理。元の資料は変更しません。'));
    const groups = el('div', 'project-import-groups');
    for (const group of ProjectImportBundle.groups) {
      const items = d.plan.items.filter(item => item.group === group.id);
      if (!items.length) continue;
      const label = el('label', 'project-import-choice');
      const check = el('input'); check.type = 'checkbox';
      check.id = `project-import-group-${group.id}`;
      const selected = items.filter(item => d.selected.has(item.id)).length;
      check.checked = selected === items.length;
      check.indeterminate = selected > 0 && selected < items.length;
      check.onchange = () => { for (const item of items) { if (check.checked) d.selected.add(item.id); else d.selected.delete(item.id); } renderDestination(); $(check.id).focus(); };
      label.append(check, el('span', '', group.label), el('span', 'sub', `${selected} / ${items.length}`));
      groups.append(label);
    }
    summary.append(groups);
    const actions = el('div', 'row');
    const pick = button('内容を選ぶ', () => {
      $('project-import-search').value = '';
      $('project-import-preview').hidden = true;
      renderImportPicker();
      $('project-import-picker').showModal();
    });
    actions.append(pick, el('span', 'sub', '実行ログは対象外・定義と索引は別途作成'));
    summary.append(actions);
  }

  function formatSize(bytes) { return `${(bytes / 1024).toFixed(1)} KB`; }

  function renderImportPicker() {
    const d = P.dialog;
    const query = $('project-import-search').value.trim().toLowerCase();
    const list = $('project-import-candidates'); list.replaceChildren();
    const result = ProjectImportBundle.build(d.plan.items, [...d.selected]);
    $('project-import-selection-count').textContent = `${result.items} / ${d.plan.items.length} 件を選択・${formatSize(result.bytes)}`;
    for (const item of d.plan.items.filter(item => `${item.title} ${item.sources.join(' ')}`.toLowerCase().includes(query))) {
      const row = el('div', 'project-import-candidate');
      const label = el('label', 'project-import-choice');
      const check = el('input'); check.type = 'checkbox'; check.checked = d.selected.has(item.id);
      check.onchange = () => {
        if (check.checked) d.selected.add(item.id); else d.selected.delete(item.id);
        renderDestination();
        const result = ProjectImportBundle.build(d.plan.items, [...d.selected]);
        $('project-import-selection-count').textContent = `${result.items} / ${d.plan.items.length} 件を選択・${formatSize(result.bytes)}`;
      };
      const text = el('span');
      text.append(el('strong', '', item.title), el('span', 'sub', `${item.sources.join(' / ')} · ${formatSize(ProjectImportBundle.bytes(item.content))}`));
      label.append(check, text);
      const preview = button('本文', () => {
        $('project-import-preview').textContent = item.content;
        $('project-import-preview').hidden = false;
        $('project-import-preview').focus();
      });
      preview.setAttribute('aria-label', `${item.title} の本文`);
      row.append(label, preview); list.append(row);
    }
    if (!list.children.length) list.append(el('p', 'sub', '該当する資料はありません'));
  }

  function repoOptionsForDialog(repos) {
    return repos.map(repo => {
      const name = baseName(repo);
      const duplicate = repos.some(other => other !== repo && baseName(other) === name);
      const option = new Option(duplicate ? `${name} — ${repo}` : name, repo);
      option.title = repo;
      return option;
    });
  }

  function renderDialog() {
    const d = P.dialog;
    const working = d.repos.filter(repo => repo.role !== 'reference');
    if (working.length && !working.some(repo => repo.role === 'main')) working[0].role = 'main';
    const importing = d.mode === 'import';
    $('project-title').textContent = d.mode === 'edit' ? 'プロジェクトを編集' : importing ? 'agent-project から取り込む' : '新しいプロジェクト';
    $('project-name').value = d.project.name;
    const kb = $('project-kb');
    const repos = state.config.repos || [];
    kb.replaceChildren(new Option(repos.length ? '保存先を選択してください' : 'リポジトリを登録してください', ''), ...repoOptionsForDialog(repos));
    kb.value = d.kb || '';
    d.kb = kb.value;
    kb.title = d.kb;
    kb.disabled = d.mode === 'edit';
    renderDestination();
    const body = $('project-repos');
    body.replaceChildren();
    $('project-repo-section').hidden = importing && !d.repos.length;
    $('project-repo-count').textContent = d.repos.length ? `${d.repos.length} 件` : '';
    if (!d.repos.length) body.append(el('div', 'project-repo-empty', importing ? 'リポジトリの指定なし' : '追加したリポジトリがここに並びます'));
    d.repos.forEach((repo) => {
      const row = el('div', 'project-repo-row');
      const name = el('strong', 'project-repo-name', repo.label);
      if (repo.localId) name.append(el('span', 'sub', 'ローカル'));
      const role = el('select');
      role.setAttribute('aria-label', `${repo.label} の役割`);
      for (const value of ['work', 'reference']) role.append(new Option(ROLE[value], value));
      role.value = repo.role === 'main' ? 'work' : repo.role;
      role.disabled = importing;
      role.onchange = () => {
        repo.role = role.value;
        renderDialog();
      };
      row.append(name, role);
      if (!importing) {
        const remove = el('button', 'quiet project-repo-remove', '×');
        remove.type = 'button';
        remove.setAttribute('aria-label', `${repo.label} を外す`);
        remove.title = `${repo.label} を外す`;
        remove.onclick = () => { d.repos.splice(d.repos.indexOf(repo), 1); renderDialog(); };
        row.append(remove);
      }
      const details = el('div', 'project-repo-meta');
      if (repo.role !== 'reference') {
        const label = el('label', 'project-repo-default');
        const radio = el('input');
        radio.type = 'radio';
        radio.name = 'project-default-repo';
        radio.checked = repo.role === 'main';
        radio.disabled = importing;
        radio.setAttribute('aria-label', `${repo.label} を既定の作業先にする`);
        radio.onchange = () => {
          for (const other of working) other.role = other === repo ? 'main' : 'work';
          renderDialog();
        };
        label.append(radio, document.createTextNode('既定の作業先'));
        details.append(label);
      }
      if (repo.path) details.append(el('div', 'project-path', repo.path));
      else if (!importing) {
        const pick = el('button', 'small', 'フォルダを選ぶ');
        pick.type = 'button';
        pick.onclick = async () => {
          try { const dir = await api.projects.pickPath(repo.url || `local:${repo.localId}`); if (dir) { repo.path = dir; state.config = await api.getConfig(); renderDialog(); } }
          catch (err) { dialogError(err.message); }
        };
        details.append(pick);
      } else details.append(el('div', 'sub', '取り込み後にフォルダを選択'));
      const descLabel = el('label', '');
      const desc = el('input');
      desc.value = repo.desc || '';
      desc.placeholder = '担当する内容（任意）';
      desc.setAttribute('aria-label', `${repo.label} の説明`);
      desc.disabled = importing;
      desc.oninput = () => { repo.desc = desc.value; };
      descLabel.append(desc);
      details.append(descLabel);
      row.append(details);
      body.append(row);
    });
    const add = $('project-add-repo');
    const known = new Set(d.repos.map((repo) => repo.path).filter(Boolean));
    add.replaceChildren(...repoOptionsForDialog(repos.filter((repo) => !known.has(repo))));
    $('project-add-row').hidden = importing || !add.options.length;
    $('project-instructions').value = d.project.instructions || '';
    $('project-instructions-row').hidden = importing;
    $('project-import').hidden = d.mode !== 'new';
    $('project-save').textContent = importing ? '取り込む' : d.mode === 'edit' ? '保存' : '作成';

  }

  function openDialog(mode) {
    dialogError('');
    const cur = mode === 'edit' ? P.current : null;
    P.dialog = {
      mode, key: cur ? cur.key : '', kb: cur ? cur.kb : (state.config.knowledgeRepos || []).includes(state.repo) ? state.repo : (state.config.knowledgeRepos || [])[0] || '',
      folder: cur ? cur.folder : '',
      project: { name: cur ? cur.project.name : '', instructions: cur ? cur.project.instructions : '' },
      repos: cur ? cur.repos.map((repo) => ({ ...repo })) : [],
    };
    renderDialog();
    $('project-dialog').showModal();
  }

  async function addRepo() {
    const d = P.dialog;
    const repo = $('project-add-repo').value;
    if (!repo) return;
    $('project-add').disabled = true;
    try {
      const { url, localId } = await api.projects.remote(repo);
      d.repos.push({ url, localId, role: d.repos.some((item) => item.role === 'main') ? 'work' : 'main', desc: '', owns: [], label: baseName(repo), path: repo });
      dialogError('');
      renderDialog();
    } catch (err) { dialogError(err.message); }
    finally { $('project-add').disabled = false; }
  }

  async function startImport() {
    const draft = P.dialog;
    $('project-import').disabled = true;
    try {
      const planned = await api.projects.importPlan();
      if (!planned || P.dialog !== draft) return;
      Object.assign(P.dialog, {
        mode: 'import', root: planned.root, folder: planned.folder,
        project: { name: $('project-name').value.trim() || planned.name, instructions: '' },
        repos: planned.repos.map((repo) => ({ ...repo })),
        plan: planned, selected: new Set(planned.selected),
      });
      dialogError('');
      renderDialog();
    } catch (err) { dialogError(err.message); }
    finally { $('project-import').disabled = false; }
  }

  async function save() {
    const d = P.dialog;
    d.project.name = $('project-name').value.trim();
    d.project.instructions = (d.project.instructions || '').trim();
    d.kb = $('project-kb').value;
    if (!d.project.name) { dialogError('名前を入れてください'); $('project-name').focus(); return; }
    if (!d.kb) { dialogError('保存先を選んでください'); $('project-kb').focus(); return; }
    $('project-save').disabled = true;
    try {
      const result = d.mode === 'import'
        ? await api.projects.import(d.root, d.kb, d.project.name, [...d.selected])
        : await api.projects.save(d.key, d.kb, { ...d.project, repos: d.repos.map(({ url, localId, label, role, desc, owns }) => ({ url, localId, label, role, desc, owns })) });
      $('project-dialog').close();
      state.config = await api.getConfig();
      await load();
      await choose(result.key);
      if (result.warning) notice(result.warning);
      else notice(d.mode === 'import' ? `取り込みました（${result.written} ファイル）${result.skipped?.length ? `・同名 ${result.skipped.length} 件をスキップ` : ''}` : result.localOnly ? 'この端末に保存しました' : '保存しました');
    } catch (err) { dialogError(err.message); }
    finally { $('project-save').disabled = false; }
  }

  async function chooseContext(value) {
    if (value.startsWith('project:')) return choose(value.slice('project:'.length));
    if (key()) {
      state.config = await api.projects.select('');
      P.current = null;
      P.files = null;
      P.workflows = [];
    }
    await selectRepo(value);
    render();
  }

  function init() {
    $('project-edit').onclick = () => { $('repo-more').open = false; openDialog('edit'); };
    $('project-new').onclick = () => { $('repo-more').open = false; openDialog('new'); };
    $('project-pull').onclick = async () => {
      $('repo-more').open = false;
      try { await api.projects.pull(P.current.kb); await load(); notice('ナレッジリポジトリを最新にしました'); }
      catch (err) { notice(err.message, 'error'); }
    };
    $('project-close').onclick = () => $('project-dialog').close();
    $('project-name').oninput = () => { P.dialog.project.name = $('project-name').value; renderDestination(); };
    $('project-instructions').oninput = () => { P.dialog.project.instructions = $('project-instructions').value; };
    $('project-kb').onchange = () => { P.dialog.kb = $('project-kb').value; renderDialog(); };
    $('project-add').onclick = addRepo;
    $('project-import').onclick = startImport;
    $('project-save').onclick = save;
    $('project-import-picker-close').onclick = () => $('project-import-picker').close();
    $('project-import-search').oninput = renderImportPicker;
    $('project-import-recommended').onclick = () => { P.dialog.selected = new Set(P.dialog.plan.selected); renderImportPicker(); renderDestination(); };
    $('project-import-clear').onclick = () => { P.dialog.selected.clear(); renderImportPicker(); renderDestination(); };
  }

  window.Projects = { init, load, render, choose, chooseContext, repoOptions, routeDraft, saveActions, renderHome, refreshHome, refreshProgress, label, canAssign, assign };
})();
