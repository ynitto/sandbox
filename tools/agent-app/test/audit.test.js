'use strict';

// 監査（src/main/audit.js）と定型化物の共有（src/main/artifactShare.js）。
// 設計: docs/plans/2026-09-16-agent-app-agent-audit-split-and-artifact-sharing-design.md
//
// 押さえるのは 4 つ。
//   1. 台帳の行が agent-audit の budget-ledger と同じ形で出る（境界を渡るものは台帳だけ）
//   2. 申告が失敗しても本体の処理を止めない
//   3. 連鎖は終了コードで止まり、許容コード（抽出・蒸留の 1）では止まらない
//   4. 共有と改善は押したときだけ・同じ成果物に二重に出さない

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const audit = require('../src/main/audit');
const artifactShare = require('../src/main/artifactShare');

function tmp(prefix) { return fs.mkdtempSync(path.join(os.tmpdir(), prefix)); }
function feedRows(userData) {
  const dir = audit.feedDir(userData);
  const out = [];
  for (const name of fs.existsSync(dir) ? fs.readdirSync(dir) : []) {
    for (const line of fs.readFileSync(path.join(dir, name), 'utf8').split('\n')) {
      if (line.trim()) out.push(JSON.parse(line));
    }
  }
  return out;
}

const NOW = Date.parse('2026-09-16T05:00:00Z');

test('台帳の行は ts と status を必ず持ち、日付ごとのファイルへ追記される', () => {
  const ud = tmp('audit-feed-');
  audit.feed(ud, { workload: 'chat', agent_cli: 'claude', seconds: 3.26, status: 'done' }, { now: NOW, node: 'pc' });
  audit.feed(ud, { workload: 'chat', agent_cli: 'claude', status: 'nonsense' }, { now: NOW, node: 'pc' });
  const rows = feedRows(ud);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].ts, '2026-09-16T05:00:00.000Z');
  assert.equal(rows[0].tool, 'agent-app');
  assert.equal(rows[0].node, 'pc');
  assert.equal(rows[0].seconds, 3.3);
  // 知らない status は落とさず failed にする（集計から消えるより、失敗として残る方がよい）
  assert.equal(rows[1].status, 'failed');
  assert.deepEqual(fs.readdirSync(audit.feedDir(ud)), ['20260916.jsonl']);
});

test('成果物は種別と名前が揃ったときだけ載る', () => {
  const ud = tmp('audit-art-');
  audit.feed(ud, { status: 'done', artifact: { kind: 'task', name: 'daily', origin: 'repo:x' } }, { now: NOW });
  audit.feed(ud, { status: 'done', artifact: { kind: 'task' } }, { now: NOW });
  audit.feed(ud, { status: 'done', artifact: { kind: 'statemachine', name: 'daily' } }, { now: NOW });
  const rows = feedRows(ud);
  assert.deepEqual(rows[0].artifact, { kind: 'task', name: 'daily', origin: 'repo:x' });
  assert.ok(!rows[1].artifact);
  // 内部の綴り（ステートマシン）は種別として受けない
  assert.ok(!rows[2].artifact);
});

test('書けない置き場でも申告は例外を投げない', () => {
  const ud = path.join(tmp('audit-ro-'), 'file');
  fs.writeFileSync(ud, 'not a directory');
  assert.equal(audit.feed(ud, { status: 'done' }, { now: NOW }), null);
});

test('会話のターンは assistant の応答だけを申告し、停止と失敗を書き分ける', () => {
  const ud = tmp('audit-turn-');
  const session = { id: 's1', cli: 'copilot', model: '' };
  audit.feedTurn(ud, { session, message: { role: 'user', text: 'x' } }, { now: NOW });
  audit.feedTurn(ud, { session, message: { role: 'assistant', cli: 'claude', model: 'sonnet', elapsedMs: 4200, code: 0 } }, { now: NOW });
  audit.feedTurn(ud, { session, message: { role: 'assistant', cli: 'claude', stopped: true } }, { now: NOW });
  audit.feedTurn(ud, { session, message: { role: 'assistant', cli: 'claude', code: 2, error: 'だめ' } }, { now: NOW });
  const rows = feedRows(ud);
  assert.equal(rows.length, 3);
  assert.deepEqual(rows.map((r) => r.status), ['done', 'cancelled', 'failed']);
  assert.equal(rows[0].seconds, 4.2);
  assert.equal(rows[0].ref, 's1');
  assert.equal(rows[0].workload, 'chat');
});

