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
    fs.writeFileSync(path.join(repo, 'other.js'), 'const other = 1;\n');
    fs.mkdirSync(path.join(repo, '.kiro-project'));
    fs.mkdirSync(path.join(repo, '.agent-project'));
    fs.writeFileSync(path.join(repo, '.kiro-project', 'state.json'), '{"run": 1}\n');
    fs.writeFileSync(path.join(repo, '.agent-project', 'state.json'), '{"run": 1}\n');
    execFileSync('git', ['-C', repo, 'add', 'app.js', 'other.js', '.kiro-project/state.json', '.agent-project/state.json']);
    execFileSync('git', ['-C', repo, 'commit', '-qm', 'initial']);
    fs.writeFileSync(path.join(repo, 'app.js'), 'const value = 2;\n');
    fs.writeFileSync(path.join(repo, 'other.js'), 'const other = 2;\n');
    fs.writeFileSync(path.join(repo, '.kiro-project', 'state.json'), '{"run": 2}\n');
    fs.writeFileSync(path.join(repo, '.agent-project', 'state.json'), '{"run": 2}\n');

    const result = await git.diffRange(repo, { workingTree: true });
    assert.match(result.text, /-const value = 1;/);
    assert.match(result.text, /\+const value = 2;/);
    assert.match(result.text, /\.kiro-project\/state\.json/);
    assert.doesNotMatch(result.text, /\.agent-project/);
    assert.deepStrictEqual(result.files, ['.kiro-project/state.json', 'app.js', 'other.js']);
    assert.strictEqual(result.mode, 'working-tree');
    console.log('ok - ref が無い検収物は現在の作業ツリー全体を差分表示できる');

    const selected = await git.diffRange(repo, { workingTree: true, file: 'app.js' });
    assert.match(selected.text, /diff --git a\/app\.js b\/app\.js/);
    assert.doesNotMatch(selected.text, /other\.js/);
    assert.strictEqual(selected.file, 'app.js');
    console.log('ok - ファイル選択時は選択したファイルの差分だけを返す');
  } finally {
    fs.rmSync(repo, { recursive: true, force: true });
  }
})().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
