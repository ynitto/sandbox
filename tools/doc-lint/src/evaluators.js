'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const { pathToFileURL } = require('node:url');

const packages = {
  textlint: ['@textlint/kernel', '@textlint/textlint-plugin-markdown', 'textlint-rule-preset-ja-technical-writing'],
  markdownlint: ['markdownlint'],
  mermaid: ['mermaid', 'jsdom'],
  plantuml: ['@plantuml/mcp-js', '@viz-js/viz'],
};

const bundledRoot = path.join(__dirname, '..');

function resolveFrom(base, name) {
  const resolve = createRequire(path.join(base, 'package.json')).resolve;
  try {
    return { file: resolve(name), resolve };
  } catch (error) {
    if (!['MODULE_NOT_FOUND', 'ERR_PACKAGE_PATH_NOT_EXPORTED'].includes(error.code)) throw error;
    // Some releases expose only the server bin, with no package entry point.
    if (name === '@plantuml/mcp-js') {
      for (const modules of resolve.paths(name)) {
        const file = path.join(modules, name, 'server.js');
        if (fs.existsSync(file)) return { file, resolve };
      }
    }
    return null;
  }
}

function resolvePackage(root, name) {
  const local = root === bundledRoot ? null : resolveFrom(root, name);
  if (local && !local.file.startsWith(path.join(bundledRoot, 'node_modules') + path.sep)) {
    return { ...local, source: 'repo' };
  }
  const bundled = resolveFrom(bundledRoot, name);
  return bundled ? { ...bundled, source: 'bundled' } : { source: 'missing' };
}

const textlintRulePackage = /^(?:@[a-z\d][\w.-]*\/)?textlint-rule-[a-z\d][\w.-]*$/i;

function resolveEvaluator(root, name, tools = []) {
  const dependencies = new Set(packages[name]);
  if (name === 'textlint') {
    for (const tool of tools.filter((pkg) => textlintRulePackage.test(pkg))) dependencies.add(tool);
  }
  return Object.fromEntries([...dependencies].map((pkg) => [pkg, resolvePackage(root, pkg)]));
}

function importFile(file) {
  return import(pathToFileURL(file).href);
}

function finding(file, evaluator, rule, severity, message, line = 1, column = 1) {
  return { file, line, column, severity, ruleId: `${evaluator}/${rule}`, message,
    source: { evaluator, rule } };
}

function expandTextlintPreset(id, preset) {
  const { rules, rulesConfig = {} } = preset;
  return Object.entries(rules).filter(([name]) => rulesConfig[name] !== false).map(([name, rule]) => ({
    ruleId: `${id}/${name}`,
    rule: rule.default ?? rule,
    options: rulesConfig[name],
  }));
}

