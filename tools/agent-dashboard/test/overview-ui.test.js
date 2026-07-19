'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const renderer = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'renderer.js'), 'utf8');
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
const coworkVisibleEntries = new Function(
  'coworkPathKey',
  `${grab('coworkVisibleEntries')}; return coworkVisibleEntries;`
)(coworkPathKey);
// eslint-disable-next-line no-new-func
const coworkHasProjectConfig = new Function(
  'coworkPathKey',
  `${grab('coworkHasProjectConfig')}; return coworkHasProjectConfig;`
)(coworkPathKey);

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
}

// eslint-disable-next-line no-new-func
const amigosForProject = new Function(
  'coworkPathKey',
  `${grab('amigosForProject')}; return amigosForProject;`
)(coworkPathKey);
{
  const scoped = amigosForProject({
    homes: [
      { dir: '/work/a', configFile: '/work/a/agent-amigos.yaml' },
      { dir: '/work/b', configFile: '/work/b/agent-amigos.yaml' },
      { dir: '/work/b', configFile: null },
    ],
    missions: [
      { id: 'a1', home: '/work/a' },
      { id: 'b1', home: '/work/b' },
      { id: 'global', home: null },
    ],
    errors: ['other project error'],
  }, '/work/b');
  assert.deepStrictEqual(scoped.homes.map((h) => h.dir), ['/work/b']);
  assert.deepStrictEqual(scoped.missions.map((m) => m.id), ['b1']);
  assert.deepStrictEqual(scoped.errors, [], '他プロジェクトの読込エラーも表示しない');
  assert.strictEqual(amigosForProject({ homes: [], missions: [{ id: 'x' }] }, '/work/b').missions.length, 0);
  assert.strictEqual(
    amigosForProject({ homes: [{ dir: '/work/b', configFile: null }], missions: [] }, '/work/b').homes.length,
    0,
    'フォルダ内に設定ファイルがない明示ホームは表示しない'
  );
}

assert.match(html, /id="dlg-cowork-history"/, '定常業務の実行履歴ダイアログがある');
assert.ok(renderer.includes('data-cowork-history'), '定常業務に履歴ボタンがある');
assert.ok(!renderer.includes('すべてのプロジェクトを表示'), '他プロジェクトを表示する切替を置かない');

assert.ok(!html.includes('id="btn-mode"'), '表示モード切替を残さない');
assert.match(html, /data-tab="overview"[^>]*>概要/);
assert.match(html, /data-feature="agent-project"/);
assert.match(html, /data-tab="backlog"[^>]*>タスク/);
assert.match(html, /data-tab="flow"[^>]*>実行/);
assert.match(html, /id="btn-project-settings"/);
assert.match(html, /class="nav-group"[^>]+aria-labelledby="projects-group-title"/);
assert.match(html, /id="projects-group-title"[^>]*>プロジェクト</);
assert.match(html, /id="project-list"/);
const commonHeader = html.slice(html.indexOf('class="sidebar-header"'), html.indexOf('</div>\n      </div>', html.indexOf('class="sidebar-header"')));
assert.ok(commonHeader.includes('id="btn-doctor"'), 'AI相談は共通ヘッダーに置く');
assert.ok(commonHeader.includes('id="btn-refresh"') && commonHeader.includes('id="btn-settings"'));
assert.ok(!commonHeader.includes('id="btn-new-project"'), '新規作成は共通ヘッダーに置かない');
const projectGroup = html.slice(html.indexOf('id="projects-group-title"'), html.indexOf('id="project-list"'));
assert.ok(projectGroup.includes('id="btn-new-project"'), '新規作成はプロジェクトグループに置く');
assert.ok(!html.includes('class="doctor-tools"'), 'AI相談専用の中間グループを残さない');
assert.ok(!html.includes('id="btn-git-pull"'), '最新取得の単独ボタンを残さない');
assert.ok(!html.includes('id="btn-git-heal"'), '同期修復を Doctor の固定ボタンとして残さない');
assert.match(html, /id="btn-refresh"[^>]+aria-label="表示を更新"/);
assert.match(html, /id="project-meta"[^>]+aria-live="polite"/);
assert.match(renderer, /id="btn-sync-now"/);
assert.match(renderer, /共有先と同期/);
assert.match(renderer, /同期を修復/);
assert.match(renderer, /共有先確認:/);
assert.match(renderer, /remoteCheckedAt/);
assert.match(renderer, /refreshAll\(\{ sync: false \}\)/);
assert.match(renderer, /reloadProject\(\{ refreshRemoteHealth: sync \}\)/);
assert.match(renderer, /api\.gitHealth\(project\.dir, refreshRemoteHealth\)/);

for (const label of ['現在の状態', 'あなたの対応', '進捗', '成果', '対応する', 'タスクを見る', '実行を見る', '成果を見る']) {
  assert.ok(renderer.includes(label), `概要に「${label}」が必要です`);
}
assert.match(css, /button:focus-visible/);
assert.match(css, /@media \(max-width: 680px\)/);
assert.match(css, /\.sidebar-actions button,[\s\S]*?min-width: 44px; height: 44px;/);

// --- グローバル指示（agent-instructions）パネル ---
{
  const escStub = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const orchBadgeStub = (kind, label) => `<span class="badge ${kind}">${escStub(label)}</span>`;
  const wlLabelStub = (w) => w;
  const stateStub = { orchSkillsInventory: [{ name: 'karpathy-guidelines', dir: '/x/.github/skills' }] };
  // eslint-disable-next-line no-new-func
  const panel = new Function('esc', 'orchBadge', 'amigosWorkloadLabel', 'state',
    `${grab('orchInstructionsPanelHtml')}; return orchInstructionsPanelHtml;`)(
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
  assert.ok(out.includes('グローバル指示（全ノード共通）'), 'パネル見出しが必要');
  assert.ok(out.includes('id="orch-instr-text"'), '指示文 textarea が必要');
  assert.ok(out.includes('回答は日本語。'), '既存の指示文が反映される');
  assert.ok(out.includes('id="btn-orch-instr-save"'), '保存ボタンが必要');
  assert.ok(out.includes('karpathy-guidelines'), '棚卸しのスキルが行に出る');
  assert.ok(out.includes('checked'), '選択済みスキル / 有効トグルが checked');
  assert.ok(out.includes('未反映 r2/3'), 'status の未反映バッジが出る');
  assert.ok(out.includes('agent-instructions rev:3'), 'プレビューが描画結果を出す');
}
assert.match(renderer, /orchInstructionsPanelHtml\(ov\)/);
assert.match(renderer, /api\.orchestrationInstructionsSave/);

console.log('overview-ui: all tests passed');
