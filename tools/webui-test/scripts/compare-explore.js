#!/usr/bin/env node
'use strict';
// 同じ条件で generate --explore（baseline）と generate --explore --probe-before-act（candidate）を作り比べる。
// それぞれ作ったケースをすぐ 1 回動かし、書式の検査・初回の合否・頼み直しの回数・対象の取り違え・
// 古い probe で断った回数・ケースあたりの probe 回数・かかった時間を comparison.json / comparison.md に残す。
//
//   node scripts/compare-explore.js --url http://localhost:3000/editor.html -f conditions.md \
//     [--agent kiro|copilot | --agent-cmd "<コマンド>"] [--runs 3] [--out compare-out] [--variant ja,en]
//
// トークン数などの使用量は、今の generate の経路では取れないので「不明」（null）として残す。0 にはしない。

const fs = require('fs');
const path = require('path');
const { parseArgs } = require('util');
const { generate } = require('../src/generate');
const { loadFile } = require('../src/casefile');
const { runSuites } = require('../src/runner');

// 実行結果の失敗理由から、対象の取り違えを数える（Playwright の文言に頼る大まかな分類）
function classifyRunErrors(report) {
  const steps = report.suites.flatMap((s) => s.cases.flatMap((c) => c.steps || [])).filter((s) => s.status === 'failed' && s.error);
  return {
    ambiguousTargets: steps.filter((s) => /strict mode violation/i.test(s.error)).length,
    missingOrNotReadyTargets: steps.filter((s) => /Timeout \d+ms exceeded|が表示されません/.test(s.error) && !/strict mode violation/i.test(s.error)).length,
  };
}

async function runOnce({ mode, n, opts, outDir }) {
  const candidate = mode === 'candidate';
  const outFile = path.join(outDir, `${mode}-${n}.yaml`);
  const t0 = Date.now();
  const row = { mode, run: n, validationPass: false, attempts: null, retries: null, cases: 0, firstRunPass: false, total: 0, failed: 0, ambiguousTargets: 0, missingOrNotReadyTargets: 0, probe: null, generateMs: 0, runMs: 0, usage: null, error: null };
  try {
    const g = await generate({ ...opts, outFile, explore: true, probeBeforeAct: candidate, evidenceDir: candidate ? path.join(outDir, `${mode}-${n}-evidence`) : undefined });
    Object.assign(row, { validationPass: true, attempts: g.attempts, retries: g.attempts - 1, cases: g.cases });
    if (g.probe) row.probe = summarizeProbe(g.probe.stats, g.cases);
  } catch (e) {
    row.error = e.message.split('\n')[0];
    if (e.attempts) Object.assign(row, { attempts: e.attempts, retries: e.attempts - 1 });
    if (e.probe) row.probe = summarizeProbe(e.probe.stats, 0);
  }
  row.generateMs = Date.now() - t0;
  if (!row.validationPass) return row;
  const { suite, errors } = loadFile(outFile);
  if (!suite) { row.error = errors.join('; '); return row; }
  const t1 = Date.now();
  const report = await runSuites([suite], { outDir: path.join(outDir, `${mode}-${n}-run`), baseUrl: opts.baseUrl || new URL(opts.url).origin, variants: opts.variants, executablePath: opts.executablePath, screenshot: 'failure' });
  row.runMs = Date.now() - t1;
  Object.assign(row, { total: report.summary.total, failed: report.summary.failed, firstRunPass: report.summary.total > 0 && report.summary.failed === 0 }, classifyRunErrors(report));
  return row;
}

function summarizeProbe(st, cases) {
  const rejected = st.rejected;
  return {
    probes: st.probes,
    probesPerCase: cases ? Number((st.probes / cases).toFixed(2)) : null,
    ambiguousProbes: st.ambiguous,
    missingProbes: st.missing,
    hiddenOrDisabledProbes: st.hidden + st.disabled,
    actions: st.actions,
    staleRejections: rejected.stale + rejected.changed,
    missingProbeRejections: rejected.missingProbe,
    otherRejections: rejected.unknownProbe + rejected.ambiguous + rejected.missing + rejected.notReady + rejected.fingerprintMismatch + rejected.lifecycle,
    unguarded: st.unguarded,
  };
}

const NA = 'n/a';

function aggregate(rows) {
  const by = (mode) => rows.filter((r) => r.mode === mode);
  const rate = (xs, k) => (xs.length ? `${xs.filter((r) => r[k]).length}/${xs.length}` : '-');
  const sum = (xs, f) => xs.reduce((a, r) => a + (f(r) || 0), 0);
  const avg = (xs, f) => { const v = xs.map(f).filter((x) => typeof x === 'number'); return v.length ? Number((v.reduce((a, b) => a + b, 0) / v.length).toFixed(2)) : null; };
  return Object.fromEntries(['baseline', 'candidate'].map((m) => {
    const xs = by(m);
    return [m, {
      runs: xs.length,
      validationPass: rate(xs, 'validationPass'),
      firstRunPass: rate(xs, 'firstRunPass'),
      retries: sum(xs, (r) => r.retries),
      ambiguousTargetsAtRun: sum(xs, (r) => r.ambiguousTargets),
      missingOrNotReadyTargetsAtRun: sum(xs, (r) => r.missingOrNotReadyTargets),
      // 確認の数字は candidate にしか無い。baseline は「対象外」（不明とは分ける）
      ambiguousTargetsAtProbe: m === 'candidate' ? sum(xs, (r) => r.probe && r.probe.ambiguousProbes) : NA,
      missingOrNotReadyTargetsAtProbe: m === 'candidate' ? sum(xs, (r) => r.probe && r.probe.missingProbes + r.probe.hiddenOrDisabledProbes) : NA,
      staleRejections: m === 'candidate' ? sum(xs, (r) => r.probe && r.probe.staleRejections) : NA,
      missingProbeRejections: m === 'candidate' ? sum(xs, (r) => r.probe && r.probe.missingProbeRejections) : NA,
      unguardedCommands: m === 'candidate' ? sum(xs, (r) => r.probe && r.probe.unguarded) : NA,
      probesPerCase: m === 'candidate' ? avg(xs, (r) => r.probe && r.probe.probesPerCase) : NA,
      generateMsAvg: avg(xs, (r) => r.generateMs),
      usage: null,
    }];
  }));
}

