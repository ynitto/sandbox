'use strict';

// 参加者の台帳と利用枠。
//   <dir>/<YYYYMMDD>.jsonl … 1 行 = この PC が受けた依頼 1 件（追記専用・UTC 日付）
//   { id, posted_by, cli, model, mode, started_at, seconds, tokens_in, tokens_out, status, error_class }
// 件数と秒は常に測れる。トークンは CLI が申告したときだけ書く（推定値は書かない）。
//
// 枠切れの学習: CLI が定義の errors で class: quota に当たる出力を返したら、
//   exhausted  … その CLI をその日の残り（UTC）受けない
//   rate_limit … 10 分受けない
// 他の参加者はこの判断を再計算しない（依頼者には「いまは受けない」とだけ見える）。

const fs = require('fs');
const path = require('path');

const RATE_LIMIT_MS = 10 * 60 * 1000;

function dayKey(now = Date.now()) {
  return new Date(now).toISOString().slice(0, 10).replace(/-/g, '');
}

function nextDayStart(now = Date.now()) {
  const d = new Date(now);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() + 1);
}

class Ledger {
  constructor(dir, { now = () => Date.now() } = {}) {
    this.dir = dir;
    this.now = now;
    this.day = '';
    this.rows = [];
    this.quota = new Map();     // cli → { until, kind }
  }

  file(day) { return path.join(this.dir, `${day}.jsonl`); }

  // 今日の行をメモリに持つ（日付が変わったら読み直す）
  load() {
    const day = dayKey(this.now());
    if (day === this.day) return this.rows;
    this.day = day;
    this.rows = [];
    try {
      for (const line of fs.readFileSync(this.file(day), 'utf8').split('\n')) {
        if (!line.trim()) continue;
        try { this.rows.push(JSON.parse(line)); } catch { /* 壊れた行は飛ばす */ }
      }
    } catch { /* まだ無い */ }
    return this.rows;
  }

  record(row) {
    this.load();
    const rec = {
      id: String(row.id || ''), posted_by: String(row.posted_by || ''), cli: String(row.cli || ''),
      model: String(row.model || ''), mode: row.mode === 'write' ? 'write' : 'read',
      started_at: row.started_at || new Date(this.now()).toISOString(),
      seconds: Math.max(0, Math.round(Number(row.seconds) || 0)),
      tokens_in: Number.isFinite(row.tokens_in) ? row.tokens_in : null,
      tokens_out: Number.isFinite(row.tokens_out) ? row.tokens_out : null,
      status: ['done', 'failed', 'cancelled', 'lost'].includes(row.status) ? row.status : 'failed',
      error_class: String(row.error_class || ''),
    };
    fs.mkdirSync(this.dir, { recursive: true });
    fs.appendFileSync(this.file(this.day), `${JSON.stringify(rec)}\n`, 'utf8');
    this.rows.push(rec);
    return rec;
  }

  // 今日の実績。count は受けた件数（cancelled / lost を除く）
  today() {
    const rows = this.load();
    const out = { count: 0, seconds: 0, byRequester: {}, byCli: {} };
    for (const r of rows) {
      if (r.status === 'cancelled' || r.status === 'lost') continue;
      out.count += 1;
      out.seconds += r.seconds || 0;
      out.byRequester[r.posted_by] = (out.byRequester[r.posted_by] || 0) + 1;
      out.byCli[r.cli] = (out.byCli[r.cli] || 0) + 1;
    }
    return out;
  }

  markQuota(cli, kind) {
    const now = this.now();
    const until = kind === 'rate_limit' ? now + RATE_LIMIT_MS : nextDayStart(now);
    this.quota.set(String(cli), { until, kind: kind === 'rate_limit' ? 'rate_limit' : 'exhausted' });
  }

  quotaOf(cli) {
    const q = this.quota.get(String(cli));
    if (!q) return null;
    if (q.until <= this.now()) { this.quota.delete(String(cli)); return null; }
    return q;
  }

  cliOk(cli) { return !this.quotaOf(cli); }

  // 板に出す「受けられるか」。理由は node-budget-summary の固定語彙に寄せる。
  canAccept({ participate, clis, maxConcurrent, dailyCap, inflight }) {
    const today = this.today();
    const reasons = [];
    if (!participate) reasons.push('unavailable');
    if (Number(dailyCap) > 0 && today.count >= Number(dailyCap)) reasons.push('exceeded');
    if (Number(inflight) >= Math.max(1, Number(maxConcurrent) || 1)) reasons.push('soft');
    const perCli = {};
    for (const cli of clis || []) {
      const q = this.quotaOf(cli);
      perCli[cli] = { can_accept: !q && !reasons.length, reason_codes: q ? ['exceeded'] : (reasons.length ? reasons : ['ok']), today: today.byCli[cli] || 0 };
    }
    const anyCli = (clis || []).some((cli) => perCli[cli].can_accept);
    return {
      can_accept: !reasons.length && anyCli,
      reason_codes: reasons.length ? reasons : (anyCli ? ['ok'] : ['exceeded']),
      clis: perCli,
      today: { count: today.count, seconds: today.seconds, cap: Number(dailyCap) || 0, inflight: Number(inflight) || 0 },
    };
  }
}

module.exports = { Ledger, dayKey, nextDayStart, RATE_LIMIT_MS };
