"use strict";
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { spawn, execFile } = require('child_process');
const { promisify } = require('util');
const host = require('./host');
const store = require('./store');
const worktree = require('./worktree');
const handoff = require('./sessionHandoff');
const routine = require('./automation/routine');
const reuse = require('../shared/reuse');
const { SharedSessionSearch } = require('./sharedSessionSearch');
const { SessionWorker, WorkerGroup, runtimeDir } = require('./sessionWorker');
const { SessionIndex, comparablePath } = require('./sessionIndex');
const exec = promisify(execFile);

// 並び順を保つ単位。窓が終わるたびに、その窓の一致を新しい順で画面へ流す。
const WINDOW = 500;
// ワーカーへ一度に渡す候補の数。索引へ入れるときは本文が載るので小さくする。
const BATCH = { stream: 200, index: 50 };
// 1 回の検索で画面へ流す上限。これを超えたら条件を絞ってもらう。
const RESULT_LIMIT = 2000;

function key(value) { return crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex'); }
function matches(s, q) {
  const includes = (a, b) => !b || String(a || '').toLocaleLowerCase().includes(String(b).toLocaleLowerCase());
  const date = q.dateField === 'created' ? s.createdAt : s.updatedAt;
  return (!q.agent || s.agent === q.agent) && (!q.source || s.source === q.source)
    && includes(host.toWslPath(s.repo), host.toWslPath(q.repo)) && includes(s.model, q.model)
    && (!q.since || date >= q.since) && (!q.until || date < q.until)
    && (q.archived || !s.archived)
    && (!q.text || includes(s.title, q.text) || s.messages.some(m => includes(m.text, q.text)));
}
function appRecord(s) {
  const messages = s.messages.map((m, i) => ({ ...m, id: String(i), complete: !m.error && !m.stopped })).filter(m => ['user', 'assistant'].includes(m.role));
  return { key: 'app:' + s.id, appId: s.id, provider: s.cli, source: 'app', agent: s.cli, repo: s.repo,
    defaults: { permission: s.readonly ? 'ask' : s.autoApprove ? 'auto' : 'confirm',
      worktree: s.worktree || '', branch: s.branch || '', transport: s.transport },
    nativeId: s.cliSessions?.[s.cli]?.id || '', model: s.model || '', title: s.title || messages[0]?.text?.slice(0, 120) || '無題',
    createdAt: Date.parse(s.createdAt) / 1000, updatedAt: Date.parse(s.updatedAt) / 1000,
    revision: key(messages), messages, archived: !!s.supersededBy, partial: false };
}
function takeBoundary(record, boundary) {
  if (record.partial) throw new Error('会話の一部を読み取れません。元のアプリからエクスポートして取り込んでください');
  let end = boundary == null ? record.messages.findLastIndex(m => m.role === 'assistant' && m.complete !== false)
    : record.messages.findIndex(m => m.id === boundary);
  if (end < 0 || record.messages[end].role !== 'assistant' || record.messages[end].complete === false) throw new Error('完了した応答を選んでください');
  return { ...record, messages: record.messages.slice(0, end + 1), boundary: record.messages[end].id };
}

const fold = value => String(value || '').toLocaleLowerCase();
function snippetOf(body, needle) {
  const text = String(body || '');
  if (!needle) return text.slice(0, 180);
  const at = fold(text).indexOf(fold(needle));
  const start = Math.max(0, (at < 0 ? 0 : at) - 50);
  return text.slice(start, start + 180);
}
// 索引の行・索引へ入れる記録に共通の判定。python 側 inspect() と同じ意味にする。
function matchesRecord(record, query) {
  const date = query.dateField === 'created' ? record.createdAt : record.updatedAt;
  const needle = fold(query.text);
  return (!query.agent || query.agent === (record.provider === 'vscode' ? 'copilot' : record.provider))
    && (!query.source || query.source === (record.provider === 'vscode' ? 'vscode' : 'cli'))
    && (query.archived || !record.archived)
    && (!query.since || date >= query.since) && (!query.until || date < query.until)
    && (!query.repo || comparablePath(record.repo).includes(comparablePath(query.repo)))
    && (!query.model || fold(record.model).includes(fold(query.model)))
    && (!needle || fold(record.title).includes(needle) || fold(record.body).includes(needle));
}
function rowOf(record, sourceKey, needle) {
  return { key: sourceKey, provider: record.provider, source: record.provider === 'vscode' ? 'vscode' : 'cli',
    agent: record.provider === 'vscode' ? 'copilot' : record.provider, nativeId: record.nativeId || '',
    repo: record.repo || '', model: record.model || '', title: record.title || '',
    createdAt: record.createdAt || 0, updatedAt: record.updatedAt || 0, archived: !!record.archived,
    partial: !!record.partial, count: record.count || 0,
    snippet: record.snippet != null ? record.snippet : snippetOf(record.body, needle) };
}

class SessionBrowser {
  constructor({ userData, resourcesPath = process.resourcesPath, platform = process.platform, startWorker, getTargets, share, execFileFn = exec, spawnFn = spawn, env = process.env, index } = {}) {
    this.userData = userData;
    this.share = share;
    this.sharedSearch = share ? new SharedSessionSearch(this, share) : null;
    this.platform = platform; this.exec = execFileFn; this.spawn = spawnFn; this.env = env;
    this.runtime = runtimeDir(resourcesPath);
    this.startWorker = startWorker || this.launch.bind(this);
    this.getTargets = getTargets || this.targets.bind(this);
    this.index = index === undefined ? null : index; this.indexOpened = index !== undefined;
    this.sources = new Map(); this.jobs = new Map(); this.prepared = new Map();
    const saved = store.loadConfig(this.userData()).sessionSearch || {};
    this.imports = Array.isArray(saved.imports) ? saved.imports.filter(p => typeof p === 'string').slice(0, 100) : [];
    this.codeRoots = Array.isArray(saved.codeRoots) ? saved.codeRoots.filter(p => typeof p === 'string').slice(0, 100) : [];
  }
  async targets() {
    if (this.platform !== 'win32') return [{ id: 'local', distro: '', options: {} }];
    let stdout;
    try { ({ stdout } = await this.exec('wsl.exe', ['--list', '--quiet'], { encoding: 'buffer', timeout: 15000, windowsHide: true })); }
    catch { return [{ id: 'native', native: true, options: { home: this.env.USERPROFILE, appData: this.env.APPDATA ? [this.env.APPDATA] : [] } }]; }
    const names = stdout.toString('utf16le').replace(/\0/g, '').split(/\r?\n/).map(x => x.trim()).filter(Boolean);
    if (!names.length) return [{ id: 'native', native: true, options: { home: this.env.USERPROFILE, appData: this.env.APPDATA ? [this.env.APPDATA] : [] } }];
    return names.map((distro, i) => ({ id: distro, distro, options: i ? {} : {
      extraHomes: this.env.USERPROFILE ? [host.toWslPath(this.env.USERPROFILE)] : [],
      appData: this.env.APPDATA ? [host.toWslPath(this.env.APPDATA)] : [],
    } }));
  }
  // 検索 1 回につき保存先ごとに 1 つだけ起こす。常駐はさせない（WorkerGroup が閉じる）。
  launch(target) {
    return new SessionWorker({ target, runtime: this.runtime, platform: this.platform, spawnFn: this.spawn });
  }
  openIndex() {
    if (!this.indexOpened) {
      this.indexOpened = true;
      this.index = SessionIndex.open(path.join(this.userData(), 'session-index.db'));
    }
    return this.index;
  }
  saveSources() {
    this.imports = [...new Set(this.imports)].slice(-100);
    this.codeRoots = [...new Set(this.codeRoots)].slice(-100);
    store.saveConfig(this.userData(), { sessionSearch: { imports: this.imports, codeRoots: this.codeRoots } });
  }
  cancel(id) {
    this.sharedSearch?.cancel(id);
    const job = this.jobs.get(id);
    // 待っている途中でも即座に打ち切る。ワーカーの反応は待たない。
    if (job) { job.cancelled = true; job.stop(new Error('検索を中止しました')); job.controller.abort(); job.group?.kill(); }
  }
  async once(target, payload) {
    const worker = this.startWorker(target);
    try { return await worker.request(payload); } finally { worker.close(); }
  }
  // 候補を集める（本文は読まない）。アプリ内の会話と、保存先ごとの一覧。
  async collect(query, group, state) {
    const app = [], external = new Map();
    if (!query.source || query.source === 'app') {
      const dir = store.sessionsDir(this.userData());
      if (fs.existsSync(dir)) for (const file of fs.readdirSync(dir)) {
        if (!file.endsWith('.json')) continue;
        try { app.push({ appId: file.slice(0, -5), updatedAt: fs.statSync(path.join(dir, file)).mtimeMs / 1000 }); }
        catch { /* removed while listing */ }
      }
    }
    if (query.source === 'app') return { app, external, targets: [], pool: app.length };
    let targets = [];
    try { targets = await this.getTargets(); } catch { state.errors.push({ message: '一部の履歴保存先に接続できません' }); }
    const results = await Promise.allSettled(targets.map(async (target, i) => {
      const convert = value => this.platform === 'win32' && !target.native ? host.toWslPath(value) : value;
      const result = await group.get(target).request({ mode: 'inventory', ...target.options, query,
        imports: i ? [] : this.imports.map(convert), codeRoots: i ? [] : this.codeRoots.map(convert) });
      return { target, result };
    }));
    for (const entry of results) {
      if (entry.status === 'rejected') { state.errors.push({ message: entry.reason.message }); continue; }
      const { target, result } = entry.value;
      state.errors.push(...result.errors); state.partial ||= result.partial;
      external.set(target.id, { target, descriptors: result.descriptors });
    }
    return { app, external, targets, pool: app.length + [...external.values()].reduce((n, e) => n + e.descriptors.length, 0) };
  }
  // 索引で即答できる分と、走査が要る分に分ける。
  divide(query, app, external, index) {
    const live = app.map(entry => ({ ...entry, updatedAt: entry.updatedAt || 0 }));
    const ready = [];
    for (const { target, descriptors } of external.values()) {
      if (!index) {
        for (const descriptor of descriptors) live.push({ target, descriptor, mode: 'stream', updatedAt: descriptor.updatedAt || 0 });
        continue;
      }
      const plan = index.plan(target.id, descriptors);
      for (const descriptor of plan.stale) live.push({ target, descriptor, mode: 'index', updatedAt: descriptor.updatedAt || 0 });
      // 本文が上限で切れている会話は索引だけでは判断できないので、キーワード検索のときは走査する。
      if (query.text) for (const descriptor of plan.truncated) live.push({ target, descriptor, mode: 'stream', updatedAt: descriptor.updatedAt || 0 });
    }
    if (index && external.size) {
      const skip = new Set(live.filter(entry => entry.descriptor)
        .map(entry => entry.target.id + '\u0000' + entry.descriptor.path + '\u0000' + (entry.descriptor.nativeId || '')));
      for (const row of index.find(query, [...external.keys()])) {
        if (skip.has(row.target + '\u0000' + row.path + '\u0000' + (row.descriptorId || ''))) continue;
        const target = external.get(row.target)?.target;
        if (!target) continue;
        ready.push(this.register(target, row, query));
      }
    }
    live.sort((a, b) => b.updatedAt - a.updatedAt);
    return { live, ready };
  }
  // 一覧の 1 行にして、あとで本文を取りに行けるよう保存先を覚えておく。
  register(target, record, query) {
    const descriptor = { path: record.path, provider: record.provider,
      nativeId: record.descriptorId != null ? record.descriptorId : record.nativeId || '' };
    const sourceKey = key([target.id, record.path, record.nativeId || '']);
    this.sources.set(sourceKey, { target, descriptor });
    return rowOf(record, sourceKey, query.text);
  }
  // 窓 1 つ分を調べる。索引が使えるときは本文ごと受け取って索引へ入れる。
  // 調べた件数はその場で数えて tick へ渡す（進みが止まって見えないように）。
  async inspect(window, query, group, index, state, tick = () => {}) {
    const hits = [];
    const groups = new Map();
    for (const candidate of window) {
      if (candidate.appId) {
        try {
          const session = store.readSession(this.userData(), candidate.appId);
          if (session.kind !== 'conversation') continue;
          const record = appRecord(session);
          if (!matches(record, query)) continue;
          const body = record.messages.find(m => fold(m.text).includes(fold(query.text)))?.text || record.messages[0]?.text || '';
          const { messages, ...meta } = record;
          hits.push({ ...meta, count: messages.length, snippet: snippetOf(body, query.text) });
        } catch { state.errors.push({ message: '会話を読み取れません' }); }
        finally { state.scanned++; tick(); }
        continue;
      }
      const id = candidate.target.id + ':' + candidate.mode;
      if (!groups.has(id)) groups.set(id, { target: candidate.target, mode: candidate.mode, descriptors: [] });
      groups.get(id).descriptors.push(candidate.descriptor);
    }
    const results = await Promise.allSettled([...groups.values()].map(async ({ target, mode, descriptors }) => {
      const worker = group.get(target);
      for (let offset = 0; offset < descriptors.length; offset += BATCH[mode]) {
        const slice = descriptors.slice(offset, offset + BATCH[mode]);
        let counted = 0;
        const step = value => { state.scanned += value - counted; counted = value; tick(); };
        if (mode === 'index') {
          const records = [];
          const result = await worker.request({ mode: 'index', descriptors: slice }, message => {
            if (message.record) records.push(message.record);
            if (message.record || message.skip) step(counted + 1);
          });
          state.errors.push(...result.errors);
          index.put(target.id, records);
          for (const record of records) if (matchesRecord(record, query)) hits.push(this.register(target, record, query));
        } else {
          const result = await worker.request({ mode: 'stream', query, descriptors: slice }, message => {
            if (message.hit) hits.push(this.register(target, { ...message.hit, path: message.hit.descriptor.path }, query));
            if (message.progress) step(message.progress.scanned);
          });
          state.errors.push(...result.errors); state.partial ||= result.partial;
        }
        step(slice.length);
      }
    }));
    for (const entry of results) if (entry.status === 'rejected') state.errors.push({ message: entry.reason.message });
    return hits;
  }
  /**
   * 会話を検索し、見つかった端から emit へ渡す。ページも「次へ」も無い。
   * emit({ hit }) 一致した会話、emit({ progress }) 走査の進み。打ち止めは戻り値で返す。
   * 走査した会話はそのまま索引へ入るので、次の検索は解析なしで返せる。
   */
  async search(query = {}, requestId = crypto.randomUUID(), emit = () => {}) {
    this.cancel(requestId);
    const job = { cancelled: false, controller: new AbortController(), group: null };
    job.stopped = new Promise((_resolve, reject) => { job.stop = reject; });
    job.stopped.catch(() => { /* 中止は search 側で投げ直す */ });
    this.jobs.set(requestId, job);
    const state = { scanned: 0, matched: 0, errors: [], partial: false, seen: new Set(), capped: false };
    const guard = () => { if (job.cancelled) throw new Error('検索を中止しました'); };
    const send = (kind, payload) => { if (!job.cancelled) emit({ [kind]: payload }); };
    const group = job.group = new WorkerGroup(target => this.startWorker(target, job));
    try {
      const index = this.openIndex();
      const { app, external, pool } = await Promise.race([this.collect(query, group, state), job.stopped]);
      guard();
      const { live, ready } = this.divide(query, app, external, index);
      const peers = [];
      const peerJob = query.shared && this.sharedSearch
        ? this.sharedSearch.collect(query, requestId, sessions => peers.push(...sessions), job.controller.signal)
          .then(errors => { state.errors.push(...errors); })
        : null;
      let taken = 0;
      // 新しい順のまま下へ伸ばす。まだ調べていない位置（watermark）より新しいものだけ先に出す。
      const flush = (hits, watermark) => {
        const rows = [...hits];
        while (taken < ready.length && (watermark == null || ready[taken].updatedAt >= watermark)) rows.push(ready[taken++]);
        if (peers.length) {
          const keep = [];
          for (const session of peers) (watermark == null || session.updatedAt >= watermark ? rows : keep).push(session);
          peers.length = 0; peers.push(...keep);
        }
        const fresh = [];
        for (const row of rows) {
          const identity = row.nativeId ? key([row.agent, row.nativeId, host.toWslPath(row.repo)]) : row.key;
          if (state.seen.has(identity)) continue;
          state.seen.add(identity);
          if (state.matched + fresh.length >= RESULT_LIMIT) { state.capped = true; break; }
          fresh.push(row);
        }
        if (!fresh.length) return;
        fresh.sort((a, b) => b.updatedAt - a.updatedAt || String(a.key).localeCompare(String(b.key)));
        state.matched += fresh.length;
        send('hit', { sessions: fresh });
      };
      const total = live.length;
      let ticked = 0;
      const progress = () => send('progress', { scanned: state.scanned, total, pool, matched: state.matched });
      const tick = () => { const now = Date.now(); if (now - ticked < 200) return; ticked = now; progress(); };
      for (let offset = 0; offset < live.length && !state.capped; offset += WINDOW) {
        guard();
        const window = live.slice(offset, offset + WINDOW);
        const hits = await Promise.race([this.inspect(window, query, group, index, state, tick), job.stopped]);
        guard();
        flush(hits, window[window.length - 1].updatedAt);
        progress();
      }
      if (peerJob) await Promise.race([peerJob, job.stopped]);
      guard();
      flush([], null);
      if (index) index.prune();
      return { scanned: state.scanned, total, pool, matched: state.matched, errors: state.errors.slice(0, 20),
        partial: state.partial, capped: state.capped, indexed: !!index && !live.some(entry => entry.mode === 'index') };
    } finally { this.jobs.delete(requestId); group.close(); }
  }
  async read(id) {
    if (id.startsWith('public:') && this.share) return this.share.readPublic(id);
    if (id.startsWith('app:')) return appRecord(store.readSession(this.userData(), id.slice(4)));
    const source = this.sources.get(id);
    if (!source) throw new Error('検索して会話を選び直してください');
    const record = await this.once(source.target, { mode: 'read', descriptor: source.descriptor });
    const { descriptor, ...view } = record;
    return { ...view, key: id };
  }
  async prepare({ key: id, revision, boundary, repo, cli, model, mode, intent = 'session', kind = 'auto', request = '' }, generate) {
    const record = await this.read(id);
    if (record.revision !== revision) throw new Error('会話が更新されました。プレビューを開き直してください');
    if (!['session', 'routine'].includes(intent)) throw new Error('フォーク先を選んでください');
    if (!routine.KINDS.includes(kind) && kind !== 'auto') throw new Error('定型化の種類を選んでください');
    const selected = takeBoundary(record, boundary);
    const summary = await handoff.summarize(selected, generate);
    // 種類を利用者が選んだときは判定を任せず、その種類の手順としてまとめさせる。
    const fixed = kind === 'auto' ? null : kind;
    const method = intent === 'routine' ? routine.parse(await generate(routine.prompt(summary + (request ? '\n今回の追加要望:\n' + String(request).slice(0, 30000) : ''), fixed)), fixed) : null;
    const token = crypto.randomUUID();
    this.prepared.set(token, { record: selected, repo, cli, model, mode, intent, method, createdId: null });
    while (this.prepared.size > 10) this.prepared.delete(this.prepared.keys().next().value);
    return { token, summary, method, boundary: selected.boundary };
  }
  create({ token, summary, request = '', permission = 'confirm', transport = 'headless' }) {
    const plan = this.prepared.get(token);
    if (!plan) throw new Error('引き継ぎ内容を作り直してください');
    if (plan.createdId) return { session: store.readSession(this.userData(), plan.createdId), prompt: plan.prompt };
    if (typeof summary !== 'string' || !summary.trim() || summary.length > 30000 || typeof request !== 'string' || request.length > 30000) throw new Error('引き継ぎ内容を確認してください');
    const r = plan.record;
    const sameRepo = r.appId && r.repo === plan.repo;
    const sourceDir = r.appId ? worktree.dirsFor(r.repo, r.defaults?.worktree || '').fsDir : r.repo;
    const targetDir = sameRepo ? sourceDir : plan.repo;
    let prompt = `元の会話: ${r.title}\n元の作業フォルダ: ${sourceDir || '不明'}\n保存先: ${targetDir}\n会話の文脈を新規セッションへ引き継ぎます。ファイルの変更やツールの実行状態は複製されていません。参照パスは保存先で確認してください。\n\n${handoff.handoffPrompt(summary.trim())}\n\n${request.trim() ? '今回の依頼（こちらを実行してください）:\n' + request.trim() : ''}`;
    if (plan.method) {
      prompt = reuse.creationPrompt({ kind: plan.method.kind, purpose: plan.method.purpose, repo: plan.repo, originRepo: r.repo });
      if (plan.method.kind !== 'skill') return { method: { ...plan.method, purpose: prompt }, repo: plan.repo,
        options: { policy: 'direct', cli: plan.cli, model: plan.model, readonly: permission === 'ask', autoApprove: permission === 'auto' } };
    }
    const session = store.createSession(this.userData(), { repo: plan.repo, cli: plan.cli, model: plan.model,
      worktree: sameRepo ? r.defaults?.worktree || '' : '', branch: sameRepo ? r.defaults?.branch || '' : '',
      readonly: permission === 'ask', autoApprove: permission === 'auto', transport,
      origin: r.appId ? { sessionId: r.appId, repo: r.repo, index: Number(r.boundary) } : null,
      externalOrigin: r.appId ? null : { key: r.key, provider: r.provider, nativeId: r.nativeId, repo: r.repo, title: r.title, boundary: r.boundary, revision: r.revision, capturedAt: new Date().toISOString(), mode: plan.mode } });
    store.updateSession(this.userData(), session.id, { title: `${r.title}（フォーク）` });
    plan.createdId = session.id; plan.prompt = prompt;
    return { session: store.readSession(this.userData(), session.id), prompt };
  }
}
module.exports = { SessionBrowser, takeBoundary, matches, appRecord };
