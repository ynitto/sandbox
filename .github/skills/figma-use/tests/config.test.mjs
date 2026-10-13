import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { findConnectionFiles, parseServiceEntries, resolveCredentials } from '../scripts/modules/config.mjs';

const YAML = `# comment
redmine:
  - label: default
    url: https://redmine.example.com
    api_key: \${REDMINE_API_KEY}

figma:
  - label: default
    token: \${FIGMA_WORK_TOKEN}   # 環境変数を参照
  - label: "personal"
    token: 'figd_plain'
    api_base: https://api.figma.com

moltbook:
  nested:
    key: value
`;

function tmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'figma-use-config-'));
}

test('connections.yaml から figma のエントリだけを読む', () => {
  const entries = parseServiceEntries(YAML, 'figma');
  assert.equal(entries.length, 2);
  assert.equal(entries[0].token, '${FIGMA_WORK_TOKEN}');
  assert.equal(entries[1].label, 'personal');
  assert.equal(entries[1].token, 'figd_plain');
});

test('環境変数を展開し、ラベルで切り替える', () => {
  const dir = tmp();
  const file = path.join(dir, 'connections.yaml');
  fs.writeFileSync(file, YAML);
  const env = { SKILL_CONNECTIONS_FILE: file, FIGMA_WORK_TOKEN: 'from-env-ref' };
  const a = resolveCredentials({ env });
  assert.equal(a.token, 'from-env-ref');
  assert.match(a.source, /connections\.yaml/);
  assert.doesNotMatch(a.source, /from-env-ref/);
  assert.equal(resolveCredentials({ env, label: 'personal' }).token, 'figd_plain');
});

test('参照先の環境変数が無ければ FIGMA_TOKEN に倒れ、その旨を残す', () => {
  const dir = tmp();
  const file = path.join(dir, 'connections.yaml');
  fs.writeFileSync(file, YAML);
  const r = resolveCredentials({ env: { SKILL_CONNECTIONS_FILE: file, FIGMA_TOKEN: 'fallback' } });
  assert.equal(r.token, 'fallback');
  assert.equal(r.source, '環境変数 FIGMA_TOKEN');
  assert.ok(r.notes.some((n) => n.includes('未設定')));
});

test('何も無ければ token は null', () => {
  const r = resolveCredentials({ env: { SKILL_CONNECTIONS_FILE: path.join(tmp(), 'none.yaml') } });
  assert.equal(r.token, null);
});

test('ワークスペースの {agent_dir}/connections.yaml をグローバルより先に読む', () => {
  const ws = tmp();
  const home = tmp();
  fs.mkdirSync(path.join(ws, '.claude', 'skills'), { recursive: true });
  fs.writeFileSync(path.join(ws, '.claude', 'connections.yaml'), 'figma:\n  - label: default\n    token: ws\n');
  const fileSkillHome = path.join(home, '.claude', 'skills');
  fs.mkdirSync(fileSkillHome, { recursive: true });
  fs.writeFileSync(path.join(home, '.claude', 'connections.yaml'), 'figma:\n  - label: default\n    token: global\n');
  const cwd = path.join(ws, 'src', 'app');
  fs.mkdirSync(cwd, { recursive: true });
  const files = findConnectionFiles({ env: {}, cwd, fileSkillHome });
  assert.equal(files.length, 2);
  assert.equal(resolveCredentials({ env: {}, cwd, fileSkillHome }).token, 'ws');
  assert.equal(resolveCredentials({ env: {}, cwd: home, fileSkillHome }).token, 'global');
});
