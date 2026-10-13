#!/usr/bin/env node
// figma-use: Figma REST API からデザイン情報を取り、正規化して保存する CLI。
// Node.js 24 の標準モジュールだけで動く。React の設計はしない（事実の取得と変換だけ）。

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { connectionsWritePath, DEFAULT_API_BASE, resolveCredentials } from './lib/config.mjs';
import { ApiError, EXIT, FigmaClient } from './lib/http.mjs';
import { buildMapping } from './lib/mapping.mjs';
import { CLI_VERSION, assets, context, exportAll, indexNodes, inspect, renderImages } from './lib/ops.mjs';
import { renderInspect } from './lib/render.mjs';
import { InputError, normalizeNodeIds, parseFigmaInput, sanitizeSourceUrl } from './lib/url.mjs';

const MIN_NODE = 22;
const TARGET_NODE = 24;

const COMMON = {
  label: { type: 'string', default: 'default' },
  'cache-dir': { type: 'string' },
  refresh: { type: 'boolean', default: false },
  'check-update': { type: 'boolean', default: false },
  timeout: { type: 'string', default: '60' },
  retries: { type: 'string', default: '3' },
  'file-version': { type: 'string' },
  help: { type: 'boolean', short: 'h', default: false },
};

const NODE_OPTS = { node: { type: 'string', multiple: true, default: [] } };
const OUT_OPT = { out: { type: 'string', default: 'figma-output' } };
const CONTEXT_OPTS = {
  depth: { type: 'string' },
  variables: { type: 'boolean', default: false },
  mapping: { type: 'string' },
  'code-connect-dir': { type: 'string' },
};
const SVG_OPTS = {
  svg: { type: 'string', multiple: true, default: [] },
  'svg-candidates': { type: 'boolean', default: false },
  'svg-limit': { type: 'string', default: '50' },
  'svg-keep-text': { type: 'boolean', default: false },
};

const COMMANDS = {
  doctor: {
    summary: '実行環境・認証情報の有無・キャッシュの置き場を確認する（認証情報は表示しない）',
    usage: 'doctor [--network <Figma URL | file key>] [--label <名前>]',
    options: { label: COMMON.label, 'cache-dir': COMMON['cache-dir'], network: { type: 'string' }, timeout: COMMON.timeout, help: COMMON.help },
    details: ['--network を付けたときだけ API を 1 回呼ぶ（GET /v1/files/:key?depth=1）。'],
  },
  inspect: {
    summary: 'ページ・フレーム・ノードの ID・名前・種類を一覧にする',
    usage: 'inspect <Figma URL | file key> [--node <id>]... [--depth 2] [--json] [--out <file.md>]',
    options: { ...COMMON, ...NODE_OPTS, depth: { type: 'string', default: '2' }, json: { type: 'boolean', default: false }, out: { type: 'string' } },
    details: ['node を指定しなければファイル全体を depth まで、指定すればそのノードの下を depth まで取る。'],
  },
  context: {
    summary: '指定ノードと子孫を取り、生 JSON・正規化 JSON・Markdown を保存する',
    usage: 'context <Figma URL | file key> [--node <id>]... [--depth N] [--variables] [--mapping <file.json>] [--code-connect-dir <dir>] [--out figma-output]',
    options: { ...COMMON, ...NODE_OPTS, ...OUT_OPT, ...CONTEXT_OPTS },
    details: [
      '複数ノードはまとめて取得する（1 回の API 呼び出しに 20 ノードまで）。',
      '--variables: 変数の値を解決する（Enterprise プランと file_variables:read が必要。使えなければバインド ID だけ残す）。',
      '--mapping / --code-connect-dir: コンポーネントとコード側の部品の対応を付ける。',
    ],
  },
  screenshot: {
    summary: '指定ノードを画像にして保存する（API が返す一時 URL から実ファイルまで取る）',
    usage: 'screenshot <Figma URL | file key> [--node <id>]... [--format png|jpg|svg|pdf] [--scale 2] [--out figma-output]',
    options: { ...COMMON, ...NODE_OPTS, ...OUT_OPT, format: { type: 'string', default: 'png' }, scale: { type: 'string', default: '2' } },
    details: ['scale は 0.01〜4。32 メガピクセルを超える画像は Figma 側で縮小される。'],
  },
  assets: {
    summary: '画像の塗り（imageRef）と、指定したアイコン・ベクターの SVG を保存し、対応表を作る',
    usage: 'assets <Figma URL | file key> [--node <id>]... [--svg <id>]... [--svg-candidates] [--svg-keep-text] [--out figma-output]',
    options: { ...COMMON, ...NODE_OPTS, ...OUT_OPT, ...SVG_OPTS, depth: { type: 'string' } },
    details: ['--svg-candidates: ベクターだけでできたノード（アイコン候補）をまとめて SVG にする（--svg-limit 件まで）。'],
  },
  export: {
    summary: '構造・スクリーンショット・素材・要約・不足情報を一括で保存する',
    usage: 'export <Figma URL | file key> [--node <id>]... [--depth N] [--svg <id>]... [--svg-candidates] [--variables] [--mapping <file.json>] [--out figma-output]',
    options: { ...COMMON, ...NODE_OPTS, ...OUT_OPT, ...CONTEXT_OPTS, ...SVG_OPTS, format: { type: 'string', default: 'png' }, scale: { type: 'string', default: '2' } },
    details: ['出力: manifest.json / raw/ / normalized/ / screenshots/ / assets/ / design-summary.md / missing-information.md'],
  },
};

