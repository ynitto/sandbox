'use strict';

// 定常業務領域の「バッジと中身が食い違わない」を固定する。
//
// 直した症状: 左メニューに「定常業務 [6]」と出ているのに、開くと右ペインが空。
// 原因は 2 つで、どちらも「数える対象」と「映す対象」がずれていたこと。
//   1. バッジは端末全体の登録数（在庫）を数えていた。右ペインは選択中フォルダ 1 つ分。
//   2. 選択（selectedDir）はアプリ全体で 1 つなので、領域を切り替えても前の領域で選んだ
//      対象が残る。定常業務を開いてもプロジェクトのフォルダが選ばれたままだった。
//
// 護るもの:
//   1. バッジは**人が手を打つべきもの**だけを数える（止まっている作業。在庫は数えない）。
//   2. 領域を開いたら、その領域の一覧の中から対象を選び直す（手を打つべきもの優先、
//      戻ってきたときは前に見ていた対象）。
//   3. プロジェクト領域の一覧に定常業務専用フォルダを並べない（選ぶとタブが 1 つも
//      出せず行き止まりになる）。
//   4. 対象を合わせるのはタブの出し分けより先（順番が逆だとホームへ弾かれる）。

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const src = require('./helpers/renderer-src').read();
const html = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'index.html'), 'utf8');

// 関数を 1 本だけ切り出す（async 宣言も落とさない）。
function fn(name) {
  const at = src.indexOf(`function ${name}(`);
  assert.ok(at >= 0, `function ${name} が見つかりません`);
  const head = src.slice(Math.max(0, at - 6), at) === 'async ' ? at - 6 : at;
  let depth = 0;
  for (let i = src.indexOf('{', at); i < src.length; i++) {
    if (src[i] === '{') depth += 1;
    else if (src[i] === '}') {
      depth -= 1;
      if (depth === 0) return src.slice(head, i + 1);
    }
  }
  throw new Error(`function ${name} の閉じ括弧が見つかりません`);
}

function constLine(name) {
  const line = src.split('\n').find((l) => l.startsWith(`const ${name} =`));
  assert.ok(line, `const ${name} が見つかりません`);
  return line;
}

const body = [
  constLine('COWORK_ATTENTION_STATUS'),
  fn('coworkPathKey'),
  fn('coworkItemFolder'),
  fn('coworkItemsForFolder'),
  fn('coworkAttentionItems'),
  fn('isProjectAreaItem'),
  fn('routineFolders'),
  fn('areaItems'),
  fn('alignAreaSelection'),
  fn('areaBadge'),
].join('\n');

// eslint-disable-next-line no-new-func
const build = new Function(
  'state', 'selectProject', 'portalHomeModel', 'amigosAttentionCount', 'featureTabs',
  `${body}; return { areaItems, alignAreaSelection, areaBadge, coworkAttentionItems };`
);

const ROUTINE_DIR = '/ws/sandbox-test';
const PROJECT_DIR = '/ws/sandbox-project';

function makeState({ items = [], projects = null, selectedDir = PROJECT_DIR, areaSelection = {} } = {}) {
  return {
    area: 'routines',
    selectedDir,
    areaSelection,
    discovery: {
      projects: projects || [
        { name: 'sandbox-project', dir: PROJECT_DIR, kind: 'project', isProject: true, exists: true, needsCount: 0 },
        { name: 'sandbox-test', dir: ROUTINE_DIR, kind: 'routine', isProject: false, exists: true, needsCount: 0 },
      ],
    },
    cowork: { items },
  };
}

function work(name, status, repo = ROUTINE_DIR) {
  return { id: name, name, repo, state: { status, running: status === 'running' } };
}

function load(state) {
  const calls = [];
  const api = build(
    state,
    // 本物の selectProject と同じだけのことをする（選んだ対象を覚えて選択に反映する）。
    async (dir) => { calls.push(dir); state.selectedDir = dir; state.areaSelection[state.area] = dir; },
    () => ({ totals: { needs: 0 } }),
    () => 0,
    new Map()
  );
  return { ...api, calls };
}

