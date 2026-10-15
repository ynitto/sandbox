'use strict';
// 仕様との突き合わせ（S01〜S08）。仕様のモデル（spec.js）の各要素を、どのケースが試しているかを数える。
//
// 仕様は広く、テストケースは一部の画面・機能だけを扱うことが多い。そこで、表・図・API ごとに
// 「ケースが 1 つでも触れているもの」だけを対象にする（触れていない画面の項目を全部漏れと言わない）。
// 要件 ID は例外で、ID があるものはすべて対象にする。

const tx = require('./text');
const { fullText, procedureText, expectedText, excerpt } = require('./findings');
const { boundaryGaps } = require('./lint');

const EMPTY = /未入力|空欄|空のまま|空に|空で|入力しない|入力せず|ブランク|空文字|blank|empty|value=""|指定しない|指定なし|省略/;
const BAD_FORMAT = /形式|不正|誤った|誤り|間違った|invalid|含まない|全角|半角|記号|@なし|フォーマット|書式/;

// 語の「芯」（取り消す → 取り消）。活用の違いを吸収して文に含まれるかを見る
function stem(w) { return w.length > 2 ? w.slice(0, -1) : w; }
function mentions(text, phrase) {
  const n = tx.normalize(phrase);
  if (!n) return false;
  if (text.includes(n)) return true;
  const core = [...new Set(tx.clauses(n).flatMap((c) => [...c.core]))];
  return core.length > 0 && core.every((w) => text.includes(w) || text.includes(stem(w)));
}

function pathRegex(p) {
  const src = p.split(/(\{[^}]+\}|:[A-Za-z_]\w*)/).map((s) => (/^(\{[^}]+\}|:[A-Za-z_]\w*)$/.test(s) ? '[^/\\s"\']+' : s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))).join('');
  return new RegExp(src, 'i');
}

class SpecCheck {
  constructor(ctx) {
    this.ctx = ctx;
    this.spec = ctx.spec;
    this.f = ctx.findings;
    this.cases = ctx.cases;
    this.texts = this.cases.map((c) => ({ c, all: tx.normalize(fullText(c)), proc: tx.normalize([c.title, procedureText(c)].join('\n')), exp: tx.normalize(expectedText(c)) }));
  }

  run() {
    this.inScope = this.touchedFiles();
    this.requirements();
    this.fields();
    this.machines();
    this.permissions();
    this.endpoints();
    this.messages();
  }