const COMMON_HELP = [
  '共通オプション:',
  '  --label <名前>      connections.yaml の figma エントリのラベル（既定 default）',
  '  --cache-dir <dir>   キャッシュの置き場（既定は OS のキャッシュフォルダ。doctor で確認）',
  '  --refresh           キャッシュを使わず API から取り直す',
  '  --check-update      キャッシュを使う前に最新 version を確認する（GET /v1/files/:key/meta を 1 回呼ぶ。file_metadata:read が必要）',
  '  --file-version <id> ファイルの特定 version を取る（既定は最新）',
  '  --timeout <秒>      1 回の HTTP 呼び出しの上限（既定 60）',
  '  --retries <回>      429・5xx・通信エラーの再試行回数（既定 3）',
];

const EXIT_HELP = [
  '終了コード: 0 成功 / 1 エラー / 2 引数の誤り / 3 認証・権限 / 4 見つからない / 5 レート制限 / 6 一部失敗 / 7 通信・サーバー',
];

function mainHelp() {
  return [
    `figma-use ${CLI_VERSION} — Figma REST API からデザイン情報を取得・正規化する（Figma への書き込みはしない）`,
    '',
    '使い方: node figma_cli.mjs <コマンド> [引数]',
    '',
    'コマンド:',
    ...Object.entries(COMMANDS).map(([name, c]) => `  ${name.padEnd(11)} ${c.summary}`),
    '',
    '認証: connections.yaml の figma エントリ（token）か、環境変数 FIGMA_TOKEN。トークンは引数で渡せない。',
    '各コマンドの詳細: node figma_cli.mjs <コマンド> --help',
    '',
    ...EXIT_HELP,
  ].join('\n');
}

function commandHelp(name) {
  const c = COMMANDS[name];
  return [`使い方: node figma_cli.mjs ${c.usage}`, '', c.summary, '', ...c.details, '', ...(name === 'doctor' ? [] : COMMON_HELP), '', ...EXIT_HELP].join('\n');
}

export function defaultCacheDir(env = process.env, platform = process.platform) {
  if (env.FIGMA_USE_CACHE_DIR) return env.FIGMA_USE_CACHE_DIR;
  if (platform === 'win32') {
    const base = env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local');
    return path.win32.join(base, 'figma-use', 'cache');
  }
  const base = env.XDG_CACHE_HOME || path.join(os.homedir(), '.cache');
  return path.join(base, 'figma-use');
}

function toInt(value, name, { min, max }) {
  const n = Number(value);
  if (!Number.isFinite(n) || n < min || n > max) throw new InputError(`--${name} は ${min}〜${max} で指定してください: ${value}`);
  return n;
}

class Output {
  constructor(secret) {
    this.secret = secret;
  }

  redact(text) {
    const s = String(text);
    return this.secret ? s.split(this.secret).join('***') : s;
  }

  out(text) {
    process.stdout.write(`${this.redact(text)}\n`);
  }

  err(text) {
    process.stderr.write(`${this.redact(text)}\n`);
  }
}

function buildClient(values, creds, io) {
  return new FigmaClient({
    token: creds.token,
    apiBase: creds.apiBase,
    cacheDir: values['cache-dir'] || defaultCacheDir(),
    refresh: values.refresh,
    timeoutMs: toInt(values.timeout, 'timeout', { min: 1, max: 600 }) * 1000,
    maxRetries: toInt(values.retries, 'retries', { min: 0, max: 10 }),
    backoffMs: Number(process.env.FIGMA_USE_BACKOFF_MS ?? 1000),
    log: (m) => io.err(m),
  });
}

