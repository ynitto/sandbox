'use strict';

// 文書（Document）— 文書ルールに沿ってエージェント CLI に文書を作らせる制御面の用途層。
//
// 役割分担:
//   formats.js  … 対応形式のカタログ（形式を足すのはここだけ）
//   rules.js    … 文書ルール（1 物理ファイル）の書式と読み書き
//   sidecar.js  … 改訂履歴の書式と追記
//   store.js    … 文書フォルダ・定義（document.json）・成果物の走査
//   prompts.js  … 依頼文（決定的）
//   launcher.js … 他の制御面へのアダプタ（対話ウィンドウ起動・ヘッドレス助言）
//   documents.js（このファイル）… 上を組み合わせた用途: 作成・続き・検証・ルール化
//
// dashboard が自分で書くのは document.json・サイドカーの「人が起こした行」・文書ルールだけ。
// 成果物そのものは書かない（エージェントの対話セッションが書く）。

const fs = require('fs');
const path = require('path');
const formats = require('./formats');
const rules = require('./rules');
const sidecar = require('./sidecar');
const store = require('./store');
const prompts = require('./prompts');
const launcher = require('./launcher');

// 対話セッションを起こす操作の表。新しい操作（例: 翻訳・要約）はここに 1 行足し、
// prompts.js に依頼文を 1 つ足せば、画面は overview.actions から自動で拾う。
//   label    … 画面の操作名・一覧の「最後の操作」表示
//   history  … サイドカーの項目名
//   title    … 外部ウィンドウのタイトル
//   message  … 起動直後に画面へ出す文
//   prompt   … 依頼文の組み立て（sessionContext の戻りと payload を受ける）
//   entry    … サイドカーへ先に残す項目（payload を受ける）
//   validate … 入力検証（payload を受け、不正なら throw）
const SESSION_KINDS = {
  create: {
    label: '作成を依頼',
    history: '作成の依頼',
    title: (name) => `文書を作成: ${name}`,
    message: '別ウィンドウで文書の作成を始めました。まず質問に答えてください。',
    prompt: (ctx, payload) => prompts.createPrompt({
      ...ctx, mode: ctx.manifest.mode, formats: ctx.manifest.formats, request: payload.request,
      inputs: ctx.manifest.inputs, divisions: ctx.rule && ctx.rule.parsed ? ctx.rule.parsed.divisions : [],
    }),
    entry: (ctx, payload) => ({
      intent: [
        payload.request || '（依頼文なし。入力ファイルから作る）',
        `出力形式: ${ctx.manifest.formats.map(formats.formatLabel).join(' / ')}`,
        `進め方: ${MODE_LABELS[ctx.manifest.mode] || ctx.manifest.mode}`,
        ctx.rule ? `文書ルール: ${ctx.rule.name}（${path.basename(ctx.rule.file)}）` : '文書ルール: なし',
        ...ctx.manifest.inputs.map((it) => `入力: ${it.name}（元: ${it.source}）`),
      ],
    }),
  },
  resume: {
    label: '続きを依頼',
    history: '続きの依頼',
    title: (name) => `文書の続き: ${name}`,
    message: '別ウィンドウで続きの作業を始めました。',
    validate: (payload) => {
      if (!payload.instruction) throw new Error('続けて依頼する内容を入力してください');
    },
    prompt: (ctx, payload) => prompts.resumePrompt({ ...ctx, instruction: payload.instruction }),
    entry: (_ctx, payload) => ({ intent: [payload.instruction] }),
  },
  verify: {
    label: '検証を依頼',
    history: '検証の依頼',
    title: (name) => `文書を検証: ${name}`,
    message: '別ウィンドウで検証を始めました。指摘を確認して、直すものを選んでください。',
    prompt: (ctx, payload) => prompts.verifyPrompt({ ...ctx, review: payload.review }),
    entry: (_ctx, payload) => ({
      intent: ['汎用の検証（用語・整合性・論理性・つながり・わかりやすさ・AI 臭）を依頼'],
      findings: payload.review ? [`ドメインのレビュー結果（利用者入力）:\n${payload.review}`] : [],
    }),
  },
};

