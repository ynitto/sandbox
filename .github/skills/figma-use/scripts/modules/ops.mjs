// サブコマンドの中身（取得・保存）。表示と引数の解釈は figma_cli.mjs が持つ。

import fs from 'node:fs';
import path from 'node:path';
import { ApiError, EXIT } from './http.mjs';
import { NameRegistry, nodeIdToFileName, portableRelative, safeName, sniffExtension, writeFileInside, writeJsonInside } from './files.mjs';
import { normalizeEntry, toPlain } from './normalize.mjs';
import { renderDesignSummary, renderMissing, renderNodeMarkdown } from './render.mjs';

export const CLI_VERSION = '1.0.0';
const FATAL_STATUS = new Set([401, 403, 404]);

function chunk(list, size) {
  const out = [];
  for (let i = 0; i < list.length; i += size) out.push(list.slice(i, i + size));
  return out;
}

function fileMeta(data) {
  return {
    name: data.name ?? null,
    version: data.version ?? null,
    lastModified: data.lastModified ?? null,
    editorType: data.editorType ?? null,
  };
}

/** バッチに失敗が起きても全体を止めないが、認証・存在しない系は全体の失敗にする。 */
function isFatal(e) {
  return e instanceof ApiError && (FATAL_STATUS.has(e.status) || e.exitCode === EXIT.AUTH);
}

/** inspect: ファイル全体（depth 指定）か、指定ノードの浅い木を取る。 */
export async function inspect(client, { fileKey, nodeIds, depth, version }) {
  if (nodeIds.length) {
    const r = await fetchNodes(client, { fileKey, nodeIds, depth, version });
    const roots = nodeIds.filter((id) => r.entries[id]).map((id) => normalizeEntry(id, r.entries[id], { depth }).root);
    return { ...r, roots };
  }
  const res = await client.getJson(`/v1/files/${fileKey}`, { depth, version });
  const doc = res.data.document;
  const roots = (doc?.children || []).map((page) => normalizeEntry(page.id, { document: page, components: res.data.components, componentSets: res.data.componentSets, styles: res.data.styles }, { depth: Math.max(0, depth - 1) }).root);
  return { meta: fileMeta(res.data), fetchedAt: res.fetchedAt, fromCache: res.fromCache, roots, failures: [], raw: res.data };
}

/** GET /v1/files/:key/nodes をバッチで呼ぶ。 */
export async function fetchNodes(client, { fileKey, nodeIds, depth, version, batchSize = 20, expectedVersion = null }) {
  const entries = {};
  const raws = {};
  const failures = [];
  let meta = null;
  let fetchedAt = null;
  let fromCache = false;
  let lastError = null;
  for (const batch of chunk(nodeIds, batchSize)) {
    const query = { ids: batch.join(','), depth, version };
    let res;
    try {
      res = await client.getJson(`/v1/files/${fileKey}/nodes`, query);
      if (res.fromCache && expectedVersion && res.data.version && res.data.version !== expectedVersion) {
        client.log(`キャッシュの version ${res.data.version} が最新 ${expectedVersion} と違うため取り直します`);
        res = await client.getJson(`/v1/files/${fileKey}/nodes`, query, { cache: true, refresh: true });
      }
    } catch (e) {
      if (isFatal(e)) throw e;
      lastError = e;
      for (const id of batch) failures.push({ target: id, stage: 'nodes', message: `${e.message}${e.hint ? ` — ${e.hint}` : ''}` });
      continue;
    }
    meta = meta || fileMeta(res.data);
    fetchedAt = fetchedAt || res.fetchedAt;
    fromCache = fromCache || res.fromCache;
    if (res.data.err) failures.push({ target: batch.join(','), stage: 'nodes', message: res.data.err });
    for (const id of batch) {
      const entry = res.data.nodes?.[id];
      if (!entry || !entry.document) {
        failures.push({ target: id, stage: 'nodes', message: 'ノードが見つからないか、アクセスできない（応答が null）' });
        continue;
      }
      entries[id] = entry;
      raws[id] = { ...fileMeta(res.data), fetchedAt: res.fetchedAt, query, nodes: { [id]: entry } };
    }
  }
  // 1 件も取れず、原因が API の失敗なら、部分失敗ではなくその失敗として止める。
  if (!Object.keys(entries).length && lastError) throw lastError;
  return { meta: meta || { name: null, version: null, lastModified: null, editorType: null }, entries, raws, failures, fetchedAt, fromCache };
}

