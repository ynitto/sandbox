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
};

export async function runTextlint(filePath: string, options: RunnerOptions): Promise<TextlintCLIResult[]> {
  return new Promise((resolve, reject) => {
    const args = ['textlint', '--format', 'json'];
    if (options.textlintrcPath) {
      args.push('--config', quote(options.textlintrcPath));
    }
    args.push(quote(filePath));

    const cmd = `${options.npxPath} ${args.join(' ')}`;

    exec(cmd, { cwd: options.workingDirectory }, (error, stdout, stderr) => {
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

export async function installTextlintPlugin(packageName: string, workingDirectory: string, npmPath: string): Promise<string> {
  return new Promise((resolve, reject) => {
    exec(`${npmPath} install ${packageName}`, { cwd: workingDirectory }, (error, stdout, stderr) => {
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
