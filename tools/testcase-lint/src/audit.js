'use strict';
// 点検の本体。ケース・仕様・実装を読み、規則を当てて指摘を返す。

const fs = require('fs');
const path = require('path');
const tx = require('./text');
const { FieldMap, loadFile, lineAt } = require('./load');
const { Findings } = require('./findings');
const { Lint, messageVariants } = require('./lint');
const { Validity } = require('./validity');
const { Ambiguity } = require('./ambiguity');
const { SpecCheck } = require('./specCheck');
const { loadSpec } = require('./spec');
const { loadSource, checkSource } = require('./source');
const { RULES, SEVERITY_ORDER } = require('./rules');
const { severityOf } = require('./config');
const { globSync, isGlob } = require('./glob');

const EXTS = /\.(json|ya?ml|md)$/i;

// 仕様・実装に渡したパスの glob を、当てはまるファイルにする（フォルダ・ファイルはそのまま）
function expandPaths(inputs) {
  return inputs.flatMap((p) => (isGlob(p) ? globSync(p) : [p]));
}

function display(p) {
  const rel = path.relative(process.cwd(), p);
  return (rel && !rel.startsWith('..') && !path.isAbsolute(rel) ? rel : p).split(path.sep).join('/');
}

// ファイル・フォルダ・glob を、点検するファイルの列にする
function expand(inputs) {
  const out = [];
  for (const input of inputs) {
    if (isGlob(input)) {
      const hits = globSync(input).filter((p) => EXTS.test(p) && !/(^|[\\/])testcase-lint\.config\./.test(p));
      if (!hits.length) throw Object.assign(new Error(`当てはまるファイルがありません: ${input}`), { exitCode: 2 });
      out.push(...hits);
      continue;
    }
    let stat;
    try { stat = fs.statSync(input); } catch { throw Object.assign(new Error(`ありません: ${input}`), { exitCode: 2 }); }
    if (stat.isDirectory()) {
      const walk = (d) => {
        for (const e of fs.readdirSync(d, { withFileTypes: true }).sort((a, b) => (a.name < b.name ? -1 : 1))) {
          if (e.name.startsWith('.') || e.name === 'node_modules') continue;
          const p = path.join(d, e.name);
          if (e.isDirectory()) walk(p); else if (/\.(json|ya?ml)$/i.test(e.name) && !/^testcase-lint\.config\./.test(e.name)) out.push(p);
        }
      };
      walk(input);
    } else out.push(input);
  }
  return [...new Set(out)];
}

// 指定した書式（JSON Schema）に合うか
function schemaFindings(schemaFile, loaded, f) {
  const Ajv = require('ajv');
  const schema = JSON.parse(JSON.stringify(require('yaml').parse(fs.readFileSync(schemaFile, 'utf8'))));
  const ajv = new Ajv({ allErrors: true, strict: false });
  const validate = ajv.compile(schema);
  for (const { file, docs, trees } of loaded) {
    docs.forEach((doc, k) => {
      if (validate(doc)) return;
      for (const e of validate.errors.slice(0, 50)) {
        const segs = e.instancePath.split('/').slice(1).map((s) => s.replace(/~1/g, '/').replace(/~0/g, '~'));
        const where = e.instancePath || '(全体)';
        const extra = e.params && e.params.missingProperty ? `「${e.params.missingProperty}」がありません` : e.params && e.params.additionalProperty ? `「${e.params.additionalProperty}」は書式にない項目です` : e.message;
        f.add('D16', `${where}: ${extra}`, { file, line: lineAt(trees[k], segs), hint: `書式（${path.relative(process.cwd(), schemaFile).split(path.sep).join('/')}）に合わせる` });
      }
    });
  }
}

// 参考の指摘が 1 つのファイルで同じ規則に 10 件を超えたら、1 件にまとめる（ファイル全体の書き方の癖なので）
const COLLAPSE_OVER = 10;
function collapse(findings) {
  const groups = new Map();
  for (const f of findings) {
    if (f.severity !== 'info' || !f.cases.length) continue;
    const k = `${f.file}\u0001${f.code}`;
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(f);
  }
  const drop = new Set();
  const out = [];
  for (const fs_ of groups.values()) {
    if (fs_.length <= COLLAPSE_OVER) continue;
    for (const f of fs_) drop.add(f);
    const first = fs_[0];
    out.push({ ...first, message: `${first.name}: ${fs_.length} か所で同じ指摘があります（${fs_.slice(0, 5).map((f) => f.cases[0]).join(', ')} ほか）`, cases: fs_.map((f) => f.cases[0]), related: [], excerpt: '' });
  }
  return [...findings.filter((f) => !drop.has(f)), ...out];
}

// opts: { cases: [path], spec: [dir], source: [dir], config }
async function audit(opts) {
  const cfg = opts.config;
  const files = expand(opts.cases);
  if (!files.length) throw Object.assign(new Error('点検する .json / .yaml が見つかりません'), { exitCode: 2 });
  await tx.init();
  const fmap = new FieldMap(cfg.format.fields);
  const f = new Findings();
  const cases = []; const loaded = [];
  for (const p of files) {
    const r = loadFile(p, display(p), fmap, cases.length);
    cases.push(...r.cases);
    for (const x of r.findings) f.add(x.code, x.message, { file: x.file, line: x.line, hint: x.hint });
    loaded.push({ file: display(p), docs: r.docs, trees: r.trees || [] });
  }
  if (cfg.format.schema) schemaFindings(cfg.format.schema, loaded, f);

  const specPaths = expandPaths(opts.spec); const sourcePaths = expandPaths(opts.source);
  const spec = opts.spec.length ? loadSpec(specPaths, { requirementId: cfg.requirementId, display }) : null;
  const source = opts.source.length ? loadSource(sourcePaths) : null;
  const ctx = { cases, config: cfg, findings: f, spec, source, requirements: spec ? spec.requirements : null };
  if (spec) {
    ctx.specBoundaryKeys = new Set(spec.fields.flatMap((fld) => [['min', fld.min], ['max', fld.max]].filter(([, v]) => v != null && fld.unit).map(([kind, v]) => `${tx.normalize(fld.name)}|${fld.unit}|${kind}|${Math.round(v)}`)));
  }
  const lint = new Lint(ctx);
  ctx.isNegative = (c) => lint.isNegative(c);
  lint.run();
  new Ambiguity(ctx).run();
  new Validity(ctx).run();
  let untouched = [];
  if (spec) {
    const sc = new SpecCheck(ctx);
    sc.run();
    untouched = spec.files.filter((x) => !sc.inScope.has(x));
  }
  if (source) checkSource(ctx);
  messageVariants(ctx);

  const floor = SEVERITY_ORDER[opts.minSeverity || 'info'];
  const findings = [];
  for (const x of f.list) {
    const sev = severityOf(cfg, x.code);
    if (!sev || SEVERITY_ORDER[sev] < floor) continue;
    findings.push({ ...x, severity: sev, category: RULES[x.code][0], name: RULES[x.code][2] });
  }
  return {
    findings: collapse(findings), cases, files: files.map(display),
    spec: spec && { files: spec.files.length, untouched, requirements: spec.requirements.length, fields: spec.fields.length, machines: spec.machines.length, permissions: spec.permissions.length, endpoints: spec.endpoints.length, messages: spec.messages.length, errors: spec.errors },
    source: source && { files: source.files, errors: source.errors },
  };
}

module.exports = { audit, expand, expandPaths, display };
