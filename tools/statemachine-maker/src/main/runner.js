'use strict';

// 外部コマンドの起動。3 つの形だけ持つ:
//   capture … 短いコマンドを走らせて出力を集める（診断・記録の開始と終了）
//   stream  … 長いコマンドを走らせて出力を逐次流す（スキルの --dry-run / 実行）
//   spawnRecorder … winauto record を子プロセスで走らせ、終了を待てる形で返す
// どれもシェルを介さない（argv を直接渡す）。この端末の PATH にある実体をそのまま呼ぶ。

const { spawn } = require('child_process');
const command = require('./command');

function capture(name, args, { cwd = '', timeoutMs = 60000, env = process.env } = {}) {
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
      finish({ ok: false, status: -1, stdout, stderr, error: `${command} が ${Math.round(timeoutMs / 1000)} 秒以内に終わりませんでした` });
    }, timeoutMs);
    child.stdout.on('data', (d) => { stdout += d.toString('utf8'); });
    child.stderr.on('data', (d) => { stderr += d.toString('utf8'); });
    child.on('error', (err) => { clearTimeout(timer); finish({ ok: false, status: -1, stdout, stderr, error: String((err && err.message) || err) }); });
    child.on('close', (code) => { clearTimeout(timer); finish({ ok: code === 0, status: code, stdout, stderr, error: '' }); });
  });
}

// 走っている stream を 1 つだけ持つ（画面の「実行」は同時に 1 本）。
let running = null;

function isRunning() {
  return !!running;
}

// onLine(kind, text) に stdout / stderr を行単位で流し、終了で { code } を返す。
function stream(name, args, { cwd = '', env = process.env, onLine, onExit } = {}) {
  if (running) throw new Error('別の実行が進行中です。終わるか停止してから始めてください');
  const spec = command.spawnSpec(name, args, { cwd, env });
  let child;
  try {
    child = spawn(spec.command, spec.args, spec.options);
  } catch (err) {
    throw new Error(`起動できません: ${(err && err.message) || err}`, { cause: err });
  }
  running = child;
  const emit = (kind) => {
    let buf = '';
    return (d) => {
      buf += d.toString('utf8');
      let i;
      while ((i = buf.indexOf('\n')) >= 0) {
        if (onLine) onLine(kind, buf.slice(0, i).replace(/\r$/, ''));
        buf = buf.slice(i + 1);
      }
    };
  };
  child.stdout.on('data', emit('stdout'));
  child.stderr.on('data', emit('stderr'));
  child.on('error', (err) => { if (onLine) onLine('stderr', `起動エラー: ${(err && err.message) || err}`); });
  child.on('close', (code) => {
    if (running === child) running = null;
    if (onExit) onExit({ code });
  });
  return { pid: child.pid };
}

function stop() {
  if (!running) return false;
  try { running.kill(); } catch { /* 既に終わっている */ }
  return true;
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

module.exports = { capture, stream, stop, isRunning, spawnRecorder };
