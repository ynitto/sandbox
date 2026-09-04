'use strict';

// 文書の置き場（ファイルシステム）の読み書き。他の制御面を知らない層。
//
//   <workspaceDir>/<id>/                 … 1 文書 = 1 フォルダ
//   <workspaceDir>/<id>/document.json    … 文書の定義（このモジュールが正典）
//   <workspaceDir>/<id>/inputs/          … 入力ファイルの写し
//   <workspaceDir>/<id>/<id>.history.md  … 改訂履歴（sidecar.js）
//   <workspaceDir>/<id>/**               … 成果物（エージェントが書く。サブフォルダも可）

const fs = require('fs');
const os = require('os');
const path = require('path');
const { sharedHomeRoot } = require('../../../base/main/agent-home');
const formats = require('./formats');
const rules = require('./rules');
const sidecar = require('./sidecar');

const MANIFEST = 'document.json';
const MANIFEST_VERSION = 1;
const INPUTS_DIR = 'inputs';
const MODES = new Set(['whole', 'section']);
const SCAN_DEPTH = 4;

function cfgOf(config) {
  return (config && config.documents) || {};
}

// 文書と文書ルールの置き場は **この端末（Windows）側のホーム**。
//
// 共有ホーム（`base/agent-home` の sharedHomeRoot。Windows では WSL 側のホームを
// UNC `\\wsl$\…` で指す）は control.json のような**エンジンと分け合う状態**の置き場で、
// ここに置くものはそれとは性質が違う——人が Word / PowerPoint / Excel / draw.io で開く
// 実ファイルであり、開く主体は Windows のアプリの方である。UNC に置くと Office は
// 保護ビュー・自動保存・最近使った項目のどれもが不安定になり、エクスプローラや
// 既定のアプリ関連付けからも辿りにくい。
//
// エージェントは WSL 側で走るので、**渡すときに WSL 表記へ直す**
// （`launcher.agentPath` — 依頼文の作業フォルダも、起動する cwd も同じ変換を通す）。
// win32 以外はホームが 1 つなので、この分岐は何も変えない。
function homeRoot() {
  return os.homedir();
}

function expandHome(p) {
  return String(p || '').replace(/^~(?=$|\/|\\)/, homeRoot());
}

// 置き場を明示していないときの既定。
// 旧既定（共有ホーム＝Windows では WSL 側）に既に中身があるならそちらを使い続ける
// ——既定を Windows 側へ移した日に、それまでの文書と文書ルールが画面から消えないように。
// 引っ越したい人は設定の「置き場」で新しいフォルダを指定する（アプリは勝手に動かさない）。
function defaultDir(name) {
  const here = path.join(homeRoot(), '.agents', name);
  if (process.platform !== 'win32' || fs.existsSync(here)) return here;
  const legacy = path.join(sharedHomeRoot(), '.agents', name);
  return legacy !== here && fs.existsSync(legacy) ? legacy : here;
}

function workspaceDir(config) {
  const raw = String(cfgOf(config).workspaceDir || '').trim();
  return raw ? path.resolve(expandHome(raw)) : defaultDir('documents');
}

function rulesDir(config) {
  const raw = String(cfgOf(config).rulesDir || '').trim();
  return raw ? path.resolve(expandHome(raw)) : defaultDir('document-rules');
}

function normalizeMode(mode) {
  return MODES.has(mode) ? mode : 'whole';
}

// ---------------------------------------------------------------------------
// 定義（document.json）
// ---------------------------------------------------------------------------

function readManifest(setDir) {
  const raw = fs.readFileSync(path.join(setDir, MANIFEST), 'utf8');
  const m = JSON.parse(raw);
  if (!m || typeof m !== 'object' || Array.isArray(m)) throw new Error('document.json が壊れています');
  return m;
}

function writeManifest(setDir, manifest) {
  const target = path.join(setDir, MANIFEST);
  const tmp = `${target}.tmp.${process.pid}`;
  fs.writeFileSync(tmp, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
  fs.renameSync(tmp, target);
}

function touchManifest(setDir, patch) {
  const next = { ...readManifest(setDir), ...patch, updatedAt: new Date().toISOString() };
  writeManifest(setDir, next);
  return next;
}

// ---------------------------------------------------------------------------
// 成果物・入力
// ---------------------------------------------------------------------------

function isTempName(name) {
  return name.endsWith('.tmp') || /\.tmp\.\d+$/.test(name);
}

// フォルダの実ファイルから成果物を数える（サブフォルダも見る。inputs/ と隠しフォルダは除く）。
// document.json の outputs はエージェントが書く補足（役割・関係）で、無くても一覧は出る
// ——記録漏れで成果物が見えなくなるのを避ける。file は文書フォルダからの相対パス（'/' 区切り）。
function scanOutputs(setDir, id, manifest) {
  const skip = new Set([MANIFEST, sidecar.sidecarName(id)]);
  const declared = new Map();
  for (const o of Array.isArray(manifest && manifest.outputs) ? manifest.outputs : []) {
    if (o && o.file) declared.set(String(o.file).replace(/\\/g, '/'), o);
  }
  const out = [];
  const walk = (dir, rel, depth) => {
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const d of entries.sort((a, b) => a.name.localeCompare(b.name, 'ja'))) {
      if (d.name.startsWith('.')) continue;
      const relName = rel ? `${rel}/${d.name}` : d.name;
      if (d.isDirectory()) {
        if (rel === '' && d.name === INPUTS_DIR) continue;
        if (depth < SCAN_DEPTH) walk(path.join(dir, d.name), relName, depth + 1);
        continue;
      }
      if (!d.isFile() || (rel === '' && skip.has(d.name)) || isTempName(d.name)) continue;
      const full = path.join(dir, d.name);
      let size = 0;
      let mtime = '';
      try {
        const st = fs.statSync(full);
        size = st.size;
        mtime = st.mtime.toISOString();
      } catch { /* 消えた直後は 0 のまま */ }
      const meta = declared.get(relName) || {};
      out.push({
        file: relName,
        path: full,
        format: formats.formatOf(d.name) || String(meta.format || ''),
        role: String(meta.role || ''),
        relatedTo: Array.isArray(meta.relatedTo) ? meta.relatedTo.map(String) : [],
        relation: String(meta.relation || ''),
        size,
        updatedAt: mtime,
      });
    }
  };
  walk(setDir, '', 0);
  return out;
}

