'use strict';
// 書き方・重複・矛盾・漏れの規則（D / U / C / G）。ケースの文だけを見て分かるもの。どれも決定的。
//
// - 不備 … 1 件のケースだけを見て分かるもの（項目の欠け・曖昧な表現・書きかけ・依存）
// - 重複 … ケース同士を文字の n-gram で比べる
// - 矛盾 … 前提と手順がほぼ同じ組の期待結果を、肯定・否定の向きで比べる。数値の条件も比べる
// - 漏れ … 要件の網羅・異常系の有無・境界値・対になる条件

const tx = require('./text');
const { procedureText, expectedText, fullText, allItems, excerpt } = require('./findings');

const rx = (patterns) => new RegExp(patterns.map((p) => `(?:${p})`).join('|'));

const PLACEHOLDER = /\btbd\b|\btodo\b|\bfixme\b|\bxxx+\b|未定|要確認|検討中|\?{2,}|●●|○○|〇〇|◯◯|△△|\*\*\*|<[^>]*(ここ|記入|入力して)[^>]*>|\[(ここ|要|tbd)[^\]]*\]/;
const DEPENDS = /同上|前述の|上記の?(手順|操作|ケース|状態)|前(の|回の)?(ケース|テスト|項目)|前項|(tc|ケース|テスト|no\.?)[-_]?\d+\s*(と同(じ|様)|を(実施|実行)|の(後|あと|続き|結果|状態))/;
const OPERATION_END = /(を|に)?(クリック|ダブルクリック|押下|入力|選択|タップ|ドラッグ|スクロール)(する|します)?$|を押す$|を開く$/;
const VERIFY_IN_STEP = /(ことを確認|を確認)(する|します)?$|(が|は)表示される(こと)?$|になっている(こと)?$|であること$|されること$|できること$/;
const MULTI_ACTION = /して、|し、|て、|後、|たら、|てから/g;
const NEGATIVE_WORDS = [
  'エラー', '失敗', '異常', '不正', '無効', '拒否', 'できない', '警告', '超え', '超過', '未入力', '空欄', '空の', '範囲外', '上限',
  '下限', 'タイムアウト', '切断', '権限(が|の)?(ない|なし)', '存在しない', '重複', '誤った|間違った|誤り', '不足', '禁止', 'ロック',
  'invalid', 'error', 'fail', 'reject', 'denied', 'empty', 'timeout', 'forbidden', 'unauthori[sz]ed', 'not found',
  '(status|ステータス|http|コード)\\W{0,3}[45]\\d\\d', '\\b[45]\\d\\d\\s?(エラー|error)',
];
// 対になる条件。both=false は「a があるなら b も欲しい」だけを見る
const PAIRS = [
  { a_label: 'ログインした状態', b_label: 'ログインしていない状態', a: ['ログイン済', 'ログイン状態', 'ログインした状態', 'ログインしている'],
    b: ['未ログイン', 'ログアウト(した|後|の状態|状態)', 'ログインしていない', 'ログイン前'], both: true },
  { a_label: '管理者', b_label: '権限のない利用者', a: ['管理者', '\\badmin\\b'],
    b: ['一般(ユーザー|利用者|会員)', '権限(が|の)?(ない|なし)', '権限不足', '閲覧者'], both: true },
  { a_label: 'オン（有効）にする', b_label: 'オフ（無効）にする', a: ['オンにする', '有効にする', 'チェックを入れ', '\\bon\\s?にする'],
    b: ['オフにする', '無効にする', 'チェックを外', '\\boff\\s?にする'], both: true },
  { a_label: '入力する', b_label: '未入力・空欄のまま進める', a: ['(?<!未)入力す', '(?<!未)入力し', '\\bfill\\b'],
    b: ['未入力', '空欄', '空のまま', '空に', 'ブランク', '空文字', '入力しない', '入力せず', 'value=""'], both: false },
  { a_label: '一覧・検索結果', b_label: '該当が 0 件のとき', a: ['一覧', '検索結果', 'リスト'],
    b: ['\\b0\\s?件', 'ゼロ件', '該当(なし|しない|がない|するものがない)', 'データ(が)?(ない|なし)', '空の一覧', '見つかりません', '結果がない'], both: false },
  { a_label: '送信・通信', b_label: '通信に失敗したとき', a: ['送信', '\\bapi\\b', 'サーバー?', '通信'],
    b: ['通信(エラー|失敗|断)', 'オフライン', 'タイムアウト', 'サーバー?エラー', 'ネットワーク', 'status="5\\d\\d"', '\\b5\\d\\d\\s?(エラー|error)'], both: false },
  { a_label: '作成・追加', b_label: '削除', a: ['作成する', '追加する', '新規登録'], b: ['削除'], both: false },
  { a_label: '保存・送信', b_label: '取り消し・キャンセル', a: ['保存', '登録(する|ボタン)', '送信', '確定'],
    b: ['キャンセル', '取り消', '取消', '中止', '破棄'], both: false },
];

