'use strict';

const fs = require('fs');
const path = require('path');
const { parseArgs } = require('util');
const { loadConfig } = require('./config');
const { audit, display } = require('./audit');
const { RENDERERS } = require('./report');
const { writeEvidence } = require('./evidence');
const { loadSpec } = require('./spec');
const { RULES, SEVERITY_LABEL, SEVERITY_ORDER } = require('./rules');
const tx = require('./text');
const { version } = require('../package.json');

const USAGE = `testcase-audit — 自然文のテストケースを、テストケースとしての妥当性で点検する（LLM は使わない）

使い方:
  testcase-audit [check] <ケースのファイル・フォルダ ...> [オプション]
      重複・矛盾・漏れ・不備と、判定の強さ・実行できるか・条件の組み合わせを点検する
        --spec <フォルダ>        仕様と突き合わせる（要件・項目定義・状態遷移・権限・API・文言。繰り返し可）
        --source <フォルダ>      期待する文言が実装にあるかを見る（繰り返し可）
        -c, --config <file>     設定ファイル（既定: 今のフォルダの testcase-audit.config.yaml）
        -f, --format <形式>      text（既定）/ json / markdown
        -o, --out <file>        結果をファイルに書く
        --evidence <file>       結果ファイル（version 1 の items 形式）も書く。ほかの道具に渡す
        --fail-on <重さ>         この重さ以上があれば終了コード 1（error 既定 / warning / info / never）
        --min-severity <重さ>    この重さ未満は出さない（info 既定 / warning / error）
  testcase-audit spec <フォルダ ...> [--json]
      仕様のフォルダから何を読み取ったかを出す（突き合わせの前に確かめる）
  testcase-audit rules
      規則の一覧
  testcase-audit schema
      おすすめのケースの書式（JSON Schema）のパスと中身

終了コード: 0 = 落とす重さの指摘なし / 1 = あり / 2 = 使い方の誤り
`;

function out(io, s) { io.out.write(s); }

async function main(argv, io = { out: process.stdout, err: process.stderr }) {
  try {
    const sub = ['check', 'spec', 'rules', 'schema'].includes(argv[0]) ? argv[0] : 'check';
    const rest = argv[0] === sub ? argv.slice(1) : argv;
    if (rest.includes('-h') || rest.includes('--help') || (!argv.length)) { out(io, USAGE); return argv.length ? 0 : 2; }
    if (rest.includes('--version')) { out(io, `testcase-audit ${version}\n`); return 0; }
    if (sub === 'rules') return rules(io);
    if (sub === 'schema') return schema(io);
    if (sub === 'spec') return await specCmd(rest, io);
    return await check(rest, io);
  } catch (e) {
    io.err.write(`testcase-audit: ${e.message}\n`);
    return e.exitCode || 2;
  }
}

function rules(io) {
  const lines = ['コード 分類 既定    規則'];
  for (const [code, [cat, sev, name]] of Object.entries(RULES)) lines.push(`${code}    ${cat}  ${SEVERITY_LABEL[sev].padEnd(3, '　')} ${name}`);
  out(io, `${lines.join('\n')}\n`);
  return 0;
}

function schema(io) {
  const file = path.join(__dirname, '..', 'schema', 'testcase.schema.json');
  out(io, `# ${file}\n${fs.readFileSync(file, 'utf8')}`);
  return 0;
}

