'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const renderer = require('./helpers/renderer-src').read();
const html = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'styles.css'), 'utf8');

function grab(name) {
  const at = renderer.indexOf(`function ${name}(`);
  assert.ok(at >= 0, `renderer.js に function ${name} が見つかりません`);
  let i = renderer.indexOf('{', at);
  let depth = 0;
  for (; i < renderer.length; i++) {
    if (renderer[i] === '{') depth++;
    else if (renderer[i] === '}') {
      depth--;
      if (depth === 0) return renderer.slice(at, i + 1);
    }
  }
  throw new Error(`function ${name} の閉じ括弧が見つかりません`);
}

// eslint-disable-next-line no-new-func
const overviewSummary = new Function(`${grab('overviewSummary')}; return overviewSummary;`)();
// eslint-disable-next-line no-new-func
const appDoctorSummary = new Function(`${grab('appDoctorSummary')}; return appDoctorSummary;`)();
// eslint-disable-next-line no-new-func
const workspaceFeatureModel = new Function(`${grab('workspaceFeatureModel')}; return workspaceFeatureModel;`)();

const project = {
  liveness: { running: true, paused: false },
  needs: [{ id: 'N1', decided: false }],
  byStatus: { doing: 2, offloaded: 1, ready: 3, inbox: 1, proposed: 1 },
  claims: ['T1'],
  archive: [{ id: 'D1' }, { id: 'D2' }],
  backlog: [
    { id: 'T1', status: 'doing' },
    { id: 'T2', status: 'offloaded' },
    { id: 'T3', status: 'ready' },
    { id: 'T4', status: 'inbox' },
    { id: 'T5', status: 'proposed' },
  ],
};

const summary = overviewSummary(project, [
  { status: 'running' },
  { status: 'done' },
  { status: 'failed' },
]);
assert.strictEqual(summary.headline, '1 件の確認を待っています');
assert.deepStrictEqual(summary.undecided.map((need) => need.id), ['N1'],
  '既存の未判断 needs を概要の対応対象として保持する');
assert.strictEqual(summary.working, 3);
assert.strictEqual(summary.waiting, 5);
assert.strictEqual(summary.done, 2);
assert.strictEqual(summary.total, 7);
assert.strictEqual(summary.progress, 29);
assert.strictEqual(summary.activeRuns, 1);

const appSummary = appDoctorSummary({
  projects: [
    { running: true, needsCount: 2 },
    { running: false, needsCount: 1 },
  ],
});
assert.deepStrictEqual(appSummary, { projects: 2, running: 1, needs: 3 });

assert.deepStrictEqual(
  workspaceFeatureModel(
    { projects: [{ dir: '/loop-only', isProject: false }] },
    '/loop-only',
    2
  ),
  { agentProject: false, cowork: true, defaultTab: 'cowork' },
  'kiro-loopだけのworkspaceでは定常業務だけを表示する'
);
assert.deepStrictEqual(
  workspaceFeatureModel(
    { projects: [{ dir: '/agent-project', isProject: true }] },
    '/agent-project',
    0
  ),
  { agentProject: true, cowork: false, defaultTab: 'overview' }
);

// --- Cowork の選択プロジェクト絞り込み ---
// eslint-disable-next-line no-new-func
const coworkPathKey = new Function(`${grab('coworkPathKey')}; return coworkPathKey;`)();
// eslint-disable-next-line no-new-func
const coworkItemFolder = new Function(`${grab('coworkItemFolder')}; return coworkItemFolder;`)();
// eslint-disable-next-line no-new-func
const coworkVisibleEntries = new Function(
  'coworkPathKey', 'coworkItemFolder',
  `${grab('coworkVisibleEntries')}; return coworkVisibleEntries;`
)(coworkPathKey, coworkItemFolder);
// eslint-disable-next-line no-new-func
const coworkHasProjectConfig = new Function(
  'coworkPathKey', 'coworkItemFolder',
  `${grab('coworkHasProjectConfig')}; return coworkHasProjectConfig;`
)(coworkPathKey, coworkItemFolder);

