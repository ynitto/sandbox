'use strict';

// 人のアクション層。agent-project の公式な入力契約だけを使う:
//   1. needs/<id>.md の「## Decision Outcome」への記入 + `- [x]`
//      → ingest_feedback が取り込む（フィードバック往復の正規ルート）
//   2. inbox/<name>.json のドロップ（E4 push 型の取り込み口）
//      → ingest_inbox が backlog 化する（タスク投入の正規ルート）
//   3. commands/<name>.json のドロップ（approve / hold / pin / defer / revise）
//      → ingest_commands が CLI と同一ロジック・同一の決定記録（DR）で実行する。
//      revise は人の即時フィードバック: タスクの内容・依存（after）・優先度の修正と
//      feedback（次の act に必ず届く指示）を、ループがブロックする前に能動的に届ける。
//      ファイルだけで届くため、本体が WSL 内で稼働していても操作できる。実行エンジンが
//      止まっていれば取り込み待ちのまま残る（サイレントに失敗しない）。
// done の確定・状態遷移そのものをこのアプリが直接書き換えることはしない
// （「done は verify のみが根拠」の不変条件を壊さない）。

const fs = require('fs');
const path = require('path');
const project = require('./project');

const DECISION_MARKER = '## Decision Outcome';

// ---------------------------------------------------------------------------
// 1. needs へのフィードバック（Decision Outcome 記入 + [x] 確定）
// ---------------------------------------------------------------------------

// 合成票（needs ファイル欠落）向けの最小 MADR。本体 write_needs_file / ensure_needs と同系統。
function buildNeedsStub(stub) {
  const id = String((stub && stub.id) || '').trim();
  if (!id) throw new Error('needs スタブには id が必要です');
  const kind = String((stub && stub.kind) || 'blocked');
  const title = String((stub && stub.title) || id);
  const why = String((stub && stub.why) || '要対応');
  const date = new Date().toISOString().slice(0, 10);
  const heading =
    kind === 'plan-review' ? `実行前レビュー: ${title}` : `要対応: ${title}`;
  return (
    '---\n' +
    'status: proposed\n' +
    `date: ${date}\n` +
    'decision-makers: [human]\n' +
    `task-id: ${id}\n` +
    `kind: ${kind}\n` +
    '---\n\n' +
    `# ${heading}\n\n` +
    '## Context and Problem Statement\n\n' +
    `- なぜ: ${why}\n\n` +
    `${DECISION_MARKER}\n\n` +
    '<!-- 人の決定の記入欄。方針・指示をここに書く。 -->\n' +
    '- [ ] 確定（このボックスを [x] にして保存すると取り込みます）\n'
  );
}