function resolveTarget(positionals, values) {
  if (!positionals[0]) throw new InputError('Figma の URL か file key を指定してください');
  if (positionals.length > 1) throw new InputError(`引数が多すぎます: ${positionals.slice(1).join(' ')}（URL は引用符で囲んでください）`);
  const parsed = parseFigmaInput(positionals[0]);
  const nodeIds = normalizeNodeIds([...parsed.nodeIds, ...(values.node || [])]);
  return { ...parsed, nodeIds, sourceUrl: parsed.kind === 'key' ? null : sanitizeSourceUrl(positionals[0]) };
}

async function expectedVersion(client, values, fileKey) {
  if (!values['check-update'] || values.refresh || values['file-version']) return null;
  const res = await client.getJson(`/v1/files/${fileKey}/meta`, {}, { cache: false });
  const v = res.data.file?.version ?? res.data.version ?? null;
  client.log(`最新の version: ${v ?? '不明'}`);
  return v;
}

function requireNodes(target) {
  if (!target.nodeIds.length) {
    throw new InputError('対象ノードがありません。node-id 付きの URL を渡すか --node で指定してください（一覧は inspect で確認）');
  }
}

function printFailures(io, failures) {
  if (!failures.length) return;
  io.err(`一部の取得に失敗しました（${failures.length} 件）:`);
  for (const f of failures) io.err(`  - ${f.target} [${f.stage}]: ${f.message}`);
}

async function cmdDoctor(values, io) {
  const lines = [];
  let bad = false;
  const major = Number(process.versions.node.split('.')[0]);
  if (major >= TARGET_NODE) lines.push(`[OK] Node.js ${process.versions.node}`);
  else if (major >= MIN_NODE) lines.push(`[注意] Node.js ${process.versions.node}（動作対象は ${TARGET_NODE} 以上）`);
  else {
    lines.push(`[NG] Node.js ${process.versions.node}（${TARGET_NODE} 以上が必要）`);
    bad = true;
  }
  lines.push(`[情報] OS: ${process.platform}${process.env.WSL_DISTRO_NAME ? `（WSL: ${process.env.WSL_DISTRO_NAME}）` : ''}`);
  const creds = resolveCredentials({ label: values.label });
  io.secret = creds.token;
  if (creds.token) lines.push(`[OK] 認証情報: 設定あり（${creds.source}）。値は表示しません`);
  else {
    lines.push('[NG] 認証情報: 見つかりません（connections.yaml の figma エントリか環境変数 FIGMA_TOKEN を設定してください）');
    bad = true;
  }
  for (const n of creds.notes) lines.push(`[注意] ${n}`);
  lines.push(`[情報] 読んだ connections.yaml: ${creds.files.length ? creds.files.join(', ') : 'なし'}`);
  lines.push(`[情報] connections.yaml を置く場所（ワークスペース優先）: ${connectionsWritePath()}`);
  if (creds.apiBase !== DEFAULT_API_BASE) lines.push(`[注意] API の接続先を変更しています: ${creds.apiBase}`);
  const proxy = process.env.HTTPS_PROXY || process.env.https_proxy;
  if (proxy && process.env.NODE_USE_ENV_PROXY !== '1') {
    lines.push('[注意] HTTPS_PROXY が設定されていますが、Node.js はそのままではプロキシを使いません。経由させるには NODE_USE_ENV_PROXY=1 を設定してください');
  }
  const cacheDir = values['cache-dir'] || defaultCacheDir();
  lines.push(`[情報] キャッシュ: ${cacheDir}${fs.existsSync(cacheDir) ? '' : '（未作成）'}`);
  if (values.network) {
    const target = parseFigmaInput(values.network);
    const client = buildClient({ ...values, refresh: true, retries: '1' }, creds, io);
    try {
      const res = await client.getJson(`/v1/files/${target.fileKey}`, { depth: 1 }, { cache: false });
      lines.push(`[OK] API 疎通: 「${res.data.name}」version ${res.data.version ?? '不明'} / editorType ${res.data.editorType ?? '不明'}`);
    } catch (e) {
      lines.push(`[NG] API 疎通: ${e.message}${e.hint ? ` — ${e.hint}` : ''}`);
      bad = true;
    }
  } else {
    lines.push('[情報] API 疎通は未確認（確かめるには --network <Figma URL>。API を 1 回呼ぶ）');
  }
  for (const l of lines) io.out(l);
  if (!bad) return EXIT.OK;
  return creds.token ? EXIT.ERROR : EXIT.AUTH;
}

