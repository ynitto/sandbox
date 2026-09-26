'use strict';

// プロジェクト（projects.js）の画面向けの口と、会話の起動に添えるもの。
//
// ホストに聞くのは次の 3 つだけで、どれも人の操作のときに 1 回ずつ走る（監視・定期実行はしない）:
//   - 登録リポジトリの origin の URL（定義の URL とこの PC のフォルダを結ぶため。結果は repoPaths に残す）
//   - 定義を保存・取り込み・ファイルを足したときの git commit（そのファイルだけ）と push（今のブランチを origin へ）
//   - 「最新を取得」の git pull --ff-only

const fs = require('fs');
const os = require('os');
const path = require('path');
const { randomUUID } = require('crypto');
const store = require('./store');
const host = require('./host');
const projects = require('./projects');
const projectImport = require('./projectImport');
const projectWorkflows = require('./projectWorkflows');
const { userData, requireRepo, distroFor } = require('./paths');

const remotes = new Map();   // 登録フォルダ → origin の URL（アプリを閉じるまで。'' は origin 無し）

async function remoteOf(repo) {
  if (remotes.has(repo)) return remotes.get(repo);
  let url = '';
  try {
    const r = await host.shellFor(distroFor(repo)).exec(['git', '-C', host.toHostPath(repo), 'remote', 'get-url', 'origin'], { timeoutMs: 10000 });
    if (r.ok) url = r.output.trim();
  } catch { /* git でなければ対応なし */ }
  remotes.set(repo, url);
  return url;
}

// 定義を、この PC で使える形にする。フォルダは登録済みのものだけを採る。
function context(key, cfg = store.loadConfig(userData())) {
  if (!key) return null;
  let item;
  try { item = projects.read(key); } catch { return null; }
  if (!cfg.knowledgeRepos.includes(item.kb)) return null;
  const resolved = projects.resolve(item.project, cfg.repoPaths)
    .map((repo) => ({ ...repo, path: cfg.repos.includes(repo.path) ? repo.path : '' }));
  return { ...item, resolved };
}

// まだフォルダの分からない URL を、登録リポジトリの origin から埋める（分かった分は repoPaths に残す）
async function fillPaths(item) {
  const cfg = store.loadConfig(userData());
  const missing = new Set(item.project.repos.filter(repo => repo.url).map((repo) => projects.normalizeUrl(repo.url))
    .filter((url) => !cfg.repos.includes(cfg.repoPaths[url] || '')));
  if (!missing.size) return false;
  const found = {};
  for (const repo of cfg.repos) {
    const url = projects.normalizeUrl(await remoteOf(repo));
    if (url && missing.has(url) && !found[url]) found[url] = repo;
  }
  if (!Object.keys(found).length) return false;
  store.saveConfig(userData(), { repoPaths: { ...cfg.repoPaths, ...found } });
  return true;
}

// 画面に渡す形（フォルダの有無と、登録のラベル）
function present(item) {
  return {
    key: item.key, kb: item.kb, folder: item.folder, error: item.error || '',
    project: item.project,
    repos: (item.resolved || []).map(({ url, localId, role, desc, owns, label, path: dir }) => ({ url, localId, role, desc, owns: owns || [], label, path: dir })),
  };
}

async function commit(kb, files, message) {
  if (!files.length) return { committed: false, warning: '' };
  const shell = host.shellFor(distroFor(kb));
  const dir = host.toHostPath(kb);
  const tracked = await shell.exec(['git', '-C', dir, 'rev-parse', '--is-inside-work-tree'], { timeoutMs: 10000 });
  if (!tracked.ok) return { committed: false, pushed: false, localOnly: true };
  const add = await shell.exec(['git', '-C', dir, 'add', '--', ...files], { timeoutMs: 20000 });
  const done = add.ok && (await shell.exec(['git', '-C', dir, 'commit', '-m', message, '--', ...files], { timeoutMs: 20000 })).ok;
  if (!done) return { committed: false, pushed: false, warning: 'ファイルは書きましたが、ナレッジリポジトリにコミットできませんでした（git の管理下か確認してください）' };
  if (!await remoteOf(kb)) return { committed: true, pushed: false, localOnly: true };
  const push = await shell.exec(['git', '-C', dir, 'push', 'origin', 'HEAD'], { timeoutMs: 60000 });
  return push.ok ? { committed: true, pushed: true, warning: '' }
    : { committed: true, pushed: false, warning: `コミットしましたが push できませんでした: ${push.output.trim().split('\n').pop() || 'git push に失敗'}` };
}

