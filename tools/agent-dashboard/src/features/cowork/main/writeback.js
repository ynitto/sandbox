'use strict';

// 発見項目の編集を **実体ファイル** へ外科的に書き戻す。YAML ライブラリを使わず、所有する
// kiro-loop の所有フィールドと statemachine の先頭 name/description だけを書き換え、
// コメント・順序・他エントリは触らない。
// フル再シリアライズはコメント破壊のリスクが高いため採らない。

const { parseKiroLoopPromptsWithLines, scalarValue } = require('./discover');

// 元の field 行から末尾インラインコメント（先頭空白込み。例 `   # 1 時間ごと`）を取り出す。
// 値が引用符で始まる場合は閉じ引用符の後ろのコメントのみ拾う（値内の `#` を誤検出しない）。
function trailingComment(line) {
  const m = String(line).match(/^\s*[\w-]+:\s?(.*)$/);
  if (!m) return '';
  const val = m[1];
  const t = val.trimStart();
  if (t[0] === '"' || t[0] === "'") {
    const q = t[0];
    const lead = val.length - t.length;
    const end = val.indexOf(q, lead + 1);
    if (end < 0) return '';
    const after = val.slice(end + 1);
    const cm = after.match(/(\s+#.*)$/);
    return cm ? cm[1] : '';
  }
  const cm = val.match(/(\s+#.*)$/);
  return cm ? cm[1] : '';
}

// YAML ダブルクォート スカラ（JSON のエスケープは YAML double-quoted の部分集合で安全）。
function yamlDq(s) {
  return JSON.stringify(String(s == null ? '' : s));
}

// 表示スケジュール（"12m" / "12"）から interval 分の整数へ。取れなければ null。
function parseIntervalMinutes(schedule) {
  const m = String(schedule == null ? '' : schedule).trim().match(/^(\d+)\s*m?$/i);
  return m ? parseInt(m[1], 10) : null;
}

// out 再構成: repl（行 index→新行）と inserts（beforeIndex→行配列）を適用。
function rebuild(lines, repl, inserts, eol, removed = new Set()) {
  const out = [];
  for (let i = 0; i <= lines.length; i += 1) {
    if (inserts.has(i)) out.push(...inserts.get(i));
    if (i < lines.length && !removed.has(i)) out.push(repl.has(i) ? repl.get(i) : lines[i]);
  }
  return out.join(eol);
}

function detectEol(rawText) {
  return String(rawText).includes('\r\n') ? '\r\n' : '\n';
}

// edits: [{ promptIndex, promptName, name?, prompt?, schedule?, enabled?, scheduleKey }]
// 返り値 { text, errors }。実際の差分が無くても text は等価（元コメント/構造を保持）。
function applyKiroLoopEdits(rawText, edits) {
  const eol = detectEol(rawText);
  const norm = String(rawText).replace(/\r\n/g, '\n');
  const lines = norm.split('\n');
  const entries = parseKiroLoopPromptsWithLines(norm);
  const repl = new Map();
  const inserts = new Map();
  const removed = new Set();
  const errors = [];

  const addInsert = (beforeIdx, text) => {
    const arr = inserts.get(beforeIdx) || [];
    arr.push(text);
    inserts.set(beforeIdx, arr);
  };

  for (const edit of edits || []) {
    let entry = entries[edit.promptIndex];
    const nameVal = entry && entry.fields.name ? scalarValue(entry.fields.name.rawVal) : undefined;
    if (!entry || (edit.promptName && nameVal !== edit.promptName)) {
      // 発見後にファイルが並び替わっている等 → 名前で照合し直す
      entry = entries.find((e) => e.fields.name && scalarValue(e.fields.name.rawVal) === edit.promptName);
      if (!entry) {
        errors.push(`kiro-loop: prompt が見つかりません（index=${edit.promptIndex} name=${edit.promptName || ''}）`);
        continue;
      }
    }
    const pad = ' '.repeat(entry.fieldIndent);
    const setField = (key, valueText) => {
      const f = entry.fields[key];
      if (f) {
        // dash 行の inline フィールド（`- name: x`）は '- ' を保つため dashPrefix を使う。
        const prefix = f.line === entry.dashLine ? (entry.dashPrefix || pad) : pad;
        repl.set(f.line, prefix + key + ': ' + valueText + trailingComment(lines[f.line]));
      } else {
        addInsert(entry.dashLine + 1, pad + key + ': ' + valueText);
      }
    };
    const setPrompt = (value) => {
      const f = entry.fields.prompt;
      const body = String(value || '').replace(/\r\n/g, '\n').split('\n')
        .map((line) => `${' '.repeat(entry.fieldIndent + 2)}${line}`).join(eol);
      if (!f) {
        addInsert(entry.dashLine + 1, `${pad}prompt: |${eol}${body}`);
        return;
      }
      const prefix = f.line === entry.dashLine ? (entry.dashPrefix || pad) : pad;
      repl.set(f.line, `${prefix}prompt: |${trailingComment(lines[f.line])}${eol}${body}`);
      if (/^[|>]/.test(String(f.rawVal || '').trim())) {
        for (let i = f.line + 1; i < lines.length; i += 1) {
          if (lines[i].trim() && lines[i].match(/^\s*/)[0].length <= entry.fieldIndent) break;
          removed.add(i);
        }
      }
    };

    if (Object.prototype.hasOwnProperty.call(edit, 'name') && edit.name !== undefined) {
      setField('name', yamlDq(edit.name));
    }
    if (Object.prototype.hasOwnProperty.call(edit, 'enabled') && edit.enabled !== undefined) {
      setField('enabled', edit.enabled ? 'true' : 'false');
    }
    if (Object.prototype.hasOwnProperty.call(edit, 'prompt') && edit.prompt !== undefined) {
      setPrompt(edit.prompt);
    }
    if (Object.prototype.hasOwnProperty.call(edit, 'schedule') && edit.schedule !== undefined) {
      if (edit.scheduleKey === 'cron') {
        setField('cron', yamlDq(edit.schedule));
      } else if (edit.scheduleKey === 'interval_minutes') {
        const n = parseIntervalMinutes(edit.schedule);
        if (n != null) setField('interval_minutes', String(n));
        else errors.push(`kiro-loop: interval_minutes に変換できないスケジュール「${edit.schedule}」`);
      }
      // scheduleKey==='' の項目は schedule を書き戻さない（読んだ物理フィールドが無い）
    }
  }
  return { text: rebuild(lines, repl, inserts, eol, removed), errors };
}

// dashboard で追加した項目は marker 付きの1ブロックとして所有し、安全に追加・更新・削除する。
function upsertManagedKiroPrompt(rawText, item, prompt) {
  const eol = detectEol(rawText);
  const id = String(item.id || '').replace(/[^A-Za-z0-9_.-]+/g, '-');
  const marker = `  # agent-dashboard: ${id}`;
  let lines = String(rawText || '').replace(/\r\n/g, '\n').split('\n');
  let start = lines.findIndex((line) => line === marker);
  if (start >= 0) {
    let end = start + 2;
    while (end < lines.length && !/^\S/.test(lines[end]) && !/^ {2}(?:#|-\s)/.test(lines[end])) end += 1;
    lines.splice(start, end - start);
  }
  if (prompt == null) return { text: lines.join(eol), changed: start >= 0 };
  let promptsAt = lines.findIndex((line) => /^prompts:\s*(#.*)?$/.test(line));
  if (promptsAt < 0) {
    while (lines.length && !lines[lines.length - 1]) lines.pop();
    lines.push('', 'prompts:');
    promptsAt = lines.length - 1;
  }
  const body = String(prompt).replace(/\r\n/g, '\n').split('\n').map((line) => `      ${line}`);
  const schedule = String(item.schedule || '').trim();
  const interval = parseIntervalMinutes(schedule);
  const block = [marker, `  - name: ${yamlDq(item.name || item.id)}`, '    prompt: |', ...body];
  if (schedule) block.push(interval != null ? `    interval_minutes: ${interval}` : `    cron: ${yamlDq(schedule)}`);
  block.push('    enabled: true', '');
  lines.splice(promptsAt + 1, 0, ...block);
  return { text: lines.join(eol), changed: true };
}

// statemachine の workflow.yaml: states: より前の列0 name:/description: のみ書換/挿入。
function applyStatemachineEdits(rawText, edits) {
  const eol = detectEol(rawText);
  const norm = String(rawText).replace(/\r\n/g, '\n');
  const lines = norm.split('\n');
  let statesLine = lines.findIndex((l) => /^states:\s*(#.*)?$/.test(l));
  if (statesLine < 0) statesLine = lines.length;
  const repl = new Map();
  const inserts = new Map();

  const addInsert = (beforeIdx, text) => {
    const arr = inserts.get(beforeIdx) || [];
    arr.push(text);
    inserts.set(beforeIdx, arr);
  };
  const setTop = (key, valueText) => {
    let idx = -1;
    const re = new RegExp('^' + key + ':\\s?');
    for (let i = 0; i < statesLine; i += 1) {
      if (re.test(lines[i])) { idx = i; break; }
    }
    if (idx >= 0) {
      repl.set(idx, key + ': ' + valueText + trailingComment(lines[idx]));
    } else {
      let at = 0;
      while (at < lines.length && (/^\s*#/.test(lines[at]) || /^\s*$/.test(lines[at]))) at += 1;
      addInsert(at, key + ': ' + valueText);
    }
  };

  if (edits && Object.prototype.hasOwnProperty.call(edits, 'name') && edits.name !== undefined) {
    setTop('name', yamlDq(edits.name));
  }
  if (edits && Object.prototype.hasOwnProperty.call(edits, 'description') && edits.description !== undefined) {
    setTop('description', yamlDq(edits.description));
  }
  return { text: rebuild(lines, repl, inserts, eol), errors: [] };
}

module.exports = {
  applyKiroLoopEdits,
  upsertManagedKiroPrompt,
  applyStatemachineEdits,
  trailingComment,
  parseIntervalMinutes,
  yamlDq,
};
