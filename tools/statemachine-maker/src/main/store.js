'use strict';

// `.statemachine/<識別名>/` の読み書き。フォルダ 1 つ＝ステートマシン 1 つ。
//   list   … ルート直下の .statemachine/*/workflow.yaml を列挙する
//   read   … workflow.yaml / actions/*.md / conditions/*.md / maker.json を読み、工程列へ起こす
//   write  … 工程列をコンパイルして書く（LF・UTF-8・`/` 区切り。OS に依らない定義）
// 画面から届いたパスで任意の場所を触らない: 書き先は「ルート + .statemachine + 識別名」から
// ここが組み立て、識別名の字種は model が検査する。

const fs = require('fs');
const path = require('path');
const model = require('./model');

const DIR = '.statemachine';

function machineDir(root, machine) {
  if (!root) throw new Error('フォルダを選んでください');
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(machine) || machine === '.' || machine === '..') {
    throw new Error(`識別名が不正です: ${machine}`);
  }
  return path.join(root, DIR, machine);
}

function readText(file) {
  try { return fs.readFileSync(file, 'utf8'); } catch { return null; }
}

function stat(file) {
  try { return fs.statSync(file); } catch { return null; }
}

// ルート直下の定義を列挙する。名前と説明は YAML の先頭だけを軽く読む（全文の検証はしない）。
function list(root) {
  const base = path.join(root, DIR);
  const st = stat(base);
  if (!st || !st.isDirectory()) return [];
  const out = [];
  for (const name of fs.readdirSync(base).sort()) {
    const wf = path.join(base, name, 'workflow.yaml');
    const s = stat(wf);
    if (!s || !s.isFile()) continue;
    const head = String(readText(wf) || '').split(/\r?\n/).slice(0, 40);
    const pick = (key) => {
      const line = head.find((l) => new RegExp(`^${key}:\\s*`).test(l));
      return line ? line.replace(new RegExp(`^${key}:\\s*`), '').replace(/^["']|["']$/g, '').trim() : '';
    };
    out.push({
      machine: name,
      dir: path.join(base, name),
      name: pick('name') || name,
      description: pick('description'),
      maker: !!stat(path.join(base, name, 'maker.json')),
      updatedAt: s.mtimeMs,
    });
  }
  return out;
}

// フォルダの中身を相対パス（`/` 区切り）→ 本文で集める。actions / conditions の md だけ。
function collectFiles(dir) {
  const files = {};
  for (const sub of ['actions', 'conditions']) {
    const d = path.join(dir, sub);
    const st = stat(d);
    if (!st || !st.isDirectory()) continue;
    for (const f of fs.readdirSync(d)) {
      if (!f.endsWith('.md')) continue;
      const body = readText(path.join(d, f));
      if (body != null) files[`${sub}/${f}`] = body;
    }
  }
  return files;
}

function read(root, machine) {
  const dir = machineDir(root, machine);
  const workflowText = readText(path.join(dir, 'workflow.yaml'));
  if (workflowText == null) throw new Error(`定義が見つかりません: ${path.join(dir, 'workflow.yaml')}`);
  const files = collectFiles(dir);
  const makerJson = readText(path.join(dir, 'maker.json')) || '';
  const { raw, warnings, workflow } = model.decompile({ workflowText, files, makerJson });
  raw.machine = machine;
  return { dir, raw, warnings, workflowText, files, workflow };
}

// 工程列を検査してコンパイルし、書く。既存のフォルダなら **このツールが管理するファイルだけ**
// 書き換える（actions/ の中で工程に対応しない md は消さずに残す——手で足した資料を壊さない）。
function write(root, rawSpec, { dryRun = false } = {}) {
  const spec = model.normalizeProcedure(rawSpec);
  if (!spec.machine) throw new Error('識別名を入力してください');
  const dir = machineDir(root, spec.machine);
  const { workflow, files } = model.compile(spec);
  const errors = model.validateWorkflow(workflow, files);
  if (errors.length) throw new Error(`定義が検証を通りません:\n${errors.map((e) => `- ${e}`).join('\n')}`);
  const warnings = model.portabilityWarnings(spec);
  if (dryRun) return { dir, spec, files, warnings, written: [] };
  fs.mkdirSync(path.join(dir, 'actions'), { recursive: true });
  const written = [];
  for (const [rel, body] of Object.entries(files)) {
    const file = path.join(dir, ...rel.split('/'));
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, String(body).replace(/\r\n?/g, '\n'), 'utf8');
    written.push(rel);
  }
  // 以前このツールが書いた actions で、今の工程に無いものは消す（maker.json に載っていたものだけ）。
  const before = previousManagedActions(dir, spec);
  for (const rel of before) {
    if (files[rel]) continue;
    try { fs.unlinkSync(path.join(dir, ...rel.split('/'))); } catch { /* 無ければよい */ }
  }
  return { dir, spec, files, warnings, written };
}

function previousManagedActions(dir, spec) {
  const out = [];
  const makerJson = readText(path.join(dir, 'maker.json.prev')) || '';
  if (!makerJson) return out;
  try {
    const parsed = JSON.parse(makerJson);
    for (const s of parsed.steps || []) if (!spec.steps.some((n) => n.id === s.id)) out.push(`actions/${s.id}.md`);
  } catch { /* 読めなければ消さない */ }
  return out;
}

// 書く前に maker.json を退避する（消す対象の判断材料。ファイル 1 つ分で足りる）。
function stash(root, machine) {
  const dir = machineDir(root, machine);
  const cur = readText(path.join(dir, 'maker.json'));
  if (cur != null) fs.writeFileSync(path.join(dir, 'maker.json.prev'), cur, 'utf8');
}

function unstash(root, machine) {
  try { fs.unlinkSync(path.join(machineDir(root, machine), 'maker.json.prev')); } catch { /* 無ければよい */ }
}

function save(root, rawSpec) {
  const machine = String((rawSpec && rawSpec.machine) || '').trim();
  if (machine) { try { stash(root, machine); } catch { /* 新規 */ } }
  try {
    return write(root, rawSpec);
  } finally {
    if (machine) unstash(root, machine);
  }
}

function exists(root, machine) {
  return !!stat(path.join(machineDir(root, machine), 'workflow.yaml'));
}

module.exports = { DIR, machineDir, list, read, write, save, exists, collectFiles };
