'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const project = require('../src/main/project');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function mkWorkspace(extra) {
  const ws = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-project-check-'));
  if (extra !== null) {
    fs.mkdirSync(path.join(ws, '.agents'), { recursive: true });
    fs.writeFileSync(
      path.join(ws, '.agents', 'agent-project.yaml'),
      `root: .agent-project\n${extra}`,
      'utf8'
    );
  }
  fs.mkdirSync(path.join(ws, '.agent-project', 'backlog'), { recursive: true });
  return ws;
}

test('任意の regression_cmd をプロジェクト共通チェックとして載せる', () => {
  const ws = mkWorkspace('regression_cmd: make -s smoke\n');
  try {
    const check = project.readProject(ws, {}).projectCheck;
    assert.strictEqual(check.configured, true);
    assert.strictEqual(check.command, 'make -s smoke');
    assert.strictEqual(check.configFile, path.join(ws, '.agents', 'agent-project.yaml'));
    assert.strictEqual(check.configSource, 'dashboard-auto-discovery');
    assert.strictEqual(check.explicitConfigUnknown, true);
  } finally {
    fs.rmSync(ws, { recursive: true, force: true });
  }
});

test('CLI名やコマンド構成を解釈しない', () => {
  const ws = mkWorkspace('regression_cmd: codd-gate verify --base HEAD~1\nintake_cmd: codd-gate tasks --debt\n');
  try {
    const check = project.readProject(ws, {}).projectCheck;
    assert.deepStrictEqual(
      { configured: check.configured, command: check.command },
      { configured: true, command: 'codd-gate verify --base HEAD~1' }
    );
    assert.ok(!Object.hasOwn(check, 'regressionWired'));
    assert.ok(!Object.hasOwn(check, 'intakeWired'));
  } finally {
    fs.rmSync(ws, { recursive: true, force: true });
  }
});

test('regression_cmd が無ければ未設定', () => {
  const ws = mkWorkspace('planner: none\n');
  try {
    const check = project.readProject(ws, {}).projectCheck;
    assert.strictEqual(check.configured, false);
    assert.strictEqual(check.command, null);
  } finally {
    fs.rmSync(ws, { recursive: true, force: true });
  }
});

test('設定ファイルが無くても読み取り専用の未設定状態を返す', () => {
  const ws = mkWorkspace(null);
  try {
    const check = project.readProject(ws, {}).projectCheck;
    assert.strictEqual(check.configured, false);
    assert.strictEqual(check.command, null);
    assert.strictEqual(check.configFile, null);
  } finally {
    fs.rmSync(ws, { recursive: true, force: true });
  }
});

console.log(`\n${passed} passed`);
