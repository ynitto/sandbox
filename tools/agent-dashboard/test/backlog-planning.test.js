'use strict';

// S6（バックログの生成・レビュー）のビュアー層テスト。追加依存なしで
// `node test/backlog-planning.test.js` で走る。
//   - 受入基準（acceptance）は**複数行フィールド**＝配列で送り、全行を置換する
//   - 観点メモ（notes/）は書いても計画は動かない。分解は commands ドロップ 1 本
//   - メモの書き込みは状態ルート配下のファイルだけ（git には触らない）

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

// package.json の既存バックログテスト入口から、純粋なブロック分割テストも実行する。
require('./note-tasking.test');

const actions = require('../src/main/actions');
const { pickBacklogDocument, MAX_BACKLOG_DOCUMENT_BYTES } = require('../src/features/agent-project/main/ipc');
const renderer = require('./helpers/renderer-src').read();
const indexHtml = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'index.html'), 'utf8');
const bootstrap = fs.readFileSync(path.join(__dirname, '..', 'src', 'renderer', 'bootstrap.js'), 'utf8');

// renderer の関数 1 本を本文ごと切り出す（detail-tabs-ui.test.js と同じ流儀）
function grab(name) {
  const at = renderer.indexOf(`function ${name}(`);
  assert.ok(at >= 0, `renderer に function ${name} が見つかりません`);
  let i = renderer.indexOf('{', at);
  let depth = 0;
  for (; i < renderer.length; i += 1) {
    if (renderer[i] === '{') depth += 1;
    else if (renderer[i] === '}') {
      depth -= 1;
      if (depth === 0) return renderer.slice(at, i + 1);
    }
  }
  throw new Error(`function ${name} の終端が見つかりません`);
}

let passed = 0;
async function test(name, fn) {
  await fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function mkProject() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-s6-'));
  const dir = path.join(root, 'projects', 'demo');
  fs.mkdirSync(path.join(dir, 'backlog'), { recursive: true });
  fs.writeFileSync(path.join(dir, 'charter.md'), '# Charter: demo\n## goal\nx\n', 'utf8');
  return { root, dir };
}

function dropped(dir) {
  const cdir = path.join(dir, 'commands');
  const files = fs.readdirSync(cdir).filter((f) => f.endsWith('.json'));
  assert.strictEqual(files.length, 1, 'commands に 1 件だけドロップされる');
  return { file: files[0], rec: JSON.parse(fs.readFileSync(path.join(cdir, files[0]), 'utf8')) };
}

