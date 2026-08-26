'use strict';

// ポータルのナビゲーション構造を固定する。
//
// 護るもの:
//   1. 第一ナビは**左メニューの領域（ワークロード）**で、右ペインのタブはその領域の
//      内部ナビ。左で選んだものと右に出るものが一致する（以前はプロジェクト一覧の隣に
//      他ツールのタブが並び、対応が取れていなかった）。
//   2. ホームは領域のひとつで、起動時の着地点。agent-project の画面を既定にしない。
//   3. どのタブがどの領域かは HTML（data-area）が正。core は列挙と表示名だけを持つ。
//   4. 領域の絞り込み（area-off）と feature の出し分け（.hidden）は別のしるしで重ねる。
//   5. 横断サマリーは discover() が既に持つデータだけで組む（取得経路を増やさない）。
//   6. 設定は「端末全体に効くもの」と「道具ごとのもの」を置き場所で分ける。

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const RENDERER_DIR = path.join(__dirname, '..', 'src', 'renderer');
const html = fs.readFileSync(path.join(RENDERER_DIR, 'index.html'), 'utf8');
const renderer = require('./helpers/renderer-src').read();
const participation = fs.readFileSync(path.join(RENDERER_DIR, 'features', 'participation.js'), 'utf8');
const agentAudit = fs.readFileSync(path.join(RENDERER_DIR, 'features', 'agent-audit.js'), 'utf8');

// --- 1) 左メニューが第一ナビ ------------------------------------------------

{
  assert.ok(html.includes('<nav id="area-nav"'), '領域ナビがサイドバーにある');
  assert.ok(html.includes('<nav id="area-nav-footer"'), '全体設定は領域ナビの末尾に固定する');
  const sidebar = html.slice(html.indexOf('<aside id="sidebar">'), html.indexOf('<main id="main">'));
  assert.ok(sidebar.includes('id="area-nav"'), '領域ナビはサイドバーの中');
  assert.ok(
    sidebar.indexOf('id="area-nav"') < sidebar.indexOf('id="tree"'),
    '領域ナビは対象一覧より上（領域 → その中の対象、の順で読む）'
  );
  // 対象一覧は領域ごと。プロジェクト領域はプロジェクト、定常業務領域は作業フォルダ。
  assert.match(sidebar, /data-area-list="projects"/);
  assert.match(sidebar, /data-area-list="routines"/);
  assert.ok(sidebar.includes('id="routine-list"'), '定常業務の対象一覧がある');
  const missionsArea = renderer.indexOf("{ id: 'missions', label: 'ミッション'");
  const routinesArea = renderer.indexOf("{ id: 'routines', label: '定常業務'");
  assert.ok(missionsArea >= 0 && missionsArea < routinesArea,
    '左メニューではミッションを定常業務の上に置く');
  console.log('ok - 左メニューは領域ナビ ＋ その領域の対象一覧');
}

// --- 2) タブは領域に属し、ホームが起動時の着地点 ------------------------------

