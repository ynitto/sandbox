'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

function fixture() {
  class Element {
    constructor() { this.children = []; this.value = ''; this.dataset = {}; }
    append(...children) { this.children.push(...children); for (const child of children) child.parentNode = this; }
    replaceChildren(...children) { this.children = []; this.append(...children); }
  }
  const nodes = new Map(['skills-list', 'skills-repo', 'skills-agent', 'skills-count', 'skills-publish',
    'skills-remove-mode', 'skills-remove', 'skills-status', 'audit-share-repo', 'audit-share-token', 'audit-push-main', 'audit-push-main-row', 'settings-error'].map((id) => [id, new Element()]));
  const pending = [];
  const removals = [];
  const publications = [];
  const window = { api: {
    publish: {
      skills: (repo) => new Promise((resolve) => pending.push({ repo, resolve })),
      submit: (options) => { publications.push(options); return Promise.resolve({ branch: 'published' }); },
    },
    removeSkills: (repo, agent, keys) => new Promise((resolve, reject) => removals.push({ repo, agent, keys, resolve, reject })),
  } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/renderer/skills.js'), 'utf8'), {
    window, document: { getElementById: (id) => nodes.get(id), createElement: () => new Element() },
  });
  window.Skills.init();
  return { window, nodes, pending, removals, publications };
}

test('リポジトリ切り替え中は古い公開操作を隠し、遅れた応答で表示を戻さない', async () => {
  const { window, nodes, pending } = fixture();
  window.Skills.open({ repos: ['a', 'b'] }, ['codex']);
  const doc = (status) => ({ configured: true, items: [{ name: 'review', place: 'repo', status, canPublish: status === 'unpublished' }] });
  pending[0].resolve(doc('unpublished'));
  await new Promise(setImmediate);
  assert.equal(nodes.get('skills-publish').hidden, false);

  nodes.get('skills-repo').value = 'b';
  nodes.get('skills-repo').onchange();
  assert.equal(nodes.get('skills-publish').hidden, true);
  assert.equal(nodes.get('skills-list').children[0].textContent, '読み込んでいます…');
  nodes.get('skills-repo').value = 'a';
  nodes.get('skills-repo').onchange();
  const published = doc('published');
  Object.assign(published.items[0], { localVersion: '1.9.0', remoteVersion: '1.10.0', versionComparison: 'remote-newer' });
  pending[2].resolve(published);
  await new Promise(setImmediate);
  pending[1].resolve(doc('unpublished'));
  await new Promise(setImmediate);
  const row = nodes.get('skills-list').children[0];
  assert.equal(row.children.length, 2, '新しくないローカル版には公開ラベルを付けない');
  assert.match(row.children[1].children[0].textContent, /review\s+v1\.9\.0/);
  assert.doesNotMatch(row.children[1].children[1].textContent, /公開先|ローカル/);
  assert.equal(nodes.get('skills-publish').hidden, true);
});

test('各スキルに版を表示し、ローカルが新しいものだけ未公開ラベルを付ける', async () => {
  const { window, nodes, pending } = fixture();
  window.Skills.open({ repos: ['a'] }, ['codex']);
  pending[0].resolve({ configured: true, items: ['same', 'remote-newer', 'unknown', 'local-newer'].map((versionComparison) => ({
    name: versionComparison, localVersion: '1.2.3', status: 'updated', versionComparison,
  })) });
  await new Promise(setImmediate);
  const rows = nodes.get('skills-list').children;
  assert.equal(rows[0].children[0].dataset.skill, 'local-newer');
  assert.equal(rows[0].children[2].textContent, '未公開');
  for (const row of rows.slice(1)) assert.equal(row.children.length, 2);
  for (const row of rows) {
    assert.match(row.children[1].children[0].textContent, /v1\.2\.3/);
    assert.doesNotMatch(row.children[1].children[1].textContent, /v1\.2\.3/);
  }
});

