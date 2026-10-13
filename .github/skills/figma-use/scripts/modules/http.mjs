// Figma REST API の呼び出し。タイムアウト・有限回の再試行・Retry-After・JSON キャッシュを持つ。
//
// トークンは X-Figma-Token ヘッダーにだけ載せる。画像 URL（Figma 以外のホスト）からの
// ダウンロードにはトークンを付けない。エラーメッセージにもヘッダーやトークンは入れない。

import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

export const EXIT = {
  OK: 0,
  ERROR: 1,
  USAGE: 2,
  AUTH: 3,
  NOT_FOUND: 4,
  RATE_LIMIT: 5,
  PARTIAL: 6,
  NETWORK: 7,
};

export class ApiError extends Error {
  constructor(message, { status = null, exitCode = EXIT.ERROR, hint = '', endpoint = '' } = {}) {
    super(message);
    this.status = status;
    this.exitCode = exitCode;
    this.hint = hint;
    this.endpoint = endpoint;
  }
}

const HINTS = {
  400: '指定（node ID・scale・format など）が API に受け付けられませんでした。取得量が大きすぎる場合も 400 になります。node や depth を絞ってください。',
  401: 'トークンが無効です。Figma の Settings → Security で Personal Access Token を作り直し、connections.yaml か FIGMA_TOKEN を更新してください。',
  403: 'このトークンではアクセスできません。トークンの期限・スコープ（file_content:read が必要。Variables は file_variables:read と Enterprise プラン）と、ファイルの共有権限を確認してください。',
  404: 'ファイルまたはノードが見つかりません。URL の file key・node-id と、branch の URL かどうかを確認してください。',
  429: 'レート制限に達しました。しばらく待ってから再実行してください。キャッシュがある取得は --refresh を外すと API を呼びません。',
  500: 'Figma 側でレンダリングまたは応答に失敗しました。node の数や scale を減らして再実行してください。',
};

const EXIT_BY_STATUS = { 400: EXIT.ERROR, 401: EXIT.AUTH, 403: EXIT.AUTH, 404: EXIT.NOT_FOUND, 429: EXIT.RATE_LIMIT };