// テストデータの安全（D18・D19）。ケースは CI のログや不具合票に写るので、本物の個人情報・鍵を置かない
const EMAIL = /[\w.+-]+@((?:[a-z0-9-]+\.)+[a-z]{2,})\b/gi;
// 予約済みのドメイン（RFC 2606・6761）。これ以外は実在しうる
const RESERVED_DOMAIN = /(^|\.)(example\.(com|net|org)|example|test|invalid|localhost)$/;
// 国際ブランドの番号の形（先頭の数字と桁数）。13 桁のタイムスタンプ・JAN コードを拾わないため、形の合うものだけを見る
const CARD = /(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])/g;
const CARD_BRAND = /^(4(\d{12}|\d{15}|\d{18})|5[1-5]\d{14}|2(2[2-9]\d|[3-6]\d\d|7[01]\d|720)\d{12}|3[47]\d{13}|35(2[89]|[3-8]\d)\d{12}|3(0[0-5]|[68]\d)\d{11}|6(011|5\d\d)\d{12})$/;
// 決済会社が公開しているテスト用の番号（設定の test_data.cards で足せる）
const TEST_CARDS = new Set(['4111111111111111', '4242424242424242', '4012888888881881', '4000056655665556', '5555555555554444',
  '5105105105105100', '2223003122003222', '378282246310005', '371449635398431', '3530111333300000', '3566002020360505',
  '3566111111111113', '36227206271667', '30569309025904', '6011111111111117', '6011000990139424', '4222222222222']);
// 形の決まった鍵だけを拾う（「パスワード」はテストの値として要るので見ない）
const SECRETS = [
  [/\b(AKIA|ASIA)[0-9A-Z]{16}\b/g, 'AWS のアクセスキー'],
  [/\bgh[pousr]_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{22,}/g, 'GitHub のトークン'],
  [/\bsk-[A-Za-z0-9_-]{20,}/g, 'API キー'],
  [/\b[sr]k_live_[A-Za-z0-9]{20,}/g, 'Stripe の本番キー'],
  [/\bxox[abprs]-[A-Za-z0-9-]{10,}/g, 'Slack のトークン'],
  [/\bAIza[0-9A-Za-z_-]{35}\b/g, 'Google の API キー'],
  [/\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/g, 'JWT'],
  [/-----BEGIN [A-Z ]*PRIVATE KEY-----/g, '秘密鍵'],
];

function luhn(digits) {
  let sum = 0;
  for (let i = 0; i < digits.length; i++) {
    let d = Number(digits[digits.length - 1 - i]);
    if (i % 2) { d *= 2; if (d > 9) d -= 9; }
    sum += d;
  }
  return sum % 10 === 0;
}

// ponytail: 同じ数字が 6 つ続く番号はテスト用とみなす（4000 0000 0000 0077 など）。本物でもまれに当たる
const testLikeCard = (digits, extra) => TEST_CARDS.has(digits) || extra.includes(digits) || /(\d)\1{5}/.test(digits);
// 「sk-xxxxxxxx…」のような伏せ字は鍵とみなさない
const placeholderSecret = (s) => !s.startsWith('-----') && new Set(s.replace(/^[a-z]+[-_]/i, '')).size < 8;

