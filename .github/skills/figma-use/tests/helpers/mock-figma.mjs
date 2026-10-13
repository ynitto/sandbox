// テスト用の Figma REST API の模造。実 API は呼ばない。
// fixtures/design-file.json の木から、files・nodes・images・image fills・variables・meta の応答を作る。

import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const FIXTURES = path.join(HERE, '..', 'fixtures');
export const FILE_KEY = 'AbCdEfGhIjKlMnOpQrStUv';
export const TOKEN = 'figd_TEST-SECRET-TOKEN-should-never-appear';

// 1x1 の PNG と最小の JPEG（先頭バイトで種類を判定させる）。
export const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');
export const JPEG = Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0x00, 0x10, 0x4a, 0x46, 0x49, 0x46, 0x00, 0x01, 0xff, 0xd9]);

function loadFile() {
  return JSON.parse(fs.readFileSync(path.join(FIXTURES, 'design-file.json'), 'utf8'));
}

function findNode(node, id) {
  if (node.id === id) return node;
  for (const c of node.children || []) {
    const hit = findNode(c, id);
    if (hit) return hit;
  }
  return null;
}

function truncate(node, depth) {
  const copy = { ...node };
  if (depth === undefined) return copy;
  if (depth <= 0) {
    delete copy.children;
    return copy;
  }
  if (copy.children) copy.children = copy.children.map((c) => truncate(c, depth - 1));
  return copy;
}

/**
 * options:
 *   failures: { [pathPrefix]: [{ status, headers, body }...] } — 先頭から順に返してから通常応答
 *   variables: true で variables/local を返す（既定は 403）
 *   nullRender: レンダリングで null を返す node ID の配列
 *   brokenDownload: ダウンロードで 500 を返す node ID の配列
 */
export async function startMock(options = {}) {
  const file = loadFile();
  const requests = [];
  const failures = structuredClone(options.failures || {});
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    requests.push({ method: req.method, path: url.pathname, query: Object.fromEntries(url.searchParams), headers: req.headers });
    const send = (status, body, headers = {}) => {
      const isBuf = Buffer.isBuffer(body);
      res.writeHead(status, { 'content-type': isBuf ? headers['content-type'] || 'application/octet-stream' : 'application/json', ...headers });
      res.end(isBuf ? body : JSON.stringify(body));
    };
    for (const [prefix, queue] of Object.entries(failures)) {
      if (url.pathname.startsWith(prefix) && queue.length) {
        const f = queue.shift();
        return send(f.status, f.body ?? { status: f.status, err: 'mock failure' }, f.headers || {});
      }
    }
    const base = `http://127.0.0.1:${server.address().port}`;
    const isApi = url.pathname.startsWith('/v1/');
    if (isApi && req.headers['x-figma-token'] !== TOKEN) return send(403, { status: 403, err: 'Invalid token' });

    let m;
    if ((m = url.pathname.match(/^\/v1\/files\/([^/]+)$/))) {
      if (m[1] !== FILE_KEY) return send(404, { status: 404, err: 'Not found' });
      const depth = url.searchParams.has('depth') ? Number(url.searchParams.get('depth')) : undefined;
      return send(200, { ...file, document: truncate(file.document, depth) });
    }
    if ((m = url.pathname.match(/^\/v1\/files\/([^/]+)\/nodes$/))) {
      if (m[1] !== FILE_KEY) return send(404, { status: 404, err: 'Not found' });
      const depth = url.searchParams.has('depth') ? Number(url.searchParams.get('depth')) : undefined;
      const nodes = {};
      for (const id of (url.searchParams.get('ids') || '').split(',')) {
        const n = findNode(file.document, id);
        nodes[id] = n ? { document: truncate(n, depth), components: file.components, componentSets: file.componentSets, styles: file.styles, schemaVersion: 0 } : null;
      }
      const { document, components, componentSets, styles, ...meta } = file;
      return send(200, { ...meta, nodes });
    }
    if ((m = url.pathname.match(/^\/v1\/files\/([^/]+)\/meta$/))) {
      return send(200, { file: { name: file.name, version: options.latestVersion || file.version, last_touched_at: file.lastModified } });
    }
    if ((m = url.pathname.match(/^\/v1\/files\/([^/]+)\/variables\/local$/))) {
      if (!options.variables) return send(403, { status: 403, error: true, message: 'Limited by Figma plan' });
      return send(200, JSON.parse(fs.readFileSync(path.join(FIXTURES, 'variables-local.json'), 'utf8')));
    }
    if ((m = url.pathname.match(/^\/v1\/files\/([^/]+)\/images$/))) {
      return send(200, { error: false, status: 200, meta: { images: { abc123imageref: `${base}/fill/abc123imageref` } } });
    }
    if ((m = url.pathname.match(/^\/v1\/images\/([^/]+)$/))) {
      const format = url.searchParams.get('format') || 'png';
      const images = {};
      for (const id of (url.searchParams.get('ids') || '').split(',')) {
        images[id] = (options.nullRender || []).includes(id) || !findNode(file.document, id)
          ? null
          : `${base}/render/${encodeURIComponent(id)}.${format}`;
      }
      return send(200, { err: null, images });
    }
    if ((m = url.pathname.match(/^\/render\/(.+)\.(\w+)$/))) {
      const id = decodeURIComponent(m[1]);
      if ((options.brokenDownload || []).includes(id)) return send(500, Buffer.from('broken'), { 'content-type': 'text/plain' });
      if (m[2] === 'svg') return send(200, Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"><path d="M0 0L24 24"/></svg>`), { 'content-type': 'image/svg+xml' });
      return send(200, PNG, { 'content-type': 'image/png' });
    }
    if (url.pathname.startsWith('/fill/')) return send(200, JPEG, { 'content-type': 'binary/octet-stream' });
    return send(404, { status: 404, err: 'unknown endpoint' });
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const apiBase = `http://127.0.0.1:${server.address().port}`;
  return { apiBase, requests, close: () => new Promise((r) => server.close(r)) };
}