test('保存したリポジトリとAIを復元し、変更していないトークンを空で上書きしない', () => {
  const { window, nodes } = fixture();
  const config = { repos: ['a', 'b'], audit: { shareRepo: 'https://example.test/skills.git', shareTokenEncrypted: 'encrypted', skillRepo: 'b', skillAgent: 'claude' } };
  window.Skills.fill(config);
  window.Skills.open(config, ['codex', 'claude']);
  assert.equal(nodes.get('skills-repo').value, 'b');
  assert.equal(nodes.get('skills-agent').value, 'claude');
  assert.equal(nodes.get('audit-share-repo').value, config.audit.shareRepo);
  assert.equal(nodes.get('audit-share-token').value, '');
  assert.equal(Object.hasOwn(window.Skills.patch(), 'shareToken'), false);
  nodes.get('audit-share-token').value = 'new-token';
  nodes.get('audit-share-token').oninput();
  assert.equal(window.Skills.patch().shareToken, 'new-token');
  nodes.get('audit-share-token').value = '';
  nodes.get('audit-share-token').oninput();
  assert.equal(window.Skills.patch().shareToken, '');
});

const tick = () => new Promise(setImmediate);
const removableDoc = () => ({ configured: true, items: [
  { name: 'review', canPublish: true, removalKey: 'repo-review', deletePath: '/repo/.agents/skills/review' },
  { name: 'design', canPublish: false, removalKey: 'home-design', deletePath: '/home/.agents/skills/design' },
  { name: 'linked', canPublish: false, removalKey: '', removalError: 'リンク経由の保存先からは削除できません' },
] });

test('削除は未選択で開始し、公開用選択と分離する。共通スキルも選べ、リンクの項目は理由を表示する', async () => {
  const f = fixture();
  f.window.Skills.open({ repos: ['a'] }, ['codex']);
  f.pending[0].resolve(removableDoc());
  await tick();
  const row = (name) => f.nodes.get('skills-list').children.find((r) => r.children[0].dataset.skill === name);
  assert.equal(row('review').children[0].checked, true);
  f.nodes.get('skills-remove-mode').onclick();
  assert.equal(f.nodes.get('skills-publish').hidden, true);
  assert.equal(f.nodes.get('skills-remove').disabled, true);
  for (const r of f.nodes.get('skills-list').children) assert.equal(r.children[0].checked, false);
  assert.equal(row('design').children[0].disabled, false);
  assert.match(row('design').children[1].children[1].textContent, /\/home\/.*design/);
  assert.equal(row('linked').children[0].disabled, true);
  assert.match(row('linked').children[1].children[1].textContent, /リンク/);
  row('design').children[0].checked = true;
  row('design').children[0].onchange();
  assert.equal(f.nodes.get('skills-remove').disabled, false);
  assert.equal(f.nodes.get('skills-count').textContent, '削除対象 1 件');
  f.nodes.get('skills-remove-mode').onclick();
  assert.equal(f.nodes.get('skills-publish').hidden, false);
  assert.equal(row('review').children[0].checked, true);
  assert.equal(row('design').children[0].checked, false);
  f.nodes.get('skills-remove-mode').onclick();
  assert.equal(f.nodes.get('skills-remove').disabled, true);
});

test('削除中は連打・公開・モード切替を防ぎ、実体のキーだけ渡して部分失敗を表示し再読込する', async () => {
  const f = fixture();
  f.window.Skills.open({ repos: ['a'] }, ['claude']);
  f.pending[0].resolve(removableDoc());
  await tick();
  f.nodes.get('skills-remove-mode').onclick();
  for (const row of f.nodes.get('skills-list').children.filter((r) => !r.children[0].disabled)) {
    row.children[0].checked = true;
    row.children[0].onchange();
  }
  const done = f.nodes.get('skills-remove').onclick();
  f.nodes.get('skills-remove').onclick();
  f.nodes.get('skills-remove-mode').onclick();
  f.nodes.get('skills-publish').onclick();
  assert.equal(f.removals.length, 1);
  assert.equal(f.publications.length, 0);
  assert.equal(f.removals[0].repo, 'a');
  assert.equal(f.removals[0].agent, 'claude');
  assert.deepEqual(Array.from(f.removals[0].keys).sort(), ['home-design', 'repo-review']);
  for (const id of ['skills-remove', 'skills-remove-mode', 'skills-agent', 'skills-repo']) assert.equal(f.nodes.get(id).disabled, true);
  f.removals[0].resolve({ cancelled: false, removed: [{ name: 'review' }], failed: [{ name: 'design', error: '移動できません' }] });
  await tick();
  assert.equal(f.pending.length, 2);
  f.pending[1].resolve({ configured: true, items: [removableDoc().items[1]] });
  await done;
  assert.match(f.nodes.get('skills-status').textContent, /1 件をゴミ箱へ移動しました.*design.*移動できません/);
  assert.equal(f.nodes.get('skills-list').children[0].children[0].checked, false);
  assert.equal(f.nodes.get('skills-remove').disabled, true);
});

