'use strict';

const { engines } = require('../package.json');
const { spawnSync } = require('node:child_process');
const { findRoot, readConfig, updateTools } = require('./config');
const { packages, resolveEvaluator, loadEvaluator, withDiagnostics } = require('./evaluators');

const probes = {
  markdownlint: ['# Sample\n', 'sample.md'],
  textlint: ['設定を確認します。\n', 'sample.md'],
  mermaid: ['flowchart TD\n  A --> B\n', 'sample.mmd'],
  plantuml: ['@startuml\nAlice -> Bob\n@enduml\n', 'sample.puml'],
};

async function doctor(cwd) {
  const root = findRoot(cwd) ?? cwd;
  const minimum = engines.node.slice(2).split('.').map(Number);
  const current = process.versions.node.split('.').map(Number);
  const difference = current.map((part, index) => part - minimum[index]).find((part) => part !== 0);
  let ok = (difference ?? 0) >= 0;
  process.stdout.write(`${ok ? 'OK' : 'NG'} Node.js ${process.versions.node} (${engines.node})\n`);
  for (const name of Object.keys(packages)) {
    try {
      const deps = resolveEvaluator(root, name);
      for (const [pkg, { source }] of Object.entries(deps)) {
        process.stdout.write(`${source === 'missing' ? 'NG' : 'OK'} ${pkg} (${source})\n`);
      }
      await withDiagnostics(async () => {
        const lint = await loadEvaluator(name, deps);
        const findings = await lint(...probes[name]);
        if (findings.length) throw new Error(findings.map(({ message }) => message).join('; '));
      });
      process.stdout.write(`OK ${name}\n`);
    } catch (error) {
      process.stdout.write(`NG ${name}\n`);
      process.stderr.write(`${error.message}\n`);
      ok = false;
    }
  }
  return ok ? 0 : 2;
}

function npm(cwd, args) {
  const result = spawnSync('npm', args, { cwd, stdio: ['inherit', 2, 2], shell: process.platform === 'win32' });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`npm ${args[0]} に失敗しました (${result.status ?? result.signal})`);
}

function manageTools(cwd, action, name) {
  if (!/^(?:@[a-z\d][\w.-]*\/)?[a-z\d][\w.-]*$/i.test(name)) {
    throw new Error(`npm パッケージ名が不正です: ${name}`);
  }
  const root = findRoot(cwd);
  if (!root) throw new Error('設定が見つかりません。doc-lint init を実行してください。');
  readConfig(root);
  npm(root, action === 'add' ? ['install', '--save-dev', name] : ['uninstall', name]);
  updateTools(root, action, name);
}

module.exports = { doctor, npm, manageTools };