const stepOf = (item) => { const m = /^steps\[(\d+)\]/.exec(item.field); return m ? m[1] : null; };

function groupsOf(pairs) {
  const parent = new Map();
  const find = (x) => { if (!parent.has(x)) parent.set(x, x); while (parent.get(x) !== x) { parent.set(x, parent.get(parent.get(x))); x = parent.get(x); } return x; };
  for (const [i, j] of pairs) parent.set(find(j), find(i));
  const members = new Map(); const best = new Map();
  for (const [i, j, s] of pairs) {
    const r = find(i);
    if (!members.has(r)) members.set(r, new Set());
    members.get(r).add(i); members.get(r).add(j);
    best.set(r, Math.max(best.get(r) || 0, s));
  }
  return [...members.entries()].map(([r, m]) => [[...m].sort((a, b) => a - b), best.get(r)]).sort((a, b) => a[0][0] - b[0][0]);
}

class Lint {
  constructor(ctx) {
    this.ctx = ctx;
    this.cases = ctx.cases;
    this.f = ctx.findings;
    const w = ctx.config.words;
    this.negative = rx([...NEGATIVE_WORDS, ...w.negative]);
  }

  run() {
    this.ids();
    for (const c of this.cases) this.caseRules(c);
    this.requirementLinks();
    this.pairs();
    this.selfConflicts();
    this.numeric();
    this.negativeCases();
    this.pairConditions();
  }

  // 指摘した組を控える（条件の組み合わせの点検で、同じ組を重ねて指摘しないため）
  reported(a, b) {
    if (!this.ctx.reportedPairs) this.ctx.reportedPairs = new Set();
    this.ctx.reportedPairs.add([a.index, b.index].sort((x, y) => x - y).join(','));
  }

  // ---- 不備 ----

  // 本物らしい個人情報・鍵（D18・D19）。鍵・カード番号は出力に写さない（指摘が CI のログに残るため）
  testData(c) {
    const td = this.ctx.config.testData || { domains: [], cards: [] };
    const conds = c.conditions ? Object.entries(c.conditions).map(([k, v]) => ({ text: `${k}: ${v}`, line: c.line })) : [];
    for (const [item, text] of [[null, c.title], ...[...allItems(c), ...conds].map((i) => [i, i.text])]) {
      if (!text) continue;
      const raw = String(text).normalize('NFKC');
      for (const m of raw.matchAll(EMAIL)) {
        const domain = m[1].toLowerCase();
        if (RESERVED_DOMAIN.test(domain) || td.domains.some((d) => domain === d || domain.endsWith(`.${d}`))) continue;
        this.f.add('D18', `メールアドレス「${m[0]}」のドメイン「${domain}」は実在しうるので、メールが本当に届くおそれがあります`, { case: c, item, hint: 'example.com・*.test などの予約済みのドメインにする。テスト用に持っているドメインなら設定の test_data.domains に足す', excerpt: excerpt(text) });
      }
      for (const m of raw.matchAll(CARD)) {
        const digits = m[0].replace(/\D/g, '');
        if (!CARD_BRAND.test(digits) || !luhn(digits) || testLikeCard(digits, td.cards)) continue;
        this.f.add('D19', `本物らしいカード番号（末尾 ${digits.slice(-4)}）が書かれています`, { case: c, item, hint: '決済会社が公開しているテスト用の番号にする。テスト用の番号なら設定の test_data.cards に足す' });
      }
      for (const [re, what] of SECRETS) {
        for (const m of raw.matchAll(re)) {
          if (placeholderSecret(m[0])) continue;
          this.f.add('D19', `${what}らしい値（${m[0].slice(0, 4)}…）が書かれています`, { case: c, item, hint: '鍵はケースに書かず、実行するときに環境変数などから渡す。漏れた鍵は無効にする' });
        }
      }
    }
  }

