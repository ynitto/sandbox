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
    const mainBranch = execFileSync('git', ['-C', repo, 'rev-parse', '--abbrev-ref', 'HEAD']).toString().trim();
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

    // 作業 ref が未解決でも、target ブランチが分かるならローカル HEAD ではなく
    // target との差分（分岐点＝merge-base から作業ツリーまで、未コミット分も含む）を出す。
    execFileSync('git', ['-C', repo, 'checkout', '-q', '-b', 'work']);
    execFileSync('git', ['-C', repo, 'commit', '-qam', 'work commit']); // value=2 等をコミット
    fs.writeFileSync(path.join(repo, 'app.js'), 'const value = 3;\n');   // さらに未コミット変更
    const vsTarget = await git.diffRange(repo, { base: mainBranch, workingTree: true });
    assert.match(vsTarget.text, /-const value = 1;/, 'target の分岐点（initial）を比較元にする');
    assert.match(vsTarget.text, /\+const value = 3;/, '未コミットの変更も含める');
    assert.strictEqual(vsTarget.base, mainBranch, '比較元は target ブランチ');
    assert.strictEqual(vsTarget.mode, 'working-tree-vs-target');
    assert.ok(vsTarget.files.includes('app.js') && vsTarget.files.includes('other.js'));
    assert.doesNotMatch(vsTarget.text, /\.agent-project/, '内部ディレクトリは除外する');
    console.log('ok - target 指定時はローカル HEAD でなく target との差分を出す');

    // target が解決できない（存在しないブランチ）ときは従来どおり作業ツリー vs HEAD へ倒す。
    const unknownTarget = await git.diffRange(repo, { base: 'no-such-branch', workingTree: true });
    assert.strictEqual(unknownTarget.mode, 'working-tree', 'target 未解決なら HEAD 比較へフォールバック');
    console.log('ok - target が解決できないときは HEAD 差分へフォールバックする');

    // fetch + origin/<branch> 優先: コメント付き再実行で push し直した最新を検収できる。
    {
      const bare = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-dashboard-origin-'));
      const clone1 = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-dashboard-c1-'));
      const clone2 = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-dashboard-c2-'));
      const g = (dir, ...a) => execFileSync('git', ['-C', dir, ...a]);
      try {
        execFileSync('git', ['init', '-q', '--bare', '-b', 'main', bare]);
        execFileSync('git', ['clone', '-q', bare, clone1]);
        g(clone1, 'config', 'user.email', 't@e.com'); g(clone1, 'config', 'user.name', 't');
        fs.writeFileSync(path.join(clone1, 'base.txt'), 'base\n');
        g(clone1, 'add', '-A'); g(clone1, 'commit', '-qm', 'base'); g(clone1, 'push', '-q', 'origin', 'main');
        // 作業ブランチ feat の 1 回目を push（clone1 の origin/feat = v1）
        g(clone1, 'checkout', '-qb', 'feat');
        fs.writeFileSync(path.join(clone1, 'app.js'), 'const v = 1;\n');
        g(clone1, 'add', '-A'); g(clone1, 'commit', '-qm', 'feat v1'); g(clone1, 'push', '-q', 'origin', 'feat');
        // 別クローンから feat を再 push（＝コメント付き再実行で push し直した状況。origin=v2）
        execFileSync('git', ['clone', '-q', '-b', 'feat', bare, clone2]);
        g(clone2, 'config', 'user.email', 't@e.com'); g(clone2, 'config', 'user.name', 't');
        fs.writeFileSync(path.join(clone2, 'app.js'), 'const v = 2;\n');
        g(clone2, 'add', '-A'); g(clone2, 'commit', '-qm', 'feat v2'); g(clone2, 'push', '-q', 'origin', 'feat');

        // fetch なし: clone1 の origin/feat はまだ v1（古い）
        const stale = await git.diffRange(clone1, { base: 'main', branch: 'feat', fetch: false });
        assert.strictEqual(stale.ref, 'origin/feat', 'branch 指定時は origin/<branch> を比較先に使う');
        assert.match(stale.text, /\+const v = 1;/, 'fetch なしは取り込み済みの古い origin/feat = v1');
        assert.doesNotMatch(stale.text, /const v = 2;/);
        // fetch あり: origin/feat が v2 に更新され、最新を検収できる
        const fresh = await git.diffRange(clone1, { base: 'main', branch: 'feat', fetch: true });
        assert.strictEqual(fresh.ref, 'origin/feat');
        assert.strictEqual(fresh.mode, 'range');
        assert.match(fresh.text, /\+const v = 2;/, 'fetch 後は origin/feat の最新 = v2 を検収する');
        console.log('ok - fetch + origin/<branch> 優先で再 push された最新を検収できる');
      } finally {
        for (const d of [bare, clone1, clone2]) fs.rmSync(d, { recursive: true, force: true });
      }
    }

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