function listInputs(setDir) {
  try {
    return fs.readdirSync(path.join(setDir, INPUTS_DIR)).filter((n) => !n.startsWith('.'))
      .map((name) => ({ name, path: path.join(setDir, INPUTS_DIR, name) }));
  } catch {
    return [];
  }
}

function copyInputs(setDir, sources) {
  const dir = path.join(setDir, INPUTS_DIR);
  fs.mkdirSync(dir, { recursive: true });
  const out = [];
  const used = new Set();
  for (const src of sources || []) {
    const from = String(src || '').trim();
    if (!from) continue;
    let st;
    try {
      st = fs.statSync(from);
    } catch {
      throw new Error(`入力ファイルが見つかりません: ${from}`);
    }
    if (!st.isFile()) throw new Error(`入力はファイルだけを指定できます: ${from}`);
    let name = path.basename(from);
    const ext = path.extname(name);
    const stem = name.slice(0, name.length - ext.length);
    let i = 2;
    while (used.has(name.toLowerCase())) {
      name = `${stem}-${i}${ext}`;
      i += 1;
    }
    used.add(name.toLowerCase());
    fs.copyFileSync(from, path.join(dir, name));
    out.push({ name, source: from });
  }
  return out;
}

// ---------------------------------------------------------------------------
// 文書の一覧・解決
// ---------------------------------------------------------------------------

function setSummary(setDir, id, manifest = readManifest(setDir)) {
  const outputs = scanOutputs(setDir, id, manifest);
  const last = outputs.map((o) => o.updatedAt).filter(Boolean).sort().pop() || '';
  return {
    id,
    dir: setDir,
    name: String(manifest.name || id),
    formats: formats.normalizeFormats(manifest.formats),
    mode: normalizeMode(manifest.mode),
    rule: manifest.rule && manifest.rule.file ? { file: manifest.rule.file, name: manifest.rule.name || '' } : null,
    createdAt: String(manifest.createdAt || ''),
    updatedAt: last || String(manifest.updatedAt || manifest.createdAt || ''),
    lastAction: manifest.lastAction || null,
    outputCount: outputs.length,
  };
}

function listSets(config) {
  const root = workspaceDir(config);
  let names;
  try {
    names = fs.readdirSync(root, { withFileTypes: true }).filter((d) => d.isDirectory()).map((d) => d.name);
  } catch {
    return [];
  }
  const out = [];
  for (const id of names) {
    const dir = path.join(root, id);
    if (!fs.existsSync(path.join(dir, MANIFEST))) continue;
    try {
      out.push(setSummary(dir, id));
    } catch { /* 壊れた定義はスキップ（OS で直す） */ }
  }
  return out.sort((a, b) => String(b.updatedAt).localeCompare(String(a.updatedAt)));
}

function resolveSet(config, id) {
  const key = String(id || '').trim();
  if (!key || key === '.' || key === '..' || /[\\/]/.test(key)) throw new Error('文書の識別子が不正です');
  const dir = path.join(workspaceDir(config), key);
  if (!fs.existsSync(path.join(dir, MANIFEST))) throw new Error(`文書が見つかりません: ${key}`);
  return { id: key, dir };
}

// 空いている文書 id（同名があれば -2, -3 …）。
function availableSetId(root, name) {
  const slug = rules.slugify(name) || 'document';
  let id = slug;
  let i = 2;
  while (fs.existsSync(path.join(root, id))) {
    id = `${slug}-${i}`;
    i += 1;
  }
  return id;
}

// 新しい文書フォルダを作り、定義と入力の写しを置く。
function createSet(config, { name, formats: formatIds, mode, rule, request, inputs }) {
  const root = workspaceDir(config);
  fs.mkdirSync(root, { recursive: true });
  const id = availableSetId(root, name);
  const dir = path.join(root, id);
  fs.mkdirSync(dir, { recursive: true });
  const copied = copyInputs(dir, inputs);
  const now = new Date().toISOString();
  const manifest = {
    version: MANIFEST_VERSION,
    id,
    name,
    formats: formatIds,
    mode: normalizeMode(mode),
    rule: rule ? { file: path.basename(rule.file), name: rule.name } : null,
    request,
    inputs: copied,
    outputs: [],
    createdAt: now,
    updatedAt: now,
    lastAction: { kind: 'create', at: now },
  };
  writeManifest(dir, manifest);
  return { id, dir, manifest };
}

module.exports = {
  MANIFEST,
  homeRoot,
  defaultDir,
  MANIFEST_VERSION,
  INPUTS_DIR,
  MODES,
  workspaceDir,
  rulesDir,
  normalizeMode,
  readManifest,
  writeManifest,
  touchManifest,
  scanOutputs,
  listInputs,
  copyInputs,
  setSummary,
  listSets,
  resolveSet,
  availableSetId,
  createSet,
};
