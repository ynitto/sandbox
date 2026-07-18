'use strict';

// agent-amigos ミッションの読み取り専用ビュー。
// バス上のファイル（真実）だけを読む — dashboard からバスへは一切書かない
// （書き込み所有権はオーナー / amigo のもの。設計書 §4.2）。
//
// バスの形は 2 種類を受ける:
//   - ローカルバス:        <busDir>/missions/<mid>/mission.json
//   - GitBus workdir:      <busDir>/mission__<mid>/mission.json（ブランチ別クローン）
// busDirs 未設定時は ~/.agent/amigos/bus/*（GitBus 既定 workdir）を自動発見する。
//
// phase は表示用の近似導出（静穏化・座席の lease まではみない）。正確な状態は
// agent-amigos status が正 — ここは「何がどこまで進んでいるか」の一覧が目的。

const fs = require('fs');
const os = require('os');
const path = require('path');

function expandHome(p) {
  if (!p) return p;
  return p === '~' || p.startsWith('~/') ? path.join(os.homedir(), p.slice(1)) : p;
}

function readJson(p) {
  try {
    return JSON.parse(fs.readFileSync(p, 'utf8'));
  } catch {
    return null;
  }
}

function listDirs(p) {
  try {
    return fs
      .readdirSync(p, { withFileTypes: true })
      .filter((e) => e.isDirectory())
      .map((e) => e.name)
      .sort();
  } catch {
    return [];
  }
}

function listJsonFiles(p) {
  try {
    return fs
      .readdirSync(p)
      .filter((n) => n.endsWith('.json') && !n.includes('.tmp.'))
      .sort();
  } catch {
    return [];
  }
}

function discoverBusDirs(cfg) {
  const c = (cfg && cfg.amigos) || {};
  const configured = (Array.isArray(c.busDirs) ? c.busDirs : []).map(expandHome).filter(Boolean);
  if (configured.length) return configured;
  const base = path.join(os.homedir(), '.agent', 'amigos', 'bus');
  return listDirs(base).map((n) => path.join(base, n));
}

// バスディレクトリ内のミッション実体 [{id, dir}] を列挙する（両形式対応）。
function missionDirsIn(busDir) {
  const out = [];
  for (const mid of listDirs(path.join(busDir, 'missions'))) {
    const dir = path.join(busDir, 'missions', mid);
    if (fs.existsSync(path.join(dir, 'mission.json'))) out.push({ id: mid, dir });
  }
  for (const name of listDirs(busDir)) {
    if (!name.startsWith('mission__')) continue;
    const dir = path.join(busDir, name);
    if (fs.existsSync(path.join(dir, 'mission.json'))) {
      out.push({ id: name.slice('mission__'.length), dir });
    }
  }
  return out;
}

function collectMessages(dir) {
  const msgs = new Map();
  const inboxRoot = path.join(dir, 'inbox');
  for (const role of listDirs(inboxRoot)) {
    for (const f of listJsonFiles(path.join(inboxRoot, role))) {
      const m = readJson(path.join(inboxRoot, role, f));
      if (m && m.id && !msgs.has(m.id)) msgs.set(m.id, m);
    }
  }
  const allRoot = path.join(dir, 'channels', 'all');
  for (const who of listDirs(allRoot)) {
    for (const f of listJsonFiles(path.join(allRoot, who))) {
      const m = readJson(path.join(allRoot, who, f));
      if (m && m.id && !msgs.has(m.id)) msgs.set(m.id, m);
    }
  }
  return [...msgs.values()].sort((a, b) => {
    const byTime = String(a.created_at || '').localeCompare(String(b.created_at || ''));
    return byTime || String(a.id).localeCompare(String(b.id));
  });
}

