'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..', 'src', 'renderer');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const renderer = fs.readFileSync(path.join(root, 'renderer.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'styles.css'), 'utf8');

assert.match(html, /<meta name="viewport" content="width=device-width, initial-scale=1"/);
assert.match(html, /data-tab="history"[^>]*>成果</);
assert.match(html, /data-tab="cowork"[^>]*[^>]*>定期・定型作業</);

for (const id of ['dlg-settings', 'dlg-developer-tools', 'developer-project-info', 'btn-open-developer-tools']) {
  assert.ok(html.includes(`id="${id}"`), `${id} が必要です`);
}

const normalSettings = html.slice(html.indexOf('<dialog id="dlg-settings"'), html.indexOf('<dialog id="dlg-developer-tools"'));
for (const id of ['cfg-roots', 'cfg-refresh', 'cfg-notify']) {
  assert.ok(normalSettings.includes(`id="${id}"`), `通常設定に ${id} が必要です`);
}
for (const id of ['cfg-flow-bus', 'cfg-flow-lockdir', 'cfg-project-command', 'cfg-agent-cli']) {
  assert.ok(!normalSettings.includes(`id="${id}"`), `通常設定に ${id} を出しません`);
  assert.ok(html.includes(`id="${id}"`), `開発者ツールに ${id} を残します`);
}

assert.ok(renderer.includes('function openDeveloperTools()'));
assert.ok(renderer.includes('function developerProjectInfoHtml()'));
assert.ok(renderer.includes('data-open-developer'));
assert.ok(renderer.includes('内部ログを開く'));
assert.ok(!renderer.includes('<div class="section-title">動作ログ（直近 80 行）</div>'));
assert.ok(!renderer.includes('<summary>実行環境</summary>'));

assert.match(css, /\.developer-facts/);
assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
assert.match(css, /button:focus-visible/);

console.log('user-centered-ui: all tests passed');
