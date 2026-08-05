'use strict';

// ローカルモデル（ollama）の接続先の読み書き。
//
// **なぜノードの宣言（host.yaml）に書くのか**: ollama を別 PC（GPU を積んだ 1 台）で
// 走らせる構成では接続先を指定する必要があるが、これまでの唯一の口は環境変数
// `OLLAMA_HOST` だった。agent-tools の実行はほとんどが常駐体・デーモン・この画面から
// 起こされる子プロセスで、そこへ環境変数を届けるには systemd unit やシェル初期化を人が
// 直すしかない。しかも正典構成（Windows の画面 + WSL の実行エンジン）では、この画面の
// プロセス環境は WSL 側へ渡らない——ここで env を足しても実行側には効かない。
//
// そこで**接続先は実行側の宣言（`~/.agents/agent-project.host.yaml` の `ollama.host`）を
// 正とし**、この画面はその 1 か所を編集する。読み手は `agentcore.ollama_adapter` の
// 1 実装なので、agent-project / agent-flow / agent-amigos のどこから起動されても同じ
// 接続先になる（解決順は `--host` > `$OLLAMA_HOST` > この宣言 > 既定 localhost）。
//
// **書き方は外科的**（cowork/main/writeback.js と同じ流儀）。host.yaml は人が書いて
// コメントで運用の理由を残すファイルなので、フル再シリアライズで整形とコメントを
// 壊さない。触るのは `ollama:` ブロックの `host:` 1 行だけ。
//
// **宣言ファイルが無い端末では作らない**。空の host.yaml が現れると agent-project は
// 「プロジェクトを持たないノード宣言」として読む——設定していないことが、設定した
// つもりの宣言に化ける。無いときは理由を返して人に作らせる。

const fs = require('fs');
const path = require('path');
const nodeRepos = require('../../agent-project/main/nodeRepos');
const { parseYaml, isPlainObject, scalarString } = require('../../../base/main/yaml');

