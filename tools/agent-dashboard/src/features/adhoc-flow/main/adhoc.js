'use strict';

// アドホック flow 実行（計画 S21 / M1・柱2×柱3 / C3・C7）。
//
// プロジェクト（charter・バックログ・受入基準）を立てずに、agent-flow の単発 run を
// その場で投入・監視する。書くのは公式契約だけ:
//   - 投入 … <bus>/inbox/<run-id>.json（submit_request 契約。plan フィールドで
//     ユーザー定義フロー＝ビルダーの成果物を運ぶ）
//   - 手法 … run 専用の AGENT_TUNING_DIR に agent-tuning 契約のスナップショットを複製
//     （S26 の「参照でなく複製」と同じ。source: methods/<id>@<hash> で乖離検出可能）
// 実行系は agent-flow run そのもの（新しい実行系・状態ファイルは作らない）。
// run の読み取りは agent-project feature の flow.js（バスパーサ）をそのまま再利用する
// （C7: バスの読み手を 2 実装にしない）。
//
// アドホック run は done を名乗らない（C5）: 受入基準も verify も無いので、UI は
// 「終了（未検収）」として扱い、バックログの状態ファイルには一切書かない。
// 正式な仕事にする口は promote（S22: 既存の inbox 投函契約で agent-project へ昇格）。

const fs = require('fs');
const path = require('path');
const { agentHomeSubdir } = require('../../../base/main/agent-home');
const exec = require('../../routines/main/exec');
const flow = require('../../agent-project/main/flow');
const tuning = require('../../orchestration/main/tuning');

const SUBMITTER = 'agent-dashboard-adhoc';

function cfgOf(config) {
  return (config && config.adhocFlow) || {};
}

function resolveBusDir(config) {
  const c = cfgOf(config);
  return String(c.busDir || '').trim() || agentHomeSubdir('flow', 'bus');
}

function writeJsonAtomic(file, data) {
  const tmp = `${file}.tmp.${process.pid}`;
  fs.writeFileSync(tmp, `${JSON.stringify(data, null, 2)}\n`);
  fs.renameSync(tmp, file);
}

// --- プリセット（保存済みフロー定義） ---------------------------------------
// ビルダーの成果物。実行時に投入契約（plan）へ変換されるだけの宣言データで、
// 検証の正典はエンジン側（agent_flow plan_strategy_user）。ここでは保存物が
// 壊れていないかの軽い整形だけを行い、検証ロジックを複製しない（C7）。

function normalizePreset(raw) {
  if (!raw || typeof raw !== 'object') throw new Error('プリセットが不正です');
  const name = String(raw.name || '').trim();
  if (!name) throw new Error('プリセット名は必須です');
  const nodes = [];
  const seen = new Set();
  for (const n of Array.isArray(raw.nodes) ? raw.nodes : []) {
    const id = String((n && n.id) || '').trim();
    const goal = String((n && n.goal) || '').trim();
    if (!id || !goal) throw new Error('ノードには id と goal が必要です');
    if (seen.has(id)) throw new Error(`ノード id が重複しています: ${id}`);
    seen.add(id);
    nodes.push({
      id,
      goal,
      kind: String((n && n.kind) || 'work').trim() || 'work',
      deps: (Array.isArray(n.deps) ? n.deps : []).map((d) => String(d).trim()).filter(Boolean),
      agentCli: String((n && n.agentCli) || '').trim(),
      model: String((n && n.model) || '').trim(),
    });
  }
  return {
    id: String(raw.id || '').trim() || `preset-${Date.now()}`,
    name,
    description: String(raw.description || '').trim(),
    evaluate: raw.evaluate === true,
    nodes,
    methods: (Array.isArray(raw.methods) ? raw.methods : [])
      .map((m) => String(m).trim()).filter(Boolean),
    agentCli: String(raw.agentCli || '').trim(),
    model: String(raw.model || '').trim(),
    planner: String(raw.planner || '').trim(),
    updatedAt: new Date().toISOString(),
  };
}

// プリセット → 投入契約の plan（submit_request / plan_strategy_user の語彙）。
// ノードが無いプリセット（手法・エンジン設定だけの使い回し）は plan 無し＝planner に任せる。
function planFromPreset(preset) {
  if (!preset || !preset.nodes || !preset.nodes.length) return null;
  const nodes = preset.nodes.map((n) => {
    const node = { id: n.id, goal: n.goal, deps: n.deps || [], kind: n.kind || 'work' };
    if (n.agentCli) {
      node.agent = { agent_cli: n.agentCli, ...(n.model ? { model: n.model } : {}) };
    }
    return node;
  });
  const plan = { name: preset.name, nodes };
  if (preset.evaluate) plan.evaluate = true;
  return plan;
}

