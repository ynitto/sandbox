'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');
const git = require('../src/main/git');

(async () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-dashboard-diff-'));
  try {
    execFileSync('git', ['init', '-q', repo]);
    execFileSync('git', ['-C', repo, 'config', 'user.email', 'test@example.com']);
    execFileSync('git', ['-C', repo, 'config', 'user.name', 'Test']);
    fs.writeFileSync(path.join(repo, 'app.js'), 'const value = 1;\n');
    execFileSync('git', ['-C', repo, 'add', 'app.js']);
    execFileSync('git', ['-C', repo, 'commit', '-qm', 'initial']);
    fs.writeFileSync(path.join(repo, 'app.js'), 'const value = 2;\n');

    const result = await git.diffRange(repo, { workingTree: true });
    assert.match(result.text, /-const value = 1;/);
    assert.match(result.text, /\+const value = 2;/);
    assert.strictEqual(result.mode, 'working-tree');
    console.log('ok - ref が無い検収物は現在の作業ツリー全体を差分表示できる');
  } finally {
    fs.rmSync(repo, { recursive: true, force: true });
  }
})().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
