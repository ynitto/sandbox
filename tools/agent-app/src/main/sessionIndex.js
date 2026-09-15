"use strict";
// 会話の索引。一度読んだ会話の見出しと本文をアプリ側（userData）へ置き、
// 2 回目以降の検索を解析なしで返す。索引が使えない環境では open() が null を返し、
// 検索は毎回の走査（ふるい付き）に落ちる。
const fs = require('fs');
const path = require('path');

const BODY_LIMIT = 512 * 1024;
const SIZE_LIMIT = 1024 * 1024 * 1024;

function sqlite() {
  try { return require('node:sqlite'); } catch { return null; }
}
function comparablePath(value) {
  let text = String(value || '').replace(/\\/g, '/');
  if (text.length > 2 && text[1] === ':') text = '/mnt/' + text[0].toLowerCase() + text.slice(2);
  return text.toLocaleLowerCase();
}
function like(value) { return '%' + String(value).replace(/[\\%_]/g, c => '\\' + c) + '%'; }

class SessionIndex {
  static open(file) {
    const mod = sqlite();
    if (!mod) return null;
    try {
      fs.mkdirSync(path.dirname(file), { recursive: true });
      const db = new mod.DatabaseSync(file);
      const index = new SessionIndex(db);
      index.prepare();
      return index;
    } catch { return null; }
  }
  constructor(db) { this.db = db; }
  prepare() {
    this.db.exec(`
      PRAGMA journal_mode = WAL;
      CREATE TABLE IF NOT EXISTS sessions(
        id INTEGER PRIMARY KEY, target TEXT NOT NULL, path TEXT NOT NULL,
        descriptor_id TEXT NOT NULL DEFAULT '', native_id TEXT NOT NULL DEFAULT '', provider TEXT, size INTEGER, mtime REAL, repo TEXT, repo_key TEXT, model TEXT, model_key TEXT,
        title TEXT, created_at REAL, updated_at REAL, archived INTEGER, messages INTEGER, partial INTEGER,
        truncated INTEGER, body TEXT,
        UNIQUE(target, path, descriptor_id));
      CREATE INDEX IF NOT EXISTS sessions_updated ON sessions(updated_at DESC);
      CREATE VIRTUAL TABLE IF NOT EXISTS session_text USING fts5(
        title, body, content='sessions', content_rowid='id', tokenize='trigram');
      CREATE TRIGGER IF NOT EXISTS sessions_ai AFTER INSERT ON sessions BEGIN
        INSERT INTO session_text(rowid, title, body) VALUES (new.id, new.title, new.body); END;
      CREATE TRIGGER IF NOT EXISTS sessions_ad AFTER DELETE ON sessions BEGIN
        INSERT INTO session_text(session_text, rowid, title, body) VALUES ('delete', old.id, old.title, old.body); END;
      CREATE TRIGGER IF NOT EXISTS sessions_au AFTER UPDATE ON sessions BEGIN
        INSERT INTO session_text(session_text, rowid, title, body) VALUES ('delete', old.id, old.title, old.body);
        INSERT INTO session_text(rowid, title, body) VALUES (new.id, new.title, new.body); END;`);
  }
  // 候補のうち、索引が無い・古いものだけを返す。消えた会話はその場で落とす。
  plan(target, descriptors) {
    const known = new Map();
    for (const row of this.db.prepare('SELECT path, descriptor_id, size, mtime, truncated FROM sessions WHERE target = ?').all(target)) {
      known.set(row.path + '\u0000' + row.descriptor_id, row);
    }
    const seen = new Set(), stale = [], truncated = [];
    for (const descriptor of descriptors) {
      const key = descriptor.path + '\u0000' + (descriptor.nativeId || '');
      seen.add(key);
      const row = known.get(key);
      const same = row && row.mtime === (descriptor.updatedAt || 0)
        && (descriptor.provider === 'kiro' || row.size === (descriptor.size || 0));
      if (!same) stale.push(descriptor);
      // 本文が上限で切れている会話は、索引だけでは一致を判断しきれない。
      else if (row.truncated) truncated.push(descriptor);
    }
    const gone = [...known.keys()].filter(key => !seen.has(key));
    if (gone.length) {
      const drop = this.db.prepare('DELETE FROM sessions WHERE target = ? AND path = ? AND descriptor_id = ?');
      for (const key of gone) { const [file, native] = key.split('\u0000'); drop.run(target, file, native); }
    }
    return { stale, truncated, removed: gone.length, known: known.size };
  }
  put(target, records) {
    const insert = this.db.prepare(`INSERT INTO sessions(
        target, path, descriptor_id, native_id, provider, size, mtime, repo, repo_key, model, model_key, title,
        created_at, updated_at, archived, messages, partial, truncated, body)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(target, path, descriptor_id) DO UPDATE SET
        native_id=excluded.native_id, provider=excluded.provider, size=excluded.size, mtime=excluded.mtime, repo=excluded.repo,
        repo_key=excluded.repo_key, model=excluded.model, model_key=excluded.model_key, title=excluded.title,
        created_at=excluded.created_at, updated_at=excluded.updated_at, archived=excluded.archived,
        messages=excluded.messages, partial=excluded.partial, truncated=excluded.truncated, body=excluded.body`);
    for (const record of records) {
      insert.run(target, record.path, record.descriptorId || '', record.nativeId || '', record.provider || '', record.size || 0,
        record.mtime || 0, record.repo || '', comparablePath(record.repo), record.model || '',
        String(record.model || '').toLocaleLowerCase(), record.title || '', record.createdAt || 0,
        record.updatedAt || 0, record.archived ? 1 : 0, record.count || 0, record.partial ? 1 : 0,
        record.truncated ? 1 : 0, String(record.body || '').slice(0, BODY_LIMIT));
    }
  }
  forget(target, paths) {
    const drop = this.db.prepare('DELETE FROM sessions WHERE target = ? AND path = ?');
    for (const file of paths) drop.run(target, file);
  }
  // 索引に入っている会話のうち、条件に合うものを新しい順で返す。
  find(query = {}, targets = null) {
    const needle = String(query.text || '');
    const where = [], args = [];
    let from = 'sessions s';
    if (needle.length >= 3) {
      from = 'session_text t JOIN sessions s ON s.id = t.rowid';
      where.push('session_text MATCH ?');
      args.push('"' + needle.replace(/"/g, '""') + '"');
    } else if (needle) {
      where.push('(s.title LIKE ? ESCAPE \'\\\' OR s.body LIKE ? ESCAPE \'\\\')');
      args.push(like(needle), like(needle));
    }
    if (targets) { where.push(`s.target IN (${targets.map(() => '?').join(',')})`); args.push(...targets); }
    if (query.agent) { where.push('(CASE WHEN s.provider = \'vscode\' THEN \'copilot\' ELSE s.provider END) = ?'); args.push(query.agent); }
    if (query.source) { where.push('(CASE WHEN s.provider = \'vscode\' THEN \'vscode\' ELSE \'cli\' END) = ?'); args.push(query.source); }
    if (!query.archived) where.push('s.archived = 0');
    const field = query.dateField === 'created' ? 's.created_at' : 's.updated_at';
    if (query.since) { where.push(`${field} >= ?`); args.push(query.since); }
    if (query.until) { where.push(`${field} < ?`); args.push(query.until); }
    if (query.repo) { where.push('s.repo_key LIKE ? ESCAPE \'\\\''); args.push(like(comparablePath(query.repo))); }
    if (query.model) { where.push('s.model_key LIKE ? ESCAPE \'\\\''); args.push(like(String(query.model).toLocaleLowerCase())); }
    const clause = where.length ? 'WHERE ' + where.join(' AND ') : '';
    const lower = needle.toLocaleLowerCase();
    const sql = `SELECT s.target, s.path, s.descriptor_id AS descriptorId, s.native_id AS nativeId, s.provider, s.repo, s.model, s.title,
        s.created_at AS createdAt, s.updated_at AS updatedAt, s.archived, s.messages AS count, s.partial,
        CASE WHEN ? = '' THEN substr(s.body, 1, 180)
             ELSE substr(s.body, max(1, instr(lower(s.body), ?) - 50), 180) END AS snippet
      FROM ${from} ${clause} ORDER BY s.updated_at DESC`;
    return this.db.prepare(sql).all(lower, lower, ...args);
  }
  stats() {
    const rows = this.db.prepare('SELECT count(*) AS sessions FROM sessions').get();
    const size = this.db.prepare('SELECT page_count * page_size AS bytes FROM pragma_page_count(), pragma_page_size()').get();
    return { sessions: Number(rows.sessions || 0), bytes: Number(size.bytes || 0) };
  }
  // 上限を超えたら、古い会話から索引を落とす（会話自体は走査で見つかる）。
  prune(limit = SIZE_LIMIT) {
    let dropped = 0;
    while (this.stats().bytes > limit) {
      const victims = this.db.prepare('SELECT id FROM sessions ORDER BY updated_at ASC LIMIT 200').all();
      if (!victims.length) break;
      const drop = this.db.prepare('DELETE FROM sessions WHERE id = ?');
      for (const row of victims) drop.run(row.id);
      dropped += victims.length;
      this.db.exec('VACUUM');
    }
    return dropped;
  }
  close() { try { this.db.close(); } catch { /* already closed */ } }
}

module.exports = { SessionIndex, comparablePath, BODY_LIMIT, SIZE_LIMIT };