// いま選んでいるプロジェクトに repo が入っていればその鍵（タスク・ワークフローを作る会話に付ける）
function projectFor(repo, cfg = store.loadConfig(userData())) {
  const ctx = context(cfg.lastProject, cfg);
  if (!ctx) return '';
  return ctx.kb === repo || ctx.resolved.some((item) => item.path === repo) ? ctx.key : '';
}

// 会話を起こすときに渡すもの（ほかのリポジトリとナレッジのホスト側パス）
function launchDirs(sess, cfg) {
  const ctx = context(sess && sess.project, cfg);
  if (!ctx) return [];
  const dirs = ctx.resolved.filter((repo) => repo.path && repo.path !== sess.repo).map((repo) => repo.path);
  if (ctx.kb !== sess.repo) dirs.push(ctx.kb);
  return [...new Set(dirs)].map((dir) => host.toHostPath(dir));
}

// 最初の依頼に添える節（プロジェクトの会話でなければ ''）
function promptBlock(sess, cfg) {
  const ctx = context(sess && sess.project, cfg);
  if (!ctx) return '';
  return projects.contextBlock({
    project: ctx.project, folder: ctx.folder, current: sess.repo, kbHost: host.toHostPath(ctx.kb),
    rules: projects.rulesText(ctx.kb, ctx.folder), branch: sess.worktree ? String(sess.branch || '') : '',
    resolved: ctx.resolved.map((repo) => ({ ...repo, hostPath: repo.path ? host.toHostPath(repo.path) : '' })),
  });
}

function defaultHostYaml() {
  const file = path.join(os.homedir(), '.agents', 'agent-project.host.yaml');
  return fs.existsSync(file) ? file : '';
}