// ヘッドレス助言で終わる操作（対話セッションは起こさない）。画面の表示名だけをここで持つ。
const ADVICE_KINDS = {
  feedback: { label: 'フィードバック', history: '完成後のフィードバック' },
};

const MODE_LABELS = { whole: '一気に作る', section: '区分ごとに作る' };

// 画面へ渡す操作の一覧（一覧の「最後の操作」表示に使う）。
function actionCatalog() {
  return [
    ...Object.entries(SESSION_KINDS).map(([kind, k]) => ({ kind, label: k.label })),
    ...Object.entries(ADVICE_KINDS).map(([kind, k]) => ({ kind, label: k.label })),
  ];
}

// ---------------------------------------------------------------------------
// 読み取り
// ---------------------------------------------------------------------------

function loadRuleFor(config, manifest) {
  const ref = manifest && manifest.rule && manifest.rule.file;
  if (!ref) return null;
  try {
    return rules.readRule(store.rulesDir(config), ref);
  } catch (e) {
    return { file: ref, name: (manifest.rule && manifest.rule.name) || '', content: '', error: e.message };
  }
}

// 表示に要るものを 1 往復で返す（定義・成果物・入力・履歴・ルール）。
function get(config, { id } = {}) {
  const { dir, id: key } = store.resolveSet(config, id);
  const manifest = store.readManifest(dir);
  const rule = loadRuleFor(config, manifest);
  return {
    ...store.setSummary(dir, key, manifest),
    request: String(manifest.request || ''),
    inputs: store.listInputs(dir),
    outputs: store.scanOutputs(dir, key, manifest),
    history: sidecar.readSidecar(dir, key),
    sidecar: path.join(dir, sidecar.sidecarName(key)),
    // 参照は定義に書いたファイル名（rulesDir 内の相対名）で返す。読めたときの絶対パスは
    // 表示に要らないうえ、画面がそのまま別の IPC へ渡すと置き場の外扱いになる。
    rule: rule ? {
      file: String(manifest.rule.file), name: rule.name || String(manifest.rule.name || ''),
      missing: rule.missing || [], error: rule.error || '',
    } : null,
  };
}

function overview(config) {
  const errors = [];
  const ws = store.workspaceDir(config);
  const rd = store.rulesDir(config);
  let agentInfo = null;
  try {
    agentInfo = launcher.describeAgent(config, ws);
  } catch (e) {
    errors.push(`エージェントの解決: ${e.message}`);
  }
  return {
    workspaceDir: ws,
    rulesDir: rd,
    workspaceExists: fs.existsSync(ws),
    rulesDirExists: fs.existsSync(rd),
    sets: store.listSets(config),
    rules: rules.listRules(rd),
    agent: agentInfo,
    formats: formats.formatOptions(),
    sections: rules.RULE_SECTIONS.map(([key, label, help]) => ({ key, label, help })),
    modes: Object.entries(MODE_LABELS).map(([id, label]) => ({ id, label })),
    actions: actionCatalog(),
    errors,
  };
}

// ---------------------------------------------------------------------------
// 対話セッション（作成・続き・検証）
// ---------------------------------------------------------------------------

// 依頼文に共通で渡す文脈（文書名・フォルダ・ルール・成果物・サイドカー名）。
// setDir は **エージェントから見た表記**（Windows 側の置き場は WSL 表記へ直す）。
// 依頼文の「作業フォルダは …」と、起動する cwd を同じ表記に揃える。
function sessionContext(config, dir, id, manifest) {
  return {
    name: String(manifest.name || id),
    setDir: launcher.agentPath(dir),
    manifest,
    rule: loadRuleFor(config, manifest),
    outputs: store.scanOutputs(dir, id, manifest),
    sidecarFile: sidecar.sidecarName(id),
    manifestFile: store.MANIFEST,
  };
}

