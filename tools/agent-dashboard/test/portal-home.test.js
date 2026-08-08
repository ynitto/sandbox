'use strict';

// ホーム（ポータル）— agent-* ファミリー全体の横断ビュー — の構造を固定する。
//
// 護るもの:
//   1. ホームはどの制御面にも属さない base のタブで、タブ列の先頭・起動時の既定画面である
//      （agent-project の「概要」を既定にしない＝特定の制御面を一等席にしない）。
//   2. カードは registerPortalCard の登録簿で差し込む（コアがカードの一覧を持たない）。
//      feature モジュール所有の面（参加・利用状況）は各 features/*.js が自分で登録する。
//   3. 横断サマリー（portalHomeModel）は discover() が既に持つデータだけで組む
//      （ホームのために新しい取得経路・状態の書き手を作らない）。

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const RENDERER_DIR = path.join(__dirname, '..', 'src', 'renderer');
const html = fs.readFileSync(path.join(RENDERER_DIR, 'index.html'), 'utf8');
const renderer = require('./helpers/renderer-src').read();
const participation = fs.readFileSync(path.join(RENDERER_DIR, 'features', 'participation.js'), 'utf8');
const agentAudit = fs.readFileSync(path.join(RENDERER_DIR, 'features', 'agent-audit.js'), 'utf8');

// --- 1) タブ構造: ホームが先頭・base 所属・起動時アクティブ -------------------

{
  const homeTab = html.indexOf('data-tab="home"');
  const overviewTab = html.indexOf('data-tab="overview"');
  assert.ok(homeTab >= 0, 'ホームタブがある');
  assert.ok(overviewTab >= 0, '概要タブがある');
  assert.ok(homeTab < overviewTab, 'ホームはタブ列の先頭（概要より前）');
  assert.ok(
    /<button class="tab active" data-tab="home" data-feature="base"/.test(html),
    'ホームは base 所属で起動時アクティブ'
  );
  assert.ok(
    html.includes('<div id="tab-home" class="tabpane active" data-feature="base">'),
    'ホームのペインが起動時アクティブ'
  );
  // agent-project の概要は起動時アクティブではない（ホームが既定画面）。
  assert.ok(html.includes('<div id="tab-overview" class="tabpane" data-feature="agent-project">'));
  assert.ok(html.includes('<script src="sections/home.js"></script>'), 'home.js を読み込む');
  console.log('ok - ホームはタブ列の先頭・base 所属・起動時の既定画面');
}

// --- 2) 登録簿: registerPortalCard がコアにあり、feature が自分で登録する -------

{
  assert.ok(renderer.includes('function registerPortalCard('), 'コアにカード登録簿がある');
  assert.ok(renderer.includes('globalThis.registerPortalCard'), '登録口はグローバルに公開');
  assert.ok(renderer.includes("registerFeatureTab('home'"), 'ホーム自身もフィーチャータブ登録簿を使う');
  assert.ok(renderer.includes('registerCorePortalCards'), 'コア側カード（sections 所有面）の登録がある');
  assert.ok(participation.includes('registerPortalCard'), '参加は自分のカードを自分で登録する');
  assert.ok(agentAudit.includes('registerPortalCard'), '利用状況は自分のカードを自分で登録する');
  // 利用状況カードは入口だけ（数字は全体設定「利用状況」の 1 か所で出す）。
  assert.ok(agentAudit.includes('data-portal-settings="usage"'));
  console.log('ok - カードは登録簿経由（コアがカード一覧を持たない）');
}

// --- 3) portalHomeModel: discover() の結果だけで組む純関数 ---------------------

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
  console.log('ok - portalHomeModel は discover() の結果だけで横断サマリーを組む');
}

{
  const model = portalHomeModel([]);
  assert.strictEqual(model.totals.needs, 0);
  assert.deepStrictEqual(model.queue, []);
  const modelNull = portalHomeModel(null);
  assert.deepStrictEqual(modelNull.queue, [], 'null 入力でも壊れない');
  console.log('ok - 空の発見結果でも壊れない');
}

// --- 4) 横断キューは公式の画面遷移だけ（状態の書き手にならない） ---------------

{
  const home = fs.readFileSync(path.join(RENDERER_DIR, 'sections', 'home.js'), 'utf8');
  assert.ok(home.includes('data-portal-needs'), '対応待ちキューからプロジェクトの要対応へ飛べる');
  assert.ok(home.includes("switchTab('needs')"), '遷移先は既存の要対応タブ（新しい判断面を作らない）');
  assert.ok(!home.includes('api.readProject'), 'ホームは discover() 以外のプロジェクト読み込みを増やさない');
  console.log('ok - ホームは読み取りと画面遷移だけ（新しい取得経路を作らない）');
}

// --- 5) 起動時にホームへ着地する（前回選択の復元はタブを奪わない） --------------

{
  assert.ok(
    renderer.includes("selectProject(el.dataset.dir, { reveal: true })"),
    'サイドバーの選択は reveal（ホームからそのプロジェクトの画面へ移る）'
  );
  const bootstrap = fs.readFileSync(path.join(RENDERER_DIR, 'bootstrap.js'), 'utf8');
  assert.ok(
    !/selectProject\(target\.dir,\s*\{\s*reveal/.test(bootstrap),
    '起動時の前回選択の復元は reveal しない（最初の画面はホームのまま）'
  );
  console.log('ok - 起動時はホームに着地し、人の選択操作だけが画面を移す');
}