// --- 手法（F17）の run 専用スナップショット ----------------------------------
// 選んだ手法だけを run 専用の tuning.json へ複製し、AGENT_TUNING_DIR で agent-flow に
// 読ませる（methods.py の唯一の per-run 強制口）。端末全体の tuning.json はこの run では
// **読まれない**（置換であって合成ではない）——どの手法が効いたかを run 単位で決定的に
// するための意図した挙動で、UI にもそう表示する。

function availableMethods(config) {
  const seen = new Map();
  for (const m of tuning.catalog(config)) {
    seen.set(String(m.id), { ...m, _from: 'catalog' });
  }
  const state = tuning.load(config);
  for (const m of Array.isArray(state.methods) ? state.methods : []) {
    if (m && m.id) seen.set(String(m.id), { ...m, _from: 'tuning' });
  }
  return [...seen.values()];
}

function methodsSnapshot(config, ids) {
  if (!ids || !ids.length) return null;
  const avail = new Map(availableMethods(config).map((m) => [String(m.id), m]));
  const out = [];
  for (const id of ids) {
    const m = avail.get(String(id));
    if (!m) throw new Error(`手法が見つかりません: ${id}`);
    const { _from, ...body } = m;
    const snap = JSON.parse(JSON.stringify(body));
    snap.enabled = true;
    if (_from === 'catalog') snap.source = `methods/${snap.id}@${tuning.sourceHash(body)}`;
    else if (!snap.source) snap.source = `custom/${snap.id}`;
    out.push(snap);
  }
  return out;
}

function runTuningDir(config, runId) {
  const root = String(cfgOf(config).tuningRoot || '').trim();
  return root ? path.join(root, runId) : agentHomeSubdir('flow', 'tuning', runId);
}

function writeRunTuning(config, runId, methods) {
  const dir = runTuningDir(config, runId);
  fs.mkdirSync(dir, { recursive: true });
  const data = {
    version: 1,
    revision: 1,
    enabled: true,
    methods,
    trials: [],
    profiles: { default: {}, 'external-facing': { injections: [] } },
    updated_at: new Date().toISOString(),
    updated_by: 'agent-dashboard adhoc-flow',
  };
  writeJsonAtomic(path.join(dir, 'tuning.json'), data);
  return dir;
}

// --- 投入と起動 ---------------------------------------------------------------

function newRunId() {
  const t = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  const stamp = `${t.getFullYear()}${pad(t.getMonth() + 1)}${pad(t.getDate())}`
    + `-${pad(t.getHours())}${pad(t.getMinutes())}${pad(t.getSeconds())}`;
  return `adhoc-${stamp}-${Math.floor(1000 + Math.random() * 9000)}`;
}

// 起動シェル行の組み立て（テスト可能にするため純関数で切り出す）。
// nohup + & で切り離す——run は自己完結（heartbeat・park 監視持ち）なので、
// dashboard の生存に依存させない（C6）。ログは $HOME 基準で組む（WSL でも壊れない）。
function buildLaunchLine(config, { runId, busDir, tuningDir, agentCli, model, planner }) {
  const c = cfgOf(config);
  const q = exec.shellQuote;
  const cmd = String(c.agentFlowCommand || '').trim() || 'agent-flow';
  const guard = String(c.agentFlowCommand || '').trim()
    ? ''
    : 'command -v agent-flow >/dev/null 2>&1 || { echo agent-flow-not-found >&2; exit 127; }; ';
  const env = tuningDir ? `AGENT_TUNING_DIR=${q(exec.toWslCwd(tuningDir))} ` : '';
  const flags = [
    '--bus', q(exec.toWslCwd(busDir)), '--run-id', q(runId), 'run', '--from-inbox',
    ...(planner ? ['--planner', q(planner)] : []),
    ...(agentCli ? ['--agent-cli', q(agentCli)] : []),
    ...(model ? ['--model', q(model)] : []),
  ].join(' ');
  return `${guard}LOGDIR="$HOME/.agents/flow/logs"; mkdir -p "$LOGDIR"; `
    + `${env}nohup ${cmd} ${flags} >> "$LOGDIR/${runId}.log" 2>&1 & echo launched:$!`;
}

