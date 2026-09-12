'use strict';

// 画面の一貫性を機械で押さえる（規則は リポジトリ直下の CLAUDE.md「画面（UI）を作る・直す」）。
//
// なぜ要るか: 機能を足すたびに、既存の部品を見ずに独自の見出し・説明文・パネルを組んで
// シンプルさを壊す事故が繰り返し起きている。ここでは「見れば分かる」ではなく
// **機械で分かる形**にした 4 つだけを固定する:
//   1. 端末ミラーと入力欄は共有の実体（.terminal-stage / .composer-shell）を使う
//   2. その見た目の定義は 1 か所だけ（私物の複製を作らない）
//   3. 新しい面に直値の色を足さない（トークンを使う）
//   4. 同じ見出し・説明を 2 つの層が描かない
//   5. 「共有に依頼」はどの入力欄にも同じ形である（会話・タスク・ワークフロー）

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const SRC = path.join(__dirname, '..', 'src');
const read = (p) => fs.readFileSync(path.join(SRC, p), 'utf8');

// index.html の中の、会話の置き場（slot）の <div> だけを取り出す。
function slotOf(html, name) {
  const start = html.indexOf(`<div slot="${name}"`);
  assert.ok(start > 0, `slot="${name}" の置き場が無い`);
  const end = html.indexOf('</statemachine-workbench>', start);
  return html.slice(start, end);
}
function teachingSlot(html) { return slotOf(html, 'teaching'); }

test('端末ミラーと入力欄は会話画面と同じ実体を使う（見た目を作り直さない）', () => {
  const html = read('renderer/index.html');
  const css = read('renderer/styles.css');
  // 会話（#terminal-stage / #composer の .composer-shell）とタスクの相談（#task-terminal /
  // #task-composer）の両方が、同じクラスを名乗る。
  assert.match(html, /id="terminal-stage" class="terminal-stage"/);
  assert.match(html, /id="task-terminal" class="terminal-stage"/);
  assert.match(html, /id="flow-teach-terminal" class="terminal-stage"/);
  assert.strictEqual((html.match(/class="composer-shell"/g) || []).length, 3, '入力欄は会話・タスク・ワークフローで同じクラス');
  // 見た目の宣言（背景・角丸・影）は共有クラスの側にだけある。
  assert.match(css, /^\.terminal-stage \{[^}]*background: var\(--term-bg\)/m);
  assert.match(css, /^\.composer-shell \{[^}]*border-radius: 16px/m);
  for (const clone of ['.task-terminal {', '.task-composer {', '.task-create {', '.task-record {',
    '.flow-teach-terminal {', '.flow-teach-composer {', '.share-card {', '.share-terminal {']) {
    assert.ok(!css.includes(clone), `共有部品の私物な複製がある: ${clone}`);
  }
});

test('タスクの相談の面に、直値の色を足さない（トークンを使う）', () => {
  const css = read('renderer/styles.css');
  const block = css.slice(css.indexOf('#task-teaching {'));
  assert.ok(block, '#task-teaching の規則が無い');
  const hex = block.match(/#[0-9a-fA-F]{3,8}\b/g) || [];
  assert.deepStrictEqual(hex, [], `直値の色ではなくトークン（var(--…)）を使う: ${hex.join(' ')}`);
});

test('見出しと説明は 1 か所だけが描く（埋め込み側と両方で言わない）', () => {
  const slot = teachingSlot(read('renderer/index.html'));
  const workbench = read('renderer/automation/teaching.js');
  // 親が置くのは操作面だけ。見出しはワークベンチが持つ。
  assert.ok(!/<h[1-3][\s>]/.test(slot), '会話の置き場に見出しを置かない（ワークベンチが持つ）');
  // 「手順」タブの編集面は、カードの見出し（AIと編集）とタスク名で足りる。説明を足さない。
  const editor = workbench.slice(workbench.indexOf('function editorSlotHtml('), workbench.indexOf('function html()'));
  assert.ok(!/<h[1-3][\s>]|<p[\s>]/.test(editor), '編集の置き場に見出しや説明文を足さない');
  assert.match(editor, /<slot name="teaching"><\/slot>/);
});

test('画面に解説を常駐させない（仕組みの説明は README に置く）', () => {
  const slot = teachingSlot(read('renderer/index.html'))
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/<label class="sr-only"[\s\S]*?<\/label>/g, '');
  // 段落（<p>）は JS が入れる状態行の器で、マークアップに文章を書かない。
  // hidden は初期状態にすぎないので、隠れているかどうかでは免除しない。
  for (const paragraph of slot.match(/<p\b[^>]*>([\s\S]*?)<\/p>/g) || []) {
    const body = paragraph.replace(/<[^>]+>/g, '').trim();
    assert.strictEqual(body, '', `画面に居座る説明の段落は README へ移す: ${body}`);
  }
  // 段落以外でも、静的な文字の塊を置かない（ボタンやラベルの語だけにする）。
  for (const run of slot.split(/<[^>]+>/)) {
    const text = run.replace(/\s+/g, ' ').trim();
    assert.ok(text.length <= 40, `画面に居座る解説は README へ移す: ${text}`);
  }
});

