'use strict';

const fs = require('fs');
const path = require('path');
const { parseArgs } = require('util');
const { loadFile, collectFiles } = require('./casefile');

const USAGE = `web-test — 条件からテストケースを作り、Playwright で実行してスクリーンショット付きの結果を出す

使い方:
  web-test generate "<条件>" -o tests/login.yaml [--agent kiro|copilot] [--url <最初に開くURL>]
      エージェントに条件を渡してテストケースファイルを作る。--url を渡すと、その画面の要素一覧も渡す
        -f, --conditions-file <file>  条件をファイルから読む
        --agent-cmd "<コマンド>"       kiro / copilot 以外の CLI（依頼ファイルを読む指示を最後の引数で渡す）
        --base-url <url>              ケースファイルの baseUrl
        --update                      -o のファイルを直す・足す（今の内容をエージェントに渡す）
        --no-snapshot                 画面の要素一覧を取らない
        --retries <n>                 書式の誤りを直してもらう回数（既定 1）
        --verbose                     エージェントの出力をそのまま表示する
  web-test run <ファイルかディレクトリ>... [--base-url <url>] [--out <dir>]
      テストケースを実行し、<out>/<日時>/report.html・report.md・results.json とスクリーンショットを書く
        --workers <n>                 同時に動かすケース数（既定 1）
        --only <ID,...>               指定した ID（前方一致）のケースだけ
        --screenshot step|failure|off ファイルの screenshot 設定を上書き
        --capture-root <dir>          screenshot ステップの path: の起点（既定はカレントディレクトリ）
        --headed                      ブラウザを表示して動かす
  web-test capture <ファイル>... --out <dir> [--base-url <url>]
      仕様書用。screenshot ステップの画像だけを <dir>/<name>.png に書き出す（レポートは作らない）
  web-test validate <ファイルかディレクトリ>...   書式を検査する
  web-test prompt "<条件>" [--url <url>]           エージェントへ渡す依頼文を表示する（チャットに貼る用）
  web-test snapshot <url>                          画面の要素一覧（アクセシビリティツリー）を表示する
  web-test format                                  テストケースファイルの書式を表示する

共通:
  --executable-path <path>  使う Chromium の実行ファイル（環境変数 WEB_TEST_EXECUTABLE_PATH でも可）
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
};

function timestamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
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
  let parsed;
  try {
    parsed = parseArgs({ args: argv, options: OPTIONS, allowPositionals: true });
  } catch (e) {
    warn(e.message);
    warn(USAGE);
    return 2;
  }
  const { values, positionals } = parsed;
  const [cmd, ...rest] = positionals;
  const executablePath = values['executable-path'] || process.env.WEB_TEST_EXECUTABLE_PATH || undefined;
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
          locale: values.locale,
          executablePath,
          log: warn,
        });
        say(`作成しました: ${r.file}（${r.cases} ケース）`);
        say(`実行: web-test run ${r.file}`);
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
          ? fs.mkdtempSync(path.join(require('os').tmpdir(), 'web-test-capture-'))
          : path.resolve(values.out || 'web-test-results', timestamp());
        const report = await runSuites(suites, {
          outDir,
          baseUrl: values['base-url'],
          headed: values.headed,
          workers: values.workers ? Number(values.workers) : 1,
          only: values.only ? values.only.split(',').map((s) => s.trim()).filter(Boolean) : null,
          screenshot: isCapture ? 'failure' : values.screenshot,
          captureRoot: path.resolve(values['capture-root'] || '.'),
          captureDir: isCapture ? path.resolve(values.out) : null,
          executablePath,
          onCase: (s, c) => warn(`${c.status === 'passed' ? '✓' : c.status === 'skipped' ? '-' : '✗'} ${s.suite} ${c.id} ${c.title}${c.error ? `\n    ${c.error}` : ''}`),
        });
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