export async function fetchVariables(client, { fileKey }) {
  try {
    const res = await client.getJson(`/v1/files/${fileKey}/variables/local`);
    return { status: 'resolved', meta: res.data.meta || null, raw: res.data };
  } catch (e) {
    if (e instanceof ApiError && (e.status === 403 || e.status === 401)) {
      client.log('変数 API にアクセスできません（Enterprise プランと file_variables:read が必要）。バインド ID だけを残します');
      return { status: 'inaccessible', meta: null, raw: null, message: e.message };
    }
    throw e;
  }
}

/** context: 取得・正規化して raw/ と normalized/ に書く。 */
export async function context(client, opts, outDir) {
  const { fileKey, nodeIds, depth, version, variables, mapping, expectedVersion } = opts;
  const r = await fetchNodes(client, { fileKey, nodeIds, depth, version, expectedVersion });
  let vars = { status: 'not_requested', meta: null };
  const failures = [...r.failures];
  if (variables) {
    try {
      vars = await fetchVariables(client, { fileKey });
    } catch (e) {
      if (isFatal(e)) throw e;
      failures.push({ target: 'variables', stage: 'variables', message: e.message });
      vars = { status: 'failed', meta: null };
    }
    if (vars.raw) writeJsonInside(outDir, 'raw/variables-local.json', vars.raw);
  }
  const results = [];
  const artifacts = {};
  for (const id of nodeIds) {
    const entry = r.entries[id];
    if (!entry) continue;
    const base = nodeIdToFileName(id);
    const rawPath = writeJsonInside(outDir, `raw/nodes-${base}.json`, r.raws[id]);
    const result = toPlain(normalizeEntry(id, entry, { depth, variables: vars.meta, variablesStatus: vars.status === 'failed' ? 'unresolved' : vars.status, mapping }));
    const jsonPath = writeJsonInside(outDir, `normalized/${base}.json`, {
      source: { fileKey, nodeId: id, version: r.meta.version, lastModified: r.meta.lastModified, fetchedAt: r.fetchedAt },
      ...result,
    });
    const md = renderNodeMarkdown(result, {
      ...r.meta,
      fileName: r.meta.name,
      fileKey,
      fetchedAt: r.fetchedAt,
      fromCache: r.fromCache,
      depth,
      rawPath: portableRelative(outDir, rawPath),
      jsonPath: portableRelative(outDir, jsonPath),
    });
    const mdPath = writeFileInside(outDir, `normalized/${base}.md`, md);
    artifacts[id] = { raw: portableRelative(outDir, rawPath), normalized: portableRelative(outDir, jsonPath), markdown: portableRelative(outDir, mdPath) };
    results.push(result);
  }
  return { meta: r.meta, fetchedAt: r.fetchedAt, fromCache: r.fromCache, results, artifacts, failures, variablesStatus: vars.status };
}

function blobPath(client, ...parts) {
  if (!client.cacheDir) return null;
  return path.join(client.cacheDir, 'blobs', ...parts.map((p) => safeName(p)));
}

function findBlob(prefix) {
  if (!prefix) return null;
  const dir = path.dirname(prefix);
  const base = path.basename(prefix);
  try {
    const hit = fs.readdirSync(dir).find((f) => f === base || f.startsWith(`${base}.`));
    return hit ? path.join(dir, hit) : null;
  } catch {
    return null;
  }
}

function storeBlob(prefix, ext, bytes) {
  if (!prefix) return;
  fs.mkdirSync(path.dirname(prefix), { recursive: true });
  fs.writeFileSync(`${prefix}${ext}`, bytes);
}

/**
 * GET /v1/images でレンダリングし、返った URL から実ファイルを保存する。
 * version が分かっているときだけ、保存済みのファイルを再利用する（URL は期限付きなので応答は保存しない）。
 */
