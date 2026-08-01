'use strict';

// 一貫性ゲート（codd-gate）の結線状態がプロジェクト情報ペイロードに載ることを検証する。
// 追加依存なしで `node test/consistency-gate.test.js` で走る。
//
// 見ているのは「設定 yaml の regression_cmd / intake_cmd が codd-gate を指しているか」。
// 「キーが空でないか」ではない——両キーは外部ゲートを差し込む汎用フックで、
// agent-project.yaml.example:171 は `regression_cmd: make -s smoke` を正当な例に挙げている。
// 判定は tools/agent-project/codd_gate_wiring.py:71-72 と同じ語順マッチ。
// コマンドは実行しないし、ここから設定を書き換えもしない（読み取り専用）。

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

// 設定 yaml に任意の行を足したワークスペースを作る。extra が null なら設定ファイル自体を置かない。
function mkWorkspace(extra) {
  const ws = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-gate-'));
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

test('regression_cmd / intake_cmd が両方あれば結線済みとして載る', () => {
  const ws = mkWorkspace(
    "regression_cmd: 'codd-gate verify --base \"$KIRO_BASE_REV\" --repos repos.json'\n" +
      "intake_cmd: 'codd-gate tasks --debt --repos repos.json'\n"
  );
  try {
    const gate = project.readProject(ws, {}).consistencyGate;
    assert.strictEqual(gate.regressionConfigured, true);
    assert.strictEqual(gate.intakeConfigured, true);
    assert.strictEqual(gate.regressionWired, true);
    assert.strictEqual(gate.intakeWired, true);
    // 表示用の文字列はクォートを剥がした素の値
    assert.strictEqual(gate.regressionCmd, 'codd-gate verify --base "$KIRO_BASE_REV" --repos repos.json');
    assert.strictEqual(gate.intakeCmd, 'codd-gate tasks --debt --repos repos.json');
    assert.strictEqual(gate.configFile, path.join(ws, '.agents', 'agent-project.yaml'));
    assert.strictEqual(gate.wired, true);
  } finally {
    fs.rmSync(ws, { recursive: true, force: true });
  }
});

test('片方だけの設定は片方だけ結線済みになる', () => {
  const ws = mkWorkspace("regression_cmd: 'codd-gate verify --base \"$KIRO_BASE_REV\"'\n");
  try {
    const gate = project.readProject(ws, {}).consistencyGate;
    assert.strictEqual(gate.regressionConfigured, true);
    assert.strictEqual(gate.intakeConfigured, false);
    assert.strictEqual(gate.regressionWired, true);
    assert.strictEqual(gate.intakeWired, false);
    assert.strictEqual(gate.intakeCmd, null);
    assert.strictEqual(gate.wired, false);
  } finally {
    fs.rmSync(ws, { recursive: true, force: true });
  }
});

// 本命。キーの有無で判定すると、この設定が「一貫性ゲート有効」と表示されてしまう。
test('codd-gate を指していないコマンドは未結線（値は表示用に残す）', () => {
  const ws = mkWorkspace("regression_cmd: make -s smoke\nintake_cmd: agent-project enqueue\n");
  try {
    const gate = project.readProject(ws, {}).consistencyGate;
    assert.strictEqual(gate.regressionConfigured, true);
    assert.strictEqual(gate.intakeConfigured, true);
    assert.strictEqual(gate.regressionWired, false);
    assert.strictEqual(gate.intakeWired, false);
    // 「未結線＝何も設定されていない」ではないことを画面が言えるよう、値は落とさない
    assert.strictEqual(gate.regressionCmd, 'make -s smoke');
    assert.strictEqual(gate.intakeCmd, 'agent-project enqueue');
  } finally {
    fs.rmSync(ws, { recursive: true, force: true });
  }
});

// 語順は見るが --repos 等の追加引数は問わない（codd_gate_wiring.py の注記と同じ）。
test('--repos が無い手書き設定も結線済みと判定する', () => {
  const ws = mkWorkspace(
    "regression_cmd: 'codd-gate verify --base \"$KIRO_BASE_REV\"'\n" +
      "intake_cmd: 'codd-gate tasks --debt'\n"
  );
  try {
    const gate = project.readProject(ws, {}).consistencyGate;
    assert.strictEqual(gate.wired, true);
  } finally {
    fs.rmSync(ws, { recursive: true, force: true });
  }
});

test('設定はあるがキーが無ければ未結線', () => {
  const ws = mkWorkspace('planner: none\n');
  try {
    const gate = project.readProject(ws, {}).consistencyGate;
    assert.strictEqual(gate.regressionConfigured, false);
    assert.strictEqual(gate.intakeConfigured, false);
    assert.strictEqual(gate.regressionWired, false);
    assert.strictEqual(gate.intakeWired, false);
    assert.strictEqual(gate.regressionCmd, null);
  } finally {
    fs.rmSync(ws, { recursive: true, force: true });
  }
});

test('空白だけのコマンドは未設定として扱う', () => {
  const ws = mkWorkspace("regression_cmd: '   '\nintake_cmd: '   '\n");
  try {
    const gate = project.readProject(ws, {}).consistencyGate;
    assert.strictEqual(gate.regressionConfigured, false);
    assert.strictEqual(gate.intakeConfigured, false);
    assert.strictEqual(gate.regressionWired, false);
    assert.strictEqual(gate.intakeWired, false);
    assert.strictEqual(gate.regressionCmd, null);
    assert.strictEqual(gate.intakeCmd, null);
  } finally {
    fs.rmSync(ws, { recursive: true, force: true });
  }
});

test('YAML の folded scalar と null を実効値として扱う', () => {
  const ws = mkWorkspace(
    'regression_cmd: >-\n' +
      '  codd-gate verify\n' +
      '  --base "$KIRO_BASE_REV"\n' +
      'intake_cmd: null\n'
  );
  try {
    const gate = project.readProject(ws, {}).consistencyGate;
    assert.strictEqual(gate.regressionCmd, 'codd-gate verify --base "$KIRO_BASE_REV"');
    assert.strictEqual(gate.regressionConfigured, true);
    assert.strictEqual(gate.regressionWired, true);
    assert.strictEqual(gate.intakeCmd, null);
    assert.strictEqual(gate.intakeConfigured, false);
  } finally {
    fs.rmSync(ws, { recursive: true, force: true });
  }
});

test('folded scalar の段落境界を PyYAML と同じ改行として扱う', () => {
  const ws = mkWorkspace(
    'regression_cmd: >-\n' +
      '  codd-gate verify\n' +
      '\n' +
      '  --base "$KIRO_BASE_REV"\n' +
      'intake_cmd: >-\n' +
      '  codd-gate tasks\n' +
      '\n' +
      '  --debt\n'
  );
  try {
    const gate = project.readProject(ws, {}).consistencyGate;
    assert.strictEqual(gate.regressionCmd, 'codd-gate verify\n--base "$KIRO_BASE_REV"');
    assert.strictEqual(gate.intakeCmd, 'codd-gate tasks\n--debt');
    assert.strictEqual(gate.regressionWired, false);
    assert.strictEqual(gate.intakeWired, false);
  } finally {
    fs.rmSync(ws, { recursive: true, force: true });
  }
});

test('引用された null は文字列のコマンドとして残す', () => {
  const ws = mkWorkspace("intake_cmd: 'null'\n");
  try {
    const gate = project.readProject(ws, {}).consistencyGate;
    assert.strictEqual(gate.intakeCmd, 'null');
    assert.strictEqual(gate.intakeConfigured, true);
    assert.strictEqual(gate.intakeWired, false);
  } finally {
    fs.rmSync(ws, { recursive: true, force: true });
  }
});

test('YAML の literal scalar を実効値として扱う', () => {
  const ws = mkWorkspace('intake_cmd: |-\n  codd-gate tasks --debt\n');
  try {
    const gate = project.readProject(ws, {}).consistencyGate;
    assert.strictEqual(gate.intakeCmd, 'codd-gate tasks --debt');
    assert.strictEqual(gate.intakeConfigured, true);
    assert.strictEqual(gate.intakeWired, true);
  } finally {
    fs.rmSync(ws, { recursive: true, force: true });
  }
});

test('~/.agents の実効設定を採り、ワークスペース設定を優先する', () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-gate-home-'));
  const ws = mkWorkspace(null);
  const originalHomedir = os.homedir;
  try {
    os.homedir = () => home;
    fs.mkdirSync(path.join(home, '.agents'), { recursive: true });
    fs.writeFileSync(
      path.join(home, '.agents', 'agent-project.yaml'),
      "regression_cmd: 'codd-gate verify --base \"$KIRO_BASE_REV\"'\n" +
        "intake_cmd: 'codd-gate tasks --debt'\n",
      'utf8'
    );
    const gate = project.readProject(ws, {}).consistencyGate;
    assert.strictEqual(gate.configFile, path.join(home, '.agents', 'agent-project.yaml'));
    assert.strictEqual(gate.regressionWired, true);
    assert.strictEqual(gate.intakeWired, true);
    fs.mkdirSync(path.join(ws, '.agents'), { recursive: true });
    fs.writeFileSync(
      path.join(ws, '.agents', 'agent-project.yaml'),
      "regression_cmd: make -s smoke\nintake_cmd: agent-project enqueue\n",
      'utf8'
    );
    const localGate = project.readProject(ws, {}).consistencyGate;
    assert.strictEqual(localGate.configFile, path.join(ws, '.agents', 'agent-project.yaml'));
    assert.strictEqual(localGate.regressionConfigured, true);
    assert.strictEqual(localGate.intakeConfigured, true);
    assert.strictEqual(localGate.regressionWired, false);
    assert.strictEqual(localGate.intakeWired, false);
  } finally {
    os.homedir = originalHomedir;
    fs.rmSync(home, { recursive: true, force: true });
    fs.rmSync(ws, { recursive: true, force: true });
  }
});

