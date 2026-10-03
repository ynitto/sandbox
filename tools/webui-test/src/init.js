'use strict';

const fs = require('fs');
const path = require('path');
const YAML = require('yaml');
const { findConfig } = require('./config');

// 設定だけを作る。アプリやケースの内容、ほかの道具の設定は推測しない。
function initConfig({ dir = '.', configPath, baseUrl = 'http://localhost:3000', serve, cases = ['tests/e2e'] } = {}) {
  const root = path.resolve(dir);
  const file = path.resolve(root, configPath || 'webui-test.config.yaml');
  if (!/\.(yaml|yml|json)$/.test(file)) throw new Error('設定の拡張子は .yaml / .yml / .json にしてください');
  const url = new URL(baseUrl);
  if (!['http:', 'https:'].includes(url.protocol)) throw new Error('--base-url は http / https の URL にしてください');
  if (serve !== undefined && !serve.trim()) throw new Error('--serve に起動コマンドを書いてください');
  if (!cases.length || cases.some((c) => !c.trim())) throw new Error('--cases にケースのパスを書いてください');
  const existing = configPath ? (fs.existsSync(file) ? file : null) : findConfig(undefined, root);
  if (existing) throw new Error(`設定は既にあります（上書きしません）: ${existing}`);
  const data = {
    defaultEnv: 'local',
    ...(serve === undefined ? {} : { serve: { command: serve, url: baseUrl } }),
    check: { cases },
    envs: { local: { baseUrl } },
  };
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, file.endsWith('.json') ? JSON.stringify(data, null, 2) + '\n' : YAML.stringify(data), { flag: 'wx' });
  return file;
}

module.exports = { initConfig };