export async function renderImages(client, { fileKey, nodeIds, format, scale, version, outDir, subdir, names, svgOptions = {} }) {
  const saved = {};
  const failures = [];
  const registry = new NameRegistry();
  const todo = [];
  for (const id of nodeIds) {
    const fileName = registry.take(names?.[id] || nodeIdToFileName(id), `.${format}`);
    const rel = `${subdir}/${fileName}`;
    const prefix = version ? blobPath(client, fileKey, `render-${version}-${nodeIdToFileName(id)}-${scale}x-${format}${svgOptions.svg_outline_text === false ? '-text' : ''}`) : null;
    const cached = !client.refresh && findBlob(prefix);
    if (cached) {
      writeFileInside(outDir, rel, fs.readFileSync(cached));
      client.log(`保存済みの画像を使用: ${id}（version ${version}）`);
      saved[id] = rel;
    } else {
      todo.push({ id, rel, prefix });
    }
  }
  for (const batch of chunk(todo, 10)) {
    let res;
    try {
      res = await client.getJson(`/v1/images/${fileKey}`, { ids: batch.map((b) => b.id).join(','), format, scale: format === 'svg' ? undefined : scale, version, ...svgOptions }, { cache: false });
    } catch (e) {
      if (isFatal(e)) throw e;
      for (const b of batch) failures.push({ target: b.id, stage: `render-${format}`, message: `${e.message}${e.hint ? ` — ${e.hint}` : ''}` });
      continue;
    }
    if (res.data.err) failures.push({ target: batch.map((b) => b.id).join(','), stage: `render-${format}`, message: String(res.data.err) });
    for (const b of batch) {
      const url = res.data.images?.[b.id];
      if (!url) {
        failures.push({ target: b.id, stage: `render-${format}`, message: 'レンダリングできなかった（非表示・不透明度 0・存在しないノードなど。応答が null）' });
        continue;
      }
      try {
        const { bytes } = await client.download(url);
        if (!bytes.length) throw new Error('空のファイル');
        writeFileInside(outDir, b.rel, bytes);
        storeBlob(b.prefix, '', bytes);
        saved[b.id] = b.rel;
      } catch (e) {
        failures.push({ target: b.id, stage: `download-${format}`, message: `画像の保存に失敗: ${e.message}` });
      }
    }
  }
  return { saved, failures };
}

/** 画像の塗り（imageRef）を実ファイルにする。 */
export async function downloadImageFills(client, { fileKey, imageFills, outDir }) {
  const saved = {};
  const failures = [];
  const pending = [];
  for (const fill of imageFills) {
    const prefix = blobPath(client, fileKey, `fill-${fill.imageRef}`);
    const cached = !client.refresh && findBlob(prefix);
    if (cached) {
      const rel = `assets/images/${safeName(fill.imageRef)}${path.extname(cached)}`;
      writeFileInside(outDir, rel, fs.readFileSync(cached));
      saved[fill.imageRef] = rel;
    } else {
      pending.push({ fill, prefix });
    }
  }
  if (!pending.length) return { saved, failures };
  let urls = {};
  try {
    const res = await client.getJson(`/v1/files/${fileKey}/images`, {}, { cache: false });
    urls = res.data.meta?.images || res.data.images || {};
  } catch (e) {
    if (isFatal(e)) throw e;
    for (const p of pending) failures.push({ target: p.fill.imageRef, stage: 'image-fills', message: e.message });
    return { saved, failures };
  }
  for (const { fill, prefix } of pending) {
    const url = urls[fill.imageRef];
    if (!url) {
      failures.push({ target: fill.imageRef, stage: 'image-fills', message: 'imageRef に対応する URL が応答にない' });
      continue;
    }
    try {
      const { bytes, contentType } = await client.download(url);
      const ext = sniffExtension(bytes, contentType);
      const rel = `assets/images/${safeName(fill.imageRef)}${ext}`;
      writeFileInside(outDir, rel, bytes);
      storeBlob(prefix, ext, bytes);
      saved[fill.imageRef] = rel;
    } catch (e) {
      failures.push({ target: fill.imageRef, stage: 'image-fills', message: `画像の保存に失敗: ${e.message}` });
    }
  }
  return { saved, failures };
}

export const SVG_NOTES = [
  'SVG は Figma のレンダリング結果で、元のレイヤー構造やコンポーネントの意味は残らない。',
  '文字は既定でアウトライン化される（svg_outline_text）。文字をテキストのまま扱いたいときは --svg-keep-text。',
  '色は直値で書かれる。テーマで色を変えるなら fill/stroke を currentColor などへ置き換える判断が要る。',
  'コードへ埋め込む前に、不要な id・クリップパス・サイズ属性を整理し、外部から来た SVG として中身（script・外部参照）を確認する。',
];

