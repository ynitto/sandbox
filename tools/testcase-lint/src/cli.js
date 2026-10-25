'use strict';

const fs = require('fs');
const path = require('path');
const { parseArgs } = require('util');
const { loadConfig, autodetect, defaults, DEFAULT_NAME } = require('./config');
const { audit, display } = require('./audit');
const { RENDERERS } = require('./report');
const { writeEvidence } = require('./evidence');
const { loadSpec } = require('./spec');
const { RULES, SEVERITY_LABEL, SEVERITY_ORDER } = require('./rules');
const tx = require('./text');
const { version } = require('../package.json');

const USAGE = `testcase-lint — 自然文のテストケースを、テストケースとしての妥当性で点検する（LLM は使わない）

ふだんはオプション無しで実行する:
  testcase-lint
      設定ファイル（testcase-lint.config.yaml。今のフォルダから親へさかのぼって探す）に書いたケース・仕様・実装を
      点検する。設定ファイルが無ければ、よくあるフォルダ（tests/cases・docs/specs・src など）を探して使う

  testcase-lint init [--cases <フォルダ>] [--spec <フォルダ>] [--source <フォルダ>]
      設定ファイルを作る（見つけたフォルダか、渡したフォルダを書く。既存の設定は上書きしない）

そのほか:
  testcase-lint [check] <ケースのファイル・フォルダ・glob ...> [オプション]
      その場で対象を渡す（init も設定ファイルも要らない）。glob は "qa/**/*.yaml" のように " で囲む
        --spec <パス>            仕様と突き合わせる（フォルダ・ファイル・glob。繰り返し可）
        --source <パス>          期待する文言が実装にあるかを見る（フォルダ・ファイル・glob。繰り返し可）
        -c, --config <file>     設定ファイル
        -f, --format <形式>      text（既定）/ json / markdown
        -o, --out <file>        結果をファイルに書く
        --evidence <file>       結果ファイル（version 1 の items 形式）も書く
        --fail-on <重さ>         この重さ以上があれば終了コード 1（error 既定 / warning / info / never）
        --min-severity <重さ>    この重さ未満は出さない（info 既定 / warning / error）
  testcase-lint spec [<フォルダ ...>]   仕様から何を読み取ったかを出す（突き合わせの前に確かめる）
  testcase-lint rules                  規則の一覧
  testcase-lint schema                 おすすめのケースの書式（JSON Schema）
  testcase-lint code [<テストコードのパス・glob ...>]
      JS/TS（importしたVitest・Jest・Node・Playwright）とPython（pytest・unittest）のASTを静的に比較する
        --globals              importのないdescribe / test / itを明示的に使う
        --allow-incomplete     未解析部分を表示しつつ終了を許す（0件は成功にしない）
        --ir <file.json>       別の言語アダプターの中間表現を読む（schema/test-code.schema.json）
      -c / -f / -o / --evidence / --fail-on / --min-severity も使える。設定は code.files / code.exclude / code.evidence

終了コード: 0 = 落とす重さの指摘なし / 1 = あり / 2 = 使い方の誤り・点検するものが無い
`;

function out(io, s) { io.out.write(s); }

async function main(argv, io = { out: process.stdout, err: process.stderr }) {
  try {
    const sub = ['check', 'code', 'spec', 'rules', 'schema', 'init'].includes(argv[0]) ? argv[0] : 'check';
    const rest = argv[0] === sub ? argv.slice(1) : argv;
    if (rest.includes('-h') || rest.includes('--help')) { out(io, USAGE); return 0; }
    if (rest.includes('--version')) { out(io, `testcase-lint ${version}\n`); return 0; }
    if (sub === 'init') return init(rest, io);
    if (sub === 'rules') return rules(io);
    if (sub === 'schema') return schema(io);
    if (sub === 'spec') return await specCmd(rest, io);
    if (sub === 'code') return await require('./code/check').codeCheck(rest, io);
    return await check(rest, io);
  } catch (e) {
    io.err.write(`testcase-lint: ${e.message}\n`);
    return e.exitCode || 2;
  }
}

