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
    frontmatter: 'name: ui-helper\ndescription: UIを改善する\ntags:\n  - ui\n  - ux',
    path: path.join(skillRoot, 'ui-helper', 'SKILL.md'),
    content: '---\nname: ui-helper\ndescription: UIを改善する\ntags:\n  - ui\n  - ux\n---\n# Rules\nKeep it compact.\n',
    place: '', repo: '', dir: path.join(skillRoot, 'ui-helper'),
  }]);
});

test('正規形の metadata.tags を自動選択用タグとして読む', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-skill-meta-tags-'));
  const skillRoot = path.join(root, 'skills');
  fs.mkdirSync(path.join(skillRoot, 'reviewer'), { recursive: true });
  fs.writeFileSync(path.join(skillRoot, 'reviewer', 'SKILL.md'), '---\nname: reviewer\ndescription: レビューする\nmetadata:\n  version: 1.0.0\n  tags:\n    - review\n    - quality\n---\n# Reviewer\n');
  const [item] = skills.catalogFromRoots([{ path: skillRoot, kind: 'skill-dir' }]);
  assert.deepStrictEqual(item.tags, ['review', 'quality']);
});

test('description の本文にある tags: をメタデータと誤認しない', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-skill-tags-in-description-'));
  const skillRoot = path.join(root, 'skills');
  fs.mkdirSync(path.join(skillRoot, 'doc-helper'), { recursive: true });
  fs.writeFileSync(path.join(skillRoot, 'doc-helper', 'SKILL.md'), '---\nname: doc-helper\ndescription: |\n  説明内の例:\n  tags:\n    - prose-only\nmetadata:\n  tags:\n    - real-tag\n---\n# Doc\n');
  const [item] = skills.catalogFromRoots([{ path: skillRoot, kind: 'skill-dir' }]);
  assert.deepStrictEqual(item.tags, ['real-tag']);
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

test('YAML の chomp 指示付き複数行 description も本文として読む', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-skill-block-chomp-'));
  const skillRoot = path.join(root, 'skills');
  fs.mkdirSync(path.join(skillRoot, 'planner'), { recursive: true });
  fs.writeFileSync(path.join(skillRoot, 'planner', 'SKILL.md'), '---\ndescription: >-\n  要件を整理する。\n  実装前に使う。\n---\n# Planner\n');
  let [item] = skills.catalogFromRoots([{ path: skillRoot, kind: 'skill-dir' }]);
  assert.strictEqual(item.description, '要件を整理する。 実装前に使う。');

  fs.writeFileSync(path.join(skillRoot, 'planner', 'SKILL.md'), '---\ndescription: |-\n  仕様を確認する。\n  漏れを探す。\n---\n# Planner\n');
  [item] = skills.catalogFromRoots([{ path: skillRoot, kind: 'skill-dir' }]);
  assert.strictEqual(item.description, '仕様を確認する。 漏れを探す。');
});

test('未対応の AI を選んでも他の AI 専用のスキルを混ぜない', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'skill-filter-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const repo = path.join(root, 'repo');
  const home = path.join(root, 'home');
  t.mock.method(os, 'homedir', () => home);
  for (const base of [repo, home]) {
    for (const [dir, name] of [['.agents', 'common'], ['.claude', 'claude-only'], ['.codex', 'codex-only']]) {
      const target = path.join(base, dir, 'skills', name);
      fs.mkdirSync(target, { recursive: true });
      fs.writeFileSync(path.join(target, 'SKILL.md'), '# test');
    }
  }
  assert.deepEqual(skills.list(repo, 'claude'), ['claude-only', 'common']);
  assert.deepEqual(skills.list(repo, 'codex'), ['codex-only', 'common']);
  for (const agent of ['cursor', 'aider', 'ollama', 'herd']) {
    assert.deepEqual(skills.list(repo, agent), ['common'], agent);
  }
  assert.deepEqual(skills.list(repo, ''), ['claude-only', 'codex-only', 'common']);
});

test('BOM・CRLF の SKILL.md でも説明・タグ・版を読み、段落の空行で説明を切らない', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-skill-crlf-'));
  const skillRoot = path.join(root, 'skills');
  fs.mkdirSync(path.join(skillRoot, 'win'), { recursive: true });
  fs.writeFileSync(path.join(skillRoot, 'win', 'SKILL.md'),
    '﻿---\r\nname: win\r\ndescription: |\r\n  一行目。\r\n\r\n  二行目。\r\ntags:\r\n  - x\r\n  - y\r\nversion: 1.2.0\r\n---\r\n本文\r\n');
  fs.mkdirSync(path.join(skillRoot, 'unix'), { recursive: true });
  fs.writeFileSync(path.join(skillRoot, 'unix', 'SKILL.md'), '---\ndescription: |\n  一行目。\n\n  二行目。\ntags:\n  - x\n---\n本文\n');
  const items = skills.catalogFromRoots([{ path: skillRoot, kind: 'skill-dir' }]);
  const win = items.find((i) => i.name === 'win');
  assert.strictEqual(win.description, '一行目。 二行目。');
  assert.deepStrictEqual(win.tags, ['x', 'y']);
  assert.strictEqual(win.version, '1.2.0');
  const unix = items.find((i) => i.name === 'unix');
  assert.strictEqual(unix.description, '一行目。 二行目。');
  assert.deepStrictEqual(unix.tags, ['x']);
});
