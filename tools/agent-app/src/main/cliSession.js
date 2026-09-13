'use strict';

// Native CLI receipts live on the tmux host, so the same path works after an app
// restart and when Electron is on Windows while the CLI runs in WSL.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { parse } = require('smol-toml');
const host = require('./host');
const supported = (cli) => cli === 'codex' || cli === 'kiro';

function runtimePath({ home, id, cli }) {
  if (!/^[a-zA-Z0-9_-]+$/.test(id) || !/^[a-zA-Z0-9_-]+$/.test(cli)) throw new Error('セッション記録先が不正です');
  return host.joinHost(home, `.local/state/agent-app/cli-sessions/${id}/${cli}`);
}

async function read(options) {
  if (!supported(options.cli)) return '';
  const result = await options.shell.exec(['cat', host.joinHost(runtimePath(options), 'session.json')]);
  if (!result.ok) return '';
  try {
    const value = JSON.parse(result.output).id;
    return typeof value === 'string' && /^[a-zA-Z0-9_.:-]{1,200}$/.test(value) ? value : '';
  } catch { return ''; }
}

async function codexNotify({ shell, home, env = {}, argv }) {
  const root = env.CODEX_HOME || (await shell.run('printf %s "${CODEX_HOME:-}"')).output || host.joinHost(home, '.codex');
  async function config(file) {
    const result = await shell.exec(['cat', file]);
    return result.ok ? parse(result.output) : {};
  }
  const base = await config(host.joinHost(root, 'config.toml'));
  let value = base.notify;
  let profile = base.profile || '';
  for (let i = 0; i < argv.length; i += 1) {
    if (['--profile', '-p'].includes(argv[i])) profile = argv[i + 1] || '';
    if (argv[i].startsWith('--profile=')) profile = argv[i].slice(10);
  }
  if (profile) {
    if (!/^[a-zA-Z0-9_.-]+$/.test(profile)) throw new Error('Codex のプロファイル名が不正です');
    const external = await config(host.joinHost(root, `${profile}.config.toml`));
    value = external.notify ?? base.profiles?.[profile]?.notify ?? value;
  }
  for (let i = 0; i < argv.length; i += 1) {
    const arg = ['--config', '-c'].includes(argv[i]) ? argv[i + 1] : argv[i].replace(/^--config=/, '');
    if (/^notify\s*=/.test(arg || '')) value = parse(arg).notify;
  }
  if (value != null && (!Array.isArray(value) || !value.every((item) => typeof item === 'string'))) {
    throw new Error('Codex の通知設定を読み取れません');
  }
  return value || [];
}

async function prepare(options) {
  const { shell, cli, argv, env = {} } = options;
  if (!supported(cli)) return { argv, env };
  const chained = cli === 'codex' ? await codexNotify(options) : [];
  const runtime = runtimePath(options);
  const script = host.joinHost(runtime, 'capture.py');
  const source = fs.readFileSync(path.join(__dirname, 'cli-session.py'), 'utf8');
  const install = await shell.exec(['python3', '-c',
    'import os,sys,pathlib; p=pathlib.Path(sys.argv[1]); p.parent.mkdir(parents=True,exist_ok=True,mode=0o700); p.write_text(sys.argv[2]); p.chmod(0o600)',
    script, source]);
  if (!install.ok) throw new Error(`セッションIDの記録にはホストの Python 3 が必要です: ${install.error}`);
  const result = await shell.exec(['python3', script, 'prepare', JSON.stringify({
    cli, argv, env, cwd: options.cwd, runtime, chained, token: crypto.randomUUID(),
  })]);
  if (!result.ok) throw new Error(`セッションIDの記録を準備できません: ${result.error}`);
  return JSON.parse(result.output);
}

module.exports = { prepare, read, supported };
