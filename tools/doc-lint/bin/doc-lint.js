#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { parseArgs } = require('node:util');
const { version } = require('../package.json');

const usage = `使い方:
  doc-lint init [--preset ja] [--force]
  doc-lint install [--frozen-lockfile]
  doc-lint doctor
  doc-lint tools add|remove <pkg>
  doc-lint check [path...] [--format stylish|json|sarif] [--output <file>] [--fail-on error|warning|never]
`;

function resolveOutput(root, output) {
  function assertInside(base, file) {
    const relative = path.relative(base, file);
    if (relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
      throw new Error(`--output はリポジトリルート内を指定してください: ${output}`);
    }
  }

  const file = path.resolve(root, output);
  assertInside(root, file);
  const realRoot = fs.realpathSync(root);
  const parent = fs.realpathSync(path.dirname(file));
  const destination = path.join(parent, path.basename(file));
  assertInside(realRoot, destination);
  if (!fs.lstatSync(destination, { throwIfNoEntry: false })) return destination;

  const realFile = fs.realpathSync(destination);
  assertInside(realRoot, realFile);
  return realFile;
}

async function main([command, ...args]) {
  if (!command || ['--help', '-h'].includes(command)) {
    process.stdout.write(usage);
    return 0;
  }
  if (command === '--version' && !args.length) {
    process.stdout.write(`doc-lint ${version}\n`);
    return 0;
  }

  const optionsByCommand = {
    init: { preset: { type: 'string', default: 'ja' }, force: { type: 'boolean' } },
    install: { 'frozen-lockfile': { type: 'boolean' } },
    doctor: {},
    tools: {},
    check: {
      format: { type: 'string', default: 'stylish' },
      output: { type: 'string' },
      'fail-on': { type: 'string' },
    },
  };
  if (!Object.hasOwn(optionsByCommand, command)) throw new Error(`不明なコマンド: ${command}`);
  const { values, positionals } = parseArgs({
    args,
    options: { ...optionsByCommand[command], help: { type: 'boolean', short: 'h' } },
    allowPositionals: command === 'check' || command === 'tools',
  });
  if (values.help) {
    process.stdout.write(usage);
    return 0;
  }

  const cwd = process.cwd();
  const { init, findRoot, failLevels } = require('../src/config');
  const { doctor, npm, manageTools } = require('../src/commands');
  switch (command) {
    case 'init':
      if (values.preset !== 'ja') throw new Error('使用できるプリセットは ja です');
      init(cwd, values.force);
      process.stdout.write('依存を入れるには doc-lint install を実行してください。\n');
      return 0;
    case 'install':
      npm(cwd, [values['frozen-lockfile'] ? 'ci' : 'install']);
      return 0;
    case 'doctor':
      return doctor(cwd);
    case 'tools': {
      const [action, name] = positionals;
      if (positionals.length !== 2 || !['add', 'remove'].includes(action)) {
        throw new Error('使い方: doc-lint tools add|remove <pkg>');
      }
      manageTools(cwd, action, name);
      return 0;
    }
    case 'check': {
      if (!['stylish', 'json', 'sarif'].includes(values.format)) throw new Error('--format が不正です');
      if (values['fail-on'] !== undefined && !failLevels.includes(values['fail-on'])) {
        throw new Error('--fail-on が不正です');
      }
      const targets = (positionals.length ? positionals : ['.']).map((target) => path.resolve(cwd, target));
      const roots = new Set(targets.map(findRoot));
      if (roots.has(null)) throw new Error('設定が見つかりません。doc-lint init を実行してください。');
      if (roots.size !== 1) throw new Error('同じ .doc-lint/config.yaml を使うパスを指定してください。');
      const [root] = roots;
      const destination = values.output === undefined ? undefined : resolveOutput(root, values.output);
      const { check, formatReport } = require('../src/check');
      const { report, code } = await check(root, targets, values);
      const output = formatReport(report, values.format);
      if (destination !== undefined) fs.writeFileSync(destination, output);
      else process.stdout.write(output);
      return code;
    }
  }
}

main(process.argv.slice(2)).then((code) => { process.exitCode = code; }).catch((error) => {
  process.stderr.write(`doc-lint: ${error.message}\n`);
  process.exitCode = 2;
});