  ids() {
    const byFile = new Map();
    for (const c of this.cases) { if (!byFile.has(c.file)) byFile.set(c.file, []); byFile.get(c.file).push(c); }
    for (const [file, cs] of byFile) {
      const withId = cs.filter((c) => c.id);
      if (!withId.length && cs.length > 1) {
        this.f.add('D01', `どのケースにも ID がありません（${cs.length} 件）`, { file, line: cs[0].line, hint: 'TC-001 のような ID を振ると、指摘・不具合票・要件との対応を取りやすい' });
      } else if (withId.length) {
        for (const c of cs) if (!c.id) this.f.add('D01', 'ID がありません', { case: c, hint: 'ほかのケースと同じ形で ID を振る', excerpt: excerpt(c.title) });
      }
    }
    // ID はファイル（スイート）ごとに一意であればよい
    const seen = new Map();
    for (const c of this.cases) {
      if (!c.id) continue;
      const k = `${c.file}\u0001${tx.normalize(c.id)}`;
      if (!seen.has(k)) seen.set(k, []);
      seen.get(k).push(c);
    }
    for (const cs of seen.values()) {
      if (cs.length > 1) this.f.add('D02', `ID「${cs[0].id}」が ${cs.length} 件で使われています`, { case: cs[0], others: cs.slice(1), hint: 'ID を振り直す' });
    }
  }

  caseRules(c) {
    if (c.kind === 'case') {
      if (!c.title) this.f.add('D03', 'タイトルがありません', { case: c, hint: '何を確かめるケースかを 1 行で書く' });
      if (!c.steps.length) this.f.add('D04', '手順がありません', { case: c, hint: '何をするかを順に書く' });
      if (!c.expected.length) this.f.add('D05', '期待結果がありません', { case: c, hint: '何が・どうなれば合格かを書く' });
    }
    for (const [item, text] of [[null, c.title], ...allItems(c).map((i) => [i, i.text])]) {
      if (!text) continue;
      const n = tx.normalize(text);
      if (PLACEHOLDER.test(n)) this.f.add('D07', '書きかけのまま残っています', { case: c, item, hint: '決めて書き切るか、ケースを外す', excerpt: excerpt(text) });
      if (DEPENDS.test(n)) this.f.add('D09', 'ほかのケースを前提にしていて、このケースだけでは実行できません', { case: c, item, hint: '必要な前提・手順をこのケースに書き写す', excerpt: excerpt(text) });
    }
    this.testData(c);
    for (const item of c.expected) {
      if (tx.normalize(item.text).split(/[。\n]/).some((p) => OPERATION_END.test(p.trim()))) {
        this.f.add('D10', '期待結果に操作が書かれています', { case: c, item, hint: '操作は手順へ移し、期待結果にはその結果どうなるかを書く', excerpt: excerpt(item.text) });
      }
    }
    for (const item of c.steps) {
      const n = tx.normalize(item.text);
      if (VERIFY_IN_STEP.test(n.replace(/[。. ]+$/, ''))) this.f.add('D11', '手順に確認が書かれています', { case: c, item, hint: '確かめることは期待結果へ移す', excerpt: excerpt(item.text) });
      if ((n.match(MULTI_ACTION) || []).length >= 3 || n.length > 150) {
        this.f.add('D12', '1 つの手順に操作が多く、どこで失敗したか分かりにくくなります', { case: c, item, hint: '1 手順 1 操作に分ける', excerpt: excerpt(item.text) });
      }
    }
  }

  requirementLinks() {
    const body = this.cases.filter((c) => c.kind === 'case');
    const linked = body.filter((c) => c.requirements.length);
    if (body.length && linked.length && linked.length * 2 >= body.length) {
      for (const c of body) if (!c.requirements.length) this.f.add('D13', 'どの要件を確かめるケースかが書かれていません', { case: c, hint: 'ほかのケースと同じ形で要件の ID を書く' });
    }
    const reqs = this.ctx.requirements;
    if (!reqs) return;
    const known = new Set(reqs.map((r) => tx.normalize(r.id)));
    for (const c of this.cases) {
      for (const r of c.requirements) {
        if (!known.has(tx.normalize(r))) this.f.add('D14', `要件「${r}」は要件一覧にありません`, { case: c, hint: 'ID の綴りを確かめる（要件が消えたならケースも見直す）' });
      }
    }
  }