const loaders = {
  async markdownlint(deps) {
    const { resolve, file } = deps.markdownlint;
    let api;
    try {
      api = resolve('markdownlint/sync');
    } catch (error) {
      if (!['MODULE_NOT_FOUND', 'ERR_PACKAGE_PATH_NOT_EXPORTED'].includes(error.code)) throw error;
      api = file;
    }
    const markdownlint = await importFile(api);
    const lint = markdownlint.lint ?? markdownlint.default?.sync;
    if (typeof lint !== 'function') throw new Error('markdownlint の同期 API がありません');
    return (text, file) => lint({ strings: { document: text } }).document.map((result) =>
      finding(file, 'markdownlint', result.ruleNames[0], 'warning',
        [result.ruleDescription, result.errorDetail].filter(Boolean).join(' '),
        result.lineNumber, result.errorRange?.[0] ?? 1));
  },

  async textlint(deps) {
    const { TextlintKernel } = require(deps['@textlint/kernel'].file);
    const plugin = require(deps['@textlint/textlint-plugin-markdown'].file);
    const rules = [];
    for (const [name, { file }] of Object.entries(deps)) {
      if (!textlintRulePackage.test(name)) continue;
      const ruleModule = await importFile(file);
      const exported = ruleModule.default ?? ruleModule;
      const rule = exported.default ?? exported;
      const ruleId = name.replace(/(^|\/)textlint-rule-/, '$1');
      if (path.posix.basename(ruleId).startsWith('preset-')) {
        rules.push(...expandTextlintPreset(ruleId, rule));
      } else {
        rules.push({ ruleId, rule });
      }
    }
    const kernel = new TextlintKernel();
    const options = {
      ext: '.md',
      plugins: [{ pluginId: 'markdown', plugin: plugin.default ?? plugin }],
      rules,
    };
    return async (text, file) => {
      const result = await kernel.lintText(text, { ...options, filePath: file });
      return result.messages.map((entry) =>
        finding(file, 'textlint', entry.ruleId || 'unknown', entry.severity === 2 ? 'error' : 'warning',
          entry.message, entry.line, entry.column));
    };
  },

  async mermaid(deps) {
    const { JSDOM } = require(deps.jsdom.file);
    const { window } = new JSDOM('');
    for (const name of ['window', 'document', 'DOMParser', 'Element', 'HTMLElement', 'SVGElement']) {
      globalThis[name] = window[name];
    }
    const { default: mermaid } = await importFile(deps.mermaid.file);
    mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', logLevel: 'fatal' });
    return async (text, file, offset = 0) => {
      try {
        await mermaid.parse(text);
        return [];
      } catch (error) {
        const message = error.message;
        const parserError = /(?:parse|lexical) error on line (\d+)/i.exec(message);
        if (!parserError && !error.hash &&
            !/^(?:No diagram type detected|Parsing failed|Lexing failed|UnknownDiagramError)/i.test(message) &&
            error.constructor.name !== 'UnknownDiagramError') throw error;
        const line = Number(parserError?.[1] ?? 1);
        return [finding(file, 'mermaid', 'syntax', 'error', message, offset + line)];
      }
    };
  },

  async plantuml(deps) {
    const viz = await importFile(deps['@viz-js/viz'].file);
    let instance;
    globalThis.Viz = { instance: () => (instance ??= viz.instance()) };
    const server = deps['@plantuml/mcp-js'].file;
    const engine = await withDiagnostics(() => importFile(path.join(path.dirname(server), 'engine.js')), true);
    if (typeof engine.checkSyntax !== 'function') throw new Error('PlantUML の構文検査 API がありません');
    return async (text, file, offset = 0) => {
      const result = JSON.parse(await withDiagnostics(() => engine.checkSyntax(text), true));
      if (result.valid === true) return [];
      if (result.valid !== false) throw new Error('PlantUML の検査結果が不正です');
      return [finding(file, 'plantuml', 'syntax', 'error', result.errorMessage || 'PlantUML syntax error',
        offset + Math.max(1, result.errorLineNumber || 1))];
    };
  },
};

async function withDiagnostics(operation, redirectStdout = false) {
  const methods = ['log', 'info', 'debug'];
  const originals = methods.map((name) => console[name]);
  for (const name of methods) console[name] = console.error;
  const stdoutWrite = process.stdout.write;
  try {
    if (redirectStdout) process.stdout.write = process.stderr.write.bind(process.stderr);
    return await operation();
  } finally {
    if (redirectStdout) process.stdout.write = stdoutWrite;
    methods.forEach((name, index) => { console[name] = originals[index]; });
  }
}

async function loadEvaluator(name, deps) {
  const missing = Object.keys(deps).filter((pkg) => deps[pkg].source === 'missing');
  if (missing.length) throw new Error(`${name} の依存が見つかりません: ${missing.join(', ')}`);
  try {
    return await loaders[name](deps);
  } catch (error) {
    throw new Error(`${name} の初期化に失敗しました: ${error.message}`, { cause: error });
  }
}

module.exports = { packages, resolvePackage, resolveEvaluator, loadEvaluator, withDiagnostics };
