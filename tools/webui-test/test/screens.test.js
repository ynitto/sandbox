'use strict';
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { compareScreens } = require('../src/screens');

test('落ちた・飛ばしたケースで撮らなかった画面は「なくなった」にせず、版の控えも残す', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'webui-screens-'));
  const outDir = path.join(dir, 'out');
  const screensDir = path.join(dir, 'screens');
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(path.join(outDir, 'a.png'), Buffer.from('shot-a'));
  const file = path.join(dir, 'login.yaml');
  const report = (status, screenshots) => ({ suites: [{ file, suite: 'login', cases: [{ id: 'S-01', title: 'ログイン', status, screenshots }] }] });
  const shot = [{ name: 'login', file: 'a.png', explicit: true }];

  const first = compareScreens(report('passed', shot), { outDir, screensDir });
  assert.strictEqual(first[0].status, 'new');
  for (const status of ['failed', 'skipped']) {
    const out = compareScreens(report(status, []), { outDir, screensDir });
    assert.deepStrictEqual(out, [], `${status} のケースの画面を消さない`);
  }
  const again = compareScreens(report('passed', shot), { outDir, screensDir });
  assert.strictEqual(again[0].status, 'same');
  assert.deepStrictEqual(again[0].history, first[0].history);
  // 通ったケースで撮らなくなった画面は、なくなったと渡す
  const gone = compareScreens(report('passed', []), { outDir, screensDir });
  assert.strictEqual(gone[0].status, 'removed');
});