  // ---- 重複・矛盾（ケースの組） ----

  pairs() {
    const cs = this.cases;
    if (cs.length < 2) return;
    const body = cs.map((c) => (c.kind === 'case' ? tx.normalize(`${procedureText(c)}\n${expectedText(c)}`) : tx.normalize(c.title)));
    const exact = new Map();
    body.forEach((b, i) => { if (!b.trim()) return; if (!exact.has(b)) exact.set(b, []); exact.get(b).push(i); });
    const exactGroup = new Map();
    for (const idx of exact.values()) {
      if (idx.length < 2) continue;
      for (const i of idx) exactGroup.set(i, idx);
      for (const a of idx) for (const b of idx) if (a < b) this.reported(cs[a], cs[b]);
      this.f.add('U01', `中身が完全に同じケースが ${idx.length} 件あります`, { case: cs[idx[0]], others: idx.slice(1).map((i) => cs[i]), hint: '1 件にまとめる' });
    }
    const sameExact = (i, j) => exactGroup.has(i) && exactGroup.get(i) === exactGroup.get(j);
    this.ctx.exactGroup = exactGroup;

    // 矛盾: 前提＋手順がほぼ同じ（値も同じ）組の期待結果を比べる
    const conflict = new Set();
    const procIdx = cs.map((c, i) => i).filter((i) => cs[i].kind === 'case' && cs[i].steps.length && cs[i].expected.length);
    const procVecs = tx.vectorize(procIdx.map((i) => tx.maskValues(procedureText(cs[i]))));
    for (const [a, b] of tx.similarPairs(procVecs, this.ctx.config.sameSteps)) {
      const i = procIdx[a]; const j = procIdx[b];
      const ci = cs[i]; const cj = cs[j];
      if (sameExact(i, j) || tx.values(procedureText(ci)) !== tx.values(procedureText(cj))) continue;
      const hit = tx.opposite(expectedText(ci), expectedText(cj));
      if (hit) {
        conflict.add(`${i},${j}`);
        this.reported(ci, cj);
        this.f.add('C01', `前提と手順が同じなのに、期待結果が逆です（「${excerpt(hit[0].text, 30)}」と「${excerpt(hit[1].text, 30)}」）`, { case: ci, others: [cj], hint: 'どちらが正しいかを仕様で確かめる。条件が違うなら、その違いを前提に書く' });
        continue;
      }
      const ei = tx.maskValues(expectedText(ci)); const ej = tx.maskValues(expectedText(cj));
      if (ei !== ej && tx.cosine(...tx.vectorize([ei, ej])) < 0.5) {
        conflict.add(`${i},${j}`);
        this.reported(ci, cj);
        this.f.add('C03', '前提と手順が同じなのに、期待結果が違います', { case: ci, others: [cj], hint: '同じ操作の結果なら 1 件にまとめる。結果を分ける条件があるなら前提に書く' });
      }
    }

    // ほぼ同じ・値だけ違う。タイトルは比べない（同じ中身に別のタイトルが付くのが重複の典型）。
    // 長い共通の手順だけで似ていると判断しないよう、期待結果どうしも似ていることを求める
    const textOf = (c) => (c.kind === 'case' ? `${procedureText(c)}\n${expectedText(c)}` : c.title);
    const vecs = tx.vectorize(cs.map((c) => tx.maskValues(textOf(c))));
    const expVecs = tx.vectorize(cs.map((c) => tx.maskValues(expectedText(c))));
    const near = []; const variants = [];
    for (const [i, j, s] of tx.similarPairs(vecs, this.ctx.config.duplicate)) {
      if (sameExact(i, j) || conflict.has(`${i},${j}`) || cs[i].kind !== cs[j].kind) continue;
      if (cs[i].kind === 'case' && tx.cosine(expVecs[i], expVecs[j]) < this.ctx.config.duplicate - 0.1) continue;
      if (tx.values(textOf(cs[i])) === tx.values(textOf(cs[j]))) near.push([i, j, s]);
      else if (cs[i].kind === 'case') variants.push([i, j, s]);
    }
    for (const [i, j] of near) this.reported(cs[i], cs[j]);
    for (const [idx, s] of groupsOf(near)) {
      this.f.add('U02', `ほぼ同じケースが ${idx.length} 件あります（似ている度合い ${s.toFixed(2)}）`, { case: cs[idx[0]], others: idx.slice(1).map((i) => cs[i]), hint: '違いが意図したものか確かめ、同じなら 1 件にまとめる' });
    }
    for (const [idx] of groupsOf(variants)) {
      if (idx.length < 3) continue;
      this.f.add('U03', `値だけが違うよく似たケースが ${idx.length} 件あります`, { case: cs[idx[0]], others: idx.slice(1).map((i) => cs[i]), hint: '1 件にまとめて、値と期待結果を表（パラメータ）にすると読みやすく、直し漏れも減る' });
    }

    const titles = new Map();
    cs.forEach((c, i) => { if (c.kind === 'case' && c.title) { const k = tx.normalize(c.title); if (!titles.has(k)) titles.set(k, []); titles.get(k).push(i); } });
    for (const idx of titles.values()) {
      if (idx.length > 1 && !idx.every((i) => exactGroup.has(i))) {
        this.f.add('U04', `タイトル「${excerpt(cs[idx[0]].title, 30)}」が ${idx.length} 件で同じですが、中身が違います`, { case: cs[idx[0]], others: idx.slice(1).map((i) => cs[i]), hint: '違いが分かるタイトルにする' });
      }
    }
  }