test('実行の申告は成果物と終了時刻を持ち、escalate を失敗と混ぜない', () => {
  const ud = tmp('audit-run-');
  audit.feedRun(ud, { root: '/home/u/sandbox', record: {
    machine: 'daily-report', taskId: 't1', runId: 'r1', ok: false, escalate: true,
    agentCli: 'claude', model: 'sonnet',
    startedAt: '2026-09-16T05:00:00Z', finishedAt: '2026-09-16T05:00:30Z',
  } });
  const [row] = feedRows(ud);
  assert.equal(row.status, 'escalate');
  assert.equal(row.seconds, 30);
  assert.equal(row.ts, '2026-09-16T05:00:30.000Z');
  assert.deepEqual(row.artifact, { kind: 'task', name: 'daily-report', origin: 'repo:sandbox' });
});

test('共有で引き受けた依頼は lost を打ち切りとして申告する', () => {
  const ud = tmp('audit-share-');
  audit.feedShare(ud, { id: 'q1', posted_by: 'pc2', cli: 'claude', seconds: 12, status: 'lost', mode: 'read' }, { now: NOW });
  const [row] = feedRows(ud);
  assert.equal(row.status, 'cancelled');
  assert.equal(row.workload, 'shared');
  assert.equal(row.ref, 'pc2');
  assert.equal(row.run_id, 'q1');
});

test('生成する設定は書き先・台帳・追加ホームだけを渡す', () => {
  const ud = tmp('audit-cfg-');
  const { file, config } = audit.generateConfig(ud, { platform: 'linux', env: {} });
  assert.ok(fs.existsSync(file));
  assert.equal(config.audit_dir, audit.storeDir(ud));
  // 申告は 1 本だけ。共有の台帳は形が違うので直接読ませない（feedShare が写す）
  assert.deepEqual(config.ledger_dirs, [audit.feedDir(ud)]);
  assert.equal(config.with_transcripts, false);
  assert.deepEqual(config.extra_homes, []);
});

test('Windows では Windows のホームを WSL 表記で渡す', () => {
  const ud = tmp('audit-cfg-win-');
  const { config } = audit.generateConfig(ud, { platform: 'win32', env: { USERPROFILE: 'C:\\Users\\me' } });
  assert.deepEqual(config.extra_homes, ['/mnt/c/Users/me']);
});

test('前置きは本人の操作を邪魔しない形で、ionice が無くても動く', () => {
  const script = audit.stepScript(['agent-audit', 'collect']);
  assert.match(script, /nice -n 19/);
  assert.match(script, /command -v ionice/);
});

// ---- 連鎖 -----------------------------------------------------------------------

function auditorWith(results, { userData, config = {} } = {}) {
  const calls = [];
  const shell = {
    run: async (script) => {
      calls.push(script);
      if (/command -v agent-audit/.test(script)) return { ok: true, status: 0, output: 'yes' };
      const step = audit.STEPS.find((s) => new RegExp(`'${s.args.join("' '")}'`).test(script));
      const got = results[step ? step.key : ''] ?? 0;
      return { ok: got === 0, status: got, output: `out:${step ? step.key : '?'}` };
    },
  };
  const auditor = new audit.Auditor({
    userData, loadConfig: () => ({ audit: { enabled: true, intervalMinutes: 60, ...config } }),
    shellFor: () => shell, platform: 'linux', env: {}, now: () => NOW,
  });
  return { auditor, calls };
}

test('連鎖は 6 段を順に呼び、抽出と蒸留の 1 では止まらない', async () => {
  const ud = tmp('audit-chain-');
  const { auditor } = auditorWith({ extract: 1, distill: 1 }, { userData: ud });
  const out = await auditor.run({ manual: true });
  assert.deepEqual(out.steps.map((s) => s.key), audit.STEPS.map((s) => s.key));
  assert.equal(out.error, '');
  assert.ok(out.steps.every((s) => s.ok));
});