  requirements() {
    const referenced = new Set();
    for (const c of this.cases) for (const r of c.requirements) referenced.add(r.toUpperCase());
    const blob = this.texts.map((t) => t.all.toUpperCase()).join('\n');
    // ケースが 1 つも触れていない仕様のファイル（別の機能の仕様）の要件は数えない
    for (const r of this.spec.requirements) {
      if (!this.inScope.has(r.file)) continue;
      if (referenced.has(r.id) || new RegExp(`\\b${r.id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'i').test(blob)) continue;
      this.f.add('S01', `要件「${r.id}」を確かめるケースがありません`, { file: r.file, line: r.line, excerpt: excerpt(r.text), hint: 'この要件を確かめるケースを足し、ケースに要件 ID を書く' });
    }
  }

  fields() {
    const byGroup = new Map();
    for (const fld of this.spec.fields) {
      const k = `${fld.file}\u0001${fld.group}`;
      if (!byGroup.has(k)) byGroup.set(k, []);
      byGroup.get(k).push(fld);
    }
    for (const fields of byGroup.values()) {
      const touched = fields.filter((fld) => this.texts.some((t) => t.all.includes(tx.normalize(fld.name))));
      if (!touched.length) continue; // この表の画面・API はケースの対象外
      for (const fld of fields) this.field(fld);
    }
  }

  field(fld) {
    const name = tx.normalize(fld.name);
    const using = this.texts.filter((t) => t.all.includes(name));
    const missing = [];
    const facts = [];
    if (fld.required) {
      facts.push('必須');
      if (!using.some((t) => EMPTY.test(t.proc))) missing.push('未入力');
    }
    if (fld.min != null || fld.max != null) {
      const unit = fld.unit || '';
      facts.push(`${fld.min != null ? fld.min : ''}〜${fld.max != null ? fld.max : ''}${unit}`);
      for (const [kind, value] of [['min', fld.min], ['max', fld.max]]) {
        if (value == null) continue;
        const k = { subject: name, unit, kind, value: Math.round(value) };
        const gaps = unit ? boundaryGaps(this.cases, k) : numberGaps(using, k);
        if (gaps.length) missing.push(`境界 ${gaps.map((p) => `${p}${unit}`).join('・')}`);
      }
    }
    if (fld.format && !fld.enum) {
      facts.push(excerpt(fld.format, 20));
      if (!using.some((t) => BAD_FORMAT.test(t.all) && this.ctx.isNegative(t.c))) missing.push('形式の誤り');
    }
    if (fld.enum && fld.enum.length <= 8) {
      const absent = fld.enum.filter((v) => !using.some((t) => t.all.includes(tx.normalize(v))));
      if (absent.length) missing.push(`選択肢 ${absent.map((v) => `「${v}」`).join('')}`);
    }
    if (!missing.length) return;
    const head = using.length ? '' : '（どのケースも触れていない）';
    this.f.add('S02', `項目「${fld.name}」（${facts.join('・')}）の ${missing.join(' / ')} を試すケースがありません${head}`, { file: fld.file, line: fld.line, hint: '足りない値ごとにケースを足す（未入力・境界のちょうどと 1 つ外・形式違い・選択肢）' });
  }

  machines() {
    for (const m of this.spec.machines) {
      const states = [...new Set(m.transitions.flatMap((t) => [t.from, t.to]))];
      if (!states.some((s) => this.texts.some((t) => t.all.includes(tx.normalize(s))))) continue;
      // 遷移元は前提・手順に、操作は手順に、遷移先は期待結果にあること（期待結果の「発送済み」を「発送する」と取り違えない）
      const covered = (tr) => this.texts.some((t) => {
        const steps = tx.normalize(t.c.steps.map((i) => i.text).join('\n'));
        return t.proc.includes(tx.normalize(tr.from)) && t.exp.includes(tx.normalize(tr.to)) && (!tr.event || mentions(steps, tr.event));
      });
      const missing = m.transitions.filter((tr) => !covered(tr));
      if (missing.length) {
        const shown = missing.slice(0, 6).map((tr) => `${tr.from} →（${tr.event || '—'}）→ ${tr.to}`).join('、');
        this.f.add('S03', `状態遷移「${m.name}」の ${missing.length} 本を試していません: ${shown}${missing.length > 6 ? ' ほか' : ''}`, { file: m.file, line: (missing[0].line || m.line), hint: '遷移ごとに、遷移元の状態を前提にして操作し、遷移先になることを確かめるケースを足す。許されない操作（取り消せない状態での取り消しなど）も試す' });
      }
      this.unexpectedTransitions(m, states);
    }
  }

  // 仕様にない遷移を期待しているケース（S08）
  unexpectedTransitions(m, states) {
    for (const t of this.texts) {
      const pre = tx.normalize(t.c.preconditions.map((i) => i.text).join('\n') + '\n' + t.c.title);
      const steps = tx.normalize(t.c.steps.map((i) => i.text).join('\n'));
      const fromStates = states.filter((s) => pre.includes(tx.normalize(s)));
      if (fromStates.length !== 1) continue;
      const from = fromStates[0];
      const events = [...new Set(m.transitions.filter((tr) => tr.event).map((tr) => tr.event))].filter((e) => mentions(steps, e));
      if (events.length !== 1) continue;
      const toStates = states.filter((s) => s !== from && t.exp.includes(tx.normalize(s)) && !/(ならない|ない)/.test(t.exp.slice(t.exp.indexOf(tx.normalize(s)))));
      if (toStates.length !== 1) continue;
      const ok = m.transitions.some((tr) => tr.from === from && tr.event === events[0] && tr.to === toStates[0]);
      if (!ok) {
        const allowed = m.transitions.filter((tr) => tr.from === from && tr.event === events[0]).map((tr) => tr.to);
        this.f.add('S08', `「${from}」で「${events[0]}」すると「${toStates[0]}」になると期待していますが、仕様の状態遷移にありません${allowed.length ? `（仕様では「${allowed.join('・')}」）` : '（仕様ではこの状態でこの操作はできない）'}`, { case: t.c, hint: '仕様と期待結果のどちらが正しいかを確かめる' });
      }
    }
  }

  permissions() {
    for (const p of this.spec.permissions) {
      const roleHit = p.roles.filter((r) => this.texts.some((t) => t.all.includes(tx.normalize(r))));
      if (!roleHit.length) continue;
      // 役割は前提・条件・タイトルに、操作は手順にあること（前提の「発送済みの注文」を操作と取り違えない）
      const missing = p.cells.filter((cell) => !this.texts.some((t) => {
        const steps = tx.normalize(t.c.steps.map((i) => i.text).join('\n'));
        return t.proc.includes(tx.normalize(cell.role)) && mentions(steps, cell.op);
      }));
      if (!missing.length) continue;
      missing.sort((a, b) => Number(a.allowed) - Number(b.allowed));
      const shown = missing.slice(0, 6).map((cell) => `${cell.role}×${cell.op}（${cell.allowed ? 'できる' : 'できない'}）`).join('、');
      this.f.add('S04', `権限「${p.name}」の ${missing.length} 組を試していません: ${shown}${missing.length > 6 ? ' ほか' : ''}`, { file: p.file, line: missing[0].line || p.line, hint: '特に「できない」組は、操作できない（ボタンが出ない・拒否される）ことを確かめるケースを足す' });
    }
  }

  endpoints() {
    for (const ep of this.spec.endpoints) {
      const re = pathRegex(ep.path);
      const using = this.texts.filter((t) => re.test(t.all));
      if (!using.length || !ep.codes.length) continue;
      const missing = ep.codes.filter((code) => !using.some((t) => new RegExp(`\\b${code}\\b`).test(t.all)));
      if (missing.length) {
        this.f.add('S05', `API「${ep.method} ${ep.path}」の応答 ${missing.join('・')} を試すケースがありません`, { file: ep.file, line: ep.line, hint: 'その応答になる条件（存在しない ID・状態の衝突・権限なしなど）でのケースを足す' });
      }
    }
  }

  touchedFiles() {
    const hit = (s) => this.texts.some((t) => t.all.includes(tx.normalize(s)));
    const files = new Set();
    for (const fld of this.spec.fields) if (hit(fld.name)) files.add(fld.file);
    const blob = this.texts.map((t) => t.all.toUpperCase()).join('\n');
    for (const r of this.spec.requirements) if (this.cases.some((c) => c.requirements.map((x) => x.toUpperCase()).includes(r.id)) || blob.includes(r.id)) files.add(r.file);
    for (const m of this.spec.machines) if (m.transitions.some((tr) => hit(tr.from) || hit(tr.to))) files.add(m.file);
    for (const p of this.spec.permissions) if (p.roles.some(hit)) files.add(p.file);
    for (const ep of this.spec.endpoints) if (this.texts.some((t) => pathRegex(ep.path).test(t.all))) files.add(ep.file);
    return files;
  }

  messages() {
    const corpus = this.spec.corpus;
    if (corpus) {
      for (const t of this.texts) {
        for (const item of t.c.expected) {
          for (const q of expectedStrings(item.text)) {
            const n = tx.normalize(q);
            if (corpus.includes(n)) continue;
            // 仕様がすべての文言を載せているとは限らないので、仕様に近い文言があるとき（写し違い・古い文言）だけ出す
            const near = nearest(n, this.spec.messages.map((m) => m.text), 0.6);
            if (!near) continue;
            this.f.add('S06', `期待する文言「${excerpt(q, 40)}」が仕様にありません（仕様では「${excerpt(near, 40)}」）`, { case: t.c, item, hint: '仕様の文言に合わせるか、仕様が古いなら仕様を直す' });
          }
        }
      }
    }
    // 対象の仕様ファイル（ケースが触れている項目・要件・状態・権限・API のあるファイル）の文言のうち、どのケースも確かめていないもの
    const touchedFiles = this.inScope;
    const byFile = new Map();
    for (const m of this.spec.messages) {
      if (!touchedFiles.has(m.file)) continue;
      const n = tx.normalize(m.text);
      if (this.texts.some((t) => t.exp.includes(n) || expectedStrings(t.c.expected.map((i) => i.text).join('\n')).some((q) => n.includes(tx.normalize(q)) && q.length >= 6))) continue;
      if (!byFile.has(m.file)) byFile.set(m.file, []);
      byFile.get(m.file).push(m);
    }
    for (const ms of byFile.values()) {
      this.f.add('S07', `仕様の文言 ${ms.length} 件を、どのケースも確かめていません: ${ms.slice(0, 4).map((m) => `「${excerpt(m.text, 30)}」`).join('')}${ms.length > 4 ? ' ほか' : ''}`, { file: ms[0].file, line: ms[0].line, hint: 'その文言が出る条件でケースを足す（文言を期待結果に書く）' });
    }
  }
}

// 数値（単位なし）の範囲の境界のうち、試していない値
function numberGaps(using, k) {
  const points = k.kind === 'max' ? [k.value, k.value + 1] : [k.value - 1, k.value];
  const tested = new Set();
  for (const t of using) {
    for (const m of t.proc.matchAll(/(?<![\d.])-?\d+(?:\.\d+)?(?![\d.])/g)) tested.add(Number(m[0]));
  }
  return points.filter((p) => !tested.has(p));
}

// 構造で書いた期待結果のうち、文言でない値のキー（セレクタ・役割・URL など）
const NOT_TEXT_KEYS = /\b(css|selector|xpath|role|testid|test_id|id|url|href|count|nth|timeout|status|method|type)="[^"]*"/gi;

// 期待結果の中の、文言らしい引用（識別子・短いもの・セレクタは除く）
function expectedStrings(text) {
  return tx.quotedStrings(String(text).replace(NOT_TEXT_KEYS, ' ')).filter((q) => [...q].length >= 4 && !/^[\w./:#=-]+$/.test(q));
}

function nearest(n, candidates, min = 0.5) {
  let best = null; let score = min;
  for (const c of candidates) {
    const cn = tx.normalize(c);
    if (Math.abs(cn.length - n.length) > Math.max(cn.length, n.length) * 0.6) continue;
    const s = tx.editSimilarity(n, cn);
    if (s > score) { best = c; score = s; }
  }
  return best;
}

module.exports = { SpecCheck, pathRegex, expectedStrings, nearest, mentions };
