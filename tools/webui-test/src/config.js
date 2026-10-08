'use strict';
// 実行する環境（ローカル・検証環境など）の設定。webui-test.config.yaml に環境ごとの接続先・認証・
// 事前に入れる値を書き、--env で切り替える。値の ${名前} は環境変数で置き換える（秘密をファイルに書かない）。
//
//   defaultEnv: local
//   evidence: results/webui-test-evidence.json                 # 最新の結果（省略時もこのパス）
//   serve: { command: npm start, url: http://localhost:3000 }   # ローカルで起動してから動かす（任意）
//   check: { cases: [tests/e2e] }                               # webui-test check（任意）
//   envs:
//     local:   { baseUrl: http://localhost:3000 }
//     staging: { baseUrl: https://stg.example.com, mocks: false, storageState: auth/staging.json,
//                headers: { Authorization: "Bearer ${STAGING_TOKEN}" } }
//
// ファイルに書くパスは、すべてこの設定ファイルのフォルダからの相対。

const fs = require('fs');
const path = require('path');
const YAML = require('yaml');
const { isPlainObject } = require('./casefile');

const CONFIG_NAMES = ['webui-test.config.yaml', 'webui-test.config.yml', 'webui-test.config.json'];
const TOP_KEYS = ['defaultEnv', 'envs', 'serve', 'check', 'evidence'];
const DEFAULT_EVIDENCE = 'results/webui-test-evidence.json';
const ENV_KEYS = ['baseUrl', 'localStorage', 'sessionStorage', 'cookies', 'headers', 'storageState', 'mocks', 'locale', 'timezone', 'serve'];
const SERVE_KEYS = ['command', 'url', 'cwd', 'env', 'timeout'];
const CHECK_KEYS = ['cases', 'env', 'maxDiffRatio'];

function expandVars(value, where, errors) {
  if (typeof value === 'string') {
    return value.replace(/\$\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (m, name) => {
      if (process.env[name] === undefined) {
        errors.push(`${where}: 環境変数 ${name} が設定されていません`);
        return '';
      }
      return process.env[name];
    });
  }
  if (Array.isArray(value)) return value.map((v, i) => expandVars(v, `${where}[${i}]`, errors));
  if (isPlainObject(value)) return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, expandVars(v, `${where}.${k}`, errors)]));
  return value;
}

function findConfig(explicit, cwd = process.cwd()) {
  if (explicit) return path.resolve(cwd, explicit);
  for (const name of CONFIG_NAMES) {
    const p = path.join(cwd, name);
    if (fs.existsSync(p)) return p;
  }
  return null;
}

function originOf(u) {
  try { return new URL(u).origin; } catch (_) { return u; }
}

const strList = (v) => (v === undefined ? [] : Array.isArray(v) ? v : [v]);

function unknownKeys(obj, allowed, where, errors) {
  for (const k of Object.keys(obj)) if (!allowed.includes(k)) errors.push(`${where}: 知らないキー「${k}」（使えるのは ${allowed.join(', ')}）`);
}

// serve: "npm start" か { command, url, cwd, env, timeout }。url が無ければ baseUrl を待つ。
function normalizeServe(raw, where, dir, baseUrl, errors) {
  if (raw === undefined || raw === null || raw === false) return null;
  const s = typeof raw === 'string' ? { command: raw } : raw;
  if (!isPlainObject(s)) { errors.push(`${where}: 起動のコマンド（文字列）か { command, url } を書きます`); return null; }
  unknownKeys(s, SERVE_KEYS, where, errors);
  if (typeof s.command !== 'string' || !s.command.trim()) errors.push(`${where}.command: 起動のコマンドを書きます（例: npm start）`);
  if (s.url !== undefined && (typeof s.url !== 'string' || !s.url.trim())) {
    errors.push(`${where}.url: URL を文字列で書きます`);
  }
  if (s.cwd !== undefined && (typeof s.cwd !== 'string' || !s.cwd.trim())) {
    errors.push(`${where}.cwd: 作業フォルダのパスを文字列で書きます`);
  }
  const url = typeof s.url === 'string' && s.url.trim() ? s.url : baseUrl;
  if (!url) errors.push(`${where}.url: 起動したことを確かめる URL を書きます（baseUrl があれば省略可）`);
  if (s.env !== undefined && !isPlainObject(s.env)) errors.push(`${where}.env: 環境変数の対応を書きます`);
  if (s.timeout !== undefined && !(Number.isFinite(s.timeout) && s.timeout > 0)) errors.push(`${where}.timeout: 待つミリ秒を数で書きます`);
  const cwd = typeof s.cwd === 'string' && s.cwd.trim() ? s.cwd : '.';
  return { command: s.command, url, cwd: path.resolve(dir, cwd), env: s.env || {}, timeout: s.timeout || 60000 };
}