test('収集が落ちたら後ろの段は呼ばない', async () => {
  const ud = tmp('audit-chain2-');
  const { auditor } = auditorWith({ collect: 2 }, { userData: ud });
  const out = await auditor.run({ manual: true });
  assert.deepEqual(out.steps.map((s) => s.key), ['collect']);
  assert.match(out.error, /収集で止まりました/);
});

test('ターンが動いている間は回さず、手動なら回す', async () => {
  const ud = tmp('audit-busy-');
  const { auditor } = auditorWith({}, { userData: ud });
  auditor.busy = () => true;
  assert.deepEqual(await auditor.run(), { skipped: 'busy' });
  assert.equal(auditor.status().deferred, 1);
  const out = await auditor.run({ manual: true });
  assert.equal(out.steps.length, audit.STEPS.length);
});

test('無効なら定期は回さない（手動は回る）', async () => {
  const ud = tmp('audit-off-');
  const { auditor } = auditorWith({}, { userData: ud, config: { enabled: false } });
  assert.deepEqual(await auditor.run(), { skipped: 'disabled' });
  auditor.schedule();
  assert.equal(auditor.timer, null);
});

test('agent-audit が無ければ本体を止めず、手動では理由を返す', async () => {
  const ud = tmp('audit-none-');
  const shell = { run: async () => ({ ok: true, status: 0, output: 'no' }) };
  const auditor = new audit.Auditor({
    userData: ud, loadConfig: () => ({ audit: {} }), shellFor: () => shell, platform: 'linux', env: {},
  });
  assert.deepEqual(await auditor.run(), { skipped: 'unavailable' });
  await assert.rejects(() => auditor.run({ manual: true }), /agent-audit がホストにありません/);
});

test('自前の設定を指したら生成しない', async () => {
  const ud = tmp('audit-owncfg-');
  const { auditor } = auditorWith({}, { userData: ud, config: { configFile: '/home/u/agent-audit.yaml' } });
  assert.equal(auditor.configPath(), '/home/u/agent-audit.yaml');
  assert.ok(!fs.existsSync(audit.configFile(ud)));
});

test('使用量の概要は実測と推定を分け、利用枠は表示期間と別に全期間から読む', async () => {
  const calls = [];
  const ud = tmp('audit-summary-');
  const auditor = new audit.Auditor({ userData: ud, loadConfig: () => ({ audit: {} }), platform: 'linux', env: {},
    shellFor: () => ({ run: async script => {
      calls.push(script);
      if (script.includes('command -v')) return { ok: true, output: 'yes' };
      const payload = script.includes("'stats'") ? { ledger: { runs: 2 } }
        : script.includes("'total'") ? { agent_limits: [{ agent_cli: 'claude', quota_used_percent: 60 }] }
          : { rows: [
            { measured_in: 100, measured_out: 20, estimated_tokens: 0, runs: 1 },
            { measured_in: 0, measured_out: 0, estimated_tokens: 80, unmeasured_runs: 1, runs: 1 },
          ] };
      return { ok: true, output: JSON.stringify(payload) };
    } }),
  });
  const got = await auditor.summary({ by: 'model', period: 'day' });
  assert.deepEqual(got.totals, { measured_in: 100, measured_out: 20, estimated_tokens: 80, unmeasured_runs: 1, runs: 2 });
  assert.equal(got.agentLimits[0].quota_used_percent, 60);
  assert.ok(calls.some(s => s.includes("'model' '--period' 'day'")));
  assert.ok(calls.some(s => s.includes("'agent_cli' '--period' 'total'")));
});

test('集計の失敗はゼロ使用として返さない', async () => {
  const ud = tmp('audit-summary-fail-');
  const auditor = new audit.Auditor({ userData: ud, loadConfig: () => ({ audit: {} }), platform: 'linux', env: {},
    shellFor: () => ({ run: async script => script.includes('command -v')
      ? { ok: true, output: 'yes' } : { ok: false, error: 'failed' } }),
  });
  const got = await auditor.summary();
  assert.equal(got.totals, null);
  assert.equal(got.usage, null);
  assert.equal(got.limitsError, true);
});