(async () => {
  // --- 受入基準（複数行フィールド）の送信契約 -------------------------------

  await test('revise は acceptance を配列で送る（単値と同じ経路だと 1 行に潰れる）', async () => {
    const { root, dir } = mkProject();
    try {
      await actions.runAction({}, {
        dir, action: 'revise', id: 'T1', reason: 'レビュー',
        fields: { acceptance: ['基準A', '基準B, カンマ入り'] },
      });
      const { rec } = dropped(dir);
      assert.deepStrictEqual(rec.acceptance, ['基準A', '基準B, カンマ入り']);
      assert.notStrictEqual(typeof rec.acceptance, 'string', '文字列へ畳まない');
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('acceptance に文字列 1 本を渡しても配列で送る', async () => {
    const { root, dir } = mkProject();
    try {
      await actions.runAction({}, {
        dir, action: 'revise', id: 'T1', reason: 'r', fields: { acceptance: 'ひとつ' },
      });
      assert.deepStrictEqual(dropped(dir).rec.acceptance, ['ひとつ']);
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('verify_agent を revise で送れる（検証条件だけ変えて回し直す口）', async () => {
    const { root, dir } = mkProject();
    try {
      await actions.runAction({}, {
        dir, action: 'revise', id: 'T1', reason: 'r',
        fields: { verify_agent: 'agent_cli=codex timeout_sec=1800' },
      });
      assert.strictEqual(dropped(dir).rec.verify_agent, 'agent_cli=codex timeout_sec=1800');
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('acceptance 未指定なら送らない（＝触らない）', async () => {
    const { root, dir } = mkProject();
    try {
      await actions.runAction({}, {
        dir, action: 'revise', id: 'T1', reason: 'r', fields: { title: '新しい題' },
      });
      const { rec } = dropped(dir);
      assert.ok(!('acceptance' in rec), '未指定は載せない');
      assert.strictEqual(rec.title, '新しい題');
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('revise は risks を配列のまま送る', async () => {
    const { root, dir } = mkProject();
    try {
      await actions.runAction({}, {
        dir, action: 'revise', id: 'T1', reason: 'リスク更新',
        fields: { risks: ['誤操作を防ぐ', '旧形式も維持する'] },
      });
      assert.deepStrictEqual(dropped(dir).rec.risks, ['誤操作を防ぐ', '旧形式も維持する']);
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('タスク追加は計画規模 size を失わず保存する', async () => {
    const { root, dir } = mkProject();
    try {
      const res = actions.enqueueToInbox(dir, { title: '計画レビュー', size: 'M' });
      assert.strictEqual(JSON.parse(fs.readFileSync(res.file, 'utf8')).size, 'M');
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('タスク追加は不正な計画規模を拒否する', async () => {
    const { root, dir } = mkProject();
    try {
      assert.throws(
        () => actions.enqueueToInbox(dir, { title: '計画レビュー', size: 'XL' }),
        /規模感は S \/ M \/ L/
      );
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  // --- 観点メモ（notes/） ---------------------------------------------------

  await test('writeNote は notes/ にファイルを作る（名前は自動で安全化）', async () => {
    const { root, dir } = mkProject();
    try {
      const res = actions.writeNote(dir, { name: '', body: '気になっていること' });
      assert.ok(fs.existsSync(res.file), 'ファイルができる');
      assert.match(res.name, /^note-\d+\.md$/, '自動命名');
      assert.strictEqual(fs.readFileSync(res.file, 'utf8'), '気になっていること\n');
      const bad = actions.writeNote(dir, { name: '../../evil', body: 'x' });
      assert.strictEqual(path.dirname(bad.file), path.join(dir, 'notes'),
        'パス区切りを含む名前は自動命名へ倒す（notes/ の外へ書かない）');
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('writeNote は空メモを拒否する', async () => {
    const { root, dir } = mkProject();
    try {
      assert.throws(() => actions.writeNote(dir, { body: '   ' }), /メモが空/);
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('updateNote は既存メモを同じファイルへ保存する', async () => {
    const { root, dir } = mkProject();
    try {
      const created = actions.writeNote(dir, { body: '書きかけ' });
      const before = fs.statSync(created.file).mtimeMs;
      const saved = actions.updateNote(dir, { name: created.name, body: '更新後', mtime: before });
      assert.strictEqual(saved.name, created.name);
      assert.strictEqual(fs.readFileSync(created.file, 'utf8'), '更新後\n');
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('updateNote は読み込み後に外部変更されたメモを上書きしない', async () => {
    const { root, dir } = mkProject();
    try {
      const created = actions.writeNote(dir, { body: '最初' });
      const stale = fs.statSync(created.file).mtimeMs;
      fs.writeFileSync(created.file, '外部変更\n', 'utf8');
      const future = new Date(Date.now() + 2000);
      fs.utimesSync(created.file, future, future);
      assert.throws(
        () => actions.updateNote(dir, { name: created.name, body: '上書き', mtime: stale }),
        /別の場所で更新/
      );
      assert.strictEqual(fs.readFileSync(created.file, 'utf8'), '外部変更\n');
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('listNotes は新しい順で archive/ を除く', async () => {
    const { root, dir } = mkProject();
    try {
      const nd = path.join(dir, 'notes');
      fs.mkdirSync(path.join(nd, 'archive'), { recursive: true });
      fs.writeFileSync(path.join(nd, 'old.md'), '古い', 'utf8');
      fs.writeFileSync(path.join(nd, 'new.md'), '新しい', 'utf8');
      fs.utimesSync(path.join(nd, 'old.md'), new Date(1000), new Date(1000));
      fs.writeFileSync(path.join(nd, 'archive', 'used.md'), '消費済み', 'utf8');
      const notes = actions.listNotes(dir);
      assert.deepStrictEqual(notes.map((n) => n.name), ['new.md', 'old.md']);
      assert.strictEqual(notes[1].body, '古い');
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('listNotes は notes/ が無くても壊れない', async () => {
    const { root, dir } = mkProject();
    try {
      assert.deepStrictEqual(actions.listNotes(dir), []);
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('markNoteBlocks はタスク化したブロックをメモ一覧へ関連付ける', async () => {
    const { root, dir } = mkProject();
    try {
      const created = actions.writeNote(dir, { body: 'タスクにする段落' });
      actions.markNoteBlocks(dir, {
        name: created.name,
        links: [{ fingerprint: 'abc123', taskIds: ['note-1', 'note-2'] }],
      });
      const note = actions.listNotes(dir)[0];
      assert.deepStrictEqual(note.links.abc123.taskIds, ['note-1', 'note-2']);
      assert.ok(fs.existsSync(path.join(dir, 'notes', '.task-links.json')));
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('requestDistillNotes は id 無しの distill-notes をドロップする', async () => {
    const { root, dir } = mkProject();
    try {
      actions.writeNote(dir, { body: 'メモ' });
      const res = await actions.requestDistillNotes({}, { dir });
      assert.strictEqual(res.via, 'file');
      const { file, rec } = dropped(dir);
      assert.match(file, /^viewer-distill-notes-project-\d+\.json$/);
      assert.strictEqual(rec.command, 'distill-notes');
      assert.ok(!('id' in rec), 'プロジェクト単位なので id は載せない');
      assert.strictEqual(rec.actor, 'agent-dashboard');
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('メモが無ければ分解を依頼しない（空の指示を投函しない）', async () => {
    const { root, dir } = mkProject();
    try {
      await assert.rejects(() => actions.requestDistillNotes({}, { dir }), /メモがありません/);
      assert.ok(!fs.existsSync(path.join(dir, 'commands')), 'ドロップもしない');
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  await test('未知の計画バージョンを指定した分解は拒否される', async () => {
    const { root, dir } = mkProject();
    try {
      fs.mkdirSync(path.join(dir, 'charters'), { recursive: true });
      fs.writeFileSync(path.join(dir, 'charters', 'v2.md'), '# Charter: v2\n## goal\nx\n', 'utf8');
      actions.writeNote(dir, { body: 'メモ' });
      await assert.rejects(
        () => actions.requestDistillNotes({}, { dir, charter: 'missing' }),
        /計画バージョン.*見つかりません/
      );
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  // --- renderer 側の契約（文字列アサーション） ------------------------------

  await test('acceptanceList は \\n 連結された複数行を 1 行 1 基準へ戻す', async () => {
    // eslint-disable-next-line no-new-func
    const acceptanceList = new Function(`${grab('acceptanceList')}; return acceptanceList;`)();
    assert.deepStrictEqual(acceptanceList({ extra: { task_acceptance_criteria: '新基準A\n新基準B' } }),
      ['新基準A', '新基準B'], '正規形（統一 verify）を一次に読む');
    assert.deepStrictEqual(
      acceptanceList({ extra: { task_acceptance_criteria: '正規', acceptance: '旧' } }),
      ['正規'], '正規形があれば旧 acceptance は読まない');
    assert.deepStrictEqual(acceptanceList({ extra: { acceptance: '基準A\n基準B' } }),
      ['基準A', '基準B'], '旧 acceptance も読み取り互換');
    assert.deepStrictEqual(acceptanceList({ extra: { accept: '昔の 1 行' } }), ['昔の 1 行'],
      '旧 accept は 1 項目として扱う（後方互換）');
    assert.deepStrictEqual(acceptanceList({ extra: {} }), []);
    assert.deepStrictEqual(acceptanceList(null), [], 'タスク未解決でも安全');
  });

  await test('タスク編集フォームは受入基準の欄を持つ', async () => {
    assert.ok(renderer.includes("id=\"rv-acceptance\""), '入力欄がある');
    assert.ok(!renderer.includes("$('rv-accept')"),
      '旧 accept の単値欄は残さない（同じものを 2 か所で編集させない）');
    assert.match(renderer,
      /fields\.task_acceptance_criteria = acceptanceNow\.length \? acceptanceNow : \[''\]/,
      '書き込みは正規形のみ。空なら [\'\'] を送って削除（本体の置換規約）');
  });

  await test('タスク編集フォームは risks を複数行で置換する', async () => {
    assert.ok(renderer.includes('id="rv-risks"'), 'リスクの入力欄がある');
    assert.match(renderer,
      /fields\.risks = risksNow\.length \? risksNow : \[''\]/,
      '1行1リスクの配列として送る。空なら削除する');
  });

  await test('タスク追加は詳細フォームを廃止し4段階で候補を確認する', async () => {
    assert.ok(renderer.includes('task-create-steps'));
    assert.ok(renderer.includes("['入力', '設計フロー', '候補確認', '追加完了']"));
    assert.ok(renderer.includes('task-candidate-card'));
  });

  await test('タスク追加は型付き入力と設計フローから複数候補を作る', async () => {
    assert.ok(renderer.includes('data-project-source-version'));
    assert.ok(renderer.includes('data-project-source-note'));
    assert.ok(renderer.includes('project-task-files'));
    assert.ok(renderer.includes("mode: 'project-design-proposal'"));
  });

  await test('タスク追加は選択した複数候補だけを既存inbox契約へ送る', async () => {
    assert.ok(renderer.includes('data-project-candidate'));
    assert.ok(renderer.includes('await api.enqueueTask'));
    assert.ok(renderer.includes('追加するタスクを選択してください'));
  });

  await test('メモ UI は編集と選択タスク化を分け、候補確認まで自動追加しない', async () => {
    assert.ok(renderer.includes('openNotesDialog'), 'メモダイアログがある');
    assert.ok(indexHtml.includes('id="notes-mode-edit"'), '編集モードがある');
    assert.ok(indexHtml.includes('id="notes-mode-task"'), 'タスク化モードがある');
    assert.match(indexHtml, /notes-editor-actions[\s\S]*notes-charter[\s\S]*btn-note-save/,
      'バージョン選択と保存はメモ編集欄にまとめる');
    assert.ok(indexHtml.includes('id="notes-charter-description"'), '選択したバージョンの説明を近くに表示する');
    assert.ok(renderer.includes("option.textContent = option.value || '初版'"),
      'メモのバージョンプルダウンはバージョン名だけを表示する');
    assert.ok(indexHtml.includes('id="enq-charter-description"'), 'タスク追加でもバージョンの説明を近くに表示する');
    assert.ok(renderer.includes("updateCharterSelectContext('enq-charter', 'enq-charter-description')"),
      'タスク追加でもバージョン名だけの選択肢と説明を同期する');
    assert.ok(indexHtml.includes('id="dlg-note-candidates"'), '候補確認ダイアログがある');
    assert.ok(renderer.includes("kind: 'note'"), '短いメモも共通の候補生成へ渡す');
    assert.ok(renderer.includes('api.enqueueTask'), '確認後は既存のタスク追加経路を使う');
    assert.ok(!indexHtml.includes('btn-notes-distill'), '全メモ一括分解は主要導線から外す');
    assert.ok(/task-toolbar-actions[\s\S]{0,800}id="btn-notes"/.test(renderer),
      'タスク画面からメモ作成・編集を開ける');
    assert.ok(renderer.includes("$('btn-notes').addEventListener('click', openNotesDialog)"),
      'メモの入口を既存のメモ作業領域へ接続する');
  });

  await test('ローカル文書は一度だけ読み、既存の候補確認経路へ渡す', async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'kpv-document-'));
    const file = path.join(root, 'plan.md');
    try {
      fs.writeFileSync(file, '# 計画\nAPIを置換する\n', 'utf8');
      const picked = await pickBacklogDocument({
        showOpenDialog: async () => ({ canceled: false, filePaths: [file] }),
      });
      assert.deepStrictEqual(picked, {
        canceled: false,
        name: 'plan.md',
        content: '# 計画\nAPIを置換する\n',
      });
      const large = path.join(root, 'large.txt');
      fs.writeFileSync(large, Buffer.alloc(MAX_BACKLOG_DOCUMENT_BYTES + 1, 65));
      await assert.rejects(
        () => pickBacklogDocument({ showOpenDialog: async () => ({ canceled: false, filePaths: [large] }) }),
        /64 KiB/
      );
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
    assert.ok(indexHtml.includes('id="dlg-document-task"'));
    assert.ok(indexHtml.includes('id="document-task-body"'));
    assert.ok(renderer.includes("mode: 'source-task-candidates'"));
    assert.ok(renderer.includes('buildSourceCandidates'), 'メモと文書で候補生成を共通化する');
    assert.ok(renderer.includes("source === 'document'"), '文書由来ではメモリンク処理を通さない');
    assert.ok(bootstrap.includes('pickDocumentForTasks'));
  });

  await test('バージョン選択は名前だけを表示し、目標を近くの説明欄へ出す', async () => {
    const select = { value: '顧客検証', options: [{ value: '顧客検証', textContent: '顧客検証 — 長い目標' }] };
    const description = { textContent: '', innerHTML: '' };
    // eslint-disable-next-line no-new-func
    const update = new Function('$', 'state', 'charterAssistContext', 'mdToHtml',
      `${grab('updateCharterSelectContext')}; return updateCharterSelectContext;`)(
      (id) => id === 'version' ? select : description,
      { project: {} },
      () => ({ goal: '**対象ユーザー**への検証を完了する' }),
      (src) => `<div class="md"><p>${src.replace(/\*\*(.*?)\*\*/, '<strong>$1</strong>')}</p></div>`
    );
    update('version', 'description');
    assert.strictEqual(select.options[0].textContent, '顧客検証');
    assert.strictEqual(
      description.innerHTML,
      '<div class="md"><p><strong>対象ユーザー</strong>への検証を完了する</p></div>'
    );
  });

  console.log(`\n${passed} tests passed`);
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
