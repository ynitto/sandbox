'use strict';
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const store = require('../store');
const { appRecord, matches } = require('../sessionBrowser');
const { MAX_TALK, KEEP_TALK } = require('./requester');

const MAX_READ = 16 * 1024 * 1024;
// Only explicitly published IDs are considered. No remote request can name a file.
class Publications {
  constructor({ userData, node, changed = () => {}, screen = async () => '' }) {
    this.userData = userData; this.node = node; this.changed = changed; this.screen = screen;
    this.file = path.join(userData, 'share', 'publications.json');
    this.pages = new Map(); this.cache = new Map(); this.screens = new Map();
    try { this.entries = new Map(JSON.parse(fs.readFileSync(this.file, 'utf8')).map(e => [e.id, e])); }
    catch (err) { if (err.code !== 'ENOENT') throw err; this.entries = new Map(); }
  }
  save() {
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    const temp = this.file + '.tmp';
    fs.writeFileSync(temp, JSON.stringify([...this.entries.values()])); fs.renameSync(temp, this.file);
    this.changed();
  }
  publish(sessionId) {
    const s = store.readSession(this.userData, sessionId);
    if (s.kind !== 'conversation') throw new Error('会話のセッションを選んでください');
    let entry = [...this.entries.values()].find(e => e.sessionId === sessionId);
    if (!entry) {
      entry = { id: crypto.randomUUID(), sessionId, title: s.title || '無題', cli: s.cli, publishedAt: new Date().toISOString(), talk: [] };
      this.entries.set(entry.id, entry); this.save();
    }
    return this.meta(entry);
  }
  stop(id) { const changed = this.entries.delete(id); this.cache.delete(id); if (changed) this.save(); return changed; }
  meta(e) { return { id: e.id, sessionId: e.sessionId, title: e.title, cli: e.cli, publishedAt: e.publishedAt, owner: this.node }; }
  list() { return [...this.entries.values()].map(e => this.meta(e)); }
  require(id) { const e = this.entries.get(id); if (!e) throw new Error('公開が停止されたか、セッションがありません'); return e; }
  async read(id) {
    const entry = this.require(id);
    const file = path.join(store.sessionsDir(this.userData), entry.sessionId + '.json');
    const stat = await fs.promises.stat(file);
    if (stat.size > MAX_READ) throw new Error('会話が取得上限を超えています');
    this.require(id);
    const stamp = `${stat.mtimeMs}:${stat.size}`;
    const cached = this.cache.get(id);
    if (cached?.stamp === stamp) return cached.record;
    const s = store.normalizeSession(JSON.parse(await fs.promises.readFile(file, 'utf8')));
    this.require(id);
    const r = appRecord(s);
    // Do not transmit attachments, local paths from tool output metadata, CLI state or credentials.
    const messages = r.messages.map(m => ({ id: m.id, role: m.role, text: String(m.text || ''), complete: m.complete }));
    const record = { key: `public:${this.node}:${id}`, publicationId: id, owner: this.node, provider: r.provider,
      source: 'app', agent: r.agent, repo: r.repo, nativeId: '', model: r.model, title: r.title,
      createdAt: r.createdAt, updatedAt: r.updatedAt, revision: r.revision, archived: r.archived, partial: false, messages };
    this.cache.set(id, { stamp, record, bytes: stat.size });
    let bytes = [...this.cache.values()].reduce((sum, v) => sum + v.bytes, 0);
    while (this.cache.size > 50 || bytes > MAX_READ * 2) { const first = this.cache.keys().next().value; bytes -= this.cache.get(first).bytes; this.cache.delete(first); }
    return record;
  }
  async search(query = {}, cursor = '') {
    let snapshot, index = 0, token;
    if (cursor) {
      const parts = String(cursor).split(':'); token = parts[0]; index = Number(parts[1]); snapshot = this.pages.get(token);
      if (parts.length !== 2 || !snapshot || Date.now() - snapshot.at > 300000 || snapshot.query !== JSON.stringify(query)
        || !Number.isInteger(index) || index < 0 || index > snapshot.ids.length) throw new Error('共有の検索結果を更新してください');
    } else {
      token = crypto.randomUUID(); snapshot = { ids: [...this.entries.keys()].reverse(), query: JSON.stringify(query), at: Date.now() };
      this.pages.set(token, snapshot); while (this.pages.size > 20) this.pages.delete(this.pages.keys().next().value);
    }
    const sessions = [], errors = []; let inspected = 0, scannedChars = 0;
    while (index < snapshot.ids.length && inspected++ < 200 && scannedChars < 8 * 1024 * 1024 && sessions.length < 50) {
      const id = snapshot.ids[index++];
      if (!this.entries.has(id)) continue;
      try {
        const record = await this.read(id);
        scannedChars += record.messages.reduce((sum, m) => sum + m.text.length, 0);
        if (!matches(record, query)) continue;
        const { messages, ...meta } = record;
        const needle = String(query.text || '').toLocaleLowerCase();
        const text = messages.find(m => m.text.toLocaleLowerCase().includes(needle))?.text || '';
        const start = Math.max(0, text.toLocaleLowerCase().indexOf(needle) - 50);
        sessions.push({ ...meta, snippet: text.slice(start, start + 180) });
      } catch (err) { errors.push({ provider: this.node, message: err.message }); }
    }
    return { sessions: sessions.filter(r => this.entries.has(r.publicationId)), errors: errors.slice(0, 20), cursor: index < snapshot.ids.length ? `${token}:${index}` : '' };
  }
  async view(id, revision = '') {
    const record = await this.read(id);
    let cached = this.screens.get(id);
    if (!cached || Date.now() - cached.at > 1000) {
      cached = { at: Date.now(), promise: Promise.resolve(this.screen(this.require(id).sessionId)).then(text => String(text || '').slice(-120000)) };
      this.screens.set(id, cached);
      while (this.screens.size > 10) this.screens.delete(this.screens.keys().next().value);
    }
    const screen = await cached.promise;
    const entry = this.require(id);
    if (revision === record.revision) return { key: record.key, revision, unchanged: true, talk: entry.talk, screen };
    return { ...record, talk: entry.talk, screen };
  }
  comment(id, { text, who, messageId }) {
    const entry = this.require(id);
    // Verify that a removed local session is no longer commentable either.
    store.readSession(this.userData, entry.sessionId);
    if (typeof text !== 'string' || !text.trim() || text.length > MAX_TALK) throw new Error('ひとことは1〜500文字で入力してください');
    if (typeof who !== 'string' || !who || who.length > 60 || !/^[\w.-]+$/.test(who)) throw new Error('参加者名が不正です');
    if (typeof messageId !== 'string' || !/^[0-9a-f-]{36}$/.test(messageId)) throw new Error('ひとことのIDが不正です');
    if (!entry.talk.some(m => m.id === messageId && m.who === who)) {
      entry.talk.push({ id: messageId, who, text: text.trim(), at: new Date().toISOString() });
      entry.talk = entry.talk.slice(-KEEP_TALK); this.save();
    }
    return entry.talk;
  }
}
module.exports = { Publications };
