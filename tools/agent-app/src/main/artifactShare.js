'use strict';

// 定型化した成果物（スキル・タスク・ワークフロー）を、指定した共有先リポジトリへ出す。
//
// 設計は docs/plans/2026-09-16-agent-app-agent-audit-split-and-artifact-sharing-design.md §2.4・§2.5。
//   提出   … 初めて成功した成果物を share/<種別>-<名前> ブランチへ push する
//   改善   … 適格性が落ちた成果物を、証跡を渡して直させ improve/<種別>-<名前> へ push する
// merge は人。ここは push までで止める。
//
// git は他の画面と同じホスト（Windows なら WSL）のシェルで動かす。資格情報も CLI と
// 同じ場所のものを使う。agent-audit へは持ち込まない——あちらは測る側で、書ける先を
// 型付きの allowlist に閉じている。
//
// 同じ成果物を二度出さない・未マージの改善に重ねて出さないための記録は
// userData/artifact-share/state.json（1 ファイル・原子置換）。

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const host = require('./host');

const DIR = 'artifact-share';
const STATE = 'state.json';
const CLONE_TIMEOUT_MS = 10 * 60 * 1000;
const GIT_TIMEOUT_MS = 2 * 60 * 1000;

// 成果物の正典の置き場。スキルだけは規約が 2 通りあるので、実在する側を選ぶ。
const LAYOUT = {
  skill: ['.agents/skills/<name>', '.github/skills/<name>'],
  task: ['.statemachine/<name>'],
  workflow: ['.agents/workflows/<name>.json', '.agents/workflows/<name>.yaml'],
};

function keyOf(kind, name) { return `${kind}/${name}`; }

function stateFile(userData) { return path.join(userData, DIR, STATE); }

function readState(userData) {
  try {
    const value = JSON.parse(fs.readFileSync(stateFile(userData), 'utf8'));
    return value && typeof value === 'object' ? value : {};
  } catch { return {}; }
}

function writeState(userData, next) {
  const file = stateFile(userData);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(`${file}.tmp`, JSON.stringify(next, null, 2), 'utf8');
  fs.renameSync(`${file}.tmp`, file);
  return next;
}

function record(userData, kind, name, patch) {
  const state = readState(userData);
  const key = keyOf(kind, name);
  const next = { ...state, [key]: { ...(state[key] || {}), ...patch } };
  return writeState(userData, next)[key];
}

function statusOf(userData, kind, name) {
  return readState(userData)[keyOf(kind, name)] || null;
}

// 成果物が repo のどこにあるか（無ければ null）。
function locate(repo, kind, name) {
  const shapes = LAYOUT[kind];
  if (!shapes || !/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(String(name || ''))) return null;
  for (const shape of shapes) {
    const rel = shape.replace('<name>', name);
    const full = path.join(repo, rel.split('/').join(path.sep));
    if (fs.existsSync(full)) return { rel, full, dir: fs.statSync(full).isDirectory() };
  }
  return null;
}

function branchFor(prefix, kind, name) {
  return `${prefix}/${kind}-${String(name).replace(/[^A-Za-z0-9_.-]/g, '-')}`;
}

function cloneDir(userData, shareRepo) {
  const id = crypto.createHash('sha256').update(String(shareRepo)).digest('hex').slice(0, 16);
  return path.join(userData, DIR, id);
}

class ArtifactShare {
  constructor({ userData, shellFor = host.shellFor, loadConfig = () => ({}), runPrompt = null, now = () => Date.now() }) {
    Object.assign(this, { userData, shellFor, loadConfig, runPrompt, now });
  }

  config() {
    const cfg = this.loadConfig() || {};
    return {
      ...(cfg.audit || {}),
      distro: process.platform === 'win32' ? String(cfg.wslDistro || '') : '',
    };
  }

  shell() { return this.shellFor(this.config().distro); }

  async git(cwd, args, { timeoutMs = GIT_TIMEOUT_MS } = {}) {
    const r = await this.shell().exec(['git', '-C', cwd, ...args], { timeoutMs });
    return { ok: r.ok, out: String(r.output || ''), error: String(r.error || r.output || '').split('\n').slice(-4).join('\n') };
  }

