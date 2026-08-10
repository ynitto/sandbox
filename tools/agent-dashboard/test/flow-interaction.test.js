'use strict';

// coherence: code=tools/agent-dashboard/src/features/agent-project/main/flow.js, doc=docs/plans/2026-08-10-agent-tools-human-extract-retrieve-implementation-plan.md

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const flow = require('../src/features/agent-project/main/flow.js');

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'flow-interaction-'));
const bus = path.join(root, 'bus');
const runId = 'run-human';
const runDir = path.join(bus, 'runs', runId);
const interactionId = 'ix-1234567890abcdef';
fs.mkdirSync(path.join(runDir, 'interactions', interactionId, 'responses'), { recursive: true });
fs.writeFileSync(path.join(runDir, 'meta.json'), JSON.stringify({
  status: 'running', request: '確認する', created_at: '2026-08-10T00:00:00Z',
}));
fs.writeFileSync(path.join(runDir, 'graph.json'), JSON.stringify({
  nodes: { ask: { goal: '公開可否を確認', kind: 'human', deps: [] } },
}));
fs.writeFileSync(path.join(runDir, 'interactions', interactionId, 'request.json'), JSON.stringify({
  version: 1, interaction_id: interactionId, run_id: runId, node_id: 'ask', mode: 'approval',
  prompt: '公開してよいですか', audience: ['reviewer'], options: [], default_option: null,
  created_at: '2026-08-10T00:00:00Z', expires_at: '2099-08-17T00:00:00Z',
}));

const before = flow.readRun(runDir);
assert.strictEqual(before.interactions.length, 1);
assert.strictEqual(before.nodes.ask.interaction.prompt, '公開してよいですか');

const written = flow.writeInteractionResponse(bus, runId, interactionId, {
  decision: 'approved', comment: '確認済み',
});
assert.strictEqual(written.answer.decision, 'approved');
assert.ok(fs.existsSync(path.join(runDir, 'interactions', interactionId, 'responses', `${written.response_id}.json`)));
assert.ok(!fs.existsSync(path.join(runDir, 'interactions', interactionId, 'resolution.json')),
  'dashboard は resolution を書かない');
assert.ok(!fs.existsSync(path.join(runDir, 'results', 'ask.json')),
  'dashboard は result を書かない');
assert.strictEqual(flow.readRun(runDir).interactions[0].responded, true);
assert.throws(() => flow.writeInteractionResponse(bus, runId, interactionId, { decision: 'maybe' }), /承認/);

console.log('6 tests passed');
