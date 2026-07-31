/**
 * ~/.copilot/ 以下のファイルを各 CLI ツールのユーザーホームディレクトリへ同期する。
 *
 * VS Code 起動時に毎回実行される。~/.copilot/ が存在しない場合は何もしない。
 * 各 CLI によってディレクトリ名・ファイル名が異なる場合は FileMapping の
 * renameFile で吸収する。
 */

import * as cp from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';
import { toWslPath } from './pathUtils';

/** ~/.copilot/ — 同期元ルート */
const COPILOT_DIR = path.join(os.homedir(), '.copilot');

// ─── 型定義 ──────────────────────────────────────────────────────────────────

interface FileMapping {
  /** 同期元サブディレクトリ（COPILOT_DIR からの相対パス） */
  srcDir: string;
  /** 同期先サブディレクトリ（homeDir からの相対パス） */
  destDir: string;
  /**
   * ファイル名変換。省略時はそのままコピー。
   * null を返すとそのファイルをスキップ。
   */
  renameFile?: (srcName: string) => string | null;
}

interface SyncConfig {
  /** この設定が適用される AgentConfig.tool の値 */
  tools: string[];
  /** 同期先ルートディレクトリ（絶対パス） */
  homeDir: string;
  /** Windows から WSL 内へ同期する設定か */
  isWslTarget?: boolean;
  /** ディレクトリ・ファイル名のマッピング */
  mappings: FileMapping[];
}

// ─── CLI ごとの同期設定 ───────────────────────────────────────────────────────

const DEFAULT_MAPPINGS: FileMapping[] = [
  { srcDir: 'agents',       destDir: 'agents'       },
  { srcDir: 'instructions', destDir: 'instructions' },
  { srcDir: 'skills',       destDir: 'skills'       },
];

/** kiro-cli 用マッピング: instructions/*.md → steering/*.md */
const KIRO_MAPPINGS: FileMapping[] = [
  { srcDir: 'agents',       destDir: 'agents'   },
  { srcDir: 'instructions', destDir: 'steering', renameFile: (name) => name.endsWith('.md') ? name : null },
  { srcDir: 'skills',       destDir: 'skills'   },
];

/**
 * 実行時にプラットフォームを判定して同期設定を構築する。
 * kiro-cli は Windows では WSL ホームへのコピーが必要なため、
 * WSL 内の HOME を Linux パスで取得する。
 */
function buildSyncConfigs(): SyncConfig[] {
  const home = os.homedir();
  const isWindows = os.platform() === 'win32';

  const kiroHomeDir = isWindows
    ? resolveWslHomeSubdir('.kiro')
    : path.join(home, '.kiro');

  const configs: SyncConfig[] = [
    {
      tools: ['claude'],
      homeDir: path.join(home, '.claude'),
      mappings: DEFAULT_MAPPINGS,
    },
    {
      tools: ['gh-copilot-suggest', 'gh-copilot-suggest-git', 'gh-copilot-suggest-gh', 'gh-copilot-explain'],
      homeDir: path.join(home, '.github', 'copilot'),
      mappings: DEFAULT_MAPPINGS,
    },
    {
      tools: ['codex'],
      homeDir: path.join(home, '.codex'),
      mappings: DEFAULT_MAPPINGS,
    },
  ];

  // kiro-cli: Windows で WSL ホームが解決できた場合のみ追加
  if (kiroHomeDir !== undefined) {
    configs.push({
      tools: ['kiro-cli'],
      homeDir: kiroHomeDir,
      isWslTarget: isWindows,
      mappings: KIRO_MAPPINGS,
    });
  }

  return configs;
}

/**
 * Windows 上で WSL ホームディレクトリの Linux パスを取得し、
 * サブディレクトリ名を結合して返す。
 * WSL が利用できない場合は undefined を返す。
 *
 * 例: resolveWslHomeSubdir('.kiro') → "/home/user/.kiro"
 */
function resolveWslHomeSubdir(subdir: string): string | undefined {
  try {
    const wslHome = cp.execSync('wsl.exe -e sh -lc "printf %s \"$HOME\""', { encoding: 'utf-8' }).trim();
    const cleanSubdir = subdir.replace(/^\/+/, '');
    return `${wslHome}/${cleanSubdir}`;
  } catch {
    return undefined;
  }
}