  // 共有先の作業用クローン。無ければ浅く clone、あれば fetch し直す。
  async ensureClone(shareRepo) {
    const dir = cloneDir(this.userData, shareRepo);
    const hostDir = host.toHostPath(dir);
    const shell = this.shell();
    if (!fs.existsSync(path.join(dir, '.git'))) {
      fs.mkdirSync(path.dirname(dir), { recursive: true });
      const r = await shell.exec(['git', 'clone', '--depth', '50', String(shareRepo), hostDir],
        { timeoutMs: CLONE_TIMEOUT_MS });
      if (!r.ok) throw new Error(`共有先を取得できません: ${String(r.error || r.output || '').split('\n').slice(-3).join('\n')}`);
      return { dir, hostDir };
    }
    const fetched = await this.git(hostDir, ['fetch', '--depth', '50', 'origin'], { timeoutMs: CLONE_TIMEOUT_MS });
    if (!fetched.ok) throw new Error(`共有先を更新できません: ${fetched.error}`);
    return { dir, hostDir };
  }

  async defaultBranch(hostDir) {
    const head = await this.git(hostDir, ['symbolic-ref', '--short', 'refs/remotes/origin/HEAD']);
    const name = head.ok ? head.out.trim().replace(/^origin\//, '') : '';
    return name || 'main';
  }

  // 共有先の作業ツリーを、既定ブランチの先端から <branch> へ移す。
  async startBranch(hostDir, branch) {
    const base = await this.defaultBranch(hostDir);
    const reset = await this.git(hostDir, ['checkout', '-B', branch, `origin/${base}`]);
    if (!reset.ok) throw new Error(`共有先でブランチを作れません: ${reset.error}`);
    return base;
  }

  async copyInto(hostDir, { sourceRepo, rel, dir }) {
    const from = host.joinHost(host.toHostPath(sourceRepo), rel);
    const to = host.joinHost(hostDir, rel);
    const parent = to.slice(0, to.lastIndexOf('/')) || '/';
    const script = `mkdir -p ${host.sq(parent)} && rm -rf ${host.sq(to)} && cp -R ${host.sq(from)}${dir ? '/.' : ''} ${host.sq(to)}`;
    const r = await this.shell().run(script, { timeoutMs: GIT_TIMEOUT_MS });
    if (!r.ok) throw new Error(`成果物を写せません: ${String(r.error || r.output || '').split('\n').slice(-3).join('\n')}`);
  }

  async commitAndPush(hostDir, { branch, message, pushToMain = false }) {
    const staged = await this.git(hostDir, ['add', '-A']);
    if (!staged.ok) throw new Error(`共有先へ追加できません: ${staged.error}`);
    const diff = await this.git(hostDir, ['diff', '--cached', '--quiet']);
    if (diff.ok) return { pushed: false, unchanged: true, branch };
    const committed = await this.git(hostDir, ['-c', 'user.name=agent-app', '-c', 'user.email=agent-app@localhost',
      'commit', '-m', message]);
    if (!committed.ok) throw new Error(`共有先へコミットできません: ${committed.error}`);
    const target = pushToMain ? await this.defaultBranch(hostDir) : branch;
    const pushed = await this.git(hostDir, ['push', 'origin', `HEAD:refs/heads/${target}`],
      { timeoutMs: CLONE_TIMEOUT_MS });
    if (!pushed.ok) throw new Error(`共有先へ push できません: ${pushed.error}`);
    return { pushed: true, branch: target, unchanged: false };
  }

  // 出所。どのリポジトリのどのコミットから来たかを成果物の隣に残す。
  async originJson(hostDir, { sourceRepo, rel, kind, name, sessionId }) {
    const head = await this.git(host.toHostPath(sourceRepo), ['rev-parse', 'HEAD']);
    const payload = {
      kind,
      name,
      from_repo: path.basename(sourceRepo),
      from_commit: head.ok ? head.out.trim() : '',
      session: String(sessionId || ''),
      shared_at: new Date(this.now()).toISOString(),
      shared_by: 'agent-app',
    };
    const dest = host.joinHost(hostDir, `${rel.replace(/\.(json|yaml)$/, '')}/origin.json`);
    const parent = dest.slice(0, dest.lastIndexOf('/')) || '/';
    const script = `mkdir -p ${host.sq(parent)} && cat > ${host.sq(dest)} <<'AGENT_APP_ORIGIN'\n${JSON.stringify(payload, null, 2)}\nAGENT_APP_ORIGIN`;
    await this.shell().run(script, { timeoutMs: GIT_TIMEOUT_MS });
    return payload;
  }

  // 初めて成功した成果物を共有先へ出す。既に出していれば何もしない。
  async submit({ repo, kind, name, sessionId = '', force = false }) {
    const cfg = this.config();
    const shareRepo = String(cfg.shareRepo || '').trim();
    if (!shareRepo) return { skipped: 'no-share-repo' };
    const known = statusOf(this.userData, kind, name);
    if (known && known.submittedBranch && !force) return { skipped: 'already', ...known };
    const found = locate(repo, kind, name);
    if (!found) return { skipped: 'not-found' };
    const { hostDir } = await this.ensureClone(shareRepo);
    const branch = branchFor('share', kind, name);
    await this.startBranch(hostDir, branch);
    await this.copyInto(hostDir, { sourceRepo: repo, rel: found.rel, dir: found.dir });
    if (found.dir) await this.originJson(hostDir, { sourceRepo: repo, rel: found.rel, kind, name, sessionId });
    const result = await this.commitAndPush(hostDir, {
      branch,
      message: `share(${kind}): ${name}\n\n初めて成功した定型化物を共有します（agent-app が自動で出しました）。`,
      pushToMain: cfg.pushToMain === true,
    });
    const saved = record(this.userData, kind, name, {
      kind, name, repo, submittedBranch: result.branch, submittedAt: new Date(this.now()).toISOString(),
      unchanged: result.unchanged,
    });
    return { ...result, ...saved };
  }

  // 適格性が落ちた成果物を直させ、improve/ ブランチへ出す。未マージの改善があれば出さない。
  async improve({ repo, kind, name, evidence = [], cli = '', model = '' }) {
    const cfg = this.config();
    const shareRepo = String(cfg.shareRepo || '').trim();
    if (!shareRepo) return { skipped: 'no-share-repo' };
    if (!this.runPrompt) return { skipped: 'no-runner' };
    const known = statusOf(this.userData, kind, name) || {};
    if (known.improveBranch && !known.improveMergedAt) return { skipped: 'improve-open', ...known };
    const found = locate(repo, kind, name);
    if (!found) return { skipped: 'not-found' };
    const { dir, hostDir } = await this.ensureClone(shareRepo);
    const branch = branchFor('improve', kind, name);
    await this.startBranch(hostDir, branch);
    await this.copyInto(hostDir, { sourceRepo: repo, rel: found.rel, dir: found.dir });
    const outcome = await this.runPrompt({
      cli, model, readonly: false, cwd: dir, repo: dir,
      prompt: improvePrompt({ kind, name, rel: found.rel, evidence }),
      timeoutMs: CLONE_TIMEOUT_MS,
    });
    if (outcome && outcome.error && !outcome.text) {
      return { skipped: 'run-failed', error: outcome.error };
    }
    const result = await this.commitAndPush(hostDir, {
      branch,
      message: `improve(${kind}): ${name}\n\n${evidenceLines(evidence).join('\n')}`,
      pushToMain: false,
    });
    if (result.unchanged) return { skipped: 'no-change', branch };
    const saved = record(this.userData, kind, name, {
      kind, name, repo, improveBranch: result.branch, improvedAt: new Date(this.now()).toISOString(),
      improveMergedAt: '', improveNote: String((outcome && outcome.text) || '').split('\n').slice(0, 6).join('\n').slice(0, 600),
    });
    return { ...result, ...saved };
  }

  list() {
    const state = readState(this.userData);
    return Object.entries(state).map(([key, value]) => ({ key, ...value }));
  }
}

function evidenceLines(evidence) {
  return (Array.isArray(evidence) ? evidence : []).slice(0, 12).map((item) => {
    if (typeof item === 'string') return `- ${item}`;
    const where = [item.status, item.error_class || item.errorClass].filter(Boolean).join(' / ');
    return `- ${item.ts || ''} ${where}${item.note ? ` ${item.note}` : ''}`.trim();
  });
}

function improvePrompt({ kind, name, rel, evidence }) {
  const label = { skill: 'スキル', task: 'タスク', workflow: 'ワークフロー' }[kind] || kind;
  const lines = evidenceLines(evidence);
  return [
    `この${label}「${name}」（${rel}）は、実際の実行で基準を満たさなくなりました。`,
    '以下は監査が記録した実測の証跡です（人の感想ではありません）。',
    lines.length ? lines.join('\n') : '- （個別の証跡は残っていません）',
    '',
    `${rel} を直してください。守ること:`,
    '- 失敗の原因に当たる箇所だけを直し、関係のない整形や機能追加をしない',
    '- 入力の前提・完了条件・確認方法が曖昧なら、そこを具体的にする',
    '- 単発の入力値や秘密情報を定義へ固定しない',
    '- 直した理由を 3 行以内で最後に書く（ファイルには書かない）',
  ].join('\n');
}

module.exports = {
  DIR, LAYOUT, ArtifactShare, locate, branchFor, cloneDir, stateFile, readState, statusOf, record,
  improvePrompt, evidenceLines,
};