function submitFeedback(needsFile, feedback, stub) {
  if (!fs.existsSync(needsFile)) {
    // 合成票（status 投影）でファイルが無いときは、差し戻し／再実行の正規ルート用にスタブを起こす。
    // 承認（approve）は commands/ 経由なのでファイル不要だが、feedback は needs への [x] が契約。
    if (!stub || !stub.id) {
      throw new Error(`needs ファイルがありません（取り込み済みの可能性）: ${needsFile}`);
    }
    fs.mkdirSync(path.dirname(needsFile), { recursive: true });
    fs.writeFileSync(needsFile, buildNeedsStub(stub), 'utf8');
  }
  let text = fs.readFileSync(needsFile, 'utf8');
  if (!text.includes(DECISION_MARKER)) {
    text = `${text.replace(/\n*$/, '\n\n')}${DECISION_MARKER}\n\n`;
  }
  const fb = String(feedback || '').trim();
  if (fb) {
    // マーカー直後に記入（read_feedback はコメントとチェックボックス行を
    // 除いたマーカー以降すべてを人の記入として読む）
    text = text.replace(DECISION_MARKER, `${DECISION_MARKER}\n\n${fb}\n`);
  }
  // 確定 [x] は Decision Outcome 配下だけを触る（本文チェックリストを潰さない）
  const outcomeIdx = text.search(/^##\s+Decision Outcome\s*$/m);
  if (outcomeIdx >= 0) {
    const head = text.slice(0, outcomeIdx);
    let tail = text.slice(outcomeIdx);
    if (/^\s*-\s*\[x\]/im.test(tail)) {
      // すでに確定済み
    } else if (/-\s*\[ \]/.test(tail)) {
      tail = tail.replace(/-\s*\[ \]/, '- [x]');
    } else {
      tail = `${tail.replace(/\n*$/, '\n')}\n- [x] 確定\n`;
    }
    text = head + tail;
  } else if (/^\s*-\s*\[x\]/im.test(text)) {
    // マーカー無しのレガシー票: 既存 [x] があれば触らない
  } else if (/-\s*\[ \]/.test(text)) {
    text = text.replace(/-\s*\[ \]/, '- [x]');
  } else {
    text = `${text.replace(/\n*$/, '\n')}\n- [x] 確定\n`;
  }
  fs.writeFileSync(needsFile, text, 'utf8');
  return { file: needsFile, feedback: fb };
}

// ---------------------------------------------------------------------------
// 2. タスク投入（inbox/*.json ドロップ）
// ---------------------------------------------------------------------------

function slugify(s) {
  const t = String(s || '')
    .toLowerCase()
    .replace(/[^a-z0-9぀-ヿ一-鿿_-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40);
  return t || 'task';
}

function validateCharterVersion(projectDir, value) {
  const name = String(value || '').trim();
  if (!name) return '';
  if (name === '.' || name === '..' || path.basename(name) !== name || /[\\/\x00-\x1f]/.test(name)) {
    throw new Error(`計画バージョン名が不正です: ${name}`);
  }
  const file = path.join(projectDir, 'charters', `${name}.md`);
  if (!fs.existsSync(file)) throw new Error(`計画バージョン「${name}」が見つかりません`);
  return name;
}

function enqueueToInbox(projectDir, spec) {
  const title = String(spec.title || '').trim();
  if (!title) throw new Error('タイトルは必須です');
  const clean = { title };
  const charter = validateCharterVersion(projectDir, spec.charter);
  if (charter) clean.charter = charter;
  // task.schema.json の人が書けるフィールド（system 管理の routed_by/cohort* は通さない）。
  // workspace/refs/paths はルーティング（書込先・モノレポの担当領域）の明示指定、
  // review/expect/followup は検収・偽 done 対策・派生タスクの契約。
  for (const key of ['id', 'verify', 'accept', 'verify_template', 'note', 'after', 'level', 'track',
    'node', 'why', 'desc', 'scope', 'out_of_scope', 'constraints', 'hints', 'demo',
    'workspace', 'refs', 'paths', 'review', 'expect', 'followup']) {
    const v = spec[key];
    if (v === undefined || v === null) continue;
    const s = Array.isArray(v) ? v.map((x) => String(x).trim()).filter(Boolean).join(', ') : String(v).trim();
    if (s !== '') clean[key] = s;
  }
  const pr = parseInt(spec.priority, 10);
  if (!isNaN(pr) && pr !== 0) clean.priority = pr;

  const inbox = path.join(projectDir, 'inbox');
  fs.mkdirSync(inbox, { recursive: true });
  const file = path.join(inbox, `viewer-${slugify(title)}-${Date.now()}.json`);
  fs.writeFileSync(file, JSON.stringify(clean, null, 2), 'utf8');
  return { file, spec: clean };
}

// ---------------------------------------------------------------------------
// 監視担当の割り当て（assignments.json）— viewer 管理のチーム運用メタデータ。
// タスク状態ファイル（backlog/*.md）には触れない: agent-project の契約の外にある
// サイドカーファイルへの書き込みなので、done の不変条件・状態遷移に影響しない。
// プロジェクトルート直下に置くため state_git 同期でチームへ共有される。
// 書きかけを同期に載せないよう .tmp に書いてから rename する。
// ---------------------------------------------------------------------------

function setTaskOwner(projectDir, id, owner) {
  const tid = String(id || '').trim();
  if (!tid || tid !== path.basename(tid)) throw new Error(`不正なタスク ID です: ${id}`);
  const name = String(owner || '').trim().slice(0, 60);
  const file = path.join(projectDir, project.ASSIGNMENTS_FILE);
  const cur = project.readAssignments(projectDir);
  if (name) {
    cur.tasks[tid] = name;
    if (!cur.members.includes(name)) cur.members.push(name);
  } else {
    delete cur.tasks[tid];
  }
  fs.mkdirSync(projectDir, { recursive: true });
  fs.writeFileSync(`${file}.tmp`, `${JSON.stringify(cur, null, 2)}\n`, 'utf8');
  fs.renameSync(`${file}.tmp`, file);
  return { file, id: tid, owner: name };
}

// ---------------------------------------------------------------------------
// レビューコメント（reviews/<task-id>/*.json）— 成果物レビューを複数メンバーで行う。
// 1 コメント = 1 ファイル（同時投稿がファイル名衝突せず state_git でマージされる）。
// viewer 管理のサイドカーなのでタスク状態・done の不変条件には触れない。
// 書きかけを watch/同期に載せないよう .tmp に書いてから rename する。
// ---------------------------------------------------------------------------

function _reviewDir(projectDir, taskId) {
  const tid = String(taskId || '').trim();
  if (!tid || tid !== path.basename(tid)) throw new Error(`不正なタスク ID です: ${taskId}`);
  return path.join(projectDir, project.REVIEWS_DIR, tid);
}

function _commentFile(dir, commentId) {
  const cid = String(commentId || '').trim();
  if (!cid || cid !== path.basename(cid) || /[\\/]/.test(cid)) {
    throw new Error(`不正なコメント ID です: ${commentId}`);
  }
  return path.join(dir, `${cid}.json`);
}

function addReviewComment(projectDir, taskId, { author, text }) {
  const body = String(text || '').trim();
  if (!body) throw new Error('コメント本文が空です');
  const dir = _reviewDir(projectDir, taskId);
  fs.mkdirSync(dir, { recursive: true });
  const ts = new Date().toISOString();
  // ファイル名に ts を含めて時系列に並べやすくする（同ミリ秒の衝突は slug で分ける）。
  const cid = `${ts.replace(/[:.]/g, '-')}-${slugify(author || 'anon')}`;
  const rec = { author: String(author || '').trim().slice(0, 60) || '匿名', text: body, ts };
  const file = path.join(dir, `${cid}.json`);
  fs.writeFileSync(`${file}.tmp`, `${JSON.stringify(rec, null, 2)}\n`, 'utf8');
  fs.renameSync(`${file}.tmp`, file);
  return { file, id: cid, comment: { id: cid, ...rec } };
}

// 監視担当者がコメントを整理（編集）する。author/ts は保持し editedTs を足す。
function editReviewComment(projectDir, taskId, commentId, text) {
  const body = String(text || '').trim();
  if (!body) throw new Error('コメント本文が空です');
  const dir = _reviewDir(projectDir, taskId);
  const file = _commentFile(dir, commentId);
  const cur = project.readReviewComments(projectDir, taskId).find((c) => c.id === String(commentId));
  if (!cur) throw new Error(`コメントが見つかりません: ${commentId}`);
  const rec = { author: cur.author, text: body, ts: cur.ts, editedTs: new Date().toISOString() };
  fs.writeFileSync(`${file}.tmp`, `${JSON.stringify(rec, null, 2)}\n`, 'utf8');
  fs.renameSync(`${file}.tmp`, file);
  return { file, id: String(commentId), comment: { id: String(commentId), ...rec } };
}

async function deleteReviewComment(projectDir, taskId, commentId, trash) {
  const dir = _reviewDir(projectDir, taskId);
  const file = _commentFile(dir, commentId);
  if (!fs.existsSync(file)) throw new Error(`コメントが見つかりません: ${commentId}`);
  const via = trash ? await trash(file) : (fs.rmSync(file, { force: true }), 'delete');
  return { file, id: String(commentId), via };
}

// ---------------------------------------------------------------------------
// 3. 人の指示（approve / hold / pin / defer / revise）
// ---------------------------------------------------------------------------

const COMMAND_ACTIONS = new Set(['approve', 'hold', 'pin', 'defer', 'revise', 'reject', 'resume-run']);
// プロジェクト単位（id 不要）のライフサイクル指示。リモートの本体を git 越しに操作する口。
const LIFECYCLE_ACTIONS = new Set(['pause', 'resume', 'stop']);

// revise が受けるフィールド編集キー（agent-project の REVISE_FIELDS と同じ）。
// 値は「置換」規約: '' / '-' / 'none' はフィールド削除、未指定（undefined/null）は触らない。
// why 以降は誘導・レビュー記述（backlog.md.example 参照）。
const REVISE_KEYS = ['title', 'priority', 'verify', 'accept', 'after', 'note', 'level', 'track',
  'node', 'why', 'desc', 'scope', 'out_of_scope', 'constraints', 'hints', 'demo'];

// 複数行フィールド（agent-project の MULTILINE_KEYS）。配列で送り、**全行を置換**する。
// 単値キーと同じ String() を通すと ['a','b'] が "a,b" の 1 行に潰れるので経路を分ける。
const REVISE_LIST_KEYS = ['acceptance'];

// revise ペイロード（フィールド編集 + feedback）を commands/CLI 両経路の形へ正規化する。
// undefined/null は「触らない」の意味なので落とす（'' は削除の明示指定として残す）
function revisePayload({ fields, feedback }) {
  const out = {};
  for (const key of REVISE_KEYS) {
    const v = fields && fields[key];
    if (v !== undefined && v !== null) out[key] = String(v);
  }
  for (const key of REVISE_LIST_KEYS) {
    const v = fields && fields[key];
    if (v === undefined || v === null) continue;
    out[key] = (Array.isArray(v) ? v : [v]).map((x) => String(x));
  }
  const fb = String(feedback || '').trim();
  if (fb) out.feedback = fb;
  return out;
}

// commands/<name>.json のドロップ（agent-project の ingest_commands が拾う）。
// 書きかけを watch に読ませないよう .tmp に書いてから rename する。
// replan / pause / resume / stop / heal はプロジェクト単位（id 不要）なので id を載せない。
function dropCommand(projectDir, { action, id, reason, fields, feedback, run, charter, title, complete }) {
  const dir = path.join(projectDir, 'commands');
  fs.mkdirSync(dir, { recursive: true });
  const projectScoped =
    action === 'replan' || action === 'heal' || action === 'distill-notes' ||
    action === 'revive' || LIFECYCLE_ACTIONS.has(action);
  const rec = {
    command: action,
    ...(projectScoped ? {} : { id: String(id) }),
    reason: String(reason || ''),
    actor: 'agent-dashboard',
    ts: new Date().toISOString(),
    ...(action === 'revise' ? revisePayload({ fields, feedback }) : {}),
    ...(action === 'resume-run' && run ? { run: String(run) } : {}),
    ...((action === 'replan' || action === 'distill-notes' || action === 'revive') && charter
      ? { charter: String(charter) }
      : {}),
    // revive は id ではなくタイトル（指紋照合）で対象を指す
    ...(action === 'revive' && title ? { title: String(title) } : {}),
    // 承認 = 完了か積み直しかは呼び出し側が明示する（本体は文面から推定しない）
    ...(action === 'approve' && complete ? { complete: true } : {}),
  };
  const slug = projectScoped ? 'project' : slugify(id);
  const file = path.join(dir, `viewer-${action}-${slug}-${Date.now()}.json`);
  fs.writeFileSync(`${file}.tmp`, JSON.stringify(rec, null, 2), 'utf8');
  fs.renameSync(`${file}.tmp`, file);
  return { file, rec };
}

// 本体（agent-project）を CLI で起こす経路は削除した（実装計画 W2-2）。CLI 引数の組み立て・
// プロセスツリーの kill・設定ファイルの探索もその経路のためだけの道具だったので一緒に消す。
// 人の操作は commands/ の投函だけで届き、実行エンジンの起動・再起動は OS の起動系が担う。

// action: approve | hold | pin | defer | revise
//   revise は fields（title/priority/verify/accept/after/note/level/track の置換）と
//   feedback（次の act に必ず届く指示）を追加で受ける。実行中（doing）のタスクは
//   本体側が現在の試行を確定せず修正内容で積み直す（早い軌道修正）。
// 経路は commands/ ドロップの一本（案2後半）。以前は actionMode(auto/file/cli) で CLI 直接実行と
// サイレントフォールバックに分岐していたが、CLI パス誤り時に「押しても何も起きない」原因不明の
// 停滞を生んでいた。稼働中の本体（同一 PC の WSL・別ホスト問わず）が git 同期越しに ingest し、
// 受理レシート（commands/processed/）でカードに「受理済み」を返す。停止中は取り込み待ちのまま
// 残り、送信済み表示で「エンジン待ち」が見える（サイレントに失敗しない）。
async function runAction(cfg, { dir, action, id, reason, fields, feedback, run, complete }) {
  if (!COMMAND_ACTIONS.has(action)) throw new Error(`不明なアクション: ${action}`);
  const why = String(reason || '').trim() || 'agent-dashboard から操作';
  if (action === 'revise' && Object.keys(revisePayload({ fields, feedback })).length === 0) {
    throw new Error('revise には変更フィールドかフィードバックの指定が必要です');
  }
  if (action === 'resume-run' && !String(run || '').trim()) {
    throw new Error('resume-run には再開する run-id の指定が必要です');
  }
  const { file } = dropCommand(dir, { action, id, reason: why, fields, feedback, run, complete });
  return {
    output: `${action} ${id}: 指示ファイルを投入しました（稼働中の agent-project が取り込み、受理後にカードへ反映されます）`,
    file,
    via: 'file',
  };
}

// charter からのバックログ再分解を要求する（エラー回復用の一発の口。プロジェクト単位＝id 無し）。
// 本体は次パスで charter を分解し直す。冪等照合は「done 以外」（処理中＋却下済み）と行う＝
// 処理中タスクの二重投入や却下済みの復活はせず、done と類似のタスクだけやり直しとして再作成される。
// 経路は runAction と同じく commands/replan ドロップの一本（案2後半）。稼働中の本体が次パスで
// 取り込み、受理レシートでカードへ反映する。停止中は取り込み待ちのまま残る（サイレント失敗しない）。
async function requestReplan(cfg, { dir, reason, charter }) {
  const why = String(reason || '').trim() || 'agent-dashboard から再分解を要求';
  const charterName = validateCharterVersion(dir, charter);
  const { file } = dropCommand(dir, { action: 'replan', reason: why, charter: charterName });
  return {
    output: 'charter からの再分解を要求しました（稼働中の agent-project が次パスで取り込みます）',
    file,
    via: 'file',
  };
}

// ---------------------------------------------------------------------------
// 観点メモ（notes/）— 人が気になったことを書き溜める場所（S6-7）。
// plan はメモを**自動では消費しない**ので、書いたままにしておいても計画は動かない。
// 人が「メモを分解」を押したときだけ、本体（agent-project distill-notes）がバックログ候補を起こす。
// ---------------------------------------------------------------------------
const NOTE_NAME_RE = /^[A-Za-z0-9_.-]+$/;

function notesDir(dir) {
  return path.join(dir, 'notes');
}

// メモ一覧（archive/ は除く＝消費済みは出さない）。新しい順。
function listNotes(dir) {
  const d = notesDir(dir);
  let names = [];
  try {
    names = fs.readdirSync(d).filter((n) => n.toLowerCase().endsWith('.md'));
  } catch {
    return [];
  }
  const out = [];
  for (const name of names) {
    const p = path.join(d, name);
    try {
      const st = fs.statSync(p);
      if (!st.isFile()) continue;
      out.push({ name, mtime: st.mtimeMs, body: fs.readFileSync(p, 'utf8') });
    } catch {
      /* 読めないメモは黙って飛ばす（一覧を壊さない） */
    }
  }
  return out.sort((a, b) => b.mtime - a.mtime);
}

// メモを 1 件書く。**git には触らない**（この機能の書き込みはすべて状態ルート配下のファイルで、
// commit/push は本体の状態同期が行う＝no-git-writes の約束を守る）。
function writeNote(dir, { name, body }) {
  const text = String(body || '').trim();
  if (!text) throw new Error('メモが空です');
  const d = notesDir(dir);
  fs.mkdirSync(d, { recursive: true });
  let base = String(name || '').trim().replace(/\.md$/i, '');
  if (!base || !NOTE_NAME_RE.test(base)) {
    base = `note-${new Date().toISOString().replace(/[^0-9]/g, '').slice(0, 14)}`;
  }
  let file = path.join(d, `${base}.md`);
  for (let i = 2; fs.existsSync(file); i += 1) file = path.join(d, `${base}-${i}.md`);
  fs.writeFileSync(`${file}.tmp`, `${text}\n`, 'utf8');
  fs.renameSync(`${file}.tmp`, file);
  return { file, name: path.basename(file) };
}

// メモをバックログ候補へ分解するよう本体へ依頼する（commands/ ドロップの一本。replan と同じ流儀）。
async function requestDistillNotes(cfg, { dir, charter }) {
  const notes = listNotes(dir);
  if (!notes.length) throw new Error('分解できるメモがありません（先にメモを書いてください）');
  const charterName = validateCharterVersion(dir, charter);
  const { file } = dropCommand(dir, {
    action: 'distill-notes',
    reason: `agent-dashboard からメモ ${notes.length} 件の分解を要求`,
    charter: charterName,
  });
  return {
    output: `メモ ${notes.length} 件の分解を要求しました（稼働中の agent-project が次パスで取り込みます）`,
    file,
    via: 'file',
  };
}

// プロジェクト単位のライフサイクル操作（pause / resume / stop）。
// 常に commands/ ドロップ（＋git push）で届ける — リモート本体（WSL・別ホスト）の watch が
// 同期間隔内に取り込む契約（agent-project の ingest_commands）。CLI は使わない
// （stop の CLI は同一ホスト限定で、この口の主用途はリモート操作のため）。
const LIFECYCLE_LABELS = { pause: '一時停止', resume: '再開', stop: '停止' };

// 不要なバックログタスクの削除 ＝ 本体の **却下（reject）** へ委ねる。
//
// 以前は viewer が backlog/<id>.md をゴミ箱へ移すだけだった（「削除の公式契約が無い」ため）。
// だがタスクファイルは状態のごく一部で、生の削除では次の 3 つが起きる:
//   1. 要対応カード（needs/<id>.md）が残る。しかも対応タスクが無い票は本体の ingest_feedback が
//      読み飛ばすので、[x] を付けても消えない袋小路になる。
//   2. 墓標（tombstones.md）が残らないので、charter 運用では次の再分解が同じタスクを作り直す
//      ＝人には「消しても復活する」としか見えない。
//   3. 状態 git の同時変更裁定では backlog は実行側（本体）が正なので、本体側に書き込みが
//      あると viewer 側の削除が取り消されうる（別 PC 運用で消えたはずのタスクが戻る）。
// reject は archive への退避・needs の掃除・claim 解放・flow の切り離し・墓標・決定記録を
// 1 つの操作として本体のプロセス内で行う＝上の 3 つが構造的に起きない。
// 取り消しは墓標の解除（requestRevive）。
function requestDeleteTask(cfg, { dir, id, reason }) {
  const tid = String(id || '').trim();
  if (!tid || tid !== path.basename(tid)) throw new Error(`不正なタスク ID です: ${id}`);
  const file = path.join(dir, 'backlog', `${tid}.md`);
  if (!fs.existsSync(file)) throw new Error(`タスクファイルがありません: ${file}`);
  // 実行中の拒否は本体（cmd_reject）にもあるが、押した瞬間に理由を返せるようここでも見る
  const status = project.parseTask(fs.readFileSync(file, 'utf8'), tid).status;
  if (status === 'doing') {
    throw new Error(`${tid} は実行中（doing）のため削除できません（先に「修正して再実行」で止めてください）`);
  }
  return runAction(cfg, {
    dir,
    action: 'reject',
    id: tid,
    reason: String(reason || '').trim() || 'agent-dashboard から削除（不要と判断）',
  });
}

// 墓標の解除（却下＝削除の取り消し）。プロジェクト単位（id ではなく title 指定）の
// commands/ ドロップ。本体の ingest_commands が cmd_revive へ渡す。
function requestRevive(cfg, { dir, title, charter }) {
  const name = String(title || '').trim();
  if (!name) throw new Error('解除する墓標のタイトルが指定されていません');
  const { file } = dropCommand(dir, {
    action: 'revive',
    reason: 'agent-dashboard から墓標を解除',
    title: name,
    charter: String(charter || '').trim() || undefined,
  });
  return {
    output:
      `「${name}」の墓標を解除するよう要求しました` +
      '（稼働中の agent-project が取り込むと、同じタスクを入れ直せるようになります）',
    file,
    via: 'file',
  };
}

function requestLifecycle(cfg, { dir, action, reason }) {
  if (!LIFECYCLE_ACTIONS.has(action)) throw new Error(`不明なライフサイクル操作: ${action}`);
  const why = String(reason || '').trim() || 'agent-dashboard から操作';
  const { file } = dropCommand(dir, { action, reason: why });
  return {
    output:
      `${LIFECYCLE_LABELS[action]}を要求しました` +
      '（稼働中の agent-project が同期間隔内に取り込みます）',
    file,
    via: 'file',
  };
}

module.exports = {
  submitFeedback,
  buildNeedsStub,
  enqueueToInbox,
  setTaskOwner,
  addReviewComment,
  editReviewComment,
  deleteReviewComment,
  validateCharterVersion,
  dropCommand,
  runAction,
  requestReplan,
  requestDeleteTask,
  requestRevive,
  requestLifecycle,
  listNotes,
  writeNote,
  requestDistillNotes,
  DECISION_MARKER,
};
