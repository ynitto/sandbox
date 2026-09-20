'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');

function driver() {
  for (const id of ['playwright', 'playwright-core',
    path.join(path.dirname(path.dirname(process.execPath)), 'lib/node_modules/@playwright/cli/node_modules/playwright-core')]) {
    try { return require(id); } catch { /* 次の場所 */ }
  }
  return null;
}

test('Electron: スキル一覧から選択・確認・キャンセル・ゴミ箱移動・再読込を通す', { timeout: 60000 }, async (t) => {
  let binary;
  try { binary = require('electron'); } catch { /* 未導入 */ }
  const pw = driver();
  if (typeof binary !== 'string' || !fs.existsSync(binary) || !pw?._electron) return t.skip('Electron / Playwright がありません');
  if (process.platform === 'linux' && !process.env.DISPLAY) return t.skip('表示先がありません');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'skills-electron-'));
  let electron;
  t.after(async () => {
    try { if (electron) await electron.close(); }
    finally { fs.rmSync(root, { recursive: true, force: true }); }
  });
  const repo = path.join(root, 'repo');
  const home = path.join(root, 'home');
  const trash = path.join(root, 'trash');
  const userData = path.join(root, 'data');
  const repoSkill = path.join(repo, '.agents/skills/review');
  const homeCopy = path.join(home, '.agents/skills/review');
  const commonSkill = path.join(home, '.agents/skills/design');
  for (const dir of [repoSkill, homeCopy, commonSkill, trash]) fs.mkdirSync(dir, { recursive: true });
  for (const dir of [repoSkill, homeCopy, commonSkill]) fs.writeFileSync(path.join(dir, 'SKILL.md'), '---\nmetadata:\n  version: 1.0.0\n---\n# test\n');
  fs.writeFileSync(path.join(repoSkill, 'helper.py'), 'print(1)');
  require('../src/main/store').saveConfig(userData, {
    repos: [repo], lastRepo: repo, transport: 'headless', share: { enabled: false },
    evaluation: { mode: 'off' }, audit: { enabled: false, skillRepo: repo, skillAgent: 'codex' },
  });
  electron = await pw._electron.launch({ executablePath: binary,
    args: [path.join(__dirname, '..'), '--no-sandbox', `--user-data-dir=${userData}`],
  });
  // OS の確認とゴミ箱だけを置き換える。テスト用の保存先以外には触れない。
  await electron.evaluate(({ dialog, shell }, fixture) => {
    const fs = process.getBuiltinModule('fs');
    const path = process.getBuiltinModule('path');
    process.getBuiltinModule('os').homedir = () => fixture.home;
    global.skillRemovalTest = { answer: 0, dialogs: [], moved: [] };
    dialog.showMessageBox = async (_window, options) => {
      global.skillRemovalTest.dialogs.push(options);
      return { response: global.skillRemovalTest.answer };
    };
    shell.trashItem = async (target) => {
      if (!target.startsWith(fs.realpathSync(fixture.root) + path.sep)) throw new Error('テストの保存先以外です');
      global.skillRemovalTest.moved.push(target);
      fs.renameSync(target, path.join(fixture.trash, `${global.skillRemovalTest.moved.length}-${path.basename(target)}`));
    };
  }, { root, home, trash });
  const win = await electron.firstWindow();
  const errors = [];
  win.on('pageerror', (e) => errors.push(e.message));
  await win.waitForFunction(() => typeof document.getElementById('settings-open').onclick === 'function');
  await win.locator('#settings-open').click();
  await win.locator('[data-settings-tab="skills"]').click();
  await win.waitForFunction(() => document.querySelectorAll('#skills-list input').length === 2);
  assert.equal(await win.locator('#skills-publish').isVisible(), true);
  assert.equal(await win.locator('#skills-publish').isDisabled(), true, '公開先がなくても操作の場所は表示する');
  assert.equal(await win.locator('#skills-list input:visible').count(), 0, '通常時はチェックボックスを隠す');
  const controls = await win.locator('#skills-remove-mode').boundingBox();
  const list = await win.locator('#skills-list').boundingBox();
  assert.ok(controls.y < list.y, '削除の選択操作は一覧の上');
  await win.locator('#skills-remove-mode').click();
  assert.equal(await win.locator('#skills-remove').isDisabled(), true);
  assert.equal(await win.locator('#skills-list input:checked').count(), 0);
  const review = win.locator('#skills-list input[data-skill="review"]');
  await review.check();
  if (process.env.AGENT_APP_SKILLS_SCREENSHOT) {
    await win.locator('#app-settings').screenshot({ path: process.env.AGENT_APP_SKILLS_SCREENSHOT });
  }
  await win.locator('#skills-remove').click();
  await win.waitForFunction(() => document.getElementById('skills-status').textContent.includes('キャンセル')
    && document.querySelectorAll('#skills-list input').length === 2);
  assert.ok(fs.existsSync(repoSkill));
  assert.equal(await review.isChecked(), false);
  const options = await electron.evaluate(() => global.skillRemovalTest.dialogs[0]);
  assert.equal(options.defaultId, 0);
  assert.equal(options.cancelId, 0);
  assert.match(options.detail, /review/);
  assert.ok(options.detail.includes(fs.realpathSync(repoSkill)));

  await electron.evaluate(() => { global.skillRemovalTest.answer = 1; });
  await review.check();
  await win.locator('#skills-remove').click();
  await win.waitForFunction(() => document.getElementById('skills-status').textContent.includes('1 件をゴミ箱へ移動しました')
    && document.querySelectorAll('#skills-list input').length === 2);
  assert.equal(fs.existsSync(repoSkill), false);
  assert.ok(fs.existsSync(homeCopy), '同名の共通コピーは残る');
  assert.ok(fs.existsSync(path.join(trash, '1-review/helper.py')), 'フォルダ全体を渡す');
  assert.equal(await review.isChecked(), false, '新しく表示された共通コピーは未選択');
  assert.ok((await win.locator('#skills-list').innerText()).includes(fs.realpathSync(homeCopy)));

  await win.locator('#skills-list input[data-skill="design"]').check();
  await win.locator('#skills-remove').click();
  await win.waitForFunction(() => document.querySelectorAll('#skills-list input').length === 1);
  assert.equal(fs.existsSync(commonSkill), false);
  assert.ok(fs.existsSync(homeCopy));
  const rejected = await win.evaluate(async () => {
    try { await window.api.removeSkills('', 'codex', ['/tmp/arbitrary']); return false; }
    catch { return true; }
  });
  assert.equal(rejected, true);
  assert.equal(await electron.evaluate(() => global.skillRemovalTest.dialogs.length), 3);
  assert.deepEqual(errors, []);
});