test('ポップアップメニューは親画面と埋め込み画面のどちらでも外側クリックで閉じる', () => {
  const parent = read('renderer/renderer.js');
  const workbench = read('renderer/automation/renderer.js');
  const selector = "details.more-menu[open], details.run-settings[open]";
  for (const source of [parent, workbench]) {
    assert.ok(source.includes(selector), 'ID の列挙ではなくポップアップ種別をまとめて扱う');
    assert.match(source, /event\.composedPath\(\)/, 'Shadow DOM と slot をまたぐクリック位置を判定する');
    assert.match(source, /path\.includes\(menu\)[\s\S]*menu\.open = false/, 'メニュー内の操作を保ち、外側クリックだけで閉じる');
  }
});

test('共有に依頼はどの入力欄からも同じ形で出せる（会話・タスク・ワークフロー）', () => {
  const html = read('renderer/index.html');
  // 入力先の切替は 3 つ。「共有に依頼」はどの入力欄にもあり、起動方針の選択肢には無い
  for (const prefix of ['input-mode', 'task-mode', 'flow-teach-mode']) {
    for (const target of ['message', 'terminal', 'share']) {
      assert.match(html, new RegExp(`id="${prefix}-${target}"`), `${prefix} に入力先 ${target} が無い`);
    }
  }
  assert.ok(!/<option value="shared">/.test(html), '共有は起動方針ではなく入力先で選ぶ');
});

test('共有の画面は会話画面と同じ部品で組む（私物の複製を作らない）', () => {
  const html = read('renderer/index.html');
  const share = read('renderer/share.js');
  // 端末は会話と同じ .terminal-stage、カードはタスクの概要と同じ .execution-card、
  // 一覧はサイドバー（.list / .list-pick）。
  assert.match(html, /id="share-terminal" class="terminal-stage"/);
  assert.match(share, /el\('section', 'execution-card'\)/);
  assert.match(share, /el\('div', 'execution-card-head'\)/);
  assert.match(share, /el\('button', 'list-pick'\)/);
  assert.ok(!/createElement|innerHTML/.test(share), '画面は既存の el ヘルパで組む');
});

test('ワークフローを教える会話も、タスクと同じ置き場（slot）で会話基盤に載せる', () => {
  const html = read('renderer/index.html');
  const flow = read('renderer/automation/flow.js');
  const slot = slotOf(html, 'flow-teaching');
  // 見出しはワークベンチが持ち、置き場には操作面だけを置く（タスクと同じ規則）
  assert.ok(!/<h[1-3][\s>]/.test(slot), '会話の置き場に見出しを置かない（ワークベンチが持つ）');
  for (const paragraph of slot.match(/<p\b[^>]*>([\s\S]*?)<\/p>/g) || []) {
    const body = paragraph.replace(/<[^>]+>/g, '').trim();
    assert.strictEqual(body, '', `画面に居座る説明の段落は README へ移す: ${body}`);
  }
  for (const run of slot.replace(/<!--[\s\S]*?-->/g, '').replace(/<label class="sr-only"[\s\S]*?<\/label>/g, '').split(/<[^>]+>/)) {
    const text = run.replace(/\s+/g, ' ').trim();
    assert.ok(text.length <= 40, `画面に居座る解説は README へ移す: ${text}`);
  }
  assert.match(flow, /<slot name="flow-teaching"><\/slot>/);
});