test('壊れたローカル JSON が global 設定へフォールバックしない', () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-gate-home-'));
  const ws = mkWorkspace(null);
  const originalHomedir = os.homedir;
  try {
    os.homedir = () => home;
    fs.mkdirSync(path.join(home, '.agents'), { recursive: true });
    fs.writeFileSync(
      path.join(home, '.agents', 'agent-project.yaml'),
      "regression_cmd: 'codd-gate verify --base \\\"$KIRO_BASE_REV\\\"'\n" +
        "intake_cmd: 'codd-gate tasks --debt'\n",
      'utf8'
    );
    fs.mkdirSync(path.join(ws, '.agents'), { recursive: true });
    const localFile = path.join(ws, '.agents', 'agent-project.json');
    fs.writeFileSync(localFile, '{ broken', 'utf8');

    const gate = project.readProject(ws, {}).consistencyGate;
    assert.strictEqual(gate.configFile, localFile);
    assert.strictEqual(gate.regressionConfigured, false);
    assert.strictEqual(gate.intakeConfigured, false);
    assert.strictEqual(gate.wired, false);
  } finally {
    os.homedir = originalHomedir;
    fs.rmSync(home, { recursive: true, force: true });
    fs.rmSync(ws, { recursive: true, force: true });
  }
});

console.log(`\n${passed} passed`);
