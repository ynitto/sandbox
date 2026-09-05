'use strict';

// 外部コマンドの起動。3 つの形だけ持つ:
//   capture … 短いコマンドを走らせて出力を集める（診断・記録の開始と終了）
//   stream  … 長いコマンドを走らせて出力を逐次流す（スキルの構成確認 / agent-tools の実行）
//   spawnRecorder … winauto record を子プロセスで走らせ、終了を待てる形で返す
// どれもシェルを介さない（argv を直接渡す）。この端末の PATH にある実体をそのまま呼ぶ。

const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const command = require('./command');
const MAX_STREAM_OUTPUT = 1024 * 1024;

function capture(name, args, { cwd = '', timeoutMs = 60000, env = process.env, input = '' } = {}) {
  const spec = command.spawnSpec(name, args, { cwd, env });
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(spec.command, spec.args, spec.options);
    } catch (err) {
      resolve({ ok: false, status: -1, stdout: '', stderr: '', error: String((err && err.message) || err) });
      return;
    }
    let stdout = '';
    let stderr = '';
    let done = false;
    const finish = (res) => { if (!done) { done = true; resolve(res); } };
    const timer = setTimeout(() => {
      try { child.kill(); } catch { /* 既に終わっている */ }
      finish({ ok: false, status: -1, stdout, stderr, error: `${name} が ${Math.round(timeoutMs / 1000)} 秒以内に終わりませんでした` });
    }, timeoutMs);
    child.stdout.on('data', (d) => { stdout += d.toString('utf8'); });
    child.stderr.on('data', (d) => { stderr += d.toString('utf8'); });
    child.on('error', (err) => { clearTimeout(timer); finish({ ok: false, status: -1, stdout, stderr, error: String((err && err.message) || err) }); });
    child.on('close', (code) => { clearTimeout(timer); finish({ ok: code === 0, status: code, stdout, stderr, error: '' }); });
    child.stdin.end(String(input == null ? '' : input));
  });
}

// 走っている stream を 1 つだけ持つ（通常実行と AI 支援は同時に動かさない）。
let running = null;

function isRunning(kind = '') {
  return !!running && (!kind || running.kind === kind);
}

function createOutputCollector({ onLine, maxBytes = MAX_STREAM_OUTPUT } = {}) {
  const outputs = { stdout: '', stderr: '' };
  const buffers = { stdout: '', stderr: '' };
  let size = 0;
  let truncated = false;
  let finished = false;

  function push(kind, data) {
    if (finished || !Object.prototype.hasOwnProperty.call(outputs, kind)) return;
    const raw = data.toString('utf8');
    const room = Math.max(0, maxBytes - size);
    const text = raw.slice(0, room);
    if (text.length < raw.length) truncated = true;
    size += text.length;
    outputs[kind] += text;
    buffers[kind] += text;
    let index;
    while ((index = buffers[kind].indexOf('\n')) >= 0) {
      if (onLine) onLine(kind, buffers[kind].slice(0, index).replace(/\r$/, ''));
      buffers[kind] = buffers[kind].slice(index + 1);
    }
  }

  function finish() {
    if (finished) return;
    finished = true;
    for (const kind of ['stdout', 'stderr']) {
      if (buffers[kind] && onLine) onLine(kind, buffers[kind].replace(/\r$/, ''));
      buffers[kind] = '';
    }
  }

  return { push, finish, result: () => ({ ...outputs, truncated }) };
}

// onLine(kind, text) に stdout / stderr を行単位で流し、終了時に収集済み出力も返す。
function stream(name, args, { cwd = '', env = process.env, kind = 'run', onLine, onExit, maxBytes = MAX_STREAM_OUTPUT } = {}) {
  if (running) throw new Error('別の実行が進行中です。終わるか停止してから始めてください');
  const spec = command.spawnSpec(name, args, { cwd, env });
  let child;
  try {
    child = spawn(spec.command, spec.args, spec.options);
  } catch (err) {
    throw new Error(`起動できません: ${(err && err.message) || err}`, { cause: err });
  }
  running = { child, kind };
  const collector = createOutputCollector({ onLine, maxBytes });
  child.stdout.on('data', (data) => collector.push('stdout', data));
  child.stderr.on('data', (data) => collector.push('stderr', data));
  child.on('error', (err) => collector.push('stderr', `起動エラー: ${(err && err.message) || err}`));
  child.on('close', (code) => {
    collector.finish();
    if (running && running.child === child) running = null;
    if (onExit) onExit({ code, ...collector.result() });
  });
  return { pid: child.pid };
}

function stop(kind = '') {
  if (!running || (kind && running.kind !== kind)) return false;
  try { running.child.kill(); } catch { /* 既に終わっている */ }
  return true;
}

function startDetached(name, args, { cwd = '', env = process.env, logFile = '', spawnProcess = spawn } = {}) {
  const spec = command.spawnSpec(name, args, { cwd, env });
  return new Promise((resolve, reject) => {
    let child;
    let output = null;
    try {
      if (logFile) {
        fs.mkdirSync(path.dirname(logFile), { recursive: true });
        output = fs.openSync(logFile, 'a');
      }
      child = spawnProcess(spec.command, spec.args, {
        ...spec.options, detached: true, stdio: logFile ? ['ignore', output, output] : 'ignore',
      });
    } catch (err) {
      if (output != null) try { fs.closeSync(output); } catch { /* 既に閉じている */ }
      reject(new Error(`起動できません: ${(err && err.message) || err}`, { cause: err }));
      return;
    }
    if (output != null) try { fs.closeSync(output); } catch { /* 子プロセスが fd を持っている */ }
    child.once('error', (err) => reject(new Error(`起動できません: ${(err && err.message) || err}`, { cause: err })));
    child.once('spawn', () => {
      child.unref();
      resolve({ pid: child.pid });
    });
  });
}

function spawnRecorder({ command: name, args, cwd = '' }) {
  const spec = command.spawnSpec(name, args, { cwd });
  const child = spawn(spec.command, spec.args, spec.options);
  let stderr = '';
  child.stderr.on('data', (d) => { stderr += d.toString('utf8'); });
  child.stdout.on('data', () => {});
  const exited = new Promise((resolve) => {
    child.on('error', (err) => resolve({ code: null, stderr: `${stderr}\n${(err && err.message) || err}` }));
    child.on('close', (code) => resolve({ code, stderr }));
  });
  return { pid: child.pid, wait: () => exited };
}

module.exports = { capture, stream, stop, isRunning, startDetached, spawnRecorder, createOutputCollector, MAX_STREAM_OUTPUT };
