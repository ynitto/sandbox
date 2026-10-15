'use strict';
// 設定（任意）。--config で渡すか、今のフォルダの testcase-audit.config.yaml を読む。
// パスは設定ファイルのあるフォルダからの相対（別のリポジトリのフォルダも書ける）。
//
//   cases: [tests/cases]                 # 点検するケース（引数で渡せば引数が優先）
//   spec: [../my-app-docs/docs]          # 仕様のフォルダ（要件・項目定義・状態遷移・権限・API・文言）
//   source: [../my-app/src]              # 実装のフォルダ（期待する文言が実装にあるか）
//   format:
//     schema: schema/cases.schema.json   # ケースファイルが従う書式（JSON Schema）。合わなければ D16
//     fields: { expected: [確認ポイント] } # 項目名の綴りを足す
//   rules: { D11: off, G04: warning }    # 規則ごとに off / error / warning / info
//   similarity: { duplicate: 0.9, same_steps: 0.92 }
//   words:
//     vague: [見栄えがよい]               # 曖昧な表現を足す（正規表現）
//     negative: [差し戻し]                # 異常系とみなす語を足す（正規表現）
//     pairs: [{ a_label: 会員, b_label: 非会員, a: ["(?<!非)会員"], b: [非会員] }]
//   requirement_id: "(?:REQ|FR|NFR)-\\d+" # 仕様の中の要件 ID の形（正規表現）

const fs = require('fs');
const path = require('path');
const YAML = require('yaml');
const { RULES, SEVERITY_ORDER } = require('./rules');

const DEFAULT_NAME = 'testcase-audit.config.yaml';

function defaults() {
  return {
    file: null, dir: process.cwd(), cases: [], spec: [], source: [],
    format: { schema: null, fields: {} },
    rules: {}, duplicate: 0.9, sameSteps: 0.92,
    words: { vague: [], negative: [], pairs: [] },
    requirementId: null,
  };
}

function fail(msg) {
  const e = new Error(msg);
  e.exitCode = 2;
  return e;
}

function loadConfig(file, { required = false } = {}) {
  const cfg = defaults();
  const target = file || (fs.existsSync(DEFAULT_NAME) ? DEFAULT_NAME : null);
  if (!target) return cfg;
  if (!fs.existsSync(target)) {
    if (required) throw fail(`設定ファイルがありません: ${target}`);
    return cfg;
  }
  let raw;
  try {
    raw = YAML.parse(fs.readFileSync(target, 'utf8')) || {};
  } catch (e) {
    throw fail(`設定ファイルを読めません: ${e.message.split('\n')[0]}`);
  }
  if (typeof raw !== 'object' || Array.isArray(raw)) throw fail('設定ファイルは「キー: 値」の形で書きます');
  cfg.file = path.resolve(target);
  cfg.dir = path.dirname(cfg.file);
  const list = (v) => (v == null ? [] : Array.isArray(v) ? v : [v]).map(String);
  const rel = (p) => path.resolve(cfg.dir, p);
  cfg.cases = list(raw.cases).map(rel);
  cfg.spec = list(raw.spec).map(rel);
  cfg.source = list(raw.source).map(rel);
  const fmt = raw.format || {};
  cfg.format.schema = fmt.schema ? rel(String(fmt.schema)) : null;
  cfg.format.fields = fmt.fields || {};
  for (const [k, v] of Object.entries(raw.rules || {})) {
    const code = String(k).toUpperCase();
    if (!RULES[code]) throw fail(`知らない規則です: ${code}（testcase-audit rules で一覧）`);
    const sev = v === false ? 'off' : String(v).toLowerCase();
    if (sev !== 'off' && !(sev in SEVERITY_ORDER)) throw fail(`${code} の値は off / error / warning / info のどれかです: ${v}`);
    cfg.rules[code] = sev;
  }
  const sim = raw.similarity || {};
  if (sim.duplicate != null) cfg.duplicate = Number(sim.duplicate);
  if (sim.same_steps != null) cfg.sameSteps = Number(sim.same_steps);
  const words = raw.words || {};
  cfg.words = { vague: list(words.vague), negative: list(words.negative), pairs: words.pairs || [] };
  if (raw.requirement_id) {
    try { new RegExp(raw.requirement_id); } catch (e) { throw fail(`requirement_id が正規表現として読めません: ${e.message}`); }
    cfg.requirementId = String(raw.requirement_id);
  }
  return cfg;
}

// 規則の重さ。null なら止めている
function severityOf(cfg, code) {
  const v = cfg.rules[code];
  if (!v) return RULES[code][1];
  return v === 'off' ? null : v;
}

module.exports = { loadConfig, severityOf, defaults, DEFAULT_NAME };
