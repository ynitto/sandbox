// プロキシの環境変数（http_proxy / https_proxy / no_proxy）を大文字小文字を問わず読む。
//
// Node.js の fetch は NODE_USE_ENV_PROXY=1 のときだけ環境変数のプロキシを使い、しかも
// HTTP_PROXY / http_proxy の 2 通りの綴りしか見ない。そこで、プロキシの指定があれば
// 綴りを大文字にそろえ、NODE_USE_ENV_PROXY=1 を付けて自分自身を起動し直す。

import { spawnSync } from 'node:child_process';

const NAMES = ['HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY'];
export const CHILD_MARK = 'FIGMA_USE_PROXY_CHILD';

/** 綴りの違う指定をまとめる。同じ名前が複数あれば大文字の綴りを優先する。 */
export function readProxyEnv(env = process.env) {
  const out = {};
  for (const name of NAMES) {
    if (env[name]) {
      out[name] = env[name];
      continue;
    }
    const key = Object.keys(env).find((k) => k.toUpperCase() === name && env[k]);
    if (key) out[name] = env[key];
  }
  return out;
}

export function hasProxy(env = process.env) {
  const p = readProxyEnv(env);
  return Boolean(p.HTTP_PROXY || p.HTTPS_PROXY);
}

/** 起動し直す子プロセスの環境。どの綴りの指定も消してから大文字で入れ直す。 */
export function childEnv(env = process.env) {
  const proxy = readProxyEnv(env);
  const out = {};
  for (const [k, v] of Object.entries(env)) {
    if (!NAMES.includes(k.toUpperCase())) out[k] = v;
  }
  return { ...out, ...proxy, NODE_USE_ENV_PROXY: '1', [CHILD_MARK]: '1' };
}

/** 表示用。認証情報（user:pass@）を伏せてホストとポートだけを返す。 */
export function describeProxy(value) {
  try {
    const u = new URL(value);
    return `${u.protocol}//${u.host}`;
  } catch {
    return '（URL として読めない値）';
  }
}

/**
 * プロキシの指定があり、まだ起動し直していなければ、子プロセスで実行して終了コードを返す。
 * 起動し直さないときは null。
 */
export function reexecWithProxy(scriptPath, args, env = process.env) {
  if (env[CHILD_MARK] === '1' || !hasProxy(env)) return null;
  const r = spawnSync(process.execPath, [...process.execArgv, scriptPath, ...args], { stdio: 'inherit', env: childEnv(env) });
  if (r.error) throw r.error;
  return r.status ?? 1;
}
