/**
 * VS Code の workspace fsPath を各実行環境向けに変換するユーティリティ。
 *
 * VS Code の uri.fsPath が返す形式:
 *   - Windows ネイティブワークスペース : "C:\Users\user\project"
 *   - Windows から開いた WSL ワークスペース: "\\wsl$\Ubuntu\home\user\project"
 *                                     または "\\wsl.localhost\Ubuntu\home\user\project"
 *   - WSL Remote / Linux / macOS      : "/home/user/project"
 */

/**
 * VS Code fsPath を WSL 内パスに変換する。
 *
 * - "\\wsl$\Ubuntu\home\user\project"      → "/home/user/project"
 * - "\\wsl.localhost\Ubuntu\home\user\proj" → "/home/user/project"
 * - "C:\Users\user\project"                → "/mnt/c/Users/user/project"
 * - "/home/user/project"                   → "/home/user/project" (変換不要)
 */
export function toWslPath(fsPath: string): string {
  // \\wsl$\<Distro>\<path> または \\wsl.localhost\<Distro>\<path>
  const wslUncMatch = fsPath.match(/^\\\\wsl(?:\$|\.localhost)\\[^\\]+(.*)$/i);
  if (wslUncMatch) {
    // バックスラッシュをスラッシュに変換
    return wslUncMatch[1].replace(/\\/g, '/') || '/';
  }

  // C:\... 形式の Windows ドライブパス
  const driveMatch = fsPath.match(/^([A-Za-z]):\\(.*)$/);
  if (driveMatch) {
    const drive = driveMatch[1].toLowerCase();
    const rest = driveMatch[2].replace(/\\/g, '/');
    return `/mnt/${drive}/${rest}`;
  }

  // すでに Linux / macOS パス（変換不要）
  return fsPath;
}

/**
 * fsPath が Windows の WSL ワークスペースパスかどうかを判定する。
 * ("\\wsl$\" または "\\wsl.localhost\" で始まる UNC パス)
 */
export function isWslWorkspacePath(fsPath: string): boolean {
  return /^\\\\wsl(?:\$|\.localhost)\\/i.test(fsPath);
}
