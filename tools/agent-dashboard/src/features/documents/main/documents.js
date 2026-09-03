'use strict';

// 文書（Document）— 文書ルールに沿ってエージェント CLI に文書を作らせる制御面の本体。
//
// 置き場:
//   <workspaceDir>/<id>/                 … 1 文書 = 1 フォルダ（成果物はここに直接置かれる）
//   <workspaceDir>/<id>/document.json    … 文書の定義（名前・形式・ルール・入力・成果物の一覧）
//   <workspaceDir>/<id>/<id>.history.md  … 改訂履歴のサイドカー（変更・利用者の意図・指摘事項）
//   <workspaceDir>/<id>/inputs/          … 入力ファイルの写し（エージェントが相対パスで読める）
//   <rulesDir>/<slug>.md                 … 文書ルール（1 物理ファイル。rules.js）
//
// エージェントの起動は 2 種類:
//   作成・続き・検証 … 文書フォルダを cwd にした**書き込み可**の対話ウィンドウ（定常業務の
//                     アドホック起動と同じ経路 runChatWindow / runHeadlessRoutine）。
//                     徹底的な質問・区分ごとの確認・指摘の取捨は人との対話が本体なので、
//                     dashboard 内で往復させず外部ターミナルで進める。
//   ルールの下書き   … 読み取り専用のヘッドレス助言（resolveDashboardAgent / runAgent）。
//                     返った本文は画面で人が編集し、保存は dashboard が rulesDir へ書く（C4）。
//
// dashboard が自分で書くのは document.json・サイドカーの「人が起こした行」・文書ルールだけ。
// 成果物そのものは書かない。

const fs = require('fs');
const path = require('path');
const { sharedHomeRoot } = require('../../../base/main/agent-home');
const rules = require('./rules');
const prompts = require('./prompts');

const DOCUMENT_WORKLOAD = 'documents';
const MANIFEST = 'document.json';
const INPUTS_DIR = 'inputs';
const MODES = new Set(['whole', 'section']);

function cfgOf(config) {
  return (config && config.documents) || {};
}

function expandHome(p) {
  return String(p || '').replace(/^~(?=$|\/|\\)/, sharedHomeRoot());
}

function workspaceDir(config) {
  const raw = String(cfgOf(config).workspaceDir || '').trim();
  return raw ? path.resolve(expandHome(raw)) : path.join(sharedHomeRoot(), '.agents', 'documents');
}

function rulesDir(config) {
  const raw = String(cfgOf(config).rulesDir || '').trim();
  return raw ? path.resolve(expandHome(raw)) : path.join(sharedHomeRoot(), '.agents', 'document-rules');
}

function sidecarName(id) {
  return `${id}.history.md`;
}

