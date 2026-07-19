'use strict';

// 納品棚（`<home>/deliveries/<mid>/`）の読み取り専用ビュー。
//
// accept が成立すると agent-amigos の owner デーモンが deliverable を納品棚へ搬出し、
// 納品書 `delivery.json`（正典: schemas/delivery.schema.json）を書く。dashboard は
// それを読むだけで、搬出も削除も行わない（書き手は owner デーモン 1 つに保つ）。
//
// 受入待ちのプレビュー（バスの deliverable/）は missions.js の readDeliverablePreview が
// 担う。ここは「受け取った後」の一覧。

const fs = require('fs');
const path = require('path');

const preview = require('./preview');

function readJson(p) {
  try {
    return JSON.parse(fs.readFileSync(p, 'utf8'));
  } catch {
    return null;
  }
}

function deliveriesDir(homeDir) {
  return path.join(homeDir, 'deliveries');
}

// ホーム 1 つ分の納品書を読む（新しい順）。
function listForHome(homeDir) {
  const base = deliveriesDir(homeDir);
  let names;
  try {
    names = fs.readdirSync(base, { withFileTypes: true })
      .filter((e) => e.isDirectory())
      .map((e) => e.name);
  } catch {
    return [];
  }
  const out = [];
  for (const mid of names) {
    const rec = readJson(path.join(base, mid, 'delivery.json'));
    if (!rec) continue;
    const files = Array.isArray(rec.files) ? rec.files : [];
    out.push({
      mission: String(rec.mission || mid),
      title: String(rec.title || ''),
      goal: String(rec.goal || ''),
      acceptedAt: String(rec.accepted_at || ''),
      acceptedBy: String(rec.accepted_by || ''),
      acceptance: String(rec.acceptance || ''),
      partial: !!rec.partial,
      partialReason: String(rec.partial_reason || ''),
      executionSeconds: Number(rec.execution_seconds) || 0,
      dir: path.join(base, mid),
      home: homeDir,
      code: rec.code && typeof rec.code === 'object' ? rec.code : null,
      files: files.map((f) => ({
        path: String(f.path || ''),
        role: String(f.role || ''),
        bytes: Number(f.bytes) || 0,
        exported: f.exported !== false,
        skipReason: String(f.skip_reason || ''),
      })),
    });
  }
  return out;
}

// 発見済みホーム全部の納品を新しい順に集める。
function list(homeList) {
  const out = [];
  for (const h of homeList || []) {
    if (h && h.dir) out.push(...listForHome(h.dir));
  }
  return out.sort((a, b) => String(b.acceptedAt).localeCompare(String(a.acceptedAt)));
}

// 受け取り済み成果物の中身。一覧のポーリングで毎回全文・画像を運ばないよう、
// ミッション詳細を開いたときにこの 1 件だけを読む（受入プレビューと同じ読み方）。
function readContents(homeDir, missionId) {
  const dir = path.join(deliveriesDir(homeDir), String(missionId || ''));
  const rec = readJson(path.join(dir, 'delivery.json'));
  if (!rec) throw new Error(`納品が見つかりません: ${missionId}`);
  return { dir, ...preview.readPreview(dir, ['delivery.json']) };
}

module.exports = { list, listForHome, deliveriesDir, readContents };
