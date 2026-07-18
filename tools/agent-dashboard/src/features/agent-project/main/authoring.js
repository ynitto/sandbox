'use strict';

// プロジェクトの「新規作成」層と、人が書く上位入力ファイル（charter.md / policy.md /
// repos.*）の「直接編集」層。ここが書くのは kiro-project の **人が書く入力だけ**:
//   - charter.md … 最上位入力（目標/制約/前提/成果物/acceptance/repos）。これを置くと
//     run が plan→execute→evaluate を回し、backlog をこの憲章から生成する。
//   - policy.md  … 運用ルール（deny/pin/defer/gate/route…）。
//   - repos.*    … リポジトリレジストリ（schemas/repos.schema.json）。charter ## repos から
//     自動生成もされる（_meta.generated_from マーカー付き）。
// タスク状態（backlog/*.md の status 等）は一切書かない — 「done は verify のみが根拠」の
// 不変条件をアプリから壊さないため（actions.js と同じ護り）。
//
// 「アーカイブ済み（done）タスクの revise して再投入」は inbox 契約（actions.enqueueToInbox）を
// そのまま使うため、ここには置かない（新しいタスクとして triage→verify を通す＝done 迂回にしない）。

const fs = require('fs');
const path = require('path');

// 直接編集できるファイル（人が書く入力のみ）。basename 完全一致でホワイトリストする。
const EDITABLE_FILES = {
  'charter.md': { label: 'プロジェクト憲章（charter.md）', kind: 'markdown' },
  'policy.md': { label: '運用ルール（policy.md）', kind: 'markdown' },
  'rules.md': { label: 'プロジェクトルール（rules.md）', kind: 'markdown' },
  'repos.json': { label: 'リポジトリ一覧（repos.json）', kind: 'json' },
  'repos.yaml': { label: 'リポジトリ一覧（repos.yaml）', kind: 'yaml' },
  'repos.yml': { label: 'リポジトリ一覧（repos.yml）', kind: 'yaml' },
};

