'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { EventEmitter } = require('node:events');
const runner = require('../src/main/runner');
const tools = require('../src/main/tools');
const ai = require('../src/main/ai');

test('ストリーム出力は行ごとに通知し、改行のない末尾も終了時に保持する', () => {
  const lines = [];
  const collector = runner.createOutputCollector({ onLine: (kind, line) => lines.push([kind, line]) });
  collector.push('stdout', Buffer.from('one\ntw'));
  collector.push('stdout', Buffer.from('o'));
  collector.push('stderr', Buffer.from('warn'));
  collector.finish();

  assert.deepStrictEqual(lines, [['stdout', 'one'], ['stdout', 'two'], ['stderr', 'warn']]);
  assert.deepStrictEqual(collector.result(), { stdout: 'one\ntwo', stderr: 'warn', truncated: false });
});

test('AI応答の収集量には上限がある', () => {
  const collector = runner.createOutputCollector({ maxBytes: 5 });
  collector.push('stdout', Buffer.from('123456789'));
  collector.finish();
  assert.deepStrictEqual(collector.result(), { stdout: '12345', stderr: '', truncated: true });
});

test('短いコマンドへ設定JSONを標準入力で渡せる', async () => {
  const input = '{"enabled":true,"kind":"daily"}';
  const result = await runner.capture(process.execPath, [
    '-e', 'process.stdin.setEncoding("utf8");let s="";process.stdin.on("data",d=>s+=d);process.stdin.on("end",()=>process.stdout.write(s));',
  ], { input, timeoutMs: 5000 });

  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.stdout, input);
});

test('自動実行プロセスはアプリから独立して起動する', async () => {
  const calls = [];
  const child = new EventEmitter();
  child.pid = 4321;
  child.unref = () => { child.unreferenced = true; };
  const started = runner.startDetached('agent-loop', ['--no-auto-attach'], {
    cwd: '/project',
    spawnProcess: (...args) => { calls.push(args); process.nextTick(() => child.emit('spawn')); return child; },
  });

  assert.deepStrictEqual(await started, { pid: 4321 });
  assert.strictEqual(child.unreferenced, true);
  assert.strictEqual(calls[0][2].detached, true);
  assert.strictEqual(calls[0][2].stdio, 'ignore');
});

test('偽のagent-herdを読み取り専用契約で起動し、末尾改行なしの候補を受け取る', async (t) => {
  if (process.platform === 'win32') { t.skip('POSIXの実行ファイルを使う試験'); return; }
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'smk-agent-herd-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const executable = path.join(dir, 'agent-herd');
  const envelope = {
    schemaVersion: 1, status: 'candidate', summary: '下書き', questions: [], assumptions: [], findings: [],
    candidate: { name: '確認', machine: 'check', purpose: '確認する', steps: [{ kind: 'agent', title: '確認', detail: '内容を確認する' }] },
  };
  fs.writeFileSync(executable, `#!/usr/bin/env node\nprocess.stdout.write(${JSON.stringify(JSON.stringify(envelope))});\n`, 'utf8');
  fs.chmodSync(executable, 0o755);
  const launch = tools.agentAssistRunSpec({ root: dir, agent: 'fake', prompt: 'JSONだけ' });

  const exited = await new Promise((resolve, reject) => {
    try {
      runner.stream(launch.command, launch.args, {
        cwd: dir, kind: 'ai', env: { ...process.env, PATH: `${dir}${path.delimiter}${process.env.PATH || ''}` }, onExit: resolve,
      });
    } catch (err) { reject(err); }
  });
  assert.strictEqual(exited.code, 0);
  assert.strictEqual(ai.parseEnvelope(exited.stdout, { mode: 'draft' }).candidate.machine, 'check');
});
