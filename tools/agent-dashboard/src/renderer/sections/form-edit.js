'use strict';

// renderer.js から分割したセクション（クラシックスクリプトのグローバルスコープを共有）。
// core=renderer.js が state / $ / 共有定数を宣言し、先に読み込まれる前提。
// 読み込み順は index.html を参照（core → sections → features → bootstrap）。

// ---------------------------------------------------------------------------
// フォーム編集（マークダウン/JSON を直接書かせず、入力欄で編集する）
//   charter → 目標・制約・前提・成果物・完了条件のフォーム（マスター/バージョンで項目を切替）
//   policy  → 運用ルールの行リスト（種類 + 対象）
//   repos   → リポジトリの行リスト（名前/URL/ベース/担当範囲/説明）
//   各フォームには「テキストで編集」があり、必要なら従来の生テキスト編集へ切り替えられる。
// ---------------------------------------------------------------------------

// 編集ボタン（data-edit）のルーティング: 種類ごとにフォームを開く。
function openProjectFile(name, opts) {
  if (name === 'policy.md') return openPolicyForm();
  if (name === 'repos.json') return openReposForm();
  if (isCharterFile(name)) return openCharterForm(name, opts);
  return openEditFile(name, opts); // その他は生テキスト編集
}

// 単一入力の行リスト。値の配列を描画し、各行に入力＋削除。追加は container._add('') で。
function renderSimpleList(container, items, placeholder) {
  container.innerHTML = '';
  const add = (val) => {
    const row = document.createElement('div');
    row.className = 'list-row';
    row.innerHTML =
      `<input class="list-input mono" value="${esc(val || '')}" placeholder="${esc(placeholder || '')}" />` +
      `<button type="button" class="list-del" title="削除">✕</button>`;
    row.querySelector('.list-del').addEventListener('click', () => row.remove());
    container.appendChild(row);
  };
  (Array.isArray(items) ? items : []).forEach(add);
  container._add = add;
}

function readSimpleList(container) {
  return [...container.querySelectorAll('.list-input')].map((i) => i.value.trim()).filter(Boolean);
}

// -------- charter フォーム --------

// 現在編集中の charter フォーム状態（保持セクション・master・version 名を持ち回る）
let charterForm = null;

