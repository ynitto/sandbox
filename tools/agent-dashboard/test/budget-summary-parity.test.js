'use strict';

// Phase5 / U5: status 射影の reason_codes・contract_version を schema 正本と突き合わせる。
// 追加依存なしで `node test/budget-summary-parity.test.js` で走る。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const project = require('../src/main/project');

const schemaPath = path.resolve(__dirname, '../../../schemas/node-budget-summary.schema.json');

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

test('BUDGET_REASON_CODES は node-budget-summary.schema.json と一致する', () => {
  const schema = JSON.parse(fs.readFileSync(schemaPath, 'utf8'));
  const enumCodes = schema.properties.reason_codes.items.enum;
  assert.deepStrictEqual([...project.BUDGET_REASON_CODES].sort(), [...enumCodes].sort());
  assert.strictEqual(project.BUDGET_SUMMARY_CONTRACT_VERSION, schema.properties.contract_version.const);
});

test('summarizeBudgetProjection は再計算せず kind を射影から畳む', () => {
  const ok = project.summarizeBudgetProjection({
    contract_version: 1,
    source: 'local-ledger',
    can_accept: true,
    reason_codes: ['ok'],
    capacity: { limit: 10, used: 1, reserved: 2 },
    unit: 'tokens',
  }, { fresh: true });
  assert.strictEqual(ok.kind, 'ok');
  assert.strictEqual(ok.reserved, 2);

  const stale = project.summarizeBudgetProjection({
    contract_version: 1,
    source: 'local-ledger',
    can_accept: true,
    reason_codes: ['ok'],
    capacity: { limit: 10, used: 1, reserved: null },
  }, { fresh: false });
  assert.strictEqual(stale.kind, 'unknown');

  const exhausted = project.summarizeBudgetProjection({
    contract_version: 1,
    source: 'local-ledger',
    can_accept: false,
    reason_codes: ['exceeded'],
    capacity: { limit: 10, used: 11, reserved: 0 },
  }, { fresh: true });
  assert.strictEqual(exhausted.kind, 'exhausted');
});

test('readNodeStatuses は budget 射影を添える（旧 viewer 互換で欠落時は null）', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'fleet-budget-'));
  try {
    const sdir = path.join(tmp, 'status');
    fs.mkdirSync(sdir, { recursive: true });
    const now = new Date().toISOString();
    fs.writeFileSync(path.join(sdir, 'pc-a.json'), JSON.stringify({
      node: 'pc-a', host: 'h1', updated_iso: now, fresh_after_sec: 120,
      budget: {
        contract_version: 1, source: 'local-ledger', can_accept: true,
        reason_codes: ['soft'], unit: 'tokens',
        capacity: { limit: 100, used: 40, reserved: 10 },
      },
    }));
    fs.writeFileSync(path.join(sdir, 'pc-b.json'), JSON.stringify({
      node: 'pc-b', updated_iso: now, fresh_after_sec: 120,
    }));
    const nodes = project.readNodeStatuses(tmp);
    assert.strictEqual(nodes[0].budget.reasonCodes[0], 'soft');
    assert.strictEqual(nodes[0].budget.reserved, 10);
    assert.strictEqual(nodes[1].budget, null);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

console.log(`\n${passed} passed`);
