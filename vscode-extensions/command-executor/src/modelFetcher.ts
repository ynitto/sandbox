import * as https from 'https';
import * as cp from 'child_process';

/** フォールバック用モデル一覧 */
export const FALLBACK_CLAUDE_MODELS = [
  'claude-opus-4-6',
  'claude-sonnet-4-6',
  'claude-haiku-4-5-20251001',
];

/**
 * Anthropic API からモデル一覧を取得する。
 * ANTHROPIC_API_KEY 環境変数、または claude CLI のトークンを使用。
 * 取得できない場合は FALLBACK_CLAUDE_MODELS を返す。
 */
export async function fetchClaudeModels(): Promise<string[]> {
  const apiKey = process.env.ANTHROPIC_API_KEY ?? await readClaudeApiKey();
  if (!apiKey) {
    return FALLBACK_CLAUDE_MODELS;
  }

  try {
    const models = await requestModels(apiKey);
    if (models.length > 0) {
      return models;
    }
  } catch {
    // 取得失敗はフォールバックで処理
  }

  return FALLBACK_CLAUDE_MODELS;
}

/** /v1/models エンドポイントを呼び出してモデル ID 一覧を返す */
function requestModels(apiKey: string): Promise<string[]> {
  return new Promise((resolve, reject) => {
    const req = https.request(
      {
        hostname: 'api.anthropic.com',
        path: '/v1/models',
        method: 'GET',
        headers: {
          'x-api-key': apiKey,
          'anthropic-version': '2023-06-01',
        },
        timeout: 5000,
      },
      (res) => {
        let body = '';
        res.on('data', (chunk: Buffer) => { body += chunk.toString(); });
        res.on('end', () => {
          if (res.statusCode !== 200) {
            reject(new Error(`HTTP ${res.statusCode}`));
            return;
          }
          try {
            const json = JSON.parse(body) as { data?: Array<{ id: string }> };
            const ids = (json.data ?? []).map((m) => m.id).filter(Boolean);
            resolve(ids);
          } catch (e) {
            reject(e);
          }
        });
      }
    );
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
    req.end();
  });
}

/**
 * `claude` CLI から API キーを読み取る試み。
 * claude が --print で動作しないため、認証情報ファイルを直接参照する。
 */
async function readClaudeApiKey(): Promise<string | undefined> {
  return new Promise((resolve) => {
    // claude auth status --json 等の出力に api_key が含まれるか試みる
    const proc = cp.spawn('claude', ['auth', 'status', '--json'], {
      timeout: 3000,
      env: { ...process.env },
      shell: false,
    });

    let stdout = '';
    proc.stdout?.on('data', (d: Buffer) => { stdout += d.toString(); });
    proc.on('close', () => {
      try {
        const json = JSON.parse(stdout) as { apiKey?: string; api_key?: string };
        resolve(json.apiKey ?? json.api_key);
      } catch {
        resolve(undefined);
      }
    });
    proc.on('error', () => resolve(undefined));
  });
}