// 操作の共通手順: 入力検証 → 定義の lastAction 更新 → サイドカーへ依頼を記録 → 依頼文 → 起動。
// 起動に失敗しても（外部ターミナルが無い等）記録は残るので、「続きを依頼」からやり直せる。
function startSession(config, kind, { dir, id, payload }) {
  const spec = SESSION_KINDS[kind];
  if (!spec) throw new Error(`未知の操作です: ${kind}`);
  if (typeof spec.validate === 'function') spec.validate(payload);
  const current = store.touchManifest(dir, { lastAction: { kind, at: new Date().toISOString() } });
  const ctx = sessionContext(config, dir, id, current);
  sidecar.appendSidecar(dir, id, sidecar.historyEntry({ kind: spec.history, ...spec.entry(ctx, payload) }));
  const prompt = spec.prompt(ctx, payload);
  const launch = launcher.launchWindow(config, {
    cwd: dir, prompt, sessionKey: `doc:${id}`, title: spec.title(ctx.name), message: spec.message,
  });
  return { set: get(config, { id }), launch };
}

function create(config, payload = {}) {
  const name = String(payload.name || '').trim();
  const formatIds = formats.normalizeFormats(payload.formats);
  const request = String(payload.prompt || '').trim();
  const inputs = Array.isArray(payload.inputs) ? payload.inputs : [];
  if (!name) throw new Error('文書の名前を入力してください');
  if (!formatIds.length) throw new Error('出力する形式を 1 つ以上選んでください');
  if (!request && !inputs.length) throw new Error('依頼内容か入力ファイルのどちらかを指定してください');
  const rule = String(payload.ruleFile || '').trim() ? rules.readRule(store.rulesDir(config), payload.ruleFile) : null;
  const { id, dir } = store.createSet(config, {
    name, formats: formatIds, mode: payload.mode, rule, request, inputs,
  });
  return startSession(config, 'create', { dir, id, payload: { request } });
}

function resume(config, payload = {}) {
  const { dir, id } = store.resolveSet(config, payload.id);
  return startSession(config, 'resume', {
    dir, id, payload: { instruction: String(payload.instruction || '').trim() },
  });
}

function verify(config, payload = {}) {
  const { dir, id } = store.resolveSet(config, payload.id);
  return startSession(config, 'verify', {
    dir, id, payload: { review: String(payload.review || '').trim() },
  });
}

// ---------------------------------------------------------------------------
// ルールの下書き（ヘッドレス・読み取り専用の助言。保存は人が確認してから rules.saveRule）
// ---------------------------------------------------------------------------

function adviseCwd(config) {
  const dir = store.rulesDir(config);
  try {
    fs.mkdirSync(dir, { recursive: true });
  } catch { /* 作れなければ cwd 無しで起動する */ }
  return fs.existsSync(dir) ? dir : undefined;
}

async function ruleAdvice(config, cwd, purpose, prompt, { name, formats: formatIds }) {
  const res = await launcher.advise(config, cwd, purpose, prompt);
  const content = rules.normalizeRuleText(res.text, { name, formats: formatIds });
  return { content, parsed: rules.parseRule(content), cli: res.cli, model: res.model, source: res.source };
}

async function draftRule(config, payload = {}) {
  const name = String(payload.name || '').trim();
  const formatIds = formats.normalizeFormats(payload.formats);
  const draft = String(payload.draft || '').trim();
  const template = String(payload.template || '').trim();
  if (!draft && !template) throw new Error('原案かテンプレートのどちらかを入力してください');
  const prompt = prompts.ruleDraftPrompt({ name, formats: formatIds, draft, template });
  return ruleAdvice(config, adviseCwd(config), 'document-rule-draft', prompt, { name, formats: formatIds });
}

