'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { targetRepo } = require('../src/main/issueFork');
const { fixPrompt, issueContextPrompt } = require('../src/main/evaluation');

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

test('受信箱の課題は固定の対処方法を含めず、利用者の依頼を待つ', () => {
  const issue = { target: { kind: 'task', name: 'report' }, statement: '月の入力が欠ける',
    criteria: [{ requirement: '月を指定する', evidence: '実行記録に月がない' }] };
  const prompt = issueContextPrompt(issue);
  assert.match(prompt, /月の入力が欠ける/);
  assert.match(prompt, /月を指定する/);
  assert.match(prompt, /利用者の依頼に従って/);
  assert.doesNotMatch(prompt, /コミット|push|gitlab-idd|修正してください/);
});