async function openCharterForm(name, opts) {
  const p = state.project;
  if (!p) return toast('プロジェクトを選択してください');
  const res = await guard('憲章の読込', () => api.readCharterFields(p.dir, name));
  if (!res) return;
  const fields = res.fields;
  const isVersion = /^charters\//.test(name);
  const isMaster = !isVersion && !!fields.master;
  // 継承の判定材料（プレビューで fields を書き換える前に控える）。
  // 見出しの無いバージョンはマスターへ**動的に**追従する（本体 _merge_master_charter の
  // 「見出しの有無」規則）。画面には実際に適用されるマスター値を初期表示し、保存時は
  // 「値を変えたときだけ」明示値として見出しを書く（変えなければ追従を維持）。
  const origConstraintsDefined = !!fields._constraintsDefined;
  const origAssumptionsDefined = !!fields._assumptionsDefined;
  let inheritedConstraints = null;
  let inheritedAssumptions = null;
  if (isVersion && (!origConstraintsDefined || !origAssumptionsDefined)) {
    const inherited = await guard('共通設定の読込', () => api.readCharterFields(p.dir, 'charter.md'));
    // 継承元になるのは charter.md がマスター（## master 付き）のときだけ。
    // 非マスターの charter.md から本体は継承しないので、値を「継承」として見せない。
    if (inherited && inherited.fields && inherited.fields.master) {
      inheritedConstraints = inherited.fields.constraints || [];
      inheritedAssumptions = inherited.fields.assumptions || [];
      if (!origConstraintsDefined) fields.constraints = inheritedConstraints.slice();
      if (!origAssumptionsDefined) fields.assumptions = inheritedAssumptions.slice();
    }
  }
  // 新規バージョン追加時は、前バージョン（または憲章）から引き継いだ やること/完了条件/成果物 を
  // 初期値にする（既存ファイルの編集では上書きしない＝res.exists のときは seed を使わない）。
  // 制約・前提はコピーせず、上の継承表示に任せる（コピーすると追従が切れた明示値になる）。
  if (!res.exists && opts) {
    if (opts.seedGoal) fields.goal = opts.seedGoal;
    if (Array.isArray(opts.seedAcceptance)) fields.acceptance = opts.seedAcceptance;
    if (Array.isArray(opts.seedDeliverables)) fields.deliverables = opts.seedDeliverables;
  }
  charterForm = {
    dir: p.dir, name, fields, isVersion, isMaster, exists: res.exists,
    origConstraintsDefined, origAssumptionsDefined, inheritedConstraints, inheritedAssumptions,
  };

  // 見出し・説明
  const verName = isVersion ? name.replace(/^charters\//, '').replace(/\.md$/, '') : '';
  $('ec-title').textContent = isVersion
    ? `計画バージョンを編集: ${verName}`
    : isMaster
      ? 'マスター憲章を編集'
      : '憲章を編集';
  $('ec-desc').textContent = isVersion
    ? 'このバージョンで達成すること、完了条件、制約、前提を設定します。新規作成時は共通設定を引き継ぎ、ここで個別に変更できます。'
    : isMaster
      ? '全バージョン共通の前提です。ここからタスクは作られません（完了条件は各バージョンが持ちます）。'
      : '目標と完了条件、制約・前提・成果物を記入します。';

  // 名前（バージョンはファイル名が識別子なので隠す。マスター/単一はプロジェクト名として編集可）
  $('ec-name-field').classList.toggle('hidden', isVersion);
  $('ec-name').value = fields.name || (isVersion ? verName : p.name || '');

  // 目標/やること
  $('ec-goal-label').textContent = isVersion ? 'やること（このバージョンで達成すること）' : '目標';
  $('ec-goal').value = fields.goal || '';

  // 完了条件（acceptance）はバージョン、または「マスターでない単一 charter」に出す。マスターは非表示。
  const showAcceptance = !isMaster;
  $('ec-acceptance-field').classList.toggle('hidden', !showAcceptance);
  renderSimpleList($('ec-acceptance'), fields.acceptance, '例: pytest -q tests/ または accept: 使用例が載っている');

  // 成果物は常に出す
  renderSimpleList($('ec-deliverables'), fields.deliverables, '例: report.py');

  // 制約・前提は新規版で共通設定をコピーするが、保存後は各バージョン固有の値になる。
  $('ec-constraints-field').classList.remove('hidden');
  $('ec-assumptions-field').classList.remove('hidden');
  renderSimpleList($('ec-constraints'), fields.constraints, '例: 標準ライブラリのみ');
  renderSimpleList($('ec-assumptions'), fields.assumptions, '例: 入力は UTF-8');

  // 継承の状態を実態に合わせて表示する:
  //   追従中（見出し無し・マスターあり）→ 変更しない限り共通設定に追従し続ける
  //   明示値（見出しあり）→ このバージョン固有・共通設定の変更には追従しない
  const note = $('ec-inherit-note');
  if (isVersion && inheritedConstraints !== null) {
    note.textContent =
      origConstraintsDefined && origAssumptionsDefined
        ? '制約・前提はこのバージョン固有の値です（共通設定の変更には追従しません）。対象リポジトリは共通設定を使用します。'
        : '制約・前提は共通設定（マスター）の値を表示しています。変更しなければ共通設定に追従し続け、' +
          '変更するとこのバージョンだけの値になります（すべて削除すると「空で上書き」として保存されます）。' +
          '対象リポジトリは共通設定を使用します。';
    note.classList.remove('hidden');
  } else if (isVersion) {
    note.textContent = '制約・前提はこのバージョン固有の値です。対象リポジトリは共通設定を使用します。';
    note.classList.remove('hidden');
  } else {
    note.classList.add('hidden');
  }
  $('ec-hint').textContent = res.exists
    ? '保存した内容は次回の自動実行から反映されます'
    : '未作成 — 保存すると新規作成します';
  $('dlg-edit-charter').showModal();
}

async function saveCharterForm() {
  const cf = charterForm;
  if (!cf) return;
  if (cf.isVersion && !$('ec-goal').value.trim()) {
    return toast('やること（このバージョンで達成すること）を記入してください');
  }
  // 完了条件が無いバージョンは done を判定できず、要対応に「完了条件を追加」が出続ける。
  // 保存前に確認して、うっかり空のまま作るのを防ぐ（意図的なら続行できる）。
  if (cf.isVersion && !readSimpleList($('ec-acceptance')).length) {
    const yes = await confirmDialog(
      '完了条件が未設定です。\nこのままだと完了を判定できず、要対応に「完了条件を追加」が出続けます。\n' +
        'このまま保存しますか？（後から追加もできます）'
    );
    if (!yes) return;
  }
  // フォームの値をフィールドへ反映（保持セクション _reposRaw/_linksRaw/_masterRaw はそのまま残す）
  const f = { ...cf.fields };
  f.master = cf.isMaster;
  if (!cf.isVersion) f.name = $('ec-name').value.trim() || f.name;
  else f.name = cf.name.replace(/^charters\//, '').replace(/\.md$/, ''); // バージョンはファイル名を名前に
  f.goal = $('ec-goal').value.trim();
  f.deliverables = readSimpleList($('ec-deliverables'));
  if (!cf.isMaster) f.acceptance = readSimpleList($('ec-acceptance'));
  const cons = readSimpleList($('ec-constraints'));
  const assum = readSimpleList($('ec-assumptions'));
  f.constraints = cons;
  f.assumptions = assum;
  // 見出しの扱い（本体の継承規則「見出しがあれば明示値・無ければマスターへ追従」と対）:
  //   元々見出しがある → 明示値のまま維持。
  //   マスターへ追従中 → 値を変えていなければ見出しを書かず追従を維持、変えたときだけ明示化
  //   （全削除は「継承を空に上書き」の明示の意思として空見出しを書く）。
  //   継承元が無い → 値を入れたときだけ見出しを書く。
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  f._constraintsDefined = cf.origConstraintsDefined
    || (cf.inheritedConstraints !== null ? !same(cons, cf.inheritedConstraints) : cons.length > 0);
  f._assumptionsDefined = cf.origAssumptionsDefined
    || (cf.inheritedAssumptions !== null ? !same(assum, cf.inheritedAssumptions) : assum.length > 0);
  const ok = await guard('保存', async () => {
    await api.writeCharterFields(cf.dir, cf.name, f);
    return true;
  });
  if (ok) {
    toast(`${cf.isVersion ? '計画バージョン' : '憲章'}を保存しました`, true);
    gitPushAfterWrite(`agent-dashboard: edit ${cf.name}`, cf.dir);
    $('dlg-edit-charter').close();
    await reloadProject();
  }
}

// フォームから生テキスト編集へ切り替える（込み入った編集や、フォームが扱わない項目の調整用）。
function charterFormToRaw() {
  const cf = charterForm;
  if (!cf) return;
  $('dlg-edit-charter').close();
  openEditFile(cf.name);
}

// -------- policy フォーム --------

const POLICY_KIND_OPTIONS = [
  ['deny', '自動実行しない（deny）'],
  ['pin', '最優先にする（pin）'],
  ['defer', '後回しにする（defer）'],
  ['offload', '委任で実行（offload）'],
  ['gate', '承認を必須にする（gate）'],
  ['protect', '保護する（protect）'],
  ['route', '振り分け先を指定（route）'],
];

let policyForm = null;

function renderPolicyRules(container, rules) {
  container.innerHTML = '';
  const opts = (sel) =>
    POLICY_KIND_OPTIONS.map(([k, label]) => `<option value="${k}"${sel === k ? ' selected' : ''}>${esc(label)}</option>`).join('');
  const add = (r) => {
    const row = document.createElement('div');
    row.className = 'list-row';
    row.innerHTML =
      `<select class="pol-kind">${opts(r && r.kind)}</select>` +
      `<input class="pol-value mono" value="${esc((r && r.value) || '')}" placeholder="対象（タスクのタイトルや ID にマッチする語）" />` +
      `<button type="button" class="list-del" title="削除">✕</button>`;
    row.querySelector('.list-del').addEventListener('click', () => row.remove());
    container.appendChild(row);
  };
  (Array.isArray(rules) ? rules : []).forEach(add);
  container._add = add;
}

async function openPolicyForm() {
  const p = state.project;
  if (!p) return toast('プロジェクトを選択してください');
  const res = await guard('運用ルールの読込', () => api.readPolicy(p.dir));
  if (!res) return;
  policyForm = { dir: p.dir };
  renderPolicyRules($('ep-rules'), res.rules);
  $('dlg-edit-policy').showModal();
}

async function savePolicyForm() {
  const pf = policyForm;
  if (!pf) return;
  const rules = [...$('ep-rules').querySelectorAll('.list-row')]
    .map((row) => ({
      kind: row.querySelector('.pol-kind').value,
      value: row.querySelector('.pol-value').value.trim(),
    }))
    .filter((r) => r.value);
  const ok = await guard('保存', async () => {
    await api.writePolicy(pf.dir, rules);
    return true;
  });
  if (ok) {
    toast('運用ルールを保存しました', true);
    gitPushAfterWrite('agent-dashboard: edit policy.md', pf.dir);
    $('dlg-edit-policy').close();
    await reloadProject();
  }
}

// -------- repos フォーム --------

let reposForm = null;

function renderRepoRows(container, rows) {
  container.innerHTML = '';
  const add = (r) => {
    const row = document.createElement('div');
    row.className = 'np-repo-row';
    row.innerHTML =
      `<input class="er-name mono" placeholder="名前" value="${esc((r && r.name) || '')}" />` +
      `<input class="er-url mono" placeholder="git URL（必須）" value="${esc((r && r.url) || '')}" />` +
      `<input class="er-base mono" placeholder="ベース 例 main" value="${esc((r && r.base) || '')}" />` +
      `<input class="er-target mono" placeholder="MR先（省略=ベース）" value="${esc((r && r.target) || '')}" />` +
      `<input class="er-path mono" placeholder="モノレポ内フォルダ 例 apps/api" value="${esc((r && r.path) || '')}" />` +
      `<input class="er-owns mono" placeholder="担当範囲（省略=参照のみ）" value="${esc((r && r.owns) || '')}" />` +
      `<input class="er-desc" placeholder="説明" value="${esc((r && r.desc) || '')}" />` +
      `<button type="button" class="np-r-del" title="削除">✕</button>`;
    // フォームが列を持たないキー（readonly/local/docs 等 = _extra）は行の DOM に持ち回り、
    // 保存時にそのまま書き戻す（フォームを開いて保存しただけで消えないように）。
    row._readonly = !!(r && r.readonly);
    row._extra = (r && r._extra) || null;
    if (row._extra) {
      row.querySelector('.er-desc').title =
        `フォーム外の設定を保持しています: ${Object.keys(row._extra).join(', ')}（保存時にそのまま残ります）`;
    }
    row.querySelector('.np-r-del').addEventListener('click', () => row.remove());
    container.appendChild(row);
  };
  (Array.isArray(rows) ? rows : []).forEach(add);
  container._add = add;
}

async function openReposForm() {
  const p = state.project;
  if (!p) return toast('プロジェクトを選択してください');
  const res = await guard('リポジトリ一覧の読込', () => api.readRepos(p.dir));
  if (!res) return;
  // repos.yaml / repos.yml が正のプロジェクトはフォームで扱えない（保存すると repos.json が
  // できるが本体は yaml 優先で無視する）。生テキスト編集へ誘導する。
  if (res.yamlFile) {
    toast(`このプロジェクトは ${res.yamlFile} が正です。テキスト編集で開きます`);
    return openEditFile(res.yamlFile);
  }
  reposForm = { dir: p.dir };
  renderRepoRows($('er-rows'), res.rows);
  const warn = $('er-warning');
  if (warn) {
    if (res.generated) {
      warn.textContent =
        '⚠ この repos.json は charter.md の ## repos から自動生成されています。ここで保存すると' +
        '手管理（repos.json が正）に切り替わり、以後 charter の ## repos は反映されなくなります。' +
        'charter 主導のままにするなら、charter.md の ## repos を編集してください。';
      warn.classList.remove('hidden');
    } else {
      warn.classList.add('hidden');
    }
  }
  $('dlg-edit-repos').showModal();
}

async function saveReposForm() {
  const rf = reposForm;
  if (!rf) return;
  const rows = [...$('er-rows').querySelectorAll('.np-repo-row')]
    .map((row) => ({
      name: row.querySelector('.er-name').value.trim(),
      url: row.querySelector('.er-url').value.trim(),
      base: row.querySelector('.er-base').value.trim(),
      target: row.querySelector('.er-target').value.trim(),
      path: row.querySelector('.er-path').value.trim(),
      owns: row.querySelector('.er-owns').value.trim(),
      desc: row.querySelector('.er-desc').value.trim(),
      readonly: row._readonly || false,
      ...(row._extra ? { _extra: row._extra } : {}),
    }))
    .filter((r) => r.url);
  const ok = await guard('保存', async () => {
    await api.writeRepos(rf.dir, rows);
    return true;
  });
  if (ok) {
    toast('リポジトリ一覧を保存しました', true);
    gitPushAfterWrite('agent-dashboard: edit repos.json', rf.dir);
    $('dlg-edit-repos').close();
    await reloadProject();
  }
}

const CHARTER_SECTION_GUIDE =
  '書式（セクション）: ## goal（目標）/ ## constraints（制約）/ ## assumptions（前提）/ ' +
  '## deliverables（成果物）/ ## acceptance（完了条件 — 成功で終わるコマンド、または accept: 文章）/ ' +
  '## repos（対象リポジトリ）/ ## links（参考リンク）';

async function openEditFile(name, opts) {
  const p = state.project;
  if (!p) return toast('プロジェクトを選択してください');
  const info = await guard('ファイル読込', () => api.readProjectFile(p.dir, name));
  if (!info) return;
  // seedContent: 新規 charter バージョン追加時に、前バージョンの内容を書きかけとして
  // 差し込む（openAddCharterVersion 参照）。まだファイルが無いときだけ使う＝既存ファイルの
  // 編集では絶対に上書きしない。
  const seeded = !info.exists && opts && opts.seedContent;
  state.editFile = { dir: p.dir, name, file: info.file, aiBackup: null };
  $('ef-title').textContent = `編集: ${info.label}`;
  $('ef-content').value = seeded ? opts.seedContent : info.content || '';
  const warn = $('ef-warning');
  if (info.generated) {
    warn.textContent =
      '⚠ この repos.json は charter.md の ## repos から自動生成されています（_meta.generated_from）。' +
      '直接編集しても run 時に charter から上書きされます。恒久的に手で管理するなら _meta を消すか、' +
      'charter の ## repos を編集してください。';
    warn.classList.remove('hidden');
  } else if (isCharterFile(name)) {
    warn.textContent = CHARTER_SECTION_GUIDE;
    warn.classList.remove('hidden');
  } else {
    warn.classList.add('hidden');
  }
  // charter だけに入力補助（雛形挿入・AI 補完）を出す
  $('ef-ai-row').classList.toggle('hidden', !isCharterFile(name));
  $('btn-ef-ai-undo').classList.add('hidden');
  $('ef-ai-status').textContent = '';
  $('btn-ef-ai').disabled = false;
  $('ef-hint').textContent = info.exists
    ? `${info.file}｜保存した内容は次回の自動実行から反映されます`
    : seeded
      ? `${info.file}（未作成 — 前バージョンの内容をコピーしています。保存すると新規作成します）`
      : `${info.file}（未作成 — 保存すると新規作成します）`;
  $('dlg-edit-file').showModal();
}

// charters/<name>.md のバージョン名として使える文字か（authoring.js の BAD_NAME_RE と揃える。
// スラッシュ等の path traversal はサーバ側 editablePath でも弾かれるが、ここで先に弾いて
// わかりやすいエラーにする）
const BAD_CHARTER_NAME_RE = /[\s/\\<>:"|?*-]/;
function isValidCharterVersionName(name) {
  return !!name && name !== '.' && name !== '..' && !BAD_CHARTER_NAME_RE.test(name);
}

// 既存プロジェクトに新しい charter バージョン（charters/<名前>.md）を追加する。
// 「新規プロジェクト作成」時にしか charter 名を指定できなかったギャップを埋める入口。
// 実体の作成は openEditFile → 保存（saveEditFile）が行う＝ここでは名前を確定するだけ。
// 注意: charters/*.md ができると agent-project は charter.md（初版）を駆動対象から外す。
// 初版がまだ charter.md 単体のときは、その旨と「⤴ バージョン化」の案内を説明文に出す。
async function openAddCharterVersion() {
  const p = state.project;
  if (!p) return toast('プロジェクトを選択してください');
  const master = !!(p.charter && p.charter.master);
  const src = p.charters && p.charters.length ? '直近のバージョン' : 'マスター憲章';
  $('nc-title').textContent = '計画バージョンを追加';
  $('nc-desc').textContent = master
    ? `バージョン名を決めると、続けて内容を入力する画面が開きます（${src}の やること・完了条件・成果物 と、共通の制約・前提を引き継ぎます。すべてこのバージョン用に変更できます）。`
    : p.charter && !(p.charters && p.charters.length)
      ? '新しい計画バージョンを作成します。作成後はバージョン一覧の計画だけが実行され、' +
        '初版は実行の対象から外れます（概要タブの「⤴ バージョン名を付ける」で初版も並行して進められます）。'
      : `新しい計画バージョンを作成します（${src}の内容を引き継いだ状態でフォームが開きます。既存のバージョンはそのまま並行して進みます）。`;
  $('nc-name').value = '';
  $('dlg-new-charter').dataset.mode = 'add';
  $('dlg-new-charter').showModal();
  $('nc-name').focus();
}

async function submitNewCharterVersion() {
  const p = state.project;
  if (!p) return $('dlg-new-charter').close();
  const mode = $('dlg-new-charter').dataset.mode || 'add';
  const name = $('nc-name').value.trim();
  if (!isValidCharterVersionName(name)) {
    toast('バージョン名が不正です（空白・スラッシュ・ハイフン等は使えません）');
    return;
  }
  const existing = new Set((p.charters || []).map((c) => c.name));
  if (existing.has(name)) {
    toast(`バージョン「${name}」はすでに存在します`);
    return;
  }
  $('dlg-new-charter').close();
  if (mode === 'promote') {
    await submitPromoteCharter(name);
    return;
  }
  // 初期値の引き継ぎ元: 直近の計画バージョン（あれば）、無ければマスター/初版の憲章。
  // その やること/完了条件/成果物 をフォームの初期状態に入れて、前バージョンから編集して作れる
  // ようにする。制約・前提はここでコピーしない — マスターがあれば openCharterForm が
  // 「継承値の表示」として出し、変更しない限りマスターへの追従が保たれる（コピーすると
  // その時点のスナップショットで固定され、以後の共通設定の変更が伝わらなくなる）。
  const srcName =
    p.charters && p.charters.length ? `charters/${p.charters[p.charters.length - 1].name}.md` : 'charter.md';
  let seed = {};
  const src = await guard('引き継ぎ元の読込', () => api.readCharterFields(p.dir, srcName));
  if (src && src.fields) {
    seed = {
      seedGoal: src.fields.goal || '',
      seedAcceptance: Array.isArray(src.fields.acceptance) ? src.fields.acceptance : [],
      seedDeliverables: Array.isArray(src.fields.deliverables) ? src.fields.deliverables : [],
    };
  }
  // 名前を決めたら、続けて内容（やること・完了条件）を入力するバージョンのフォームを開く（保存で新規作成）
  await openCharterForm(`charters/${name}.md`, seed);
}

// charter.md の雛形を挿入する（空のときだけ即挿入。書きかけがあるときは確認してから置換）
async function insertCharterTemplate() {
  const ef = state.editFile;
  if (!ef) return;
  const current = $('ef-content').value;
  if (current.trim()) {
    const ok = await confirmDialog('編集中の内容を破棄して charter の雛形に置き換えます。よろしいですか？');
    if (!ok) return;
  }
  const m = /^charters\/([^/\\]+)\.md$/.exec(ef.name);
  const fallback = (state.project && state.project.name) || 'project';
  // バージョン（charters/<name>.md）の雛形は空の制約・前提見出しを持たない
  // （そのまま保存してもマスターの制約・前提を「空に上書き」しない）
  const res = await guard('雛形の取得', () => api.charterTemplate(m ? m[1] : fallback, !!m));
  if (!res) return;
  $('ef-content').value = res.content;
  $('ef-ai-status').textContent = '雛形を挿入しました — 各セクションを埋めるか、✨ AI 補完で下書きできます';
}

// エディタの charter 全文をエージェントに渡し、書式を保った完成版へ補完する。
// 置換のみでファイルには書かない（保存は人の「保存」ボタン）。補完前の内容は
// aiBackup に取り置き、「↩ 補完前に戻す」で戻せる。
async function aiRefineCharter() {
  const ef = state.editFile;
  if (!ef) return;
  const btn = $('btn-ef-ai');
  const status = $('ef-ai-status');
  const before = $('ef-content').value;
  btn.disabled = true;
  status.textContent = 'エージェントに問い合わせ中…（モデル応答まで数十秒かかることがあります）';
  try {
    const res = await api.agentCharter({ dir: ef.dir, mode: 'refine', content: before });
    ef.aiBackup = before;
    $('ef-content').value = res.content;
    $('btn-ef-ai-undo').classList.remove('hidden');
    status.textContent =
      `補完しました（${res.cli}${res.model ? ` / ${res.model}` : ''}）— 内容を確認して保存してください`;
  } catch (err) {
    status.textContent = '';
    toast(`AI 補完に失敗しました: ${err.message || err}`);
  } finally {
    btn.disabled = false;
  }
}

function undoAiRefine() {
  const ef = state.editFile;
  if (!ef || ef.aiBackup == null) return;
  $('ef-content').value = ef.aiBackup;
  ef.aiBackup = null;
  $('btn-ef-ai-undo').classList.add('hidden');
  $('ef-ai-status').textContent = '補完前の内容に戻しました';
}

async function saveEditFile() {
  const ef = state.editFile;
  if (!ef) return;
  const content = $('ef-content').value;
  const ok = await guard('保存', async () => {
    await api.writeProjectFile(ef.dir, ef.name, content);
    toast(`${ef.name} を保存しました`, true);
    return true;
  });
  if (ok) {
    gitPushAfterWrite(`agent-dashboard: edit ${ef.name}`, ef.dir);
    $('dlg-edit-file').close();
    await reloadProject();
  }
}
