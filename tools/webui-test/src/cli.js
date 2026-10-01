'use strict';

const fs = require('fs');
const path = require('path');
const { parseArgs } = require('util');
const { spawn } = require('child_process');
const { loadFile, collectFiles } = require('./casefile');
const { loadEnv } = require('./config');
const { collect } = require('./context');
const { withServer } = require('./serve');

// このツールに入っている @playwright/test で、書き出したテストを動かす。
// 書き出し先が別の場所でも '@playwright/test' を解決できるよう NODE_PATH にこのツールの node_modules を足す。
function runPlaywrightTest(outDir, extra, { executablePath, captureRoot, io }) {
  const cli = require.resolve('@playwright/test/cli');
  const nodeModules = path.resolve(__dirname, '..', 'node_modules');
  const env = {
    ...process.env,
    NODE_PATH: [nodeModules, process.env.NODE_PATH].filter(Boolean).join(path.delimiter),
    WEBUI_TEST_CAPTURE_ROOT: path.resolve(captureRoot || '.'),
    ...(executablePath ? { WEBUI_TEST_EXECUTABLE_PATH: executablePath } : {}),
  };
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [cli, 'test', '--config', path.join(outDir, 'playwright.config.ts'), ...extra], { env, stdio: ['ignore', 'pipe', 'pipe'] });
    child.stdout.on('data', (d) => io.out.write(d));
    child.stderr.on('data', (d) => io.err.write(d));
    child.on('error', reject);
    child.on('close', resolve);
  });
}

const USAGE = `webui-test — 条件からテストケースを作り、Playwright で実行してスクリーンショット付きの結果を出す

使い方:
  webui-test generate "<条件>" -o tests/login.yaml [--agent kiro|copilot] [--url <最初に開くURL>]
      エージェントに条件を渡してテストケースファイルを作る。--url を渡すと、その画面の要素一覧も渡す
        -f, --conditions-file <file>  条件をファイルから読む
        --agent-cmd "<コマンド>"       kiro / copilot 以外の CLI（依頼ファイルを読む指示を最後の引数で渡す）
        --base-url <url>              ケースファイルの baseUrl
        --update                      -o のファイルを直す・足す（今の内容をエージェントに渡す）
        --no-snapshot                 画面の要素一覧を取らない
        --retries <n>                 書式の誤りを直してもらう回数（既定 1）
        --explore                     エージェントに playwright-cli で画面を操作・探索させてから書かせる
                                      （画面をまたぐ条件向け。--url が要る）
        --doc <file>                  仕様書。中身を依頼に入れ、ケースファイルに「coherence: doc=」の注記を書く（繰り返し可）
        --code <file>                 対象の実装。ケースファイルに「coherence: code=」の注記を書く（繰り返し可）
        --verbose                     エージェントの出力をそのまま表示する
  webui-test run <ファイルかディレクトリ>... [--base-url <url>] [--out <dir>]
      テストケースを実行し、<out>/<日時>/report.html・report.md・results.json とスクリーンショットを書く
        --workers <n>                 同時に動かすケース数（既定 1）
        --only <ID,...>               指定した ID（前方一致）のケースだけ
        --screenshot step|failure|off ファイルの screenshot 設定を上書き
        --capture-root <dir>          screenshot ステップの path: の起点（既定はカレントディレクトリ）
        --variant <name,...>          variants のうち指定したものだけ
        --headed                      ブラウザを表示して動かす
        --source <dir>                仕様・実装の置き場。実行記録にそのコミットを残す（繰り返し可）
  webui-test capture <ファイル>... --out <dir> [--base-url <url>]
      仕様書用。screenshot ステップの画像だけを <dir>/<name>.png に書き出す（レポートは作らない）
  webui-test export <ファイルかディレクトリ>... --out <dir>
      Playwright Test の .spec.ts と playwright.config.ts を書き出す（npx playwright test で動く）
  webui-test pwtest <ファイルかディレクトリ>... [--out <dir>] [-- <playwright test の引数>]
      書き出してそのまま npx playwright test で動かす（既定の書き出し先 webui-test-results/playwright）
  webui-test check [<ファイルかディレクトリ>...] [--update]
      実装・テスト・仕様書の画像の整合を確かめる（webui-test.config.yaml の check と serve を使う）。
      アプリをローカルで起動して e2e → 仕様書の画像が今の画面と同じか → 仕様書の画像のリンク
        --update                      違っていた仕様書の画像を撮り直す
  webui-test validate <ファイルかディレクトリ>...   書式を検査する
  webui-test prompt "<条件>" [--url <url>]           エージェントへ渡す依頼文を表示する（チャットに貼る用）
  webui-test snapshot <url>                          画面の要素一覧（アクセシビリティツリー）を表示する
  webui-test format                                  テストケースファイルの書式を表示する

共通:
  --env <name>              webui-test.config.yaml の環境（接続先・認証・事前の値）を選ぶ
  --config <file>           環境の設定ファイル（既定はカレントディレクトリの webui-test.config.yaml）
  --executable-path <path>  使う Chromium の実行ファイル（環境変数 WEBUI_TEST_EXECUTABLE_PATH でも可）
終了コード: 0 = すべて合格 / 1 = 不合格あり / 2 = 使い方・書式の誤り
`;