function toMarkdown(result) {
  const a = result.summary;
  const v = (x) => (x === null || x === undefined ? '不明' : x === NA ? '対象外' : String(x));
  const rows = [
    ['書式の検査に合格', 'validationPass'], ['作ったケースが 1 回目で合格', 'firstRunPass'], ['頼み直しの回数（合計）', 'retries'],
    ['実行で 1 つに決まらなかった対象', 'ambiguousTargetsAtRun'], ['実行で見つからない・操作できなかった対象', 'missingOrNotReadyTargetsAtRun'],
    ['確認で 1 つに決まらなかった対象', 'ambiguousTargetsAtProbe'], ['確認で見つからない・隠れている・押せなかった対象', 'missingOrNotReadyTargetsAtProbe'],
    ['古い確認で断った操作', 'staleRejections'], ['確認なしで断った操作', 'missingProbeRejections'], ['見張らずに通した操作', 'unguardedCommands'], ['ケースあたりの確認回数', 'probesPerCase'], ['作成にかかった時間の平均（ms）', 'generateMsAvg'], ['使用量（トークン）', 'usage'],
  ];
  return [
    '# generate --explore の比較', '',
    `- 条件: ${result.conditionsFile || '（引数）'}`, `- URL: ${result.url}`, `- エージェント: ${result.agent}`, `- 回数: 各 ${result.runs} 回`, '',
    '| 項目 | baseline（--explore） | candidate（--probe-before-act） |', '|---|---|---|',
    ...rows.map(([label, k]) => `| ${label} | ${v(a.baseline[k])} | ${v(a.candidate[k])} |`), '',
    '使用量は今の作成の経路では取れないため「不明」としている（0 ではない）。「対象外」は baseline に確認の仕組みが無いことを表す。',
    '「見張らずに通した操作」は、確認を求めずにそのまま渡した操作（eval・type・hover・修飾キー付きの press など。画面遷移は含めない）の数で、確認を回り道した操作がどれだけあったかの目安になる。', '',
  ].join('\n');
}

async function main(argv) {
  const { values } = parseArgs({ args: argv, options: {
    url: { type: 'string' }, 'conditions-file': { type: 'string', short: 'f' }, conditions: { type: 'string' },
    agent: { type: 'string' }, 'agent-cmd': { type: 'string' }, runs: { type: 'string' }, out: { type: 'string' },
    variant: { type: 'string' }, retries: { type: 'string' }, 'base-url': { type: 'string' }, 'executable-path': { type: 'string' },
  } });
  if (!values.url || !(values['conditions-file'] || values.conditions)) throw new Error('--url と -f <条件ファイル>（か --conditions）が要ります');
  const outDir = path.resolve(values.out || 'compare-explore-out');
  fs.mkdirSync(outDir, { recursive: true });
  const opts = {
    conditions: values.conditions || fs.readFileSync(values['conditions-file'], 'utf8'),
    url: values.url, baseUrl: values['base-url'], agent: values.agent, agentCmd: values['agent-cmd'],
    retries: values.retries !== undefined ? Number(values.retries) : undefined, snapshot: false,
    variants: values.variant ? values.variant.split(',') : null,
    executablePath: values['executable-path'] || process.env.WEBUI_TEST_EXECUTABLE_PATH || undefined,
    cwd: outDir,
  };
  const runs = Number(values.runs || 1);
  const rows = [];
  // baseline と candidate を交互に動かし、アプリやマシンの調子の偏りが片方に寄らないようにする
  for (let n = 1; n <= runs; n += 1) {
    for (const mode of ['baseline', 'candidate']) {
      const row = await runOnce({ mode, n, opts, outDir });
      process.stderr.write(`${mode} #${n}: 検査 ${row.validationPass ? '合格' : '不合格'} / 初回実行 ${row.firstRunPass ? '合格' : '不合格'}${row.error ? ` (${row.error})` : ''}\n`);
      rows.push(row);
    }
  }
  const result = { url: values.url, conditionsFile: values['conditions-file'] || null, agent: values['agent-cmd'] || values.agent || 'kiro', runs, rows, summary: aggregate(rows) };
  fs.writeFileSync(path.join(outDir, 'comparison.json'), JSON.stringify(result, null, 2));
  fs.writeFileSync(path.join(outDir, 'comparison.md'), toMarkdown(result));
  process.stdout.write(toMarkdown(result));
  return result;
}

if (require.main === module) {
  main(process.argv.slice(2)).catch((e) => { process.stderr.write(e.message + '\n'); process.exitCode = 1; });
}

module.exports = { main, aggregate, classifyRunErrors, summarizeProbe, toMarkdown };