{
  assert.ok(
    /<button class="tab active" data-tab="home" data-area="home"/.test(html),
    'ホームは自分の領域を持ち、起動時アクティブ'
  );
  assert.ok(html.includes('<div id="tab-home" class="tabpane active"'), 'ホームのペインが起動時アクティブ');
  assert.ok(
    html.includes('<div id="tab-overview" class="tabpane" data-feature="agent-project">'),
    'agent-project の概要は起動時アクティブではない'
  );
  // すべてのタブが領域を宣言している（宣言の無いタブはどの領域でも出ない＝迷子になる）
  const tabs = [...html.matchAll(/<button class="tab[^"]*"([^>]*)>/g)].map((m) => m[1]);
  assert.ok(tabs.length >= 12, 'タブを取り違えていない');
  for (const attrs of tabs) {
    assert.ok(/data-area="/.test(attrs), `data-area の無いタブがある: ${attrs.trim()}`);
  }
  console.log('ok - すべてのタブが領域を宣言し、ホームが起動時の着地点');
}

// --- 3) 領域ごとのタブ構成（起動と実行トレースが各領域に揃う） -----------------

{
  const areaOf = (tab) => {
    const m = html.match(new RegExp(`data-tab="${tab}"[^>]*data-area="([^"]+)"`));
    return m && m[1];
  };
  for (const [tab, area] of [
    ['home', 'home'],
    ['overview', 'projects'],
    ['backlog', 'projects'],
    ['needs', 'projects'],
    ['flow', 'projects'],
    ['history', 'projects'],
    ['project-settings', 'projects'],
    ['cowork', 'routines'],
    ['routine-settings', 'routines'],
    ['amigos', 'missions'],
    ['participation', 'participation'],
    ['usage', 'usage'],
    ['orchestration', 'settings'],
  ]) {
    assert.strictEqual(areaOf(tab), area, `${tab} タブは ${area} 領域`);
  }
  // 定常業務は dashboard 自身が実行側なので、動かす・記録を追う・設定するが 1 領域に揃う。
  for (const id of ['tab-cowork', 'tab-routine-settings', 'tab-usage']) {
    assert.ok(html.includes(`id="${id}"`), `${id} のペインがある`);
  }
  assert.ok(!html.includes('id="tab-routine-runs"'));
  console.log('ok - 定常業務は履歴を画面内に持ち、設定は共通のタブ構成を使う');
}

// --- 4) 領域の絞り込みは feature の出し分けと別のしるし -----------------------

{
  assert.ok(renderer.includes('function applyAreaTabs('), '領域による絞り込みがある');
  assert.ok(renderer.includes("classList.toggle('area-off'"), '絞り込みは area-off で行う');
  const fn = renderer.slice(
    renderer.indexOf('function areaTabButtons('),
    renderer.indexOf('function applyAreaTabs(')
  );
  assert.ok(
    fn.includes('!tab.hidden') && fn.includes("!tab.classList.contains('hidden')"),
    '領域の絞り込みは feature 側の .hidden を上書きせず、両方を満たすタブだけ出す'
  );
  const css = fs.readFileSync(path.join(RENDERER_DIR, 'styles.css'), 'utf8');
  assert.ok(css.includes('.tab.area-off'), 'area-off の見た目が定義されている');
  console.log('ok - 領域の絞り込みと feature の出し分けが打ち消し合わない');
}

// --- 5) 領域と対象の選択が食い違わない ----------------------------------------

{
  assert.ok(renderer.includes('function switchArea('), '領域の切り替えがある');
  // タブを名指しで開いたときは領域も合わせる（ホームの対応待ち → プロジェクトの要対応）
  const switchTabSrc = renderer.slice(
    renderer.indexOf('function switchTab('),
    renderer.indexOf('\nfunction gotoRun(')
  );
  assert.ok(
    switchTabSrc.includes('target.dataset.area !== state.area'),
    'タブを名指しで開いたら左メニューの選択も合わせる'
  );
  // 対象を選んだだけでは領域を動かさない（一覧はその領域の中にある）
  const selectSrc = renderer.slice(
    renderer.indexOf('async function selectProject('),
    renderer.indexOf('function resetFlowDrilldown(')
  );
  assert.ok(!selectSrc.includes('switchArea('), '対象の選択で領域を横取りしない');
  assert.ok(!selectSrc.includes('switchTab('), '対象の選択でタブを横取りしない');
  // 起動時はホームへ着地する。重い各領域と前回プロジェクトの詳細は初期描画を塞がない。
  const bootstrap = fs.readFileSync(path.join(RENDERER_DIR, 'bootstrap.js'), 'utf8');
  assert.ok(bootstrap.includes("switchArea('home')"), '起動時の着地点はホーム');
  const beforeHome = bootstrap.slice(0, bootstrap.indexOf("await switchArea('home')"));
  assert.ok(beforeHome.includes('await refreshDiscovery()'), 'ホームの横断表示に必要な発見だけは先に待つ');
  assert.ok(!beforeHome.includes('await refreshCowork()'), '全フォルダ走査はホームの初期描画を塞がない');
  assert.ok(!beforeHome.includes('await selectProject('), '前回プロジェクトの詳細をホームのために先読みしない');
  assert.ok(bootstrap.includes('requestAnimationFrame(') && bootstrap.includes('warmStartupData()'),
    '初期描画をブラウザへ渡した後に残りの領域を温める');
  assert.match(bootstrap, /async function warmStartupData\(\)[\s\S]*Promise\.all\(\[/,
    '独立した制御面は直列待ちせず並行して取得する');
  // 左メニューの出し分けに必要な取得はバックグラウンドでも必ず実行し、完了時に描き直す。
  for (const refresh of ['refreshDiscovery()', 'refreshCowork()', 'refreshAmigos()', 'refreshOrchestration()']) {
    assert.ok(bootstrap.includes(refresh), `起動時に ${refresh} を呼ぶ（領域の出し分けの材料）`);
  }
  console.log('ok - 領域と対象の選択が互いを横取りしない');
}

// --- 6) portalHomeModel: discover() の結果だけで組む純関数 ---------------------

function grab(name) {
  const at = renderer.indexOf(`function ${name}(`);
  assert.ok(at >= 0, `renderer に function ${name} が見つかりません`);
  let i = at + `function ${name}`.length;
  while (i < renderer.length && renderer[i] !== '{') i++;
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
const portalHomeModel = new Function(`${grab('portalHomeModel')}; return portalHomeModel;`)();

{
  const projects = [
    { dir: 'A', name: 'alpha', needsCount: 1, running: true, kind: 'project' },
    { dir: 'B', name: 'beta', charterName: 'ベータ', needsCount: 3, kind: 'project', paused: true },
    { dir: 'C', name: 'gamma', needsCount: 2, kind: 'routine' },
    { dir: 'D', name: 'delta', needsCount: 9, exists: false, kind: 'project' },
    { dir: 'E', name: 'eps', needsCount: 0, kind: 'project', quarantined: true },
  ];
  const model = portalHomeModel(projects);
  assert.strictEqual(model.totals.projects, 3, 'routine と届かないものを除いた件数');
  assert.strictEqual(model.totals.routines, 1, '定常業務フォルダは別勘定');
  assert.strictEqual(model.totals.needs, 6, '要対応の総数は届く分だけ（exists:false は除外）');
  assert.strictEqual(model.totals.running, 1);
  assert.strictEqual(model.totals.paused, 1);
  assert.strictEqual(model.totals.quarantined, 1);
  assert.deepStrictEqual(
    model.queue.map((q) => q.dir),
    ['B', 'C', 'A'],
    '対応待ちキューは件数の多い順・届かないものは並べない'
  );
  assert.strictEqual(model.queue[0].name, 'ベータ', '表示名は charter 名を優先');
  const empty = portalHomeModel([]);
  assert.strictEqual(empty.totals.needs, 0);
  assert.deepStrictEqual(portalHomeModel(null).queue, [], 'null 入力でも壊れない');
  console.log('ok - 横断サマリーは discover() の結果だけで組む');
}

{
  const home = fs.readFileSync(path.join(RENDERER_DIR, 'sections', 'home.js'), 'utf8');
  assert.ok(home.includes('data-portal-needs'), '対応待ちキューからその要対応へ飛べる');
  assert.ok(home.includes("switchTab('needs')"), '遷移先は既存の要対応タブ（新しい判断面を作らない）');
  assert.ok(!home.includes('api.readProject'), 'ホームは discover() 以外の読み込みを増やさない');
  assert.ok(home.includes('data-portal-area='), 'カードは領域へ移動する（左メニューと同じ行き先）');
  console.log('ok - ホームは読み取りと画面遷移だけ');
}

// --- 7) 1 画面の失敗が他の画面を道連れにしない -------------------------------
//
// 実障害: 定常業務の設定を描く段で、まだ描かれていない全体設定の入力欄へ代入しようとして
// 例外が出た。描画が 1 本の連鎖だったため、そこから後ろ（利用状況・ミッション・
// プロジェクト設定・全体設定・参加）が**全部空**になり、左メニューから開いても何も出ない
// 状態が固定した。原因（欄の有無）と、被害の広がり方（連鎖）の両方を固定する。

{
  const src = fs.readFileSync(path.join(RENDERER_DIR, 'renderer.js'), 'utf8');
  const body = src.slice(src.indexOf('function renderAllTabs('), src.indexOf('function activeTab('));
  const calls = [...body.matchAll(/^\s{2}(render[A-Za-z]+)\(/gm)].map((m) => m[1]);
  // renderAllTabs が直に呼んでよいのは renderPane と、ナビ側の少数の再描画だけ。
  const allowed = new Set(['renderPane', 'renderAreaNav', 'renderAreaLists', 'renderAreaHeader']);
  for (const name of calls) {
    assert.ok(allowed.has(name), `renderAllTabs は ${name} を renderPane 経由で呼ぶ（失敗を1画面に閉じ込める）`);
  }
  assert.ok(body.includes('for (const [name] of featureTabs) renderPane('),
    'フィーチャータブの描画も 1 つずつ隔離する');
  const pane = src.slice(src.indexOf('function renderPane('), src.indexOf('function renderAllTabs('));
  assert.ok(pane.includes('catch') && pane.includes('uiLog'), '失敗は握りつぶさずコンソールへ残す');
  console.log('ok - 1 画面の描画失敗がほかの画面を空にしない');
}

{
  // populateSettingsFields は全体設定と定常業務の設定の両方から呼ばれる。相手の画面が
  // まだ描かれていなければその欄は DOM に無いので、「あるものにだけ入れる」でなければ落ちる。
  const fn = grab('populateSettingsFields');
  const present = new Map();
  // 定常業務の設定だけが描かれている状況（全体設定の cfg-* はまだ無い）
  for (const id of ['cfg-cowork-loop-command', 'cfg-cowork-sm-command']) {
    present.set(id, { value: '', checked: false });
  }
  const doc = { getElementById: (id) => present.get(id) || null };
  const state = {
    config: {
      projects: { refreshSec: 5 }, notifications: {}, engine: {}, agent: {},
      gitlab: {}, reviewViewer: {}, cowork: { loopCommand: 'agent-loop' }, delegation: {},
    },
  };
  // eslint-disable-next-line no-new-func
  const run = new Function('document', 'state', `
    const $ = (id) => document.getElementById(id);
    ${fn}
    return populateSettingsFields;
  `)(doc, state);
  run(); // 例外が出ないこと自体が検証（出れば renderAllTabs ごと落ちる）
  assert.strictEqual(present.get('cfg-cowork-loop-command').value, 'agent-loop',
    'その画面にある欄には値が入る');
  assert.ok(!fn.includes("$('cfg-refresh').value"), '欄の存在を前提にした素の代入を残さない');
  console.log('ok - 設定の欄は「あるものにだけ入れる」（画面が分かれても落ちない）');
}

{
  const switchAreaSrc = renderer.slice(
    renderer.indexOf('function switchArea('),
    renderer.indexOf('function initTabs(')
  );
  assert.ok(switchAreaSrc.includes('!visible.length'),
    '出せるタブが無い領域を、押しても何も起きない行き止まりにしない');
  assert.ok(switchAreaSrc.includes("switchArea('home')"), '行き止まりならホームへ着地させる');
  // ミッション領域と実行タブは入口なので常設し、一覧だけをデータ有無で出し分ける。
  const amigos = fs.readFileSync(path.join(RENDERER_DIR, 'sections', 'amigos.js'), 'utf8');
  assert.ok(renderer.includes("if (id === 'missions') return true;"),
    'ミッション領域は未設定でも入口として表示する');
  assert.ok(amigos.includes('const showRun = true;')
    && amigos.includes('const showMissions = amigosNodeHasWork();'),
  '実行は常設し、既存ミッション一覧だけをデータ有無で出し分ける');
  assert.ok(!amigos.includes('amigosForProject'),
    'ミッションは端末の話なので、選択中プロジェクトで絞らない');
  console.log('ok - 左メニューに出ている領域は必ずどこかへ着地する');
}

// --- 8) カードと領域は登録簿で差し込む ----------------------------------------

{
  assert.ok(renderer.includes('function registerPortalCard('), 'コアにカード登録簿がある');
  assert.ok(renderer.includes('globalThis.registerPortalCard'), '登録口はグローバルに公開');
  assert.ok(renderer.includes("registerFeatureTab('home'"), 'ホーム自身も既存の登録簿を使う');
  assert.ok(participation.includes('registerPortalCard'), '参加は自分のカードを自分で登録する');
  assert.ok(agentAudit.includes('registerPortalCard'), '利用状況は自分のカードを自分で登録する');
  // 領域を出すかどうかの判定を core が持たない面は、登録簿の available() に聞く
  assert.ok(renderer.includes('function areaAvailable('), '領域の出し分けがある');
  assert.ok(participation.includes('available:'), '参加は自分で「使えるか」を答える');
  console.log('ok - カードと領域の出し分けは登録簿経由（コアが中身を知らない）');
}