function submit(config, { request, preset } = {}) {
  const req = String(request || '').trim();
  if (!req) throw new Error('要求テキストは必須です');
  const p = preset ? normalizePreset(preset) : null;
  const runId = newRunId();
  const busDir = resolveBusDir(config);
  fs.mkdirSync(path.join(busDir, 'inbox'), { recursive: true });

  // submit_request 契約（agent_flow/bus.py と同じ形）。workspace は常に null＝読み取り専用 run。
  // アドホックに書込先を持たせない——成果はバスの results/artifacts に出て、正式化は
  // promote（S22）でタスクとして通常の検証つき経路へ載せ替える。
  const rec = {
    id: runId,
    request: req,
    submitter: SUBMITTER,
    workspace: null,
    references: [],
    submitted_at: new Date().toISOString(),
  };
  const plan = p ? planFromPreset(p) : null;
  if (plan) rec.plan = plan;
  writeJsonAtomic(path.join(busDir, 'inbox', `${runId}.json`), rec);

  const methods = p ? methodsSnapshot(config, p.methods) : null;
  const tuningDir = methods && methods.length ? writeRunTuning(config, runId, methods) : null;

  const line = buildLaunchLine(config, {
    runId,
    busDir,
    tuningDir,
    agentCli: p ? p.agentCli : '',
    model: p ? p.model : '',
    planner: p ? p.planner : '',
  });
  const r = exec.shInWsl(line, 20000, cfgOf(config).distro || '');
  if (r.status !== 0 || /agent-flow-not-found/.test(String(r.stderr || ''))) {
    throw new Error(`agent-flow の起動に失敗しました: ${String(r.stderr || r.stdout || '').trim().slice(0, 400)}`);
  }
  return { runId, busDir, tuningDir, plan: !!plan, methods: methods ? methods.map((m) => m.id) : [] };
}

// 再投入: 旧 run の inbox 記録（自分が書いた契約）を新しい id で写す。plan も引き継ぐ。
// 旧 run が消えていても inbox 記録が残っていれば再投入できる。
function resubmit(config, runId) {
  const busDir = resolveBusDir(config);
  const old = readInbox(busDir, runId);
  if (!old) throw new Error(`inbox 記録が見つかりません: ${runId}`);
  const next = newRunId();
  const rec = { ...old, id: next, submitted_at: new Date().toISOString() };
  writeJsonAtomic(path.join(busDir, 'inbox', `${next}.json`), rec);
  // 旧 run の手法スナップショットも新 run へ写す（同条件の再実行）
  let tuningDir = null;
  const oldTuning = path.join(runTuningDir(config, runId), 'tuning.json');
  if (fs.existsSync(oldTuning)) {
    const data = JSON.parse(fs.readFileSync(oldTuning, 'utf8'));
    tuningDir = writeRunTuning(config, next, Array.isArray(data.methods) ? data.methods : []);
  }
  const line = buildLaunchLine(config, { runId: next, busDir, tuningDir });
  const r = exec.shInWsl(line, 20000, cfgOf(config).distro || '');
  if (r.status !== 0) {
    throw new Error(`agent-flow の起動に失敗しました: ${String(r.stderr || r.stdout || '').trim().slice(0, 400)}`);
  }
  return { runId: next, busDir };
}

function readInbox(busDir, runId) {
  try {
    return JSON.parse(fs.readFileSync(path.join(busDir, 'inbox', `${runId}.json`), 'utf8'));
  } catch {
    return null;
  }
}

// --- 昇格（S22 / M1 後半） ----------------------------------------------------
// run の成果を agent-project の正式なタスクへ載せ替える。書くのは既存の inbox 投函契約
// （actions.enqueueToInbox）だけで、昇格したタスクは通常の受入基準と verify を通る。
// 昇格しない限りアドホックの成果は done にならない。

function promote(config, { projectDir, spec } = {}) {
  const dir = String(projectDir || '').trim();
  if (!dir) throw new Error('昇格先プロジェクトを選んでください');
  const engine = require('../../agent-project/main/engine');
  const roots = engine.projectRoots(config).map((r) => path.resolve(String(r)));
  if (!roots.includes(path.resolve(dir))) {
    // C1: 任意パスへの投函を許さない。実行エンジンが担当しているプロジェクトだけが宛先。
    throw new Error(`実行エンジンが担当しているプロジェクトではありません: ${dir}`);
  }
  const actions = require('../../agent-project/main/actions');
  return actions.enqueueToInbox(dir, spec || {});
}

function listProjects(config) {
  const engine = require('../../agent-project/main/engine');
  return engine.projectRoots(config).map((dir) => ({ dir, name: path.basename(dir) }));
}

module.exports = {
  SUBMITTER,
  resolveBusDir,
  normalizePreset,
  planFromPreset,
  availableMethods,
  methodsSnapshot,
  writeRunTuning,
  runTuningDir,
  buildLaunchLine,
  submit,
  resubmit,
  readInbox,
  promote,
  listProjects,
  // 読み取りはバスパーサ（agent-project/main/flow.js）をそのまま使う
  flow,
};