const OPTIONS = {
  help: { type: 'boolean', short: 'h' },
  out: { type: 'string', short: 'o' },
  'base-url': { type: 'string' },
  workers: { type: 'string' },
  only: { type: 'string' },
  screenshot: { type: 'string' },
  'capture-root': { type: 'string' },
  headed: { type: 'boolean' },
  agent: { type: 'string' },
  'agent-cmd': { type: 'string' },
  url: { type: 'string' },
  'conditions-file': { type: 'string', short: 'f' },
  update: { type: 'boolean' },
  'no-snapshot': { type: 'boolean' },
  retries: { type: 'string' },
  verbose: { type: 'boolean' },
  locale: { type: 'string' },
  'executable-path': { type: 'string' },
  env: { type: 'string' },
  config: { type: 'string' },
  variant: { type: 'string' },
  source: { type: 'string', multiple: true },
  explore: { type: 'boolean' },
  doc: { type: 'string', multiple: true },
  code: { type: 'string', multiple: true },
};

const list = (v) => (v ? v.split(',').map((x) => x.trim()).filter(Boolean) : null);

function timestamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

// 既定の結果の置き場。中に「すべて無視」の .gitignore を置き、リポジトリの変更に数えさせない
// （codd-statemachine の「計画に無いファイルを変えていないか」の検査を、結果のファイルで落とさない）。
function resultsBase(dir) {
  const base = path.resolve(dir || '.', 'webui-test-results');
  fs.mkdirSync(base, { recursive: true });
  const ignore = path.join(base, '.gitignore');
  if (!fs.existsSync(ignore)) fs.writeFileSync(ignore, '# webui-test の実行結果（コミットしない）\n*\n');
  return base;
}

function loadSuites(inputs, log) {
  if (!inputs.length) throw usageError('テストケースファイルかディレクトリを指定してください');
  const files = collectFiles(inputs);
  if (!files.length) throw usageError('テストケースファイル（.yaml / .yml / .json）が見つかりません');
  const suites = [];
  const errors = [];
  for (const f of files) {
    const { suite, errors: e } = loadFile(f);
    if (suite) suites.push(suite);
    errors.push(...e);
  }
  if (errors.length) {
    const err = usageError(`書式の誤りがあります:\n${errors.map((e) => '  - ' + e).join('\n')}`);
    throw err;
  }
  return suites;
}

function usageError(msg) {
  const e = new Error(msg);
  e.exitCode = 2;
  return e;
}

function conditionsFrom(values, positionals) {
  if (values['conditions-file']) return fs.readFileSync(values['conditions-file'], 'utf8');
  const text = positionals.join(' ').trim();
  if (!text) throw usageError('条件を引数か -f <ファイル> で渡してください');
  return text;
}