async function cmdInspect(values, positionals, io, creds) {
  const target = resolveTarget(positionals, values);
  const depth = toInt(values.depth, 'depth', { min: 1, max: 20 });
  const client = buildClient(values, creds, io);
  const r = await inspect(client, { fileKey: target.fileKey, nodeIds: target.nodeIds, depth, version: values['file-version'] });
  if (values.json) {
    io.out(JSON.stringify({ fileKey: target.fileKey, file: r.meta, fetchedAt: r.fetchedAt, fromCache: r.fromCache, depth, roots: r.roots, failures: r.failures }, null, 2));
  } else {
    const md = renderInspect(r.roots, { ...r.meta, fileName: r.meta.name, fileKey: target.fileKey, fetchedAt: r.fetchedAt, fromCache: r.fromCache, depth });
    if (values.out) {
      fs.mkdirSync(path.dirname(path.resolve(values.out)), { recursive: true });
      fs.writeFileSync(values.out, md);
      io.out(`保存しました: ${values.out}`);
    } else io.out(md);
  }
  printFailures(io, r.failures);
  return r.failures.length ? EXIT.PARTIAL : EXIT.OK;
}

function contextOptions(values, target) {
  const { mapping, notes } = buildMapping({ mappingFile: values.mapping, codeConnectDir: values['code-connect-dir'] });
  return {
    fileKey: target.fileKey,
    mainFileKey: target.mainFileKey,
    branchKey: target.branchKey,
    sourceUrl: target.sourceUrl,
    nodeIds: target.nodeIds,
    depth: values.depth !== undefined ? toInt(values.depth, 'depth', { min: 1, max: 50 }) : undefined,
    version: values['file-version'],
    variables: values.variables,
    mapping,
    mappingNotes: notes,
  };
}

async function cmdContext(values, positionals, io, creds) {
  const target = resolveTarget(positionals, values);
  requireNodes(target);
  const client = buildClient(values, creds, io);
  const opts = contextOptions(values, target);
  opts.mappingNotes.forEach((n) => io.err(n));
  opts.expectedVersion = await expectedVersion(client, values, target.fileKey);
  const outDir = path.resolve(values.out);
  const r = await context(client, opts, outDir);
  io.out(`取得: 「${r.meta.name ?? '?'}」version ${r.meta.version ?? '応答になし'}${r.fromCache ? `（キャッシュ: ${r.fetchedAt}）` : ''}`);
  for (const [id, a] of Object.entries(r.artifacts)) io.out(`  ${id}: ${path.join(values.out, a.markdown)}`);
  printFailures(io, r.failures);
  return r.failures.length ? EXIT.PARTIAL : EXIT.OK;
}

const FORMATS = new Set(['png', 'jpg', 'svg', 'pdf']);

function imageOptions(values) {
  const format = values.format.toLowerCase();
  if (!FORMATS.has(format)) throw new InputError(`--format は png / jpg / svg / pdf のどれかです: ${values.format}`);
  return { format, scale: toInt(values.scale, 'scale', { min: 0.01, max: 4 }) };
}

async function cmdScreenshot(values, positionals, io, creds) {
  const target = resolveTarget(positionals, values);
  requireNodes(target);
  const { format, scale } = imageOptions(values);
  const client = buildClient(values, creds, io);
  const outDir = path.resolve(values.out);
  const r = await renderImages(client, { fileKey: target.fileKey, nodeIds: target.nodeIds, format, scale, version: values['file-version'], outDir, subdir: 'screenshots' });
  for (const [id, rel] of Object.entries(r.saved)) io.out(`  ${id}: ${path.join(values.out, rel)}`);
  printFailures(io, r.failures);
  if (!Object.keys(r.saved).length) return EXIT.ERROR;
  return r.failures.length ? EXIT.PARTIAL : EXIT.OK;
}