  // ケースの中の食い違い（同じ手順に付いた期待結果どうし・前提どうし）
  selfConflicts() {
    for (const c of this.cases) {
      for (const list of [c.expected, c.preconditions]) {
        const cl = list.flatMap((it) => tx.clauses(it.text).map((x) => [it, x]));
        let hit = null;
        for (let a = 0; a < cl.length && !hit; a++) {
          for (let b = a + 1; b < cl.length; b++) {
            const [ia, x] = cl[a]; const [ib, y] = cl[b];
            // 別々の手順に付いた確認は時間の順に並ぶので、逆の向きでも食い違いではない
            if (stepOf(ia) !== stepOf(ib)) continue;
            if (x.negative !== y.negative && tx.sameCore(x.core, y.core)) { hit = [x.text, y.text]; break; }
          }
        }
        if (hit) this.f.add('C02', `同じケースの中で「${excerpt(hit[0], 30)}」と「${excerpt(hit[1], 30)}」が食い違います`, { case: c, hint: 'どちらかが誤り。時間の順に変わるなら手順を分けて書く' });
      }
    }
  }

  // ---- 数値の条件 ----

  constraintSources() {
    const out = [];
    for (const r of this.ctx.requirements || []) {
      for (const k of tx.constraints(r.text)) out.push({ k, file: r.file, line: r.line, label: `要件 ${r.id}` });
    }
    // 仕様の項目定義の表（文字数・範囲）も、食い違いを見る出どころにする
    for (const fld of (this.ctx.spec && this.ctx.spec.fields) || []) {
      for (const [kind, value] of [['min', fld.min], ['max', fld.max]]) {
        if (value == null || !fld.unit) continue;
        const text = `${value}${fld.unit}${kind === 'max' ? '以下' : '以上'}`;
        out.push({ k: { subject: tx.normalize(fld.name), unit: fld.unit, kind, value: Math.round(value), text }, file: fld.file, line: fld.line, label: '仕様', spec: true });
      }
    }
    for (const c of this.cases) {
      for (const [text, line] of [[c.title, c.line], ...allItems(c).map((i) => [i.text, i.line || c.line])]) {
        for (const k of tx.constraints(text)) out.push({ k, file: c.file, line, label: c.id || `${c.line} 行目` });
      }
    }
    return out;
  }

