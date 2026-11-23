"use strict";

const { discoverCommands, main } = require("./core/cli");

/**
 * 自動発見されたコマンドの名前、説明、使用法を名前の昇順で返す。
 * @returns {{name: string, help: string, usage: string}[]} コマンド一覧。
 */
function listCommands() {
  const commands = discoverCommands();
  return [...commands.keys()].sort().map((name) => {
    const { help, usage } = commands.get(name);
    return { name, help, usage };
  });
}

/**
 * @typedef {object} RunOptions
 * @property {string | Buffer} [stdin] 標準入力。既定は空の Buffer。
 * @property {NodeJS.ProcessEnv} [env] 環境変数。既定は process.env。
 * @property {string} [cwd] 作業ディレクトリ。既定は process.cwd()。
 * @property {boolean} [stdinIsTTY=false] 標準入力を端末として扱うかどうか。
 */

/**
 * @typedef {object} RunResult
 * @property {number} code 0 は成功またはヘルプ表示、2 は入力・使用法エラー。
 *   1 は予期しない例外またはコマンド固有の結果を表す。その他のコードは CLI に従う。
 * @property {string} stdout 捕捉した標準出力の UTF-8 文字列。
 * @property {string} stderr 捕捉した標準エラーの文字列。予期しない例外のメッセージも含む。
 * @property {Buffer} stdoutBuffer 捕捉した標準出力を呼び出し順に連結したバイト列。
 */

/**
 * コマンド名と引数を指定して実行し、ホストプロセスを終了せずに結果を返す。
 * @param {string} name コマンド名。
 * @param {string[]} [argv=[]] コマンド名に続く引数。
 * @param {RunOptions} [options={}] 実行時の入出力と環境の設定。
 * @returns {Promise<RunResult>} CLI と同じ終了コードと捕捉した出力。予期しない例外では 1。
 */
function runCommand(name, argv = [], options = {}) {
  return run([name, ...argv], options);
}

/**
 * CLI に渡す引数列全体を実行し、ホストプロセスを終了せずに結果を返す。
 * @param {string[]} [argv=[]] コマンド名または全体オプションから始まる引数列。
 * @param {RunOptions} [options={}] 実行時の入出力と環境の設定。
 * @returns {Promise<RunResult>} CLI と同じ終了コードと捕捉した出力。予期しない例外では 1。
 */
async function run(argv = [], options = {}) {
  const stdoutChunks = [];
  const stderrChunks = [];
  let code;
  try {
    const { stdin = "", env = process.env, cwd = process.cwd(), stdinIsTTY = false } = options;
    const stdinBuffer = Buffer.isBuffer(stdin) ? stdin : Buffer.from(stdin, "utf8");
    code = await main(argv, {
      out(text) {
        stdoutChunks.push(Buffer.from(text, "utf8"));
      },
      err(text) {
        stderrChunks.push(text);
      },
      outBuffer(buffer) {
        stdoutChunks.push(Buffer.from(buffer));
      },
      outSync(buffer) {
        stdoutChunks.push(Buffer.from(buffer));
      },
      readStdin() {
        return stdinBuffer;
      },
      stdinIsTTY,
      env,
      cwd,
    });
  } catch (error) {
    code = 1;
    stderrChunks.push(`${error?.message ?? String(error)}\n`);
  }
  const stdoutBuffer = Buffer.concat(stdoutChunks);
  return {
    code,
    stdout: stdoutBuffer.toString("utf8"),
    stderr: stderrChunks.join(""),
    stdoutBuffer,
  };
}

module.exports = { listCommands, runCommand, run };