export function hintFor(status) {
  if (HINTS[status]) return HINTS[status];
  if (status >= 500) return HINTS[500];
  return '';
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Retry-After（秒 or HTTP 日付）をミリ秒にする。読めなければ null。 */
export function parseRetryAfter(value, now = Date.now()) {
  if (value == null || value === '') return null;
  const n = Number(value);
  if (Number.isFinite(n) && n >= 0) return n * 1000;
  const t = Date.parse(value);
  return Number.isNaN(t) ? null : Math.max(0, t - now);
}

export class FigmaClient {
  constructor({
    token,
    apiBase,
    cacheDir = null,
    refresh = false,
    timeoutMs = 60_000,
    maxRetries = 3,
    maxRetryWaitMs = 120_000,
    backoffMs = 1000,
    log = (msg) => process.stderr.write(`${msg}\n`),
    fetchImpl = globalThis.fetch,
  }) {
    this.token = token;
    this.apiBase = apiBase.replace(/\/+$/, '');
    this.cacheDir = cacheDir;
    this.refresh = refresh;
    this.timeoutMs = timeoutMs;
    this.maxRetries = maxRetries;
    this.maxRetryWaitMs = maxRetryWaitMs;
    this.backoffMs = backoffMs;
    this.log = log;
    this.fetchImpl = fetchImpl;
    this.calls = 0;
    this.cacheHits = [];
  }

  endpointUrl(endpoint, query = {}) {
    const url = new URL(this.apiBase + endpoint);
    for (const key of Object.keys(query).sort()) {
      const value = query[key];
      if (value === undefined || value === null || value === false) continue;
      url.searchParams.set(key, String(value));
    }
    return url;
  }

  cachePath(url) {
    if (!this.cacheDir) return null;
    const hash = crypto.createHash('sha256').update(url.pathname + url.search).digest('hex').slice(0, 32);
    return path.join(this.cacheDir, 'api', `${hash}.json`);
  }

  /**
   * GET して JSON を返す。cache: true のとき、キャッシュがあれば API を呼ばない。
   * 戻り値は { data, fetchedAt, fromCache }。
   */
  async getJson(endpoint, query = {}, { cache = true, refresh = this.refresh } = {}) {
    const url = this.endpointUrl(endpoint, query);
    const cacheFile = cache ? this.cachePath(url) : null;
    if (cacheFile && !refresh && fs.existsSync(cacheFile)) {
      try {
        const hit = JSON.parse(fs.readFileSync(cacheFile, 'utf8'));
        const version = hit.data?.version ?? '不明';
        this.log(`キャッシュを使用: ${endpoint}（取得 ${hit.fetchedAt} / version ${version}）。最新にするには --refresh`);
        this.cacheHits.push({ endpoint, fetchedAt: hit.fetchedAt, version: hit.data?.version ?? null });
        return { data: hit.data, fetchedAt: hit.fetchedAt, fromCache: true };
      } catch {
        // 壊れたキャッシュは無視して取り直す。
      }
    }
    if (!this.token) {
      throw new ApiError('Figma のトークンが設定されていません', {
        exitCode: EXIT.AUTH,
        hint: 'connections.yaml の figma エントリか、環境変数 FIGMA_TOKEN を設定してください（doctor で確認できます）。',
        endpoint,
      });
    }
    const res = await this.request(url, { 'X-Figma-Token': this.token }, endpoint);
    let data;
    try {
      data = JSON.parse(res.text);
    } catch {
      throw new ApiError(`API の応答が JSON ではありません: ${endpoint}`, { endpoint, exitCode: EXIT.NETWORK });
    }
    const fetchedAt = new Date().toISOString();
    if (cacheFile) {
      fs.mkdirSync(path.dirname(cacheFile), { recursive: true });
      fs.writeFileSync(cacheFile, JSON.stringify({ endpoint, query: url.search, fetchedAt, data }));
    }
    return { data, fetchedAt, fromCache: false };
  }

  /** 画像 URL からバイト列を取る。Figma のトークンは付けない。 */
  async download(fileUrl) {
    let url;
    try {
      url = new URL(fileUrl);
    } catch {
      throw new ApiError(`画像 URL が不正です: ${fileUrl}`, { exitCode: EXIT.ERROR });
    }
    const allowHttp = this.apiBase.startsWith('http://');
    if (url.protocol !== 'https:' && !(allowHttp && url.protocol === 'http:')) {
      throw new ApiError(`https 以外の画像 URL は取得しません: ${url.protocol}`, { exitCode: EXIT.ERROR });
    }
    const res = await this.request(url, {}, 'image-download', { binary: true });
    return { bytes: res.bytes, contentType: res.contentType };
  }

  async request(url, headers, endpoint, { binary = false } = {}) {
    let attempt = 0;
    for (;;) {
      attempt += 1;
      this.calls += 1;
      let res;
      try {
        res = await this.fetchImpl(url, { headers, signal: AbortSignal.timeout(this.timeoutMs) });
      } catch (e) {
        const reason = e?.name === 'TimeoutError' ? `タイムアウト（${this.timeoutMs / 1000} 秒）` : `接続エラー（${e?.cause?.code || e?.cause?.message || e?.message || e}）`;
        if (attempt <= this.maxRetries) {
          const wait = this.backoffMs * 2 ** (attempt - 1);
          this.log(`${endpoint}: ${reason}。${wait / 1000} 秒後に再試行（${attempt}/${this.maxRetries}）`);
          await sleep(wait);
          continue;
        }
        throw new ApiError(`${endpoint}: ${reason}`, { exitCode: EXIT.NETWORK, endpoint, hint: 'ネットワークとプロキシ設定を確認してください。' });
      }
      if (res.ok) {
        if (binary) {
          const bytes = Buffer.from(await res.arrayBuffer());
          return { bytes, contentType: res.headers.get('content-type') || '' };
        }
        return { text: await res.text() };
      }
      const status = res.status;
      let detail = '';
      try {
        const body = await res.text();
        const parsed = JSON.parse(body);
        detail = parsed.err || parsed.message || '';
      } catch {
        // 本文が JSON でなければ詳細なし。
      }
      const retryable = status === 429 || status >= 500;
      if (retryable && attempt <= this.maxRetries) {
        let wait = this.backoffMs * 2 ** (attempt - 1);
        if (status === 429) {
          const after = parseRetryAfter(res.headers.get('retry-after'));
          if (after !== null) wait = after;
          if (wait > this.maxRetryWaitMs) {
            throw new ApiError(`${endpoint}: レート制限（HTTP 429）。Retry-After が ${Math.round(wait / 1000)} 秒と長いため待たずに中止しました`, {
              status,
              exitCode: EXIT.RATE_LIMIT,
              endpoint,
              hint: rateLimitHint(res.headers),
            });
          }
        }
        this.log(`${endpoint}: HTTP ${status}。${(wait / 1000).toFixed(1)} 秒後に再試行（${attempt}/${this.maxRetries}）`);
        await sleep(wait);
        continue;
      }
      const hint = status === 429 ? rateLimitHint(res.headers) : hintFor(status);
      throw new ApiError(`${endpoint}: HTTP ${status}${detail ? `（${detail}）` : ''}`, {
        status,
        exitCode: EXIT_BY_STATUS[status] ?? (status >= 500 ? EXIT.NETWORK : EXIT.ERROR),
        endpoint,
        hint,
      });
    }
  }
}

function rateLimitHint(headers) {
  const parts = [HINTS[429]];
  const tier = headers.get('x-figma-plan-tier');
  const type = headers.get('x-figma-rate-limit-type');
  if (tier || type) parts.push(`（プラン: ${tier || '不明'} / 席の種別: ${type === 'low' ? 'Collab・View（上限が低い）' : type === 'high' ? 'Full・Dev' : '不明'}）`);
  const link = headers.get('x-figma-upgrade-link');
  if (link) parts.push(`上限の詳細: ${link}`);
  return parts.join(' ');
}