  numeric() {
    const sources = this.constraintSources();
    const byKey = new Map();
    for (const s of sources) {
      if (!s.k.subject || !s.k.unit) continue;
      const key = `${s.k.subject}|${s.k.unit}|${s.k.kind}`;
      if (!byKey.has(key)) byKey.set(key, []);
      byKey.get(key).push(s);
    }
    for (const xs of byKey.values()) {
      const vals = new Set(xs.map((s) => s.k.value));
      if (vals.size < 2) continue;
      const where = xs.slice(0, 4).map((s) => `${s.label}「${s.k.text}」`).join('、');
      this.f.add('C04', `「${xs[0].k.subject}」の${xs[0].k.kind === 'max' ? '上限' : '下限'}が食い違います: ${where}`, { file: xs[0].file, line: xs[0].line, hint: '仕様の値に合わせて、どちらかを直す' });
    }
    // 境界値。試した値は、条件として書いた所を除く数値と、入力に使った引用の文字数から拾う
    // （期待結果の文言の長さを、入力した値と取り違えないよう、期待結果は数えない）
    const done = new Set();
    for (const s of sources) {
      const { k } = s;
      if (!k.unit) continue;
      const key = `${k.subject}|${k.unit}|${k.kind}|${k.value}`;
      // 仕様の項目定義の境界は、仕様との突き合わせ（S02）がまとめて出す
      if (done.has(key) || s.spec || (this.ctx.specBoundaryKeys && this.ctx.specBoundaryKeys.has(key))) continue;
      done.add(key);
      const missing = boundaryGaps(this.cases, k);
      if (!missing.length) continue;
      const name = k.subject ? `「${k.subject}」の` : '';
      const [lo, hi] = boundaryPoints(k);
      this.f.add('G03', `${name}条件「${k.text}」の境界 ${missing.map((p) => `${p}${k.unit}`).join('・')} を試すケースがありません（${s.label}）`, { file: s.file, line: s.line, hint: `境界のちょうど（${k.kind === 'max' ? lo : hi}${k.unit}）と、1 つ外（${k.kind === 'max' ? hi : lo}${k.unit}）の両方を試す` });
    }
  }

  // ---- 漏れ ----

  isNegative(c) {
    if (this.negative.test(tx.normalize(fullText(c)))) return true;
    return tx.clauses([c.title, procedureText(c)].join('\n')).some((x) => x.negative);
  }

  // 機能のまとまり（ファイルと、その中の suite・feature）ごとに見る。要件ごとだと、
  // 「正しく入れたら通る」だけを書いた要件まで毎回指摘してしまう
  negativeCases() {
    const groups = new Map();
    for (const c of this.cases.filter((x) => x.kind === 'case')) {
      const name = c.group.filter(Boolean).join(' / ') || c.file.split('/').pop();
      const k = `${c.file}\u0001${name}`;
      if (!groups.has(k)) groups.set(k, { name, cs: [] });
      groups.get(k).cs.push(c);
    }
    for (const { name, cs } of groups.values()) {
      if (!cs.some((c) => this.isNegative(c))) {
        this.f.add('G02', `「${name}」のケース ${cs.length} 件はすべて正常系です`, { case: cs[0], hint: '誤った入力・権限なし・通信の失敗など、うまくいかないときのケースを足す' });
      }
    }
  }

  pairConditions() {
    const pairs = [...PAIRS, ...this.ctx.config.words.pairs.map((p) => ({ both: true, ...p }))];
    const texts = this.cases.map((c) => [c, tx.normalize(fullText(c))]);
    for (const p of pairs) {
      const aLabel = p.a_label || p.name || p.a.slice(0, 2).join('・');
      const bLabel = p.b_label || p.b.slice(0, 2).join('・');
      const ra = rx(p.a); const rb = rx(p.b);
      const aHit = texts.filter(([, t]) => ra.test(t)).map(([c]) => c);
      const bHit = texts.filter(([, t]) => rb.test(t)).map(([c]) => c);
      if (aHit.length && !bHit.length) {
        this.f.add('G04', `「${aLabel}」を試すケースはありますが、「${bLabel}」を試すケースがありません`, { case: aHit[0], hint: `「${bLabel}」のケースを足す（要らないなら設定でこの規則を止める）` });
      } else if (bHit.length && !aHit.length && p.both !== false) {
        this.f.add('G04', `「${bLabel}」を試すケースはありますが、「${aLabel}」を試すケースがありません`, { case: bHit[0], hint: `「${aLabel}」のケースを足す（要らないなら設定でこの規則を止める）` });
      }
    }
  }
}

