'use strict';

// 公開履歴の有無ではなく、Git の公開ブランチと一覧に載せた実体を照合する。
// 公開操作用の作業ツリーとは別の bare clone を使い、表示から checkout/push しない。
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const host = require('./host');
const { readVersion, compareVersions } = require('./skillVersion');
const { gitExec } = require('./skillCredentials');

const ROOTS = ['.agents/skills', '.github/skills', '.claude/skills', '.codex/skills'];
const ignored = (name) => ['.git', '__pycache__', 'node_modules', '.DS_Store'].includes(name) || name.endsWith('.pyc');

function registryOf(item) {
  try {
    const value = JSON.parse(fs.readFileSync(path.join(path.dirname(path.dirname(item.dir)), 'skill-registry.json'), 'utf8'));
    return value && typeof value === 'object' ? value : {};
  } catch { return {}; }
}

function localFiles(dir) {
  const result = new Map();
  function walk(full, rel) {
    const stat = fs.lstatSync(full);
    if (stat.isSymbolicLink()) result.set(rel, Buffer.from(fs.readlinkSync(full)));
    else if (stat.isDirectory()) {
      for (const name of fs.readdirSync(full)) {
        if (!ignored(name) && !(rel === '' && name === 'origin.json')) walk(path.join(full, name), rel ? `${rel}/${name}` : name);
      }
    } else if (stat.isFile()) result.set(rel, fs.readFileSync(full));
  }
  walk(dir, '');
  return result;
}

function blobHash(bytes, algorithm) {
  return crypto.createHash(algorithm).update(`blob ${bytes.length}\0`).update(bytes).digest('hex');
}

function matches(local, remote) {
  if (local.size !== remote.size) return false;
  for (const [name, bytes] of local) {
    const hash = remote.get(name);
    if (!hash) return false;
    const algorithm = hash.length === 64 ? 'sha256' : 'sha1';
    if (blobHash(bytes, algorithm) === hash) continue;
    // Windows にインストールしたテキストの CRLF は内容変更に数えない。
    if (bytes.includes(0) || blobHash(Buffer.from(bytes.toString('utf8').replace(/\r\n/g, '\n')), algorithm) !== hash) return false;
  }
  return true;
}

// main が再走査したカタログからのみ作る。renderer の任意パスは受け付けない。
function sourceOf(item) {
  if (!item || !/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(item.name)
    || path.basename(item.path || '') !== 'SKILL.md') return null;
  return { full: item.dir, rel: `.agents/skills/${item.name}`, dir: true,
    key: crypto.createHash('sha256').update(path.resolve(item.path)).digest('hex') };
}

class SkillPublication {
  constructor({ userData, shell, loadAuth = () => ({}), now = () => Date.now() }) {
    Object.assign(this, { userData, shell, loadAuth, now });
    this.cache = new Map();
  }

  present(item, base, remote, actionable) {
    const state = { ...base, ...remote };
    return {
      name: item.name, description: item.description, version: item.version, place: item.place,
      ...state,
      publicationKey: sourceOf(item)?.key || '',
      canPublish: actionable && !!state.configured && state.versionComparison === 'local-newer',
      canImprove: actionable && !!base.canImprove && state.status !== 'unknown',
    };
  }

  async exec(argv, url = '') {
    const auth = url ? this.loadAuth() : {};
    const result = await gitExec(this.shell(), argv, { timeoutMs: 120000 }, auth.url === url ? auth : {});
    if (!result.ok) throw new Error(String(result.error || result.output || 'Git の公開状態を確認できません'));
    return String(result.output || '');
  }

  snapshot(url) {
    const known = this.cache.get(url);
    if (known && this.now() - known.at < 60000) return known.promise;
    const entry = { at: Infinity };
    entry.promise = this.readSnapshot(url).then((trees) => {
      entry.at = this.now();
      return trees;
    }).catch((error) => {
      if (this.cache.get(url) === entry) this.cache.delete(url);
      throw error;
    });
    this.cache.set(url, entry);
    return entry.promise;
  }