assert.strictEqual(coworkPathKey('\\\\wsl.localhost\\Ubuntu\\home\\me\\proj\\'), '/home/me/proj');
assert.strictEqual(coworkPathKey('/home/me/proj'), '/home/me/proj');
assert.strictEqual(coworkPathKey('/mnt/c/Users/Me/proj'), 'c:/users/me/proj');
assert.strictEqual(coworkPathKey('C:\\Users\\Me\\proj'), 'c:/users/me/proj');

{
  const draft = [
    { id: 'a', repo: '/home/me/proj-a' },
    { id: 'b', repo: '\\\\wsl.localhost\\Ubuntu\\home\\me\\proj-b' },
    { id: 'c', repo: '/home/me/proj-b' },
  ];
  // 選択プロジェクトの作業だけ（UNC と POSIX は同一視・index は draft の位置を保つ）
  const vis = coworkVisibleEntries(draft, '/home/me/proj-b');
  assert.deepStrictEqual(vis.map((e) => e.item.id), ['b', 'c'], '選択プロジェクトの作業だけを表示する');
  assert.deepStrictEqual(vis.map((e) => e.index), [1, 2], 'index は draft の位置（編集/削除用）');
  assert.strictEqual(coworkVisibleEntries(draft, null).length, 0, 'プロジェクト未選択では一覧を出さない');
  assert.strictEqual(
    coworkHasProjectConfig({ discoveredRepos: ['/home/me/proj-b'] }, '/home/me/proj-b'),
    true,
    '選択プロジェクト自身の設定ファイルだけを認識する'
  );
  assert.strictEqual(coworkHasProjectConfig({ discoveredRepos: ['/home/me/proj-a'] }, '/home/me/proj-b'), false);
  assert.strictEqual(coworkHasProjectConfig({ items: [{ repo: '/home/me/proj-b' }] }, '/home/me/proj-b'), true,
    '手動追加した作業だけのプロジェクトも定常業務を表示する');
  // 所属は登録したフォルダ（root）。設定ファイルがサブフォルダにある作業も、登録した
  // フォルダの一覧に出す（repo で括ると、どのフォルダを選んでも出てこない）。
  const nested = [{ id: 'n', root: '/home/me/proj-b', repo: '/home/me/proj-b/packages/api' }];
  assert.deepStrictEqual(coworkVisibleEntries(nested, '/home/me/proj-b').map((e) => e.item.id), ['n'],
    'サブフォルダに設定がある作業は登録フォルダの一覧に出る');
  assert.strictEqual(coworkHasProjectConfig({ items: nested }, '/home/me/proj-b'), true);
}

// ミッションは端末（ノード）の話で、選択中プロジェクトでは絞らない。ミッションが自分の
// 領域を持った以上、プロジェクトで絞ると「左メニューには出ているのに中身が常に空」になる。
// eslint-disable-next-line no-new-func
const amigosNodeView = new Function(
  'coworkPathKey',
  `${grab('amigosNodeView')}; return amigosNodeView;`
)(coworkPathKey);
{
  const view = amigosNodeView({
    homes: [
      { dir: '/work/a', configFile: '/work/a/agent-amigos.yaml' },
      { dir: '/work/b', configFile: '/work/b/agent-amigos.yaml' },
      { dir: '/work/c', configFile: null },
    ],
    missions: [
      { id: 'a1', home: '/work/a' },
      { id: 'b1', home: '/work/b' },
      { id: 'orphan', home: '/work/c' },
      { id: 'global', home: null },
    ],
    errors: ['読込エラー'],
  });
  assert.deepStrictEqual(view.homes.map((h) => h.dir), ['/work/a', '/work/b'],
    'この端末のホームはプロジェクト選択に関係なく全部出す');
  assert.deepStrictEqual(view.missions.map((m) => m.id), ['a1', 'b1'],
    '実体のあるホームのミッションだけを出す（home 不明・設定ファイル無しは除く）');
  assert.deepStrictEqual(view.errors, ['読込エラー'], 'この端末の読込エラーは伏せない');
  assert.strictEqual(amigosNodeView({ homes: [], missions: [{ id: 'x' }] }).missions.length, 0,
    'ホームが無ければミッションも出さない');
  assert.strictEqual(
    amigosNodeView({ homes: [{ dir: '/work/b', configFile: null }], missions: [] }).homes.length,
    0,
    'フォルダ内に設定ファイルがない明示ホームは表示しない'
  );
  assert.deepStrictEqual(amigosNodeView(null).missions, [], '未取得でも壊れない');
}

