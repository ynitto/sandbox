'use strict';

// Cowork の自動発見。実行エンジンが担当するプロジェクト配下を走査し、定常業務ループの
// 定期処理（`prompts[]` を持つ設定ファイル。→ LOOP_CONFIG_CANDIDATES）とステートマシン
// （.statemachine/<name>/workflow.yaml）を **ジョブ単位** で抽出して Cowork 項目にする。
//
// **パーサは 2 系統あり、役割で分けている**:
//   ・値（name / schedule / enabled / prompt 本文）= base/main/yaml.js（YAML ライブラリ）
//   ・書き戻しのアンカー（各フィールドの物理行・インデント）= parseAgentLoopPromptsWithLines
// 値の解釈（引用・ブロックスカラ・フロー記法）はライブラリに任せ、行指向パーサは「どの行に
// 書くか」だけを答える。**両者は prompts[] の並びが一致していなければならない**——ずれると
// 別のエントリへ書き戻す。行指向側がコメント行でリストを閉じないのはそのため。
// .json は JSON.parse で読む。

const fs = require('fs');
const os = require('os');
const path = require('path');
const {
  toViewerPath, viewerDistro, _isPosixAbs, _pathKey,
} = require('../../agent-project/main/project');
const { parseFlatYaml } = require('../../agent-project/main/toolconfig');
const { parseYaml, isPlainObject, scalarString } = require('../../../base/main/yaml');
const { userHomeRoots, agentHomeDir } = require('../../../base/main/agent-home');

// 走査を軽く保つためのスキップ（プロジェクト内部の既知/生成物ディレクトリ）。隠しフォルダは
// 別途 name.startsWith('.') で降下対象から外す（マーカーの .kiro/.statemachine は「降りる」の
// ではなく候補フォルダ内を「プローブ」して見つける）。
const SCAN_SKIP = new Set([
  'node_modules', 'dist', 'release', 'build', 'out', 'coverage', 'bus', 'work',
  'archive', 'flow-archive', 'backlog', 'needs', 'decisions', 'commands', 'inbox',
  'claims', 'autonomy', 'charters', 'runs', 'vendor', 'target',
]);

// 定常業務ループの設定ファイル候補 `[サブディレクトリ, ファイル名, 形式]`。上から順に
// 探し、最初に見つかった 1 つをそのフォルダの設定として採る。
//
// agent-loop は起動ディレクトリ配下の共通ホーム（`.agents/`、旧 `.agent/` はそこにしか
// 無いときのフォールバック）から `agent-loop.{yaml,yml,json}` を読む
// （agent_loop/config.py の DEFAULT_CONFIG_NAMES と `_load_prompt_file_data`）。
// **ここが agent-loop 側の正**。退役済みの設定は移行手順でこの名前へコピーする。
const LOOP_CONFIG_CANDIDATES = [
  ['.agents', 'agent-loop.yaml', 'yaml'],
  ['.agents', 'agent-loop.yml', 'yaml'],
  ['.agents', 'agent-loop.json', 'json'],
  ['.agent', 'agent-loop.yaml', 'yaml'],
  ['.agent', 'agent-loop.yml', 'yaml'],
  ['.agent', 'agent-loop.json', 'json'],
];

function readText(file) {
  try {
    return fs.readFileSync(file, 'utf8').replace(/\r\n/g, '\n');
  } catch {
    return null;
  }
}

function isDir(p) {
  try {
    return fs.statSync(p).isDirectory();
  } catch {
    return false;
  }
}

function safeList(dir) {
  try {
    return fs.readdirSync(dir);
  } catch {
    return [];
  }
}