// ディレクトリ名に使えない文字（Windows/Unix 共通で避ける）。'.'/'..' も弾く。
const BAD_NAME_RE = /[\s/\\<>:"|?*\x00-\x1f]/;

// charters/<name>.md（複数バージョンの charter）も人が書く入力として編集を許可する
const CHARTERS_RE = /^charters\/([^/\\]+\.md)$/;

function isEditable(name) {
  return Object.prototype.hasOwnProperty.call(EDITABLE_FILES, name) || CHARTERS_RE.test(name);
}

function editableMeta(name) {
  if (Object.prototype.hasOwnProperty.call(EDITABLE_FILES, name)) return EDITABLE_FILES[name];
  const m = CHARTERS_RE.exec(name);
  if (m) return { label: `計画バージョン（${name}）`, kind: 'markdown' };
  return null;
}

function editablePath(dir, name) {
  const m = CHARTERS_RE.exec(name);
  if (m) {
    if (path.basename(m[1]) !== m[1]) throw new Error(`不正なファイル名です: ${name}`);
    return path.join(dir, 'charters', m[1]);
  }
  if (!isEditable(name)) throw new Error(`編集できないファイルです: ${name}`);
  if (path.basename(name) !== name) throw new Error(`不正なファイル名です: ${name}`);
  return path.join(dir, name);
}

// ---------------------------------------------------------------------------
// charter.md の雛形生成
// ---------------------------------------------------------------------------

// 複数行の入力を箇条書き（- 始まり）に整える。すでに - 始まりの行はそのまま。
function bulletize(text) {
  return String(text || '')
    .split('\n')
    .map((l) => l.replace(/\s+$/, ''))
    .filter((l) => l.trim())
    .map((l) => (/^\s*-\s+/.test(l) ? l : `- ${l.trim()}`))
    .join('\n');
}

// charter の `## repos` セクションを組み立てる（charter.md.example の書式）:
//   - <name> = <url>
//     - owns: ...
//     - desc: ...
//     - base: ...
function charterReposLines(repos) {
  const entries = Array.isArray(repos) ? repos : [];
  const out = [];
  for (const e of entries) {
    if (!e) continue;
    const url = String(e.url || '').trim();
    if (!url) continue;
    const nm = String(e.name || '').trim();
    out.push(nm ? `- ${nm} = ${url}` : `- ${url}`);
    for (const k of ['owns', 'desc', 'base', 'target', 'path']) {
      const v = e[k] != null && String(e[k]).trim();
      if (v) out.push(`  - ${k}: ${v}`);
    }
  }
  return out.join('\n');
}

// 新規プロジェクトの charter.md 本文を作る。空セクションでも見出しは残す（後から編集できる）。
// spec.master が真なら `## master` セクションを付けたマスター憲章（全バージョン共通の前提。
// kiro-project はこれを分解せず、charters/<名前>.md の計画バージョンへ継承する）にする。
// マスター憲章は完了条件（acceptance）を持たない: 完了条件は各計画バージョンが定義する。
function buildCharter(spec) {
  const name = String((spec && spec.name) || '').trim() || 'project';
  const master = !!(spec && spec.master);
  const out = [`# Charter: ${name}`, ''];
  const section = (key, body, hint) => {
    out.push(`## ${key}`);
    if (hint) out.push(hint);
    if (body) out.push(body);
    out.push('');
  };
  if (master) {
    section(
      'master',
      '',
      '# この憲章はマスター（全バージョン共通の前提）です。ここからタスクは作られません。\n' +
        '# やるべきことは charters/<名前>.md（計画バージョン）に書きます。'
    );
  }
  section('goal', String((spec && spec.goal) || '').trim());
  section('constraints', bulletize(spec && spec.constraints));
  section('assumptions', bulletize(spec && spec.assumptions));
  section('deliverables', bulletize(spec && spec.deliverables));
  if (!master) {
    // 完了条件（acceptance）はマスターには書かない。計画バージョン（またはバージョン運用でない
    // 単一 charter）だけが持つ＝done 判定の根拠。
    section(
      'acceptance',
      bulletize(spec && spec.acceptance),
      '# 各行＝終了コード0をPASSとみなすシェルコマンド。書けない条件は `- accept: <自然文>` でも可。' +
        '\n# acceptance を書けないプロジェクトは done 判定不能 → 必ず人へ回る。'
    );
  }
  section(
    'repos',
    charterReposLines(spec && spec.repos),
    '# 対象リポジトリ（任意）。owns を書くと書込先（ワークスペース）、書かなければ参照のみ。'
  );
  section('links', '', '# 参考リンク・横展開先プロジェクト（任意）。');
  return `${out.join('\n').replace(/\n+$/, '')}\n`;
}

// ---------------------------------------------------------------------------
// フォーム編集用: charter / policy / repos の構造化パース・シリアライズ
//   人がマークダウン/JSON を直接書く代わりに、ビュアーの入力欄で編集するための橋渡し。
//   charter はフォームが触らないセクション（repos/links/master 本文）を生テキストで保持して
//   書き戻し時にそのまま戻す（データを失わない）。
// ---------------------------------------------------------------------------

// charter.md をセクション単位に分解する（Python の parse_charter と同じ見出し規則）。
function parseCharterDoc(text) {
  const s = String(text || '');
  const titleM = s.match(/^#\s+(?:Charter|憲章)\s*[:：]?\s*(.+?)\s*$/m);
  const sections = {};
  const order = [];
  let cur = null;
  for (const line of s.split('\n')) {
    const m = line.match(/^##\s+([A-Za-z]+)\b/);
    if (m) {
      cur = m[1].toLowerCase();
      if (!(cur in sections)) {
        sections[cur] = [];
        order.push(cur);
      }
      continue;
    }
    if (cur) sections[cur].push(line);
  }
  const body = {};
  for (const k of order) body[k] = sections[k].join('\n').replace(/\n+$/, '');
  return { title: titleM ? titleM[1].trim() : '', sections: body, order, master: 'master' in body };
}

// セクション本文（複数行）を箇条書き項目の配列にする（- とコード用バッククォートを剥がす。
// kiro-project の _charter_bullets と同じく `cmd` の飾りを外して素の文字列で扱う）。
function sectionItems(body) {
  return String(body || '')
    .split('\n')
    .map((l) => l.replace(/^\s*[-*+]\s+/, '').trim())
    .filter((l) => l && !l.startsWith('#') && !l.startsWith('<!--'))
    .map((l) => (l.length >= 2 && l.startsWith('`') && l.endsWith('`') ? l.slice(1, -1).trim() : l));
}

// charter.md → フォーム編集用フィールド。編集対象を配列化し、フォームが触らないセクション
// （repos/links/master 本文）は生テキストで保持する（_ 始まりのキー）。
function charterToFields(text) {
  const doc = parseCharterDoc(text);
  return {
    name: doc.title,
    master: doc.master,
    goal: (doc.sections.goal || '').trim(),
    constraints: sectionItems(doc.sections.constraints),
    assumptions: sectionItems(doc.sections.assumptions),
    _constraintsDefined: Object.prototype.hasOwnProperty.call(doc.sections, 'constraints'),
    _assumptionsDefined: Object.prototype.hasOwnProperty.call(doc.sections, 'assumptions'),
    deliverables: sectionItems(doc.sections.deliverables),
    acceptance: sectionItems(doc.sections.acceptance),
    _reposRaw: doc.sections.repos || '',
    _linksRaw: doc.sections.links || '',
    _masterRaw: doc.sections.master || '',
  };
}

// フォームフィールド → charter.md。master は完了条件（acceptance）を書かない（バージョンが持つ）。
function fieldsToCharter(f) {
  const bul = (arr) =>
    (Array.isArray(arr) ? arr : [])
      .map((s) => String(s).trim())
      .filter(Boolean)
      .map((s) => `- ${s}`)
      .join('\n');
  const out = [`# Charter: ${String((f && f.name) || 'project').trim() || 'project'}`, ''];
  const sec = (key, body) => {
    out.push(`## ${key}`);
    if (body) out.push(body);
    out.push('');
  };
  if (f && f.master) {
    sec(
      'master',
      (f._masterRaw || '').trim() ||
        '<!-- マスター憲章（全バージョン共通の前提）。ここからタスクは作られません。\n' +
          '     やるべきことは計画バージョン（charters/<名前>.md）に書きます。 -->'
    );
  }
  sec('goal', String((f && f.goal) || '').trim());
  sec('constraints', bul(f && f.constraints));
  sec('assumptions', bul(f && f.assumptions));
  sec('deliverables', bul(f && f.deliverables));
  if (!(f && f.master)) sec('acceptance', bul(f && f.acceptance));
  if (((f && f._reposRaw) || '').trim()) sec('repos', f._reposRaw.trim());
  if (((f && f._linksRaw) || '').trim()) sec('links', f._linksRaw.trim());
  return `${out.join('\n').replace(/\n+$/, '')}\n`;
}

// policy.md ↔ ルール配列（kind/value）。kiro.js parsePolicy と同じ規則。
const POLICY_KINDS = ['deny', 'pin', 'defer', 'offload', 'gate', 'protect', 'route'];
function policyToRules(text) {
  const rules = [];
  for (const line of String(text || '').split('\n')) {
    const m = line.trim().match(/^(deny|pin|defer|offload|gate|protect|route):\s*(.+)$/);
    if (m) rules.push({ kind: m[1], value: m[2].split('#')[0].trim() });
  }
  return rules;
}
function rulesToPolicy(rules) {
  const lines = (Array.isArray(rules) ? rules : [])
    .filter((r) => r && POLICY_KINDS.includes(r.kind) && String(r.value || '').trim())
    .map((r) => `${r.kind}: ${String(r.value).trim()}`);
  return lines.length ? `${lines.join('\n')}\n` : '';
}

// repos.json ↔ フォーム行（name/url/base/owns/desc）。
function reposJsonToRows(content) {
  let data;
  try {
    data = JSON.parse(content);
  } catch {
    return [];
  }
  if (!data || typeof data !== 'object') return [];
  const rows = [];
  for (const [name, e] of Object.entries(data)) {
    if (name === '_meta' || !e || typeof e !== 'object') continue;
    rows.push({
      name,
      url: e.url || '',
      base: e.base || '',
      owns: Array.isArray(e.owns) ? e.owns.join(', ') : e.owns || '',
      desc: e.desc || '',
    });
  }
  return rows;
}

// ---------------------------------------------------------------------------
// repos.json の生成（kiro-project の export_repo_registry と同じ形）
// ---------------------------------------------------------------------------

// カンマ/空白区切りのグロブ文字列を配列にする（repos スキーマの globs）。
function splitGlobs(v) {
  if (Array.isArray(v)) return v.map((x) => String(x).trim()).filter(Boolean);
  return String(v || '')
    .split(/[,\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

// キーを再帰的にソートした安定 JSON（kiro-project は sort_keys=True で書くため、
// 生成物の byte 一致を狙って同じ順序にする＝本体が毎回上書きし直さない）。
function sortDeep(v) {
  if (Array.isArray(v)) return v.map(sortDeep);
  if (v && typeof v === 'object') {
    const o = {};
    for (const k of Object.keys(v).sort()) o[k] = sortDeep(v[k]);
    return o;
  }
  return v;
}

// charter ## repos 相当のエントリ配列から repos.json のテキストを作る。
// includeMeta=true（新規プロジェクト作成時）は `_meta.generated_from` 付き＝charter が正で本体が
// 再生成する。includeMeta=false（ビュアーのフォーム編集時）は _meta 無し＝手管理として repos.json
// が正になり本体が上書きしない（ユーザーがフォームで直した内容が保たれる）。
function exportReposJson(repos, includeMeta = true) {
  const entries = {};
  const list = Array.isArray(repos) ? repos : [];
  for (const e of list) {
    if (!e || !String(e.url || '').trim()) continue;
    const rec = {};
    for (const k of ['url', 'desc', 'base', 'target', 'path']) {
      const v = e[k] != null && String(e[k]).trim();
      if (v) rec[k] = v;
    }
    const owns = splitGlobs(e.owns);
    if (owns.length) rec.owns = owns;
    if (e.readonly && !owns.length) rec.readonly = true;
    const key = String(e.name || '').trim() || String(e.url).trim() || `repo${Object.keys(entries).length + 1}`;
    entries[key] = rec;
  }
  const payload = includeMeta
    ? {
        _meta: {
          generated_from: 'charter.md ## repos',
          note: 'kiro-project が自動生成（正は charter）。手で管理するなら _meta を消す',
        },
        ...entries,
      }
    : entries;
  return `${JSON.stringify(sortDeep(payload), null, 2)}\n`;
}

// ---------------------------------------------------------------------------
// プロジェクトの新規作成
// ---------------------------------------------------------------------------

// プロジェクトルート <root>/<name>/ に charter.md（＋ repos があれば repos.json）を作る
// （1 プロジェクト = 1 ディレクトリ。root に既存の親フォルダを指定し、その下へ作る）。
// 既存の charter.md がある場合は拒否（上書き防止）。ディレクトリは無ければ作る。
// spec.master が真ならマスター運用で作る: charter.md はマスター憲章（分解されない共通前提）になり、
// charterName（任意）を指定すると最初の計画バージョン charters/<名前>.md も併せて作る
// （goal・acceptance をやるべきこととして引き継ぐ。前提・制約はマスターから継承される）。
function createProject(spec) {
  const root = String((spec && spec.root) || '').trim();
  if (!root) throw new Error('作成先の親フォルダを指定してください');
  const name = String((spec && spec.name) || '').trim();
  if (!name || name === '.' || name === '..' || BAD_NAME_RE.test(name)) {
    throw new Error(`プロジェクト名が不正です（空白・区切り文字は不可）: ${name || '(空)'}`);
  }
  const dir = path.join(path.resolve(root), name);
  // charter 名（バージョン）指定時は charters/<charterName>.md（複数バージョン運用）に置く
  const charterName = String((spec && spec.charterName) || '').trim();
  if (charterName && (charterName === '.' || charterName === '..' || BAD_NAME_RE.test(charterName))) {
    throw new Error(`charter 名が不正です: ${charterName}`);
  }
  const master = !!(spec && spec.master);
  const charterFile = charterName && !master
    ? path.join(dir, 'charters', `${charterName}.md`)
    : path.join(dir, 'charter.md');
  if (fs.existsSync(charterFile)) {
    throw new Error(`すでに charter.md が存在します（上書きしません）: ${charterFile}`);
  }
  const repos = (Array.isArray(spec.repos) ? spec.repos : []).filter((e) => e && String(e.url || '').trim());

  fs.mkdirSync(path.dirname(charterFile), { recursive: true });
  fs.writeFileSync(charterFile, buildCharter({ ...spec, name: master ? name : charterName || name }), 'utf8');
  let versionFile = null;
  if (master && charterName) {
    versionFile = path.join(dir, 'charters', `${charterName}.md`);
    fs.mkdirSync(path.dirname(versionFile), { recursive: true });
    fs.writeFileSync(
      versionFile,
      buildCharter({ name: charterName, goal: spec.goal, deliverables: spec.deliverables,
                     acceptance: spec.acceptance }),
      'utf8'
    );
  }
  // inbox/ は外部ソース（このビュアーの投入含む）が見つけられるよう先に作っておく
  // （kiro-project 本体も起動時に作る）。
  fs.mkdirSync(path.join(dir, 'inbox'), { recursive: true });

  let reposFile = null;
  if (repos.length) {
    reposFile = path.join(dir, 'repos.json');
    fs.writeFileSync(reposFile, exportReposJson(repos), 'utf8');
  }
  return { dir, name, root: path.resolve(root), charterFile, versionFile, reposFile };
}

// ---------------------------------------------------------------------------
// 初版 charter.md のバージョン化（charters/<name>.md への昇格）
// ---------------------------------------------------------------------------

function safeList(dir) {
  try {
    return fs.readdirSync(dir);
  } catch {
    return [];
  }
}

function readJsonFile(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return null;
  }
}

// project.json のトップレベル（初版 charter.md の収束状態）が持つキー。
// kiro-project の load_charter_state / save_charter_state（name 無し＝トップレベル）と対。
const CHARTER_STATE_KEYS = [
  'id', 'name', 'history', 'best', 'stall', 'acceptance_synth', 'planned_charter_sig',
  'acceptance_total', 'status', 'cost', 'cycles', 'accepted_charter_sig',
];

// 初版（charter.md）に後からバージョン名を付け、charters/<name>.md へ移す（昇格）。
// kiro-project は charters/*.md があると charter.md を駆動対象から外すため、バージョン追加後も
// 初版を並行駆動に含めるにはこの昇格が必要になる。やること:
//   1. charter.md → charters/<name>.md へ移動（本文は無変更）
//   2. 既存の未タグ backlog タスクへ `- charter: <name>` を付与（初版のタスクの帰属を追従。
//      charter: は帰属メタデータで status ではない＝「done は verify のみが根拠」は壊さない）
//   3. project.json のトップレベル収束状態（承認済み accepted 等）を charters[<name>] へ移す
//   4. 残っている milestone カード needs/<pid>.md を needs/<pid>-<name>.md へ改名
function promoteCharterVersion(dir, name) {
  const nm = String(name || '').trim();
  if (!nm || nm === '.' || nm === '..' || BAD_NAME_RE.test(nm)) {
    throw new Error(`バージョン名が不正です（空白・区切り文字は不可）: ${nm || '(空)'}`);
  }
  const src = path.join(dir, 'charter.md');
  if (!fs.existsSync(src)) throw new Error(`charter.md がありません（昇格対象なし）: ${src}`);
  const dst = path.join(dir, 'charters', `${nm}.md`);
  if (fs.existsSync(dst)) throw new Error(`すでに存在します（上書きしません）: ${dst}`);

  fs.mkdirSync(path.dirname(dst), { recursive: true });
  fs.renameSync(src, dst);

  let tagged = 0;
  const bdir = path.join(dir, 'backlog');
  for (const f of safeList(bdir)) {
    if (!f.endsWith('.md')) continue;
    const file = path.join(bdir, f);
    let text;
    try {
      text = fs.readFileSync(file, 'utf8');
    } catch {
      continue;
    }
    if (/^-\s+charter\s*:/m.test(text)) continue; // 既に別バージョンへ帰属済み
    fs.writeFileSync(file, `${text.replace(/\n*$/, '\n')}- charter: ${nm}\n`, 'utf8');
    tagged += 1;
  }

  const pj = path.join(dir, 'project.json');
  const state = readJsonFile(pj);
  const pid = path.basename(path.resolve(dir)); // kiro-project の _project_id（ルートのディレクトリ名）と同じ
  if (state && state.id) {
    const sub = {};
    for (const k of CHARTER_STATE_KEYS) {
      if (state[k] !== undefined) {
        sub[k] = state[k];
        delete state[k];
      }
    }
    sub.id = `${pid}-${nm}`; // 複数 charter 運用の milestone/state id（<project>-<name>）
    state.charters = state.charters || {};
    if (!state.charters[nm]) state.charters[nm] = sub;
    fs.writeFileSync(pj, `${JSON.stringify(state, null, 2)}\n`, 'utf8');
  }

  const oldNeeds = path.join(dir, 'needs', `${pid}.md`);
  if (fs.existsSync(oldNeeds)) {
    try {
      fs.renameSync(oldNeeds, path.join(dir, 'needs', `${pid}-${nm}.md`));
    } catch {
      /* milestone カードの改名失敗は昇格自体の失敗にしない（次の評価で書き直される） */
    }
  }
  return { from: src, to: dst, name: nm, tagged };
}

// 未使用の計画バージョンを削除する。画面の disabled 表示だけを信用せず、削除直前に
// backlog / archive の帰属メタデータを読み直す。作業中・完了済みのどちらかに紐づく版は、
// 履歴との対応が失われるため削除しない。
function deleteCharterVersion(dir, name) {
  const nm = String(name || '').trim();
  if (!nm || nm === '.' || nm === '..' || BAD_NAME_RE.test(nm)) {
    throw new Error(`バージョン名が不正です（空白・区切り文字は不可）: ${nm || '(空)'}`);
  }
  const file = editablePath(dir, `charters/${nm}.md`);
  if (!fs.existsSync(file)) throw new Error(`計画バージョンが見つかりません: ${nm}`);

  const related = [];
  for (const folder of ['backlog', 'archive']) {
    const taskDir = path.join(dir, folder);
    for (const entry of safeList(taskDir)) {
      if (!entry.endsWith('.md')) continue;
      try {
        const text = fs.readFileSync(path.join(taskDir, entry), 'utf8');
        const m = text.match(/^\s*-\s+charter\s*:\s*(.+?)\s*$/m);
        if (m && m[1].trim() === nm) related.push(`${folder}/${entry}`);
      } catch (e) {
        throw new Error(`関連する作業を確認できないため削除できません: ${folder}/${entry} (${e.message})`);
      }
    }
  }
  if (related.length) {
    throw new Error(`関連する作業が ${related.length} 件あるため削除できません: ${nm}`);
  }
  fs.unlinkSync(file);
  return { file, name: nm };
}

// ---------------------------------------------------------------------------
// 上位入力ファイルの読み書き（編集）
// ---------------------------------------------------------------------------

// repos.json が「charter からの自動生成物」か（_meta.generated_from マーカー）。
// 生成物を直接編集しても run 時に charter から上書きされるため、UI で警告するのに使う。
function isGeneratedRepos(name, content) {
  if (name !== 'repos.json') return false;
  try {
    const data = JSON.parse(content);
    return !!(data && data._meta && data._meta.generated_from);
  } catch {
    return false;
  }
}

function readProjectFile(dir, name) {
  const file = editablePath(dir, name);
  let content = '';
  let exists = false;
  try {
    content = fs.readFileSync(file, 'utf8');
    exists = true;
  } catch {
    /* 未作成なら空で返す（新規作成扱い） */
  }
  const meta = editableMeta(name);
  return {
    name,
    file,
    exists,
    content,
    label: meta.label,
    kind: meta.kind,
    generated: isGeneratedRepos(name, content),
  };
}

function writeProjectFile(dir, name, content) {
  const file = editablePath(dir, name);
  const text = String(content == null ? '' : content);
  if ((editableMeta(name) || {}).kind === 'json' && text.trim()) {
    try {
      JSON.parse(text);
    } catch (e) {
      throw new Error(`JSON として不正です: ${e.message}`);
    }
  }
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, text.endsWith('\n') ? text : `${text}\n`, 'utf8');
  return { file, name };
}

module.exports = {
  EDITABLE_FILES,
  POLICY_KINDS,
  isEditable,
  buildCharter,
  charterReposLines,
  exportReposJson,
  createProject,
  promoteCharterVersion,
  deleteCharterVersion,
  readProjectFile,
  writeProjectFile,
  isGeneratedRepos,
  parseCharterDoc,
  charterToFields,
  fieldsToCharter,
  policyToRules,
  rulesToPolicy,
  reposJsonToRows,
};
