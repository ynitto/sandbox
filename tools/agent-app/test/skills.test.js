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