// 実行の記録は定常業務領域の 1 タブ。動かす画面と結果を追う画面が同じ領域に並ぶので、
// 同じ内容のダイアログ（旧 dlg-cowork-history）は置かない。
assert.match(html, /data-tab="routine-runs"[^>]*>実行の記録/, '定常業務に実行の記録タブがある');
assert.ok(!html.includes('id="dlg-cowork-history"'), '実行履歴のダイアログは残さない');
assert.ok(renderer.includes('data-cowork-history'), '定常業務に履歴ボタンがある');
assert.ok(!renderer.includes('すべてのプロジェクトを表示'), '他プロジェクトを表示する切替を置かない');

assert.ok(!html.includes('id="btn-mode"'), '表示モード切替を残さない');
assert.match(html, /data-tab="overview"[^>]*>概要/);
assert.match(html, /data-feature="agent-project"/);
assert.match(html, /data-tab="backlog"[^>]*>タスク/);
assert.match(html, /data-tab="flow"[^>]*>実行/);
assert.ok(!html.includes('id="btn-project-settings"'), '浮いたプロジェクト設定ボタンを残さない');
assert.match(html, /data-tab="project-settings"[^>]*>設定/);
assert.match(html, /class="nav-group"[^>]+aria-labelledby="projects-group-title"/);
assert.match(html, /id="projects-group-title"[^>]*>プロジェクト</);
assert.match(html, /id="project-list"/);
const commonHeader = html.slice(html.indexOf('class="sidebar-header"'), html.indexOf('</div>\n      </div>', html.indexOf('class="sidebar-header"')));
assert.ok(commonHeader.includes('id="btn-doctor"'), 'AI相談は共通ヘッダーに置く');
assert.ok(commonHeader.includes('id="btn-refresh"'));
// 設定の入口は領域ナビ末尾の「全体設定」1 つ。同じ場所へ行く歯車を共通ヘッダーに並べない。
assert.ok(!commonHeader.includes('id="btn-settings"'), '設定の歯車ボタンは置かない');
assert.ok(!commonHeader.includes('id="btn-new-project"'), '新規作成は共通ヘッダーに置かない');
const projectGroup = html.slice(html.indexOf('id="projects-group-title"'), html.indexOf('id="project-list"'));
assert.ok(projectGroup.includes('id="btn-new-project"'), '新規作成はプロジェクトグループに置く');
assert.ok(!html.includes('class="doctor-tools"'), 'AI相談専用の中間グループを残さない');
assert.ok(!html.includes('id="btn-git-pull"'), '最新取得の単独ボタンを残さない');
assert.ok(!html.includes('id="btn-git-heal"'), '同期修復を Doctor の固定ボタンとして残さない');
assert.match(html, /id="btn-refresh"[^>]+aria-label="表示を更新"/);
assert.match(html, /id="project-meta"[^>]+aria-live="polite"/);
// 同期の表示は実行エンジンの状況ファイル一本（W2-5）。操作は「今すぐ同期」の投函だけで、
// この画面から git を叩く経路（pull / commitPush / heal）はソースごと消えている。
assert.match(renderer, /id="btn-sync-now"/);
assert.match(renderer, /今すぐ同期/);
assert.match(renderer, /最終確認:/);
assert.match(renderer, /api\.engineStatus\(\)/);
assert.match(renderer, /api\.requestHeal\(dir\)/);
for (const gone of ['api.gitHealth', 'api.gitHeal', 'api.gitPull', 'api.gitCommitPush',
  'gitPushAfterWrite', 'gitPushBusOp', 'api.startProject', 'api.removeProject']) {
  assert.ok(!renderer.includes(gone), `${gone} は削除済みのはず`);
}