async function specCmd(args, io) {
  const { values, positionals } = parseArgs({ args, allowPositionals: true, options: { json: { type: 'boolean' }, config: { type: 'string', short: 'c' } } });
  const cfg = loadConfig(values.config, { required: Boolean(values.config) });
  const dirs = positionals.length ? positionals : cfg.spec;
  if (!dirs.length) throw Object.assign(new Error('仕様のフォルダを指定してください'), { exitCode: 2 });
  await tx.init();
  const m = loadSpec(dirs, { requirementId: cfg.requirementId, display });
  if (values.json) {
    const { corpus, ...rest } = m;
    out(io, `${JSON.stringify(rest, null, 2)}\n`);
    return 0;
  }
  const lines = [`仕様 ${m.files.length} ファイル`];
  const at = (x) => `${x.file}${x.line ? `:${x.line}` : ''}`;
  lines.push('', `要件 ${m.requirements.length} 件`, ...m.requirements.map((r) => `  ${r.id}  ${r.text.slice(0, 50)}  (${at(r)})`));
  lines.push('', `項目 ${m.fields.length} 件`, ...m.fields.map((f) => `  ${f.name}  ${[f.required && '必須', (f.min != null || f.max != null) && `${f.min ?? ''}〜${f.max ?? ''}${f.unit}`, f.format, f.enum && `選択肢 ${f.enum.join('/')}`].filter(Boolean).join('・')}  (${at(f)})`));
  lines.push('', `状態遷移 ${m.machines.length} 件`, ...m.machines.flatMap((x) => [`  ${x.name} (${at(x)})`, ...x.transitions.map((t) => `    ${t.from} →（${t.event || '—'}）→ ${t.to}`)]));
  lines.push('', `権限表 ${m.permissions.length} 件`, ...m.permissions.map((p) => `  ${p.name}: ${p.roles.join('・')} × 操作 ${new Set(p.cells.map((c) => c.op)).size} (${at(p)})`));
  lines.push('', `API ${m.endpoints.length} 件`, ...m.endpoints.map((e) => `  ${e.method} ${e.path}  応答 ${e.codes.join('・') || '—'}  (${at(e)})`));
  lines.push('', `文言 ${m.messages.length} 件`, ...m.messages.slice(0, 30).map((x) => `  「${x.text}」 (${at(x)})`), ...(m.messages.length > 30 ? [`  …ほか ${m.messages.length - 30} 件`] : []));
  for (const e of m.errors) lines.push(`注意: ${e}`);
  out(io, `${lines.join('\n')}\n`);
  return 0;
}

async function check(args, io) {
  const { values, positionals } = parseArgs({
    args, allowPositionals: true,
    options: {
      spec: { type: 'string', multiple: true }, source: { type: 'string', multiple: true },
      config: { type: 'string', short: 'c' }, format: { type: 'string', short: 'f', default: 'text' },
      out: { type: 'string', short: 'o' }, evidence: { type: 'string' },
      'fail-on': { type: 'string', default: 'error' }, 'min-severity': { type: 'string', default: 'info' },
    },
  });
  if (!RENDERERS[values.format]) throw Object.assign(new Error(`--format は text / json / markdown のどれかです: ${values.format}`), { exitCode: 2 });
  if (!['error', 'warning', 'info', 'never'].includes(values['fail-on'])) throw Object.assign(new Error(`--fail-on は error / warning / info / never のどれかです`), { exitCode: 2 });
  if (!(values['min-severity'] in SEVERITY_ORDER)) throw Object.assign(new Error(`--min-severity は info / warning / error のどれかです`), { exitCode: 2 });
  const cfg = loadConfig(values.config, { required: Boolean(values.config) });
  const cases = positionals.length ? positionals : cfg.cases;
  if (!cases.length) throw Object.assign(new Error('点検するケースのファイルかフォルダを指定してください（設定の cases にも書ける）'), { exitCode: 2 });
  const result = await audit({
    cases, config: cfg, minSeverity: values['min-severity'],
    spec: [...(values.spec || []), ...cfg.spec], source: [...(values.source || []), ...cfg.source],
  });
  const text = RENDERERS[values.format](result);
  if (values.out) {
    fs.mkdirSync(path.dirname(path.resolve(values.out)), { recursive: true });
    fs.writeFileSync(values.out, text);
    io.err.write(`testcase-audit: ${values.out} に書き出しました\n`);
  } else out(io, text);
  if (values.evidence) writeEvidence(values.evidence, result, { failOn: values['fail-on'] });
  if (values['fail-on'] === 'never') return 0;
  const limit = SEVERITY_ORDER[values['fail-on']];
  return result.findings.some((f) => SEVERITY_ORDER[f.severity] >= limit) ? 1 : 0;
}

module.exports = { main, USAGE };
