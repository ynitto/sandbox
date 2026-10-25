'use strict';
const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');
const { parseArgs } = require('util');
const { defaults, loadConfig, severityOf } = require('../config');
const { globSync, isGlob, toRegex } = require('../glob');
const { display } = require('../audit');
const { RENDERERS } = require('../report');
const { writeEvidence } = require('../evidence');
const { SEVERITY_ORDER, RULES } = require('../rules');
const { analyze } = require('./analyze');
const { annotateTemporary, temporaryFindings, temporaryInputs } = require('../temporary');

const EXT = /\.(?:[cm]?[jt]sx?|py)$/i;
const NAMED = /(?:\.(?:test|spec)\.[cm]?[jt]sx?|(?:^|[\\/])test_[^/\\]+\.py|_test\.py)$/i;
const SKIP = new Set(['node_modules', 'dist', 'build', 'coverage', '__pycache__']);
const fail = (message) => Object.assign(new Error(message), { exitCode: 2 });

function filesFor(inputs, cfg) {
  const found = new Set();
  const excludes = cfg.code.exclude.map((pattern) => new RegExp(toRegex(path.resolve(cfg.dir, pattern.endsWith('/') ? `${pattern}**` : pattern).split(path.sep).join('/'))));
  const excluded = (p) => excludes.some((r) => r.test(path.resolve(p).split(path.sep).join('/')));
  function add(p, explicit) {
    if (excluded(p)) return;
    const stat = fs.statSync(p);
    if (stat.isDirectory()) {
      for (const entry of fs.readdirSync(p, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name, 'en'))) {
        if (entry.name.startsWith('.') || SKIP.has(entry.name) || entry.isSymbolicLink()) continue;
        const child = path.join(p, entry.name);
        if (entry.isDirectory()) add(child, false);
        else if (NAMED.test(child)) add(child, false);
      }
    } else if (explicit || NAMED.test(p)) found.add(path.resolve(p));
  }
  for (const input of inputs) {
    if (isGlob(input)) {
      const hits = globSync(input).filter((p) => EXT.test(p));
      if (!hits.length) throw fail(`テストコードに当てはまるファイルがありません: ${input}`);
      hits.forEach((p) => add(p, true));
    } else add(input, true);
  }
  return [...found].sort();
}

function readIR(filename) {
  const data = JSON.parse(fs.readFileSync(filename, 'utf8'));
  const Ajv = require('ajv');
  const validate = new Ajv({ allErrors: true, allowUnionTypes: true }).addSchema(require('../../schema/testcase.schema.json'), 'testcase.schema.json')
    .compile(require('../../schema/test-code.schema.json'));
  if (!validate(data)) throw fail(`テストコードの中間表現が書式と違います: ${JSON.stringify(validate.errors[0])}`);
  return data;
}

