'use strict';

// Cowork の自動発見。全体設定の `projects.roots` 配下を走査し、kiro-loop の定期処理
// （.kiro/kiro-loop.{yaml,yml,json} の prompts[]）とステートマシン（.statemachine/<name>/
// workflow.yaml）を **ジョブ単位** で抽出して Cowork 項目にする。
//
// dashboard には YAML ライブラリが無いため、prompts の抽出は行指向の限定パーサで行う
// （ブロックスカラ `prompt: |` やコメントは「厳密に field 列の key: value 行だけ読む」規則で
// 構造的に無視される）。.json は JSON.parse で読む。
//
// 発見時に各フィールドの行番号（parseKiroLoopPromptsWithLines）と scheduleKey を記録し、
// 保存時の外科的書き戻し（writeback.js）が「読んだ物理フィールドへそのまま書く」ためのアンカーにする。

const fs = require('fs');
const os = require('os');
const path = require('path');
const {
  toViewerPath, _isPosixAbs, _pathKey,
} = require('../../agent-project/main/project');
const { parseFlatYaml } = require('../../agent-project/main/toolconfig');

// 走査を軽く保つためのスキップ（プロジェクト内部の既知/生成物ディレクトリ）。隠しフォルダは
// 別途 name.startsWith('.') で降下対象から外す（マーカーの .kiro/.statemachine は「降りる」の
// ではなく候補フォルダ内を「プローブ」して見つける）。
const SCAN_SKIP = new Set([
  'node_modules', 'dist', 'release', 'build', 'out', 'coverage', 'bus', 'work',
  'archive', 'flow-archive', 'backlog', 'needs', 'decisions', 'commands', 'inbox',
  'claims', 'autonomy', 'charters', 'runs', 'vendor', 'target',
]);

const KIRO_CONFIG_NAMES = [
  ['kiro-loop.yaml', 'yaml'],
  ['kiro-loop.yml', 'yaml'],
  ['kiro-loop.json', 'json'],
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
// kiro-loop prompts パーサ（行指向）
// ---------------------------------------------------------------------------
const FIELD_KEYS = new Set(['name', 'interval_minutes', 'cron', 'enabled', 'prompt']);

// prompts: リストのエントリを、各フィールドの行番号付きで返す。
//   entry = { index, dashLine, fieldIndent, fields: { <key>: { line, rawVal } } }
// fields は FIELD_KEYS のうち実在するものだけを持つ。ブロックスカラ本文・ネストマップ・
// コメントは fieldIndent 列に一致しないため field として拾わない。
function parseKiroLoopPromptsWithLines(text) {
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
    // トップレベルの新キー（prompts と同じ列 0 のスカラ）に達したら prompts リスト終了
    if (indent === 0) break;
    if (cur && indent === cur.fieldIndent) readField(raw.slice(indent), j);
    // それ以外（ブロックスカラ本文・ネスト）は無視
  }
  return entries;
}