test('利用枠の入口は品質集計や収集を実行せず、壊れた応答を未取得として返す', async () => {
  const calls = [];
  const auditor = new audit.Auditor({ userData: tmp('audit-limits-'), loadConfig: () => ({ audit: {} }), platform: 'linux', env: {},
    shellFor: () => ({ run: async script => {
      calls.push(script);
      return script.includes('command -v') ? { ok: true, output: 'yes' } : { ok: true, output: 'bad json' };
    } }),
  });
  const result = await auditor.limits();
  assert.equal(result.limitsError, true);
  assert.deepEqual(result.agentLimits, []);
  assert.equal(calls.length, 2);
  assert.ok(calls[1].includes("'usage' '--by' 'agent_cli' '--period' 'total'"));
});

test('ローカルの割合は実行回数を使い、未分類も分母に含める', async () => {
  const rows = [
    { group: 'claude', runs: 2, measured_in: 100, measured_out: 20, unmeasured_runs: 1 },
    { group: 'ollama', runs: 5, measured_in: 2000 },
    { group: 'custom', runs: 3, measured_in: 50 },
  ];
  const auditor = new audit.Auditor({ userData: tmp('audit-allocation-'), loadConfig: () => ({ audit: {} }), platform: 'linux', env: {},
    shellFor: () => ({ run: async script => script.includes('command -v') ? { ok: true, output: 'yes' }
      : { ok: true, output: JSON.stringify({ rows }) } }),
  });
  const result = await auditor.summary({ by: 'agent_cli' });
  assert.equal(result.allocationUsage.localPercent, 50);
  assert.equal(result.allocationUsage.cloud.tokens, 120);
  assert.equal(result.allocationUsage.cloud.unmeasured, 1);
  assert.equal(result.allocationUsage.other.runs, 3);
});

test('成果物・洞察・レポートはストアのファイルをそのまま読む', () => {
  const ud = tmp('audit-read-');
  const store = audit.storeDir(ud);
  fs.mkdirSync(path.join(store, 'insights'), { recursive: true });
  fs.mkdirSync(path.join(store, 'reports'), { recursive: true });
  fs.writeFileSync(path.join(store, 'artifacts.json'), JSON.stringify({
    version: 1, revision: 3, generated_at: 'x',
    artifacts: [{ kind: 'task', name: 'daily', status: 'trial', samples: 5 }],
  }));
  fs.writeFileSync(path.join(store, 'insights', 'i1.json'), JSON.stringify({ id: 'i1', updated_at: '2026-09-01' }));
  fs.writeFileSync(path.join(store, 'insights', 'i2.json'), JSON.stringify({ id: 'i2', updated_at: '2026-09-10' }));
  fs.writeFileSync(path.join(store, 'reports', '20260916T000000Z-all.md'), '# r');
  const got = audit.artifacts(ud);
  assert.equal(got.revision, 3);
  assert.equal(got.items[0].status, 'trial');
  assert.deepEqual(audit.insights(ud).map((i) => i.id), ['i2', 'i1']);
  assert.equal(audit.reports(ud)[0].name, '20260916T000000Z-all.md');
  // ストアが無い PC でも落ちない
  assert.deepEqual(audit.artifacts(tmp('audit-empty-')).items, []);
});

// ---- 共有と改善 -----------------------------------------------------------------

function repoWith(kind, name) {
  const repo = tmp('audit-repo-');
  if (kind === 'task') {
    fs.mkdirSync(path.join(repo, '.statemachine', name), { recursive: true });
    fs.writeFileSync(path.join(repo, '.statemachine', name, 'machine.yaml'), 'states: []');
  } else if (kind === 'workflow') {
    fs.mkdirSync(path.join(repo, '.agents', 'workflows'), { recursive: true });
    fs.writeFileSync(path.join(repo, '.agents', 'workflows', `${name}.json`), '{}');
  } else {
    fs.mkdirSync(path.join(repo, '.agents', 'skills', name), { recursive: true });
    fs.writeFileSync(path.join(repo, '.agents', 'skills', name, 'SKILL.md'), '# s');
  }
  return repo;
}

