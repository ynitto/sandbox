'use strict';

// 知識タブ（記憶3層の可視化・quiet運転の承認キュー）のテスト。Electron は起動せず、
// コマンド組み立て・JSON契約の読み取り・画面HTMLを検証する（K3）。
// 計画: docs/plans/2026-08-15-agent-tools-cross-agent-knowledge-operation-plan.md §3.4

const test = require('node:test');
const assert = require('node:assert/strict');

const knowledge = require('../src/features/agent-knowledge/main/knowledge');
const ui = require('../src/renderer/features/agent-knowledge');

const okResult = (stdout) => ({ ok: true, status: 0, stdout, stderr: '', error: '' });

// --- main/knowledge.js -------------------------------------------------------

test('draftsSettings は既定を空文字にし、cfg から拾う', () => {
  assert.deepEqual(knowledge.draftsSettings({}), { command: '', distro: '', draftsDir: '' });
  assert.deepEqual(
    knowledge.draftsSettings({ agentKnowledge: { draftsCommand: ' python3 x.py ', draftsDir: '/d' } }),
    { command: 'python3 x.py', distro: '', draftsDir: '/d' });
});

test('buildScript は --drafts-dir をサブコマンドの前に置き、値を引用する', () => {
  const settings = { command: 'python3 x.py', draftsDir: "~/dir with'quote" };
  const script = knowledge.buildScript(settings, ['list', '--json']);
  assert.equal(script,
    "python3 x.py '--drafts-dir' '~/dir with'\"'\"'quote' 'list' '--json'");
});

test('parseJsonArray はログインシェルの前置きが混ざっても JSON 配列を取り出す', () => {
  assert.deepEqual(knowledge.parseJsonArray('hi\n[{"file":"a.md"}]\n'), [{ file: 'a.md' }]);
  assert.throws(() => knowledge.parseJsonArray('no json'), /JSON 配列がありません/);
});

test('listDrafts はコマンド未設定なら configured:false を返し、シェルを呼ばない', () => {
  let called = false;
  const result = knowledge.listDrafts({}, () => { called = true; return okResult('[]'); });
  assert.deepEqual(result, { configured: false, drafts: [] });
  assert.equal(called, false);
});

test('listDrafts は list --json を呼び、承認待ちの一覧を返す', () => {
  const cfg = { agentKnowledge: { draftsCommand: 'python3 moltbook_drafts.py' } };
  const scripts = [];
  const result = knowledge.listDrafts(cfg, (script) => {
    scripts.push(script);
    return okResult('[{"file":"12-alice.md","iid":"12","gate":{"allowed":true}}]');
  });
  assert.match(scripts[0], /'list' '--json'/);
  assert.equal(result.configured, true);
  assert.equal(result.drafts[0].file, '12-alice.md');
});

test('listDrafts は失敗時に stderr を理由として投げる', () => {
  const cfg = { agentKnowledge: { draftsCommand: 'python3 moltbook_drafts.py' } };
  assert.throws(
    () => knowledge.listDrafts(cfg, () => (
      { ok: false, status: 2, stdout: '', stderr: '下書きディレクトリがありません', error: '' })),
    /下書きディレクトリがありません/);
});

test('approveDraft はファイル名を渡して approve を呼ぶ', () => {
  const cfg = { agentKnowledge: { draftsCommand: 'python3 moltbook_drafts.py' } };
  const scripts = [];
  const result = knowledge.approveDraft(cfg, '12-alice.md', (script) => {
    scripts.push(script);
    return okResult('承認しました');
  });
  assert.match(scripts[0], /'approve' '--file' '12-alice\.md'/);
  assert.deepEqual(result, { ok: true });
});

test('approveDraft は未設定なら案内つきで投げる（シェルを呼ばない）', () => {
  assert.throws(() => knowledge.approveDraft({}, '12-alice.md', () => okResult('')),
    /moltbook-use が設定されていません/);
});

test('discardDraft は理由つきで discard を呼ぶ', () => {
  const cfg = { agentKnowledge: { draftsCommand: 'python3 moltbook_drafts.py' } };
  const scripts = [];
  knowledge.discardDraft(cfg, '12-alice.md', '重複', (script) => {
    scripts.push(script);
    return okResult('却下しました');
  });
  assert.match(scripts[0], /'discard' '--file' '12-alice\.md' '--reason' '重複'/);
});

