'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const rendererSrc = fs.readFileSync(
  path.join(__dirname, '..', 'src', 'renderer', 'renderer.js'), 'utf8'
);

function grab(name) {
  const at = rendererSrc.indexOf(`function ${name}(`);
  assert.ok(at >= 0, `renderer.js に function ${name} が見つかりません`);
  let depth = 0;
  for (let i = rendererSrc.indexOf('{', at); i < rendererSrc.length; i++) {
    if (rendererSrc[i] === '{') depth += 1;
    else if (rendererSrc[i] === '}') {
      depth -= 1;
      if (depth === 0) return rendererSrc.slice(at, i + 1);
    }
  }
  throw new Error(`function ${name} の閉じ括弧が見つかりません`);
}

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]
));
// eslint-disable-next-line no-new-func
const projectCheckHtml = new Function('esc', `${grab('projectCheckHtml')}; return projectCheckHtml;`)(esc);

assert.strictEqual(projectCheckHtml(null), '');
assert.strictEqual(projectCheckHtml({}), '');

const configured = projectCheckHtml({
  projectCheck: {
    configFile: '/ws/.agents/agent-project.yaml',
    configured: true,
    command: 'make -s smoke',
  },
});
assert.ok(configured.includes('プロジェクト共通チェック'));
assert.ok(configured.includes('make -s smoke'));
assert.ok(configured.includes('設定済み'));
assert.ok(!configured.includes('codd-gate'));
assert.ok(!configured.includes('intake_cmd'));
assert.ok(!configured.includes('data-project-check-open'));

const missing = projectCheckHtml({
  projectCheck: {
    configFile: '/ws/.agents/agent-project.yaml',
    configured: false,
    command: null,
  },
});
assert.ok(missing.includes("regression_cmd: './tools/check'"));
assert.ok(missing.includes('data-project-check-open="/ws/.agents/agent-project.yaml"'));
assert.ok(!missing.includes('codd_gate_'));

const noConfig = projectCheckHtml({
  projectCheck: { configFile: null, configured: false, command: null },
});
assert.ok(noConfig.includes("regression_cmd: './tools/check'"));
assert.ok(!noConfig.includes('data-project-check-open'));

const xss = projectCheckHtml({
  projectCheck: { configured: true, command: '<img src=x onerror=alert(1)>' },
});
assert.ok(!xss.includes('<img'));
assert.ok(xss.includes('&lt;img'));

console.log('consistency-gate-ui: ok');
