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
    name: 'ui-helper', description: 'UIを改善する', tags: ['ui', 'ux'],
    path: path.join(skillRoot, 'ui-helper', 'SKILL.md'),
    content: '---\nname: ui-helper\ndescription: UIを改善する\ntags:\n  - ui\n  - ux\n---\n# Rules\nKeep it compact.\n',
  }]);
});

test('自動選択用に複数行の説明を読む', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-skill-block-'));
  const skillRoot = path.join(root, 'skills');
  fs.mkdirSync(path.join(skillRoot, 'ui-designer'), { recursive: true });
  fs.writeFileSync(path.join(skillRoot, 'ui-designer', 'SKILL.md'), '---\ndescription: |\n  UIとUXを設計する。\n  画面レイアウトも扱う。\n---\n# UI\n');
  const [item] = skills.catalogFromRoots([{ path: skillRoot, kind: 'skill-dir' }]);
  assert.strictEqual(item.description, 'UIとUXを設計する。 画面レイアウトも扱う。');
});
