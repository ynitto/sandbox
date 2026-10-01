'use strict';
// webui-test check — 実装・テスト・仕様書の画像が食い違っていないかを 1 回で確かめる。
// codd-statemachine などの「変えたあとの検査コマンド」から呼ぶ想定。終了コード 0 = 整合 / 1 = ずれあり。
//
//   1. アプリをローカルで起動して（serve）、e2e のケース（check.cases）を動かす
//   2. 仕様書の画像（screenshot ステップの path:）が今の画面と同じか。--update で撮り直す
//   3. 仕様書（check.docs のマークダウン）が貼っている画像が実在するか。撮っている画像を貼っていない仕様書は知らせるだけ
//
// 単体テストは扱わない（codd-statemachine の test など、呼び出し側が動かす）。
//
// 出力の最後の数行に結果をまとめる（呼び出し側が出力の末尾だけを見せても分かるように）。

const fs = require('fs');
const path = require('path');
const { withServer } = require('./serve');
const { scanDocs } = require('./docimages');

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

// opts: { env, cases, update, outDir, executablePath, workers, loadSuites, timestamp, io, argv }
async function check(opts) {
  const { env, io } = opts;
  const say = (s) => io.out.write(s + '\n');
  const conf = env.check || { cases: [], docs: [], maxDiffRatio: 0 };
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

  // 1. e2e（ローカルで起動して動かす）と 2. 仕様書の画像
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
      captureRoot: env.captureRoot || baseDir,
      docImages: opts.update ? 'copy' : 'compare',
      maxDiffRatio: conf.maxDiffRatio,
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

    const images = cases.flatMap((c) => c.docImages || []);
    if (images.length) {
      if (opts.update) {
        const changed = images.filter((i) => i.status !== 'same');
        summary.push(`仕様書の画像: ${images.length} 枚中 ${changed.length} 枚を撮り直した`);
        summary.push(...changed.slice(0, MAX_LIST).map((i) => `  ↻ ${rel(i.path)}（${i.status === 'created' ? '新規' : '更新'}）`));
      } else {
        const bad = images.filter((i) => i.status !== 'same');
        if (bad.length) {
          ok = false;
          summary.push(`仕様書の画像: ${images.length} 枚中 ${bad.length} 枚が今の画面と違う`);
          summary.push(...listed(bad, (i) => `${rel(i.path)} — ${i.status === 'missing' ? 'まだ無い' : i.message}（今の画面: ${rel(path.join(opts.outDir, i.actual))}）`));
          summary.push('  画面の変更が意図どおりなら `webui-test check --update` で撮り直す（ケースの失敗なら実装かケースを直す）');
        } else {
          summary.push(`仕様書の画像: ${images.length} 枚とも今の画面と同じ`);
        }
      }
    }

    // 3. 仕様書が貼っている画像
    if (conf.docs.length) {
      const scan = scanDocs(conf.docs);
      const broken = scan.links.filter((l) => !l.exists);
      const linked = new Set(scan.links.map((l) => path.resolve(l.file)));
      const unlinked = [...new Set(images.map((i) => path.resolve(i.path)))].filter((p) => !linked.has(p));
      if (broken.length) {
        ok = false;
        summary.push(`仕様書の画像のリンク: ${scan.docs} 文書中 ${broken.length} 件の先がない`);
        summary.push(...listed(broken, (l) => `${rel(l.doc)}:${l.line} → ${l.target}`));
      } else {
        summary.push(`仕様書の画像のリンク: ${scan.docs} 文書、${scan.links.length} 件とも実在する`);
      }
      if (unlinked.length) {
        summary.push(`  （知らせるだけ）撮っているがどの仕様書も貼っていない画像: ${unlinked.slice(0, MAX_LIST).map(rel).join(', ')}${unlinked.length > MAX_LIST ? ' ほか' : ''}`);
      }
    }
  }

  say('');
  say(`== webui-test check: ${ok ? '整合している' : 'ずれがある'}`);
  for (const line of summary) say(line);
  return ok ? 0 : 1;
}

module.exports = { check };