async function codeCheck(args, io) {
  const { values, positionals } = parseArgs({ args, allowPositionals: true, options: {
    config: { type: 'string', short: 'c' }, format: { type: 'string', short: 'f', default: 'text' },
    out: { type: 'string', short: 'o' }, evidence: { type: 'string' },
    'fail-on': { type: 'string' }, 'min-severity': { type: 'string' }, globals: { type: 'boolean' },
    'allow-incomplete': { type: 'boolean' }, ir: { type: 'string' },
  } });
  if (!RENDERERS[values.format]) throw fail('--format は text / json / markdown です');
  if (values['fail-on'] && !['error', 'warning', 'info', 'never'].includes(values['fail-on'])) throw fail('--fail-on は error / warning / info / never です');
  if (values['min-severity'] && !(values['min-severity'] in SEVERITY_ORDER)) throw fail('--min-severity は info / warning / error です');
  if (values.ir && positionals.length) throw fail('--ir とテストコードのパスは同時に指定できません');
  const cfg = loadConfig(values.config, { required: Boolean(values.config) });
  cfg.code ||= defaults().code;
  if (values.globals != null) cfg.code.globals = values.globals;
  let tests = []; let diagnostics = []; let files = [];
  const inputs = positionals.length ? positionals : cfg.code.files.length ? cfg.code.files : [process.cwd()];
  if (values.ir) {
    const data = readIR(values.ir);
    tests = data.tests;
    diagnostics = data.diagnostics || [];
    files = [...new Set([...tests, ...diagnostics].map((t) => t.file))].sort();
    for (const t of tests) if (!t.comparable && !t.trivial && !diagnostics.some((d) => d.file === t.file && d.line === t.line)) {
      diagnostics.push({ file: t.file, line: t.line, reason: '入力アダプターがこのテストを比較対象外としています' });
    }
  } else {
    const paths = filesFor(inputs, cfg);
    if (!paths.length) throw fail('点検するテストコードがありません。パス・globまたはcode.filesを指定してください');
    files = paths.map(display);
    for (const filename of paths) {
      const file = display(filename);
      try {
        if (!EXT.test(filename)) throw fail('対応する言語は JavaScript / TypeScript / Python です。他の言語は --ir で中間表現を渡せます');
        if (fs.statSync(filename).size > cfg.code.maxBytes) throw fail(`ファイルが code.max_file_bytes (${cfg.code.maxBytes}) を超えています`);
        const source = fs.readFileSync(filename, 'utf8');
        let result;
        if (/\.py$/i.test(filename)) {
          const proc = spawnSync(cfg.code.python, ['-I', '-S', '-X', 'utf8', path.join(__dirname, 'python.py')], {
            input: JSON.stringify({ source, file }), encoding: 'utf8', timeout: 15000, maxBuffer: 16 * 1024 * 1024,
          });
          if (proc.error || proc.status !== 0) throw fail(`Python AST アダプターを動かせません: ${proc.error?.message || proc.stderr}`);
          result = JSON.parse(proc.stdout);
        } else result = require('./javascript').javascript(source, file, filename, cfg.code);
        annotateTemporary(result.tests, result.comments || [], source);
        const directives = new Map();
        source.split('\n').forEach((line, i) => {
          if (!/^\s*(?:\/\/|#)\s*testcase-lint-code-ignore-next-line\b/.test(line)) return;
          const match = line.match(/^\s*(?:\/\/|#)\s*testcase-lint-code-ignore-next-line\s+(K0[1-5](?:\s*,\s*K0[1-5])*)\s*:\s*(\S.*)$/);
          if (!match) result.diagnostics.push({ file, line: i + 1, reason: '抑制コメントにはK01〜K05の規則と空でない理由が必要です' });
          else directives.set(i + 2, Object.fromEntries(match[1].split(',').map((code) => [code.trim(), match[2].trim()])));
        });
        for (const t of result.tests) {
          t.suppressions = directives.get(t.line) || {};
          for (const row of t.rows || []) row.suppressions = directives.get(row.line) || {};
        }
        tests.push(...result.tests);
        diagnostics.push(...result.diagnostics);
        if (!result.tests.some((t) => t.body)) diagnostics.push({ file, line: 0, reason: '認識できるテストがありません。import・code.globals・動的なテスト登録を確認してください' });
      } catch (e) {
        diagnostics.push({ file, line: e.loc?.line || 0, reason: e.message });
      }
    }
  }
  // 同じ未解析位置を、親suiteと個々の宣言から二重に報告しない。
  diagnostics = [...new Map(diagnostics.map((d) => [JSON.stringify([d.file, d.line, d.reason]), d])).values()];
  const complete = diagnostics.length === 0;
  const allFindings = [...analyze(tests, diagnostics, cfg), ...temporaryFindings(tests).flatMap((f) => {
    const severity = severityOf(cfg, f.code);
    return severity ? [{ ...f, severity, category: RULES[f.code][0], name: RULES[f.code][2] }] : [];
  })];
  const minSeverity = values['min-severity'] || cfg.minSeverity;
  const findings = allFindings.filter((f) => SEVERITY_ORDER[f.severity] >= SEVERITY_ORDER[minSeverity]);
  const metadata = tests.filter((t) => t.body).map((t) => ({ file: t.file, line: t.line, title: t.fullTitle, framework: t.framework, comparable: t.comparable,
    ...(Object.prototype.hasOwnProperty.call(t, 'temporary') ? { temporary: t.temporary } : {}),
    ...(t.temporarySignals?.length ? { temporarySignals: t.temporarySignals } : {}) }));
  const result = { files, cases: metadata, findings, spec: null, source: null,
    analysis: { mode: 'code', complete, tests: metadata.length, comparable: tests.filter((t) => t.comparable && !t.trivial).length, diagnostics,
      suppressions: tests.flatMap((t) => [t, ...(t.rows || [])].filter((m) => m.suppressions && Object.keys(m.suppressions).length)
        .map((m) => ({ file: t.file, line: m.line, rules: m.suppressions }))) },
    inputs: { config: cfg.file && display(cfg.file), auto: false, cases: values.ir ? [values.ir] : inputs, spec: [], source: [] } };
  const outputs = [values.out, values.evidence || cfg.code.evidence].filter(Boolean);
  const protectedPaths = [...files.map((p) => path.resolve(p)), ...temporaryInputs(tests), ...(values.ir ? [path.resolve(values.ir)] : [])];
  if (cfg.file) protectedPaths.push(path.resolve(cfg.file));
  if (outputs.some((p) => protectedPaths.includes(path.resolve(p)))) throw fail('点検する入力ファイルへは出力できません');
  if (outputs.length > 1 && path.resolve(outputs[0]) === path.resolve(outputs[1])) throw fail('レポートとevidenceの出力先は別にしてください');
  const report = RENDERERS[values.format](result);
  if (values.out) {
    fs.mkdirSync(path.dirname(path.resolve(values.out)), { recursive: true });
    fs.writeFileSync(values.out, report);
    io.err.write(`testcase-lint: ${values.out} に書き出しました\n`);
  } else io.out.write(report);
  const failOn = values['fail-on'] || cfg.failOn;
  const evidence = values.evidence || cfg.code.evidence;
  if (evidence) writeEvidence(evidence, result, { prefix: 'testcase-lint/code', failOn });
  if (!metadata.length || (!complete && !values['allow-incomplete'])) return 2;
  return failOn !== 'never' && allFindings.some((f) => SEVERITY_ORDER[f.severity] >= SEVERITY_ORDER[failOn]) ? 1 : 0;
}

module.exports = { codeCheck, filesFor };