test('成果物の置き場は種別ごとの正典を見て決める', () => {
  const repo = repoWith('task', 'daily');
  assert.equal(artifactShare.locate(repo, 'task', 'daily').rel, '.statemachine/daily');
  assert.equal(artifactShare.locate(repo, 'task', 'missing'), null);
  assert.equal(artifactShare.locate(repo, 'task', '../etc'), null);
  const wf = repoWith('workflow', 'nightly');
  assert.equal(artifactShare.locate(wf, 'workflow', 'nightly').rel, '.agents/workflows/nightly.json');
  const sk = repoWith('skill', 'reviewer');
  assert.equal(artifactShare.locate(sk, 'skill', 'reviewer').rel, '.agents/skills/reviewer');
});

function fakeShell(log, { fail = () => false } = {}) {
  return {
    exec: async (argv, opts) => {
      log.push(argv.join(' '));
      const failure = fail(argv);
      if (failure) return { ok: false, status: 1, output: '', error: String(failure) };
      if (argv.includes('diff') && argv.includes('--cached')) return { ok: false, status: 1, output: '' };
      if (argv.includes('symbolic-ref')) return { ok: true, status: 0, output: 'origin/main' };
      if (argv.includes('ls-remote')) return { ok: true, status: 0, output: 'ref: refs/heads/main\tHEAD\nabc123\tHEAD' };
      if (argv.includes('rev-parse')) return { ok: true, status: 0, output: 'abc123' };
      return { ok: true, status: 0, output: '' };
    },
    run: async (script) => { log.push(script); return { ok: true, status: 0, output: '' }; },
  };
}

test('共有先が未設定なら何もしない', async () => {
  const ud = tmp('share-none-');
  const share = new artifactShare.ArtifactShare({ userData: ud, loadConfig: () => ({ audit: {} }), shellFor: () => fakeShell([]) });
  assert.deepEqual(await share.submit({ repo: repoWith('task', 'daily'), kind: 'task', name: 'daily' }), { skipped: 'no-share-repo' });
});

test('初めて成功した成果物を share/ ブランチへ出し、二度目は出さない', async () => {
  const ud = tmp('share-submit-');
  const repo = repoWith('task', 'daily');
  const log = [];
  const share = new artifactShare.ArtifactShare({
    userData: ud, loadConfig: () => ({ audit: { shareRepo: 'git@example:team/skills.git' } }),
    shellFor: () => fakeShell(log), now: () => NOW,
  });
  const first = await share.submit({ repo, kind: 'task', name: 'daily', sessionId: 's1' });
  assert.equal(first.pushed, true);
  assert.equal(first.branch, 'share/task-daily');
  assert.ok(log.some((l) => /git clone --depth 50 git@example:team\/skills\.git/.test(l)));
  assert.ok(log.some((l) => /checkout -B share\/task-daily origin\/main/.test(l)));
  assert.ok(log.some((l) => /push origin HEAD:refs\/heads\/share\/task-daily/.test(l)));
  assert.ok(log.some((l) => /origin\.json/.test(l)), '出所を残す');
  const second = await share.submit({ repo, kind: 'task', name: 'daily' });
  assert.equal(second.skipped, 'already');
});

test('公開の状態は「未公開・未公開の変更・公開済み」を中身で見分ける', async () => {
  const ud = tmp('publish-state-');
  const repo = repoWith('skill', 'reviewer');
  const share = new artifactShare.ArtifactShare({
    userData: ud, loadConfig: () => ({ audit: { shareRepo: 'git@example:team/skills.git' } }),
    shellFor: () => fakeShell([]), now: () => NOW,
  });
  const before = share.state({ repo, kind: 'skill', name: 'reviewer' });
  assert.equal(before.status, 'unpublished');
  assert.equal(before.canPublish, true);

  await share.submit({ repo, kind: 'skill', name: 'reviewer' });
  const published = share.state({ repo, kind: 'skill', name: 'reviewer' });
  assert.equal(published.status, 'published');
  assert.equal(published.canPublish, false, '同じ中身を二度出さない');

  // 時刻だけを動かしても「変更」にしない（git の checkout や写しで時刻は動く）
  const file = path.join(repo, '.agents', 'skills', 'reviewer', 'SKILL.md');
  const later = new Date(Date.now() + 60000);
  fs.utimesSync(file, later, later);
  assert.equal(share.state({ repo, kind: 'skill', name: 'reviewer' }).status, 'published', '時刻では判定しない');

  // 中身を直したら、先頭に出すために「未公開の変更」へ戻る
  fs.writeFileSync(file, '# s\n直した\n');
  const updated = share.state({ repo, kind: 'skill', name: 'reviewer' });
  assert.equal(updated.status, 'updated');
  assert.equal(updated.canPublish, true, '直したものは公開し直せる');
});