function messageSummary(message, max = 120) {
  const text = String(message.subject || message.body || '').replace(/\s+/g, ' ').trim();
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1).trimEnd()}…`;
}

function readMissionSummary(id, dir) {
  const mission = readJson(path.join(dir, 'mission.json'));
  if (!mission) return null;
  const roles = {};
  for (const f of listJsonFiles(path.join(dir, 'roles'))) {
    const r = readJson(path.join(dir, 'roles', f));
    if (r && r.id) roles[r.id] = r;
  }
  const roster = readJson(path.join(dir, 'roster.json')) || {};
  const round = listJsonFiles(path.join(dir, 'rejections')).length;
  const statuses = {};
  for (const f of listJsonFiles(path.join(dir, 'status'))) {
    const s = readJson(path.join(dir, 'status', f));
    if (s) statuses[f.replace(/\.json$/, '')] = s;
  }

  // ミッション予算（バス events の cli_seconds 総和 = 依頼側会計）
  let spentSeconds = 0;
  const eventsDir = path.join(dir, 'events');
  let eventNames = [];
  try {
    eventNames = fs.readdirSync(eventsDir).filter((n) => n.endsWith('.jsonl'));
  } catch {
    /* events なし */
  }
  for (const name of eventNames) {
    let text;
    try {
      text = fs.readFileSync(path.join(eventsDir, name), 'utf8');
    } catch {
      continue;
    }
    for (const line of text.split('\n')) {
      const s = line.trim();
      if (!s) continue;
      try {
        const sec = Number(JSON.parse(s).cli_seconds);
        if (Number.isFinite(sec) && sec > 0) spentSeconds += sec;
      } catch {
        /* skip */
      }
    }
  }
  const budgetCfg = (mission.budget || {});
  const limitSeconds = Math.max(0, Number(budgetCfg.execution_minutes) || 0) * 60;
  const softRatio = Number(budgetCfg.soft_ratio) || 0.9;
  const hard = limitSeconds > 0 && spentSeconds >= limitSeconds;
  const soft = limitSeconds > 0 && spentSeconds >= limitSeconds * softRatio;

  // ロール別の状態（roster の担当 × status の done_round / state）
  const roleRows = Object.values(roles)
    .sort((a, b) => String(a.id).localeCompare(String(b.id)))
    .map((r, index) => {
      const ent = roster[r.id];
      const st = ent ? statuses[`${ent.node}--${r.id}`] : null;
      const explicitTitle = String(r.title || '').trim();
      return {
        id: r.id,
        title: explicitTitle || r.id,
        displayName: explicitTitle || (r.builtin === 'integrator' ? '成果の取りまとめ' : `担当 ${index + 1}`),
        responsibility: String(r.mission || '').trim(),
        required: r.required !== false,
        builtin: r.builtin || '',
        node: ent ? ent.node : null,
        state: st ? st.state : null,
        turn: st ? st.turn : null,
        done: !!(st && st.done_round === round),
        note: (st && st.note) || '',
      };
    });

  // 未回答質問（owner 宛は人の判断待ちなので数えない — agent-amigos と同じ規約）
  const msgs = collectMessages(dir);
  const answered = new Set(msgs.filter((m) => m.type === 'answer' && m.reply_to).map((m) => m.reply_to));
  const unanswered = msgs.filter(
    (m) => m.type === 'question' && m.to !== 'owner' && !answered.has(m.id)
  ).length;
  const roleLabels = Object.fromEntries(roleRows.map((r) => [r.id, r.displayName]));
  const participantLabel = (who) => {
    if (who === 'owner') return '進行役';
    if (who === 'all') return '全員';
    if (who === 'system') return 'システム';
    return roleLabels[who] || '担当エージェント';
  };
  const messages = msgs.map((m) => {
    const requiresAttention =
      (m.type === 'question' && !answered.has(m.id)) ||
      (m.type === 'decision-request' && m.to === 'owner');
    return {
      id: m.id,
      fromLabel: participantLabel(m.from),
      toLabel: participantLabel(m.to),
      type: String(m.type || 'info'),
      subject: String(m.subject || '').trim(),
      body: String(m.body || '').trim(),
      summary: messageSummary(m),
      replyTo: m.reply_to || null,
      createdAt: m.created_at || '',
      answered: m.type === 'question' && answered.has(m.id),
      requiresAttention,
    };
  });

  // phase の近似導出（表示用）
  const manifest = readJson(path.join(dir, 'deliverable', 'MANIFEST.json'));
  const finalDoc = readJson(path.join(dir, 'final.json'));
  const requiredWorkers = roleRows.filter((r) => r.required && r.builtin !== 'integrator');
  const staffed = requiredWorkers.every((r) => r.node);
  let phase = 'working';
  if (readJson(path.join(dir, 'cancelled.json'))) phase = 'cancelled';
  else if (finalDoc && finalDoc.accepted) phase = 'done';
  else if (hard && budgetCfg.on_exhausted === 'fail') phase = 'failed';
  else if (!staffed) phase = 'open';
  else if (manifest && Number(manifest.round) === round) phase = 'reviewing';
  else if (requiredWorkers.every((r) => r.done)) phase = 'integrating';

  return {
    id,
    dir,
    title: mission.title || id,
    goal: mission.goal || '',
    owner: mission.owner_node || '',
    postedAt: mission.posted_at || '',
    phase,
    round,
    staffed,
    roles: roleRows,
    messages,
    attentionCount: messages.filter((m) => m.requiresAttention).length,
    unanswered,
    pausedRoles: roleRows.filter((r) => r.state === 'paused').map((r) => r.id),
    budget: { spentSeconds, limitSeconds, soft, hard },
    manifest: manifest
      ? { round: manifest.round, partial: !!manifest.partial, reason: manifest.reason || '' }
      : null,
  };
}

function overview(cfg, extraBusDirs) {
  const busDirs = [...new Set([...discoverBusDirs(cfg),
                               ...(extraBusDirs || []).filter(Boolean)])];
  const missions = [];
  const errors = [];
  for (const busDir of busDirs) {
    for (const { id, dir } of missionDirsIn(busDir)) {
      try {
        const summary = readMissionSummary(id, dir);
        if (summary) missions.push({ ...summary, busDir });
      } catch (e) {
        errors.push(`${busDir}/${id}: ${e.message}`);
      }
    }
  }
  missions.sort((a, b) => String(b.postedAt).localeCompare(String(a.postedAt)));
  return { busDirs, missions, errors };
}

module.exports = { discoverBusDirs, missionDirsIn, readMissionSummary, overview };