// 上限 M なら [M, M+1]、下限 m なら [m-1, m]
function boundaryPoints(k) {
  return k.kind === 'max' ? [k.value, k.value + 1] : [k.value - 1, k.value];
}

// 条件 k の境界のうち、どのケースも試していない値
function boundaryGaps(cases, k) {
  const points = boundaryPoints(k).filter((p) => p >= 0);
  const tested = new Set();
  for (const c of cases) {
    if (k.subject && !tx.normalize(fullText(c)).includes(k.subject)) continue;
    const t = [c.title, procedureText(c)].join('\n');
    for (const p of tx.testedPoints(t, k.unit)) tested.add(p);
    if (k.unit === '文字' || k.unit === '桁') for (const p of tx.quotedLengths(t)) tested.add(p);
  }
  return points.filter((p) => !tested.has(p));
}

// 期待する文言の揺れ（C06・C07）。ケースどうしで同じ文言を少しだけ違えて書いたものを出す。
// 仕様・実装のどちらかに片方でもあれば出さない（両方あれば別の文言、片方だけなら S06・I01 がもう片方を言う）。
// 仕様も実装も渡さないときに、文言の誤りを拾う唯一の手がかりになる。
const PUNCT = /[\s。、．，,.!！?？・…]/g;
function messageVariants(ctx) {
  const { expectedStrings } = require('./specCheck');
  const known = (n) => (ctx.source && ctx.source.files && ctx.source.corpus.includes(n)) || (ctx.spec && ctx.spec.corpus && ctx.spec.corpus.includes(n));
  // 文言（数値を伏せたもの）→ 出てくる所
  const byText = new Map();
  for (const c of ctx.cases) {
    for (const item of c.expected) {
      for (const q of expectedStrings(item.text)) {
        const n = tx.normalize(q);
        const key = n.replace(/\d+(?:[.,]\d+)?/g, '0');
        if (!byText.has(key)) byText.set(key, { key, n, q, at: [] });
        byText.get(key).at.push({ c, item });
      }
    }
  }
  const texts = [...byText.values()];
  const vecs = tx.vectorize(texts.map((t) => t.key));
  // 候補は n-gram の余弦で絞り（件数の 2 乗を避ける）、編集距離で確かめる
  for (const [i, j] of tx.similarPairs(vecs, 0.6)) {
    const a = texts[i]; const b = texts[j];
    const sameCase = a.at.some((x) => b.at.some((y) => x.c === y.c));
    if (sameCase || known(a.n) || known(b.n)) continue; // 1 つのケースで両方を確かめているなら、別の文言として書き分けている
    const punctOnly = a.key.replace(PUNCT, '') === b.key.replace(PUNCT, '');
    // ponytail: 「姓を入力」「名を入力」のような別の文言も 0.85 を超えうるので、C07 は参考にとどめる
    if (!punctOnly && ([...a.key].length < 8 || tx.editSimilarity(a.key, b.key) < 0.85)) continue;
    // 少ない方を揺れとみなし、多い方を示す
    const [few, many] = a.at.length <= b.at.length ? [a, b] : [b, a];
    const others = [...new Set(many.at.map((x) => x.c))].slice(0, 3);
    const msg = punctOnly
      ? `期待する文言「${excerpt(few.q, 40)}」が、ほかのケースの「${excerpt(many.q, 40)}」と句読点・空白だけ違います`
      : `期待する文言「${excerpt(few.q, 40)}」が、ほかのケースの「${excerpt(many.q, 40)}」と少しだけ違います`;
    ctx.findings.add(punctOnly ? 'C06' : 'C07', msg, { case: few.at[0].c, item: few.at[0].item, others, hint: '画面に出る文言を確かめて、どちらかにそろえる（仕様か実装のフォルダを渡すと、どちらが正しいかも分かる）' });
  }
}

module.exports = { Lint, NEGATIVE_WORDS, boundaryGaps, boundaryPoints, rx, messageVariants };