function normalizeCheck(raw, dir, errors) {
  if (raw === undefined) return null;
  if (!isPlainObject(raw)) { errors.push('check: { cases } を書きます'); return null; }
  unknownKeys(raw, CHECK_KEYS, 'check', errors);
  const cases = strList(raw.cases);
  if (!cases.every((x) => typeof x === 'string')) errors.push('check.cases: パスの配列を書きます');
  if (raw.maxDiffRatio !== undefined && !(typeof raw.maxDiffRatio === 'number' && raw.maxDiffRatio >= 0 && raw.maxDiffRatio < 1)) {
    errors.push('check.maxDiffRatio: 前回の画面と違ってよい画素の割合を 0 以上 1 未満で書きます（既定 0。例: 0.001）');
  }
  return {
    cases: cases.map((c) => path.resolve(dir, String(c))),
    env: raw.env,
    maxDiffRatio: raw.maxDiffRatio === undefined ? 0 : raw.maxDiffRatio,
  };
}

// 環境を 1 つ選んで返す。設定ファイルが無ければ既定の local（何も上書きしない）。
// 戻り値: { name, settings, file, dir, serve, check, evidence }
function loadEnv({ configPath, envName, cwd } = {}) {
  const file = findConfig(configPath, cwd);
  if (!file) {
    if (configPath) throw new Error(`設定ファイルがありません: ${configPath}`);
    return { name: envName || 'local', settings: {}, file: null, dir: null, serve: null, check: null,
      evidence: path.resolve(cwd || process.cwd(), DEFAULT_EVIDENCE) };
  }
  const text = fs.readFileSync(file, 'utf8');
  const data = file.endsWith('.json') ? JSON.parse(text) : YAML.parse(text);
  if (!isPlainObject(data) || !isPlainObject(data.envs)) throw new Error(`${file}: envs に環境ごとの設定を書きます`);
  const dir = path.dirname(file);
  const errors = [];
  unknownKeys(data, TOP_KEYS, file, errors);
  const top = expandVars({ serve: data.serve, check: data.check, evidence: data.evidence }, file, errors);
  if (top.evidence !== undefined && (typeof top.evidence !== 'string' || !top.evidence.trim())) {
    errors.push('evidence: 結果を書き出すファイルのパスを書きます');
  }
  const check = normalizeCheck(top.check, dir, errors);
  const name = envName || (check && check.env) || data.defaultEnv || Object.keys(data.envs)[0];
  const raw = data.envs[name];
  if (!isPlainObject(raw)) throw new Error(`${file}: 環境「${name}」がありません（あるのは ${Object.keys(data.envs).join(', ')}）`);
  unknownKeys(raw, ENV_KEYS, `${file}: envs.${name}`, errors);
  const settings = expandVars(raw, `envs.${name}`, errors);
  if (settings.storageState !== undefined) {
    if (typeof settings.storageState !== 'string' || !settings.storageState.trim()) {
      errors.push(`envs.${name}.storageState: 保存済み認証状態のファイルパスを文字列で書きます`);
    } else {
      settings.storageState = path.resolve(dir, settings.storageState);
    }
  }
  // 起動は環境ごとの serve が優先（serve: false で起動しない）。全体の serve は、接続先が無いか、
  // 接続先が serve の URL と同じ origin の環境にだけ効く（検証環境を動かすときにローカルを起動しない）。
  const own = settings.serve !== undefined;
  const serveRaw = own ? settings.serve : top.serve;
  delete settings.serve;
  let serve = normalizeServe(serveRaw, own ? `envs.${name}.serve` : 'serve', dir, settings.baseUrl, errors);
  if (serve && !own && settings.baseUrl && originOf(settings.baseUrl) !== originOf(serve.url)) serve = null;
  if (serve && !settings.baseUrl) settings.baseUrl = serve.url;
  if (errors.length) throw new Error(errors.join('\n'));
  return { name, settings, file, dir, serve, check, evidence: path.resolve(dir, top.evidence || DEFAULT_EVIDENCE) };
}

module.exports = { loadEnv, findConfig, expandVars, CONFIG_NAMES, DEFAULT_EVIDENCE };
