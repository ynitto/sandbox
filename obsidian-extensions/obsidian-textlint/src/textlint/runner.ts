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

export async function runTextlint(filePath: string, options: RunnerOptions): Promise<TextlintCLIResult[]> {
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

    exec(cmd, { cwd }, (error, stdout, stderr) => {
      if (stdout) {
        try {
          resolve(JSON.parse(stdout));
          return;
        } catch (_e) {
          // fall through to error handling
        }
      }
      if (error) {
        reject(new Error(stderr || error.message));
        return;
      }
      resolve([]);
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
    const cwd = useGlobal ? undefined : workingDirectory;
    exec(`${npmPath} install${globalFlag} ${packageName}`, { cwd }, (error, stdout, stderr) => {
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