// YAML スカラ値を取り出す。引用値は閉じ引用符までを内容とし後続コメントを無視する
// （`#` が引用内にある場合は保持）。非引用値は末尾インラインコメントを剥がす。
function scalarValue(rawVal) {
  const t = String(rawVal == null ? '' : rawVal).trim();
  if (t[0] === '"' || t[0] === "'") {
    const end = t.indexOf(t[0], 1);
    return end >= 0 ? t.slice(1, end) : t.slice(1);   // 閉じ引用符の内側（後続コメント無視）
  }
  const m = t.match(/\s+#.*$/);
  return (m ? t.slice(0, m.index) : t).trim();
}

// ---------------------------------------------------------------------------
// agent-loop prompts パーサ（行指向）
// ---------------------------------------------------------------------------
const FIELD_KEYS = new Set(['name', 'interval_minutes', 'cron', 'enabled', 'prompt',
                            'acceptance']);

// prompts: リストのエントリを、各フィールドの行番号付きで返す。
//   entry = { index, dashLine, fieldIndent, fields: { <key>: { line, rawVal } } }
// fields は FIELD_KEYS のうち実在するものだけを持つ。ブロックスカラ本文・ネストマップ・
// コメントは fieldIndent 列に一致しないため field として拾わない。
function parseAgentLoopPromptsWithLines(text) {
  const lines = String(text == null ? '' : text).split('\n');
  let start = -1;
  for (let i = 0; i < lines.length; i += 1) {
    if (/^prompts:\s*(#.*)?$/.test(lines[i])) { start = i + 1; break; }
  }
  if (start < 0) return [];

  const entries = [];
  let dashIndent = null;
  let cur = null;

  const readField = (segment, lineIdx) => {
    const m = segment.match(/^(\w[\w-]*):\s?(.*)$/);
    if (!m || !FIELD_KEYS.has(m[1])) return;
    if (cur && cur.fields[m[1]] === undefined) {
      cur.fields[m[1]] = { line: lineIdx, rawVal: m[2] };
    }
  };

  for (let j = start; j < lines.length; j += 1) {
    const raw = lines[j];
    if (/^\s*$/.test(raw)) continue;                 // 空行は許容
    const indent = raw.length - raw.replace(/^\s+/, '').length;
    const dash = raw.match(/^(\s*)-\s+/);
    if (dash) {
      const di = dash[1].length;
      if (dashIndent === null) dashIndent = di;
      if (di === dashIndent) {
        const fieldIndent = di + dash[0].slice(di).length;   // '- ' 分の桁 = 最初の key の列
        // dashPrefix = 行頭〜最初の key 直前（例 '  - '）。dash 行の inline フィールドを
        // 書き戻す際に '- ' を落とさないため保持する。
        cur = { index: entries.length, dashLine: j, fieldIndent, dashPrefix: raw.slice(0, fieldIndent), fields: {} };
        entries.push(cur);
        readField(raw.slice(fieldIndent), j);               // dash 行の inline 先頭フィールド
        continue;
      }
      // dashIndent より深い/浅い dash はエントリのフィールド（ネストリスト等）→ field 扱いしない
      if (di < dashIndent) break;                            // リストが閉じた
      continue;
    }
    // 列 0 のコメント行（`# ---- 区切り ----`）でリストを閉じない。閉じると以降の
    // エントリが丸ごと落ち、値側（YAML パーサ）と番号がずれて別のエントリへ書き戻す。
    if (raw.trimStart().startsWith('#')) continue;
    // トップレベルの新キー（prompts と同じ列 0 のスカラ）に達したら prompts リスト終了
    if (indent === 0) break;
    if (cur && indent === cur.fieldIndent) readField(raw.slice(indent), j);
    // それ以外（ブロックスカラ本文・ネスト）は無視
  }
  return entries;
}

// prompts: の各エントリ（マップ）を YAML パーサで読む。**値の読みはこちらが正**——
// 行指向パーサ（parseAgentLoopPromptsWithLines）は書き戻しのアンカー専用で、
// 引用・ブロックスカラ・フロー記法の解釈までは持たない。
function agentLoopEntries(text) {
  const doc = parseYaml(text);
  if (!isPlainObject(doc) || !Array.isArray(doc.prompts)) return [];
  return doc.prompts.filter(isPlainObject);
}

// エントリごとの prompt 本文。ブロックスカラの相対インデントは保つ（前後の空白だけ落とす）。
function agentLoopPromptTexts(text) {
  return agentLoopEntries(text).map((e) => {
    const v = e.prompt;
    if (v == null) return '';
    return (typeof v === 'string' ? v : (scalarString(v) || '')).trim();
  });
}

// 書き戻し不要な読み取り用: {name, interval_minutes, cron, enabled} の素の値。
function parseAgentLoopPrompts(text) {
  return agentLoopEntries(text).map((e) => {
    const out = {};
    const name = scalarString(e.name);
    if (name !== null) out.name = name;
    const cron = scalarString(e.cron);
    if (cron !== null) out.cron = cron;
    const n = parseInt(scalarString(e.interval_minutes), 10);
    if (!Number.isNaN(n)) out.interval_minutes = n;
    if (typeof e.enabled === 'boolean') out.enabled = e.enabled;
    return out;
  });
}

// .json / .yaml いずれかから prompts エントリ（素の値）・prompt 本文・format を返す。
function readAgentPrompts(file, format) {
  const text = readText(file);
  if (text === null) return { format, entries: [], texts: [] };
  if (format === 'json') {
    try {
      const obj = JSON.parse(text);
      const arr = Array.isArray(obj && obj.prompts) ? obj.prompts : [];
      const entries = arr.filter((p) => p && typeof p === 'object');
      return { format, entries, texts: entries.map((p) => String(p.prompt == null ? '' : p.prompt)) };
    } catch {
      return { format, entries: [], texts: [] };
    }
  }
  return { format, entries: parseAgentLoopPrompts(text), texts: agentLoopPromptTexts(text) };
}

// agent-loop の prompt 本文がステートマシン実行の対エントリ（「xxx ステートマシンを実行して」等）
// かどうかを判定する。フォルダ名（.statemachine/<name>）か表示名（workflow.yaml の name）が
// 本文に現れ、かつ「ステートマシン」への言及があれば対とみなす。
function pairedStateMachineOf(promptText, smInfos) {
  const t = String(promptText == null ? '' : promptText);
  if (!t) return null;
  for (const sm of smInfos || []) {
    if (t.includes(`.statemachine/${sm.smName}`)) return sm;
    if (!/ステートマシン|state\s*machine/i.test(t)) continue;
    const names = [sm.smName, sm.meta && sm.meta.name].filter(Boolean);
    if (names.some((n) => t.includes(n))) return sm;
  }
  return null;
}

function scheduleOf(entry) {
  if (entry.cron) return { schedule: String(entry.cron), scheduleKey: 'cron' };
  if (entry.interval_minutes != null && entry.interval_minutes !== '') {
    return { schedule: `${entry.interval_minutes}m`, scheduleKey: 'interval_minutes' };
  }
  return { schedule: '', scheduleKey: '' };
}

// ---------------------------------------------------------------------------
// フォルダ走査
// ---------------------------------------------------------------------------
function detectMarkers(dir, includeLegacy = true) {
  let kiroFile = null;
  let kiroFormat = null;
  for (const [subdir, name, fmt] of LOOP_CONFIG_CANDIDATES) {
    if (!includeLegacy && subdir === '.agent') continue;
    const f = subdir ? path.join(dir, subdir, name) : path.join(dir, name);
    try {
      if (fs.statSync(f).isFile()) { kiroFile = f; kiroFormat = fmt; break; }
    } catch { /* not present */ }
  }
  const smRoot = path.join(dir, '.statemachine');
  const smNames = [];
  if (isDir(smRoot)) {
    for (const name of safeList(smRoot)) {
      if (name.startsWith('.')) continue;
      const wf = path.join(smRoot, name, 'workflow.yaml');
      try {
        if (fs.statSync(wf).isFile()) smNames.push(name);
      } catch { /* not a workflow dir */ }
    }
  }
  if (!kiroFile && !smNames.length) return null;
  return { folder: dir, kiroFile, kiroFormat, smNames: smNames.sort() };
}

// dashboard が管理項目を**書く**先。既存のプロジェクト設定があればそれを使い、無ければ
// `<root>/.agents/agent-loop.yml` を作る。ユーザーホームでは旧 `.agent` を読まない。
function loopConfigFile(root) {
  let marker = null;
  const home = userHomeRoots().some((dir) => _pathKey(dir) === _pathKey(root));
  try { marker = detectMarkers(root, !home); } catch { /* 走査できない = 新規扱い */ }
  if (marker && marker.kiroFile) return { file: marker.kiroFile, format: marker.kiroFormat };
  return { file: path.join(agentHomeDir(root), 'agent-loop.yml'), format: 'yaml' };
}

// root 配下を maxDepth まで走査。マーカーを持つフォルダを見つけたらその配下は掘らない
// （1 フォルダ = 1 ワークスペース）。
function scanForCoworkConfigs(rootDir, maxDepth, includeLegacy = true) {
  const found = [];
  const walk = (dir, depth) => {
    const marker = detectMarkers(dir, includeLegacy);
    if (marker) { found.push(marker); return; }
    if (depth >= maxDepth) return;
    for (const name of safeList(dir)) {
      if (name.startsWith('.') || SCAN_SKIP.has(name)) continue;
      const child = path.join(dir, name);
      if (isDir(child)) walk(child, depth + 1);
    }
  };
  walk(rootDir, 0);
  return found.sort((a, b) => a.folder.localeCompare(b.folder));
}

// 走査ルートも ⚙ 設定のディストロで解決する（既定へ丸めると、設定と違う環境を走査して
// 何も見つからない／別実体を見つける）。
function resolveRoot(r, config) {
  const raw = String(r).replace(/^~(?=$|\/|\\)/, os.homedir());
  return _isPosixAbs(raw) ? toViewerPath(raw, viewerDistro(config)) : path.resolve(raw);
}

// ---------------------------------------------------------------------------
// 発見項目の生成
// ---------------------------------------------------------------------------
// 定常業務の走査ルート = 実行エンジンが担当しているプロジェクト + 設定の cowork.roots（S2）。
//
// engine 側は agent-project が管理するプロジェクト（宣言は host.yaml）。cowork.roots は
// agent-project 管理外の定常業務専用フォルダで、宣言も実行も dashboard が持つ。
// W2-4 で廃止したのは「agent-project プロジェクト一覧の二重管理」であって、
// dashboard 自身が実行するものの宣言まで持てなくする趣旨ではない。
function coworkRoots(config) {
  const cfg = (config && config.cowork) || {};
  const declared = Array.isArray(cfg.roots) ? cfg.roots : [];
  return [
    ...require('../../agent-project/main/engine').projectRoots(config),
    ...declared.map((r) => String(r || '').trim()).filter(Boolean),
    // ユーザーホームは常に対象（`~/.agents/agent-loop.yml` はどの登録簿にも載らない）。
    ...userHomeRoots(),
  ];
}

function discoverCoworkItems(config) {
  const cfg = (config && config.cowork) || {};
  if (cfg.discover === false) return [];
  const roots = coworkRoots(config);
  const scanDepth = Math.max(1, Number(cfg.scanDepth || 2));

  const items = [];
  const seenRoots = new Set();
  // ホームは**そのフォルダ自身だけ**を見る（下へ潜らない）。ホームの下には Library や
  // ドキュメントの木が広がっていて、登録フォルダと同じ深さで歩くと走査だけで数秒かかる。
  // ホームに置く定常業務は `~/.agents/agent-loop.yml` ＝ホーム直下なので、これで足りる。
  const homeKeys = new Set(userHomeRoots().map((d) => _pathKey(resolveRoot(d, config))));
  for (const r of roots) {
    if (!r) continue;
    const root = resolveRoot(r, config);
    const rk = _pathKey(root);
    if (seenRoots.has(rk)) continue;                 // 同一実体の root を二重走査しない
    seenRoots.add(rk);
    // root = 登録したフォルダ（cowork.roots または実行エンジンのプロジェクト）。
    // folder = 設定ファイル（.kiro / .statemachine）が実際にあるフォルダで、走査の深さの
    // ぶんだけ root の下にありうる。**この 2 つは役割が違う**——実行の cwd と画面での
    // 所属は登録したフォルダ（root）、プロンプト本文とログの在り処は設定のあるフォルダ
    // （repo）。同じものとして扱うと、サブフォルダに設定を置いた作業がどのフォルダの
    // 一覧にも出てこなくなる。
    const home = homeKeys.has(rk);
    for (const mk of scanForCoworkConfigs(root, home ? 0 : scanDepth, !home)) {
      const folder = mk.folder;
      const fk = _pathKey(folder);
      const smInfos = mk.smNames.map((smName) => {
        const wf = path.join(folder, '.statemachine', smName, 'workflow.yaml');
        return { smName, wf, meta: parseFlatYaml(readText(wf) || '') };
      });
      // ステートマシンを実行するだけのループエントリは対のステートマシンへ統合する
      // （同じ作業が loop と state-machine の 2 項目に割れて見えないように）。
      const pairBySm = new Map();   // smName -> { entry, idx, format }
      const pairedIdx = new Set();
      let kiro = { format: mk.kiroFormat, entries: [], texts: [] };
      if (mk.kiroFile) {
        kiro = readAgentPrompts(mk.kiroFile, mk.kiroFormat);
        if (smInfos.length) {
          kiro.entries.forEach((e, idx) => {
            const sm = pairedStateMachineOf(kiro.texts[idx], smInfos);
            if (sm && !pairBySm.has(sm.smName)) {
              pairBySm.set(sm.smName, { entry: e, idx });
              pairedIdx.add(idx);
            }
          });
        }
        kiro.entries.forEach((e, idx) => {
          if (pairedIdx.has(idx)) return;
          const name = e.name || `prompt-${idx + 1}`;
          const { schedule, scheduleKey } = scheduleOf(e);
          items.push({
            id: `disc:loop:${fk}:${name}`,
            source: 'discovered',
            type: 'loop',
            name: e.name || `定期実行 ${idx + 1}`,
            repo: folder,
            root,
            schedule,
            prompt: kiro.texts[idx] || '',
            enabled: e.enabled !== false,
            _src: {
              kind: 'loop', file: mk.kiroFile, format: kiro.format,
              repo: folder, promptIndex: idx, promptName: e.name || '', scheduleKey,
            },
          });
        });
      }
      for (const { smName, wf, meta } of smInfos) {
        const pair = pairBySm.get(smName) || null;
        const { schedule, scheduleKey } = pair ? scheduleOf(pair.entry) : { schedule: '', scheduleKey: '' };
        items.push({
          id: `disc:sm:${fk}:${smName}`,
          source: 'discovered',
          type: 'state-machine',
          name: meta.name || smName,
          repo: folder,
          root,
          workflow: smName,
          description: meta.description || '',
          schedule,
          ...(pair ? { enabled: pair.entry.enabled !== false } : {}),
          _src: {
            kind: 'statemachine', file: wf, format: 'yaml',
            repo: folder, workflowName: smName,
            ...(pair ? {
              loop: {
                file: mk.kiroFile, format: kiro.format,
                promptIndex: pair.idx, promptName: pair.entry.name || '', scheduleKey,
              },
            } : {}),
          },
        });
      }
    }
  }
  return items;
}

module.exports = {
  isDir,
  coworkRoots,
  discoverCoworkItems,
  scanForCoworkConfigs,
  detectMarkers,
  loopConfigFile,
  parseAgentLoopPrompts,
  parseAgentLoopPromptsWithLines,
  agentLoopPromptTexts,
  pairedStateMachineOf,
  resolveRoot,
  scalarValue,
  scheduleOf,
};