// 接続先として受け付ける形。`http://host:11434` と scheme 省略の `host:11434` の両方。
// 空白・引用符・`#`（YAML のコメント開始）を含む値は弾く——書いた瞬間に宣言が壊れる。
function validateHost(value) {
  const host = String(value == null ? '' : value).trim();
  if (!host) return '';
  if (/[\s"'#]/.test(host)) {
    throw new Error('接続先に空白や引用符は使えません（例: http://gpu-box:11434）');
  }
  if (!/^[A-Za-z][A-Za-z0-9+.-]*:\/\/[^/]+/.test(host) && !/^[A-Za-z0-9_.-]+(:\d+)?$/.test(host)) {
    throw new Error(`接続先の書き方が正しくありません: ${host}（例: http://gpu-box:11434）`);
  }
  return host;
}

function readText(file) {
  try {
    return fs.readFileSync(file, 'utf8');
  } catch {
    return null;
  }
}

// 現在の宣言。host.yaml が無ければ file:'' を返す（＝この端末では編集できない）。
function loadOllamaHost(cfg) {
  const file = nodeRepos.findHostConfig(cfg);
  if (!file) return { file: '', host: '', timeoutSec: 0, exists: false };
  const text = readText(file);
  if (text === null) return { file, host: '', timeoutSec: 0, exists: false };
  const doc = file.toLowerCase().endsWith('.json')
    ? (() => { try { return JSON.parse(text); } catch { return null; } })()
    : parseYaml(text);
  const block = isPlainObject(doc) && isPlainObject(doc.ollama) ? doc.ollama : {};
  const timeout = Number(block.timeout_sec);
  return {
    file,
    host: scalarString(block.host) || '',
    timeoutSec: Number.isFinite(timeout) && timeout > 0 ? timeout : 0,
    exists: true,
  };
}

function detectEol(text) {
  return String(text).includes('\r\n') ? '\r\n' : '\n';
}

// 行が「トップレベルのキー」か（インデント無しの `key:`）。コメント・空行は false。
function isTopLevelKey(line) {
  return /^[A-Za-z_][\w.-]*\s*:/.test(line);
}

// `ollama:` ブロックの `host:` 1 行だけを差し替える（無ければ足す・空なら消す）。
// 返り値は新しい本文。ブロックごと無く host も空なら、本文は変わらない。
function applyOllamaHost(rawText, host) {
  const eol = detectEol(rawText);
  const lines = String(rawText).replace(/\r\n/g, '\n').split('\n');
  const value = String(host || '');
  const start = lines.findIndex((line) => /^ollama\s*:/.test(line));

  if (start < 0) {
    if (!value) return lines.join(eol);
    const body = [
      '',
      '# ローカルモデル（ollama）の接続先。空ならこの PC の ollama（127.0.0.1:11434）を使う。',
      '# 環境変数 OLLAMA_HOST を設定している場合はそちらが優先される。',
      'ollama:',
      `  host: ${JSON.stringify(value)}`,
    ];
    // 末尾の空行を重ねない（同じ編集を繰り返しても本文が育たない）。
    while (lines.length && lines[lines.length - 1].trim() === '') lines.pop();
    return [...lines, ...body, ''].join(eol);
  }

  // `ollama: {host: …}` のような 1 行表記は行単位では安全に触れない（足すと構文が壊れる）。
  // 黙って壊すより、直す場所を言って断る。
  const inline = lines[start].replace(/^ollama\s*:/, '').replace(/\s+#.*$/, '').trim();
  if (inline) {
    throw new Error(
      `ノード宣言の ollama: が 1 行で書かれているため、この画面からは変更できません（${'{'}host: …${'}'} 形式）。`
      + 'ファイルを直接編集してください。'
    );
  }

  let end = start + 1;
  while (end < lines.length && !isTopLevelKey(lines[end])) end += 1;
  const hostIdx = lines.slice(start + 1, end)
    .findIndex((line) => /^\s+host\s*:/.test(line));
  const absolute = hostIdx < 0 ? -1 : start + 1 + hostIdx;

  if (!value) {
    if (absolute < 0) return lines.join(eol);
    lines.splice(absolute, 1);
    // 中身が無くなった `ollama:` は残さない（読み手が「宣言はある」と誤解する形にしない）。
    const rest = lines.slice(start + 1, end - 1).filter((line) => line.trim() && !line.trim().startsWith('#'));
    if (!rest.length) lines.splice(start, 1);
    return lines.join(eol);
  }
  if (absolute < 0) {
    lines.splice(start + 1, 0, `  host: ${JSON.stringify(value)}`);
    return lines.join(eol);
  }
  const indent = (lines[absolute].match(/^\s*/) || [''])[0];
  lines[absolute] = `${indent}host: ${JSON.stringify(value)}`;
  return lines.join(eol);
}

// 宣言を書き戻す（原子書換）。host.yaml が無ければ作らずエラーにする。
function saveOllamaHost(cfg, payload) {
  const host = validateHost((payload || {}).host);
  const current = loadOllamaHost(cfg);
  if (!current.file || !current.exists) {
    throw new Error(
      '実行エンジンのノード宣言（agent-project.host.yaml）が見つからないため保存できません。'
      + '実行エンジンを設定してから、もう一度お試しください。'
    );
  }
  if (current.file.toLowerCase().endsWith('.json')) {
    const text = readText(current.file);
    let doc;
    try {
      doc = JSON.parse(text);
    } catch (err) {
      throw new Error(`ノード宣言を読めません: ${err.message}`, { cause: err });
    }
    const block = isPlainObject(doc.ollama) ? { ...doc.ollama } : {};
    if (host) block.host = host;
    else delete block.host;
    if (Object.keys(block).length) doc.ollama = block;
    else delete doc.ollama;
    writeAtomic(current.file, `${JSON.stringify(doc, null, 2)}\n`);
    return loadOllamaHost(cfg);
  }
  const text = readText(current.file);
  if (text === null) throw new Error('ノード宣言を読めません');
  writeAtomic(current.file, applyOllamaHost(text, host));
  return loadOllamaHost(cfg);
}

function writeAtomic(target, text) {
  const tmp = `${target}.tmp.${process.pid}`;
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(tmp, text);
  fs.renameSync(tmp, target);
}

module.exports = { loadOllamaHost, saveOllamaHost, applyOllamaHost, validateHost };