async function main() {

// --- 1) バッジは在庫を数えない ------------------------------------------------

{
  // 登録しただけ・まだ動かしていない作業（unknown）は 6 件あってもバッジを出さない。
  const idle = load(makeState({ items: Array.from({ length: 6 }, (_, i) => work(`w${i}`, 'unknown')) }));
  assert.strictEqual(idle.areaBadge('routines'), null, '在庫の件数でバッジを光らせない');

  const stuck = load(makeState({
    items: [work('a', 'unknown'), work('b', 'failed'), work('c', 'idle'), work('d', 'blocked')],
  }));
  const badge = stuck.areaBadge('routines');
  assert.deepStrictEqual(
    { text: badge.text, tone: badge.tone },
    { text: '2', tone: 'warn' },
    '止まっている作業だけを warn で数える'
  );

  // 動いている最中は「止まっている」ではない（ログに error が残っていても実行中は待つ）。
  const running = load(makeState({ items: [{ id: 'r', name: 'r', repo: ROUTINE_DIR, state: { status: 'running', running: true } }] }));
  assert.strictEqual(running.areaBadge('routines'), null, '実行中は手を打つ対象にしない');
  console.log('ok - 定常業務のバッジは手を打つべきものだけを数える');
}

// --- 2) 領域を開いたら、その領域の対象を選び直す --------------------------------

{
  // 持ち込んだ選択（プロジェクト領域のフォルダ）はこの領域では中身が無いので引き継がない。
  const state = makeState({ items: [work('a', 'unknown')] });
  const { alignAreaSelection, calls } = load(state);
  await alignAreaSelection('routines');
  assert.deepStrictEqual(calls, [ROUTINE_DIR], '空のフォルダに着地させず、作業のあるフォルダを開く');
  assert.strictEqual(state.areaSelection.routines, ROUTINE_DIR);
  console.log('ok - 定常業務を開くと定常業務のフォルダが選ばれる');
}

{
  // 既にその領域の対象を選んでいるなら触らない（人の選択を上書きしない）。
  const state = makeState({ items: [work('a', 'unknown')], selectedDir: ROUTINE_DIR });
  const { alignAreaSelection, calls } = load(state);
  await alignAreaSelection('routines');
  assert.deepStrictEqual(calls, [], '領域の中の対象を選んでいるなら選び直さない');
  assert.strictEqual(state.areaSelection.routines, ROUTINE_DIR, '選択中の対象を覚える');
  console.log('ok - 領域の中の選択は横取りしない');
}

{
  // 持ち込んだ選択がその領域でも中身を持つなら、そのまま引き継ぐ（勝手に移さない）。
  const state = makeState({ items: [work('a', 'unknown', PROJECT_DIR)] });
  const { alignAreaSelection, calls } = load(state);
  await alignAreaSelection('routines');
  assert.deepStrictEqual(calls, [], '選択中のフォルダに作業があるなら移動しない');
  assert.strictEqual(state.areaSelection.routines, PROJECT_DIR);
  console.log('ok - 中身のある選択は領域をまたいでも引き継ぐ');
}

{
  // 初めて開く領域では、手を打つべきものが多い対象へ着地する（バッジの数字と着地点を揃える）。
  const other = '/ws/other-routines';
  const state = makeState({
    projects: [
      { name: 'sandbox-project', dir: PROJECT_DIR, kind: 'project', isProject: true, exists: true, needsCount: 0 },
      { name: 'sandbox-test', dir: ROUTINE_DIR, kind: 'routine', isProject: false, exists: true },
      { name: 'other', dir: other, kind: 'routine', isProject: false, exists: true },
    ],
    items: [work('a', 'unknown'), work('b', 'failed', other)],
  });
  const { alignAreaSelection, calls } = load(state);
  await alignAreaSelection('routines');
  assert.deepStrictEqual(calls, [other], '止まっている作業を持つフォルダへ着地する');
  console.log('ok - バッジが数えた対象がそのまま着地点になる');
}

{
  // 戻ってきたときは前に見ていた対象を出す（毎回先頭へ戻さない）。
  const other = '/ws/other-routines';
  const state = makeState({
    projects: [
      { name: 'sandbox-project', dir: PROJECT_DIR, kind: 'project', isProject: true, exists: true, needsCount: 0 },
      { name: 'sandbox-test', dir: ROUTINE_DIR, kind: 'routine', isProject: false, exists: true },
      { name: 'other', dir: other, kind: 'routine', isProject: false, exists: true },
    ],
    items: [work('a', 'unknown'), work('b', 'failed', other)],
    areaSelection: { routines: ROUTINE_DIR },
  });
  const { alignAreaSelection, calls } = load(state);
  await alignAreaSelection('routines');
  assert.deepStrictEqual(calls, [ROUTINE_DIR], '前に見ていた対象へ戻す');
  console.log('ok - 領域を往復しても前に見ていた対象へ戻る');
}

// --- 3) プロジェクト領域に定常業務専用フォルダを並べない ------------------------

{
  const { areaItems } = load(makeState({ items: [work('a', 'unknown')] }));
  assert.deepStrictEqual(
    areaItems('projects').map((x) => x.dir),
    [PROJECT_DIR],
    '定常業務専用フォルダはプロジェクト領域の一覧に出さない'
  );
  // 定常業務は両方（登録フォルダ ＋ プロジェクトとして登録したフォルダ）を対象にする。
  // 走査はもともと両方を回っているので、片方だけ並べると一覧に無いフォルダの作業が出てくる。
  assert.deepStrictEqual(
    areaItems('routines').map((x) => x.dir),
    [PROJECT_DIR, ROUTINE_DIR],
    '定常業務の対象は登録フォルダとプロジェクトのフォルダの両方'
  );
  assert.deepStrictEqual(areaItems('routines').map((x) => x.works), [0, 1], '作業の件数も持つ');
  assert.ok(
    src.includes('p.exists && isProjectAreaItem(p)'),
    'サイドバーの一覧も同じ条件で絞る（一覧と照合の食い違いを作らない）'
  );
  console.log('ok - 領域の一覧は領域ごとに分かれる');
}

// --- 4) 対象を合わせるのはタブの出し分けより先 ---------------------------------

{
  const switchAreaSrc = fn('switchArea');
  assert.ok(switchAreaSrc.includes('await alignAreaSelection('), '領域の切り替えで対象を合わせる');
  assert.ok(
    switchAreaSrc.indexOf('await alignAreaSelection(') < switchAreaSrc.indexOf('applyAreaTabs()'),
    'タブの出し分けは対象を合わせたあと（前の領域の対象で行き止まり判定をしない）'
  );
  console.log('ok - 対象を合わせてからタブを出し分ける');
}

// --- 5) 入力が必要な業務は HTML ダイアログを経由する ----------------------------

{
  assert.ok(html.includes('<dialog id="dlg-cowork-parameters"'), 'OS ダイアログではなく HTML dialog を置く');
  assert.ok(html.includes('id="cowork-parameter-fields"'), 'パラメータ入力欄の置き場を持つ');
  assert.ok(src.includes('function openCoworkParametersDialog('), '実行前に入力ダイアログを開く');
  assert.ok(src.includes('if (!keys.length) return run({});'), '入力不要なら従来どおり即時実行する');
  assert.ok(src.includes('type="text" autocomplete="off" required'), '初版は必須の文字列入力だけにする');
  assert.ok(src.includes('dlg.showModal()'), 'HTML dialog をモーダル表示する');
  assert.ok(src.includes("dlg.addEventListener('cancel', onCancel)"), 'Escape では実行せず閉じる');
  console.log('ok - 入力が必要な定常業務は HTML ダイアログを経由する');
}

console.log('\nroutine-area-ui: all tests passed');

}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