async function ruleFromHistory(config, payload = {}) {
  const { dir, id } = store.resolveSet(config, payload.id);
  const manifest = store.readManifest(dir);
  const history = sidecar.readSidecar(dir, id);
  if (!history.trim()) throw new Error('改訂履歴がまだありません');
  const name = String(payload.name || '').trim() || `${manifest.name}のルール`;
  const formatIds = formats.normalizeFormats(manifest.formats);
  const prompt = prompts.ruleFromHistoryPrompt({
    name, formats: formatIds, history, rule: loadRuleFor(config, manifest),
    manifest: JSON.stringify(manifest, null, 2),
  });
  const res = await ruleAdvice(config, dir, 'document-rule-from-history', prompt, { name, formats: formatIds });
  return { ...res, name };
}

// 完成後のフィードバックを、既存ルールの更新案か新規ルールの案にする。
// フィードバック本文はサイドカーへ先に残す——ルール化を保存しなくても意図は文書側に残る。
async function feedback(config, payload = {}) {
  const { dir, id } = store.resolveSet(config, payload.id);
  const text = String(payload.feedback || '').trim();
  if (!text) throw new Error('フィードバックを入力してください');
  const target = payload.target === 'existing' ? 'existing' : 'new';
  const manifest = store.readManifest(dir);
  const ref = String(payload.ruleFile || (manifest.rule && manifest.rule.file) || '').trim();
  if (target === 'existing' && !ref) throw new Error('更新する文書ルールを選択してください');
  const rule = target === 'existing' ? rules.readRule(store.rulesDir(config), ref) : loadRuleFor(config, manifest);
  sidecar.appendSidecar(dir, id, sidecar.historyEntry({
    kind: ADVICE_KINDS.feedback.history,
    intent: [text],
    findings: [target === 'existing' ? `文書ルール「${rule.name}」の更新案を作成` : '新しい文書ルールの案を作成'],
  }));
  store.touchManifest(dir, { lastAction: { kind: 'feedback', at: new Date().toISOString() } });
  const name = target === 'existing' ? rule.name : String(payload.name || '').trim() || `${manifest.name}のルール`;
  const prompt = prompts.feedbackRulePrompt({
    name: manifest.name, feedback: text, history: sidecar.readSidecar(dir, id), rule, target,
  });
  const res = await ruleAdvice(config, dir, 'document-rule-feedback', prompt,
    { name, formats: formats.normalizeFormats(manifest.formats) });
  return { ...res, name, target, file: target === 'existing' ? path.basename(rule.file) : '' };
}

// ---------------------------------------------------------------------------
// 設定
// ---------------------------------------------------------------------------

function saveSettings(config, saveConfig, payload = {}) {
  const current = (config && config.documents) || {};
  const pick = (key) => String(payload[key] == null ? current[key] : payload[key]).trim();
  return saveConfig({
    ...config,
    documents: { ...current, workspaceDir: pick('workspaceDir'), rulesDir: pick('rulesDir') },
  });
}

module.exports = {
  SESSION_KINDS,
  ADVICE_KINDS,
  MODE_LABELS,
  actionCatalog,
  get,
  overview,
  create,
  resume,
  verify,
  draftRule,
  ruleFromHistory,
  feedback,
  saveSettings,
  // 互換の再輸出（ipc.js とテストが短い名前で使う）
  DOCUMENT_WORKLOAD: launcher.DOCUMENT_WORKLOAD,
  MANIFEST: store.MANIFEST,
  INPUTS_DIR: store.INPUTS_DIR,
  workspaceDir: store.workspaceDir,
  rulesDir: store.rulesDir,
  listSets: store.listSets,
  scanOutputs: store.scanOutputs,
  readManifest: store.readManifest,
  availableSetId: store.availableSetId,
  sidecarName: sidecar.sidecarName,
  historyEntry: sidecar.historyEntry,
  appendSidecar: sidecar.appendSidecar,
  readSidecar: sidecar.readSidecar,
  formatOf: formats.formatOf,
};
