'use strict';
// 実行する環境（ローカル・検証環境など）の設定。web-test.config.yaml に環境ごとの接続先・認証・
// 事前に入れる値を書き、--env で切り替える。値の ${名前} は環境変数で置き換える（秘密をファイルに書かない）。
//
//   defaultEnv: local
//   envs:
//     local:   { baseUrl: http://localhost:3000 }
//     staging: { baseUrl: https://stg.example.com, mocks: false, storageState: auth/staging.json,
//                headers: { Authorization: "Bearer ${STAGING_TOKEN}" } }

const fs = require('fs');
const path = require('path');
const YAML = require('yaml');
const { isPlainObject } = require('./casefile');

const CONFIG_NAMES = ['web-test.config.yaml', 'web-test.config.yml', 'web-test.config.json'];
const ENV_KEYS = ['baseUrl', 'localStorage', 'sessionStorage', 'cookies', 'headers', 'storageState', 'mocks', 'locale', 'timezone'];

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

// 環境を 1 つ選んで返す。設定ファイルが無ければ既定の local（何も上書きしない）。
function loadEnv({ configPath, envName, cwd } = {}) {
  const file = findConfig(configPath, cwd);
  if (!file) {
    if (configPath) throw new Error(`設定ファイルがありません: ${configPath}`);
    return { name: envName || 'local', settings: {}, file: null };
  }
  const text = fs.readFileSync(file, 'utf8');
  const data = file.endsWith('.json') ? JSON.parse(text) : YAML.parse(text);
  if (!isPlainObject(data) || !isPlainObject(data.envs)) throw new Error(`${file}: envs に環境ごとの設定を書きます`);
  const name = envName || data.defaultEnv || Object.keys(data.envs)[0];
  const raw = data.envs[name];
  if (!isPlainObject(raw)) throw new Error(`${file}: 環境「${name}」がありません（あるのは ${Object.keys(data.envs).join(', ')}）`);
  const errors = [];
  for (const k of Object.keys(raw)) if (!ENV_KEYS.includes(k)) errors.push(`${file}: envs.${name}: 知らないキー「${k}」（使えるのは ${ENV_KEYS.join(', ')}）`);
  const settings = expandVars(raw, `envs.${name}`, errors);
  if (settings.storageState) settings.storageState = path.resolve(path.dirname(file), settings.storageState);
  if (errors.length) throw new Error(errors.join('\n'));
  return { name, settings, file };
}

module.exports = { loadEnv, findConfig, expandVars, CONFIG_NAMES };