test('discardDraft は理由なしでも動く', () => {
  const cfg = { agentKnowledge: { draftsCommand: 'python3 moltbook_drafts.py' } };
  const scripts = [];
  knowledge.discardDraft(cfg, '12-alice.md', '', (script) => {
    scripts.push(script);
    return okResult('');
  });
  assert.doesNotMatch(scripts[0], /--reason/);
});

// --- renderer/features/agent-knowledge.js -----------------------------------

test('ltmSectionHtml は未設定を「未収集」として明示する', () => {
  const html = ui.ltmSectionHtml({ configured: false, status: '未収集（memory_stores.ltm_dirs が未設定）' });
  assert.match(html, /未収集/);
});

test('ltmSectionHtml は publish待ち・忘却リスク・退役候補・類似クラスタを表示する', () => {
  const html = ui.ltmSectionHtml({
    configured: true,
    stores: [{
      path: '~/.claude/memory/home', total: 12, active: 10, archived: 2,
      publish_waiting: 3, forgetting_risk: 1, dormant: 2, dormant_oldest_days: 45,
      similar_clusters: 1, similar_clustered: 2, index_drift: 0, notes: [],
    }],
  });
  assert.match(html, /publish 待ち/);
  assert.match(html, /3 件/);
  assert.match(html, /忘却リスク/);
  assert.match(html, /退役候補/);
  assert.match(html, /45 日/);
  assert.match(html, /類似クラスタ/);
});

test('wikiSectionHtml はヒット率が無いとき「—」を出す', () => {
  const html = ui.wikiSectionHtml({
    configured: true,
    stores: [{
      path: '~/wiki', atoms: 3, topics: 1, new_last_7d: 1, updated_last_7d: 1,
      index_drift: 0, lint_violations: 0, queries_hit_rate: null, notes: [],
    }],
  });
  assert.match(html, /—/);
});

test('personaSectionHtml は件数と滞留日数だけを出す（本文・タイトルは持たない。C1）', () => {
  const html = ui.personaSectionHtml({
    configured: true,
    stores: [{ path: '~/.claude/persona', pending_updates: 2, pending_oldest_days: 5.5, managed_files: 3 }],
  });
  assert.match(html, /未反映の観察ログ/);
  assert.match(html, /2 件/);
  assert.match(html, /5\.5 日/);
  assert.doesNotMatch(html, /profile\.md|preferences\.md/);
});

test('moltbookSectionHtml は測れないものを名指しで出す', () => {
  const html = ui.moltbookSectionHtml({
    configured: true,
    stores: [{
      path: '~/.moltbook', outbox_pending: 4, outbox_oldest_days: 3, published_local: 1,
      inbox_pending: 0, uncollected: ['未回答メンション', 'goods'],
    }],
  });
  assert.match(html, /outbox 滞留/);
  assert.match(html, /4 件/);
  assert.match(html, /未回答メンション/);
  assert.match(html, /goods/);
});

test('tasksTableHtml は取得前（空）は案内を出す', () => {
  assert.match(ui.tasksTableHtml(), /整理タスクはありません/);
});

test('draftRowHtml は gate=ALLOW のとき承認ボタンを有効にする', () => {
  const html = ui.draftRowHtml({
    file: '12-alice.md', iid: '12', author: 'alice', created: '2026-08-16',
    body: '根拠つき回答', gate: { allowed: true, summary: 'ALLOW' },
  });
  assert.match(html, /ALLOW/);
  assert.doesNotMatch(html, /class="primary-inline knowledge-draft-approve" disabled/);
});

test('draftRowHtml は gate=BLOCK のとき承認ボタンを無効にする（却下は可能）', () => {
  const html = ui.draftRowHtml({
    file: 'bad.md', iid: '12', author: 'alice', created: '2026-08-16',
    body: '危険な本文', gate: { allowed: false, summary: 'BLOCK / 理由: シークレット' },
  });
  assert.match(html, /BLOCK/);
  assert.match(html, /knowledge-draft-approve" disabled/);
  assert.doesNotMatch(html, /knowledge-draft-discard" disabled/);
});

test('draftsQueueHtml は moltbook-use 未設定を案内する（取得前の既定状態）', () => {
  assert.match(ui.draftsQueueHtml(), /設定されていません/);
});