test('公開先が空なら、画面に公開の操作を出さない', () => {
  const ud = tmp('publish-off-');
  const repo = repoWith('task', 'daily');
  const share = new artifactShare.ArtifactShare({ userData: ud, loadConfig: () => ({ audit: {} }), shellFor: () => fakeShell([]) });
  const state = share.state({ repo, kind: 'task', name: 'daily' });
  assert.equal(state.configured, false);
  assert.equal(state.canPublish, false);
  assert.equal(state.canImprove, false);
});

test('改善案は実測が基準を割ったときだけ出せる（未取り込みのうちは重ねない）', () => {
  const ud = tmp('publish-improve-');
  const repo = repoWith('task', 'daily');
  const share = new artifactShare.ArtifactShare({
    userData: ud, loadConfig: () => ({ audit: { shareRepo: 'git@e:r.git' } }), shellFor: () => fakeShell([]),
  });
  assert.equal(share.state({ repo, kind: 'task', name: 'daily', verdict: 'qualified' }).canImprove, false);
  assert.equal(share.state({ repo, kind: 'task', name: 'daily', verdict: 'trial' }).canImprove, true);
  artifactShare.record(ud, 'task', 'daily', { improveBranch: 'improve/task-daily', improveMergedAt: '' });
  assert.equal(share.state({ repo, kind: 'task', name: 'daily', verdict: 'blocked' }).canImprove, false, '未取り込みの改善案があるうちは出さない');
});

test('デフォルトブランチへ公開する設定なら既定ブランチへ push する', async () => {
  const ud = tmp('share-main-');
  const log = [];
  const share = new artifactShare.ArtifactShare({
    userData: ud, loadConfig: () => ({ audit: { shareRepo: 'git@e:r.git', pushToMain: true } }),
    shellFor: () => fakeShell(log), now: () => NOW,
  });
  const out = await share.submit({ repo: repoWith('task', 'daily'), kind: 'task', name: 'daily' });
  assert.equal(out.branch, 'main');
  assert.ok(log.some((l) => /push origin HEAD:refs\/heads\/main/.test(l)));
});

test('push が失敗したら記録を残さない（次の周期でやり直せる）', async () => {
  const ud = tmp('share-pushfail-');
  const log = [];
  const share = new artifactShare.ArtifactShare({
    userData: ud, loadConfig: () => ({ audit: { shareRepo: 'git@e:r.git' } }),
    shellFor: () => fakeShell(log, { fail: (argv) => (argv.includes('push') ? 'rejected' : false) }), now: () => NOW,
  });
  await assert.rejects(() => share.submit({ repo: repoWith('task', 'daily'), kind: 'task', name: 'daily' }), /push できません/);
  assert.deepEqual(share.list(), []);
});

test('改善は証跡を渡して improve/ へ出し、未マージのうちは重ねて出さない', async () => {
  const ud = tmp('share-improve-');
  const repo = repoWith('task', 'daily');
  const log = [];
  const prompts = [];
  const share = new artifactShare.ArtifactShare({
    userData: ud, loadConfig: () => ({ audit: { shareRepo: 'git@e:r.git' } }),
    shellFor: () => fakeShell(log), now: () => NOW,
    runPrompt: async (opts) => { prompts.push(opts); return { text: '直した理由', error: '' }; },
  });
  const out = await share.improve({
    repo, kind: 'task', name: 'daily', cli: 'claude',
    evidence: [{ ts: '2026-09-15T00:00:00Z', status: 'failed', error_class: 'verify' }],
  });
  assert.equal(out.branch, 'improve/task-daily');
  assert.equal(prompts[0].readonly, false, '直すのだから書き込みで回す');
  assert.match(prompts[0].prompt, /\.statemachine\/daily/);
  assert.match(prompts[0].prompt, /verify/);
  const again = await share.improve({ repo, kind: 'task', name: 'daily', cli: 'claude' });
  assert.equal(again.skipped, 'improve-open');
});