async function main(argv, io = { out: process.stdout, err: process.stderr }) {
  const say = (s) => io.out.write(s + '\n');
  const warn = (s) => io.err.write(s + '\n');
  // `--` の後ろは npx playwright test にそのまま渡す
  const dd = argv.indexOf('--');
  const passthrough = dd >= 0 ? argv.slice(dd + 1) : [];
  const own = dd >= 0 ? argv.slice(0, dd) : argv;
  let parsed;
  try {
    parsed = parseArgs({ args: own, options: OPTIONS, allowPositionals: true });
  } catch (e) {
    warn(e.message);
    warn(USAGE);
    return 2;
  }
  const { values, positionals } = parsed;
  const [cmd, ...rest] = positionals;
  const executablePath = values['executable-path'] || process.env.WEBUI_TEST_EXECUTABLE_PATH || undefined;
  if (!cmd || values.help || cmd === 'help') {
    say(USAGE);
    return cmd || values.help ? 0 : 2;
  }
  try {
    switch (cmd) {
      case 'format': {
        say(require('./generate').FORMAT_REFERENCE);
        return 0;
      }
      case 'validate': {
        const suites = loadSuites(rest);
        for (const s of suites) say(`OK  ${path.relative(process.cwd(), s.file)}（${s.cases.length} ケース）`);
        return 0;
      }
      case 'prompt': {
        const { buildPrompt, snapshotPage } = require('./generate');
        const pageInfo = values.url && !values['no-snapshot'] ? await snapshotPage(values.url, { executablePath, locale: values.locale }) : null;
        say(buildPrompt({ conditions: conditionsFrom(values, rest), url: values.url, baseUrl: values['base-url'], pageInfo }));
        return 0;
      }
      case 'snapshot': {
        if (!rest[0]) throw usageError('URL を指定してください');
        const info = await require('./generate').snapshotPage(rest[0], { executablePath, locale: values.locale, maxChars: 1e9 });
        say(`# ${info.title}\n# ${info.url}\n${info.aria}`);
        return 0;
      }
      case 'generate': {
        if (!values.out) throw usageError('保存先を -o <ファイル> で指定してください');
        const { generate } = require('./generate');
        const r = await generate({
          conditions: conditionsFrom(values, rest),
          outFile: values.out,
          agent: values.agent,
          agentCmd: values['agent-cmd'],
          url: values.url,
          baseUrl: values['base-url'],
          update: values.update,
          snapshot: !values['no-snapshot'],
          retries: values.retries !== undefined ? Number(values.retries) : undefined,
          verbose: values.verbose,
          explore: values.explore,
          docs: values.doc || [],
          code: values.code || [],
          locale: values.locale,
          executablePath,
          log: warn,
        });
        say(`作成しました: ${r.file}（${r.cases} ケース）`);
        say(`実行: webui-test run ${r.file}`);
        return 0;
      }
      case 'run':
      case 'capture': {
        const suites = loadSuites(rest);
        const { runSuites } = require('./runner');
        const isCapture = cmd === 'capture';
        if (isCapture && !values.out) throw usageError('画像の書き出し先を --out <ディレクトリ> で指定してください');
        if (values.screenshot && !['step', 'failure', 'off'].includes(values.screenshot)) throw usageError('--screenshot は step / failure / off のどれかです');
        const outDir = isCapture
          ? fs.mkdtempSync(path.join(require('os').tmpdir(), 'webui-test-capture-'))
          : path.join(values.out ? path.resolve(values.out) : resultsBase(), timestamp());
        const env = loadEnv({ configPath: values.config, envName: values.env });
        const report = await withServer(env.serve, () => runSuites(suites, {
          outDir,
          env,
          variants: list(values.variant),
          baseUrl: values['base-url'],
          headed: values.headed,
          workers: values.workers ? Number(values.workers) : 1,
          only: list(values.only),
          screenshot: isCapture ? 'failure' : values.screenshot,
          captureRoot: values['capture-root'] ? path.resolve(values['capture-root']) : env.captureRoot || path.resolve('.'),
          captureDir: isCapture ? path.resolve(values.out) : null,
          executablePath,
          onCase: (s, c) => warn(`${c.status === 'passed' ? '✓' : c.status === 'skipped' ? '-' : '✗'} ${s.suite} ${c.variant ? `${c.id} [${c.variant}]` : c.id} ${c.title}${c.error ? `\n    ${c.error}` : ''}`),
        }), { log: warn });
        report.context = collect({ argv, env, files: suites.map((x) => x.file), sources: values.source || [] });
        const { summary } = report;
        if (isCapture) {
          const files = report.suites.flatMap((s) => s.cases.flatMap((c) => c.captured || []));
          for (const f of files) { const rel = path.relative(process.cwd(), f); say(rel.startsWith('..') ? f : rel); }
          if (summary.failed) {
            const { writeReport } = require('./report');
            const r = writeReport(report, outDir);
            warn(`失敗したケースがあります。詳細: ${r.html}`);
          } else {
            fs.rmSync(outDir, { recursive: true, force: true });
          }
          say(`書き出し ${files.length} 枚 / 失敗 ${summary.failed} ケース`);
        } else {
          const { writeReport } = require('./report');
          const r = writeReport(report, outDir);
          say(`合計 ${summary.total}: 合格 ${summary.passed} / 不合格 ${summary.failed} / スキップ ${summary.skipped}`);
          say(`レポート: ${r.html}`);
        }
        return summary.failed ? 1 : 0;
      }
      case 'check': {
        const env = loadEnv({ configPath: values.config, envName: values.env });
        const outDir = path.join(values.out ? path.resolve(values.out) : resultsBase(env.dir), `check-${timestamp()}`);
        return await require('./check').check({
          env,
          cases: rest,
          update: values.update,
          outDir,
          workers: values.workers ? Number(values.workers) : 1,
          executablePath,
          loadSuites,
          argv,
          io,
        });
      }
      case 'export':
      case 'pwtest': {
        const suites = loadSuites(rest);
        if (cmd === 'export' && !values.out) throw usageError('書き出し先を --out <ディレクトリ> で指定してください');
        const env = loadEnv({ configPath: values.config, envName: values.env });
        const outDir = values.out ? path.resolve(values.out) : path.join(resultsBase(), 'playwright');
        const { exportSuites } = require('./export');
        const files = exportSuites(suites, outDir, { env, baseUrl: values['base-url'], screenshot: values.screenshot });
        if (cmd === 'export') {
          for (const f of files) say(path.relative(process.cwd(), f) || f);
          say(`実行: npx playwright test --config ${path.relative(process.cwd(), path.join(outDir, 'playwright.config.ts'))}`);
          return 0;
        }
        const captureRoot = values['capture-root'] || env.captureRoot || '.';
        const code = await withServer(env.serve, () => runPlaywrightTest(outDir, passthrough, { executablePath, captureRoot, io }), { log: warn });
        say(`レポート: ${path.join(outDir, 'playwright-report', 'index.html')}（npx playwright show-report ${path.relative(process.cwd(), path.join(outDir, 'playwright-report'))}）`);
        return code === 0 ? 0 : 1;
      }
      default:
        warn(`知らないコマンド: ${cmd}\n`);
        warn(USAGE);
        return 2;
    }
  } catch (e) {
    warn(e.message);
    return e.exitCode || 1;
  }
}

module.exports = { main, USAGE };
