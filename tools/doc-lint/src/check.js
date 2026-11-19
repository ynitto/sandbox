'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { name, version } = require('../package.json');
const { readConfig } = require('./config');
const { loadEvaluator, resolveEvaluator, withDiagnostics } = require('./evaluators');

const excluded = new Set(['node_modules', '.git', '.doc-lint']);
const extensions = new Map([
  ['.md', 'markdown'], ['.mmd', 'mermaid'], ['.puml', 'plantuml'], ['.plantuml', 'plantuml'],
]);
const diagramLanguages = new Map([
  ['mermaid', 'mermaid'], ['puml', 'plantuml'], ['plantuml', 'plantuml'],
]);

function collectFiles(targets) {
  const files = new Set();
  function visit(target, stat) {
    if (stat.isDirectory()) {
      for (const entry of fs.readdirSync(target, { withFileTypes: true })) {
        if (!excluded.has(entry.name)) visit(path.join(target, entry.name), entry);
      }
    } else if (stat.isFile() && extensions.has(path.extname(target).toLowerCase())) {
      files.add(target);
    }
  }
  for (const target of targets) {
    if (!target.split(path.sep).some((part) => excluded.has(part))) visit(target, fs.statSync(target));
  }
  return [...files].sort();
}

async function check(root, targets, options) {
  const config = readConfig(root);
  const evaluators = new Map();
  for (const [name, enabled] of Object.entries(config.evaluators)) {
    if (enabled) evaluators.set(name, await loadEvaluator(name, resolveEvaluator(root, name, config.tools)));
  }

  const parser = evaluators.has('mermaid') || evaluators.has('plantuml')
    ? require('markdown-it')('commonmark')
    : null;
  const files = collectFiles(targets);
  const findings = [];
  for (const target of files) {
    const file = path.relative(root, target).split(path.sep).join('/');
    const text = fs.readFileSync(target, 'utf8');
    const kind = extensions.get(path.extname(target).toLowerCase());
    async function evaluate(name, input, offset) {
      const lint = evaluators.get(name);
      if (!lint) return;
      try {
        findings.push(...await lint(input, file, offset));
      } catch (error) {
        throw new Error(`${file}: ${name} の実行に失敗しました: ${error.message}`, { cause: error });
      }
    }

    if (kind === 'markdown') {
      await evaluate('markdownlint', text);
      await evaluate('textlint', text);
      for (const token of parser?.parse(text, {}) ?? []) {
        if (token.type !== 'fence') continue;
        const language = token.info.trim().split(/\s+/, 1)[0].toLowerCase();
        const evaluator = diagramLanguages.get(language);
        if (evaluator) await evaluate(evaluator, token.content, token.map[0] + 1);
      }
    } else {
      await evaluate(kind, text);
    }
  }

  findings.sort((a, b) => a.file.localeCompare(b.file) || a.line - b.line || a.column - b.column);
  const errors = findings.filter(({ severity }) => severity === 'error').length;
  const warnings = findings.length - errors;
  const failOn = options['fail-on'] ?? config.failOn;
  const failed = failOn !== 'never' && (errors > 0 || (failOn === 'warning' && warnings > 0));
  return {
    report: { version: 1, tool: { name, version }, findings, summary: { filesChecked: files.length, errors, warnings } },
    code: failed ? 1 : 0,
  };
}

function formatReport(report, format) {
  const { findings, summary: { filesChecked, errors, warnings } } = report;
  if (format === 'stylish') {
    const lines = findings.map(({ file, line, column, severity, ruleId, message }) =>
      `${file}:${line}:${column}  ${severity}  ${ruleId}\n  ${message.replace(/\r?\n/g, '\n  ')}`);
    lines.push(`${filesChecked} files checked, ${errors} errors, ${warnings} warnings`);
    return `${lines.join('\n')}\n`;
  }
  if (format === 'sarif') {
    const rules = new Map();
    const results = findings.map(({ file, line, column, severity, ruleId, message }) => {
      rules.set(ruleId, { id: ruleId });
      return {
        ruleId, level: severity, message: { text: message },
        locations: [{ physicalLocation: {
          artifactLocation: { uri: file.split('/').map(encodeURIComponent).join('/') },
          region: { startLine: line, startColumn: column },
        } }],
      };
    });
    return JSON.stringify({
      $schema: 'https://schemastore.azurewebsites.net/schemas/json/sarif-2.1.0-rtm.5.json',
      version: '2.1.0',
      runs: [{
        tool: { driver: { name, version, rules: [...rules.values()] } },
        results,
      }],
    }, null, 2) + '\n';
  }
  return JSON.stringify(report, null, 2) + '\n';
}

module.exports = { check: (...args) => withDiagnostics(() => check(...args)), formatReport };
