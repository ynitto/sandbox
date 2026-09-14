"use strict";
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { spawn, execFile } = require('child_process');
const { promisify } = require('util');
const host = require('./host');
const store = require('./store');
const handoff = require('./sessionHandoff');
const routine = require('./automation/routine');
const reuse = require('../shared/reuse');
const exec = promisify(execFile);

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

class SessionBrowser {
  constructor({ userData, resourcesPath = process.resourcesPath, platform = process.platform, runWorker, getTargets, execFileFn = exec, spawnFn = spawn, env = process.env } = {}) {
    this.userData = userData;
    this.platform = platform; this.exec = execFileFn; this.spawn = spawnFn; this.env = env;
    const packaged = resourcesPath && path.join(resourcesPath, 'audit-runtime');
    this.runtime = packaged && fs.existsSync(packaged) ? packaged : path.resolve(__dirname, '../../../agent-audit');
    this.runWorker = runWorker || this.worker.bind(this);
    this.getTargets = getTargets || this.targets.bind(this);
    this.sources = new Map(); this.pages = new Map(); this.jobs = new Map(); this.prepared = new Map();
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
  worker(target, options, job) {
    const runtime = this.platform === 'win32' && !target.native ? host.toWslPath(this.runtime) : this.runtime;
    const script = 'import sys,runpy;sys.path.insert(0,' + JSON.stringify(runtime) + ');runpy.run_module("agent_audit.session_browser",run_name="__main__")';
    const command = 'exec python3 -c ' + host.sq(script);
    const argv = this.platform === 'win32' ? ['-d', target.distro, '-e', 'bash', '-lc', command] : ['-lc', command];
    return new Promise((resolve, reject) => {
      if (job.cancelled) return reject(new Error('検索を中止しました'));
      const child = this.spawn(target.native ? 'python' : this.platform === 'win32' ? 'wsl.exe' : '/bin/bash', target.native ? ['-c', script] : argv, { windowsHide: true });
      job.children.add(child);
      let output = '', error = '', exceeded = false;
      const timer = setTimeout(() => { exceeded = true; child.kill(); }, 60000);
      child.stdout.on('data', bytes => { output += bytes.toString(); if (output.length > 32 * 1024 * 1024) { exceeded = true; child.kill(); } });
      child.stderr.on('data', bytes => { error = (error + bytes.toString()).slice(-2000); });
      child.on('error', reject);
      child.on('close', code => {
        clearTimeout(timer); job.children.delete(child);
        if (job.cancelled) return reject(new Error('検索を中止しました'));
        if (exceeded) return reject(new Error('履歴の取得が上限に達しました。条件を絞るか会話を取り込んでください'));
        if (code) return reject(new Error('履歴を取得できません。Python 3 の利用環境を確認してください。' + error));
        try { const value = JSON.parse(output); if (!value.ok) throw new Error(value.error); resolve(value.data); } catch (err) { reject(err); }
      });
      child.stdin.on('error', () => {});
      child.stdin.end(JSON.stringify({ ...target.options, ...options }));
    });
  }
  saveSources() {
    this.imports = [...new Set(this.imports)].slice(-100);
    this.codeRoots = [...new Set(this.codeRoots)].slice(-100);
    store.saveConfig(this.userData(), { sessionSearch: { imports: this.imports, codeRoots: this.codeRoots } });
  }
  cancel(id) {
    const job = this.jobs.get(id);
    if (job) { job.cancelled = true; for (const child of job.children) child.kill(); }
  }
  async search(query = {}, requestId = crypto.randomUUID(), cursor = '') {
    const job = { children: new Set(), cancelled: false }; this.jobs.set(requestId, job);
    try {
      let id, page, index = 0;
      if (cursor) {
        const parts = cursor.split(':'); id = parts[0]; index = Number(parts[1]);
        page = this.pages.get(id);
        if (!page || page.query !== key(query) || parts.length !== 2 || !Number.isInteger(index) || index < 0 || index > page.results.length) throw new Error('検索結果を更新してください');
        if (page.results[index]) return page.results[index];
        if (page.busy) throw new Error('検索中です');
      } else {
        id = crypto.randomUUID();
        const candidates = [], errors = []; let partial = false;
        if (!query.source || query.source === 'app') {
          const dir = store.sessionsDir(this.userData());
          if (fs.existsSync(dir)) for (const file of fs.readdirSync(dir)) {
            if (!file.endsWith('.json')) continue;
            try { candidates.push({ appId: file.slice(0, -5), updatedAt: fs.statSync(path.join(dir, file)).mtimeMs / 1000 }); } catch { /* removed while listing */ }
          }
        }
        if (query.source !== 'app') {
          let targets = [];
          try { targets = await this.getTargets(); } catch { errors.push({ message: '一部の履歴保存先に接続できません' }); }
          const results = await Promise.allSettled(targets.map(async (target, i) => {
            const convert = p => this.platform === 'win32' && !target.native ? host.toWslPath(p) : p;
            const result = await this.runWorker(target, { mode: 'inventory', query, imports: i ? [] : this.imports.map(convert), codeRoots: i ? [] : this.codeRoots.map(convert) }, job);
            return { target, result };
          }));
          for (const entry of results) {
            if (entry.status === 'rejected') { errors.push({ message: entry.reason.message }); continue; }
            const { target, result } = entry.value;
            errors.push(...result.errors); partial ||= result.partial;
            for (const descriptor of result.descriptors) candidates.push({ target, descriptor, updatedAt: descriptor.updatedAt || 0 });
          }
        }
        candidates.sort((a, b) => b.updatedAt - a.updatedAt);
        page = { query: key(query), candidates, offset: 0, seen: new Set(), results: [], errors, partial, count: 0 };
        this.pages.set(id, page);
        while (this.pages.size > 5) this.pages.delete(this.pages.keys().next().value);
      }
      page.busy = true;
      try {
        // Inspect at most 200 candidates, and return at most 50 matches per request.
        // Snapshot state is committed only after success, so cancelled pages can be retried.
        let offset = page.offset, inspected = 0;
        const records = [], seen = new Set(page.seen), errors = [...page.errors];
        while (offset < page.candidates.length && records.length < 50 && inspected < 200) {
          const batch = page.candidates.slice(offset, offset + Math.min(50 - records.length, 200 - inspected));
          offset += batch.length; inspected += batch.length;
          const groups = new Map(), found = [];
          for (const candidate of batch) {
            if (candidate.appId) {
              try {
                const session = store.readSession(this.userData(), candidate.appId);
                if (session.kind === 'conversation') { const r = appRecord(session); if (matches(r, query)) found.push(r); }
              } catch { errors.push({ message: '会話を読み取れません' }); }
            } else {
              const { target, descriptor } = candidate;
              if (!groups.has(target.id)) groups.set(target.id, { target, descriptors: [] });
              groups.get(target.id).descriptors.push(descriptor);
            }
          }
          const results = await Promise.allSettled([...groups.values()].map(async ({ target, descriptors }) => ({ target,
            result: await this.runWorker(target, { mode: 'search', query, descriptors }, job) })));
          for (const entry of results) {
            if (entry.status === 'rejected') { errors.push({ message: entry.reason.message }); continue; }
            const { target, result } = entry.value;
            errors.push(...result.errors);
            for (const r of result.sessions) {
              const sourceKey = key([target.id, r.descriptor.path, r.nativeId]);
              this.sources.set(sourceKey, { target, descriptor: r.descriptor });
              found.push({ ...r, key: sourceKey });
            }
          }
          if (job.cancelled) throw new Error('検索を中止しました');
          for (const r of found) {
            const identity = r.nativeId ? key([r.agent, r.nativeId, host.toWslPath(r.repo)]) : r.key;
            if (seen.has(identity)) continue;
            seen.add(identity);
            const { messages, descriptor, ...meta } = r;
            const needle = String(query.text || '').toLocaleLowerCase();
            const body = messages?.find(m => m.text.toLocaleLowerCase().includes(needle))?.text || '';
            const start = Math.max(0, body.toLocaleLowerCase().indexOf(needle) - 50);
            records.push({ ...meta, snippet: r.snippet || body.slice(start, start + 180) });
          }
        }
        if (job.cancelled) throw new Error('検索を中止しました');
        records.sort((a, b) => b.updatedAt - a.updatedAt || a.key.localeCompare(b.key));
        const more = offset < page.candidates.length;
        const result = { sessions: records, total: page.count + records.length, totalExact: !more, page: index + 1,
          errors: errors.slice(0, 20), partial: page.partial, cursor: more ? id + ':' + (index + 1) : '', previous: index ? id + ':' + (index - 1) : '' };
        page.offset = offset; page.seen = seen; page.count = result.total; page.errors = result.errors; page.results.push(result);
        return result;
      } finally { page.busy = false; }
    } finally { this.jobs.delete(requestId); }
  }
  async read(id) {
    if (id.startsWith('app:')) return appRecord(store.readSession(this.userData(), id.slice(4)));
    const source = this.sources.get(id);
    if (!source) throw new Error('検索して会話を選び直してください');
    const record = await this.runWorker(source.target, { mode: 'read', descriptor: source.descriptor }, { children: new Set(), cancelled: false });
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
    let prompt = `元の会話: ${r.title}\n元の作業フォルダ: ${r.repo || '不明'}\n保存先: ${plan.repo}\n会話の文脈を新規セッションへ引き継ぎます。ファイルの変更やツールの実行状態は複製されていません。参照パスは保存先で確認してください。\n\n${handoff.handoffPrompt(summary.trim())}\n\n${request.trim() ? '今回の依頼（こちらを実行してください）:\n' + request.trim() : ''}`;
    if (plan.method) {
      prompt = reuse.creationPrompt({ kind: plan.method.kind, purpose: plan.method.purpose, repo: plan.repo, originRepo: r.repo });
      if (plan.method.kind !== 'skill') return { method: { ...plan.method, purpose: prompt }, repo: plan.repo,
        options: { policy: 'direct', cli: plan.cli, model: plan.model, readonly: permission === 'ask', autoApprove: permission === 'auto' } };
    }
    const session = store.createSession(this.userData(), { repo: plan.repo, cli: plan.cli, model: plan.model,
      readonly: permission === 'ask', autoApprove: permission === 'auto', transport,
      origin: r.appId ? { sessionId: r.appId, repo: r.repo, index: Number(r.boundary) } : null,
      externalOrigin: r.appId ? null : { key: r.key, provider: r.provider, nativeId: r.nativeId, repo: r.repo, title: r.title, boundary: r.boundary, revision: r.revision, capturedAt: new Date().toISOString(), mode: plan.mode } });
    store.updateSession(this.userData(), session.id, { title: `${r.title}（フォーク）` });
    plan.createdId = session.id; plan.prompt = prompt;
    return { session: store.readSession(this.userData(), session.id), prompt };
  }
}
module.exports = { SessionBrowser, takeBoundary, matches, appRecord };
