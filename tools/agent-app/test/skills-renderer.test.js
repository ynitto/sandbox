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
    'skills-status', 'audit-share-repo', 'audit-share-token', 'audit-push-main', 'audit-push-main-row', 'settings-error'].map((id) => [id, new Element()]));
  const pending = [];
  const window = { api: { publish: { skills: (repo) => new Promise((resolve) => pending.push({ repo, resolve })) } } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/renderer/skills.js'), 'utf8'), {
    window, document: { getElementById: (id) => nodes.get(id), createElement: () => new Element() },
  });
  window.Skills.init();
  return { window, nodes, pending };
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