/** assets: 画像の塗りと、指定ノードの SVG を保存して対応表を書く。 */
export async function assets(client, { fileKey, results, svgIds, svgKeepText, version, outDir, nodeIndex }) {
  const imageFills = mergeImageFills(results);
  const fills = await downloadImageFills(client, { fileKey, imageFills, outDir });
  const names = {};
  for (const id of svgIds) names[id] = `${safeName(nodeIndex.get(id)?.name || 'node')}--${nodeIdToFileName(id)}`;
  const svg = svgIds.length
    ? await renderImages(client, { fileKey, nodeIds: svgIds, format: 'svg', scale: 1, version, outDir, subdir: 'assets/svg', names, svgOptions: svgKeepText ? { svg_outline_text: false } : {} })
    : { saved: {}, failures: [] };
  const map = {
    imageFills: imageFills.map((f) => ({ imageRef: f.imageRef, nodeIds: f.nodeIds, scaleMode: f.scaleMode ?? null, file: fills.saved[f.imageRef] ?? null })),
    svg: svgIds.map((id) => ({ nodeId: id, name: nodeIndex.get(id)?.name ?? null, file: svg.saved[id] ?? null })),
    svgNotes: SVG_NOTES,
  };
  writeJsonInside(outDir, 'assets/assets-map.json', map);
  return { map, failures: [...fills.failures, ...svg.failures] };
}

export function mergeImageFills(results) {
  const byRef = new Map();
  for (const r of results) {
    for (const f of r.facts.imageFills) {
      const prev = byRef.get(f.imageRef) || { imageRef: f.imageRef, nodeIds: [], scaleMode: f.scaleMode };
      for (const id of f.nodeIds) if (!prev.nodeIds.includes(id)) prev.nodeIds.push(id);
      byRef.set(f.imageRef, prev);
    }
  }
  return [...byRef.values()];
}

export function indexNodes(results) {
  const index = new Map();
  const walk = (n) => {
    index.set(n.id, n);
    (n.children || []).forEach(walk);
  };
  results.forEach((r) => walk(r.root));
  return index;
}

/** export: context・screenshot・assets をまとめ、manifest と要約を書く。 */
export async function exportAll(client, opts, outDir) {
  const ctx = await context(client, opts, outDir);
  const failures = [...ctx.failures];
  const okIds = ctx.results.map((r) => r.nodeId);
  const version = opts.version || ctx.meta.version;
  const shots = okIds.length
    ? await renderImages(client, { fileKey: opts.fileKey, nodeIds: okIds, format: opts.format, scale: opts.scale, version, outDir, subdir: 'screenshots' })
    : { saved: {}, failures: [] };
  failures.push(...shots.failures);
  const nodeIndex = indexNodes(ctx.results);
  let svgIds = [...opts.svgIds];
  if (opts.svgCandidates) {
    for (const r of ctx.results) for (const c of r.facts.svgCandidates) if (!svgIds.includes(c.id)) svgIds.push(c.id);
    svgIds = svgIds.slice(0, opts.svgLimit);
  }
  const unknownSvg = svgIds.filter((id) => !nodeIndex.has(id));
  for (const id of unknownSvg) client.log(`SVG 対象 ${id} は取得した範囲の外です（名前は未取得のまま書き出します）`);
  const assetResult = await assets(client, { fileKey: opts.fileKey, results: ctx.results, svgIds, svgKeepText: opts.svgKeepText, version, outDir, nodeIndex });
  failures.push(...assetResult.failures);

  const byNode = {};
  for (const id of okIds) byNode[id] = { ...ctx.artifacts[id], screenshot: shots.saved[id] ?? null };
  const assetFiles = [
    ...assetResult.map.imageFills.filter((f) => f.file).map((f) => f.file),
    ...assetResult.map.svg.filter((s) => s.file).map((s) => s.file),
  ];
  const manifest = {
    tool: 'figma-use',
    cliVersion: CLI_VERSION,
    generatedAt: new Date().toISOString(),
    status: failures.length ? 'partial' : 'complete',
    source: { url: opts.sourceUrl ?? null, fileKey: opts.fileKey, mainFileKey: opts.mainFileKey ?? opts.fileKey, branchKey: opts.branchKey ?? null, nodeIds: opts.nodeIds, requestedVersion: opts.version ?? null, depth: opts.depth ?? null },
    file: ctx.meta,
    cache: { used: ctx.fromCache || client.cacheHits.length > 0, hits: client.cacheHits, nodesFetchedAt: ctx.fetchedAt },
    variables: ctx.variablesStatus,
    apiCalls: client.calls,
    artifacts: {
      byNode,
      assets: assetFiles,
      assetsMap: 'assets/assets-map.json',
      summary: 'design-summary.md',
      missing: 'missing-information.md',
      ...(fs.existsSync(path.join(outDir, 'raw/variables-local.json')) ? { variables: 'raw/variables-local.json' } : {}),
    },
    failures,
  };
  writeJsonInside(outDir, 'manifest.json', manifest);
  writeFileInside(outDir, 'design-summary.md', renderDesignSummary(manifest, ctx.results));
  writeFileInside(outDir, 'missing-information.md', renderMissing(manifest, ctx.results));
  return { manifest, results: ctx.results };
}
