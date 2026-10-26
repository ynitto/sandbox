'use strict';
// 設定。原則、オプション無しの `testcase-lint` だけで動かし、何をどこまで見るかはこの設定ファイルに書く。
//
// 読む順:
//   1. --config で渡したファイル
//   2. 今のフォルダから親へさかのぼって最初に見つかった testcase-lint.config.yaml
//   3. どちらも無ければ、よくあるフォルダ（tests/cases・docs/specs・src など）を探して使う
// パスは設定ファイルのあるフォルダからの相対（別のリポジトリのフォルダも書ける）。
//
//   cases: [tests/cases]                 # 点検するケース（引数で渡せば引数が優先）
//   spec: [../my-app-docs/docs]          # 仕様のフォルダ（要件・項目定義・状態遷移・権限・API・文言）
//   source: [../my-app/src]              # 実装のフォルダ（期待する文言が実装にあるか）
//   evidence: results/testcase-lint.json # 結果ファイルも書く（ほかの道具に渡す）
//   fail_on: error                       # この重さ以上があれば終了コード 1（error / warning / info / never）
//   min_severity: info                   # この重さ未満は出さない
//   format:
//     schema: schema/cases.schema.json   # ケースファイルが従う書式（JSON Schema）。合わなければ D16
//     fields: { expected: [確認ポイント] } # 項目名の綴りを足す
//   rules: { D11: off, G04: warning }    # 規則ごとに off / error / warning / info
//   similarity: { duplicate: 0.9, same_steps: 0.92 }
//   words:
//     vague: [見栄えがよい]               # 合否を決められない表現を足す（正規表現）
//     negative: [差し戻し]                # 異常系とみなす語を足す（正規表現）
//     pairs: [{ a_label: 会員, b_label: 非会員, a: ["(?<!非)会員"], b: [非会員] }]
//   requirement_id: "(?:REQ|FR|NFR)-\\d+" # 仕様の中の要件 ID の形（正規表現）
//   test_data:
//     domains: [staging.example.co.jp]    # テスト用のメールアドレスのドメインを足す（D18。末尾一致）
//     cards: ["4000 0000 0000 0077"]      # テスト用のカード番号を足す（D19）

const fs = require('fs');
const path = require('path');
const YAML = require('yaml');
const { RULES, SEVERITY_ORDER } = require('./rules');

const DEFAULT_NAME = 'testcase-lint.config.yaml';

// 設定ファイルが無いときに探すフォルダ（今のフォルダからの相対。あるものをすべて使う）
const AUTO = {
  cases: ['tests/cases', 'test/cases', 'tests/testcases', 'testcases', 'test-cases', 'tests/scenarios', 'scenarios',
    'tests/e2e', 'test/e2e', 'e2e', 'cases'],
  spec: ['docs/specs', 'docs/spec', 'docs/requirements', 'specs', 'spec', 'requirements'],
  source: ['src', 'app'],
};

function defaults() {
  return {
    file: null, dir: process.cwd(), cases: [], spec: [], source: [], detected: null,
    evidence: null, failOn: 'error', minSeverity: 'info',
    format: { schema: null, fields: {} },
    rules: {}, duplicate: 0.9, sameSteps: 0.92,
    words: { vague: [], negative: [], pairs: [] },
    requirementId: null,
    testData: { domains: [], cards: [] },
    code: { files: [], exclude: [], globals: false, python: process.platform === 'win32' ? 'python' : 'python3', evidence: null, maxBytes: 2 * 1024 * 1024 },
  };
}

function fail(msg) {
  const e = new Error(msg);
  e.exitCode = 2;
  return e;
}

// 今のフォルダから親へさかのぼって設定ファイルを探す
function findConfig(start = process.cwd()) {
  let dir = path.resolve(start);
  for (;;) {
    const p = path.join(dir, DEFAULT_NAME);
    if (fs.existsSync(p)) return p;
    const up = path.dirname(dir);
    if (up === dir) return null;
    dir = up;
  }
}

// 設定ファイルが無いとき: よくあるフォルダを探す
function autodetect(cfg, base = process.cwd()) {
  const found = (names) => names.map((n) => path.join(base, n)).filter((p) => fs.existsSync(p) && fs.statSync(p).isDirectory());
  cfg.cases = found(AUTO.cases);
  // 入れ子（tests/cases と tests/cases/e2e のような重なり）は外側だけを使う
  cfg.cases = cfg.cases.filter((p) => !cfg.cases.some((q) => q !== p && p.startsWith(q + path.sep)));
  cfg.spec = found(AUTO.spec);
  cfg.source = found(AUTO.source);
  cfg.detected = { cases: cfg.cases.length > 0, spec: cfg.spec.length > 0, source: cfg.source.length > 0 };
  return cfg;
}

function loadConfig(file, { required = false } = {}) {
  const cfg = defaults();
  const target = file || findConfig();
  if (!target) return autodetect(cfg);
  if (!fs.existsSync(target)) {
    if (required) throw fail(`設定ファイルがありません: ${target}`);
    return autodetect(cfg);
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
  const code = raw.code || {};
  if (typeof code !== 'object' || Array.isArray(code)) throw fail('code は設定のオブジェクトです');
  cfg.code.files = list(code.files).map(rel);
  cfg.code.exclude = list(code.exclude);
  if (code.globals != null && typeof code.globals !== 'boolean') throw fail('code.globals は true / false です');
  cfg.code.globals = code.globals === true;
  if (code.python != null) {
    if (typeof code.python !== 'string' || !code.python.trim()) throw fail('code.python は Python の実行ファイル名です');
    cfg.code.python = code.python;
  }
  if (code.evidence) cfg.code.evidence = rel(String(code.evidence));
  if (code.max_file_bytes != null) {
    if (!Number.isSafeInteger(code.max_file_bytes) || code.max_file_bytes < 1) throw fail('code.max_file_bytes は正の整数です');
    cfg.code.maxBytes = code.max_file_bytes;
  }
  if (raw.evidence) cfg.evidence = rel(String(raw.evidence));
  if (raw.fail_on != null) {
    cfg.failOn = String(raw.fail_on);
    if (!['error', 'warning', 'info', 'never'].includes(cfg.failOn)) throw fail(`fail_on は error / warning / info / never のどれかです: ${raw.fail_on}`);
  }
  if (raw.min_severity != null) {
    cfg.minSeverity = String(raw.min_severity);
    if (!(cfg.minSeverity in SEVERITY_ORDER)) throw fail(`min_severity は info / warning / error のどれかです: ${raw.min_severity}`);
  }
  const fmt = raw.format || {};
  cfg.format.schema = fmt.schema ? rel(String(fmt.schema)) : null;
  cfg.format.fields = fmt.fields || {};
  for (const [k, v] of Object.entries(raw.rules || {})) {
    const code = String(k).toUpperCase();
    if (!RULES[code]) throw fail(`知らない規則です: ${code}（testcase-lint rules で一覧）`);
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
  const td = raw.test_data || {};
  cfg.testData = { domains: list(td.domains).map((d) => d.toLowerCase()), cards: list(td.cards).map((x) => x.replace(/\D/g, '')) };
  return cfg;
}

// 規則の重さ。null なら止めている
function severityOf(cfg, code) {
  const v = cfg.rules[code];
  if (!v) return RULES[code][1];
  return v === 'off' ? null : v;
}

module.exports = { loadConfig, findConfig, autodetect, severityOf, defaults, DEFAULT_NAME, AUTO };
