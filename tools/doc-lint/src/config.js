'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { dependencies } = require('../package.json');

const configPath = path.join('.doc-lint', 'config.yaml');
const failLevels = ['error', 'warning', 'never'];
const defaultConfig = {
  version: 1,
  preset: 'ja',
  failOn: 'error',
  evaluators: { textlint: true, markdownlint: true, mermaid: true, plantuml: true },
  tools: [],
};

function findRoot(start) {
  let root = path.resolve(start);
  if (!fs.statSync(root).isDirectory()) root = path.dirname(root);
  while (!fs.existsSync(path.join(root, configPath))) {
    const parent = path.dirname(root);
    if (parent === root) return null;
    root = parent;
  }
  return root;
}

function readConfig(root) {
  const { parse } = require('yaml');
  const file = path.join(root, configPath);
  const config = parse(fs.readFileSync(file, 'utf8'));
  if (config?.version !== 1 || config.preset !== 'ja' || !failLevels.includes(config.failOn) ||
      !config.evaluators || typeof config.evaluators !== 'object' ||
      Array.isArray(config.evaluators) ||
      Object.entries(config.evaluators).some(([name, enabled]) =>
        !Object.hasOwn(defaultConfig.evaluators, name) || typeof enabled !== 'boolean') ||
      !Array.isArray(config.tools) || config.tools.some((name) => typeof name !== 'string')) {
    throw new Error(`${file} の設定が不正です`);
  }
  return { ...config, evaluators: { ...defaultConfig.evaluators, ...config.evaluators } };
}

function init(root, force) {
  const { stringify } = require('yaml');
  const file = path.join(root, configPath);
  const manifestPath = path.join(root, 'package.json');
  const manifest = fs.existsSync(manifestPath)
    ? JSON.parse(fs.readFileSync(manifestPath, 'utf8'))
    : { private: true };

  const evaluatorPackages = [
    'textlint', 'markdownlint', 'mermaid', 'jsdom', '@plantuml/mcp-js',
    '@viz-js/viz', 'textlint-rule-preset-ja-technical-writing',
  ];
  manifest.devDependencies = { ...manifest.devDependencies };
  for (const name of evaluatorPackages) {
    manifest.devDependencies[name] ??= dependencies[name];
  }

  fs.mkdirSync(path.dirname(file), { recursive: true });
  try {
    fs.writeFileSync(file, stringify(defaultConfig), { flag: force ? 'w' : 'wx' });
  } catch (error) {
    if (error.code !== 'EEXIST') throw error;
  }
  fs.writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
}

function updateTools(root, action, name) {
  const { parseDocument } = require('yaml');
  const file = path.join(root, configPath);
  const document = parseDocument(fs.readFileSync(file, 'utf8'));
  if (document.errors.length) throw document.errors[0];
  const tools = new Set(document.get('tools').toJSON());
  if (action === 'add') tools.add(name);
  else tools.delete(name);
  document.set('tools', [...tools]);
  fs.writeFileSync(file, document.toString());
}

module.exports = { findRoot, readConfig, init, updateTools, failLevels };
