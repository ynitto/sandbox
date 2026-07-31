'use strict';

// 納品棚（`<home>/deliveries/<mid>/`）の読み取り専用ビュー。
//
// accept が成立すると agent-amigos の owner デーモンが deliverable を納品棚へ搬出し、
// 納品書 `delivery.json`（正典: schemas/delivery.schema.json）を書く。dashboard は
// それを読むだけで、搬出も削除も行わない（書き手は owner デーモン 1 つに保つ）。
//
// 受入待ちのプレビュー（バスの deliverable/）は missions.js の readDeliverablePreview が
// 担う。ここは「受け取った後」の一覧。

const fs = require('fs');
const path = require('path');

const preview = require('./preview');

function readJson(p) {
  try {
    return JSON.parse(fs.readFileSync(p, 'utf8'));
  } catch {
    return null;
  }
}

function deliveriesDir(homeDir) {
  return path.join(homeDir, 'deliveries');
}

// ホーム 1 つ分の納品書を読む（新しい順）。
function listForHome(homeDir) {
  const base = deliveriesDir(homeDir);
  let names;
  try {
    names = fs.readdirSync(base, { withFileTypes: true })
      .filter((e) => e.isDirectory())
      .map((e) => e.name);
  } catch {
    return [];
  }
  const out = [];
  for (const mid of names) {
    const rec = readJson(path.join(base, mid, 'delivery.json'));
    if (!rec) continue;
    const files = Array.isArray(rec.files) ? rec.files : [];
    out.push({
      mission: String(rec.mission || mid),
      title: String(rec.title || ''),
      goal: String(rec.goal || ''),
      acceptedAt: String(rec.accepted_at || ''),
      acceptedBy: String(rec.accepted_by || ''),
      acceptance: String(rec.acceptance || ''),
      partial: !!rec.partial,
      partialReason: String(rec.partial_reason || ''),
      executionSeconds: Number(rec.execution_seconds) || 0,
      dir: path.join(base, mid),
      home: homeDir,
      code: rec.code && typeof rec.code === 'object' ? rec.code : null,
      files: files.map((f) => ({
        path: String(f.path || ''),
        role: String(f.role || ''),
        bytes: Number(f.bytes) || 0,
        exported: f.exported !== false,
        skipReason: String(f.skip_reason || ''),
      })),
    });
  }
  return out;
}

// 発見済みホーム全部の納品を新しい順に集める。
function list(homeList) {
  const out = [];
  for (const h of homeList || []) {
    if (h && h.dir) out.push(...listForHome(h.dir));
  }
  return out.sort((a, b) => String(b.acceptedAt).localeCompare(String(a.acceptedAt)));
}

// 受け取り済み成果物の中身。一覧のポーリングで毎回全文・画像を運ばないよう、
// ミッション詳細を開いたときにこの 1 件だけを読む（受入プレビューと同じ読み方）。
function readContents(homeDir, missionId) {
  const dir = path.join(deliveriesDir(homeDir), String(missionId || ''));
  const rec = readJson(path.join(dir, 'delivery.json'));
  if (!rec) throw new Error(`納品が見つかりません: ${missionId}`);
  return { dir, ...preview.readPreview(dir, ['delivery.json']) };
}

function pathInside(root, target) {
  const rel = path.relative(path.resolve(root), path.resolve(target));
  return rel === '' || (!rel.startsWith(`..${path.sep}`) && rel !== '..' && !path.isAbsolute(rel));
}

function safeFolderName(title, missionId) {
  const fallback = String(missionId || 'mission');
  const cleaned = String(title || fallback)
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, '-')
    .replace(/[. ]+$/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 72) || fallback;
  const id = String(missionId || '').replace(/[^\w.-]+/g, '-').slice(0, 40);
  return id && !cleaned.includes(id) ? `${cleaned}-${id}` : cleaned;
}

function uniqueTarget(parentDir, baseName) {
  let target = path.join(parentDir, baseName);
  for (let n = 2; fs.existsSync(target); n += 1) target = path.join(parentDir, `${baseName}-${n}`);
  return target;
}

// 納品棚を原本として保ち、納品書に実体ありと記録されたファイルだけを別フォルダへコピーする。
// 一時フォルダを同じ親に作り、全コピー成功後に rename することで半端な成果フォルダを残さない。
function copyToFolder(homeDir, missionId, parentDir) {
  const source = path.join(deliveriesDir(homeDir), String(missionId || ''));
  const rec = readJson(path.join(source, 'delivery.json'));
  if (!rec) throw new Error(`納品が見つかりません: ${missionId}`);
  const requestedParent = String(parentDir || '').trim();
  if (!requestedParent) throw new Error('保存先フォルダが指定されていません');
  let parent;
  try {
    parent = fs.realpathSync(requestedParent);
  } catch {
    throw new Error('保存先フォルダが見つかりません');
  }
  if (!fs.statSync(parent).isDirectory()) throw new Error('保存先はフォルダではありません');
  const realSource = fs.realpathSync(source);
  if (pathInside(realSource, parent)) {
    throw new Error('納品棚自身またはその配下にはコピーできません');
  }

  const baseName = safeFolderName(rec.title, rec.mission || missionId);
  const target = uniqueTarget(parent, baseName);
  const temp = path.join(parent, `.${baseName}.copying-${process.pid}-${Date.now()}`);
  const files = Array.isArray(rec.files) ? rec.files : [];
  const exported = files.filter((file) => file && file.exported !== false);
  let copied = 0;
  let missing = 0;
  fs.mkdirSync(temp, { recursive: false });
  try {
    for (const file of exported) {
      const rel = String(file.path || '');
      const from = path.resolve(source, rel);
      if (!rel || !pathInside(source, from) || from === source) {
        throw new Error(`不正な成果物パスです: ${rel || '(空)'}`);
      }
      let stat;
      try {
        stat = fs.lstatSync(from);
      } catch {
        missing += 1;
        continue;
      }
      if (!stat.isFile() || stat.isSymbolicLink()) {
        throw new Error(`通常ファイルではない成果物はコピーできません: ${rel}`);
      }
      const realFrom = fs.realpathSync(from);
      if (!pathInside(realSource, realFrom)) {
        throw new Error(`納品棚の外を指す成果物はコピーできません: ${rel}`);
      }
      const to = path.resolve(temp, rel);
      if (!pathInside(temp, to)) throw new Error(`不正なコピー先パスです: ${rel}`);
      fs.mkdirSync(path.dirname(to), { recursive: true });
      fs.copyFileSync(from, to, fs.constants.COPYFILE_EXCL);
      copied += 1;
    }
    fs.renameSync(temp, target);
  } catch (err) {
    fs.rmSync(temp, { recursive: true, force: true });
    throw err;
  }
  return {
    target,
    copied,
    skipped: files.filter((file) => file && file.exported === false).length,
    missing,
  };
}

module.exports = {
  list,
  listForHome,
  deliveriesDir,
  readContents,
  copyToFolder,
  safeFolderName,
  pathInside,
};
