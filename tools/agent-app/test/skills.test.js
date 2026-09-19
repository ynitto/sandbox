'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const skills = require('../src/main/skills');

test('スキルディレクトリとコマンドファイルから重複しない候補名を返す', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-skills-'));
  const skillRoot = path.join(root, 'skills');
  const commandRoot = path.join(root, 'commands');
  fs.mkdirSync(path.join(skillRoot, 'review'), { recursive: true });
  fs.mkdirSync(path.join(skillRoot, 'empty'), { recursive: true });
  fs.mkdirSync(commandRoot, { recursive: true });
  fs.writeFileSync(path.join(skillRoot, 'review', 'SKILL.md'), '# review');
  fs.writeFileSync(path.join(commandRoot, 'review.md'), '# same');
  fs.writeFileSync(path.join(commandRoot, 'deploy.md'), '# deploy');
  assert.deepStrictEqual(skills.listFromRoots([
    { path: skillRoot, kind: 'skill-dir' },
    { path: commandRoot, kind: 'command-dir' },
  ]), ['deploy', 'review']);

  const repo = path.join(root, 'repo');
  fs.mkdirSync(path.join(repo, '.agents', 'skills', 'repo-skill'), { recursive: true });
  fs.writeFileSync(path.join(repo, '.agents', 'skills', 'repo-skill', 'SKILL.md'), '# repo');
  assert.ok(skills.list(repo).includes('repo-skill'));
});

test('自動選択用にスキルの説明・タグ・本文を読む', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-skill-catalog-'));
  const skillRoot = path.join(root, 'skills');
  fs.mkdirSync(path.join(skillRoot, 'ui-helper'), { recursive: true });
  fs.writeFileSync(path.join(skillRoot, 'ui-helper', 'SKILL.md'), '---\nname: ui-helper\ndescription: UIを改善する\ntags:\n  - ui\n  - ux\n---\n# Rules\nKeep it compact.\n');
  assert.deepStrictEqual(skills.catalogFromRoots([{ path: skillRoot, kind: 'skill-dir' }]), [{
    name: 'ui-helper', description: 'UIを改善する', tags: ['ui', 'ux'], version: '',
    path: path.join(skillRoot, 'ui-helper', 'SKILL.md'),
    content: '---\nname: ui-helper\ndescription: UIを改善する\ntags:\n  - ui\n  - ux\n---\n# Rules\nKeep it compact.\n',
    place: '', repo: '', dir: path.join(skillRoot, 'ui-helper'),
  }]);
});

test('一覧に出す版は frontmatter の version から読む', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-skill-version-'));
  const skillRoot = path.join(root, 'skills');
  fs.mkdirSync(path.join(skillRoot, 'release-notes'), { recursive: true });
  fs.writeFileSync(path.join(skillRoot, 'release-notes', 'SKILL.md'), "---\nname: release-notes\nversion: '1.2.0'\n---\n# notes\n");
  const [item] = skills.catalogFromRoots([{ path: skillRoot, kind: 'skill-dir' }]);
  assert.strictEqual(item.version, '1.2.0');
  fs.writeFileSync(path.join(skillRoot, 'release-notes', 'SKILL.md'), '---\nmetadata:\n  version: 1.10\n---\n# notes\n');
  assert.equal(skills.catalogFromRoots([{ path: skillRoot, kind: 'skill-dir' }])[0].version, '1.10');
});

test('AI を選ぶと、その AI の置き場と共通の置き場だけを歩く', () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-skill-agent-'));
  const claude = skills.sourceRoots(repo, 'claude').map((item) => item.path);
  assert.ok(claude.some((dir) => dir.endsWith(path.join('.claude', 'commands'))), 'その AI の置き場');
  assert.ok(claude.some((dir) => dir.endsWith(path.join('.agents', 'skills'))), '共通の置き場');
  assert.ok(!claude.some((dir) => dir.includes(`${path.sep}.codex${path.sep}`)), '別の AI の置き場は歩かない');
  // リポジトリの中を先に見る（同じ名前なら、その仕事の分を優先する）
  assert.strictEqual(claude[0], path.join(repo, '.claude', 'skills'));
  assert.ok(skills.sourceRoots(repo).some((item) => item.path.includes(`${path.sep}.codex${path.sep}`)), 'AI を指定しなければ全部');
});

test('置き場がリポジトリの中か共通かを行に残す（公開できるのはリポジトリの中だけ）', () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-skill-place-'));
  fs.mkdirSync(path.join(repo, '.agents', 'skills', 'deploy'), { recursive: true });
  fs.writeFileSync(path.join(repo, '.agents', 'skills', 'deploy', 'SKILL.md'), '# deploy');
  const [item] = skills.catalogFromRoots([{ path: path.join(repo, '.agents', 'skills'), kind: 'skill-dir', place: 'repo', repo }]);
  assert.strictEqual(item.place, 'repo');
  assert.strictEqual(item.repo, repo);
});

test('自動選択用に複数行の説明を読む', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-skill-block-'));
  const skillRoot = path.join(root, 'skills');
  fs.mkdirSync(path.join(skillRoot, 'ui-designer'), { recursive: true });
  fs.writeFileSync(path.join(skillRoot, 'ui-designer', 'SKILL.md'), '---\ndescription: |\n  UIとUXを設計する。\n  画面レイアウトも扱う。\n---\n# UI\n');
  const [item] = skills.catalogFromRoots([{ path: skillRoot, kind: 'skill-dir' }]);
  assert.strictEqual(item.description, 'UIとUXを設計する。 画面レイアウトも扱う。');
});
