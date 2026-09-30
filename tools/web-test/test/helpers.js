'use strict';
const fs = require('fs');
const path = require('path');
const { createServer } = require('../examples/sample-app/server');

// 同梱の Chromium が playwright の版と合わない環境では WEB_TEST_EXECUTABLE_PATH で差し替える
function executablePath() {
  return process.env.WEB_TEST_EXECUTABLE_PATH || undefined;
}

async function startSampleApp() {
  const server = createServer();
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const baseUrl = `http://127.0.0.1:${server.address().port}`;
  return { baseUrl, close: () => new Promise((r) => server.close(r)) };
}

function tmpDir(t) {
  const d = fs.mkdtempSync(path.join(require('os').tmpdir(), 'web-test-test-'));
  t.after(() => fs.rmSync(d, { recursive: true, force: true }));
  return d;
}

// cli.main を呼び、終了コードと出力を返す
async function cli(args, opts = {}) {
  const { main } = require('../src/cli');
  let out = '';
  let err = '';
  const prevCwd = process.cwd();
  if (opts.cwd) process.chdir(opts.cwd);
  try {
    const code = await main(args, { out: { write: (s) => { out += s; } }, err: { write: (s) => { err += s; } } });
    return { code, out, err };
  } finally {
    process.chdir(prevCwd);
  }
}

module.exports = { startSampleApp, tmpDir, cli, executablePath };
