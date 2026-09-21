'use strict';

// 画面の一貫性を機械で押さえる（規則は リポジトリ直下の CLAUDE.md「画面（UI）を作る・直す」）。
//
// なぜ要るか: 機能を足すたびに、既存の部品を見ずに独自の見出し・説明文・パネルを組んで
// シンプルさを壊す事故が繰り返し起きている。ここでは「見れば分かる」ではなく
// **機械で分かる形**にしたものだけを固定する:
//   1. 端末ミラーと入力欄は共有の実体（.terminal-stage / .composer-shell）を使う
//   2. その見た目の定義は 1 か所だけ（私物の複製を作らない）
//   3. 新しい面に直値の色を足さない（トークンを使う）
//   4. 同じ見出し・説明を 2 つの層が描かない
//   5. 「共有に依頼」はどの入力欄にも同じ形である（会話・タスク・ワークフロー）
//   6. 人と人のやり取り（ひとこと）は 1 つの部品を 2 つの置き場に載せる（会話画面・共有画面）
//   7. あとから足した操作も、既存の器（.settings-popover / .message-action / .startup-row）を借りる

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

test('領域の見出し帯は 1 つの形（会話・会話を検索・タスク・共有・受信箱）', () => {
  const html = read('renderer/index.html');
  const css = read('renderer/styles.css');
  // 1. 主要な領域はどれも .area-head を名乗る（寸法・罫線・狭い幅の逃げを 1 か所で持つ）
  for (const head of ['<header id="chat-head" class="area-head">', '<header id="automation-head" class="area-head">',
    '<header id="share-head" class="area-head">']) assert.ok(html.includes(head), `${head} が無い`);
  assert.strictEqual((html.match(/class="area-head"/g) || []).length, 5, '見出し帯は 会話・会話を検索・タスク・共有・受信箱 の 5 つ');
  // 2. 寸法と題名の規則は .area-head の側にだけ置く（画面ごとに書き直さない）
  assert.match(css, /^\.area-head \{[^}]*min-height: 60px/m);
  assert.match(css, /^\.area-head \.title \{[^}]*font-weight: 650/m);
  assert.ok(!/^#chat-head \{/m.test(css), '会話ヘッダーで寸法を書き直さない');
  assert.ok(!/^#share-head/m.test(css), '共有ヘッダーで寸法を書き直さない');
  // 3. 狭い幅で、メニューを開くボタンに題名が隠れない（逃げはすべての見出し帯に効く）
  assert.match(css, /@media \(max-width: 820px\)[^@]*\.area-head \{ padding-left: 62px; \}/);
  // 4. 変更パネルの見出しも、隣の見出し帯と高さと罫線を合わせる
  assert.match(css, /^#changes > \.side-head \{[^}]*min-height: 60px/m);
  // 5. タブの列に置くのはタブだけ（操作のボタンを混ぜない）
  for (const nav of html.match(/<nav class="views"[\s\S]*?<\/nav>/g) || []) {
    assert.ok(!/class="(?:small|primary|quiet|danger)"/.test(nav), `タブの列に操作のボタンが混ざっている: ${nav}`);
    assert.match(nav, /aria-selected="/, 'タブは選択状態を持つ');
  }
  // 6. 狭い幅でもタブは消さない（消すと「ファイル」「参加者」へ行けなくなる）
  assert.ok(!/\.views[^{]*\{[^}]*display: none/.test(css), '狭い幅でタブを消さない（詰めて残す）');
  // 7. 名前を持たない「その他」の menu は、置き場に依らず同じ ••• の印にする
  //    （サイドバーのリポジトリ管理・会話ヘッダー・ファイル。「定型」のように名前がある menu は別）
  const dotted = (html.match(/<details[^>]*class="[^"]*more-menu[^"]*"[\s\S]*?<\/summary>/g) || [])
    .filter((menu) => /aria-label="[^"]*(?:その他|管理)/.test(menu));
  assert.ok(dotted.length >= 3, `••• の menu が見つからない: ${dotted.length}`);
  for (const menu of dotted) {
    assert.match(menu, /class="[^"]*more-dots/, `••• の印を使っていない: ${menu}`);
    assert.match(menu, /<summary[^>]*>•••<\/summary>/, `「その他」の印は ••• に揃える: ${menu}`);
  }
  // 8. 共有の見出し帯に置くのは、その画面の操作だけ（この PC の受け持ちは「参加者」の側）
  const shareHead = html.match(/<header id="share-head"[\s\S]*?<\/header>/)?.[0] || '';
  assert.ok(!shareHead.includes('share-accept'), '「自動で引き受ける」は見出し帯に置かない');
});

test('端末ミラーと入力欄は会話画面と同じ実体を使う（見た目を作り直さない）', () => {
  const html = read('renderer/index.html');
  const css = read('renderer/styles.css');
  // 会話（#terminal-stage / #composer の .composer-shell）とタスクの相談（#task-terminal /
  // #task-composer）の両方が、同じクラスを名乗る。
  assert.match(html, /id="terminal-stage" class="terminal-stage"/);
  assert.match(html, /id="task-terminal" class="terminal-stage"/);
  assert.match(html, /id="flow-teach-terminal" class="terminal-stage"/);
  assert.strictEqual((html.match(/class="composer-shell"/g) || []).length, 4, '入力欄は会話・タスク・ワークフロー・共有で同じクラス');
  // 見た目の宣言（背景・角丸・影）は共有クラスの側にだけある。
  assert.match(css, /^\.terminal-stage \{[^}]*background: var\(--term-bg\)/m);
  assert.match(css, /^\.composer-shell \{[^}]*border-radius: 16px/m);
  for (const clone of ['.task-terminal {', '.task-composer {', '.task-create {', '.task-record {',
    '.flow-teach-terminal {', '.flow-teach-composer {', '.share-card {', '.share-terminal {', '.share-talk {']) {
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

test('ひとことは 1 つの部品を 2 つの置き場に載せる（会話画面と共有画面）', () => {
  const html = read('renderer/index.html');
  const css = read('renderer/styles.css');
  const talk = read('renderer/talk.js');
  const renderer = read('renderer/renderer.js');
  const share = read('renderer/share.js');
  // 器は会話履歴と同じ折りたたみ、中身は同じ .talk。描くのは talk.js の 1 つだけ。
  assert.match(html, /id="share-talk" class="conversation-history"/);
  assert.match(html, /id="share-thread" class="conversation-history"/);
  assert.strictEqual((html.match(/class="talk"/g) || []).length, 2, 'やり取りの器は会話画面と共有画面の 2 つ');
  assert.match(renderer, /Talk\.render\(/);
  assert.match(share, /Talk\.render\(/);
  // 吹き出しの見た目は talk の側にだけあり、会話の吹き出しと同じトークンで書く
  assert.match(css, /^\.talk-bubble \{[^}]*background: var\(--code-bg\)/m);
  assert.match(css, /^\.talk-line\.mine \.talk-bubble \{[^}]*background: var\(--accent-bg\)/m);
  const hex = (css.slice(css.indexOf('.talk {'), css.indexOf('.unread {')).match(/#[0-9a-fA-F]{3,8}\b/g) || []);
  assert.deepStrictEqual(hex, [], `直値の色ではなくトークンを使う: ${hex.join(' ')}`);
  assert.ok(!/innerHTML/.test(talk), '本文は文字として入れる（差し込みを作らない）');
});

test('新しい操作は既存の部品で組む（確認待ちの行き先・定型の依頼・前回の値）', () => {
  const html = read('renderer/index.html');
  const css = read('renderer/styles.css');
  const renderer = read('renderer/renderer.js');
  const workbench = read('renderer/automation/renderer.js');
  // 1. 確認待ちは答えを並べる面を持たない。状態の印のまま、端末操作（既存の入力先）へ連れて行く。
  assert.strictEqual((html.match(/class="settings-popover"/g) || []).length, 5, '会話・作成・編集・取り込みの実行設定は共通ポップオーバーを使う');
  assert.ok(!css.includes('.phase-popover {'), '共有部品の私物な複製がある: .phase-popover');
  assert.ok(!css.includes('.attention-panel {'), '共有部品の私物な複製がある: .attention-panel');
  assert.ok(!css.includes('.phase-menu {'), '確認待ちに自分用のパネルを作らない');
  // 2. 回答の下・依頼の下の操作は .message-actions / .message-action だけを使う
  for (const source of [renderer]) {
    const buttons = source.match(/el\('button', '([a-z- ]*)', '(?:変更をコミット|入力欄に戻す)/g) || [];
    for (const button of buttons) assert.match(button, /'message-action'/);
  }
  // 3. 設定に足した 2 群は、起動時アクションと同じ行（.startup-row）と同じ見出し（.settings-group-head）
  assert.match(html, /<div class="settings-group">\s*<div class="settings-group-head"><span><strong>定型の依頼<\/strong>/);
  assert.match(renderer, /el\('div', 'startup-row quick-row'\)/);
  // 4. 今回足した規則に直値の色を入れない（確認待ちの行き先と定型の依頼の行）
  const added = [...(css.match(/^\.phase\.answerable[^{]*\{[^}]*\}$/gm) || []), ...(css.match(/^\.quick-row[^{]*\{[^}]*\}$/gm) || [])];
  assert.strictEqual(added.length, 4, '確認待ちの行き先と定型の依頼の行の規則が揃っていない');
  assert.deepStrictEqual(added.join('\n').match(/#[0-9a-fA-F]{3,8}\b/g) || [], [], '直値の色ではなくトークン（var(--…)）を使う');
  // 5. 実行条件の「前回」は補助の 1 行（.muted）で、新しいカードや見出しを作らない
  assert.match(workbench, /<small class="muted">前回: /);
  assert.ok(!/<h3>前回/.test(workbench), '前回の値に見出しを足さない');
  // 6. 吹き出しの下の操作（入力欄に戻す・フォーク）は、会話と検索のプレビューで同じ部品・同じ端
  const search = read('renderer/sessionSearch.js');
  assert.match(search, /button\('フォーク', 'message-action'/, '検索のプレビューも .message-action を使う');
  assert.ok(!/button\('フォーク', 'small quiet'/.test(search), 'フォークだけ別の見た目にしない');
  assert.match(renderer, /el\('button', 'message-action', 'フォーク'\)/);
  assert.match(css, /\.response-turn > \.message-actions \{[^}]*align-self: stretch/, '操作の行は吹き出しの外で同じ端にそろえる');
  assert.match(renderer, /'response-turn user-turn'/, '依頼も応答と同じ組み立て（吹き出し＋下の操作）にする');
});

test('保存データの整理は設定の既存の器（設定の行・状態の印・足元の集計）で組む', () => {
  const html = read('renderer/index.html');
  const css = read('renderer/styles.css');
  const storage = read('renderer/storage.js');          // 保存データの面を描くのはこのモジュール
  const panel = html.match(/<section data-settings-panel="storage"[\s\S]*?<\/section>/)?.[0] || '';
  assert.ok(panel, '設定に「保存データ」の面が無い');
  // 1. タブ名で分かることを本文で繰り返さない（面の中に見出しと説明の常駐を作らない）
  assert.ok(!/<h[1-4][\s>]/.test(panel), '設定の面に見出しを置かない（タブ名が名乗る）');
  assert.ok(!/<p[\s>]/.test(panel), '仕組みの説明は README に置く');
  // 2. 種類の行は設定の行（.setting-check）、大きさは状態の印（.status）をそのまま借りる
  assert.match(storage, /el\('label', 'setting-check'\)/, '種類の行は設定の行を借りる');
  assert.match(storage, /el\('span', 'status', item\.bytes/, '大きさは状態の印を借りる');
  assert.ok(!/\.cleanup-row|\.cleanup-item|\.cleanup-size/.test(css), '設定の行の私物な複製を作らない');
  // 3. 足元の集計と操作は「更新」「実行環境」と同じ器（.environment-status + .row）
  assert.match(panel, /<div class="environment-status">/);
  const cleanup = panel.slice(panel.indexOf('<div id="cleanup-items">'));
  assert.strictEqual((cleanup.match(/class="row"/g) || []).length, 2, '集計と操作は .row に並べる');
  // 4. 色は主操作と状態の区別にだけ。この面の主ボタンはダイアログの「保存」なので、削除は .danger
  assert.match(panel, /id="cleanup-run" class="danger"/);
  assert.ok(!/id="cleanup-run"[^>]*class="[^"]*primary/.test(panel), '1 つの面に主ボタンを 2 つ置かない');
  // 5. 足した規則に直値の色を入れない
  const added = css.match(/^\.setting-check > \.status \{[^}]*\}$/m) || [];
  assert.strictEqual(added.length, 1, '右端の大きさの規則が 1 つだけある');
  assert.deepStrictEqual(added.join('').match(/#[0-9a-fA-F]{3,8}\b/g) || [], [], '直値の色ではなくトークン（var(--…)）を使う');
});

test('受信箱は既存の部品（メニューの領域・一覧の行・件数の印）で組み、判定は main に置く', () => {
  const html = read('renderer/index.html');
  const css = read('renderer/styles.css');
  const renderer = read('renderer/renderer.js');
  const preload = read('preload.js');
  const navigation = read('renderer/navigation.js');
  // 1. 受信箱は主要メニューの 1 領域（会話・タスク・ワークフロー・共有と同じ並び）。一覧は同じ .list、件数は「共有」と同じ .unread
  const menu = html.match(/<nav id="areas"[\s\S]*?<\/nav>/)?.[0] || '';
  assert.match(menu, /id="area-inbox">[\s\S]*?<span>受信箱<\/span>/);
  assert.match(html, /<ul id="inbox-items" class="list grow" hidden><\/ul>/);
  assert.match(navigation, /inbox: \{ label: '受信箱', createLabel: '新しい会話', listId: 'inbox-items' \}/);
  assert.match(renderer, /el\('li', `row-item\$\{item\.queue === 'action' \? ' attention' : ''\}`\)/, '要対応の行は会話一覧の「確認待ち」と同じ印');
  assert.match(renderer, /const pick = el\('button', 'list-pick'\);[\s\S]*?pick\.onclick = \(\) => openAttentionItem/, '項目は会話一覧と同じ .list-pick');
  assert.match(renderer, /const button = \$\('area-inbox'\);[\s\S]*?badge = el\('span', 'unread'\)/, '件数は「共有」と同じ .unread の印');
  // 2. 見出しは他の領域と同じ .area-head、本文は 1 行だけ。受信箱に見た目の規則・私物の部品を足さない
  // 課題（agent-audit の洞察）の本文はタスクの概要と同じ .execution-card の並び（.issue-cards は並べる器だけ）
  assert.match(html, /<section id="inbox-area" aria-label="受信箱" hidden>\s*<header class="area-head">\s*<div class="area-heading">\s*<div class="title">受信箱<\/div>\s*<p id="inbox-meta"><\/p>\s*<\/div>\s*<\/header>\s*<div id="inbox-body">\s*<div class="blank compact"><p id="inbox-sub"><\/p><\/div>\s*<div id="inbox-issues" class="issue-cards" hidden><\/div>\s*<\/div>\s*<\/section>/);
  assert.match(css, /^#share-area, #inbox-area \{/m, '本文の面は「共有」と同じ規則を共有する');
  assert.deepStrictEqual(css.match(/^#inbox[^{]*\{/gm) || [], ['#inbox-body {'], '本文のスクロール領域以外に専用の規則を足さない');
  for (const clone of ['.inbox-card {', '.inbox-item {', '.attention-inbox {', '.inbox-panel {']) assert.ok(!css.includes(clone), `共有部品の私物な複製がある: ${clone}`);
  // 3. 未読・要対応の判定は main（attention:list）。renderer は投影を出すだけ
  assert.match(preload, /attention: \{\s*list: \(\) => invoke\('attention:list'\),\s*seen: \(key, resultAt\) => invoke\('attention:seen'/);
  assert.ok(!/queue\s*[:=]\s*['"](?:unread|action)['"]/.test(renderer), 'renderer で未読・要対応を決めない');
  // 4. 項目から行くのは既存の画面（通知と同じ openSessionInRepo、領域の切替と一覧の選択）。答え方や画面を作らない
  assert.match(renderer, /openSessionInRepo\(t\.repo, t\.id, \{ answer: item\.queue === 'action' \}\)/);
  assert.match(renderer, /await showArea\(area\);\s*if \(t\.id\) await selectAreaItem\(area/);
  assert.ok(!html.includes('id="inbox-answer"') && !html.includes('inbox-fork'), '受信箱に答える面やフォークの複製を置かない');
  // 5. 説明は 1 行だけ（本文の .blank の p）。段落を並べない
  assert.strictEqual((html.match(/id="inbox-sub"/g) || []).length, 1);
});

test('利用状況の面は設定の既存の器（設定の行・状態の印・足元の集計）で組む', () => {
  const html = read('renderer/index.html');
  const css = read('renderer/styles.css');
  const audit = read('renderer/audit.js');            // 利用状況の面を描くのはこのモジュール
  const panel = html.match(/<section data-settings-panel="audit"[\s\S]*?<div id="settings-error"/)?.[0] || '';
  assert.ok(panel, '設定に「利用状況」の面が無い');
  // 1. タブ名で分かることを本文で繰り返さない（見出しと説明の常駐を作らない）
  assert.ok(!/<h[1-4][\s>]/.test(panel), '設定の面に見出しを置かない（タブ名が名乗る）');
  // 2. 設定の行と足元の集計は既存の器をそのまま借りる
  assert.match(panel, /class="wt-table settings-table/);
  assert.match(panel, /class="setting-field"/);
  assert.ok(!/\.audit-row|\.audit-card|\.audit-panel|\.audit-list/.test(css), '設定の行の私物な複製を作らない');
  // 3. 並びと印も既存の部品（.row / .spacer / .status / .sub）を使う
  assert.match(panel, /class="row allocation-context"/, '操作は .row を借りる');
  assert.ok(!/<details|id="audit-evaluation/.test(panel), '実績を畳まず、評価結果を表示しない');
  assert.ok(!/id="usage-mode"|id="usage-local-model"/.test(panel), '配分とモデルの編集は実行制御に集約');
  // 4. 色は主操作と状態の区別にだけ。この面の主ボタンはダイアログの「保存」
  assert.ok(!/id="audit-run"[^>]*class="[^"]*primary/.test(panel), '1 つの面に主ボタンを 2 つ置かない');
  assert.ok(!/class="[^"]*danger/.test(panel), '普通の操作を警告色で塗らない');
  // 5. 縦並びの一覧は既存の間隔の規則へ相乗りする（同じ見た目を別の名前で定義し直さない）
  const list = css.match(/^\.startup-actions, #audit-usage, #skills-list \{[^}]*\}$/m) || [];
  assert.strictEqual(list.length, 1, '縦並びの一覧の規則は 1 か所だけ');
  assert.deepStrictEqual(audit.match(/#[0-9a-fA-F]{3,8}\b/g) || [], [], '直値の色ではなくトークンを使う');
  // 6. 数字は main（agent-audit）が作る。画面で足し算しない
  assert.ok(!/reduce\(|\+ row\.|runs \+/.test(audit), '集計は agent-audit に任せる（画面で作らない）');
  // 7. 定型化したものの公開は、そのものが居る画面が持つ（設定に一覧を戻さない）
  assert.ok(!/id="audit-artifacts"/.test(html), '定型化したものの一覧を設定へ戻さない');
});

test('スキルの面は 1 つの一覧で、未公開を先頭に出し、公開と削除の選択操作は設定の下に置く', () => {
  const html = read('renderer/index.html');
  const css = read('renderer/styles.css');
  const skills = read('renderer/skills.js');
  const panel = html.match(/<section data-settings-panel="skills"[\s\S]*?<\/section>\s*<section data-settings-panel="storage"/)?.[0] || '';
  assert.ok(panel, '設定に「スキル」の面が無い');
  // 1. タブ名で分かることを本文で繰り返さない
  assert.ok(!/<h[1-4][\s>]/.test(panel), '設定の面に見出しを置かない（タブ名が名乗る）');
  assert.ok(!/<p[\s>]/.test(panel), '仕組みの説明は README に置く');
  // 2. 器は「保存データ」の面をそのまま借りる（設定の行 → 一覧 → 足元に操作）
  assert.match(panel, /class="setting-field(?: setting-field-wide)?"/);
  assert.match(panel, /class="environment-status"/);
  assert.match(skills, /el\('label', 'setting-check(?: [a-z-]+)*'\)/, '行は保存データと同じ .setting-check を借りる（並びの修飾だけ足してよい）');
  assert.ok(!/\.skill-row|\.skill-card|\.skill-panel|\.skills-list\b/.test(css), 'スキルの行の私物な複製を作らない');
  // 3. 設定は一覧の上。操作は足元で切り替え、行にボタンを並べない
  assert.ok(panel.indexOf('audit-share-repo') < panel.indexOf('skills-list'), '公開先の設定は一覧の上に置く');
  assert.ok(panel.indexOf('audit-push-main-row') < panel.indexOf('skills-remove-mode'));
  assert.ok(panel.indexOf('skills-publish') < panel.indexOf('skills-list'), '公開操作は設定の下に置く');
  assert.ok(!/el\('button'/.test(skills.slice(skills.indexOf('function render()'), skills.indexOf('function say('))),
    '行ごとにボタンを作らない（一覧がボタンの壁になる）');
  assert.ok(panel.indexOf('skills-remove-mode') < panel.indexOf('skills-list'));
  assert.ok(panel.indexOf('id="skills-remove"') < panel.indexOf('skills-list'), '削除の実行も設定の下に置く');
  assert.match(panel, /id="skills-remove" class="small danger" disabled/);
  // 4. 状態は印を借りる。未公開を先頭に並べる
  assert.match(skills, /el\('span', 'status warn', mark\)/, '状態の印を借りる');
  assert.match(skills, /versionComparison === 'local-newer'/, '新しいローカル版だけを未公開とする');
  assert.match(panel, /class="setting-field setting-field-wide"/);
  assert.match(panel, /id="audit-share-token" type="password"/);
  assert.ok(!panel.includes('stacked-field'), '他の設定と同じ横並びの項目');
  // 5. 必要になるまで入力欄を出さない（公開先が空なら main へ直接の行は隠す）
  assert.match(panel, /id="audit-push-main-row" hidden/);
  assert.match(skills, /\$\('audit-push-main-row'\)\.hidden = !\$\('audit-share-repo'\)\.value\.trim\(\)/);
  assert.deepStrictEqual(skills.match(/#[0-9a-fA-F]{3,8}\b/g) || [], [], '直値の色ではなくトークンを使う');
});

test('公開の札とカードは 1 か所で作り、タスクとワークフローが同じ形を借りる', () => {
  const publish = read('renderer/publish.js');
  const css = read('renderer/styles.css');
  // 1. カードは「一枚のまとまり」の既存の形（.execution-card + .execution-card-head）
  assert.match(publish, /class="execution-card"/);
  assert.match(publish, /class="execution-card-head"/);
  assert.match(publish, /<h3>公開<\/h3>/, '見出しは 1 つ、説明は 1 行');
  assert.ok(!/\.publish-card|\.publish-row|\.publish-badge/.test(css), '同じ見た目を別の名前で定義し直さない');
  // 2. 札は状態の印を借りる
  assert.match(publish, /<span class="status warn">未公開<\/span>/);
  // 3. 押せる操作が無いときはカードを出さない（説明だけの面を残さない）
  assert.match(publish, /if \(!actions && info\.status === 'published'/);
  // 4. 言葉を混ぜない。LAN は「共有」、リポジトリへ出すのは「公開」
  assert.ok(!/共有先|共有する/.test(publish), 'リポジトリへ出すことを「共有」と呼ばない');
  assert.deepStrictEqual(publish.match(/#[0-9a-fA-F]{3,8}\b/g) || [], [], '直値の色ではなくトークンを使う');
});
test('評価と課題は既存の部品で組む（設定の行・検索の足元・受信箱のカード）。人が点を付けるボタンは持たない', () => {
  const html = read('renderer/index.html');
  const css = read('renderer/styles.css');
  const renderer = read('renderer/renderer.js');
  const search = read('renderer/sessionSearch.js');
  // 1. 自動評価の設定は「遷移や振り分けの判定」と同じ .setting-field の 1 行で、モデルの欄を増やさない
  assert.match(html, /<label class="setting-field" id="evaluation-row">[\s\S]*?<select id="evaluation-mode">/);
  assert.ok(!html.includes('id="evaluation-model"'), '判定に使うモデルは上の行と共有する');
  // 2. まとめて評価は検索の足元に操作 1 つ。既定の判定経路を使い、AI の選択は置かない
  const footer = html.slice(html.indexOf('<footer id="search-batch"'), html.indexOf('</footer>', html.indexOf('<footer id="search-batch"')));
  assert.strictEqual((footer.match(/class="primary"/g) || []).length, 1, '主ボタンは足元に 1 つ');
  assert.ok(!/<select|<input|<details/.test(footer), '評価のエージェント・モデル選択は置かない');
  assert.ok(!/search-batch-all|すべて選ぶ/.test(search), '「すべて選ぶ」は置かない（検索の絞り込みで対象を決める）');
  // 3. 課題のカードはタスクの概要と同じ .execution-card。改善案の文は置かない
  assert.match(renderer, /function renderIssueCards[\s\S]*el\('section', 'execution-card'\)[\s\S]*el\('div', 'execution-card-head'\)/);
  assert.ok(!/suggested_action/.test(renderer), '課題に agent-audit の定型の改善案を添えない');
  assert.ok(!/<h2|<h3/.test(html.slice(html.indexOf('id="inbox-area"'), html.indexOf('</section>', html.indexOf('id="inbox-area"')))), '受信箱の本文は見出しを描かない（カードが持つ）');
  // 4. 人が応答に点を付ける口は無い（方針: 評価は自動か、まとめて）
  assert.ok(!/良い|悪い|thumbs/.test(renderer.slice(renderer.indexOf('function responseForkActions'), renderer.indexOf('function responseForkActions') + 4000)), '応答の下に評価ボタンを置かない');
  // 5. 足した面に直値の色を足さない
  const added = css.slice(css.indexOf('/* 課題（受信箱）'));
  assert.ok(!/#[0-9a-f]{3,6}\b/i.test(added), `直値の色: ${added}`);
});

test('ホームは会話画面の面（空状態と入力欄）と一覧の行を借り、判定は会話の送信経路に置く', () => {
  const html = read('renderer/index.html');
  const css = read('renderer/styles.css');
  const renderer = read('renderer/renderer.js');
  const navigation = read('renderer/navigation.js');
  const store = read('main/store.js');
  // 1. 主要メニューの先頭の 1 領域。一覧は同じ .list、＋ は出さない（受信箱と同じ入口だけ）
  const menu = html.match(/<nav id="areas"[\s\S]*?<\/nav>/)?.[0] || '';
  assert.match(menu, /id="area-home">[\s\S]*?<span>ホーム<\/span>/);
  assert.ok(menu.indexOf('id="area-home"') < menu.indexOf('id="area-inbox"'), 'ホームは主要メニューの先頭');
  assert.match(html, /<ul id="home-items" class="list grow" hidden><\/ul>/);
  assert.match(navigation, /home: \{ label: 'ホーム', listLabel: '最近の依頼', createLabel: '新しい会話', listId: 'home-items' \}/);
  assert.match(renderer, /\$\('session-new'\)\.hidden = \['share', 'inbox', 'home'\]\.includes\(state\.area\)/);
  // 2. 面は会話画面そのもの（#main の空状態と #composer）。ホーム専用の面・入力欄・見出し帯を作らない
  assert.ok(!html.includes('id="home-area"') && !html.includes('id="home-prompt"') && !html.includes('id="home-head"'), 'ホーム専用の面や入力欄を作らない');
  assert.match(renderer, /const workspace = state\.area !== 'conversation' && !home;/);
  assert.match(renderer, /if \(home\) \{\s*newDraft\(\);/, 'ホームは常に新しい会話（空状態）から');
  assert.strictEqual((html.match(/class="area-head"/g) || []).length, 5, 'ホームは見出し帯を増やさない（会話の見出し帯を使う）');
  // 3. リポジトリの選択は置き場を移すだけ（同じ #repository-context を付け替える。複製を作らない）
  assert.strictEqual((html.match(/id="repository-context"/g) || []).length, 1, 'リポジトリ選択は 1 つだけ');
  assert.match(renderer, /repositorySlot\.append\(\$\('repository-context'\)\)/, '同じコントロールを移す');
  for (const rule of css.match(/^#home-[^{\n]*\{[^}]*\}/gm) || []) {
    assert.doesNotMatch(rule, /background|box-shadow|border-radius|#[0-9a-fA-F]{3,8}\b/, `ホームの置き場に見た目を足さない（置き場所だけ）: ${rule}`);
  }
  // 4. 直近の一覧は会話一覧と同じ行（.row-item / .list-pick）。押すと既存の画面（会話・タスク・ワークフロー）へ行く
  assert.match(renderer, /function renderHomeItems\(\)[\s\S]*?const pick = el\('button', 'list-pick'\);[\s\S]*?pick\.onclick = \(\) => openHomeItem\(item\)/);
  const open = renderer.match(/async function openHomeItem\(item\) \{[\s\S]*?\n\}/)?.[0] || '';
  assert.match(open, /openSessionInRepo\(item\.repo, item\.id\)/, '会話は通知・受信箱と同じ経路で開く');
  assert.match(open, /await showArea\(area\);\s*await selectAreaItem\(area,/, 'タスク・ワークフローは領域の切替と一覧の選択だけ');
  // 5. 判定は会話の送信経路（runTurn の振り分け）そのまま。ホームは行き先を開くだけで、実行のボタンは押さない
  assert.ok(!/home:route|homeRoute|api\.route\(/.test(renderer), 'ホーム専用の判定を作らない');
  assert.match(renderer, /if \(state\.area === 'home'\) \{ await leaveHomeRouted\(state\.current\); return res; \}/);
  assert.match(renderer, /async function leaveHomeRouted\(session\) \{[\s\S]*?await api\.removeSession\(session\.id\);[\s\S]*?if \(routing\) await openRouted\(routing\);/);
  assert.match(renderer, /if \(state\.area === 'home'\) await showArea\('conversation'\);/);
  assert.ok(!/leaveHomeRouted[\s\S]{0,600}automation\.run\(|leaveHomeRouted[\s\S]{0,600}flowRun\(/.test(renderer), '流用先を開くだけで実行しない');
  // 6. 初回の既定画面はホーム。保存した領域はそのまま
  assert.match(store, /area: 'home', view: 'chat'/);
  assert.match(store, /\['tasks', 'workflows', 'share', 'inbox', 'home'\]\.includes\(next\.area\)/);
});
