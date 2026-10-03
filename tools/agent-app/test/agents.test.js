'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const agents = require('../src/main/agents');

test('host availability keeps a failed probe as unknown on cache hits', async () => {
  let calls = 0;
  const shellFor = () => ({
    async run() {
      calls += 1;
      return { ok: false, output: '' };
    },
  });
  const commands = ['cache-failure-probe-agent'];

  const first = await agents.hostAvailability('test-distro', commands, { shellFor });
  const second = await agents.hostAvailability('test-distro', commands, { shellFor });

  assert.equal(first, null);
  assert.equal(second, null, 'cached failure must not turn into an empty availability map');
  assert.equal(calls, 1, 'the failure result is still cached for the normal TTL');
});