// 設定ファイルを作る。既存のものは上書きしない
function init(args, io) {
  const { values, positionals } = parseArgs({ args, allowPositionals: true, options: { cases: { type: 'string', multiple: true }, spec: { type: 'string', multiple: true }, source: { type: 'string', multiple: true } } });
  const dir = path.resolve(positionals[0] || '.');
  const file = path.join(dir, DEFAULT_NAME);
  if (fs.existsSync(file)) { out(io, `設定ファイルはもうあります（上書きしません）: ${display(file)}\n`); return 0; }
  const found = autodetect(defaults(), dir);
  const rel = (p) => path.relative(dir, path.resolve(p)).split(path.sep).join('/') || '.';
  const pick = (given, auto) => (given && given.length ? given.map(rel) : auto.map(rel));
  const cases = pick(values.cases, found.cases); const spec = pick(values.spec, found.spec); const source = pick(values.source, found.source);
  const yamlList = (xs) => `[${xs.join(', ')}]`;
  const body = [
    '# testcase-lint の設定。このフォルダか下のフォルダで testcase-lint を実行すると読まれる。',
    '# パスはこのファイルからの相対。別のリポジトリのフォルダも書ける（例: ../my-app-docs/docs）。',
    `cases: ${yamlList(cases)}${cases.length ? '' : '   # ケースのフォルダを書く'}`,
    `spec: ${yamlList(spec)}${spec.length ? '' : '    # 仕様のフォルダ（要件・項目定義・状態遷移・権限・API・文言）'}`,
    `source: ${yamlList(source)}${source.length ? '' : '  # 実装のフォルダ（期待する文言が実装にあるか）'}`,
    '# evidence: results/testcase-lint.json   # 結果ファイルも書く（ほかの道具に渡す）',
    '# fail_on: error                         # この重さ以上があれば終了コード 1',
    '# format: { schema: schema/cases.schema.json }   # ケースが従う書式（JSON Schema）',
    '# rules: { D11: off }                    # 規則ごとに off / error / warning / info',
    '',
  ].join('\n');
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(file, body);
  out(io, `設定ファイルを作りました: ${display(file)}\n  ケース: ${cases.join('、') || '（なし。cases に書く）'}\n  仕様: ${spec.join('、') || '（なし）'}\n  実装: ${source.join('、') || '（なし）'}\nこれからは testcase-lint だけで点検できます。\n`);
  return 0;
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
      'fail-on': { type: 'string' }, 'min-severity': { type: 'string' },
    },
  });
  if (!RENDERERS[values.format]) throw Object.assign(new Error(`--format は text / json / markdown のどれかです: ${values.format}`), { exitCode: 2 });
  if (values['fail-on'] && !['error', 'warning', 'info', 'never'].includes(values['fail-on'])) throw Object.assign(new Error(`--fail-on は error / warning / info / never のどれかです`), { exitCode: 2 });
  if (values['min-severity'] && !(values['min-severity'] in SEVERITY_ORDER)) throw Object.assign(new Error(`--min-severity は info / warning / error のどれかです`), { exitCode: 2 });
  const cfg = loadConfig(values.config, { required: Boolean(values.config) });
  const cases = positionals.length ? positionals : cfg.cases;
  if (!cases.length) {
    throw Object.assign(new Error('点検するケースが見つかりません。testcase-lint init で設定ファイルを作り、cases にケースのフォルダを書いてください（その場で渡すなら testcase-lint <フォルダ>）'), { exitCode: 2 });
  }
  // 見つけたフォルダ（設定ファイルが無いとき）は、ケースも見つけたもののときだけ使う。
  // ケースをその場で渡したのに、今のフォルダの src などを勝手に実装として読まないため
  if (cfg.detected && positionals.length) { cfg.spec = []; cfg.source = []; }
  const failOn = values['fail-on'] || cfg.failOn;
  const minSeverity = values['min-severity'] || cfg.minSeverity;
  const result = await audit({
    cases, config: cfg, minSeverity,
    spec: [...(values.spec || []), ...cfg.spec], source: [...(values.source || []), ...cfg.source],
  });
  result.inputs = {
    config: cfg.file && display(cfg.file), auto: Boolean(cfg.detected) && !positionals.length,
    cases: cases.map((p) => display(path.resolve(p))),
    spec: [...(values.spec || []), ...cfg.spec].map((p) => display(path.resolve(p))),
    source: [...(values.source || []), ...cfg.source].map((p) => display(path.resolve(p))),
  };
  const text = RENDERERS[values.format](result);
  if (values.out) {
    fs.mkdirSync(path.dirname(path.resolve(values.out)), { recursive: true });
    fs.writeFileSync(values.out, text);
    io.err.write(`testcase-lint: ${values.out} に書き出しました\n`);
  } else out(io, text);
  const evidence = values.evidence || cfg.evidence;
  if (evidence) writeEvidence(evidence, result, { failOn });
  if (failOn === 'never') return 0;
  const limit = SEVERITY_ORDER[failOn];
  return result.findings.some((f) => SEVERITY_ORDER[f.severity] >= limit) ? 1 : 0;
}

module.exports = { main, USAGE };
