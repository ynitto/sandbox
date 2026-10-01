'use strict';
// webui-test check — アプリを起動して e2e を動かし、前回と画面が変わったかを確かめる。
// codd-statemachine などの「変えたあとの検査コマンド」から呼ぶ想定。終了コード 0 = ケースがすべて通った / 1 = 落ちたケースがある。
//
//   1. アプリをローカルで起動して（serve）、e2e のケース（check.cases）を動かす
//   2. スクリーンショットを前回の画面と比べる（軽く: バイト列、違えば画素）。変わっても落とさない
//   3. 振る舞い・時間・画面を webui-test-results/evidence.json に書く
//
// 仕様書には依存しない。文書への影響を測って画像を差し替えるのは、evidence を受け取る側（codd-statemachine）。
// 単体テストも扱わない（codd-statemachine の test など、呼び出し側が動かす）。
//
// 出力の最後の数行に結果をまとめる（呼び出し側が出力の末尾だけを見せても分かるように）。

const path = require('path');
const { withServer } = require('./serve');
const { compareScreens } = require('./screens');
const { writeEvidence } = require('./evidence');

const MAX_LIST = 5;

const rel = (p) => {
  const r = path.relative(process.cwd(), p);
  return r && !path.isAbsolute(r) ? r.split(path.sep).join('/') : p;
};

function listed(items, fmt) {
  const lines = items.slice(0, MAX_LIST).map((x) => `  ✗ ${fmt(x)}`);
  if (items.length > MAX_LIST) lines.push(`  …ほか ${items.length - MAX_LIST} 件（レポートを見る）`);
  return lines;
}

// opts: { env, cases, outDir, latestDir, executablePath, workers, loadSuites, timestamp, io, argv }
async function check(opts) {
  const { env, io } = opts;
  const say = (s) => io.out.write(s + '\n');
  const conf = env.check || { cases: [], maxDiffRatio: 0 };
  const baseDir = env.dir || process.cwd();
  const caseInputs = opts.cases.length ? opts.cases : conf.cases;
  if (!caseInputs.length) {
    const e = new Error('動かすテストケースがありません（引数か、webui-test.config.yaml の check.cases に書きます）');
    e.exitCode = 2;
    throw e;
  }
  const suites = opts.loadSuites(caseInputs);
  const summary = [];
  let ok = true;

  // 1. e2e（ローカルで起動して動かす）
  say(`== e2e: ${suites.length} ファイル（環境 ${env.name}${env.serve ? `、${env.serve.command} で起動` : ''}）`);
  const { runSuites } = require('./runner');
  const { writeReport } = require('./report');
  const { collect } = require('./context');
  let report;
  try {
    report = await withServer(env.serve, () => runSuites(suites, {
      outDir: opts.outDir,
      env,
      workers: opts.workers || 1,
      executablePath: opts.executablePath,
      onCase: (s, c) => say(`${c.status === 'passed' ? '✓' : c.status === 'skipped' ? '-' : '✗'} ${s.suite} ${c.variant ? `${c.id} [${c.variant}]` : c.id} ${c.title}${c.error ? `\n    ${c.error}` : ''}`),
    }), { log: say });
  } catch (e) {
    ok = false;
    summary.push(`e2e: 動かせません。${String(e.message).split('\n')[0]}`);
    say(String(e.message));
  }
  if (report) {
    report.context = collect({ argv: opts.argv, env, files: suites.map((x) => x.file), sources: [] });
    const r = writeReport(report, opts.outDir);
    const s = report.summary;
    const cases = report.suites.flatMap((x) => x.cases.map((c) => ({ ...c, suite: x.suite })));
    const failed = cases.filter((c) => c.status === 'failed');
    if (failed.length) ok = false;
    summary.push(`e2e: ${s.total} 件中 合格 ${s.passed} / 不合格 ${s.failed} / スキップ ${s.skipped}（レポート: ${rel(r.html)}）`);
    summary.push(...listed(failed, (c) => `${c.suite} ${c.variant ? `${c.id} [${c.variant}]` : c.id}: ${c.error || '失敗'}`));

    // 2. 前回の画面と比べる（変わっても落とさない。何が変わったかを evidence で渡す）
    const latestDir = opts.latestDir || path.dirname(opts.outDir);
    const screens = compareScreens(report, { outDir: opts.outDir, screensDir: path.join(latestDir, 'screens'), maxDiffRatio: conf.maxDiffRatio });
    if (screens.length) {
      const count = (st) => screens.filter((x) => x.status === st).length;
      summary.push(`画面: ${screens.filter((x) => x.status !== 'removed').length} 枚（前回と同じ ${count('same')}・変わった ${count('changed')}・新しい ${count('new')}・なくなった ${count('removed')}）`);
      const moved = screens.filter((x) => x.status === 'changed' || x.status === 'removed');
      summary.push(...moved.slice(0, MAX_LIST).map((x) => `  ↻ ${x.id} — ${x.status === 'removed' ? 'なくなった' : x.message || '変わった'}`));
      if (moved.length > MAX_LIST) summary.push(`  …ほか ${moved.length - MAX_LIST} 枚`);
    }

    // 3. テストで得たもの（振る舞い・時間・画面）
    const evidence = writeEvidence(report, screens, { outDir: opts.outDir, latestDir, root: baseDir });
    summary.push(`テストで得たもの（振る舞い・時間・画面）: ${rel(evidence)}`);
  }

  say('');
  say(`== webui-test check: ${ok ? '通った' : '落ちたケースがある'}`);
  for (const line of summary) say(line);
  return ok ? 0 : 1;
}

module.exports = { check };
