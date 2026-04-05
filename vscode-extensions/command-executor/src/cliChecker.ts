/**
 * CLI ツールの利用可否チェック。
 * AgentConfig.tool から実行ファイル名へのマッピングを持ち、
 * PATH 上に存在するかを確認する。
 */

import * as cp from 'child_process';
import * as os from 'os';
import { AgentConfig } from './agentConfig';

/**
 * AgentConfig.tool → キャッシュキー。
 * 同じキャッシュキーを持つ tool は一度だけチェックする。
 * Windows の kiro-cli は WSL 経由で動くため専用キーを使う。
 */
function getCacheKey(tool: string): string | undefined {
  const isWindows = os.platform() === 'win32';
  switch (tool) {
    case 'claude':                return 'claude';
    case 'gh-copilot-suggest':
    case 'gh-copilot-suggest-git':
    case 'gh-copilot-suggest-gh':
    case 'gh-copilot-explain':    return 'gh';
    case 'codex':                 return 'codex';
    case 'q':                     return 'q';
    case 'kiro-cli':              return isWindows ? 'wsl:kiro-cli' : 'kiro-cli';
    default:                      return undefined;
  }
}

/**
 * agents から利用可能なものだけを返す。
 * 同じキャッシュキーを持つ tool は一度だけチェックする。
 */
export function filterAvailableAgents(agents: AgentConfig[]): AgentConfig[] {
  const cache = new Map<string, boolean>();

  return agents.filter((agent) => {
    const key = getCacheKey(agent.tool);
    if (key === undefined) {
      return false; // マッピング未定義の tool は表示しない
    }
    if (!cache.has(key)) {
      cache.set(key, checkCacheKey(key));
    }
    return cache.get(key)!;
  });
}

// ─── 内部ヘルパー ─────────────────────────────────────────────────────────────

/** キャッシュキーに対応する利用可否チェックを実行する */
function checkCacheKey(key: string): boolean {
  if (key.startsWith('wsl:')) {
    return isWslExecutableAvailable(key.slice(4));
  }
  return isExecutableAvailable(key);
}

/** ネイティブ PATH 上の実行ファイルを確認する */
function isExecutableAvailable(exe: string): boolean {
  try {
    const cmd = os.platform() === 'win32' ? `where "${exe}"` : `which "${exe}"`;
    cp.execSync(cmd, { stdio: 'ignore' });
    return true;
  } catch {
    return false;
  }
}

/** WSL 内の実行ファイルを確認する（Windows 専用） */
function isWslExecutableAvailable(exe: string): boolean {
  const systemRoot = process.env.SystemRoot ?? 'C:\\Windows';
  const wslCandidates = [`${systemRoot}\\System32\\wsl.exe`, 'wsl.exe'];

  for (const wsl of wslCandidates) {
    try {
      cp.execSync(wsl, ['-e', 'sh', '-c', `command -v ${exe.replace(/'/g, `'\\''`)} >/dev/null 2>&1`], {
        stdio: 'ignore'
      });
      return true;
    } catch {
      // 続行して次の候補を試す
    }
  }

  return false;
}
