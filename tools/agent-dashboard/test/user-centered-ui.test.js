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
assert.match(html, /data-tab="cowork"[^>]*[^>]*>定常業務</);
assert.ok(!html.includes('定期・定型作業'));
assert.ok(!renderer.includes('定期・定型作業'));

for (const id of [
  'dlg-settings',
  'dlg-advanced-settings',
  'btn-open-advanced-settings',
  'dlg-technical-info',
  'technical-project-info',
]) {
  assert.ok(html.includes(`id="${id}"`), `${id} が必要です`);
}

const normalSettings = html.slice(html.indexOf('<dialog id="dlg-settings"'), html.indexOf('<dialog id="dlg-advanced-settings"'));
const advancedSettings = html.slice(html.indexOf('<dialog id="dlg-advanced-settings"'), html.indexOf('<dialog id="dlg-technical-info"'));
const technicalInfo = html.slice(html.indexOf('<dialog id="dlg-technical-info"'), html.indexOf('<dialog id="dlg-need-output"'));
for (const id of ['cfg-roots', 'cfg-refresh', 'cfg-notify']) {
  assert.ok(normalSettings.includes(`id="${id}"`), `通常設定に ${id} が必要です`);
}
for (const id of ['cfg-flow-bus', 'cfg-flow-lockdir', 'cfg-project-command', 'cfg-agent-cli']) {
  assert.ok(!normalSettings.includes(`id="${id}"`), `通常設定に ${id} を出しません`);
  assert.ok(advancedSettings.includes(`id="${id}"`), `詳細設定に ${id} を残します`);
  assert.ok(!technicalInfo.includes(`id="${id}"`), `技術情報に ${id} を出しません`);
}
assert.match(advancedSettings, /<h2>詳細設定<\/h2>/);
assert.match(technicalInfo, /<h2>技術情報<\/h2>/);
assert.ok(!advancedSettings.includes('technical-project-info'));
assert.ok(!technicalInfo.includes('btn-save-advanced-settings'));

assert.ok(renderer.includes('function openAdvancedSettings()'));
assert.ok(renderer.includes('function openTechnicalInfo()'));
assert.ok(renderer.includes('function technicalProjectInfoHtml()'));
assert.ok(!renderer.includes('function developerProjectInfoHtml()'));
assert.ok(renderer.includes('data-open-technical-info'));
assert.ok(renderer.includes('内部ログを開く'));
assert.ok(!renderer.includes('data-open-developer'));
assert.ok(!renderer.includes('<div class="section-title">動作ログ（直近 80 行）</div>'));
assert.ok(!renderer.includes('<summary>実行環境</summary>'));

assert.match(css, /\.developer-facts/);
assert.match(css, /\.developer-log\s*\{[^}]*overflow-wrap:\s*anywhere/s);
assert.match(css, /\.developer-log\s*>\s*div\s*\{[^}]*word-break:\s*break-word/s);
assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
assert.match(css, /button:focus-visible/);

console.log('user-centered-ui: all tests passed');