for (const label of ['現在の状態', 'あなたの対応', '進捗', '成果', '対応する', 'タスクを見る', '実行を見る', '成果を見る']) {
  assert.ok(renderer.includes(label), `概要に「${label}」が必要です`);
}
assert.ok(renderer.includes('const staleNodes = (p.nodes || []).filter((node) => !node.running)'),
  '応答のないPCを「あなたの対応」に反映します');
assert.ok(renderer.includes('実行を確認する'), 'PCが応答しないときの次の操作を直接示します');
assert.match(css, /button:focus-visible/);
assert.match(css, /@media \(max-width: 680px\)/);
assert.match(css, /\.sidebar-actions button,[\s\S]*?min-width: 44px; height: 44px;/);

// --- グローバル指示（agent-instructions）パネル ---
{
  const escStub = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const orchBadgeStub = (kind, label) => `<span class="badge ${kind}">${escStub(label)}</span>`;
  const wlLabelStub = (w) => w;
  const stateStub = { orchSkillsInventory: [
    {
      name: 'karpathy-guidelines', description: 'LLM向けの実装原則', category: 'coding',
      version: '1.2.0', tags: ['quality', 'code'], sources: ['kiro', 'agents'], dir: '/secret/skills',
    },
    { name: 'systematic-debugging', description: '原因を段階的に調べる', sources: ['claude'] },
  ] };
  // eslint-disable-next-line no-new-func
  const panel = new Function('esc', 'orchBadge', 'amigosWorkloadLabel', 'state',
    `${grab('orchSkillRowHtml')}; ${grab('orchInstructionsPanelHtml')}; return orchInstructionsPanelHtml;`)(
    escStub, orchBadgeStub, wlLabelStub, stateStub);
  const ov = {
    instructions: {
      enabled: true, revision: 3, text: '回答は日本語。',
      skills: [{ name: 'karpathy-guidelines', note: '常時適用' }], tools: { allow: ['fs_read'], deny_note: 'push は確認' },
      max_chars: 2000,
    },
    instructionsPreview: '<!-- agent-instructions rev:3 -->\n## 共通指示（agent-dashboard 管理・全ノード共通）\n回答は日本語。',
    status: [{ tool: 'agent-flow', workload: 'flow', instructions_revision_applied: 2 }],
  };
  const out = panel(ov);
  assert.ok(out.includes('すべてのエージェントへの共通指示'), '利用者向けのパネル見出しが必要');
  assert.ok(out.includes('id="orch-instr-text"'), '指示文 textarea が必要');
  assert.ok(out.includes('回答は日本語。'), '既存の指示文が反映される');
  assert.ok(out.includes('id="btn-orch-instr-save"'), '保存ボタンが必要');
  assert.strictEqual((out.match(/class="orch-skill-row"/g) || []).length, 1,
    '選択済みスキルだけを編集行として表示する');
  assert.ok(out.includes('id="orch-skill-add"'), '名前を入力して追加する欄がある');
  assert.ok(out.includes('list="orch-skill-options"'), '入力欄が候補リストにつながっている');
  assert.ok(out.includes('id="orch-skill-candidate-description"'), '追加前の候補説明欄がある');
  assert.ok(out.includes('<option value="systematic-debugging"'), '未選択スキルが入力候補に出る');
  assert.ok(out.includes('karpathy-guidelines'), '選択済みスキルが行に出る');
  assert.ok(out.includes('class="orch-skill-details"'), '説明と属性は開閉できる詳細領域に入る');
  assert.ok(out.includes('<summary>説明と属性を表示</summary>'), '詳細を表示する操作が明確である');
  assert.ok(out.includes('LLM向けの実装原則'), '選択後は説明が表示される');
  assert.ok(out.includes('分類: coding'), '選択後は分類が表示される');
  assert.ok(out.includes('タグ: quality, code'), '選択後はタグが表示される');
  assert.ok(out.includes('利用元: Kiro, 共通'), '重複したスキルの利用元がまとめて表示される');
  assert.ok(!out.includes('/secret/skills'), '内部の保存パスは表示しない');
  assert.ok(!out.includes('class="orch-skill-on"'), '大量のチェックボックス一覧を表示しない');
  assert.ok(out.includes('data-ui-key="orch-instr-preview"'), 'プレビューの開閉状態を復元できるキーがある');
  // 「実行サービスへの反映状況」表は外した。何を伝える枠なのかが画面から読めず、同じ話題は
  // 実行制御の「設定の反映」列が持つ（同じ話題の表を 2 か所に置かない）。
  assert.ok(!out.includes('未反映（2/3）'), '反映状況の表は残さない');
  assert.ok(!out.includes('instructions_revision_applied'), '反映状況の根拠も読まない');
  assert.ok(out.includes('agent-instructions rev:3'), 'プレビューが描画結果を出す');
}
{
  // eslint-disable-next-line no-new-func
  const shorten = new Function(`${grab('shortSkillDescription')}; return shortSkillDescription;`)();
  assert.strictEqual(shorten('あ'.repeat(101)), `${'あ'.repeat(100)}…`, '候補説明は100文字で打ち切る');
  assert.strictEqual(shorten('短い説明'), '短い説明', '短い候補説明はそのまま表示する');
}
assert.match(renderer, /orchInstructionsPanelHtml\(overview\)/);
assert.match(renderer, /api\.orchestrationInstructionsSave/);
{
  const render = grab('renderOrchestration');
  assert.match(render, /captureUiState\(\)/, '全体設定の再描画前に開閉・スクロール状態を保存する');
  assert.match(render, /restoreUiState\(/, '全体設定の再描画後に開閉・スクロール状態を復元する');
}
assert.match(renderer, /details\[key\] = d\.open/,
  '開いた状態だけでなく、利用者が閉じた状態も保存する');
assert.match(renderer, /d\.open = ui\.details\[key\]/, '保存した開閉状態をそのまま復元する');
assert.match(renderer, /data-ui-key="orch-agents-/, '担当設定の開閉状態に安定キーがある');
assert.match(renderer, /data-ui-key="orch-dropin-new"/, 'エージェント追加欄の開閉状態に安定キーがある');
assert.match(renderer, /btn-orch-skill-add/);
assert.match(renderer, /orch-skill-remove/);
assert.match(renderer, /orchInstructionsDirty/, '共通指示の未保存入力を自動更新から保護する');

// --- 実行サービスは PID ごとでなくワークロードごとに表示する ---
{
  const escStub = (s) => String(s == null ? '' : s);
  const badgeStub = (kind, label) => `<span class="${kind}">${label}</span>`;
  const labelStub = (w) => ({ flow: 'フロー' }[w] || w);
  const lifecycleStub = (v) => ({ run: '稼働', pause: '一時停止', stop: '停止' }[v] || v);
  // eslint-disable-next-line no-new-func
  const panel = new Function('esc', 'orchBadge', 'amigosWorkloadLabel', 'orchLifecycleLabel',
    `${grab('orchStatusPanelHtml')}; return orchStatusPanelHtml;`)(
    escStub, badgeStub, labelStub, lifecycleStub);
  const out = panel({
    control: { revision: 2, workloads: { flow: { lifecycle: 'stop' } } },
    budget: { knownWorkloads: ['flow'] },
    status: [
      { tool: 'agent-flow', workload: 'flow', pid: 101, lifecycle: 'stop', fresh: false, revision_applied: 2 },
      { tool: 'agent-flow', workload: 'flow', pid: 102, lifecycle: 'stop', fresh: false, revision_applied: 2 },
      { tool: 'agent-flow', workload: 'flow', pid: 103, lifecycle: 'stop', fresh: true, revision_applied: 2 },
    ],
  });
  assert.strictEqual((out.match(/<tr>/g) || []).length, 2, '見出しとフロー1行だけを表示する');
  assert.strictEqual((out.match(/data-orch-wl="flow"/g) || []).length, 2, '操作は許可と全体停止の2つだけ');
  assert.ok(out.includes('稼働中 1件'));
  assert.ok(out.includes('終了済み記録 2件は非表示'));
  assert.ok(out.includes('端末全体で停止'));
  assert.ok(!out.includes('一時停止</button>'), '停止と同じ動作の一時停止ボタンを出さない');
}
assert.match(renderer, /個別のrunを止める操作ではありません/);

// --- プロジェクト共通チェック: CLI名を解釈せず設定と意味だけを表示する ---
{
  const escStub = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  // eslint-disable-next-line no-new-func
  const checkHtml = new Function('esc', `${grab('projectCheckHtml')}; return projectCheckHtml;`)(escStub);

  const configured = checkHtml({ projectCheck: {
    configFile: '/ws/.agents/agent-project.yaml', configured: true, command: 'make smoke' } });
  assert.ok(configured.includes('プロジェクト共通チェック'));
  assert.ok(configured.includes('make smoke'));
  assert.ok(configured.includes('失敗した場合は done の確定を止めます'));
  assert.ok(!configured.includes('intake_cmd'));
  assert.ok(!configured.includes('codd-gate'));

  const missing = checkHtml({ projectCheck: {
    configFile: '/ws/.agents/agent-project.yaml', configured: false, command: null } });
  assert.ok(missing.includes("regression_cmd: './tools/check'"));
  assert.ok(missing.includes('設定ファイルを開く'));
}

// --- nodesSummaryHtml（案6・複数 PC ノード一覧） ---
{
  // eslint-disable-next-line no-new-func
  const nodesSummaryHtml = new Function(
    'esc', 'humanizeAge',
    `${grab('nodesSummaryHtml')}; return nodesSummaryHtml;`
  )((s) => String(s == null ? '' : s), (ms) => `${Math.round(ms / 1000)}秒前`);
  assert.strictEqual(nodesSummaryHtml([]), '', 'ノードが無ければ何も出さない（従来の見た目）');
  assert.strictEqual(nodesSummaryHtml(undefined), '', 'undefined でも安全');
  const html = nodesSummaryHtml([
    { node: 'pc-a', host: 'h1', running: true, ageSec: 0 },
    { node: 'pc-b', host: 'h2', running: false, ageSec: 9999 },
  ]);
  assert.ok(html.includes('pc-a') && html.includes('pc-b'), '両ノードを出す');
  assert.ok(html.includes('稼働中'), '稼働中ノードを示す');
  // status/<node>.json は心拍ではなく「作業が進んだときの記録」。古いことを「応答なし」と
  // 言い切らない（待機中の PC を故障のように見せない）。
  assert.ok(!html.includes('応答なし'), '記録が古いだけのノードを応答なしと断定しない');
  assert.ok(html.includes('しばらく更新がありません'), '古い記録はそのまま伝える');
  assert.ok(html.includes('node-stale'), '更新の止まったノードは stale クラス');
  // この PC は常駐体の心拍で判定する（記録が何日古くても、心拍があれば稼働中）。
  const selfHtml = nodesSummaryHtml([{ node: 'mac', host: 'Mac', running: true, self: true, ageSec: 523721 }]);
  assert.ok(selfHtml.includes('この PC・稼働中'), '自ノードは心拍で稼働中と出す');
  assert.ok(!selfHtml.includes('しばらく更新がありません'), '自ノードに記録の古さを持ち込まない');
}

console.log('overview-ui: all tests passed');
