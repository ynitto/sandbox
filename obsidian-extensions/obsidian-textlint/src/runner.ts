import { exec } from 'child_process';
import { TextlintMessage } from '@textlint/types';

export type TextlintCLIResult = {
  filePath: string;
  messages: TextlintMessage[];
};

export type RunnerOptions = {
  npxPath: string;
  textlintrcPath?: string;
  workingDirectory?: string;
  useGlobal?: boolean;
  textlintPath?: string;
};

export type RunTextlintResult = {
  results: TextlintCLIResult[];
  rawOutput: string;
};

export async function runTextlint(filePath: string, options: RunnerOptions): Promise<RunTextlintResult> {
  return new Promise((resolve, reject) => {
    const args = ['--format', 'json'];
    if (options.textlintrcPath) {
      args.push('--config', quote(options.textlintrcPath));
    }
    args.push(quote(filePath));

    const cmd = options.useGlobal
      ? `${options.textlintPath || 'textlint'} ${args.join(' ')}`
      : `${options.npxPath} textlint ${args.join(' ')}`;

    const cwd = options.useGlobal ? undefined : options.workingDirectory;

    exec(loginShellCommand(cmd), { cwd }, (error, stdout, stderr) => {
      const rawOutput = [stdout.trim(), stderr.trim()].filter(Boolean).join('\n');
      if (stdout) {
        try {
          resolve({ results: JSON.parse(stdout), rawOutput });
          return;
        } catch (_e) {
          // fall through to error handling
        }
      }
      if (error) {
        reject(new Error(stderr || error.message));
        return;
      }
      resolve({ results: [], rawOutput });
    });
  });
}

export type RunTextlintFixResult = {
  rawOutput: string;
};

export async function runTextlintFix(filePath: string, options: RunnerOptions): Promise<RunTextlintFixResult> {
  return new Promise((resolve) => {
    const args = ['--fix'];
    if (options.textlintrcPath) {
      args.push('--config', quote(options.textlintrcPath));
    }
    args.push(quote(filePath));

    const cmd = options.useGlobal
      ? `${options.textlintPath || 'textlint'} ${args.join(' ')}`
      : `${options.npxPath} textlint ${args.join(' ')}`;

    const cwd = options.useGlobal ? undefined : options.workingDirectory;

    exec(loginShellCommand(cmd), { cwd }, (_error, stdout, stderr) => {
      // textlint --fix exits non-zero when fixes were applied; we always resolve
      const rawOutput = [stdout.trim(), stderr.trim()].filter(Boolean).join('\n');
      resolve({ rawOutput });
    });
  });
}

export type RuleConfig = number | { severity?: number; [key: string]: unknown };

export type TextlintPrintConfigResult = {
  rules: Record<string, RuleConfig>;
  rawOutput: string;
};

export async function runTextlintPrintConfig(options: RunnerOptions & { workingDirectory: string }): Promise<TextlintPrintConfigResult> {
  return new Promise((resolve) => {
    const args = ['--print-config'];
    if (options.textlintrcPath) {
      args.push('--config', quote(options.textlintrcPath));
    }

    const cmd = options.useGlobal
      ? `${options.textlintPath || 'textlint'} ${args.join(' ')}`
      : `${options.npxPath} textlint ${args.join(' ')}`;

    const cwd = options.workingDirectory || undefined;

    exec(loginShellCommand(cmd), { cwd }, (_error, stdout, stderr) => {
      const rawOutput = [stdout.trim(), stderr.trim()].filter(Boolean).join('\n');
      // Extract JSON block robustly (login shell may prepend extra output)
      const jsonStart = stdout.indexOf('{');
      const jsonEnd = stdout.lastIndexOf('}');
      const jsonStr = jsonStart !== -1 && jsonEnd > jsonStart ? stdout.slice(jsonStart, jsonEnd + 1) : stdout;
      try {
        const parsed = JSON.parse(jsonStr) as { rules?: Record<string, RuleConfig> };
        resolve({ rules: parsed.rules ?? {}, rawOutput });
      } catch {
        resolve({ rules: {}, rawOutput });
      }
    });
  });
}

export async function installTextlintPlugin(
  packageName: string,
  workingDirectory: string,
  npmPath: string,
  useGlobal = false,
): Promise<string> {
  return new Promise((resolve, reject) => {
    const globalFlag = useGlobal ? ' -g' : '';
    const cwd = workingDirectory || undefined;
    exec(loginShellCommand(`${npmPath} install${globalFlag} ${packageName}`), { cwd }, (error, stdout, stderr) => {
      if (error) {
        reject(new Error(stderr || error.message));
        return;
      }
      resolve(stdout);
    });
  });
}

function quote(s: string): string {
  return `"${s.replace(/"/g, '\\"')}"`;
}

/**
 * On macOS, GUI apps (e.g. Obsidian) launch without the user's shell environment,
 * so PATH entries added in ~/.zprofile or ~/.zshrc are not inherited.
 * Wrapping commands in a login shell ensures those paths are loaded.
 */
function loginShellCommand(cmd: string): string {
  if (process.platform === 'darwin') {
    const shell = process.env.SHELL || '/bin/zsh';
    // Use single quotes for the -c argument to avoid double-escaping inner double quotes.
    // e.g. quote(cmd) would turn "/path" into \"\/path\", causing textlint to receive the
    // literal backslash-quote characters. Single-quoting passes the command verbatim.
    const escaped = cmd.replace(/'/g, "'\\''");
    return `${shell} -l -c '${escaped}'`;
  }
  return cmd;
}
