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

    // 検収物の path が WSL 側の POSIX パスでも、win32 では UNC へ橋渡ししてから解決する
    {
      const origPlatform = Object.getOwnPropertyDescriptor(process, 'platform');
      const origDistro = process.env.WSL_DISTRO_NAME;
      Object.defineProperty(process, 'platform', { value: 'win32', configurable: true });
      process.env.WSL_DISTRO_NAME = 'Ubuntu';
      try {
        assert.strictEqual(
          git.bridgeRepoPath('/home/dev/proj'),
          '\\\\wsl.localhost\\Ubuntu\\home\\dev\\proj'
        );
        assert.strictEqual(git.bridgeRepoPath('C:\\proj'), 'C:\\proj');   // Windows パスはそのまま
        // /mnt/<drive>/… は UNC でなく Windows ドライブ実体へ（検収 diff が読めなかった原因）
        assert.strictEqual(git.bridgeRepoPath('/mnt/c/Users/dev/proj'), 'C:\\Users\\dev\\proj');
        assert.strictEqual(git.bridgeRepoPath('/mnt/d'), 'D:\\');
      } finally {
        if (origPlatform) Object.defineProperty(process, 'platform', origPlatform);
        if (origDistro === undefined) delete process.env.WSL_DISTRO_NAME;
        else process.env.WSL_DISTRO_NAME = origDistro;
      }
      assert.strictEqual(git.bridgeRepoPath('/home/dev/proj'), '/home/dev/proj'); // 非 win32 は素通し
      console.log('ok - diffRange は WSL の POSIX パスを win32 で UNC へ橋渡しする');
    }
  } finally {
    fs.rmSync(repo, { recursive: true, force: true });
  }
})().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