function stamp(d = new Date()) {
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

// サイドカーへ 1 項目を追記する。エージェントにも同じ形で書かせる（prompts.sidecarRules）。
function historyEntry({ kind, by = '利用者', at = new Date(), changes = [], intent = [], findings = [] }) {
  const list = (items) => {
    const rows = (items || []).map((s) => String(s || '').trim()).filter(Boolean);
    return rows.length ? rows.map((s) => `- ${s.replace(/\r?\n/g, '\n  ')}`).join('\n') : '- （なし）';
  };
  return [
    `## ${stamp(at)} — ${kind}（${by}）`,
    '',
    '### 変更',
    list(changes),
    '',
    '### 利用者の意図',
    list(intent),
    '',
    '### 指摘事項',
    list(findings),
    '',
  ].join('\n');
}

function appendSidecar(setDir, id, entry) {
  const file = path.join(setDir, sidecarName(id));
  const exists = fs.existsSync(file);
  const head = exists ? '' : `# 改訂履歴: ${id}\n\n`
    + '変更・利用者の意図・指摘事項を時系列で残す。文書ルールの元になる。\n\n';
  fs.appendFileSync(file, `${head}${exists ? '\n' : ''}${entry}`, 'utf8');
  return file;
}

function readSidecar(setDir, id) {
  try {
    return fs.readFileSync(path.join(setDir, sidecarName(id)), 'utf8');
  } catch {
    return '';
  }
}

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

function formatOf(file) {
  const lower = String(file || '').toLowerCase();
  // 長い拡張子（.drawio.svg）を先に見る
  const sorted = [...rules.FORMATS].sort((a, b) => b[2].length - a[2].length);
  const row = sorted.find(([, , ext]) => lower.endsWith(ext));
  return row ? row[0] : '';
}

// フォルダの実ファイルから成果物を数える。document.json の outputs はエージェントが書く
// 補足（役割・関係）で、無くても一覧は出る——記録漏れで成果物が見えなくなるのを避ける。
function scanOutputs(setDir, id, manifest) {
  let names;
  try {
    names = fs.readdirSync(setDir, { withFileTypes: true })
      .filter((d) => d.isFile() && !d.name.startsWith('.'))
      .map((d) => d.name);
  } catch {
    return [];
  }
  const skip = new Set([MANIFEST, sidecarName(id)]);
  const declared = new Map();
  for (const o of Array.isArray(manifest && manifest.outputs) ? manifest.outputs : []) {
    if (o && o.file) declared.set(String(o.file), o);
  }
  const out = [];
  for (const name of names.sort((a, b) => a.localeCompare(b, 'ja'))) {
    if (skip.has(name) || name.endsWith('.tmp') || /\.tmp\.\d+$/.test(name)) continue;
    let size = 0;
    let mtime = '';
    try {
      const st = fs.statSync(path.join(setDir, name));
      size = st.size;
      mtime = st.mtime.toISOString();
    } catch { /* 消えた直後は 0 のまま */ }
    const d = declared.get(name) || {};
    out.push({
      file: name,
      path: path.join(setDir, name),
      format: formatOf(name) || String(d.format || ''),
      role: String(d.role || ''),
      relatedTo: Array.isArray(d.relatedTo) ? d.relatedTo.map(String) : [],
      relation: String(d.relation || ''),
      size,
      updatedAt: mtime,
    });
  }
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

function setSummary(setDir, id) {
  const manifest = readManifest(setDir);
  const outputs = scanOutputs(setDir, id, manifest);
  const last = outputs.map((o) => o.updatedAt).filter(Boolean).sort().pop() || '';
  return {
    id,
    dir: setDir,
    name: String(manifest.name || id),
    formats: rules.normalizeFormats(manifest.formats),
    mode: MODES.has(manifest.mode) ? manifest.mode : 'whole',
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

function loadRuleFor(config, manifest) {
  const ref = manifest && manifest.rule && manifest.rule.file;
  if (!ref) return null;
  try {
    return rules.readRule(rulesDir(config), ref);
  } catch (e) {
    return { file: ref, name: (manifest.rule && manifest.rule.name) || '', content: '', error: e.message };
  }
}

// 表示に要るものを 1 往復で返す（定義・成果物・入力・履歴・ルール）。
function get(config, { id } = {}) {
  const { dir, id: key } = resolveSet(config, id);
  const manifest = readManifest(dir);
  const rule = loadRuleFor(config, manifest);
  return {
    ...setSummary(dir, key),
    request: String(manifest.request || ''),
    inputs: listInputs(dir),
    outputs: scanOutputs(dir, key, manifest),
    history: readSidecar(dir, key),
    sidecar: path.join(dir, sidecarName(key)),
    // 参照は定義に書いたファイル名（rulesDir 内の相対名）で返す。読めたときの絶対パスは
    // 表示に要らないうえ、画面がそのまま別の IPC へ渡すと置き場の外扱いになる。
    rule: rule ? {
      file: String(manifest.rule.file), name: rule.name || String(manifest.rule.name || ''),
      missing: rule.missing || [], error: rule.error || '',
    } : null,
  };
}

// この端末で文書作成に使うエージェント（表示用。起動時にも同じ解決を通す）。
function resolveDocumentAgent(config, cwd) {
  const agent = require('../../agent-project/main/agent');
  return agent.resolveAgent(config, cwd, { workload: DOCUMENT_WORKLOAD });
}

function overview(config) {
  const errors = [];
  const ws = workspaceDir(config);
  const rd = rulesDir(config);
  let agentInfo = null;
  try {
    const r = resolveDocumentAgent(config, ws);
    agentInfo = { cli: r.cli, model: r.model, source: r.source, interactive: !!(r.spec && r.spec.interactive) };
  } catch (e) {
    errors.push(`エージェントの解決: ${e.message}`);
  }
  return {
    workspaceDir: ws,
    rulesDir: rd,
    workspaceExists: fs.existsSync(ws),
    rulesDirExists: fs.existsSync(rd),
    sets: listSets(config),
    rules: rules.listRules(rd),
    agent: agentInfo,
    formats: rules.FORMATS.map(([id, label]) => ({ id, label })),
    sections: rules.RULE_SECTIONS.map(([key, label, help]) => ({ key, label, help })),
    errors,
  };
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

function copyInputs(setDir, sources) {
  const dir = path.join(setDir, INPUTS_DIR);
  fs.mkdirSync(dir, { recursive: true });
  const out = [];
  const used = new Set();
  for (const src of sources) {
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

// 対話ウィンドウ（書き込み可）で文書フォルダを cwd にエージェントを起こす。
// CLI/モデルの解決・共通指示の前置・セッション開始コマンドは定常業務のアドホック起動と
// 同じ部品を使う（起動器を増やさない）。
function launchWindow(config, { cwd, prompt, title, sessionKey, message }) {
  const cowork = require('../../cowork/main/cowork');
  const { runChatWindow } = require('../../cowork/main/loopProvider');
  const agent = require('../../agent-project/main/agent');
  const selected = resolveDocumentAgent(config, cwd);
  const fullPrompt = cowork.withGlobalInstructions(config, prompt);
  if (cowork.needsHeadlessHarness(selected.spec)) {
    return cowork.runHeadlessRoutine(config, {
      cwd, prompt: fullPrompt, acceptance: [], selected, title, record: () => {},
    });
  }
  const launch = agent.interactiveLaunchSpec(config, cwd, { workload: DOCUMENT_WORKLOAD, resolved: selected });
  const res = runChatWindow({
    chatCommand: launch.chatCommand,
    prompt: fullPrompt,
    cwd,
    sessionCommands: cowork.planSessionCommands(config, cwd, {
      agentCli: launch.cli, skillCommandPrefix: launch.skillCommandPrefix,
    }),
    readyPattern: launch.readyPattern,
    readyTimeoutSec: launch.readyTimeoutSec,
    sessionKey,
    sessionPrefix: 'agent-doc',
    title,
    message: message || `別ウィンドウで ${launch.cli} を起動しました`,
  });
  return { ...res, cli: launch.cli, model: launch.model };
}

function touchManifest(dir, patch) {
  const manifest = readManifest(dir);
  const next = { ...manifest, ...patch, updatedAt: new Date().toISOString() };
  writeManifest(dir, next);
  return next;
}

function create(config, payload = {}) {
  const name = String(payload.name || '').trim();
  const formats = rules.normalizeFormats(payload.formats);
  const mode = MODES.has(payload.mode) ? payload.mode : 'whole';
  const request = String(payload.prompt || '').trim();
  const sources = Array.isArray(payload.inputs) ? payload.inputs : [];
  if (!name) throw new Error('文書の名前を入力してください');
  if (!formats.length) throw new Error('出力する形式を 1 つ以上選んでください');
  if (!request && !sources.length) throw new Error('依頼内容か入力ファイルのどちらかを指定してください');
  let rule = null;
  if (String(payload.ruleFile || '').trim()) {
    rule = rules.readRule(rulesDir(config), payload.ruleFile);
  }
  const root = workspaceDir(config);
  fs.mkdirSync(root, { recursive: true });
  const id = availableSetId(root, name);
  const dir = path.join(root, id);
  fs.mkdirSync(dir, { recursive: true });
  const inputs = copyInputs(dir, sources);
  const now = new Date();
  const manifest = {
    version: 1,
    id,
    name,
    formats,
    mode,
    rule: rule ? { file: path.basename(rule.file), name: rule.name } : null,
    request,
    inputs,
    outputs: [],
    createdAt: now.toISOString(),
    updatedAt: now.toISOString(),
    lastAction: { kind: 'create', at: now.toISOString() },
  };
  writeManifest(dir, manifest);
  appendSidecar(dir, id, historyEntry({
    kind: '作成の依頼', at: now,
    intent: [
      request || '（依頼文なし。入力ファイルから作る）',
      `出力形式: ${formats.map(rules.formatLabel).join(' / ')}`,
      `進め方: ${mode === 'section' ? '区分ごとに作る' : '一気に作る'}`,
      rule ? `文書ルール: ${rule.name}（${path.basename(rule.file)}）` : '文書ルール: なし',
      ...inputs.map((it) => `入力: ${it.name}（元: ${it.source}）`),
    ],
  }));
  const prompt = prompts.createPrompt({
    name, setDir: dir, mode, formats, rule, inputs, request,
    sidecarFile: sidecarName(id), manifestFile: MANIFEST,
    divisions: rule ? rule.parsed.divisions : [],
  });
  const launch = launchWindow(config, {
    cwd: dir, prompt, sessionKey: `doc:${id}`, title: `文書を作成: ${name}`,
    message: '別ウィンドウで文書の作成を始めました。まず質問に答えてください。',
  });
  return { set: get(config, { id }), launch };
}

function resume(config, payload = {}) {
  const { dir, id } = resolveSet(config, payload.id);
  const instruction = String(payload.instruction || '').trim();
  if (!instruction) throw new Error('続けて依頼する内容を入力してください');
  const manifest = touchManifest(dir, { lastAction: { kind: 'resume', at: new Date().toISOString() } });
  const rule = loadRuleFor(config, manifest);
  appendSidecar(dir, id, historyEntry({ kind: '続きの依頼', intent: [instruction] }));
  const prompt = prompts.resumePrompt({
    name: manifest.name, setDir: dir, instruction, rule,
    sidecarFile: sidecarName(id), manifestFile: MANIFEST,
    outputs: scanOutputs(dir, id, manifest),
  });
  const launch = launchWindow(config, {
    cwd: dir, prompt, sessionKey: `doc:${id}`, title: `文書の続き: ${manifest.name}`,
    message: '別ウィンドウで続きの作業を始めました。',
  });
  return { set: get(config, { id }), launch };
}

function verify(config, payload = {}) {
  const { dir, id } = resolveSet(config, payload.id);
  const review = String(payload.review || '').trim();
  const manifest = touchManifest(dir, { lastAction: { kind: 'verify', at: new Date().toISOString() } });
  const rule = loadRuleFor(config, manifest);
  appendSidecar(dir, id, historyEntry({
    kind: '検証の依頼',
    intent: ['汎用の検証（用語・整合性・論理性・つながり・わかりやすさ・AI 臭）を依頼'],
    findings: review ? [`ドメインのレビュー結果（利用者入力）:\n${review}`] : [],
  }));
  const prompt = prompts.verifyPrompt({
    name: manifest.name, setDir: dir, review, rule,
    sidecarFile: sidecarName(id), manifestFile: MANIFEST,
    outputs: scanOutputs(dir, id, manifest),
  });
  const launch = launchWindow(config, {
    cwd: dir, prompt, sessionKey: `doc:${id}`, title: `文書を検証: ${manifest.name}`,
    message: '別ウィンドウで検証を始めました。指摘を確認して、直すものを選んでください。',
  });
  return { set: get(config, { id }), launch };
}

// ---------------------------------------------------------------------------
// ルールの下書き（ヘッドレス・読み取り専用の助言）
// ---------------------------------------------------------------------------

async function advise(config, cwd, purpose, prompt) {
  const agent = require('../../agent-project/main/agent');
  const resolved = agent.resolveDashboardAgent(config, cwd, { purpose });
  const raw = await agent.runDashboardAgent(config, resolved, purpose, () => agent.runAgent(resolved, prompt, cwd));
  const text = agent.stripFence(raw);
  if (!String(text || '').trim()) throw new Error('エージェントの応答が空でした');
  return { text, cli: resolved.cli, model: resolved.model, source: resolved.source };
}

function safeAdviseCwd(config) {
  const dir = rulesDir(config);
  try {
    fs.mkdirSync(dir, { recursive: true });
  } catch { /* 作れなければ cwd 無しで起動する */ }
  return fs.existsSync(dir) ? dir : undefined;
}

async function draftRule(config, payload = {}) {
  const name = String(payload.name || '').trim();
  const formats = rules.normalizeFormats(payload.formats);
  const draft = String(payload.draft || '').trim();
  const template = String(payload.template || '').trim();
  if (!draft && !template) throw new Error('原案かテンプレートのどちらかを入力してください');
  const prompt = prompts.ruleDraftPrompt({ name, formats, draft, template });
  const res = await advise(config, safeAdviseCwd(config), 'document-rule-draft', prompt);
  const content = rules.normalizeRuleText(res.text, { name, formats });
  return { content, parsed: rules.parseRule(content), cli: res.cli, model: res.model, source: res.source };
}

async function ruleFromHistory(config, payload = {}) {
  const { dir, id } = resolveSet(config, payload.id);
  const manifest = readManifest(dir);
  const history = readSidecar(dir, id);
  if (!history.trim()) throw new Error('改訂履歴がまだありません');
  const rule = loadRuleFor(config, manifest);
  const name = String(payload.name || '').trim() || `${manifest.name}のルール`;
  const prompt = prompts.ruleFromHistoryPrompt({
    name, formats: rules.normalizeFormats(manifest.formats), history, rule,
    manifest: JSON.stringify(manifest, null, 2),
  });
  const res = await advise(config, dir, 'document-rule-from-history', prompt);
  const content = rules.normalizeRuleText(res.text, { name, formats: manifest.formats });
  return { content, name, parsed: rules.parseRule(content), cli: res.cli, model: res.model };
}

// 完成後のフィードバックを、既存ルールの更新案か新規ルールの案にする。
// フィードバック本文はサイドカーへ先に残す——ルール化を保存しなくても意図は文書側に残る。
async function feedback(config, payload = {}) {
  const { dir, id } = resolveSet(config, payload.id);
  const text = String(payload.feedback || '').trim();
  if (!text) throw new Error('フィードバックを入力してください');
  const target = payload.target === 'existing' ? 'existing' : 'new';
  const manifest = readManifest(dir);
  let rule = null;
  if (target === 'existing') {
    const ref = String(payload.ruleFile || (manifest.rule && manifest.rule.file) || '').trim();
    if (!ref) throw new Error('更新する文書ルールを選択してください');
    rule = rules.readRule(rulesDir(config), ref);
  } else if (manifest.rule && manifest.rule.file) {
    rule = loadRuleFor(config, manifest);
  }
  appendSidecar(dir, id, historyEntry({
    kind: '完成後のフィードバック',
    intent: [text],
    findings: [target === 'existing' ? `文書ルール「${rule.name}」の更新案を作成` : '新しい文書ルールの案を作成'],
  }));
  touchManifest(dir, { lastAction: { kind: 'feedback', at: new Date().toISOString() } });
  const history = readSidecar(dir, id);
  const name = target === 'existing' ? rule.name
    : String(payload.name || '').trim() || `${manifest.name}のルール`;
  const prompt = prompts.feedbackRulePrompt({ name: manifest.name, feedback: text, history, rule, target });
  const res = await advise(config, dir, 'document-rule-feedback', prompt);
  const content = rules.normalizeRuleText(res.text, { name, formats: manifest.formats });
  return {
    content, name, target,
    file: target === 'existing' ? path.basename(rule.file) : '',
    parsed: rules.parseRule(content), cli: res.cli, model: res.model,
  };
}

function saveSettings(config, saveConfig, payload = {}) {
  const next = {
    ...config,
    documents: {
      ...cfgOf(config),
      workspaceDir: String(payload.workspaceDir == null ? cfgOf(config).workspaceDir : payload.workspaceDir).trim(),
      rulesDir: String(payload.rulesDir == null ? cfgOf(config).rulesDir : payload.rulesDir).trim(),
    },
  };
  return saveConfig(next);
}

module.exports = {
  DOCUMENT_WORKLOAD,
  MANIFEST,
  INPUTS_DIR,
  workspaceDir,
  rulesDir,
  sidecarName,
  historyEntry,
  appendSidecar,
  readSidecar,
  readManifest,
  scanOutputs,
  formatOf,
  listSets,
  get,
  overview,
  create,
  resume,
  verify,
  draftRule,
  ruleFromHistory,
  feedback,
  saveSettings,
  launchWindow,
  availableSetId,
};