function register(handle, { dialog, getWindow }) {
  handle('projects:list', () => {
    const cfg = store.loadConfig(userData());
    const items = projects.list(cfg.knowledgeRepos).map((item) => (item.project
      ? present(context(item.key, cfg) || item) : present(item)));
    return { items, knowledgeRepos: cfg.knowledgeRepos, lastProject: cfg.lastProject };
  });

  // 選んだとき・編集を開いたとき。フォルダが分からない分を origin から埋めてから返す
  handle('projects:open', async (p) => {
    const cfg = store.loadConfig(userData());
    const item = context(p.key, cfg);
    if (!item) throw new Error('プロジェクトが見つかりません（ナレッジリポジトリを確認してください）');
    if (await fillPaths(item)) return present(context(p.key));
    return present(item);
  });

  handle('projects:select', (p) => {
    const key = String(p.key || '');
    if (key && !context(key)) throw new Error('プロジェクトが見つかりません');
    return store.saveConfig(userData(), { lastProject: key });
  });

  handle('projects:knowledgeRepos', (p) => {
    const repos = (Array.isArray(p.repos) ? p.repos : []).map((repo) => requireRepo(repo));
    return store.saveConfig(userData(), { knowledgeRepos: repos });
  });

  // 保存。新しいプロジェクトならナレッジリポジトリにフォルダを作る。そのファイルだけをコミットする
  handle('projects:save', async (p) => {
    const project = projects.normalize(p.project);
    let kb;
    let folder;
    if (p.key) {
      const parts = projects.splitKey(p.key);
      if (!parts) throw new Error('プロジェクトが見つかりません');
      kb = requireRepo(parts.kb);
      folder = parts.folder;
    } else {
      kb = requireRepo(p.kb);
      folder = projects.folderName(project.name);
      if (fs.existsSync(projects.projectFile(kb, folder))) throw new Error(`同じ名前のプロジェクトがあります: ${folder}`);
    }
    const files = projects.write(kb, folder, project);
    const cfg = store.loadConfig(userData());
    const key = projects.keyOf(kb, folder);
    store.saveConfig(userData(), { knowledgeRepos: [...new Set([...cfg.knowledgeRepos, kb])], lastProject: key });
    const result = await commit(kb, files, `agent-app: プロジェクト「${project.name}」を${p.key ? '更新' : '作成'}`);
    return { key, ...result };
  });

  // 定義の URL に、この PC のフォルダを当てる（選んだフォルダは登録リポジトリにも足す）
  handle('projects:pickPath', async (p) => {
    const url = projects.normalizeUrl(p.url);
    if (!url) throw new Error('URL がありません');
    const res = await dialog.showOpenDialog(getWindow(), { properties: ['openDirectory'], title: `${projects.repoLabel(p.url)} のフォルダを選ぶ` });
    if (res.canceled || !res.filePaths.length) return null;
    const dir = res.filePaths[0];
    const cfg = store.loadConfig(userData());
    store.saveConfig(userData(), { repos: cfg.repos.includes(dir) ? cfg.repos : [...cfg.repos, dir], repoPaths: { ...cfg.repoPaths, [url]: dir } });
    return dir;
  });

  // 登録リポジトリの URL（編集画面でリポジトリを足すとき）
  handle('projects:remote', async (p) => {
    const repo = requireRepo(p.repo);
    const url = await remoteOf(repo);
    const cfg = store.loadConfig(userData());
    if (url) {
      store.saveConfig(userData(), { repoPaths: { ...cfg.repoPaths, [projects.normalizeUrl(url)]: repo } });
      return { url };
    }
    // 同じフォルダには同じ識別子を使い、共有する定義には端末のパスを含めない。
    const previous = Object.entries(cfg.repoPaths).find(([key, dir]) => key.startsWith('local:') && dir === repo);
    const localId = previous ? previous[0].slice(6) : randomUUID();
    store.saveConfig(userData(), { repoPaths: { ...cfg.repoPaths, [`local:${localId}`]: repo } });
    return { url: '', localId, label: path.basename(repo) };
  });

  handle('projects:choose', (p) => {
    const item = context(p.key);
    if (!item) return { repo: '', reason: '' };
    const chosen = projects.chooseRepo(item.resolved, p.text);
    return { repo: chosen.repo ? chosen.repo.path : '', reason: chosen.reason };
  });

  handle('projects:pull', async (p) => {
    const kb = requireRepo(p.kb);
    const r = await host.shellFor(distroFor(kb)).exec(['git', '-C', host.toHostPath(kb), 'pull', '--ff-only'], { timeoutMs: 60000 });
    if (!r.ok) throw new Error(`最新を取得できませんでした: ${r.output.trim().split('\n').pop() || 'git pull に失敗'}`);
    return { output: r.output.trim() };
  });

  // ホームのナレッジのカード。開いたときに 1 回だけ読む
  handle('projects:files', (p) => {
    const item = context(p.key);
    if (!item) throw new Error('プロジェクトが見つかりません');
    return projects.knowledgeFiles(item.kb, item.folder, 5);
  });

  handle('projects:workflows', (p) => {
    const item = context(p.key);
    if (!item) throw new Error('プロジェクトが見つかりません');
    return projectWorkflows.list(item).map(({ body, ...entry }) => entry);
  });

  handle('projects:openWorkflow', (p) => {
    const item = context(p.key);
    if (!item) throw new Error('プロジェクトが見つかりません');
    return projectWorkflows.ensure(item, p.id);
  });

  // ナレッジにファイルを足す（ドロップ・ファイル選択）。files/ へ写して、そのファイルだけコミットして push
  //   items … [{ name, size, write(dest) }]
  const addFiles = async (key, items) => {
    const item = context(key);
    if (!item) throw new Error('プロジェクトが見つかりません');
    if (!items.length) throw new Error('ファイルがありません');
    const big = items.find((file) => file.size > projects.MAX_FILE_BYTES);
    if (big) throw new Error(`${big.name} は 10 MB を超えています`);
    const written = [];
    for (const file of items) {
      const rel = projects.fileTarget(item.kb, item.folder, file.name);
      fs.mkdirSync(path.dirname(path.join(item.kb, rel)), { recursive: true });
      file.write(path.join(item.kb, rel));
      written.push(rel);
    }
    const names = written.map((rel) => path.posix.basename(rel)).join('、');
    return { written, ...(await commit(item.kb, written, `agent-app: ナレッジに ${names} を追加`)) };
  };
  // ドロップ: 画面から中身を受け取る（添付と同じ）
  handle('projects:addFile', (p) => {
    const data = Buffer.from(p.data || []);
    const name = path.basename(String(p.name || '')) || 'file';
    return addFiles(p.key, [{ name, size: data.length, write: (dest) => fs.writeFileSync(dest, data) }]);
  });
  handle('projects:pickFiles', async (p) => {
    const res = await dialog.showOpenDialog(getWindow(), { properties: ['openFile', 'multiSelections'], title: 'ナレッジに追加するファイルを選ぶ' });
    if (res.canceled || !res.filePaths.length) return null;
    return addFiles(p.key, res.filePaths.map((source) => ({
      name: path.basename(source), size: fs.statSync(source).size, write: (dest) => fs.copyFileSync(source, dest),
    })));
  });

  // 既存の会話を、選んでいるプロジェクトに入れる（そのリポジトリが入っているときだけ）
  handle('projects:assign', (p) => {
    const sess = store.readSession(userData(), p.id);
    const key = projectFor(sess.repo);
    if (!key) throw new Error('選んでいるプロジェクトに、この会話のリポジトリが入っていません');
    store.updateSession(userData(), sess.id, { project: key });
    return { project: key };
  });

  // 会話の中から「ナレッジに保存」: 送る本文を組むだけ（送るのは画面のいつもの送信）
  handle('projects:knowledgePrompt', (p) => {
    const sess = store.readSession(userData(), p.id);
    const item = context(sess.project);
    if (!item) throw new Error('プロジェクトの会話ではありません');
    return { prompt: projects.knowledgePrompt({ kbHost: host.toHostPath(item.kb), folder: item.folder, scope: p.scope, kind: p.kind }) };
  });

  // agent-project からの取り込み。まず中身を見せ（plan）、了承されたら書く（apply）
  handle('projects:importPlan', async () => {
    const res = await dialog.showOpenDialog(getWindow(), { properties: ['openDirectory'], title: 'agent-project の状態フォルダを選ぶ' });
    if (res.canceled || !res.filePaths.length) return null;
    const planned = projectImport.plan({ root: res.filePaths[0], hostYaml: defaultHostYaml() });
    const cfg = store.loadConfig(userData());
    return {
      root: planned.root, source: planned.source, name: planned.project.name, folder: planned.folder,
      repos: planned.project.repos.map((repo) => ({ ...repo, label: projects.repoLabel(repo.url), path: cfg.repoPaths[projects.normalizeUrl(repo.url)] || planned.repoPaths[projects.normalizeUrl(repo.url)] || '' })),
      items: planned.items, selected: planned.selected, excluded: planned.excluded,
    };
  });

  handle('projects:import', async (p) => {
    const kb = requireRepo(p.kb);
    const planned = projectImport.plan({ root: p.root, hostYaml: defaultHostYaml(), name: p.name });
    const done = projectImport.apply(kb, planned, p.selected);
    const cfg = store.loadConfig(userData());
    const local = Object.fromEntries(Object.entries(planned.repoPaths).filter(([, dir]) => {
      try { return fs.statSync(dir).isDirectory(); } catch { return false; }
    }));
    const key = projects.keyOf(kb, planned.folder);
    store.saveConfig(userData(), {
      repos: [...new Set([...cfg.repos, ...Object.values(local)])],
      repoPaths: { ...cfg.repoPaths, ...local },
      knowledgeRepos: [...new Set([...cfg.knowledgeRepos, kb])],
      lastProject: key,
    });
    const item = context(key);
    await fillPaths(item);
    const imported = projectWorkflows.register(context(key));
    const result = await commit(kb, done.written, `agent-app: agent-project「${planned.project.name}」を取り込み`);
    return { key, written: done.written.length, skipped: done.skipped, ...result,
      workflows: imported.registered.length, warning: [result.warning, ...imported.warnings].filter(Boolean).join('\n') };
  });
}

module.exports = { register, context, projectFor, launchDirs, promptBlock };