  async readSnapshot(url) {
    const id = crypto.createHash('sha256').update(url).digest('hex').slice(0, 16);
    const dir = path.join(this.userData, 'skill-publication', id);
    const cwd = host.toHostPath(dir);
    if (!fs.existsSync(path.join(dir, 'HEAD'))) {
      fs.mkdirSync(path.dirname(dir), { recursive: true });
      await this.exec(['git', 'clone', '--bare', '--depth', '1', '--no-single-branch', '--', url, cwd], url);
    } else {
      await this.exec(['git', '-C', cwd, 'fetch', '--depth', '1', '--prune', 'origin', '+refs/heads/*:refs/heads/*'], url);
    }
    const refs = await this.exec(['git', '-C', cwd, 'for-each-ref', '--format=%(refname)', 'refs/heads/']);
    const trees = [];
    for (const ref of refs.trim().split('\n').filter(Boolean)) {
      const output = await this.exec(['git', '-C', cwd, 'ls-tree', '-r', '-z', ref]);
      const files = new Map();
      for (const line of output.split('\0')) {
        const match = line.match(/^\d+ blob ([a-f0-9]+)\t([\s\S]+)$/);
        if (match) files.set(match[2], match[1]);
      }
      trees.push({ branch: ref.replace(/^refs\/heads\//, ''), files });
    }
    return { trees, cwd, versions: new Map() };
  }

  async versionOf(snapshot, hash) {
    if (!snapshot.versions.has(hash)) {
      const version = await this.exec(['git', '-C', snapshot.cwd, 'cat-file', 'blob', hash]);
      snapshot.versions.set(hash, readVersion(version));
    }
    return snapshot.versions.get(hash);
  }

  async states(items, shareRepo = '') {
    const result = new Map();
    // 失敗もこの一覧の間は共有する。同じ接続失敗をスキル数だけ繰り返さない。
    const snapshots = new Map();
    const origins = new Map();
    for (const item of items) {
      if (path.basename(item.path) !== 'SKILL.md') continue;
      const registry = registryOf(item);
      const installed = (Array.isArray(registry.installed_skills) ? registry.installed_skills : []).find((entry) => entry?.name === item.name);
      const source = (Array.isArray(registry.repositories) ? registry.repositories : []).find((entry) => entry?.name === installed?.source_repo);
      let url = String(shareRepo || source?.url || '').trim();
      if (!url && item.repo) {
        if (!origins.has(item.repo)) {
          try { origins.set(item.repo, (await this.exec(['git', '-C', host.toHostPath(item.repo), 'remote', 'get-url', 'origin'])).trim()); }
          catch { origins.set(item.repo, ''); }
        }
        url = origins.get(item.repo);
      }
      if (!url) continue;
      try {
        if (!snapshots.has(url)) snapshots.set(url, this.snapshot(url));
        const snapshot = await snapshots.get(url);
        const candidates = ROOTS.map((root) => `${root}/${item.name}`);
        if (source?.url === url && source.skill_root) candidates.unshift(`${source.skill_root.replace(/\/$/, '')}/${item.name}`);
        const local = localFiles(item.dir);
        const localVersion = readVersion(local.get('SKILL.md')?.toString('utf8'));
        let state = { status: 'unpublished', branch: '' };
        let newest = null;
        for (const tree of snapshot.trees) {
          for (const rel of candidates) {
            const skillHash = tree.files.get(`${rel}/SKILL.md`);
            if (!skillHash) continue;
            const version = await this.versionOf(snapshot, skillHash);
            if (!newest || compareVersions(version, newest.version) === 1
              || (compareVersions(newest.version, newest.version) === null && compareVersions(version, version) === 0)) {
              newest = { version, branch: tree.branch };
            }
            const remote = new Map();
            for (const [file, hash] of tree.files) {
              if (!file.startsWith(`${rel}/`)) continue;
              const name = file.slice(rel.length + 1);
              if (name === 'origin.json' || name.split('/').some(ignored)) continue;
              remote.set(name, hash);
            }
            const same = matches(local, remote);
            if (state.status !== 'published') state = { status: same ? 'published' : 'updated', branch: tree.branch };
          }
        }
        const comparison = newest ? compareVersions(localVersion, newest.version) : null;
        result.set(item.path, {
          ...state, localVersion, remoteVersion: newest?.version || '', remoteVersionBranch: newest?.branch || '',
          versionComparison: !newest ? (compareVersions(localVersion, localVersion) === 0 ? 'local-newer' : 'unknown') : comparison === null ? 'unknown'
            : comparison > 0 ? 'local-newer' : comparison < 0 ? 'remote-newer' : 'same',
        });
      } catch (error) {
        result.set(item.path, { status: 'unknown', error: error.message, branch: '' });
      }
    }
    return result;
  }
}

module.exports = { SkillPublication, sourceOf };
