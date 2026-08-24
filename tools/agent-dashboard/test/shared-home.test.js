'use strict';

// 共有ホームの解決（`base/main/agent-home.js` の sharedHomeRoot / agentHomeSubdir）。
// 追加依存なしで `node test/shared-home.test.js` で走る。
//
// 背景（実際に起きた不具合）: dashboard は Windows、定常業務のエンジン（agent-loop /
// agentcore CLI 群）は WSL で動く。共有状態（control.json・node-budget・tuning …）の
// 実体は WSL 側の `~/.agents` にあるのに、dashboard が os.homedir() だけでパスを組むと
// `C:\Users\…\.agents` へ読み書きしてしまい、**別々のファイル**になる——画面で保存した
// control.json がエンジンに永久に見えない。Windows では WSL ホーム（UNC）を優先する。

const assert = require('assert');
const os = require('os');
const path = require('path');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

const AGENT_HOME_PATH = require.resolve('../src/base/main/agent-home');
const WSL_PATH = require.resolve('../src/base/main/wsl');

// platform と wsl.wslHomeDir を差し替えたまま fn を実行する（解決は呼び出し時なので、
// require 後に戻すと素の環境で解決してしまう）。
function withEnv({ platform, wslHome }, fn) {
  const realPlatform = Object.getOwnPropertyDescriptor(process, 'platform');
  Object.defineProperty(process, 'platform', { value: platform });
  const realWsl = require.cache[WSL_PATH];
  require.cache[WSL_PATH] = {
    id: WSL_PATH,
    filename: WSL_PATH,
    loaded: true,
    exports: { wslHomeDir: () => wslHome },
  };
  delete require.cache[AGENT_HOME_PATH];
  try {
    fn(require(AGENT_HOME_PATH));
  } finally {
    Object.defineProperty(process, 'platform', realPlatform);
    if (realWsl) require.cache[WSL_PATH] = realWsl;
    else delete require.cache[WSL_PATH];
    delete require.cache[AGENT_HOME_PATH];
  }
}

test('Windows + WSL あり: 共有状態は WSL ホーム（UNC）の .agents を指す（この不具合の本体）', () => {
  const unc = '\\\\wsl.localhost\\Ubuntu\\home\\me';
  withEnv({ platform: 'win32', wslHome: unc }, (ah) => {
    assert.strictEqual(ah.sharedHomeRoot(), unc);
    assert.strictEqual(ah.agentHomeSubdir('control'), path.join(unc, '.agents', 'control'));
    assert.strictEqual(ah.agentHomeDir(), path.join(unc, '.agents'));
  });
});

test('Windows + WSL なし: このマシンのホームへ戻る', () => {
  withEnv({ platform: 'win32', wslHome: '' }, (ah) => {
    assert.strictEqual(ah.sharedHomeRoot(), os.homedir());
    assert.strictEqual(ah.agentHomeSubdir('control'), path.join(os.homedir(), '.agents', 'control'));
  });
});

test('Linux / WSL 内: ホームはこのマシンのホームのまま（WSL に問い合わせない）', () => {
  withEnv({
    platform: 'linux',
    wslHome: '\\\\wsl.localhost\\Ubuntu\\home\\me',  // 誤って参照したら気づくよう値は入れておく
  }, (ah) => {
    assert.strictEqual(ah.sharedHomeRoot(), os.homedir());
    assert.strictEqual(ah.agentHomeSubdir('budget'), path.join(os.homedir(), '.agents', 'budget'));
  });
});

test('明示 base 付きの agentHomeDir は据え置き（プロジェクト配下の .agents）', () => {
  withEnv({ platform: 'win32', wslHome: '\\\\wsl.localhost\\Ubuntu\\home\\me' }, (ah) => {
    assert.strictEqual(ah.agentHomeDir('C:\\proj'), path.join('C:\\proj', '.agents'));
  });
});

console.log(`shared-home: ok (${passed})`);
