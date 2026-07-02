'use strict';

// ローカル CLI エージェント（kiro-cli など）にプロンプトを渡して要約させる。
// Obsidian Web Clipper の「ページ内容をエージェントに送る」動作の CLI 版。
//
// コマンドテンプレートのプレースホルダ:
//   {promptFile} … プロンプト全文を書き出した一時ファイルのパス
//   {prompt}     … プロンプト全文を argv でそのまま渡す
//                  （ARGV_LIMIT 超過時は自動でファイル退避 + 参照渡しに切替）
//   どちらも無い場合は標準入力にプロンプトを流し込む。

const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

// kiro-flow と同じ発想: argv が長すぎると OS の制限で起動に失敗するため退避する
const ARGV_LIMIT = 100000;

// テンプレートを argv 配列に分割する（ダブル/シングルクォート対応の簡易トークナイザ）
function tokenize(command) {
  const tokens = [];
  const re = /"([^"]*)"|'([^']*)'|(\S+)/g;
  let m;
  while ((m = re.exec(command)) !== null) {
    tokens.push(m[1] ?? m[2] ?? m[3]);
  }
  return tokens;
}

function writePromptFile(prompt) {
  const file = path.join(
    os.tmpdir(),
    `gitlab-review-viewer-prompt-${process.pid}-${Date.now()}.md`
  );
  fs.writeFileSync(file, prompt, 'utf8');
  return file;
}

function stripAnsi(text) {
  // eslint-disable-next-line no-control-regex
  return String(text).replace(/\[[0-9;?]*[a-zA-Z]/g, '');
}

async function runAgent({ command, timeoutSec = 300 }, prompt) {
  if (!command || !command.trim()) {
    throw new Error('エージェントコマンドが設定されていません（設定画面から指定してください）');
  }
  let tokens = tokenize(command);
  let promptFile = null;
  let useStdin = true;

  const needsFile =
    tokens.some((t) => t.includes('{promptFile}')) ||
    (tokens.some((t) => t.includes('{prompt}')) &&
      Buffer.byteLength(prompt, 'utf8') > ARGV_LIMIT);

  if (needsFile) {
    promptFile = writePromptFile(prompt);
  }
  tokens = tokens.map((t) => {
    if (t.includes('{promptFile}')) {
      useStdin = false;
      return t.replaceAll('{promptFile}', promptFile);
    }
    if (t.includes('{prompt}')) {
      useStdin = false;
      if (promptFile) {
        return t.replaceAll(
          '{prompt}',
          `プロンプト全文は次のファイルにあります。必ず読み込み、その指示に従ってください: ${promptFile}`
        );
      }
      return t.replaceAll('{prompt}', prompt);
    }
    return t;
  });

  try {
    return await spawnAndCollect(tokens, useStdin ? prompt : null, timeoutSec, undefined);
  } finally {
    if (promptFile) {
      try {
        fs.unlinkSync(promptFile);
      } catch {
        /* 一時ファイル削除失敗は無視 */
      }
    }
  }
}

function spawnAndCollect(tokens, stdinText, timeoutSec, cwd) {
  return new Promise((resolve, reject) => {
    // Windows では kiro-cli 等が .cmd シムのことがあるため shell 経由で起動する
    const useShell = process.platform === 'win32';
    const child = useShell
      ? spawn(buildCommandLine(tokens), { shell: true, windowsHide: true, cwd })
      : spawn(tokens[0], tokens.slice(1), { shell: false, cwd });

    let stdout = '';
    let stderr = '';
    let settled = false;
    const timer =
      timeoutSec > 0
        ? setTimeout(() => {
            settled = true;
            child.kill();
            reject(new Error(`エージェントがタイムアウトしました（${timeoutSec} 秒超過）`));
          }, timeoutSec * 1000)
        : null;

    child.stdout.on('data', (d) => (stdout += d));
    child.stderr.on('data', (d) => (stderr += d));
    child.on('error', (err) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      reject(new Error(`エージェントの起動に失敗しました: ${err.message}`));
    });
    child.on('close', (code) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      if (code !== 0) {
        reject(
          new Error(
            `エージェントが失敗しました (exit=${code}): ${stripAnsi(stderr).trim().slice(0, 500)}`
          )
        );
        return;
      }
      resolve(stripAnsi(stdout).trim());
    });

    if (stdinText !== null) {
      child.stdin.write(stdinText);
    }
    child.stdin.end();
  });
}

// shell:true 用にトークンを 1 本のコマンドラインへ組み立てる。
// プロンプト本体はファイル経由で渡す前提のため、ここに来るのは設定者が書いた
// 短いトークンのみ。cmd.exe で壊れやすい二重引用符は除去して安全側に倒す。
function buildCommandLine(tokens) {
  return tokens
    .map((t) => {
      const clean = t.replaceAll('"', '');
      return /\s/.test(clean) ? `"${clean}"` : clean;
    })
    .join(' ');
}

function buildPrompt(template, vars) {
  let out = String(template || '');
  for (const [k, v] of Object.entries(vars)) {
    out = out.replaceAll(`{${k}}`, v == null ? '' : String(v));
  }
  return out;
}

// 短い外部コマンドの実行（kiro-autonomous approve など）。プレースホルダは
// 呼び出し側で置換済みの前提。
async function runCommand(command, { timeoutSec = 120, cwd } = {}) {
  const tokens = tokenize(String(command || ''));
  if (!tokens.length) throw new Error('コマンドが空です');
  return spawnAndCollect(tokens, null, timeoutSec, cwd);
}

module.exports = { runAgent, runCommand, buildPrompt, tokenize };
