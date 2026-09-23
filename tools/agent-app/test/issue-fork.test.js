'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { targetRepo } = require('../src/main/issueFork');
const { fixPrompt } = require('../src/main/evaluation');

const config = { repos: ['/work/project', '/work/shared-skills'], audit: { skillRepositoryPath: '/work/shared-skills' } };
const evidence = (kind, name) => [{ artifact: { kind, name, origin: 'repo:project' } }];

test('共通スキルの修正先はローカルのスキル管理リポジトリ', () => {
  const repo = targetRepo({ config, issue: { target: { kind: 'skill', name: 'review' } },
    evidence: evidence('skill', 'review'), catalog: () => [{ name: 'review', place: 'home' }] });
  assert.equal(repo, '/work/shared-skills');
});

test('リポジトリ固有のスキルとステートマシン・ワークフローは出所のリポジトリ', () => {
  for (const kind of ['skill', 'task', 'workflow']) {
    const repo = targetRepo({ config, issue: { target: { kind, name: 'review' } },
      evidence: evidence(kind, 'review'), catalog: () => [{ name: 'review', place: 'repo' }] });
    assert.equal(repo, '/work/project');
  }
});

test('曖昧な出所で別の作業フォルダを推測しない', () => {
  assert.throws(() => targetRepo({ config: { ...config, repos: ['/a/project', '/b/project'] },
    issue: { target: { kind: 'workflow', name: 'deploy' } }, evidence: evidence('workflow', 'deploy'), catalog: () => [] }),
  /特定できません/);
});

test('修正フォークの依頼は調査後の変更と検証を求める', () => {
  const prompt = fixPrompt({ target: { kind: 'skill', name: 'review' }, statement: '手順が不足' });
  assert.match(prompt, /修正してください/);
  assert.match(prompt, /検証してください/);
  assert.doesNotMatch(prompt, /まだ直さなくてよい/);
});
