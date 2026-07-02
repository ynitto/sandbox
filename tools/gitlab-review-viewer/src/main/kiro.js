'use strict';

// kiro-autonomous の needs/<id>.md（人の判断待ち・検収待ち）をビュアーから扱う。
//
// needs ファイルは MADR（Markdown Any Decision Records）互換:
//   ---
//   status: proposed / date / decision-makers / task-id / kind
//   ---
//   # 要対応: <id> — <title>
//   ## Context and Problem Statement
//   ## Decision Outcome   ← 人の決定記入欄（旧形式は「## フィードバック」）
//   - [ ] 確定（[x] にすると kiro-autonomous が次パスで取り込む）
//
// レイアウト: <root>/projects/<name>/needs/*.md（標準）または <root>/needs/*.md

const fs = require('fs');
const path = require('path');
const { runCommand, buildPrompt } = require('./agent');

const FEEDBACK_MARKERS = ['## Decision Outcome', '## フィードバック'];

function parseNeedsContent(raw) {
  const fm = {};
  let body = raw;
  const m = raw.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?/);
  if (m) {
    body = raw.slice(m[0].length);
    for (const line of m[1].split(/\r?\n/)) {
      const i = line.indexOf(':');
      if (i > 0) fm[line.slice(0, i).trim()] = line.slice(i + 1).trim();
    }
  }
  const pick = (re) => {
    const mm = body.match(re);
    return mm ? mm[1].trim() : '';
  };
  return {
    frontmatter: fm,
    title: pick(/^# (.+)$/m),
    why: pick(/^- なぜ: (.+)$/m),
    stateLine: pick(/^- 状態: (.+)$/m),
    kind: fm.kind || '',
    status: fm.status || '',
    submitted: /^\s*-\s*\[[xX]\]/m.test(raw),
  };
}

function assertUnderRoot(root, file) {
  if (!root) throw new Error('kiro-autonomous のコンテナパスが未設定です（設定画面から指定してください）');
  const r = path.resolve(root);
  const f = path.resolve(file);
  if (f !== r && !f.startsWith(r + path.sep)) {
    throw new Error(`コンテナ外のファイルは操作できません: ${file}`);
  }
  return f;
}

function listNeeds(root) {
  if (!root) throw new Error('kiro-autonomous のコンテナパスが未設定です（設定画面から指定してください）');
  if (!fs.existsSync(root)) throw new Error(`パスが存在しません: ${root}`);
  const entries = [];
  const scan = (dir, project) => {
    if (!fs.existsSync(dir)) return;
    for (const name of fs.readdirSync(dir)) {
      if (!name.endsWith('.md')) continue;
      const file = path.join(dir, name);
      try {
        entries.push({
          id: path.basename(name, '.md'),
          project,
          file,
          mtime: fs.statSync(file).mtimeMs,
          ...parseNeedsContent(fs.readFileSync(file, 'utf8')),
        });
      } catch {
        /* 読めないファイルはスキップ */
      }
    }
  };
  const projectsDir = path.join(root, 'projects');
  if (fs.existsSync(projectsDir)) {
    for (const name of fs.readdirSync(projectsDir)) {
      const p = path.join(projectsDir, name);
      try {
        if (fs.statSync(p).isDirectory()) scan(path.join(p, 'needs'), name);
      } catch {
        /* skip */
      }
    }
  }
  scan(path.join(root, 'needs'), '');
  entries.sort((a, b) => b.mtime - a.mtime);
  return entries;
}

function readNeeds(root, file) {
  const f = assertUnderRoot(root, file);
  const raw = fs.readFileSync(f, 'utf8');
  return { file: f, raw, ...parseNeedsContent(raw) };
}

// 決定記入欄にフィードバックを書き、確定チェックボックスを [x] にする。
// kiro-autonomous 側の取り込み（ingest_feedback）と同じ約束事:
//   [x] の時だけ確定・マーカー以降のコメント/チェックボックス以外の行が本文。
function submitFeedback(root, file, text) {
  const f = assertUnderRoot(root, file);
  let raw = fs.readFileSync(f, 'utf8');
  if (!FEEDBACK_MARKERS.some((mk) => raw.includes(mk))) {
    throw new Error('決定記入欄（## Decision Outcome / ## フィードバック）が見つかりません');
  }
  raw = raw.replace(/^(\s*-\s*)\[ \]/m, '$1[x]');
  raw = raw.replace(/^status: proposed$/m, 'status: accepted');
  const fb = String(text || '').trim();
  if (fb) raw = raw.trimEnd() + '\n\n' + fb + '\n';
  fs.writeFileSync(f, raw, 'utf8');
  return { file: f, submitted: true };
}

async function approveNeeds(kiroCfg, { id, project, reason }) {
  if (!kiroCfg.root) {
    throw new Error('kiro-autonomous のコンテナパスが未設定です（設定画面から指定してください）');
  }
  const command = buildPrompt(kiroCfg.approveCommand, {
    id,
    project: project || 'default',
    root: path.resolve(kiroCfg.root),
    reason: String(reason || 'ビュアーから承認').replaceAll('"', ''),
  });
  const output = await runCommand(command, {
    timeoutSec: 120,
    cwd: path.dirname(path.resolve(kiroCfg.root)),
  });
  return { output };
}

module.exports = { listNeeds, readNeeds, submitFeedback, approveNeeds, parseNeedsContent };
