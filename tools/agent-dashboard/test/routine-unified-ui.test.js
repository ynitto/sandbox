'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const src = require('./helpers/renderer-src').read();
const html = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'styles.css'), 'utf8');

// 定常業務は対象を選び直すタブ構造ではなく、1つの作業面で操作を完結させる。
const routineTabs = [...html.matchAll(/<button[^>]+data-tab="([^"]+)"[^>]+data-area="routines"/g)]
  .map((match) => match[1]);
assert.deepStrictEqual(routineTabs, ['cowork', 'routine-settings'], '定常業務領域は定常業務と設定の2タブを持つ');
assert.ok(!html.includes('id="tab-routine-runs"'), '実行の記録専用ペインを残さない');
assert.ok(html.includes('id="tab-routine-settings"'), '設定は他領域と同じタブ構成にする');
assert.ok(src.includes('function renderCoworkWorkspace('), '一覧・操作・履歴を統合する作業面を描画する');

console.log('ok - 定常業務は単一の統合作業画面を使う');

// 履歴は選択中業務の右ペインへ表示し、別タブへ移動しない。
const openHistoryAt = src.indexOf('async function openCoworkHistory(');
assert.ok(openHistoryAt >= 0, '履歴を開く入口がある');
const openHistoryBody = src.slice(openHistoryAt, src.indexOf('\n}', openHistoryAt) + 2);
assert.ok(!openHistoryBody.includes('switchTab('), '履歴確認でタブを移動しない');
assert.ok(src.includes('coworkHistoryBodyHtml(history)'), '統合詳細に履歴本体を埋め込む');
assert.ok(src.includes('function coworkHistoryForSelected('), '選択中業務以外の履歴を表示しない');

console.log('ok - 履歴は選択中業務の右ペインへ表示する');

// 個別設定と全体設定は、選択を失わず同じ画面から開く。
assert.ok(src.includes('class="cowork-selected-actions"'), '選択中業務の固定操作群がある');
assert.ok(src.includes('>設定変更</button>'), '選択業務の上部から個別設定を開ける');
assert.ok(src.includes('id="btn-routine-adhoc"'), 'AIへの作業依頼を統合画面の補助操作として残す');
assert.ok(html.includes('id="dlg-routine-adhoc"'), 'AIへの作業依頼は独立ダイアログで開く');

console.log('ok - 個別設定と全体設定を統合作業画面から開く');

// 数十件でも画面全体を送らず、左一覧だけを高密度にスクロールする。
assert.match(css, /\.cowork-split-view\s*\{[^}]*grid-template-columns:\s*minmax\(240px,\s*320px\)\s+minmax\(0,\s*1fr\)/s,
  '通常幅では左一覧・右詳細の2列にする');
assert.match(css, /\.cowork-routine-selector\s*\{[^}]*overflow-y:\s*auto/s,
  '業務一覧だけを縦スクロールする');
assert.match(css, /\.cowork-selected-hero\s*\{[^}]*position:\s*sticky/s,
  '選択中業務の操作を右ペイン上部へ固定する');
assert.match(css, /@media\s*\(max-width:\s*800px\)[\s\S]*?\.cowork-split-view\s*\{[^}]*grid-template-columns:\s*1fr/s,
  '狭い画面では1列へ切り替える');
assert.ok(src.includes("ev.key !== '/'"), '/ キーで業務検索へ移動する');

console.log('ok - 数十件向けの高密度マスター・詳細レイアウトを使う');

assert.ok(!src.includes('<span class="summary-kicker">選択</span>'), '選択欄に説明ラベルを重ねない');
assert.ok(src.includes('placeholder="名前で検索"'), '入力欄は選択指示ではなく検索条件を示す');
assert.ok(!src.includes('data-cowork-visible-count'), '一覧内に重複する件数を表示しない');
assert.ok(!src.includes('<code>${esc(l.name)}</code>'), 'ログカードにファイル名を表示しない');
assert.ok(!src.includes('<span>ログ</span>'), 'ログ選択セルに重複ラベルを表示しない');
assert.ok(src.includes('<time datetime='), 'ログは実行日時を選択する');
assert.ok(src.includes('<span class="muted">${esc(fmtLogSize(l.size))}</span>'), '日時とサイズを同じ選択セルに表示する');
assert.ok(!src.includes('class="cowork-latest-result"'), '最終結果を重複表示しない');
assert.ok(!src.includes('<div><span>対象</span><strong title="${esc(item.repo'), '対象カードを重複表示しない');
assert.ok(src.includes('class="cowork-status-result"'), '最終結果は二段表示専用の領域を使う');
assert.ok(src.includes('<span>最終実行日時</span>'), '最終結果ではなく最終実行日時だけを表示する');
assert.ok(src.includes('function coworkNextRunAt(') && src.includes('次回実行予定'), '次回実行日時を推定して表示する');
assert.ok(src.includes('execution.agent_cli') && src.includes('execution.model'), '実行予定のエージェントとモデルを表示する');
assert.ok(src.includes('<button id="btn-cowork-save">変更を保存</button>'), '下書きの永続化操作だと分かる名前にする');

console.log('ok - ラベルと履歴情報を簡潔に保つ');