// エントリごとの prompt 本文（インラインスカラ or ブロックスカラ `prompt: |`）を返す。
// 書き戻しには使わない読み取り専用（ステートマシン対エントリの検出用）。
function kiroLoopPromptTexts(text) {
  const norm = String(text == null ? '' : text);
  const lines = norm.split('\n');
  return parseKiroLoopPromptsWithLines(norm).map((e) => {
    const f = e.fields.prompt;
    if (!f) return '';
    const raw = String(f.rawVal == null ? '' : f.rawVal).trim();
    if (!/^[|>][+-]?\d*\s*(#.*)?$/.test(raw)) return scalarValue(f.rawVal);
    // ブロックスカラ: fieldIndent より深い行を本文として読む（浅い行で終了）
    const out = [];
    for (let i = f.line + 1; i < lines.length; i += 1) {
      const l = lines[i];
      if (/^\s*$/.test(l)) { out.push(''); continue; }
      const indent = l.length - l.replace(/^\s+/, '').length;
      if (indent <= e.fieldIndent) break;
      out.push(l.trim());
    }
    return out.join('\n').trim();
  });
}

// 書き戻し不要な読み取り用: {name, interval_minutes, cron, enabled} の素の値。
function parseKiroLoopPrompts(text) {
  return parseKiroLoopPromptsWithLines(text).map((e) => {
    const out = {};
    if (e.fields.name) out.name = scalarValue(e.fields.name.rawVal);
    if (e.fields.cron) out.cron = scalarValue(e.fields.cron.rawVal);
    if (e.fields.interval_minutes) {
      const n = parseInt(scalarValue(e.fields.interval_minutes.rawVal), 10);
      if (!Number.isNaN(n)) out.interval_minutes = n;
    }
    if (e.fields.enabled) {
      const v = scalarValue(e.fields.enabled.rawVal).toLowerCase();
      if (v === 'true') out.enabled = true;
      else if (v === 'false') out.enabled = false;
    }
    return out;
  });
}

// .json / .yaml いずれかから prompts エントリ（素の値）・prompt 本文・format を返す。
function readKiroPrompts(file, format) {
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
  return { format, entries: parseKiroLoopPrompts(text), texts: kiroLoopPromptTexts(text) };
}

// kiro-loop の prompt 本文がステートマシン実行の対エントリ（「xxx ステートマシンを実行して」等）
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
function detectMarkers(dir) {
  let kiroFile = null;
  let kiroFormat = null;
  for (const [name, fmt] of KIRO_CONFIG_NAMES) {
    // workspace固有の .kiro/ を優先しつつ、kiro-loop 本体の公式探索順にある
    // カレントディレクトリ直下の kiro-loop.yaml/.yml/.json も受け入れる。
    for (const f of [path.join(dir, '.kiro', name), path.join(dir, name)]) {
      try {
        if (fs.statSync(f).isFile()) { kiroFile = f; kiroFormat = fmt; break; }
      } catch { /* not present */ }
    }
    if (kiroFile) break;
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

// root 配下を maxDepth まで走査。マーカーを持つフォルダを見つけたらその配下は掘らない
// （1 フォルダ = 1 ワークスペース）。
function scanForCoworkConfigs(rootDir, maxDepth) {
  const found = [];
  const walk = (dir, depth) => {
    const marker = detectMarkers(dir);
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

function resolveRoot(r) {
  const raw = String(r).replace(/^~(?=$|\/|\\)/, os.homedir());
  return _isPosixAbs(raw) ? toViewerPath(raw) : path.resolve(raw);
}

// ---------------------------------------------------------------------------
// 発見項目の生成
// ---------------------------------------------------------------------------
function discoverCoworkItems(config) {
  const cfg = (config && config.cowork) || {};
  if (cfg.discover === false) return [];
  const roots = (config && config.projects && config.projects.roots) || [];
  const scanDepth = Math.max(
    1,
    Number(cfg.scanDepth || (config && config.projects && config.projects.scanDepth) || 2)
  );

  const items = [];
  const seenRoots = new Set();
  for (const r of roots) {
    if (!r) continue;
    const root = resolveRoot(r);
    const rk = _pathKey(root);
    if (seenRoots.has(rk)) continue;                 // 同一実体の root を二重走査しない
    seenRoots.add(rk);
    for (const mk of scanForCoworkConfigs(root, scanDepth)) {
      const folder = mk.folder;
      const fk = _pathKey(folder);
      const smInfos = mk.smNames.map((smName) => {
        const wf = path.join(folder, '.statemachine', smName, 'workflow.yaml');
        return { smName, wf, meta: parseFlatYaml(readText(wf) || '') };
      });
      // ステートマシンを実行するだけの kiro-loop エントリは対のステートマシンへ統合する
      // （同じ作業が loop と state-machine の 2 項目に割れて見えないように）。
      const pairBySm = new Map();   // smName -> { entry, idx, format }
      const pairedIdx = new Set();
      let kiro = { format: mk.kiroFormat, entries: [], texts: [] };
      if (mk.kiroFile) {
        kiro = readKiroPrompts(mk.kiroFile, mk.kiroFormat);
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
            schedule,
            enabled: e.enabled !== false,
            _src: {
              kind: 'kiro-loop', file: mk.kiroFile, format: kiro.format,
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
  discoverCoworkItems,
  scanForCoworkConfigs,
  detectMarkers,
  parseKiroLoopPrompts,
  parseKiroLoopPromptsWithLines,
  kiroLoopPromptTexts,
  pairedStateMachineOf,
  resolveRoot,
  scalarValue,
  scheduleOf,
};