test('ネイティブ確認のキャンセルと IPC エラーでも選択をクリアして一覧を再取得する', async () => {
  for (const cancel of [true, false]) {
    const f = fixture();
    f.window.Skills.open({ repos: ['a'] }, ['codex']);
    f.pending[0].resolve(removableDoc());
    await tick();
    f.nodes.get('skills-remove-mode').onclick();
    const box = f.nodes.get('skills-list').children[0].children[0];
    box.checked = true; box.onchange();
    const done = f.nodes.get('skills-remove').onclick();
    if (cancel) f.removals[0].resolve({ cancelled: true, removed: [], failed: [] });
    else f.removals[0].reject(new Error('対象が変わりました'));
    await tick();
    f.pending[1].resolve(removableDoc());
    await done;
    assert.match(f.nodes.get('skills-status').textContent, cancel ? /キャンセル/ : /削除できません/);
    if (!cancel) assert.equal(f.nodes.get('settings-error').textContent, '対象が変わりました');
    assert.equal(f.nodes.get('skills-remove').disabled, true);
  }
});

test('AI 切替と設定の開き直しで削除モードを解除し、古い削除結果で新しい画面を書き換えない', async () => {
  const f = fixture();
  f.window.Skills.open({ repos: ['a'] }, ['codex', 'claude']);
  f.pending[0].resolve(removableDoc());
  await tick();
  f.nodes.get('skills-remove-mode').onclick();
  f.nodes.get('skills-agent').value = 'claude';
  f.nodes.get('skills-agent').onchange();
  f.pending[1].resolve(removableDoc());
  await tick();
  assert.equal(f.nodes.get('skills-remove').hidden, true);
  f.nodes.get('skills-remove-mode').onclick();
  const box = f.nodes.get('skills-list').children[0].children[0];
  box.checked = true; box.onchange();
  const done = f.nodes.get('skills-remove').onclick();
  f.window.Skills.reset();
  f.window.Skills.open({ repos: ['b'] }, ['codex']);
  f.pending[2].resolve({ configured: false, items: [] });
  await tick();
  f.removals[0].resolve({ cancelled: false, removed: [{}], failed: [] });
  await done;
  assert.equal(f.pending.length, 3);
  assert.equal(f.nodes.get('skills-status').textContent, '');
  assert.equal(f.nodes.get('skills-remove').hidden, true);
  assert.equal(f.nodes.get('skills-repo').value, 'b');
});

test('最後のスキルを削除して一覧が空になっても削除モードを閉じられる', async () => {
  const f = fixture();
  f.window.Skills.open({ repos: ['a'] }, ['codex']);
  f.pending[0].resolve({ configured: false, items: [removableDoc().items[0]] });
  await tick();
  f.nodes.get('skills-remove-mode').onclick();
  const box = f.nodes.get('skills-list').children[0].children[0];
  box.checked = true; box.onchange();
  const done = f.nodes.get('skills-remove').onclick();
  f.removals[0].resolve({ cancelled: false, removed: [{ name: 'review' }], failed: [] });
  await tick();
  f.pending[1].resolve({ configured: false, items: [] });
  await done;
  assert.equal(f.nodes.get('skills-remove-mode').hidden, false);
  assert.equal(f.nodes.get('skills-remove-mode').textContent, 'キャンセル');
  f.nodes.get('skills-remove-mode').onclick();
  assert.equal(f.nodes.get('skills-remove').hidden, true);
});