// ─── 公開 API ─────────────────────────────────────────────────────────────────

/**
 * ~/.copilot/ の内容を利用可能な CLI ツールのユーザーホームへ同期する。
 * VS Code 起動時に呼び出す。~/.copilot/ が存在しない場合は何もしない。
 *
 * @param availableTools 利用可能な AgentConfig.tool の集合
 */
export function syncCopilotConfig(availableTools: ReadonlySet<string>): void {
  if (!fs.existsSync(COPILOT_DIR)) {
    return;
  }

  const seenHomeDirs = new Set<string>();
  for (const config of buildSyncConfigs()) {
    // いずれかの tool が利用可能であれば同期対象とする
    const isAvailable = config.tools.some((t: string) => availableTools.has(t));
    if (!isAvailable) {
      continue;
    }
    // 同一 homeDir への二重コピーを防ぐ
    if (seenHomeDirs.has(config.homeDir)) {
      continue;
    }
    seenHomeDirs.add(config.homeDir);
    syncConfig(config);
  }
}

// ─── 内部ヘルパー ─────────────────────────────────────────────────────────────

function copyDirRecursive(
  srcDir: string,
  destDir: string,
  renameFile?: (name: string) => string | null
): void {
  fs.mkdirSync(destDir, { recursive: true });
  for (const entry of fs.readdirSync(srcDir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      copyDirRecursive(
        path.join(srcDir, entry.name),
        path.join(destDir, entry.name),
        renameFile
      );
    } else if (entry.isFile()) {
      const destName = renameFile ? renameFile(entry.name) : entry.name;
      if (destName === null) {
        continue; // スキップ指定
      }
      fs.copyFileSync(path.join(srcDir, entry.name), path.join(destDir, destName));
    }
  }
}

function syncConfig(config: SyncConfig): void {
  try {
    if (config.isWslTarget && os.platform() === 'win32') {
      syncConfigToWsl(config);
      return;
    }

    for (const mapping of config.mappings) {
      const srcDir = path.join(COPILOT_DIR, mapping.srcDir);
      if (!fs.existsSync(srcDir)) {
        continue;
      }

      const destDir = path.join(config.homeDir, mapping.destDir);

      copyDirRecursive(srcDir, destDir, mapping.renameFile);
    }
  } catch (e) {
    // 同期失敗はサイレントに無視（CLI が未インストールの場合など）
    console.log(e instanceof Error ? e.message : String(e));
  }
}

function shellQuote(value: string): string {
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

function syncConfigToWsl(config: SyncConfig): void {
  for (const mapping of config.mappings) {
    const srcDir = path.join(COPILOT_DIR, mapping.srcDir);
    if (!fs.existsSync(srcDir)) {
      continue;
    }

    const dstDir = `${config.homeDir.replace(/\/+$/, '')}/${mapping.destDir.replace(/^\/+/, '')}`;

    // renameFile フィルタを適用してファイルを個別にコピーする。
    // cp -a では renameFile によるフィルタ・リネームが反映されないため、
    // ファイルごとに wsl.exe cp を呼び出す。
    copyDirRecursiveToWsl(srcDir, dstDir, mapping.renameFile);
  }
}

/**
 * srcDir 内のファイルを renameFile フィルタを適用しながら WSL 内 dstDir へ再帰コピーする。
 */
function copyDirRecursiveToWsl(
  srcDir: string,
  dstDir: string,
  renameFile?: (name: string) => string | null
): void {
  // 先にコピー先ディレクトリを作成する
  cp.execFileSync('wsl.exe', ['-e', 'sh', '-lc', `mkdir -p ${shellQuote(dstDir)}`], { stdio: 'ignore' });

  for (const entry of fs.readdirSync(srcDir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      copyDirRecursiveToWsl(
        path.join(srcDir, entry.name),
        `${dstDir}/${entry.name}`,
        renameFile
      );
    } else if (entry.isFile()) {
      const destName = renameFile ? renameFile(entry.name) : entry.name;
      if (destName === null) {
        continue; // スキップ指定
      }
      const srcWsl = toWslPath(path.join(srcDir, entry.name));
      cp.execFileSync('wsl.exe', [
        '-e', 'sh', '-lc',
        `cp ${shellQuote(srcWsl)} ${shellQuote(`${dstDir}/${destName}`)}`
      ], { stdio: 'ignore' });
    }
  }
}