test('AI が動かなければ改善ブランチを作らない', async () => {
  const ud = tmp('share-improve-fail-');
  const share = new artifactShare.ArtifactShare({
    userData: ud, loadConfig: () => ({ audit: { shareRepo: 'git@e:r.git' } }),
    shellFor: () => fakeShell([]), now: () => NOW,
    runPrompt: async () => ({ text: '', error: '起動できません' }),
  });
  const out = await share.improve({ repo: repoWith('task', 'daily'), kind: 'task', name: 'daily', cli: 'claude' });
  assert.equal(out.skipped, 'run-failed');
  assert.deepEqual(share.list(), []);
});

// ---- 結線（申告が残る経路） -------------------------------------------------------

test('実行の記録を残すと、監査への申告も同じ呼び出しで残る', () => {
  const runHistory = require('../src/main/automation/run-history');
  const ud = tmp('audit-wire-run-');
  runHistory.append(ud, '/home/u/sandbox', {
    runId: 'r1', taskId: 't1', machine: 'daily', ok: true, agentCli: 'claude', model: 'sonnet',
    startedAt: '2026-09-16T05:00:00Z', finishedAt: '2026-09-16T05:00:05Z',
  });
  const rows = feedRows(ud);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].artifact.name, 'daily');
  assert.equal(runHistory.read(ud, '/home/u/sandbox').length, 1);
});

test('共有の台帳に 1 行足すと監査へも申告する', () => {
  const { Ledger } = require('../src/main/share/ledger');
  const auditMod = require('../src/main/audit');
  const ud = tmp('audit-wire-share-');
  const ledger = new Ledger(path.join(ud, 'share', 'ledger'), {
    onRecord: (row) => auditMod.feedShare(ud, row, { node: 'pc' }),
  });
  ledger.record({ id: 'q1', posted_by: 'pc2', cli: 'claude', seconds: 4, status: 'done' });
  assert.equal(feedRows(ud).length, 1);
  assert.equal(ledger.today().count, 1);
});

test('申告の失敗は引き受けの記録を巻き戻さない', () => {
  const { Ledger } = require('../src/main/share/ledger');
  const ud = tmp('audit-wire-throw-');
  const ledger = new Ledger(path.join(ud, 'share', 'ledger'), {
    onRecord: () => { throw new Error('壊れた'); },
  });
  const row = ledger.record({ id: 'q1', posted_by: 'pc2', cli: 'claude', status: 'done' });
  assert.equal(row.id, 'q1');
  assert.equal(ledger.today().count, 1);
});

test('設定は周期と共有先だけを持ち、範囲外の値を丸める', () => {
  const settings = require('../src/main/settings');
  const got = settings.normalize({ audit: { intervalMinutes: 99999, shareRepo: ' git@e:r.git ', pushToMain: 'yes' } }).audit;
  assert.deepEqual(got, {
    enabled: true, intervalMinutes: 1440, shareRepo: 'git@e:r.git', pushToMain: true, configFile: '',
    shareTokenEncrypted: '', skillRepo: '', skillAgent: '', manualLimits: [],
  });
  assert.equal(settings.normalize({}).audit.intervalMinutes, 60);
  assert.equal(settings.normalize({ audit: { enabled: false } }).audit.enabled, false);
});

test('整理は古い申告だけを消し、監査の集計結果は残す', () => {
  const cleanup = require('../src/main/cleanup');
  const ud = tmp('audit-cleanup-');
  const feed = audit.feedDir(ud);
  fs.mkdirSync(feed, { recursive: true });
  fs.writeFileSync(path.join(feed, '20260101.jsonl'), '{}\n');
  fs.writeFileSync(path.join(feed, '20260916.jsonl'), '{}\n');
  const store = audit.storeDir(ud);
  fs.mkdirSync(store, { recursive: true });
  fs.writeFileSync(path.join(store, 'artifacts.json'), '{}');
  const item = cleanup.scan({ userData: ud, now: NOW }).items.find((i) => i.key === 'auditFeed');
  assert.equal(item.count, 1);
  cleanup.remove({ userData: ud, now: NOW }, ['auditFeed']);
  assert.deepEqual(fs.readdirSync(feed), ['20260916.jsonl']);
  assert.ok(fs.existsSync(path.join(store, 'artifacts.json')), 'ストアは触らない');
});