async function cmdAssets(values, positionals, io, creds) {
  const target = resolveTarget(positionals, values);
  requireNodes(target);
  const client = buildClient(values, creds, io);
  const outDir = path.resolve(values.out);
  const opts = contextOptions(values, target);
  const ctx = await context(client, opts, outDir);
  const nodeIndex = indexNodes(ctx.results);
  let svgIds = normalizeNodeIds(values.svg);
  if (values['svg-candidates']) {
    for (const r of ctx.results) for (const c of r.facts.svgCandidates) if (!svgIds.includes(c.id)) svgIds.push(c.id);
    svgIds = svgIds.slice(0, toInt(values['svg-limit'], 'svg-limit', { min: 1, max: 500 }));
  }
  const r = await assets(client, { fileKey: target.fileKey, results: ctx.results, svgIds, svgKeepText: values['svg-keep-text'], version: values['file-version'] || ctx.meta.version, outDir, nodeIndex });
  io.out(`画像の塗り: ${r.map.imageFills.filter((f) => f.file).length}/${r.map.imageFills.length} 件 / SVG: ${r.map.svg.filter((s) => s.file).length}/${r.map.svg.length} 件`);
  io.out(`対応表: ${path.join(values.out, 'assets', 'assets-map.json')}`);
  const failures = [...ctx.failures, ...r.failures];
  printFailures(io, failures);
  return failures.length ? EXIT.PARTIAL : EXIT.OK;
}

async function cmdExport(values, positionals, io, creds) {
  const target = resolveTarget(positionals, values);
  requireNodes(target);
  const client = buildClient(values, creds, io);
  const opts = {
    ...contextOptions(values, target),
    ...imageOptions(values),
    svgIds: normalizeNodeIds(values.svg),
    svgCandidates: values['svg-candidates'],
    svgLimit: toInt(values['svg-limit'], 'svg-limit', { min: 1, max: 500 }),
    svgKeepText: values['svg-keep-text'],
  };
  opts.mappingNotes.forEach((n) => io.err(n));
  opts.expectedVersion = await expectedVersion(client, values, target.fileKey);
  const outDir = path.resolve(values.out);
  const { manifest } = await exportAll(client, opts, outDir);
  io.out(`保存しました: ${values.out}（${manifest.status === 'complete' ? 'すべて取得' : '一部失敗'} / API 呼び出し ${manifest.apiCalls} 回）`);
  io.out(`  要約: ${path.join(values.out, 'design-summary.md')}`);
  io.out(`  不足情報: ${path.join(values.out, 'missing-information.md')}`);
  printFailures(io, manifest.failures);
  return manifest.failures.length ? EXIT.PARTIAL : EXIT.OK;
}

const HANDLERS = { inspect: cmdInspect, context: cmdContext, screenshot: cmdScreenshot, assets: cmdAssets, export: cmdExport };

export async function main(argv) {
  const io = new Output(null);
  const [name, ...rest] = argv;
  if (!name || name === '--help' || name === '-h' || name === 'help') {
    io.out(mainHelp());
    return name ? EXIT.OK : EXIT.USAGE;
  }
  if (name === '--version') {
    io.out(CLI_VERSION);
    return EXIT.OK;
  }
  const command = COMMANDS[name];
  if (!command) {
    io.err(`不明なコマンドです: ${name}\n\n${mainHelp()}`);
    return EXIT.USAGE;
  }
  let parsed;
  try {
    parsed = parseArgs({ args: rest, options: command.options, allowPositionals: true, strict: true });
  } catch (e) {
    io.err(`${e.message}\n\n${commandHelp(name)}`);
    return EXIT.USAGE;
  }
  const { values, positionals } = parsed;
  if (values.help) {
    io.out(commandHelp(name));
    return EXIT.OK;
  }
  const major = Number(process.versions.node.split('.')[0]);
  if (major < MIN_NODE) {
    io.err(`Node.js ${TARGET_NODE} 以上が必要です（現在 ${process.versions.node}）`);
    return EXIT.ERROR;
  }
  try {
    if (name === 'doctor') return await cmdDoctor(values, io);
    const creds = resolveCredentials({ label: values.label });
    io.secret = creds.token;
    return await HANDLERS[name](values, positionals, io, creds);
  } catch (e) {
    if (e instanceof InputError) {
      io.err(`エラー: ${e.message}`);
      return EXIT.USAGE;
    }
    if (e instanceof ApiError) {
      io.err(`エラー: ${e.message}`);
      if (e.hint) io.err(`対処: ${e.hint}`);
      return e.exitCode;
    }
    io.err(`エラー: ${e?.stack || e}`);
    return EXIT.ERROR;
  }
}

const invokedDirectly = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (invokedDirectly) {
  main(process.argv.slice(2)).then((code) => {
    process.exitCode = code;
  });
}
